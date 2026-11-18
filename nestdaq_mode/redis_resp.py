# SPDX-License-Identifier: LGPL-3.0-or-later
"""Minimal synchronous RESP2 client."""
import socket
from urllib.parse import urlsplit

class RedisError(Exception):
    pass

class Redis:
    def __init__(self, uri):
        u = urlsplit(uri)
        if u.scheme != 'tcp' or not u.hostname or u.username or u.password or u.query or u.fragment:
            raise ValueError('invalid Redis URI')
        self.sock = socket.create_connection((u.hostname, u.port or 6379), timeout=2)
        self.sock.settimeout(2)
        self.file = self.sock.makefile('rb')
        db = u.path.lstrip('/') or '0'
        if not db.isdigit():
            raise ValueError('invalid Redis database')
        if int(db): self.command('SELECT', db)

    def command(self, *args):
        parts = [str(x).encode() for x in args]
        self.sock.sendall(b'*%d\r\n' % len(parts) + b''.join(b'$%d\r\n' % len(p) + p + b'\r\n' for p in parts))
        return self._read()

    def _read(self):
        line = self.file.readline()
        if not line.endswith(b'\r\n'): raise RedisError('connection closed or invalid response')
        tag, value = line[:1], line[1:-2]
        if tag == b'-': raise RedisError(value.decode(errors='replace'))
        if tag == b'+': return value.decode()
        if tag == b':': return int(value)
        if tag == b'$':
            n = int(value)
            if n == -1: return None
            data = self.file.read(n + 2)
            if len(data) != n + 2 or data[-2:] != b'\r\n': raise RedisError('truncated response')
            return data[:-2].decode()
        if tag == b'*':
            n = int(value)
            return None if n == -1 else [self._read() for _ in range(n)]
        raise RedisError('invalid response')
