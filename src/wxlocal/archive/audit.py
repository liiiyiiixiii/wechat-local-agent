"""Independent verification of an archive written by `pipeline.py`.

Run as a module: ``python -m wxlocal.archive.audit <archive-dir>``.
The audit re-reads every generated JSONL against the decrypted snapshots and re-hashes
each asset, without trusting the pipeline's own report.
"""

import json
import sys
import wave
from pathlib import Path

from . import pipeline as e


def require(value, label):
    if not value:
        raise ValueError('archive_audit_failed: ' + label)


def main():
    out = Path(sys.argv[1]).resolve()
    (out / 'audit.json').unlink(missing_ok=True)
    report = json.loads((out / 'export-report.json').read_text(encoding='utf-8'))
    tables = json.loads((out / 'source-tables.json').read_text(encoding='utf-8'))

    actual = {}
    message_count = 0
    for path in (out / 'conversations').glob('*/messages.jsonl'):
        for line in path.open(encoding='utf-8'):
            record = json.loads(line)
            key = (record['source']['database'], record['source']['table'], record['local_id'])
            require(key not in actual, 'duplicate source row ' + str(key))
            actual[key] = record
            message_count += 1
    for table in tables:
        with e.connect(out / 'databases' / table['database']) as connection:
            ids = {
                r['rowid']: r['user_name'] for r in connection.execute('SELECT rowid,user_name FROM Name2Id')
            }
            for row in connection.execute('SELECT * FROM ' + e.ident(table['table'])):
                expected = {k: e.rawjson(v) for k, v in dict(row).items()}
                key = (table['database'], table['table'], row['local_id'])
                stored = actual.get(key, {})
                require(stored.get('raw') == expected, 'source row ' + str(key))
                normalized = e.normalize(
                    dict(row), table['database'], table['table'], table['session_id'], ids
                )
                for field in ('id', 'session_id', 'sender_id', 'timestamp', 'type', 'text'):
                    require(stored.get(field) == normalized[field], field + ' ' + str(key))
    require(
        message_count == sum(t['count'] for t in tables) == report['exported_message_count'],
        'message count differs between snapshot, JSONL and report',
    )

    assets = [
        json.loads(line) for line in (out / 'asset-inventory.jsonl').read_text(encoding='utf-8').splitlines()
    ]
    checked, wav_count = set(), 0
    for entry in assets:
        for asset in [entry.get('original'), entry.get('decoded')]:
            if not asset or not asset.get('path'):
                continue
            file = out / asset['path']
            require(file.is_file(), 'missing asset ' + asset['path'])
            require(file.stat().st_size == asset['size'], 'asset size mismatch: '+asset['path'])
            if asset['path'] in checked:
                continue
            require(e.digest(file) == asset['sha256'], 'asset SHA-256 mismatch: '+asset['path'])
            checked.add(asset['path'])
            if file.suffix == '.wav':
                with wave.open(str(file)) as wav:
                    require(
                        wav.getnframes() > 0 and wav.getnchannels() > 0,
                        'invalid WAV: '+asset['path'],
                    )
                wav_count += 1
    for path in (out / 'databases').rglob('*.db'):
        verification = json.loads(path.with_suffix('.verification.json').read_text(encoding='utf-8'))
        require(
            verification['row_counts_match_source_transaction'],
            'snapshot row counts: '+path.name,
        )
        require(verification['table_integrity_checked'], 'missing integrity check: '+path.name)

    docs = json.loads((out / 'document-index.json').read_text(encoding='utf-8'))
    for doc in docs:
        if doc.get('text_path'):
            require((out / doc['text_path']).is_file(), 'missing document text: '+doc['text_path'])

    result = {
        'success': True,
        'message_rows_compared_field_by_field': message_count,
        'unique_asset_files_sha256_verified': len(checked),
        'wav_files_opened': wav_count,
        'documents_indexed': len(docs),
        'source_databases_verified': len(list((out / 'databases').rglob('*.db'))),
    }
    e.dump(out / 'audit.json', result)
    print(json.dumps(result))


if __name__ == '__main__':
    main()
