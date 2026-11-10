"""Independent durable media job. Reuses the verified codecs and attachment matching."""

import json
import os
from pathlib import Path
import shutil
import time

from .archive import pipeline as exporter
from .config import online
from .sources import generation, signature
from .store import Store, message_id
from .util import dumps


def seed_work(archive, work):
    work.mkdir(parents=True, exist_ok=True, mode=0o700)
    if (work / '.seeded').exists():
        return
    for sub in ('assets', 'document-text'):
        source = archive / sub
        if source.exists():
            for src in source.rglob('*'):
                rel = src.relative_to(archive)
                dest = work / rel
                if src.is_dir():
                    dest.mkdir(parents=True, exist_ok=True)
                elif src.is_file() and not src.is_symlink() and not dest.exists():
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    # Only immutable content-addressed binaries share inodes; document text is copied.
                    if sub == 'assets':
                        try:
                            os.link(src, dest)
                        except OSError:
                            shutil.copy2(src, dest)
                    else:
                        shutil.copy2(src, dest)
    for name in ('asset-inventory.jsonl', 'document-index.json'):
        if (archive / name).exists():
            shutil.copy2(archive / name, work / name)
    (work / '.seeded').write_text('immutable asset seed complete\n')


def run_job(state, account, keys, archive):
    state, account = Path(state), Path(account)
    work = state / 'media-work'
    work.mkdir(parents=True, exist_ok=True, mode=0o700)
    if archive:
        seed_work(Path(archive), work)
    # Independent snapshots for attachment references and voices; text sync never waits on OCR.
    sigpath = work / 'source-signatures.json'
    sigs = json.loads(sigpath.read_text(encoding='utf-8')) if sigpath.exists() else {}
    source_root = account / 'db_storage'
    paths = [
        source_root / 'contact/contact.db',
        source_root / 'hardlink/hardlink.db',
        source_root / 'message/message_resource.db',
    ]
    paths += sorted((source_root / 'message').glob('message_[0-9]*.db'))
    paths += sorted((source_root / 'message').glob('media_*.db'))
    generations = {}
    for p in paths:
        if not p.exists():
            continue
        rel = p.relative_to(source_root).as_posix()
        sig = signature(p)
        dest = work / 'databases' / rel
        if sigs.get(rel) != sig or not dest.exists():
            dest.with_suffix('.verification.json').unlink(missing_ok=True)
            exporter.snapshot(p, dest, keys)
            sigs[rel] = sig
        generations[rel] = generation(p)
    exporter.dump(sigpath, sigs)
    sessions, contacts, total = exporter.inventory_messages(work)
    assets = exporter.media_inventory(account, work, sessions)
    # Match the archive's existing allowlisted, hash-verified emoji retrieval policy.
    if online():
        assets = exporter.fetch_missing_emoji(work, sessions, assets)
    exporter.finalize(
        work,
        sessions,
        contacts,
        assets,
        total,
        {'account_id': account.name, 'schema': 'wechat-agent-media/1'},
        publish_latest=False,
    )
    store = Store(state / 'index.sqlite', account.name)
    try:
        for records in sessions.values():
            for offset in range(0, len(records), 100):
                with store.c:
                    for rec in records[offset : offset + 100]:
                        gen = generations.get(rec['source']['database'], 'unknown')
                        mid = message_id(account.name, rec, gen)
                        # A media snapshot may lag text sync. Never overwrite message body/version with it.
                        existing = store.c.execute('SELECT raw FROM messages WHERE id=?', (mid,)).fetchone()
                        if existing and existing['raw'] == dumps(rec['raw']):
                            store.attach(mid, rec, work)
        with store.c:
            store.setmeta('media_last_success_at', time.time())
    finally:
        store.close()


def main():
    from .config import STATE, config, prepare

    prepare()
    cfg = config()
    run_job(STATE, cfg['account'], cfg['keys'], cfg['archive'])


if __name__ == '__main__':
    main()
