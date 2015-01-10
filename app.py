#!flask/bin/python
# -*- coding: utf-8 -*-
from blessings import Terminal
from flask import Flask, jsonify, request
from Queue import Queue
import codecs
import logging
import requests
import socket
import struct
import sys
import threading
import time

sys.stdout = codecs.getwriter('utf8')(sys.stdout)
sys.stderr = codecs.getwriter('utf8')(sys.stderr)

""" Hide logger messages from console output"""
log = logging.getLogger('werkzeug')
log.setLevel(logging.ERROR)

queue = Queue(10)
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
        global queue
        global node
        while True:
            task = queue.get()
            if task['method']:
                getattr(node, task['method'])(*task['args'])
            queue.task_done()


class Heartbeat(threading.Thread):
    def __init__(self):
        threading.Thread.__init__(self)

    def run(self):
        i = 0
        while True:
            # Try to send a heartbeat to the neighbor
            url = Node.format_url(node.next_host, node.next_port, '/heartbeat')
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
            queue.put({'method': 'init_message', 'args': (s,)})


class Node():
    def __init__(self, arg):
        self.le_participant = False
        self.leader_id = None
        self.heartbeat_t_stamp = int(time.time())

        self.host, self.port = self.parse_ip_port(arg[0])
        if len(arg) == 2:
            self.next_host, self.next_port = self.parse_ip_port(arg[1])
            self.status = 'NEW'
        else:
            # Connected to itself.
            self.next_host, self.next_port = self.host, self.port
            self.status = 'READY'
            self.leader_id = self.id()

    def id(self):
        """
        Encoding the IP:Port combination to a unique integer (Real logic value)
        :return: integer value of the node identification
        """
        return (self.ip2int(self.host) << 16) + self.port

    def decode_id(self, node_id):
        ip = self.int2ip(node_id >> 16)
        port = node_id & 65535
        return ip, port

    def start(self):
        if self.status == 'NEW':
            # we should try to connect
            url = self.format_url(self.next_host, self.next_port, '/ring/join')
            params = {'ip': self.host, 'port': self.port}
            r = requests.get(url, params=params)
            response = r.json()
            if response['success']:
                self.next_host = response['host']
                self.next_port = int(response['port'])
                self.init_leader_election()

    def join(self, ip, port):
        """
        Called by the server (listening part)
        :param ip:
        :param port:
        :return:
        """
        old_pointer = {
            'host': self.next_host,
            'port': self.next_port,
            'success': True
        }
        self.next_host = ip
        self.next_port = int(port)
        return old_pointer

    def serialize(self):
        s = {
            'host': self.host,
            'port': self.port,
            'next_host': self.next_host,
            'next_port': self.next_port,
            'leader': self.leader_id,
            'heartbeat': self.heartbeat_t_stamp
        }
        return s

    def change_next_ptr(self, ip, port):
        self.next_host = ip
        self.next_port = port
        queue.put({'method': 'init_leader_election', 'args': ()})

    def quit_ring(self):
        # If this node is the last one - do nothing
        if self.host == self.next_host and self.port == self.next_port:
            return

        url = self.format_url(self.next_host, self.next_port, '/ring/quit')
        params = self.serialize()
        requests.post(url, params=params)

    def init_leader_election(self):
        time.sleep(2)
        self.chang_roberts('election', 0)

    def chang_roberts(self, message, node_id):
        if message == 'election':
            if node_id > self.id():
                # I am definitely not a leader
                self.le_participant = True
            elif node_id < self.id() and not self.le_participant:
                # I am better, try to be a leader
                self.le_participant = True
                node_id = self.id()
            elif node_id == self.id():
                # I am the leader
                self.le_participant = False
                self.leader_id = node_id
                message = 'elected'
            else:
                return
        elif message == 'elected':
            if self.id() == node_id:
                return
            else:
                self.le_participant = False
                self.leader_id = node_id
        else:
            return

        url = self.format_url(self.next_host, self.next_port,
                              '/ring/le/' + message)

        requests.post(url, {'node_id': node_id})
        return

    def init_panic(self):
        self.panic(self.host, self.port)

    def panic(self, host, port):
        try:
            url = self.format_url(self.next_host, self.next_port, '/panic')
            requests.post(url, {'host': host, 'port': port})
        except requests.ConnectionError:
            # Dead node found.
            self.change_next_ptr(host, port)

    def init_message(self, message):
        self.propagate_message(message, self.id())

    def propagate_message(self, message, sender):
        if self.leader_id == self.id():
            self.persist_message(message, sender, innitial=True)
        else:
            try:
                url = self.format_url(self.next_host, self.next_port,
                                      '/ring/message')
                requests.post(url, {'message': message, 'sender': sender})
            except requests.ConnectionError:
                # TODO: Handle error.
                pass

    def persist_message(self, message, sender, innitial=False):
        if not innitial and self.id() == self.leader_id:
            return

        try:
            url = self.format_url(self.next_host, self.next_port,
                                  '/ring/message')
            requests.put(url, {'message': message, 'sender': sender})
        except requests.ConnectionError:
            # TODO: Handle error.
            pass

        ip, port = self.decode_id(sender)
        print ip + ":" + str(port) + ": " + message


    @staticmethod
    def parse_ip_port(s):
        ip, port = s.split(':')
        return ip, int(port)

    @staticmethod
    def validate_ip(ip):
        try:
            socket.inet_aton(ip)
            return True
        except socket.error:
            return False

    @staticmethod
    def format_url(ip, port, path):
        return "http://" + ip + ":" + str(port) + path

    @staticmethod
    def ip2int(ip):
        return struct.unpack("!I", socket.inet_aton(ip))[0]

    @staticmethod
    def int2ip(int_ip):
        return socket.inet_ntoa(struct.pack("!I", int_ip))


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
    if real_ip != ip:
        return jsonify({'message': 'You are a liar!', 'success': False}), 403

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
        queue.put({'method': 'chang_roberts', 'args': (message, node_id)})
    return ''


@app.route('/heartbeat', methods=['POST'])
def heartbeat():
    node.heartbeat_t_stamp = int(time.time())
    return ''


@app.route('/panic', methods=['POST'])
def panic():
    host = request.values.get('host')
    port = int(request.values.get('port'))
    queue.put({'method': 'panic', 'args': (host, port)})


@app.route('/ring/message', methods=['POST', 'PUT'])
def message_ring():
    message = request.values.get('message')
    sender = int(request.values.get('sender'))
    if request.method == 'POST':
        queue.put({'method': 'propagate_message', 'args': (message, sender)})
    else:
        queue.put({'method': 'persist_message', 'args': (message, sender)})


node = None
server = Server()
client = Worker()
thread_hbeat = Heartbeat()
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

    client.daemon = True
    client.start()

    thread_hbeat.daemon = True
    thread_hbeat.start()

    chat_ui.daemon = True
    chat_ui.start()

    while True:
        try:
            time.sleep(1)
        except KeyboardInterrupt:
            node.quit_ring()
            sys.exit()