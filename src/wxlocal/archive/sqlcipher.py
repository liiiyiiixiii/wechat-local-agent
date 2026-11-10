#!/usr/bin/env python3
"""SQLCipher 4 read-only probe. No key extraction; no plaintext export.

Vendored from the MIT-licensed wechat-local-toolkit (`sqlcipher_probe.py`) so the
runtime package never depends on a sibling checkout. The only local adaptation is that
the configured WCDB build is offered as the default library candidate.

Keys enter through a mode-0600 file, never command-line arguments.
Production opens are SQLITE_OPEN_READONLY; only self-test creates toy databases.
"""

import argparse
import ctypes as C
import ctypes.util
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import tempfile
import time

from .. import config as _config


def library():
    candidates = [
        str(_config.wcdb_library()),
        '/opt/homebrew/opt/sqlcipher/lib/libsqlcipher.dylib',
        '/usr/local/opt/sqlcipher/lib/libsqlcipher.dylib',
        ctypes.util.find_library('sqlcipher'),
    ]
    name = next((x for x in candidates if x and (not x.startswith('/') or Path(x).exists())), None)
    if not name:
        raise RuntimeError('SQLCipher library missing; install with brew install sqlcipher')
    lib = C.CDLL(name)
    lib.sqlite3_open_v2.argtypes = [C.c_char_p, C.POINTER(C.c_void_p), C.c_int, C.c_char_p]
    lib.sqlite3_open_v2.restype = C.c_int
    lib.sqlite3_close.argtypes = [C.c_void_p]
    lib.sqlite3_key.argtypes = [C.c_void_p, C.c_void_p, C.c_int]
    lib.sqlite3_key.restype = C.c_int
    lib.sqlite3_exec.argtypes = [C.c_void_p, C.c_char_p, C.c_void_p, C.c_void_p, C.c_void_p]
    lib.sqlite3_exec.restype = C.c_int
    lib.sqlite3_busy_timeout.argtypes = [C.c_void_p, C.c_int]
    return lib


class NativeDB:
    def __init__(self, path, key, *, fixture_write=False):
        if not re.fullmatch(r'[0-9a-fA-F]{64}', key):
            raise ValueError('expected 64 hex characters for raw SQLCipher key')
        self.lib = library()
        self.ptr = C.c_void_p()
        flags = 6 if fixture_write else 1  # READWRITE|CREATE, or READONLY
        rc = self.lib.sqlite3_open_v2(os.fsencode(path), C.byref(self.ptr), flags, None)
        if rc:
            self.close()
            raise RuntimeError(f'SQLCipher open failed (code {rc})')
        try:
            key_arg = ("x'" + key + "'").encode('ascii')
            rc = self.lib.sqlite3_key(self.ptr, key_arg, len(key_arg))
            if rc:
                raise RuntimeError(f'SQLCipher key setup failed (code {rc})')
            self.query('PRAGMA cipher_compatibility=4;')
            self.lib.sqlite3_busy_timeout(self.ptr, 2000)
            if not fixture_write:
                self.query('PRAGMA query_only=ON;')
            self.query('SELECT count(*) FROM sqlite_master;')
        except BaseException:
            self.close()
            raise

    def query(self, sql):
        rows = []
        callback_type = C.CFUNCTYPE(
            C.c_int, C.c_void_p, C.c_int, C.POINTER(C.c_char_p), C.POINTER(C.c_char_p)
        )

        def collect(_, n, values, names):
            rows.append(
                {
                    names[i].decode(): values[i].decode('utf-8', 'replace') if values[i] is not None else None
                    for i in range(n)
                }
            )
            return 0

        callback = callback_type(collect)
        rc = self.lib.sqlite3_exec(self.ptr, sql.encode(), callback, None, None)
        if rc:
            # No raw SQL or native error strings, since those may reveal secrets/content.
            raise RuntimeError(f'SQLCipher query failed (code {rc})')
        return rows

    def close(self):
        if self.ptr:
            self.lib.sqlite3_close(self.ptr)
            self.ptr = C.c_void_p()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


class WindowsDB:
    """SQLCipher DB-API wheel, including JSON-safe errors for failed key checks."""

    def __init__(self, path, key, *, fixture_write=False):
        from sqlcipher3 import dbapi2 as cipher

        if not re.fullmatch(r'[0-9a-fA-F]{64}', key):
            raise ValueError('invalid_sqlcipher_key')
        mode = 'rwc' if fixture_write else 'ro'
        self.connection = cipher.connect(
            Path(path).resolve().as_uri() + '?mode=' + mode, uri=True, isolation_level=None
        )
        self.connection.row_factory = cipher.Row
        try:
            self.connection.execute(f"""PRAGMA key = "x'{key}'" """)
            self.connection.execute('PRAGMA cipher_compatibility=4')
            if not fixture_write:
                self.connection.execute('PRAGMA query_only=ON')
            self.connection.execute('SELECT count(*) FROM sqlite_master').fetchone()
        except Exception:
            self.close()
            raise RuntimeError('database_key_or_format_invalid') from None
        self.fixture_write = fixture_write

    def query(self, sql):
        try:
            if self.fixture_write and ';' in sql.strip().rstrip(';'):
                # Execute individually: executescript commits an open transaction.
                from sqlcipher3 import complete_statement

                statement = ''
                for char in sql:
                    statement += char
                    if char == ';' and complete_statement(statement):
                        self.connection.execute(statement)
                        statement = ''
                if statement.strip():
                    self.connection.execute(statement)
                return []
            return [dict(r) for r in self.connection.execute(sql)]
        except Exception as exc:
            raise RuntimeError('sqlcipher_query_failed') from exc

    def close(self):
        self.connection.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


DB = WindowsDB if os.name == 'nt' and not os.environ.get('SQLCIPHER_LIBRARY') else NativeDB


def load_key(path, db_path):
    p = Path(path)
    if os.name != 'nt' and p.stat().st_mode & 0o077:
        raise RuntimeError('key file must be private (chmod 600)')
    data = json.loads(p.read_text(encoding='utf-8-sig'))
    master = data.get('passphrase_hex') if isinstance(data, dict) else None
    if isinstance(data, dict) and isinstance(data.get('keys'), dict):
        data = data['keys']
    if not isinstance(data, dict):
        raise RuntimeError('key manifest must be an object')
    target = Path(db_path).resolve()
    with target.open('rb') as f:
        salt = f.read(16).hex()
    entries = []
    for name, value in data.items():
        normalized = name.replace('\\', '/')
        absolute_match = Path(normalized).is_absolute() and Path(normalized).resolve() == target
        if normalized == salt or target.as_posix().endswith('/' + normalized) or absolute_match:
            if isinstance(value, dict):
                value = value.get('enc_key', value.get('key'))
            if isinstance(value, str) and re.fullmatch('[0-9a-fA-F]{64}', value):
                entries.append(value.lower())
    if len(set(entries)) != 1:
        if not entries and isinstance(master, str) and re.fullmatch('[0-9a-fA-F]{64}', master):
            with target.open('rb') as f:
                page = f.read(4096)
            derived = hashlib.pbkdf2_hmac('sha512', bytes.fromhex(master), page[:16], 256000, 32)
            # Opening schema page 1 verifies SQLCipher's page MAC before accepting the key.
            with DB(target, derived.hex()):
                return derived.hex()
        raise RuntimeError('missing or ambiguous key for this database')
    return entries[0]


def verify(path, key_path):
    key = load_key(key_path, path)
    started = time.perf_counter()
    with DB(path, key) as db:
        rows = db.query("SELECT count(*) AS count FROM sqlite_master WHERE type='table';")
        result = {
            'verified': True,
            'table_count': int(rows[0]['count']),
            'query_only': db.query('PRAGMA query_only;')[0]['query_only'],
            'cipher_version': db.query('PRAGMA cipher_version;')[0]['cipher_version'],
            'elapsed_ms': round((time.perf_counter() - started) * 1000, 2),
        }
        return result


def self_test():
    from .wal import wal_info

    checks = []
    with tempfile.TemporaryDirectory(prefix='sqlcipher-fixture-') as d:
        root = Path(d)
        db_path = root / 'fixture.db'
        key = os.urandom(32).hex()
        with DB(db_path, key, fixture_write=True) as writer:
            writer.query(
                'PRAGMA journal_mode=WAL; PRAGMA wal_autocheckpoint=0; '
                'CREATE TABLE messages(id INTEGER PRIMARY KEY, content TEXT); '
                "INSERT INTO messages VALUES(1, '已合并的测试消息'); "
                'PRAGMA wal_checkpoint(TRUNCATE);'
            )
            before = hashlib.sha256(db_path.read_bytes()).hexdigest()
            writer.query("INSERT INTO messages VALUES(2, '仅在 WAL 中的新消息');")
            assert before == hashlib.sha256(db_path.read_bytes()).hexdigest()
            checks.append('new_commit_did_not_change_main_db')
            wal = Path(str(db_path) + '-wal')
            info = wal_info(wal)
            assert info['header_checksum_valid'] and info['commit_markers_in_prefix'] >= 1
            checks.append('wal_header_and_frame_checksums_verified')
            damaged = bytearray(wal.read_bytes())
            damaged[32 + 24 + 100] ^= 1
            broken = root / 'damaged.wal'
            broken.write_bytes(damaged)
            assert wal_info(broken)['scan_end'] == 'invalid_or_incomplete_frame'
            checks.append('corrupt_wal_frame_rejected')
            assert db_path.read_bytes()[:16] != b'SQLite format 3\x00'
            checks.append('fixture_is_encrypted')
            with DB(db_path, key) as reader:
                rows = reader.query('SELECT id, content FROM messages ORDER BY id;')
                assert len(rows) == 2 and rows[1]['content'] == '仅在 WAL 中的新消息'
                checks.append('readonly_reader_sees_committed_wal_message')
                try:
                    reader.query("INSERT INTO messages VALUES(3, 'must fail');")
                except RuntimeError:
                    checks.append('readonly_write_rejected')
                else:
                    raise AssertionError('write was allowed')
                writer.query("BEGIN; INSERT INTO messages VALUES(3, 'uncommitted');")
                assert reader.query('SELECT count(*) AS n FROM messages;')[0]['n'] in ('2', 2)
                writer.query('ROLLBACK;')
                checks.append('uncommitted_message_not_visible')
                writer.query("INSERT INTO messages VALUES(4, 'next commit');")
                assert reader.query('SELECT count(*) AS n FROM messages;')[0]['n'] in ('3', 3)
                checks.append('same_connection_sees_next_commit')
            copied = root / 'db_only.db'
            shutil.copy2(db_path, copied)
            with DB(copied, key) as db_only:
                assert db_only.query('SELECT count(*) AS n FROM messages;')[0]['n'] in ('1', 1)
                checks.append('copying_only_db_misses_wal_messages')
            try:
                with DB(db_path, '00' * 32):
                    pass
            except RuntimeError:
                checks.append('wrong_key_rejected')
            else:
                raise AssertionError('wrong key was accepted')
            key_file = root / 'keys.json'
            fd = os.open(key_file, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, 'w') as f:
                json.dump({str(db_path): {'enc_key': key}}, f)
            assert verify(db_path, key_file)['verified']
            checks.append('private_key_manifest_verified')
            assert before == hashlib.sha256(db_path.read_bytes()).hexdigest()
            checks.append('reader_did_not_checkpoint_or_change_main_db')
    return {'fixture_tests_passed': len(checks), 'checks': checks, 'real_wechat_messages_verified': False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    sub.add_parser('self-test')
    v = sub.add_parser('verify')
    v.add_argument('--db', required=True)
    v.add_argument('--key-file', required=True)
    args = parser.parse_args()
    try:
        result = self_test() if args.command == 'self-test' else verify(args.db, args.key_file)
        print(json.dumps(result, ensure_ascii=False, indent=2))
    except (RuntimeError, ValueError, OSError) as e:
        print(json.dumps({'ok': False, 'error': str(e)}, ensure_ascii=False))
        return 2
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
