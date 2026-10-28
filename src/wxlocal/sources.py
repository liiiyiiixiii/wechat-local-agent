"""Source fingerprinting: cheap identity for encrypted DBs and the media trees.

`signature`/`generation` tell the collector whether a source database changed or was
replaced. `media_signature` is intentionally cheap and only covers trees where WeChat
writes new attachments in place; volatile caches and derivative thumbnails are left out
so normal WeChat use does not trigger archive rebuilds.
"""
import hashlib
from pathlib import Path

from .util import dumps, sha

# New attachments appear under msg/attach; msg/file, msg/video and cache are covered by
# the text collector trigger or the hourly archive refresh instead.
MEDIA_SIGNATURE_AREAS = ('msg/attach', 'resource', 'business/emoticon', 'business/forward')


def signature(path):
    result = []
    for p in (path, Path(str(path) + '-wal')):
        try:
            s = p.stat();result.append([s.st_ino, s.st_size, s.st_mtime_ns])
        except FileNotFoundError:
            result.append(None)
    return dumps(result)


def generation(path):
    with Path(path).open('rb') as f:
        header = f.read(16)
    if header == b'SQLite format 3\0':
        # Plain fixture identity also detects replacement without a SQLCipher salt.
        return sha(dumps([str(path), path.stat().st_ino]))[:24]
    return hashlib.sha256(header).hexdigest()[:24]


def media_signature(account):
    h = hashlib.sha256()
    for area in MEDIA_SIGNATURE_AREAS:
        for p in sorted((account / area).rglob('*')):
            if p.is_symlink():
                continue
            try:
                st = p.stat()
                if p.is_file():
                    # Thumbnails are regenerated when an image is merely viewed.
                    if p.name.endswith(('_t.dat', '_h.dat')):
                        continue
                    h.update(dumps([str(p.relative_to(account)), st.st_size, st.st_mtime_ns]).encode())
            except FileNotFoundError:
                continue
    root = account / 'db_storage'
    for p in sorted(root.rglob('*.db')):
        if p.name.startswith('media_') or p.name in ('hardlink.db', 'message_resource.db'):
            h.update((str(p) + signature(p)).encode())
    return h.hexdigest()
