# SPDX-License-Identifier: LGPL-3.0-or-later
"""Loopback HTTP and WebSocket tunnels to screens in a session container."""
from datetime import datetime, timezone
import hmac
import os
import re
import secrets
import selectors
import socket
import subprocess
import sys
import threading
import time
from urllib.parse import parse_qsl, urlsplit

RELAY = '''# sandbox-screen-relay
import os, selectors, socket, sys
s = socket.create_connection(('127.0.0.1', int(sys.argv[1])))
with selectors.DefaultSelector() as sel:
    sel.register(sys.stdin.buffer, selectors.EVENT_READ, s)
    sel.register(s, selectors.EVENT_READ, sys.stdout.buffer)
    while True:
        for key, _ in sel.select():
            data = os.read(key.fileobj.fileno(), 65536)
            if not data: sys.exit(0)
            if key.data is s: s.sendall(data)
            else: os.write(key.data.fileno(), data)
'''


def response(client, code, extra=''):
    reason = {303: 'See Other', 400: 'Bad Request', 403: 'Forbidden', 502: 'Bad Gateway'}[code]
    client.sendall(('HTTP/1.1 %d %s\r\n%sContent-Length: 0\r\nConnection: close\r\n\r\n' %
                    (code, reason, extra)).encode('ascii'))


class Tunnels:
    def __init__(self):
        self.lock = threading.RLock()
        self.screens = {}
        self.clients = set()
        self.processes = set()
        self.closed = False

    def open(self, name, ports):
        with self.lock:
            if self.closed:
                raise RuntimeError('server stopping')
            if name not in self.screens:
                screens = {}
                try:
                    for label in ('webctl', 'slowdash', 'dqm'):
                        listener = socket.socket()
                        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                        listener.bind(('127.0.0.1', 0))
                        listener.listen()
                        listener.settimeout(.2)
                        port = listener.getsockname()[1]
                        ticket = secrets.token_urlsafe(32)
                        path = '/slowplot.html?config=slowplot-msensor.json&' if label == 'slowdash' else '/?'
                        screens[label] = {'port': port, 'url': 'http://127.0.0.1:%d%s%s' %
                                          (port, path, 'ticket=' + ticket), 'ticket': ticket,
                                          'listener': listener, 'remote': int(ports[label])}
                    self.screens[name] = screens
                    for label, screen in screens.items():
                        threading.Thread(target=self.accept, args=(name, label, screen), daemon=True).start()
                except Exception:
                    for screen in screens.values(): screen['listener'].close()
                    raise
            return {'session': name, 'screens': {k: {'port': v['port'], 'url': v['url']}
                                                  for k, v in self.screens[name].items()}}

    def accept(self, name, label, screen):
        listener = screen['listener']
        while not self.closed:
            try:
                client, _ = listener.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            with self.lock:
                if self.closed:
                    client.close()
                    break
                self.clients.add(client)
            threading.Thread(target=self.handle, args=(client, name, label, screen), daemon=True).start()

    def handle(self, client, name, label, screen):
        proc = None
        result = '400'
        sent = received = 0
        data = b''
        try:
            deadline = time.monotonic() + 5
            client.settimeout(5)
            data = client.recv(4096)
            if not data:
                return
            while b'\r\n\r\n' not in data:
                if len(data) >= 65536: raise ValueError('header too large')
                client.settimeout(max(.001, deadline - time.monotonic()))
                chunk = client.recv(min(4096, 65537 - len(data)))
                if not chunk: raise ValueError('closed')
                data += chunk
                if time.monotonic() >= deadline: raise ValueError('timeout')
            head = data.split(b'\r\n\r\n', 1)[0]
            if len(head) + 4 > 65536: raise ValueError('header too large')
            lines = head.decode('iso-8859-1').split('\r\n')
            method, target, version = lines[0].split(' ')
            url = urlsplit(target)
            if (not re.fullmatch(r"[!#$%&'*+.^_`|~0-9A-Za-z-]+", method) or
                    not target.startswith('/') or any(ord(c) < 33 or ord(c) > 126 for c in target) or
                    url.scheme or url.netloc or url.fragment or version not in ('HTTP/1.0', 'HTTP/1.1')):
                raise ValueError('bad request line')
            headers = {}
            for line in lines[1:]:
                key, value = line.split(':', 1)
                if not re.fullmatch(r"[!#$%&'*+.^_`|~0-9A-Za-z-]+", key): raise ValueError('bad header')
                headers.setdefault(key.lower(), []).append(value.strip())
            origin = 'http://127.0.0.1:%d' % screen['port']
            if headers.get('host') != [origin[7:]] or headers.get('origin', [origin]) != [origin]:
                result = '403'; response(client, 403); return
            pairs = parse_qsl(url.query, keep_blank_values=True)
            tickets = [value for key, value in pairs if key == 'ticket']
            secret = screen['ticket']
            if method == 'GET' and len(tickets) == 1 and hmac.compare_digest(tickets[0].encode(), secret.encode()):
                remaining = [part for part in url.query.split('&') if not parse_qsl(part, keep_blank_values=True) or
                             parse_qsl(part, keep_blank_values=True)[0][0] != 'ticket']
                query = '&'.join(remaining)
                location = url.path + (('?' + query) if query else '')
                extra = ('Location: %s\r\nSet-Cookie: sandbox_screen_%d=%s; HttpOnly; SameSite=Strict; Path=/\r\n'
                         'Cache-Control: no-store\r\n' % (location, screen['port'], secret))
                result = '303'; response(client, 303, extra); return
            cookie_name = 'sandbox_screen_%d' % screen['port']
            cookies = [part.strip().partition('=') for value in headers.get('cookie', []) for part in value.split(';')]
            if not any(k == cookie_name and hmac.compare_digest(v.encode(), secret.encode()) for k, _, v in cookies):
                result = '403'; response(client, 403); return
            with self.lock:
                if self.closed: return
                proc = subprocess.Popen(['docker', 'exec', '-i', name, 'python3', '-u', '-c', RELAY,
                                         str(screen['remote'])], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                        stderr=subprocess.DEVNULL, bufsize=0)
                self.processes.add(proc)
            proc.stdin.write(data); received += len(data)
            client.settimeout(None)
            with selectors.DefaultSelector() as sel:
                sel.register(client, selectors.EVENT_READ, proc.stdin)
                sel.register(proc.stdout, selectors.EVENT_READ, client)
                while True:
                    for key, _ in sel.select():
                        chunk = os.read(key.fileobj.fileno(), 65536)
                        if not chunk:
                            if key.fileobj is proc.stdout and not sent:
                                result = '502'; response(client, 502)
                            return
                        if key.data is client:
                            client.sendall(chunk); sent += len(chunk); result = 'relay'
                        else:
                            key.data.write(chunk); received += len(chunk)
        except socket.timeout:
            if data:
                try: response(client, 400)
                except OSError: pass
        except (ValueError, UnicodeError, IndexError):
            try: response(client, 400)
            except OSError: pass
        except (OSError, subprocess.SubprocessError):
            if proc is not None and not sent:
                result = '502'
                try: response(client, 502)
                except OSError: pass
        finally:
            client.close()
            if proc is not None:
                try: proc.stdin.close()
                except OSError: pass
                try: proc.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    proc.terminate()
                    try: proc.wait(timeout=.5)
                    except subprocess.TimeoutExpired:
                        proc.kill(); proc.wait()
                proc.stdout.close()
            with self.lock:
                self.clients.discard(client)
                if proc is not None: self.processes.discard(proc)
            print('%s %d %s %s %d %d' % (datetime.now(timezone.utc).isoformat(), screen['port'], label,
                                          result, received, sent), file=sys.stderr, flush=True)

    def close_all(self):
        with self.lock:
            self.closed = True
            for screens in self.screens.values():
                for screen in screens.values(): screen['listener'].close()
            for client in list(self.clients):
                try: client.shutdown(socket.SHUT_RDWR)
                except OSError: pass
                client.close()
            for proc in list(self.processes):
                try: proc.stdin.close()
                except OSError: pass
        processes = list(self.processes)
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline and any(p.poll() is None for p in processes):
            time.sleep(.02)
        for proc in processes:
            if proc.poll() is None:
                try: proc.terminate()
                except OSError: pass
        deadline = time.monotonic() + .5
        while time.monotonic() < deadline and any(p.poll() is None for p in processes):
            time.sleep(.02)
        for proc in processes:
            if proc.poll() is None:
                try: proc.kill()
                except OSError: pass
