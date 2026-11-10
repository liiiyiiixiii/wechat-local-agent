"""Per-user daemon: one collector, a separate media subprocess, bounded local RPC."""

import json
import os
import signal

import subprocess
import threading
import time

from .collector import Collector, import_archive
from .locking import acquire
from .config import WINDOWS, PYTHON, ROOT, SOCKET, STATE, config, prepare
from .util import dumps
from .scheduler import MEDIA_MIN_INTERVAL_SECONDS, MEDIA_REFRESH_SECONDS, MEDIA_SCAN_SECONDS, MediaTrigger
from .sources import media_signature
from .store import Store

STOP = threading.Event()
READY = threading.Event()


def log(event, **fields):
    print(dumps({'at': time.time(), 'event': event, **fields}), flush=True)


def _media_env():
    env = dict(os.environ)
    env['PYTHONPATH'] = os.pathsep.join(filter(None, (str(ROOT / 'src'), env.get('PYTHONPATH'))))
    return env


def collect_loop(cfg):
    collector = Collector(STATE, cfg['account'], cfg['keys'])
    seed_pending = bool(cfg['archive'])
    while not STOP.is_set():
        stage = 'seed_archive' if seed_pending else 'source_scan'
        try:
            if seed_pending:
                import_archive(STATE, cfg['account'], cfg['archive'])
                seed_pending = False
            result = collector.sync_once()
            if not result['errors']:
                READY.set()
                error = None
            else:
                error = {
                    'stage': stage,
                    'reason': result['errors'][0]['code'],
                    'action': 'check doctor; source access is retried automatically',
                }
            if result['changed'] or error:
                log('sync', **result)
        except Exception as exc:
            error = {
                'stage': stage,
                'reason': type(exc).__name__,
                'action': 'check doctor and seed archive; retrying automatically',
            }
        state = Store(STATE / 'index.sqlite')
        try:
            with state.c:
                state.setmeta('initialization_error', error)
                if error:
                    sync = state.meta('sync', {})
                    sync.update(state='degraded', last_checked_at=time.time())
                    state.setmeta('sync', sync)
        finally:
            state.close()
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
                        store.queue_media('media_source_changed')
                        store.setmeta('media_source_signature', commit)
                if trigger.pending is None:
                    last_success = store.meta('media_last_success_at') or 0
                    if now - max(last_success, last_finished) >= MEDIA_REFRESH_SECONDS:
                        with store.c:
                            store.queue_media('hourly_refresh')
            job = store.c.execute("SELECT * FROM jobs WHERE id='media'").fetchone()
            if (
                job
                and job['requested'] > job['completed']
                and time.time() >= retry_at
                and time.time() - last_finished >= MEDIA_MIN_INTERVAL_SECONDS
                and trigger.pending is None
            ):
                target = job['requested']
                with store.c:
                    store.c.execute(
                        "UPDATE jobs SET state='running',started=?,error=NULL WHERE id='media'",
                        (time.time(),),
                    )
                log('media_started', generation=target)
                with (STATE / 'media.log').open('a') as output:
                    process = subprocess.Popen(
                        [str(PYTHON), '-m', 'wxlocal.media'],
                        cwd=str(ROOT),
                        env=_media_env(),
                        stdout=output,
                        stderr=output,
                        start_new_session=not WINDOWS,
                    )
                    began = time.time()
                    while process.poll() is None:
                        if STOP.wait(1) or time.time() - began > 1800:
                            process.terminate() if WINDOWS else os.killpg(process.pid, signal.SIGTERM)
                            try:
                                process.wait(timeout=5)
                            except subprocess.TimeoutExpired:
                                process.kill() if WINDOWS else os.killpg(process.pid, signal.SIGKILL)
                                process.wait()
                            break
                code = process.returncode
                last_finished = time.time()
                with store.c:
                    if code == 0:
                        store.c.execute(
                            "UPDATE jobs SET state=CASE WHEN requested>? THEN 'pending' ELSE 'idle' END,completed=?,finished=?,error=NULL WHERE id='media'",
                            (target, target, time.time()),
                        )
                    else:
                        store.c.execute(
                            "UPDATE jobs SET state='failed',finished=?,error=? WHERE id='media'",
                            (time.time(), 'worker_exit_' + str(code)),
                        )
                        retry_at = time.time() + 60
                log('media_finished', success=code == 0, seconds=round(time.time() - began, 2))
        except Exception as e:
            log('media_scheduler_failed', error=type(e).__name__)
            retry_at = time.time() + 60
        STOP.wait(2)
    store.close()


def exchange(reader, writer):
    store = None
    shutdown = False
    try:
        line = reader.readline(65537)
        if len(line) > 65536 or not line.endswith(b'\n'):
            raise ValueError('request_too_large_or_incomplete')
        req = json.loads(line)
        method = req['method']
        if method == '_shutdown':
            shutdown = True
            result = {'stopping': True}
        else:
            if not READY.is_set() and method != 'get_status':
                raise ValueError('index_initializing: inspect status')
            store = Store(STATE / 'index.sqlite')
            result = store.call(method, req.get('params', {}))
            if method == 'get_status':
                result.update(
                    initialized=READY.is_set(),
                    initialization_error=store.meta('initialization_error'),
                    daemon_pid=os.getpid(),
                    media_last_success_at=store.meta('media_last_success_at'),
                )
        response = {'result': result}
    except (ValueError, TypeError, KeyError) as exc:
        response = {'error': str(exc)[:240]}
    except Exception as exc:
        response = {'error': 'internal_error:' + type(exc).__name__}
    finally:
        if store:
            store.close()
    data = (dumps(response) + '\n').encode()
    if len(data) > 8 * 1024 * 1024:
        data = b'{"error":"response_too_large"}\n'
    try:
        writer.write(data)
        writer.flush()
    finally:
        if shutdown:
            STOP.set()


def serve():
    if WINDOWS:
        from .windows import serve_pipe

        def listen():
            try:
                serve_pipe(exchange, STOP)
            finally:
                STOP.set()

        thread = threading.Thread(target=listen, daemon=True)
        thread.start()
        STOP.wait()
        return
    import socketserver

    class Handler(socketserver.StreamRequestHandler):
        def handle(self):
            self.request.settimeout(15)
            exchange(self.rfile, self.wfile)

    SOCKET.unlink(missing_ok=True)
    with socketserver.ThreadingUnixStreamServer(str(SOCKET), Handler) as server:
        SOCKET.chmod(0o600)
        server.daemon_threads = True
        server.timeout = 0.5
        while not STOP.is_set():
            server.handle_request()
    SOCKET.unlink(missing_ok=True)


def main():
    prepare()
    cfg = config()
    lock = acquire(STATE / 'daemon.lock')
    store = Store(STATE / 'index.sqlite', cfg['account'].name)
    with store.c:
        store.setmeta('initialization_error', None)
        status = store.meta('sync', {})
        status.update(state='starting', daemon_started_at=time.time())
        store.setmeta('sync', status)
    store.close()
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda *_: STOP.set())
    collector = threading.Thread(target=collect_loop, args=(cfg,), name='collector')
    media = threading.Thread(target=media_loop, args=(cfg,), name='media')
    collector.start()
    media.start()
    log('daemon_started', pid=os.getpid())
    try:
        serve()
    finally:
        STOP.set()
        collector.join()
        media.join()
        lock.close()
        log('daemon_stopped')


if __name__ == '__main__':
    main()
