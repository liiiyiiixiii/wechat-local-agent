"""Read-only SQLCipher adapter and incremental reconciliation. No WeChat UI actions."""
import ctypes as C
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import time

from .archive import pipeline as exporter
from .archive.sqlcipher import DB, load_key
from .sources import generation, signature
from .store import Store, message_id
from .util import dumps, sha

# Only these message kinds can add or change archive attachments. Text-only traffic is
# picked up by the hourly archive refresh instead of rebuilding media on every message.
MEDIA_KINDS = {'image', 'voice', 'video', 'file', 'emoji'}


class Native:
    def __init__(self, path, key):
        self.db = DB(path, key)
        self.lib = self.db.lib
        for name, args, restype in (
            ('sqlite3_prepare_v2', [C.c_void_p, C.c_char_p, C.c_int, C.POINTER(C.c_void_p), C.c_void_p], C.c_int),
            ('sqlite3_step', [C.c_void_p], C.c_int),
            ('sqlite3_finalize', [C.c_void_p], C.c_int),
            ('sqlite3_column_count', [C.c_void_p], C.c_int),
            ('sqlite3_column_name', [C.c_void_p, C.c_int], C.c_char_p),
            ('sqlite3_column_type', [C.c_void_p, C.c_int], C.c_int),
            ('sqlite3_column_int64', [C.c_void_p, C.c_int], C.c_longlong),
            ('sqlite3_column_double', [C.c_void_p, C.c_int], C.c_double),
            ('sqlite3_column_blob', [C.c_void_p, C.c_int], C.c_void_p),
            ('sqlite3_column_text', [C.c_void_p, C.c_int], C.c_void_p),
            ('sqlite3_column_bytes', [C.c_void_p, C.c_int], C.c_int),
        ):
            fn = getattr(self.lib, name);fn.argtypes = args;fn.restype = restype

    def query(self, sql):
        stmt = C.c_void_p()
        rc = self.lib.sqlite3_prepare_v2(self.db.ptr, sql.encode(), -1, C.byref(stmt), None)
        if rc:
            raise RuntimeError('source_prepare_error_' + str(rc))
        try:
            n = self.lib.sqlite3_column_count(stmt)
            names = [self.lib.sqlite3_column_name(stmt, i).decode() for i in range(n)]
            rows = []
            while True:
                rc = self.lib.sqlite3_step(stmt)
                if rc == 101:
                    break
                if rc != 100:
                    raise RuntimeError('source_step_error_' + str(rc))
                row = {}
                for i, name in enumerate(names):
                    typ = self.lib.sqlite3_column_type(stmt, i)
                    if typ == 1:
                        value = self.lib.sqlite3_column_int64(stmt, i)
                    elif typ == 2:
                        value = self.lib.sqlite3_column_double(stmt, i)
                    elif typ in (3, 4):
                        ptr = (self.lib.sqlite3_column_text if typ == 3 else self.lib.sqlite3_column_blob)(stmt, i)
                        value = C.string_at(ptr, self.lib.sqlite3_column_bytes(stmt, i)) if ptr else b''
                        if typ == 3:
                            value = value.decode('utf8', 'replace')
                    else:
                        value = None
                    row[name] = value
                rows.append(row)
            return rows
        finally:
            self.lib.sqlite3_finalize(stmt)

    def close(self):
        self.db.close()


class Plain:
    """Explicit fixture adapter, never an automatic fallback for failed decryption."""
    def __init__(self, path):
        self.c = sqlite3.connect(Path(path).as_uri() + '?mode=ro', uri=True)
        self.c.row_factory = sqlite3.Row
        self.c.execute('PRAGMA query_only=ON')

    def query(self, sql):
        return [dict(r) for r in self.c.execute(sql)]

    def close(self):
        self.c.close()


def normalize(raw, database, table, chat, ids):
    content = exporter.decode(raw.get('message_content'))
    if not content and raw.get('compress_content'):
        content = exporter.decode(raw['compress_content'])
    sender = ids.get(raw.get('real_sender_id'))
    if chat.endswith('@chatroom') and sender and content.startswith(sender + ':\n'):
        content = content[len(sender) + 2:]
    typ = int(raw.get('local_type') or 0)
    kind = {1:'text', 3:'image', 34:'voice', 43:'video', 47:'emoji', 48:'location', 42:'contact', 50:'call', 10000:'system'}.get(typ, 'app' if typ & 0xffffffff == 49 else 'unknown')
    try:
        meta = exporter.fields(content)
    except (ValueError, OverflowError):
        meta = {'file_name': '', 'readable': '', 'md5s': []}
    if meta.get('file_name'):
        kind = 'file'
    return {'id': sha(database + '\0' + table + '\0' + str(raw['local_id']))[:24],
            'session_id': chat, 'sender_id': sender, 'local_id': raw['local_id'],
            'server_id': str(raw.get('server_id') or 0), 'timestamp': int(raw.get('create_time') or 0),
            'type': kind, 'content': content, 'text': content if kind == 'text' else meta.get('readable') or content,
            'metadata': meta, 'source': {'database': database, 'table': table},
            'raw': {k: exporter.rawjson(v) for k, v in raw.items()}, 'assets': []}


class Collector:
    def __init__(self, state, account, keys=None, fixture=False):
        self.state, self.account = Path(state), Path(account)
        self.root = self.account / 'db_storage'
        self.keys, self.fixture = keys, fixture
        self.keycache = {}
        self.last_full = 0

    def open(self, path):
        if self.fixture:
            return Plain(path)
        cache_key = (str(path), generation(path), self.keys.stat().st_mtime_ns)
        if cache_key not in self.keycache:
            self.keycache[cache_key] = load_key(self.keys, path)
        return Native(path, self.keycache[cache_key])

    def contacts(self, store):
        path = self.root / 'contact/contact.db'
        if not path.exists():
            return
        before = signature(path)
        if before == store.meta('contact_signature'):
            return
        db = self.open(path)
        try:
            contacts = {r['username']: {'nickname': r['nick_name'], 'remark': r['remark']}
                        for r in db.query('SELECT username,nick_name,remark FROM contact')}
        finally:
            db.close()
        with store.c:
            store.contacts(contacts);store.setmeta('contact_signature', before)

    def sync_once(self, force=False):
        started = time.time()
        store = Store(self.state / 'index.sqlite', self.account.name)
        errors, changed = [], 0
        full = force or started - self.last_full >= 3600
        try:
            try:
                self.contacts(store)
            except Exception as e:
                errors.append({'database': 'contact/contact.db', 'code': type(e).__name__})
            paths = sorted((self.root / 'message').glob('message_[0-9]*.db'))
            if not paths:
                errors.append({'database': 'message', 'code': 'no_message_databases'})
            existing = {str(p.relative_to(self.root)) for p in paths}
            for source in store.c.execute('SELECT db FROM sources').fetchall():
                if source['db'] not in existing:
                    with store.c:
                        store.c.execute('UPDATE sources SET error=? WHERE db=?', ('source_missing', source['db']))
                    errors.append({'database': source['db'], 'code': 'source_missing'})
            for path in paths:
                rel = str(path.relative_to(self.root))
                old = store.c.execute('SELECT * FROM sources WHERE db=?', (rel,)).fetchone()
                sig = signature(path);gen = generation(path)
                if not full and old and old['signature'] == sig and not old['error']:
                    continue
                try:
                    db = self.open(path)
                    try:
                        db.query('BEGIN')
                        names = [r['name'] for r in db.query("SELECT name FROM sqlite_master WHERE type='table'")]
                        if 'Name2Id' not in names:
                            raise RuntimeError('source_schema_incompatible')
                        ids = {r['rowid']: r['user_name'] for r in db.query('SELECT rowid,user_name FROM Name2Id')}
                        users = set(ids.values()) | {r[0] for r in store.c.execute('SELECT id FROM conversations')}
                        mapped = {hashlib.md5(u.encode()).hexdigest(): u for u in users}
                        tables = [n for n in names if re.fullmatch('Msg_[a-fA-F0-9]{32}', n)]
                        batches = [(table, db.query('SELECT * FROM ' + exporter.ident(table))) for table in tables]
                        db.query('COMMIT')
                    finally:
                        db.close()
                    progress, seen = {}, set()
                    before_seq = store.c.execute('SELECT coalesce(max(seq),0) FROM changes').fetchone()[0]
                    media_changed = bool(old and old['generation'] != gen)
                    with store.c:
                        if old and old['generation'] != gen:
                            store.event('source_replaced', None, None, {'database': rel})
                        for table, rows in batches:
                            chat = mapped.get(table[4:].lower(), 'unresolved_' + table[4:])
                            progress[table] = {'max_local_id': max([r['local_id'] for r in rows] or [0]), 'rows': len(rows)}
                            for raw in rows:
                                rec = normalize(raw, rel, table, chat, ids)
                                if rec['type'] in MEDIA_KINDS:
                                    media_changed = True
                                seen.add(store.upsert(rec, gen))
                        # Preserve records removed from the source, but expose their absence explicitly.
                        previous = store.c.execute('SELECT id,version FROM messages WHERE origin_db=? AND present=1', (rel,)).fetchall()
                        for r in previous:
                            if r['id'] not in seen:
                                store.c.execute('UPDATE messages SET present=0,version=version+1 WHERE id=?', (r['id'],))
                                store.event('source_record_absent', r['id'], r['version']+1)
                        store.c.execute('INSERT OR REPLACE INTO sources VALUES(?,?,?,?,?,NULL)',
                                        (rel, gen, sig, dumps(progress), time.time()))
                        after_seq = store.c.execute('SELECT coalesce(max(seq),0) FROM changes').fetchone()[0]
                        changed += after_seq - before_seq
                        if media_changed:
                            store.queue_media()
                except Exception as e:
                    code = type(e).__name__
                    errors.append({'database': rel, 'code': code})
                    with store.c:
                        store.c.execute('INSERT INTO sources VALUES(?,?,?,?,?,?) ON CONFLICT(db) DO UPDATE SET error=excluded.error',
                                        (rel, gen, '', '{}', time.time(), 'read_or_key_error:' + code))
            if full and not errors:
                self.last_full = started
            with store.c:
                previous = store.meta('sync', {})
                store.setmeta('sync', {'state': 'degraded' if errors else 'ready', 'last_checked_at': time.time(),
                    'last_success_at': previous.get('last_success_at') if errors else time.time(),
                    'last_full_reconcile_at': self.last_full, 'duration_seconds': round(time.time()-started, 3),
                    'errors': errors, 'poll_interval_seconds': 2})
            return {'changed': changed, 'errors': errors, 'seconds': round(time.time()-started, 3)}
        finally:
            store.close()


def import_archive(state, account, archive, fixture=False):
    store = Store(Path(state) / 'index.sqlite', account.name)
    try:
        if store.meta('seed_complete'):
            return
        contacts = json.loads((archive / 'contacts.json').read_text())
        index = json.loads((archive / 'index.json').read_text())
        count = 0
        with store.c:
            store.contacts(contacts)
            for conversation in index:
                for line in (archive / conversation['folder'] / 'messages.jsonl').read_text().splitlines():
                    record = json.loads(line)
                    source = account / 'db_storage' / record['source']['database']
                    gen = generation(source) if source.exists() else 'archive-' + sha(str(archive))[:16]
                    mid = store.upsert(record, gen)
                    store.attach(mid, record, archive, emit=False)
                    count += 1
            expected = sum(i['message_count'] for i in index)
            if count != expected or store.c.execute('SELECT count(*) FROM messages').fetchone()[0] != expected:
                raise RuntimeError('archive_message_count_mismatch')
            store.setmeta('seed_complete', {'archive': str(archive), 'records': count, 'at': time.time()})
            store.c.execute("INSERT OR IGNORE INTO jobs(id,state,requested) VALUES('media','pending',1)")
    finally:
        store.close()
