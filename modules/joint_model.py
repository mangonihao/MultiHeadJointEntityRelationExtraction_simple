# -*- coding: utf-8 -*-

# @Author  : xmh
# @Time    : 2021/3/3 10:02
# @File    : joint_model.py

"""
file description:：

"""
import torch
import torch.nn as nn
import torch.nn.functional as F
from torchcrf import CRF
import numpy as np
from utils.config import USE_CUDA
# from utils.FocalLoss import Focal_loss
import json
from transformers import AlbertModel, BertModel


def setup_seed(seed):
    torch.manual_seed(seed)
    torch.cuda.manual_seed_all(seed)


class JointModel(nn.Module):
    def __init__(self, config, embedding_pre=None):
        super().__init__()
        setup_seed(1)
        print("do not use adv training")
        self.vocab_size = config.vocab_size
        self.embedding_dim = config.embedding_dim
        self.hidden_dim = config.hidden_dim_lstm
        self.num_layers = config.num_layers
        self.batch_size = config.batch_size
        self.layer_size = config.layer_size  # self.hidden_dim, 之前这里没有改
        self.num_token_type = config.num_token_type  # 实体类型的综述
        self.config = config

        if self.config.encode_name == 'gru':
            self.gru = nn.GRU(config.embedding_dim, config.hidden_dim_lstm, num_layers=config.num_layers,
                              batch_first=True,
                              bidirectional=True, dropout=config.dropout_lstm)
            if embedding_pre is not None:  # 测试不加载词向量的情况
                print("use pretrained embeddings")
                self.word_embedding = nn.Embedding.from_pretrained(torch.FloatTensor(embedding_pre), freeze=False)
            else:
                self.word_embedding = nn.Embedding(config.vocab_size, config.embedding_dim,
                                                   padding_idx=config.pad_token_id)
            # self.word_embedding = nn.Embedding(config.vocab_size, config.embedding_dim)
        elif self.config.encode_name == 'bert':
            print("use bert")
            # self.bert = BertModel.from_pretrained('../pretrained/bert-base-chinese')
            self.bert = BertModel.from_pretrained('/home/xieminghui/Projects/EntityRelationExtraction/bert-base-chinese')
            print("加载bert成功")
        elif self.config.encode_name == 'albert':
            print("use albert")
            with open('../pretrained/albert_chinese_tiny/config.json', 'r') as f:
                config_albert = json.load(f)
            # self.albert = AlbertModel(config_albert)
            self.albert = AlbertModel.from_pretrained('../pretrained/albert_chinese_tiny')
            print("加载albert成功")
            # self.gru = nn.GRU(config.hidden_dim_lstm*2, config.hidden_dim_lstm, num_layers=config.num_layers,
            #                   batch_first=True,
            #                   bidirectional=True, dropout=config.dropout_lstm)
            # print("use GRU")
            # if embedding_pre is not None:  # 测试不加载词向量的情况
            #     print("use pretrained embeddings")
            #     self.word_embedding = nn.Embedding.from_pretrained(torch.FloatTensor(embedding_pre), freeze=False)
            # else:
            #     self.word_embedding = nn.Embedding(config.vocab_size, config.embedding_dim,
            #                                        padding_idx=config.pad_token_id)
        
        self.token_type_embedding = nn.Embedding(config.num_token_type, config.token_type_dim)
        self.rel_embedding = nn.Embedding(config.num_relations, config.rel_emb_size)
        
        if USE_CUDA:
            self.weights_rel = (torch.ones(self.config.num_relations) * 50).cuda()
        else:
            self.weights_rel = torch.ones(self.config.num_relations) * 50
        self.weights_rel[0] = 1

        if USE_CUDA:
            self.pos_weights_rel = (torch.ones(self.config.num_relations) * 20).cuda()
        else:
            self.pos_weights_rel = torch.ones(self.config.num_relations) * 20
        self.pos_weights_rel[0] = 1
        
        self.dropout_embedding_layer = torch.nn.Dropout(config.dropout_embedding)
        self.crf_model = CRF(self.num_token_type, batch_first=True)
        
        if self.config.use_attention or self.config.use_jieba:
            self.ner_layer = nn.Linear(config.hidden_dim_lstm*2 + 1, config.num_token_type)
            self.selection_u = nn.Linear(self.hidden_dim * 2 + self.config.token_type_dim + 1, config.rel_emb_size)
            self.selection_v = nn.Linear(self.hidden_dim * 2 + self.config.token_type_dim + 1, config.rel_emb_size)
            self.hidden_proj = nn.Linear(self.hidden_dim * 2, self.hidden_dim)
            self.layer_proj = nn.Linear(2*self.config.num_layers, 1)
        else:
            self.ner_layer = nn.Linear(config.hidden_dim_lstm * 2, config.num_token_type)
            self.selection_u = nn.Linear(self.hidden_dim * 2 + self.config.token_type_dim, config.rel_emb_size)
            self.selection_v = nn.Linear(self.hidden_dim * 2 + self.config.token_type_dim, config.rel_emb_size)
        
        self.selection_uv = nn.Linear(2*config.rel_emb_size, config.rel_emb_size)

    def atten_network(self, encoder_out, hidden_final, is_test):
        # [batch, seq_len, hidden_dim_lstm]
        out_squeeze = encoder_out[:, :, :self.config.hidden_dim_lstm] + encoder_out[:, :, self.config.hidden_dim_lstm:]
        # hidden_squeeze = torch.sum(hidden_final, dim=0, keepdim=True).permute(1, 2, 0)  # [1, batch, hidden_dim_lstm]
        # out_squeeze = self.hidden_proj(encoder_out)  # [batch, seq_len, hidden_dim_lstm]
        hidden_squeeze = self.layer_proj(hidden_final.permute(1, 2, 0))  # [batch, hidden_dim, 1]
        hidden_squeeze = hidden_squeeze.squeeze(2)
        out_squeeze = torch.tanh(out_squeeze)
        
        atten_score = torch.bmm(out_squeeze, hidden_squeeze.unsqueeze(2)).squeeze(2)  # [batch, 1, seq_len]

        atten_weights = F.softmax(atten_score, dim=1)
        
        return atten_weights.unsqueeze(2)  # [batch, seq_len]
        
    
    def forward(self, data_item, is_test=False, is_eval=False):
        # 因为不是多跳机制，所以hidden_init不能继承之前的最终隐含态
        '''
        
        :param data_item: data_item = {'',}
        :type data_item: dict
        :return:
        :rtype:
        '''
        
        if self.config.encode_name == 'albert':
            # embeddings = self.word_embedding(data_item['text_tokened'].to(torch.int64))  # 要转化为int64
            # if self.config.use_dropout:
            #     embeddings = self.dropout_embedding_layer(embeddings)
            # output_lstm = self.albert(inputs_embeds=embeddings, attention_mask=data_item['mask_tokens'])[0]
            output_lstm = self.albert(data_item['text_tokened'].to(torch.int64), attention_mask=data_item['mask_tokens'])[0]

        elif self.config.encode_name == 'bert':
            output_lstm = self.bert(data_item['text_tokened'].to(torch.int64), data_item['mask_tokens'])[0]
        else:
            # [batch_size, seq_len, embedding_dim]
            embeddings = self.word_embedding(data_item['text_tokened'].to(torch.int64))  # 要转化为int64
            if self.config.use_dropout:
                embeddings = self.dropout_embedding_layer(embeddings)
    
            if USE_CUDA:
                hidden_init = torch.randn(2 * self.num_layers, self.batch_size, self.hidden_dim).cuda()
            else:
                hidden_init = torch.randn(2 * self.num_layers, self.batch_size, self.hidden_dim)
            output_lstm, h_n = self.gru(embeddings, hidden_init)
        if self.config.use_attention:
            atten_weights = self.atten_network(output_lstm, h_n, is_test)
        # output_lstm [batch, seq_len, 2*hidden_dim]  h_n [2*num_layers, batch, hidden_dim]
        # if self.config.use_dropout:
        #     output_lstm = self.dropout_lstm_layer(output_lstm)  # 用了效果变差
        # ner_score = self.get_ner_score(output_lstm)
        # [batch_size, seq_len, num_token_type]
        # 添加attention权重的情况
        if self.config.use_attention:
            ner_input = torch.cat((output_lstm, atten_weights), 2)
        else:
            ner_input = output_lstm
        
        if self.config.use_jieba:
            ner_input = torch.cat((output_lstm, data_item['jieba_cut_vector'].unsqueeze(2)), 2)
        # print(output_lstm.shape)
        ner_score = self.ner_layer(ner_input)
        # 下面是使用CFR
        
        if USE_CUDA:
            self.crf_model = self.crf_model.cuda()
        if not is_test:
            log_likelihood = self.crf_model(ner_score, data_item['token_type_list'].to(torch.int64),
                                       mask=data_item['mask_tokens'])
            loss_ner = -log_likelihood
        # [batch_size, seq_len]
        pred_ner = self.crf_model.decode(ner_score)  # , mask=data_item['mask_tokens']
        
        #--------------------------Relation
        if not is_test and torch.rand(1) > self.config.teach_rate and not is_eval:  # 评估时不使用标签导致效果变差不少
            labels = data_item['token_type_list']
        else:
            if USE_CUDA:
                labels = torch.Tensor(pred_ner).cuda()
            else:
                labels = torch.Tensor(pred_ner)
        # [batch_size, seq_len, token_type_dim]
        # 对命名实体识别的结果进行编码
        label_embeddings = self.token_type_embedding(labels.to(torch.int64))
        if self.config.use_attention:
            rel_input = torch.cat((output_lstm, label_embeddings, atten_weights), 2)
        else:
            rel_input = torch.cat((output_lstm, label_embeddings), 2)
        if self.config.use_jieba:
            rel_input = torch.cat((output_lstm, label_embeddings, data_item['jieba_cut_vector'].unsqueeze(2)), 2)
        # rel_score_matrix = self.getHeadSelectionScores(rel_input)  # [batch, seq_len, seq_len, num_relation]
        B, L, H = rel_input.size()
        # tanh求导比较复杂，会占用不少内存
        # u = torch.tanh(self.selection_u(rel_input)).unsqueeze(1).expand(B, L, L, -1)  # (B,L,L,R)
        # v = torch.tanh(self.selection_v(rel_input)).unsqueeze(2).expand(B, L, L, -1)
        # 测试去除tanh的情况，因为论文官方代码没有添加tanh
        u = self.selection_u(rel_input).unsqueeze(1).expand(B, L, L, -1)  # (B,L,L,R)
        v = self.selection_v(rel_input).unsqueeze(2).expand(B, L, L, -1)
        uv = torch.tanh(self.selection_uv(torch.cat((u, v), dim=-1)))
        selection_logits = torch.einsum('bijh,rh->birj', uv, self.rel_embedding.weight)
        selection_logits = selection_logits.permute(0,1,3,2)
        if not is_test:
            loss_rel = self.masked_BCEloss(data_item['mask_tokens'], selection_logits, data_item['pred_rel_matrix'], self.weights_rel)  # 要把分类放在第二维度
            # loss_rel = self.focal_loss(rel_score_prob, data_item['pred_rel_matrix'])
        rel_score_prob = torch.sigmoid(selection_logits)
        rel_score_prob = rel_score_prob - (self.config.threshold_rel - 0.5)  # 超过了一定阈值之后才能判断关系
        pred_rel = torch.round(rel_score_prob).to(torch.int64)
        if is_test:
            return pred_ner, pred_rel

        return loss_ner, loss_rel, pred_ner, pred_rel
        
    def masked_BCEloss(self, mask, selection_logits, selection_gold, weights_rel):
        selection_mask = (mask.unsqueeze(2) *
                          mask.unsqueeze(1)).unsqueeze(3).expand(-1, -1, -1, self.config.num_relations)
        gold_predicate_matrix_one_hot = F.one_hot(selection_gold, self.config.num_relations)
        selection_loss = F.binary_cross_entropy_with_logits(selection_logits,
                                                            gold_predicate_matrix_one_hot.float(),
                                                            weight=self.weights_rel,
                                                            pos_weight=self.pos_weights_rel,
                                                            reduction='none')
        selection_loss = selection_loss.masked_select(selection_mask).sum()
        selection_loss /= mask.sum()
        return selection_loss

