'''
Author: xmh
Date: 2021-04-20 22:24:28
LastEditors: xmh
LastEditTime: 2021-05-20 20:19:01
Description: 
FilePath: \MultiHeadJointEntityRelationExtraction_simple\deploy_flask\manage.py
'''
# -*- coding: utf-8 -*-

# @Author  : xmh
# @Time    : 2021/4/2 10:40
# @File    : manage.py

"""
file description:：

"""

from flask import Flask, render_template, request, jsonify
import json
import sys
sys.path.append('/home/xieminghui/Projects/MultiHeadJointEntityRelationExtraction_simple/')  # 添加路径


from py2neo import Graph
from utils.neo4j_util import build_graph, entity_query, rel_query
from deploy.demo import test

# graph = Graph("http://localhost:7474", username="neo4j", password="root")

def change_list2json(rel_triple_list):
    res = {"nodes": [], "edges": []}
    for sentence_list in rel_triple_list:
        if sentence_list is not None:
            for triple in sentence_list:
                subject = {"data": {"id": triple[0]}}
                object = {"data": {"id": triple[1]}}
                res["nodes"].append(subject)
                res["nodes"].append(object)
                
                rel = {"data": {"source": triple[0], "target": triple[1], "relationship": triple[2]}}
                res["edges"].append(rel)
    return res


def sent_split(text):
    sentences_list = text.split('\n')
    sentences_list = [x for x in sentences_list if x.strip()!='']
    
    return sentences_list

def flask_server():
    
    app = Flask(__name__)  # 创建一个Flask应用
    neo4j_graph = Graph("http://localhost:7474", username="neo4j", password="root")  # 连接数据库
    @app.route("/")  # 创建路由
    def index():
        return render_template("index_neo4j.html", version='V 1.0.0')

    @app.route("/query", methods=["POST"])
    def query():
        res = {}
        text = request.values['text']
        if not text:
            res["result"] = "error"
            return jsonify(res)
        setences_list = sent_split(text)
        sentences_map_list = [{"text": text} for text in setences_list]
        path_test = '../deploy/test.json'
        with open(path_test, 'w', encoding='utf-8') as _:  # 清空文件内容
            pass
        for sentence in sentences_map_list:  # 使用demo里面的test.json进行数据传递
            with open(path_test, 'a+', encoding='utf-8') as f:
                json.dump(sentence, f, ensure_ascii=False)
            with open(path_test, 'a+', encoding='utf-8') as f:
                f.write('\n')
        rel_triple_list = test()
        # 下面是处理知识图谱部分
        build_graph(rel_triple_list, neo4j_graph)
        
        # 下面是为了返回给前端数据
        res = change_list2json(rel_triple_list)
        
        return jsonify(res)
    
    @app.route('/query_entity', methods=['POST'])
    def query_entity():
        name_entity = request.values['name_entity']
        rel_outgoing, rel_incoming = entity_query(name_entity, neo4j_graph)
        res_query = change_list2json([rel_outgoing, rel_incoming])
        return jsonify(res_query)

    @app.route('/query_rel', methods=['POST'])
    def query_rel():
        name_rel = request.values['name_rel']
        print(name_rel)
        res_query = rel_query(name_rel, neo4j_graph)
        res_query = change_list2json(res_query)
        return jsonify(res_query)
    
    app.run(debug=False)
    

if __name__ == '__main__':
    flask_server()