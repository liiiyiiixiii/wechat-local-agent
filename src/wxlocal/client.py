"""UTF-8 JSON RPC over a current-user local endpoint."""

import json
import socket
from contextlib import ExitStack

from .config import WINDOWS, SOCKET
from .util import dumps


def pipe_error():
    if WINDOWS:
        import pywintypes

        return pywintypes.error
    return OSError


def rpc(method, params=None, socket_path=SOCKET):
    request = (dumps({'method': method, 'params': params or {}}) + '\n').encode()
    if len(request) > 65536:
        raise ValueError('request_too_large')
    with ExitStack() as stack:
        try:
            if WINDOWS:
                from .windows import PipeReader, open_pipe

                pipe = stack.enter_context(open_pipe())
                pipe.write(request)
                stream = PipeReader(pipe, 45)
            else:
                connection = stack.enter_context(socket.socket(socket.AF_UNIX, socket.SOCK_STREAM))
                connection.settimeout(45)
                connection.connect(str(socket_path))
                connection.sendall(request)
                stream = stack.enter_context(connection.makefile('rb'))
        except (OSError, pipe_error()) as exc:
            raise RuntimeError('service_unavailable: run wxlocal start') from exc
        data = stream.readline(8 * 1024 * 1024 + 1)
    if len(data) > 8 * 1024 * 1024:
        raise ValueError('response_too_large')
    result = json.loads(data)
    if 'error' in result:
        raise ValueError(result['error'])
    return result['result']
