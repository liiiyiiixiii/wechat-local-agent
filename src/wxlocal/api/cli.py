"""Current-user service lifecycle, diagnostics and query commands."""

import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

from ..client import rpc
from ..config import HOME, STATE, SETTINGS, LEGACY_ARCHIVE, PYTHON, config, prepare, online, documents_python
from ..lifecycle import start, stop, autostart


def _codex():
    candidate = os.environ.get('WECHAT_CODEX') or shutil.which('codex')
    if candidate:
        return candidate
    for path in sorted(
        (Path.home() / '.nvm/versions/node').glob('*/bin/codex'),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    ):
        if path.exists():
            return str(path)
    raise RuntimeError('codex_missing; install Codex or set WECHAT_CODEX')


def configure(account, keys, seed_archive=None, allow_cdn=False):
    prepare()
    data = {
        'accountDir': str(Path(account).expanduser().resolve()),
        'keysFile': str(Path(keys).expanduser().resolve()),
        'allowCdn': allow_cdn,
    }
    if seed_archive:
        data['seedArchive'] = str(Path(seed_archive).resolve())
    from tempfile import NamedTemporaryFile

    with NamedTemporaryFile(mode='w', encoding='utf-8', dir=HOME, delete=False) as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    Path(f.name).replace(SETTINGS)
    return {'configuration': str(SETTINGS), 'allow_cdn': allow_cdn, 'apply': 'restart service'}


def doctor():
    from ..archive.sqlcipher import DB, load_key

    report = {
        'platform': sys.platform,
        'configuration': str(SETTINGS),
        'index': str(STATE / 'index.sqlite'),
        'archives': str(LEGACY_ARCHIVE),
        'media_work': str(STATE / 'media-work'),
        'log': str(STATE / 'daemon.log'),
        'acceptance_summary': str(STATE / 'mcp-acceptance.json'),
    }
    try:
        cfg = config()
        report['allow_cdn'] = online()
        path = next(iter(sorted((cfg['account'] / 'db_storage/message').glob('message_[0-9]*.db'))), None)
        if not path:
            raise ValueError('message_database_missing: check accountDir/db_storage/message')
        if not cfg['keys'].is_file():
            raise ValueError('key_file_missing: check keysFile')
        with DB(path, load_key(cfg['keys'], path)) as db:
            tables = {r['name'] for r in db.query("SELECT name FROM sqlite_master WHERE type='table'")}
            if 'Name2Id' not in tables:
                raise ValueError('source_schema_incompatible: Name2Id missing')
        report['source_check'] = 'passed_first_message_database'
    except Exception as exc:
        report['source_error'] = (
            str(exc) if isinstance(exc, (ValueError, RuntimeError)) else type(exc).__name__
        )
    try:
        report['document_python'] = documents_python()
    except RuntimeError:
        report['document_python'] = None
    report['media_keys_configured'] = Path(
        os.environ.get('WECHAT_MEDIA_KEYS', HOME / 'media-keys.json')
    ).is_file()
    report['ffmpeg'] = shutil.which('ffmpeg')
    report['apple_ocr'] = (
        sys.platform == 'darwin' and (Path(__file__).parents[1] / 'archive/pdf_ocr').is_file()
    )
    report['node'] = os.environ.get('WECHAT_NODE') or shutil.which('node')
    report['silk_module'] = os.environ.get('WECHAT_SILK') or None
    try:
        report['service'] = rpc('get_status')
    except RuntimeError:
        report['service'] = 'stopped'
    return report


def register_codex(remove=False):
    codex = _codex()
    if remove:
        argv = [codex, 'mcp', 'remove', 'wechat_local']
    else:
        argv = [
            codex,
            'mcp',
            'add',
            'wechat_local',
            '--',
            str(PYTHON),
            '-m',
            'wxlocal',
            '--home',
            str(HOME),
            'mcp',
        ]
    subprocess.run(argv, check=True)
    return {'registered': not remove}


def main():
    ap = argparse.ArgumentParser(description='微信 Agent 本机只读数据接口')
    sub = ap.add_subparsers(dest='command', required=True)
    for name in (
        'start',
        'stop',
        'restart',
        'uninstall',
        'register-codex',
        'unregister-codex',
        'status',
        'doctor',
        'serve',
        'mcp',
        'enable-autostart',
        'disable-autostart',
    ):
        sub.add_parser(name)
    p = sub.add_parser('configure')
    p.add_argument('--account', required=True)
    p.add_argument('--keys', required=True)
    p.add_argument('--seed-archive')
    p.add_argument('--allow-cdn', action='store_true')
    p = sub.add_parser('call')
    p.add_argument('method')
    p.add_argument('params', nargs='?', default='{}')
    p = sub.add_parser('search')
    p.add_argument('--query', default='')
    p.add_argument('--conversation-id')
    p.add_argument('--sender-id')
    p.add_argument('--start')
    p.add_argument('--end')
    p.add_argument('--message-type')
    p.add_argument('--limit', type=int, default=50)
    p.add_argument('--offset', type=int, default=0)
    p = sub.add_parser('conversations')
    p.add_argument('--query', default='')
    p.add_argument('--limit', type=int, default=50)
    p.add_argument('--offset', type=int, default=0)
    p = sub.add_parser('context')
    p.add_argument('message_id')
    p.add_argument('--before', type=int, default=10)
    p.add_argument('--after', type=int, default=10)
    p.add_argument('--text-offset', type=int, default=0)
    p.add_argument('--limit', type=int, default=50)
    p.add_argument('--offset', type=int, default=0)
    p = sub.add_parser('attachment')
    p.add_argument('attachment_id')
    p.add_argument('--offset', type=int, default=0)
    p.add_argument('--length', type=int, default=8000)
    p = sub.add_parser('changes')
    p.add_argument('consumer_id')
    p.add_argument('--replay-history', action='store_true')
    p.add_argument('--limit', type=int, default=50)
    p = sub.add_parser('ack')
    p.add_argument('consumer_id')
    p.add_argument('cursor', type=int)
    args = vars(ap.parse_args())
    command = args.pop('command')
    try:
        if command == 'start':
            result = start()
        elif command == 'stop':
            result = stop()
        elif command == 'restart':
            stop()
            result = start()
        elif command == 'uninstall':
            autostart(False)
            result = stop()
            result['uninstalled'] = True
        elif command == 'register-codex':
            result = register_codex()
        elif command == 'unregister-codex':
            result = register_codex(remove=True)
        elif command == 'configure':
            result = configure(**args)
        elif command in ('enable-autostart', 'disable-autostart'):
            result = autostart(command == 'enable-autostart')
        elif command == 'serve':
            from ..service import main as serve

            return serve()
        elif command == 'mcp':
            from .mcp import main as mcp

            return mcp()
        elif command == 'doctor':
            result = doctor()
        elif command == 'call':
            result = rpc(args['method'], json.loads(args['params']))
        else:
            methods = {
                'status': 'get_status',
                'search': 'search_messages',
                'conversations': 'find_conversations',
                'context': 'get_context',
                'attachment': 'read_attachment',
                'changes': 'get_changes',
                'ack': 'ack_changes',
            }
            result = rpc(methods[command], args)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        if command == 'doctor' and 'source_error' in result:
            return 2
    except Exception as e:
        print(json.dumps({'error': str(e)}, ensure_ascii=False), file=sys.stderr)
        raise SystemExit(1)


if __name__ == '__main__':
    raise SystemExit(main())
