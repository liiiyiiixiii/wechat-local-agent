"""Per-user daemon: one collector, a separate media subprocess, bounded local RPC."""
import fcntl
import json
import os
import signal
import socketserver
import subprocess
import threading
import time

from .collector import Collector, import_archive
from .config import PYTHON, ROOT, SOCKET, STATE, config, prepare
from .util import dumps
from .scheduler import (MEDIA_MIN_INTERVAL_SECONDS, MEDIA_REFRESH_SECONDS,
                        MEDIA_SCAN_SECONDS, MediaTrigger)
from .sources import media_signature
from .store import Store

STOP = threading.Event()
READY = threading.Event()


def log(event, **fields):
    print(dumps({'at': time.time(), 'event': event, **fields}), flush=True)


def _media_env():
    env = dict(os.environ)
    env['PYTHONPATH'] = str(ROOT / 'src')
    return env


def collect_loop(cfg):
    try:
        import_archive(STATE, cfg['account'], cfg['archive'])
        READY.set()
        log('archive_imported')
    except Exception as e:
        log('initialization_failed', error=type(e).__name__)
        with Store(STATE / 'index.sqlite').c as c:
            c.execute('INSERT OR REPLACE INTO meta VALUES(?,?)', ('initialization_error', dumps(type(e).__name__)))
        return
    collector = Collector(STATE, cfg['account'], cfg['keys'])
    while not STOP.is_set():
        try:
            result = collector.sync_once()
            if result['changed'] or result['errors']:
                log('sync', **result)
        except Exception as e:
            log('collector_failure', error=type(e).__name__)
            status_store = Store(STATE / 'index.sqlite')
            with status_store.c:
                status = status_store.meta('sync', {})
                status.update(state='degraded', last_checked_at=time.time(),
                              errors=[{'code': type(e).__name__}])
                status_store.setmeta('sync', status)
            status_store.close()
        STOP.wait(2)


def media_loop(cfg):
    while not READY.wait(1):
        if STOP.is_set():
            return
    store = Store(STATE / 'index.sqlite')
    with store.c:
        store.c.execute("UPDATE jobs SET state='pending' WHERE state='running'")
    last_scan = 0.0
    trigger = MediaTrigger()
    retry_at = 0.0
    last_finished = 0.0

    while not STOP.is_set():
        try:
            now = time.time()
            if now - last_scan >= MEDIA_SCAN_SECONDS:
                sig = media_signature(cfg['account'])
                last_scan = time.time()
                commit = trigger.observe(store.meta('media_source_signature'), sig, now)
                if commit:
                    with store.c:
                        store.queue_media()
                        store.setmeta('media_source_signature', commit)
                if trigger.pending is None:
                    last_success = store.meta('media_last_success_at') or 0
                    if now - max(last_success, last_finished) >= MEDIA_REFRESH_SECONDS:
                        with store.c:
                            store.queue_media()
            job = store.c.execute("SELECT * FROM jobs WHERE id='media'").fetchone()
            if job and job['requested'] > job['completed'] and time.time() >= retry_at \
                    and time.time() - last_finished >= MEDIA_MIN_INTERVAL_SECONDS and trigger.pending is None:
                target = job['requested']
                with store.c:
                    store.c.execute("UPDATE jobs SET state='running',started=?,error=NULL WHERE id='media'", (time.time(),))
                log('media_started', generation=target)
                with (STATE / 'media.log').open('a') as output:
                    process = subprocess.Popen([str(PYTHON), '-m', 'wxlocal.media'],
                                               cwd=str(ROOT), env=_media_env(),
                                               stdout=output, stderr=output, start_new_session=True)
                    began = time.time()
                    while process.poll() is None:
                        if STOP.wait(1) or time.time() - began > 1800:
                            os.killpg(process.pid, signal.SIGTERM)
                            try:
                                process.wait(timeout=5)
                            except subprocess.TimeoutExpired:
                                os.killpg(process.pid, signal.SIGKILL);process.wait()
                            break
                code = process.returncode
                last_finished = time.time()
                with store.c:
                    if code == 0:
                        store.c.execute("UPDATE jobs SET state=CASE WHEN requested>? THEN 'pending' ELSE 'idle' END,completed=?,finished=?,error=NULL WHERE id='media'",
                                        (target, target, time.time()))
                    else:
                        store.c.execute("UPDATE jobs SET state='failed',finished=?,error=? WHERE id='media'",
                                        (time.time(), 'worker_exit_' + str(code)))
                        retry_at = time.time() + 60
                log('media_finished', success=code == 0, seconds=round(time.time()-began, 2))
        except Exception as e:
            log('media_scheduler_failed', error=type(e).__name__)
            retry_at = time.time() + 60
        STOP.wait(2)
    store.close()


class Handler(socketserver.StreamRequestHandler):
    def handle(self):
        self.request.settimeout(15)
        store = None
        try:
            line = self.rfile.readline(65537)
            if len(line) > 65536 or not line.endswith(b'\n'):
                raise ValueError('request_too_large_or_incomplete')
            req = json.loads(line)
            if not READY.is_set() and req.get('method') != 'get_status':
                raise ValueError('index_initializing; query get_status')
            store = Store(STATE / 'index.sqlite')
            result = store.call(req['method'], req.get('params', {}))
            if req['method'] == 'get_status':
                result.update(initialized=READY.is_set(), initialization_error=store.meta('initialization_error'),
                              daemon_pid=os.getpid(), media_last_success_at=store.meta('media_last_success_at'))
            response = {'result': result}
        except (ValueError, TypeError, KeyError) as e:
            response = {'error': str(e)[:240]}
        except Exception as e:
            response = {'error': 'internal_error:' + type(e).__name__}
            log('request_failed', error=type(e).__name__)
        finally:
            if store:
                store.close()
        encoded = (dumps(response) + '\n').encode()
        if len(encoded) > 8 * 1024 * 1024:
            encoded = b'{"error":"response_too_large; reduce limit"}\n'
        try:
            self.wfile.write(encoded)
        except (BrokenPipeError, ConnectionResetError):
            pass


class Server(socketserver.ThreadingUnixStreamServer):
    daemon_threads = True
    request_queue_size = 16


def main():
    prepare();cfg = config()
    lock = (STATE / 'daemon.lock').open('a')
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        raise SystemExit('daemon_already_running')
    store = Store(STATE / 'index.sqlite', cfg['account'].name)
    with store.c:
        store.setmeta('initialization_error', None)
        status = store.meta('sync', {})
        status.update(state='starting', daemon_started_at=time.time())
        store.setmeta('sync', status)
    store.close()
    SOCKET.unlink(missing_ok=True)
    server = Server(str(SOCKET), Handler);SOCKET.chmod(0o600)
    server.timeout = 1
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda *_: STOP.set())
    collector = threading.Thread(target=collect_loop, args=(cfg,), name='collector')
    media = threading.Thread(target=media_loop, args=(cfg,), name='media')
    collector.start();media.start()
    log('daemon_started', pid=os.getpid())
    try:
        while not STOP.is_set():
            server.handle_request()
    finally:
        STOP.set();server.server_close()
        collector.join(timeout=30);media.join(timeout=10)
        SOCKET.unlink(missing_ok=True)
        log('daemon_stopped')


if __name__ == '__main__':
    main()
