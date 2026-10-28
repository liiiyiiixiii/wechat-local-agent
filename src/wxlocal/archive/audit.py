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


def main():
    out = Path(sys.argv[1]).resolve()
    report = json.loads((out / 'export-report.json').read_text())
    tables = json.loads((out / 'source-tables.json').read_text())

    actual = {}
    message_count = 0
    for path in (out / 'conversations').glob('*/messages.jsonl'):
        for line in path.open():
            record = json.loads(line)
            key = (record['source']['database'], record['source']['table'], record['local_id'])
            assert key not in actual
            actual[key] = record['raw']
            message_count += 1
    for table in tables:
        with e.connect(out / 'databases' / table['database']) as connection:
            for row in connection.execute('SELECT * FROM ' + e.ident(table['table'])):
                expected = {k: e.rawjson(v) for k, v in dict(row).items()}
                assert actual[(table['database'], table['table'], row['local_id'])] == expected
    assert message_count == sum(t['count'] for t in tables) == report['exported_message_count']

    assets = [json.loads(line) for line in (out / 'asset-inventory.jsonl').read_text().splitlines()]
    checked, wav_count = set(), 0
    for entry in assets:
        for asset in [entry.get('original'), entry.get('decoded')]:
            if not asset or not asset.get('path'):
                continue
            file = out / asset['path']
            assert file.is_file()
            assert file.stat().st_size == asset['size']
            if asset['path'] in checked:
                continue
            assert e.digest(file) == asset['sha256']
            checked.add(asset['path'])
            if file.suffix == '.wav':
                with wave.open(str(file)) as wav:
                    assert wav.getnframes() > 0 and wav.getnchannels() > 0
                wav_count += 1
    for path in (out / 'databases').rglob('*.db'):
        verification = json.loads(path.with_suffix('.verification.json').read_text())
        assert verification['row_counts_match_source_transaction']
        assert verification['table_integrity_checked']

    docs = json.loads((out / 'document-index.json').read_text())
    for doc in docs:
        if doc.get('text_path'):
            assert (out / doc['text_path']).is_file()

    result = {'success': True,
              'message_rows_compared_field_by_field': message_count,
              'unique_asset_files_sha256_verified': len(checked),
              'wav_files_opened': wav_count,
              'documents_indexed': len(docs),
              'source_databases_verified': len(list((out / 'databases').rglob('*.db')))}
    e.dump(out / 'audit.json', result)
    print(json.dumps(result))


if __name__ == '__main__':
    main()
