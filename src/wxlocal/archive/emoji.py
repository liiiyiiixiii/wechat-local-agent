"""Retrieve only explicitly referenced WeChat CDN emoji; require expected MD5.

Run as a script: ``python emoji.py <archive-dir>``. The pipeline writes emoji download
jobs first; this tool only fetches allowlisted CDN hosts and validates the message MD5.
"""
import concurrent.futures
import hashlib
import json
import ssl
import sys
import urllib.parse
import urllib.request
from pathlib import Path

HOSTS = {'wxapp.tc.qq.com', 'vweixinf.tc.qq.com', 'mmbiz.qpic.cn'}
MAX_BYTES = 20 * 1024 * 1024


def allowed(url):
    try:
        parts = urllib.parse.urlsplit(url)
        return (parts.scheme in ('http','https') and parts.hostname in HOSTS and
                not parts.username and not parts.password and parts.port in (None,80,443))
    except ValueError:
        return False


class SafeRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if not allowed(newurl):
            raise ValueError('redirect_host_not_allowed')
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def extension(data):
    if data.startswith(b'GIF8'):
        return '.gif'
    if data.startswith(b'\xff\xd8\xff'):
        return '.jpg'
    if data.startswith(b'\x89PNG\r\n\x1a\n'):
        return '.png'
    if data[:4] == b'RIFF' and data[8:12] == b'WEBP':
        return '.webp'
    return None


def ssl_context():
    ca = Path(__file__).with_name('system-ca.pem')
    return ssl.create_default_context(cafile=str(ca)) if ca.exists() else ssl.create_default_context()


def fetch(job, out, context):
    for url in job['urls']:
        if not allowed(url):
            continue
        parts = urllib.parse.urlsplit(url)
        url = urllib.parse.urlunsplit((parts.scheme, parts.netloc, parts.path, parts.query, ''))
        try:
            opener = urllib.request.build_opener(SafeRedirect(), urllib.request.HTTPSHandler(context=context))
            with opener.open(url, timeout=15) as response:
                data = response.read(MAX_BYTES + 1)
            if len(data) > MAX_BYTES:
                raise ValueError('size_limit')
            ext = extension(data)
            if not ext or hashlib.md5(data).hexdigest() != job['md5']:
                continue
            digest = hashlib.sha256(data).hexdigest()
            relative = 'assets/' + digest[:2] + '/' + digest + ext
            dest = out / relative
            dest.parent.mkdir(parents=True, exist_ok=True)
            if not dest.exists():
                dest.write_bytes(data)
            return {'id': 'cdn_emoji_' + job['md5'], 'source': 'wechat-cdn/emoji/' + job['md5'] + ext,
                    'status': 'downloaded_verified',
                    'original': {'path': relative, 'sha256': digest, 'size': len(data)},
                    'expected_md5_verified': True}
        except Exception:
            continue
    return {'id': 'cdn_emoji_' + job['md5'], 'source': 'wechat-cdn/emoji/' + job['md5'],
            'status': 'download_unavailable'}


def main():
    out = Path(sys.argv[1])
    jobs = [json.loads(line) for line in (out / 'emoji-download-jobs.jsonl').read_text(encoding='utf-8').splitlines()]
    context = ssl_context()
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        rows = list(pool.map(lambda job: fetch(job, out, context), jobs))
    (out / 'emoji-download-results.jsonl').write_text(''.join(json.dumps(x) + '\n' for x in rows))
    print(json.dumps({'emoji_requested': len(rows),
                      'emoji_downloaded_verified': sum(x['status'] == 'downloaded_verified' for x in rows)}))


if __name__ == '__main__':
    main()
