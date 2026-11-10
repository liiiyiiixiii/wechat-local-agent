"""Current-user settings, including compatibility with the original macOS installation."""

import datetime as dt
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from functools import lru_cache

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / 'src'
WINDOWS = os.name == 'nt'
LEGACY = (
    not WINDOWS
    and Path(os.environ.get('WXLOCAL_HOME', ROOT / 'private')).resolve() == (ROOT / 'private').resolve()
    and (ROOT / 'private/real-test.json').exists()
)
default_home = (
    Path(os.environ.get('LOCALAPPDATA', Path.home())) / 'WeChatLocalAgent'
    if WINDOWS
    else Path.home() / 'Library/Application Support/WeChatLocalAgent'
)
HOME = (
    Path(os.environ.get('WXLOCAL_HOME', ROOT / 'private' if LEGACY else default_home)).expanduser().resolve()
)
SETTINGS = HOME / 'config.json'
STATE = HOME / 'agent-service'
SOCKET_DIR = Path.home() / '.wechat-agent' if LEGACY else STATE
SOCKET = SOCKET_DIR / 'service.sock'
PYTHON = Path(sys.executable)
LABEL = 'local.wechat.agent'
TZ = dt.timezone(dt.timedelta(hours=8))
WCDB_LIBRARY = ROOT / 'validation/components/macos-arm64/libWCDB.dylib'
HELPER_APP = ROOT / 'validation/微信 Agent 服务.app'
HELPER = HELPER_APP / 'Contents/MacOS/WeChatAgentService'
LEGACY_ARCHIVE = HOME / 'exports'
MCP_ENTRY = ROOT / 'agent_service/mcp_server.py'


def prepare():
    os.umask(0o077)
    # Do not change ACLs on an unrelated directory supplied as WXLOCAL_HOME.
    if HOME.exists() and not LEGACY and not (HOME / '.wxlocal-home').is_file():
        if any(HOME.iterdir()):
            raise ValueError('WXLOCAL_HOME must be a dedicated empty directory on first use')
    HOME.mkdir(parents=True, exist_ok=True, mode=0o700)
    if WINDOWS and not (HOME / '.wxlocal-home').is_file():
        from .windows import protect_directory

        protect_directory(HOME)
    (HOME / '.wxlocal-home').touch(mode=0o600, exist_ok=True)
    for path in (STATE, SOCKET_DIR):
        path.mkdir(parents=True, exist_ok=True, mode=0o700)
        if not WINDOWS:
            path.chmod(0o700)


def private_config():
    if SETTINGS.exists():
        return json.loads(SETTINGS.read_text(encoding='utf-8-sig'))
    if LEGACY:
        return json.loads((HOME / 'real-test.json').read_text(encoding='utf-8'))
    raise ValueError('configuration_missing: run configure --account PATH --keys FILE')


def account_dir():
    return Path(private_config()['accountDir']).expanduser().resolve()


def keys_file():
    value = private_config().get('keysFile')
    if value:
        return Path(value).expanduser().resolve()
    if LEGACY:
        return HOME / 'bootstrap-20261016/verified-keys.json'
    raise ValueError('keysFile_missing')


def online():
    return private_config().get('allowCdn') is True


def config():
    data = private_config()
    account = account_dir()
    archive = data.get('seedArchive')
    latest = LEGACY_ARCHIVE / 'latest.json'
    if LEGACY and not archive and latest.exists():
        archive = json.loads(latest.read_text(encoding='utf-8'))['path']
    if archive:
        archive = Path(archive).expanduser().resolve()
        marker = json.loads((archive / 'archive.json').read_text(encoding='utf-8'))
        if marker['account_id'] != account.name:
            raise ValueError('archive_account_mismatch')
    return {'account': account, 'keys': keys_file(), 'archive': archive}


def node():
    candidate = os.environ.get('WECHAT_NODE') or shutil.which('node')
    if not candidate and not WINDOWS:
        candidate = next(
            iter(sorted((Path.home() / '.nvm/versions/node').glob('*/bin/node'), reverse=True)), None
        )
    if not candidate:
        raise RuntimeError('node_missing')
    return str(candidate)


@lru_cache(maxsize=1)
def documents_python():
    for candidate in (
        os.environ.get('WECHAT_DOC_RUNTIME'),
        sys.executable,
        Path.home() / '.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3',
    ):
        if candidate and Path(candidate).is_file():
            if (
                subprocess.run(
                    [str(candidate), '-c', 'import pypdf, openpyxl'], capture_output=True, timeout=30
                ).returncode
                == 0
            ):
                return str(candidate)
    raise RuntimeError('document_runtime_missing: install the documents extra or set WECHAT_DOC_RUNTIME')


def wcdb_library():
    return Path(os.environ.get('SQLCIPHER_LIBRARY') or WCDB_LIBRARY)
