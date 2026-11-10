"""Manage only this user's service; login startup is an explicit operation."""

import os
import plistlib
import subprocess
import time
from .client import rpc
from .config import HELPER, ROOT, LEGACY, HOME, LABEL, PYTHON, STATE, WINDOWS, config, prepare
from pathlib import Path


def start():
    prepare()
    config()
    try:
        return rpc('get_status')
    except RuntimeError:
        pass
    options = (
        {'creationflags': subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP}
        if WINDOWS
        else {'start_new_session': True}
    )
    with (STATE / 'daemon.log').open('ab') as log:
        process = subprocess.Popen(
            [str(PYTHON), '-m', 'wxlocal.service'],
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=log,
            **options,
        )
    deadline = time.monotonic() + 12
    while time.monotonic() < deadline:
        try:
            return rpc('get_status')
        except RuntimeError:
            if process.poll() is not None:
                raise RuntimeError(f'start_failed: inspect {STATE / "daemon.log"}')
            time.sleep(0.15)
    raise RuntimeError('startup_timeout: inspect daemon.log')


def stop():
    try:
        rpc('_shutdown')
    except RuntimeError:
        return {'stopped': True}
    deadline = time.monotonic() + 45
    from .locking import acquire

    while time.monotonic() < deadline:
        try:
            acquire(STATE / 'daemon.lock').close()
            return {'stopped': True}
        except RuntimeError:
            time.sleep(0.15)
    raise RuntimeError('stop_timeout')


def autostart(enable):
    if WINDOWS:
        import winreg

        keyname = r'Software\Microsoft\Windows\CurrentVersion\Run'
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, keyname) as key:
            if enable:
                pythonw = PYTHON.with_name('pythonw.exe')
                args = [str(pythonw), '-m', 'wxlocal', '--home', str(HOME), 'start']
                winreg.SetValueEx(key, LABEL, 0, winreg.REG_SZ, subprocess.list2cmdline(args))
            else:
                try:
                    winreg.DeleteValue(key, LABEL)
                except FileNotFoundError:
                    pass
    else:
        path = Path.home() / 'Library/LaunchAgents' / (LABEL + '.plist')
        domain = 'gui/' + str(os.getuid())
        if path.exists():
            subprocess.run(['launchctl', 'bootout', domain + '/' + LABEL], capture_output=True)
        if enable:
            path.parent.mkdir(parents=True, exist_ok=True)
            program = (
                [str(HELPER)]
                if (ROOT / 'validation/helper-relocatable').exists()
                else [str(PYTHON), '-m', 'wxlocal.service']
            )
            data = {
                'Label': LABEL,
                'ProgramArguments': program,
                'EnvironmentVariables': {'WXLOCAL_HOME': str(HOME)} if not LEGACY else {},
                'RunAtLoad': True,
                'StandardOutPath': str(STATE / 'daemon.log'),
                'StandardErrorPath': str(STATE / 'daemon.log'),
                'Umask': 0o077,
            }
            path.write_bytes(plistlib.dumps(data))
            subprocess.run(['launchctl', 'enable', domain + '/' + LABEL], check=True)
            subprocess.run(['launchctl', 'bootstrap', domain, str(path)], check=True)
        else:
            path.unlink(missing_ok=True)
    return {'autostart': enable}
