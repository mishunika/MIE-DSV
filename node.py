import requests
import socket
import struct
import time
from Queue import Queue

__author__ = 'Mihai Iachimovschi'


class Node():
    def __init__(self, arg):
        self.queue = Queue(10)
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
        self.queue.put({'method': 'init_leader_election', 'args': ()})

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
        print ip + ":" + str(port) + ": " + message.decode('utf-8')

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
