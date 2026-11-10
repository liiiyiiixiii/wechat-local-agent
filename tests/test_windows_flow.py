"""Public Windows entry points, with a private synthetic SQLCipher account."""

import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest

from wxlocal.archive.sqlcipher import DB

ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(os.name == 'nt', 'Windows named pipe and background process')
class WindowsFlow(unittest.TestCase):
    def test_install_query_archive_and_recover(self):
        with tempfile.TemporaryDirectory(prefix='微信 验收 ') as temp:
            base = Path(temp)
            home = base / '用户 状态'
            account = base / '模拟账号'
            db = account / 'db_storage/message/message_0.db'
            db.parent.mkdir(parents=True)
            keys = base / 'keys.json'
            key = '53' * 32
            keys.write_text(json.dumps({'keys': {'message/message_0.db': key}}), encoding='utf-8')
            table = 'Msg_' + hashlib.md5(b'fixture').hexdigest()
            attachment = account / 'msg/file/说明.txt'
            attachment.parent.mkdir(parents=True)
            attachment.write_text('附件排期', encoding='utf-8')
            with DB(db, key, fixture_write=True) as w:
                w.query("CREATE TABLE Name2Id(user_name TEXT);INSERT INTO Name2Id VALUES('fixture')")
                w.query(
                    f'CREATE TABLE {table}(local_id INTEGER PRIMARY KEY,server_id INTEGER,local_type INTEGER,create_time INTEGER,real_sender_id INTEGER,message_content TEXT)'
                )
                w.query(f"INSERT INTO {table} VALUES(1,100,1,1735689600,1,'项目排期')")
                w.query(
                    f"INSERT INTO {table} VALUES(2,101,49,1735689600,1,'<appmsg><type>6</type><title>说明.txt</title></appmsg>')"
                )
                w.query(
                    f"""INSERT INTO {table} VALUES(3,102,47,1735689601,1,'<msg><emoji md5="aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa" cdnurl="https://wxapp.tc.qq.com/test"/></msg>')"""
                )
            before = db.read_bytes()
            env = {**os.environ, 'WXLOCAL_HOME': str(home), 'PYTHONUTF8': '1'}

            # Trap outbound IP sockets in all service/archive subprocesses without sending traffic.
            guard = base / 'network-guard'
            guard.mkdir()
            (guard / 'sitecustomize.py').write_text(
                'import os,sys,socket,pathlib\n'
                'def guard(event,args):\n'
                " if ((event=='socket.connect' and args[0].family in (socket.AF_INET,socket.AF_INET6) and args[1][0] not in ('127.0.0.1','::1')) or (event=='socket.getaddrinfo' and args[0] not in ('localhost','127.0.0.1','::1',None))):\n"
                "  pathlib.Path(os.environ['WX_TEST_NETWORK']).touch()\n"
                "  raise PermissionError('offline acceptance')\n"
                'sys.addaudithook(guard)\n',
                encoding='utf-8',
            )
            env.update(PYTHONPATH=str(guard), WX_TEST_NETWORK=str(base / 'network-attempt'))

            def run(*args, ok=True):
                result = subprocess.run(
                    [sys.executable, *map(str, args)],
                    env=env,
                    capture_output=True,
                    encoding='utf-8',
                    timeout=90,
                )
                if ok and result.returncode:
                    log = home / 'agent-service/daemon.log'
                    self.fail(
                        result.stderr
                        + (log.read_text(encoding='utf-8', errors='replace') if log.exists() else '')
                    )
                return result

            def cli(*args):
                return json.loads(run('-m', 'wxlocal', *args).stdout)

            def until(check):
                deadline = time.monotonic() + 35
                while time.monotonic() < deadline:
                    if check():
                        return
                    time.sleep(0.25)
                self.fail('condition timed out: ' + str(cli('status')))

            cli('configure', '--account', account, '--keys', keys)
            # A syntactically valid key for the wrong database must not pass preflight.
            correct_keys = keys.read_bytes()
            keys.write_text(json.dumps({'keys': {'message/message_0.db': '34' * 32}}), encoding='utf-8')
            wrong = run('-m', 'wxlocal', 'doctor', ok=False)
            self.assertNotEqual(wrong.returncode, 0)
            self.assertIn('source_error', json.loads(wrong.stdout))
            keys.write_bytes(correct_keys)
            self.assertFalse(cli('doctor')['allow_cdn'])
            import win32security as security
            from wxlocal.windows import user_sid

            descriptor = security.GetNamedSecurityInfo(
                str(home), security.SE_FILE_OBJECT, security.DACL_SECURITY_INFORMATION
            )
            acl = descriptor.GetSecurityDescriptorDacl()
            self.assertGreater(acl.GetAceCount(), 0)
            self.assertTrue(all(acl.GetAce(i)[2] == user_sid() for i in range(acl.GetAceCount())))

            try:
                cli('start')
                until(lambda: cli('status')['initialized'])
                hit = cli('search', '--query', '项目排期')['items'][0]
                self.assertEqual(cli('context', hit['id'])['items'][0]['id'], hit['id'])
                self.assertEqual(cli('start')['daemon_pid'], cli('status')['daemon_pid'])
                until(lambda: bool(cli('search', '--query', '附件排期')['items']))
                summary = run(ROOT / 'tests/live_mcp_check.py')
                self.assertTrue(json.loads(summary.stdout)['consumer_ack'])
                self.assertFalse((home / 'agent-service/acceptance-evidence.json').exists())
                cli('stop')
                keys.rename(keys.with_suffix('.bak'))
                cli('start')
                until(lambda: cli('status')['sync']['state'] == 'degraded')
                self.assertNotEqual(run(ROOT / 'tests/live_mcp_check.py', ok=False).returncode, 0)
                keys.with_suffix('.bak').rename(keys)
                until(lambda: cli('status')['initialized'])
                self.assertEqual(cli('search', '--query', '项目排期')['items'][0]['id'], hit['id'])
            finally:
                cli('stop')
            run('-m', 'wxlocal.archive.pipeline', '--offline')
            archive = Path(json.loads((home / 'exports/latest.json').read_text(encoding='utf-8'))['path'])
            self.assertEqual(
                json.loads((archive / 'archive.json').read_text(encoding='utf-8'))['status'],
                'complete_with_media_gaps',
            )
            run('-m', 'wxlocal.archive.pipeline', '--resume', archive, '--offline')
            run('-m', 'wxlocal.archive.audit', archive)
            asset = next((archive / 'assets').rglob('*.txt'))
            asset.write_bytes(b'x' * asset.stat().st_size)
            for flags in ([], ['-O']):
                self.assertNotEqual(
                    run(*flags, '-m', 'wxlocal.archive.audit', archive, ok=False).returncode, 0
                )
            self.assertEqual(before, db.read_bytes())
            self.assertFalse((base / 'network-attempt').exists())
