"""Diagnose media-source churn without exposing filenames or content.

Compares only filesystem metadata for the areas the service fingerprints and writes a
category summary to `private/agent-service/media-source-audit.json`. Run repeatedly to
see which tree is causing archive rebuilds.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))

from wxlocal.config import STATE, config
from wxlocal.sources import MEDIA_SIGNATURE_AREAS, signature


def snapshot(account):
    items = {}
    for area in MEDIA_SIGNATURE_AREAS:
        for path in (account / area).rglob('*'):
            if path.is_symlink():
                continue
            try:
                stat = path.stat()
            except FileNotFoundError:
                continue
            if path.is_file() and not path.name.endswith(('_t.dat', '_h.dat')):
                items[str(path.relative_to(account))] = [stat.st_size, stat.st_mtime_ns]
    root = account / 'db_storage'
    for path in sorted(root.rglob('*.db')):
        if path.name.startswith('media_') or path.name in ('hardlink.db', 'message_resource.db'):
            items[str(path.relative_to(account))] = signature(path)
    return items


def main():
    account = config()['account']
    items = snapshot(account)
    path = STATE / 'media-source-audit.json'
    if path.exists():
        old = json.loads(path.read_text())
        changed = [key for key in set(old) | set(items) if old.get(key) != items.get(key)]
        print(json.dumps({'changed_count': len(changed),
                          'categories': sorted(set('/'.join(key.split('/')[:3]) for key in changed))}))
    else:
        print(json.dumps({'baseline_files': len(items)}))
    path.write_text(json.dumps(items))


if __name__ == '__main__':
    main()
