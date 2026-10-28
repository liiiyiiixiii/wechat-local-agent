"""CLI and user-level lifecycle management. No system/root services.

Every subcommand is a thin adapter over the Unix socket or launchd; the data logic lives
in `wxlocal.service`, `wxlocal.store` and `wxlocal.archive`.
"""
import argparse
import json
import os
from pathlib import Path
import plistlib
import shutil
import subprocess
import sys
import time

from ..client import rpc
from ..config import (HELPER, LABEL, MCP_ENTRY, PYTHON, ROOT, SOCKET, STATE,
                      config, prepare, wcdb_library)

PLIST = Path.home() / 'Library/LaunchAgents' / (LABEL + '.plist')
DOMAIN = 'gui/' + str(os.getuid())
TARGET = DOMAIN + '/' + LABEL


def _launch_agent():
    return {'Label': LABEL, 'ProgramArguments': [str(HELPER)],
            'WorkingDirectory': str(ROOT), 'RunAtLoad': True, 'KeepAlive': True, 'ThrottleInterval': 15,
            'StandardOutPath': str(STATE / 'daemon.log'), 'StandardErrorPath': str(STATE / 'daemon-error.log'),
            'EnvironmentVariables': {'PATH': '/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin',
                                     'PYTHONUNBUFFERED': '1'}, 'Umask': 0o077}


def _codex():
    candidate = os.environ.get('WECHAT_CODEX') or shutil.which('codex')
    if candidate:
        return candidate
    for path in sorted((Path.home() / '.nvm/versions/node').glob('*/bin/codex'),
                       key=lambda p: p.stat().st_mtime, reverse=True):
        if path.exists():
            return str(path)
    raise RuntimeError('codex_missing; install Codex or set WECHAT_CODEX')


def install():
    prepare();config()
    if not HELPER.exists():
        raise RuntimeError('helper_missing; run tools/build_helper.py')
    data = plistlib.dumps(_launch_agent())
    if not PLIST.exists() or PLIST.read_bytes() != data:
        if PLIST.exists():
            stop()
        PLIST.parent.mkdir(parents=True, exist_ok=True)
        PLIST.write_bytes(data);PLIST.chmod(0o600)
    subprocess.run(['launchctl', 'enable', TARGET], check=True)
    exists = subprocess.run(['launchctl', 'print', TARGET], capture_output=True).returncode == 0
    if not exists:
        subprocess.run(['launchctl', 'bootstrap', DOMAIN, str(PLIST)], check=True)
    return {'installed': True, 'launch_agent': str(PLIST)}


def stop():
    subprocess.run(['launchctl', 'disable', TARGET], check=True)
    r = subprocess.run(['launchctl', 'bootout', TARGET], capture_output=True)
    deadline = time.monotonic() + 45
    while subprocess.run(['launchctl', 'print', TARGET], capture_output=True).returncode == 0:
        if time.monotonic() >= deadline:
            raise RuntimeError('service_still_stopping; retry start after it exits')
        time.sleep(0.2)
    return {'stopped': r.returncode == 0 or not SOCKET.exists(), 'autostart': False, 'archive_preserved': True}


def doctor():
    sip = subprocess.run(['/usr/bin/csrutil', 'status'], capture_output=True, text=True)
    result = {'sip': sip.stdout.strip(), 'socket_exists': SOCKET.exists(),
              'socket_permissions': oct(SOCKET.stat().st_mode & 0o777) if SOCKET.exists() else None,
              'python_exists': PYTHON.exists(), 'launch_agent_exists': PLIST.exists(),
              'helper_exists': HELPER.exists(), 'wcdb_library_exists': wcdb_library().exists()}
    try:
        result['service'] = rpc('get_status')
    except Exception as e:
        result['service_error'] = str(e)
    return result


def register_codex(remove=False):
    codex = _codex()
    if remove:
        argv = [codex, 'mcp', 'remove', 'wechat_local']
    else:
        argv = [codex, 'mcp', 'add', 'wechat_local', '--', str(PYTHON), str(MCP_ENTRY)]
    subprocess.run(argv, check=True)
    return {'registered': not remove}


def main():
    ap = argparse.ArgumentParser(description='微信 Agent 本机只读数据接口')
    sub = ap.add_subparsers(dest='command', required=True)
    for name in ('start', 'stop', 'restart', 'uninstall', 'register-codex', 'unregister-codex', 'status', 'doctor'):
        sub.add_parser(name)
    p = sub.add_parser('call');p.add_argument('method');p.add_argument('params', nargs='?', default='{}')
    p = sub.add_parser('search');p.add_argument('--query', default='');p.add_argument('--conversation-id');p.add_argument('--sender-id');p.add_argument('--start');p.add_argument('--end');p.add_argument('--message-type');p.add_argument('--limit', type=int, default=50);p.add_argument('--offset', type=int, default=0)
    p = sub.add_parser('conversations');p.add_argument('--query', default='');p.add_argument('--limit', type=int, default=50);p.add_argument('--offset', type=int, default=0)
    p = sub.add_parser('context');p.add_argument('message_id');p.add_argument('--before', type=int, default=10);p.add_argument('--after', type=int, default=10);p.add_argument('--text-offset', type=int, default=0);p.add_argument('--limit', type=int, default=50);p.add_argument('--offset', type=int, default=0)
    p = sub.add_parser('attachment');p.add_argument('attachment_id');p.add_argument('--offset', type=int, default=0);p.add_argument('--length', type=int, default=8000)
    p = sub.add_parser('changes');p.add_argument('consumer_id');p.add_argument('--replay-history', action='store_true');p.add_argument('--limit', type=int, default=50)
    p = sub.add_parser('ack');p.add_argument('consumer_id');p.add_argument('cursor', type=int)
    args = vars(ap.parse_args());command = args.pop('command')
    try:
        if command == 'start':
            result = install()
        elif command == 'stop':
            result = stop()
        elif command == 'restart':
            stop();result = install()
        elif command == 'uninstall':
            result = stop();PLIST.unlink(missing_ok=True);result['uninstalled'] = True
        elif command == 'register-codex':
            result = register_codex()
        elif command == 'unregister-codex':
            result = register_codex(remove=True)
        elif command == 'doctor':
            result = doctor()
        elif command == 'call':
            result = rpc(args['method'], json.loads(args['params']))
        else:
            methods = {'status': 'get_status', 'search': 'search_messages', 'conversations': 'find_conversations',
                       'context': 'get_context', 'attachment': 'read_attachment', 'changes': 'get_changes', 'ack': 'ack_changes'}
            result = rpc(methods[command], args)
        print(json.dumps(result, ensure_ascii=False, indent=2))
    except Exception as e:
        print(json.dumps({'error': str(e)}, ensure_ascii=False), file=sys.stderr)
        raise SystemExit(1)


if __name__ == '__main__':
    main()
