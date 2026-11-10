"""Canonical source-row decoding for both live collection and archive export."""

import base64
import datetime as dt
import re
import xml.etree.ElementTree as ET
from .config import TZ
from .util import sha


def decode(v):
    if v is None:
        return ''
    if isinstance(v, str):
        return v
    if isinstance(v, bytes):
        if v.startswith(b'\x28\xb5\x2f\xfd'):
            try:
                from compression import zstd
            except ImportError:
                import zstandard as zstd
            try:
                if hasattr(zstd, 'ZstdDecompressor') and hasattr(zstd.ZstdDecompressor, 'stream_reader'):
                    with zstd.ZstdDecompressor().stream_reader(v) as stream:
                        v = stream.read()
                else:
                    v = zstd.decompress(v)
            except zstd.ZstdError as exc:
                raise ValueError('invalid_compressed_content') from exc
        return v.decode('utf8', errors='replace')
    return str(v)


def rawjson(v):
    if isinstance(v, bytes):
        return {'encoding': 'base64', 'data': base64.b64encode(v).decode()}
    if isinstance(v, int) and abs(v) > 9007199254740991:
        return str(v)
    return v


def fields(content):
    start = content.find('<')
    data = {
        'title': '',
        'description': '',
        'url': '',
        'file_name': '',
        'file_size': 0,
        'md5s': [],
        'app_type': None,
        'readable': '',
    }
    if start < 0:
        return data
    try:
        r = ET.fromstring(content[start:])
    except ET.ParseError:
        return data

    def text(name):
        x = r.find('.//' + name)
        return ''.join(x.itertext()).strip() if x is not None else ''

    data.update(title=text('title'), description=text('des'), url=text('url'))
    app = r.find('.//appmsg')
    app = app if app is not None else (r if r.tag == 'appmsg' else None)
    if app is not None:
        data['app_type'] = app.findtext('type')
        if data['app_type'] == '6':
            data['file_name'] = data['title']
            size = text('totallen')
            if size.isascii() and size.isdigit() and len(size) < 20:
                data['file_size'] = int(size)
            elif size:
                data['parse_issue'] = 'invalid_attachment_size'
    for e in r.iter():
        for k, v in e.attrib.items():
            if 'md5' in k.lower() and re.fullmatch('[a-fA-F0-9]{32}', v):
                data['md5s'].append(v.lower())
        if 'md5' in e.tag.lower() and re.fullmatch('[a-fA-F0-9]{32}', e.text or ''):
            data['md5s'].append(e.text.lower())
    data['readable'] = '\n'.join(
        dict.fromkeys(
            x.strip() for x in r.itertext() if x.strip() and not re.fullmatch('[a-fA-F0-9]{32,}', x.strip())
        )
    )
    return data


def normalize(raw, database, table, chat, ids):
    issues = []
    try:
        content = decode(raw.get('message_content')) or decode(raw.get('compress_content'))
    except ValueError:
        content = '[unreadable compressed content]'
        issues.append('invalid_compressed_content')
    try:
        timestamp = int(raw.get('create_time') or 0)
        display_time = dt.datetime.fromtimestamp(timestamp, TZ)
    except (OSError, OverflowError, ValueError, TypeError):
        timestamp = 0
        display_time = dt.datetime.fromtimestamp(0, TZ)
        issues.append('invalid_timestamp')
    sender = ids.get(raw.get('real_sender_id'))
    if chat.endswith('@chatroom') and sender and content.startswith(sender + ':\n'):
        content = content[len(sender) + 2 :]
    try:
        typ = int(raw.get('local_type') or 0)
    except (ValueError, TypeError):
        typ = 0
        issues.append('invalid_message_type')
    kind = {
        1: 'text',
        3: 'image',
        34: 'voice',
        43: 'video',
        47: 'emoji',
        48: 'location',
        42: 'contact',
        50: 'call',
        10000: 'system',
    }.get(typ, 'app' if typ & 0xFFFFFFFF == 49 else 'unknown')
    try:
        meta = fields(content)
    except (ValueError, OverflowError):
        meta = {'file_name': '', 'readable': '', 'md5s': []}
    if issues:
        meta['parse_issues'] = issues
    if meta.get('file_name'):
        kind = 'file'
    return {
        'id': sha(database + '\0' + table + '\0' + str(raw['local_id']))[:24],
        'time': display_time.isoformat(),
        'local_type': typ,
        'session_id': chat,
        'sender_id': sender,
        'local_id': raw['local_id'],
        'server_id': str(raw.get('server_id') or 0),
        'timestamp': timestamp,
        'type': kind,
        'content': content,
        'text': content if kind == 'text' else meta.get('readable') or content,
        'metadata': meta,
        'source': {'database': database, 'table': table},
        'raw': {k: rawjson(v) for k, v in raw.items()},
        'assets': [],
    }
