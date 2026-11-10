"""Compare full reconciliation with the append path on identical synthetic histories."""

import argparse
import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile
import time
import tracemalloc

from wxlocal.collector import Collector


def measure(root, count, force):
    account = root / 'account'
    folder = account / 'db_storage/message'
    folder.mkdir(parents=True)
    writer = sqlite3.connect(folder / 'message_0.db')
    try:
        table = 'Msg_' + hashlib.md5(b'fixture').hexdigest()
        writer.execute('CREATE TABLE Name2Id(user_name TEXT)')
        writer.execute("INSERT INTO Name2Id VALUES('fixture')")
        writer.execute(
            f'CREATE TABLE {table}(local_id INTEGER PRIMARY KEY,server_id INTEGER,'
            'local_type INTEGER,create_time INTEGER,real_sender_id INTEGER,message_content TEXT)'
        )
        insert = f'INSERT INTO {table} VALUES(?,?,1,1700000000,1,?)'
        writer.executemany(insert, ((n, n, '人工样本 ' * 20) for n in range(1, count + 1)))
        writer.commit()
        collector = Collector(root, account, fixture=True)
        if collector.sync_once()['errors']:
            raise RuntimeError('initial_sync_failed')
        writer.execute(insert, (count + 1, count + 1, '新增文字'))
        writer.commit()
        tracemalloc.start()
        start = time.perf_counter()
        result = collector.sync_once(force=force)
        elapsed = time.perf_counter() - start
        _, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        if result['errors'] or result['changed'] != 1:
            raise RuntimeError(result)
        return {
            'rows_scanned': result.get('rows_scanned', count + 1),
            'seconds': round(elapsed, 4),
            'python_peak_bytes': peak,
        }
    finally:
        writer.close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--rows', type=int, default=10000)
    args = parser.parse_args()
    with tempfile.TemporaryDirectory() as temp:
        print(
            json.dumps(
                {
                    mode: measure(Path(temp) / mode, args.rows, full)
                    for mode, full in [('full', True), ('append', False)]
                },
                indent=2,
            )
        )
