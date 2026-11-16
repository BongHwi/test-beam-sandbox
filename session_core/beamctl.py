# SPDX-License-Identifier: LGPL-3.0-or-later
"""Human and program entry point for the independent beam control socket."""
import argparse
import json
import socket
import sys


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--socket', required=True)
    parser.add_argument('--id')
    parser.add_argument('action', choices=['on', 'off', 'status'])
    args = parser.parse_args()
    request = {'op': 'beam', 'action': args.action, 'origin': 'command'}
    if args.id is not None:
        request['id'] = args.id
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
        client.settimeout(3)
        client.connect(args.socket)
        client.sendall((json.dumps(request) + '\n').encode())
        with client.makefile('rb') as stream:
            response = json.loads(stream.readline())
    print(json.dumps(response, sort_keys=True))
    return 0 if response.get('accepted') else 1


if __name__ == '__main__':
    sys.exit(main())
