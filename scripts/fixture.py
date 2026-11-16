#!/usr/bin/env python3
"""Temporary RFC1929 SOCKS5 + HTTPS test origin, never a production service."""
import hmac
import http.server
import json
import select
import socket
import socketserver
import ssl
import struct
import sys
import threading

CONFIG = json.load(open('/settings/fixture.json'))
EVENT_LOCK = threading.Lock()


def event(**value):
    with EVENT_LOCK:
        print(json.dumps(value), flush=True)


def read(sock, size):
    result = b''
    while len(result) < size:
        data = sock.recv(size - len(result))
        if not data:
            raise EOFError()
        result += data
    return result


class Origin(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        marker = CONFIG['marker']
        if self.path.startswith('/cache.txt'):
            body = marker.encode()
            mime = 'text/plain'
        elif self.path.startswith('/download.txt'):
            body = ('CBC download ' + marker).encode()
            mime = 'text/plain'
        else:
            body = f'<!doctype html><title>CBC fixture {marker}</title><h1>Isolated Chromium {marker}</h1><p>Temporary authenticated SOCKS5 acceptance origin.</p><a href="/download.txt" download>Download fixture</a>'.encode()
            mime = 'text/html; charset=utf-8'
        self.send_response(200)
        self.send_header('Content-Type', mime)
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'max-age=86400' if self.path.startswith('/cache.txt') else 'no-store')
        if self.path.startswith('/download.txt'):
            self.send_header('Content-Disposition', f'attachment; filename="{CONFIG["download_name"]}"')
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_):
        pass


class Socks(socketserver.BaseRequestHandler):
    def handle(self):
        sock = self.request
        sock.settimeout(20)
        try:
            version, count = read(sock, 2)
            methods = read(sock, count)
            if version != 5 or 2 not in methods:
                sock.sendall(b'\x05\xff')
                return
            sock.sendall(b'\x05\x02')
            auth_version, count = read(sock, 2)
            username = read(sock, count)
            password = read(sock, read(sock, 1)[0])
            accepted = auth_version == 1 and hmac.compare_digest(username, CONFIG['username'].encode()) and hmac.compare_digest(password, CONFIG['password'].encode())
            event(kind='auth', accepted=accepted, fixture=CONFIG['marker'])
            sock.sendall(b'\x01\x00' if accepted else b'\x01\x01')
            if not accepted:
                return
            version, command, _, atyp = read(sock, 4)
            if atyp == 3:
                host = read(sock, read(sock, 1)[0]).decode('idna')
            elif atyp == 1:
                host = socket.inet_ntop(socket.AF_INET, read(sock, 4))
            elif atyp == 4:
                host = socket.inet_ntop(socket.AF_INET6, read(sock, 16))
            else:
                return
            port = struct.unpack('!H', read(sock, 2))[0]
            event(kind='connect', atyp=atyp, destination=host, port=port, fixture=CONFIG['marker'])
            if command != 1:
                sock.sendall(b'\x05\x07\x00\x01' + b'\x00' * 6)
                return
            target = ('127.0.0.1', 8443) if host == 'cbc.test' and port == 443 else (host, port)
            with socket.create_connection(target, 15) as upstream:
                sock.sendall(b'\x05\x00\x00\x01' + b'\x00' * 6)
                sock.setblocking(False)
                upstream.setblocking(False)
                while True:
                    ready, _, _ = select.select([sock, upstream], [], [], 45)
                    if not ready:
                        return
                    for source in ready:
                        data = source.recv(65536)
                        if not data:
                            return
                        destination = upstream if source is sock else sock
                        destination.setblocking(True)
                        destination.sendall(data)
                        destination.setblocking(False)
        except (OSError, EOFError, ValueError):
            return


class Server(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


origin = http.server.ThreadingHTTPServer(('127.0.0.1', 8443), Origin)
context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
context.load_cert_chain('/settings/cert.pem', '/settings/key.pem')
origin.socket = context.wrap_socket(origin.socket, server_side=True)
threading.Thread(target=origin.serve_forever, daemon=True).start()
event(kind='ready', fixture=CONFIG['marker'])
Server(('0.0.0.0', 1080), Socks).serve_forever()
