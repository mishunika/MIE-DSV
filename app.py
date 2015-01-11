#!flask/bin/python
# -*- coding: utf-8 -*-
from blessings import Terminal
from flask import Flask, jsonify, request
from node import Node
import codecs
import logging
import requests
import sys
import threading
import time

sys.stdout = codecs.getwriter('utf8')(sys.stdout)
sys.stderr = codecs.getwriter('utf8')(sys.stderr)

""" Hide logger messages from console output"""
log = logging.getLogger('werkzeug')
log.setLevel(logging.ERROR)

HEARTBEAT_INTERVAL = 5
HEARTBEAT_TOLERANCE = 11

term = Terminal()


class Server(threading.Thread):
    def __init__(self):
        threading.Thread.__init__(self)

    def run(self):
        app.run(host=node.host, port=node.port)


class Worker(threading.Thread):
    def __init__(self):
        threading.Thread.__init__(self)

    def run(self):
        global node
        while True:
            task = node.queue.get()
            if task['method']:
                getattr(node, task['method'])(*task['args'])
            node.queue.task_done()


class Heartbeat(threading.Thread):
    def __init__(self):
        threading.Thread.__init__(self)

    def run(self):
        i = 0
        while True:
            # Try to send a heartbeat to the neighbor
            url = node.format_url(node.next_host, node.next_port, '/heartbeat')
            try:
                requests.post(url)
            except requests.ConnectionError:
                pass

            # Checking own received heartbeats
            if int(time.time()) - node.heartbeat_t_stamp > HEARTBEAT_TOLERANCE:
                node.init_panic()

            time.sleep(5)
            i += 1


class ChatUI(threading.Thread):
    def __init__(self):
        threading.Thread.__init__(self)

    def run(self):
        while True:
            s = raw_input()
            # print term.move_up + "Me: " + s.decode('utf-8') + term.clear_eol
            sys.stdout.write(term.move_up + term.move_x(0) + term.clear_eol)
            node.queue.put({'method': 'init_message', 'args': (s,)})


app = Flask(__name__)


@app.route('/')
def index():
    return jsonify({'message': 'I am working right now! Don\'t bother me!'})


@app.route('/serialize', methods=['GET'])
def serialize():
    return jsonify(node.serialize())


@app.route('/serialize/all', methods=['GET'])
def all_serialize():
    curr_node = node.serialize()
    json_res = [curr_node]
    next_host, next_port = curr_node['next_host'], curr_node['next_port']

    while curr_node['host'] != next_host or curr_node['port'] != next_port:
        try:
            r = requests.get(Node.format_url(next_host,
                                             next_port, '/serialize'))
            next_node = r.json()
            json_res.append(next_node)
            next_host = next_node['next_host']
            next_port = next_node['next_port']
        except requests.ConnectionError:
            break

    return jsonify({'nodes': json_res})


@app.route('/ring/join', methods=['GET'])
def join_ring():
    real_ip = request.remote_addr
    ip = request.args.get('ip')
    # if real_ip != ip:
    #     return jsonify({'message': 'You are a liar!', 'success': False}), 403

    port = request.args.get('port')

    return jsonify(node.join(ip, port))


@app.route('/ring/quit', methods=['POST'])
def quit_ring():
    host, port = request.values.get('host'), int(request.values.get('port'))
    next_host = request.values.get('next_host')
    next_port = int(request.values.get('next_port'))
    if host and port and next_host and next_port:
        # Check if the node is pointing to the quitting node
        if node.next_host == host and node.next_port == port:
            node.change_next_ptr(next_host, next_port)
        else:
            url = Node.format_url(node.next_host, node.next_port, '/ring/quit')
            params = {
                'host': host,
                'port': port,
                'next_host': next_host,
                'next_port': next_port
            }
            requests.post(url, params=params)

    return request.values.get('port')


@app.route('/ring/le/<message>', methods=['POST'])
def le_ring(message):
    if message == 'election' or message == 'elected':
        node_id = int(request.values.get('node_id'))
        node.queue.put({'method': 'chang_roberts', 'args': (message, node_id)})
    return ''


@app.route('/heartbeat', methods=['POST'])
def heartbeat():
    node.heartbeat_t_stamp = int(time.time())
    return ''


@app.route('/panic', methods=['POST'])
def panic():
    host = request.values.get('host')
    port = int(request.values.get('port'))
    node.queue.put({'method': 'panic', 'args': (host, port)})


@app.route('/ring/message', methods=['POST', 'PUT'])
def message_ring():
    message = request.values.get('message')
    sender = int(request.values.get('sender'))
    if request.method == 'POST':
        node.queue.put({'method': 'propagate_message',
                        'args': (message, sender)})
    else:
        node.queue.put({'method': 'persist_message',
                        'args': (message, sender)})


node = None

""" Initializing the working threads
    server - the thread of listening web app
    worker - the thread that executes the tasks from the node's queue
    heart_beat - the thread responsible for sending and verifying received
        heartbeats
    chat_ui - the thread responsible for the chat UI, i.e. managing user input.

"""
server = Server()
worker = Worker()
heart_beat = Heartbeat()
chat_ui = ChatUI()

if __name__ == '__main__':
    if len(sys.argv) != 2 and len(sys.argv) != 3:
        print "Usage: ./app.py src_ip:src_port [cl_ip:cl_port]"
        sys.exit(1)
    else:
        if not Node.validate_ip(Node.parse_ip_port(sys.argv[1])[0]):
            print "The argument should have the form ip:port."
            sys.exit(1)
        node = Node(sys.argv[1:])

    server.daemon = True
    server.start()

    node.start()

    worker.daemon = True
    worker.start()

    heart_beat.daemon = True
    heart_beat.start()

    chat_ui.daemon = True
    chat_ui.start()

    while True:
        try:
            time.sleep(1)
        except KeyboardInterrupt:
            node.quit_ring()
            sys.exit()