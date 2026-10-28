"""Bounded Unix-socket client for the live index service (CLI and MCP adapters)."""
import json
import socket

from .config import SOCKET
from .util import dumps


def rpc(method, params=None, socket_path=SOCKET):
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
        client.settimeout(45)
        try:
            client.connect(str(socket_path))
        except (FileNotFoundError, ConnectionRefusedError):
            raise RuntimeError('service_unavailable: run 微信Agent.command start') from None
        client.sendall((dumps({'method': method, 'params': params or {}}) + '\n').encode())
        with client.makefile('rb') as stream:
            data = stream.readline(8 * 1024 * 1024 + 1)
        if len(data) > 8 * 1024 * 1024:
            raise RuntimeError('response_too_large')
        result = json.loads(data)
        if 'error' in result:
            raise ValueError(result['error'])
        return result['result']
