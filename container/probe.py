#!/usr/bin/env python3
"""Small network/CDP probes; runs in the guard's Chromium network namespace."""
import base64
import json
import os
import socket
import struct
import sys
import time
import urllib.request
import urllib.parse


def connect(host, port, timeout=2):
    try:
        with socket.create_connection((host, int(port)), timeout):
            return True
    except OSError:
        return False


class CDP:
    def __init__(self, target=None):
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        deadline = time.monotonic() + 20
        while True:
            try:
                targets = json.load(opener.open('http://127.0.0.1:9222/json', timeout=3))
                page = next(t for t in targets if t['type'] == 'page' and (not target or t['id'] == target))
                break
            except (OSError, StopIteration) as error:
                if time.monotonic() >= deadline:
                    raise RuntimeError('CDP not ready; start with --cdp for acceptance only') from error
                time.sleep(0.25)
        path = page['webSocketDebuggerUrl'].split(':9222', 1)[1]
        self.sock = socket.create_connection(('127.0.0.1', 9222), 3)
        self.sock.settimeout(20)
        key = base64.b64encode(os.urandom(16)).decode()
        self.sock.sendall((f'GET {path} HTTP/1.1\r\nHost: 127.0.0.1:9222\r\nUpgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n').encode())
        reply = b''
        while b'\r\n\r\n' not in reply:
            reply += self.sock.recv(1)
        if b' 101 ' not in reply:
            raise RuntimeError('CDP WebSocket handshake failed')
        self.seq = 0

    def read(self, size):
        data = b''
        while len(data) < size:
            chunk = self.sock.recv(size - len(data))
            if not chunk:
                raise RuntimeError('CDP connection closed')
            data += chunk
        return data

    def call(self, method, params=None):
        self.seq += 1
        data = json.dumps({'id': self.seq, 'method': method, 'params': params or {}}).encode()
        mask = os.urandom(4)
        size = len(data)
        header = bytes([0x81, 0x80 | (size if size < 126 else 126)])
        if size >= 126:
            header += struct.pack('!H', size)
        self.sock.sendall(header + mask + bytes(v ^ mask[i % 4] for i, v in enumerate(data)))
        while True:
            first, second = self.read(2)
            length = second & 127
            if length == 126:
                length = struct.unpack('!H', self.read(2))[0]
            elif length == 127:
                length = struct.unpack('!Q', self.read(8))[0]
            payload = self.read(length)
            if first & 15 == 8:
                raise RuntimeError('CDP WebSocket closed')
            if first & 15 != 1:
                continue
            reply = json.loads(payload)
            if reply.get('id') == self.seq:
                if 'error' in reply:
                    raise RuntimeError(str(reply['error']))
                return reply.get('result', {})

    def evaluate(self, expression, user_gesture=False):
        reply = self.call('Runtime.evaluate', {'expression': expression, 'awaitPromise': True, 'returnByValue': True, 'userGesture': user_gesture})
        if 'exceptionDetails' in reply:
            raise RuntimeError(str(reply['exceptionDetails']))
        return reply['result'].get('value')


if __name__ == '__main__':
    action = sys.argv[1]
    if action == 'connect':
        result = connect(sys.argv[2], sys.argv[3])
        print(json.dumps({'connected': result}))
        sys.exit(0 if result else 1)
    client = CDP()
    if action == 'eval':
        print(json.dumps(client.evaluate(sys.argv[2]), ensure_ascii=False))
    elif action == 'navigate':
        print(json.dumps(client.call('Page.navigate', {'url': sys.argv[2]})))
    elif action == 'page':
        url = sys.argv[2]
        # Only the local, short-lived acceptance origin uses a self-signed cert.
        ignore = url.startswith('https://cbc.test/')
        if ignore:
            client.call('Security.setIgnoreCertificateErrors', {'ignore': True})
        client.call('Page.navigate', {'url': url})
        end = time.monotonic() + 25
        while time.monotonic() < end:
            current = client.evaluate('location.href')
            requested = urllib.parse.urlsplit(url)
            loaded = urllib.parse.urlsplit(current)
            matches = current == url or (requested.scheme in ['http', 'https'] and loaded.scheme == requested.scheme and loaded.netloc == requested.netloc)
            if matches and client.evaluate('document.readyState') == 'complete':
                break
            time.sleep(0.2)
        else:
            raise RuntimeError('Page did not finish loading')
        result = client.evaluate(sys.argv[3] if len(sys.argv) > 3 else '({title:document.title,text:document.body.innerText,url:location.href})')
        if ignore:
            client.call('Security.setIgnoreCertificateErrors', {'ignore': False})
        print(json.dumps(result, ensure_ascii=False))
    elif action == 'screenshot':
        data = client.call('Page.captureScreenshot', {'format': 'png'})['data']
        sys.stdout.buffer.write(base64.b64decode(data))
    elif action == 'cookies':
        print(json.dumps(client.call('Network.getAllCookies')))
    elif action == 'download':
        filename, content = sys.argv[2:4]
        script = '(()=>{const a=document.createElement("a");a.href=URL.createObjectURL(new Blob([' + json.dumps(content) + '],{type:"text/plain"}));a.download=' + json.dumps(filename) + ';document.body.append(a);a.click();return true})()'
        print(json.dumps(client.evaluate(script, user_gesture=True)))
