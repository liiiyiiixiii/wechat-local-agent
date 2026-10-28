"""Single source of truth for paths, account scope and external runtimes.

Everything machine-specific (installation paths, keys, archive location) is resolved
here so the rest of the package never hardcodes a user path.
"""
import datetime as dt
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / 'src'
STATE = ROOT / 'private/agent-service'
SOCKET_DIR = Path.home() / '.wechat-agent'
SOCKET = SOCKET_DIR / 'service.sock'
PYTHON = ROOT / 'private/agent-venv/bin/python'
LABEL = 'local.wechat.agent'
TZ = dt.timezone(dt.timedelta(hours=8))

# Runtime assets that live outside the package because macOS permissions and large
# vendored builds are tied to their current locations.
WCDB_LIBRARY = ROOT / 'validation/components/macos-arm64/libWCDB.dylib'
HELPER_APP = ROOT / 'validation/微信 Agent 服务.app'
HELPER = HELPER_APP / 'Contents/MacOS/WeChatAgentService'
LEGACY_ARCHIVE = ROOT / 'private/exports'
# Compatibility shims keep LaunchAgent / Codex configuration unchanged after refactor.
MCP_ENTRY = ROOT / 'agent_service/mcp_server.py'

_DOCUMENT_RUNTIME = None


def dumps_json_path(path, default=None):
    path = Path(path)
    return json.loads(path.read_text()) if path.exists() else default


def prepare():
    os.umask(0o077)
    for p in (STATE, SOCKET_DIR):
        p.mkdir(parents=True, exist_ok=True, mode=0o700)
        p.chmod(0o700)


def private_config():
    """Raw private configuration: account directory, keys and codec addon paths."""
    return json.loads((ROOT / 'private/real-test.json').read_text())


def account_dir():
    return Path(private_config()['accountDir']).resolve()


def keys_file():
    return ROOT / 'private/bootstrap-20261016/verified-keys.json'


def config():
    """Load the configured account, its seed archive and the DB key file."""
    archive = Path(json.loads((ROOT / 'private/exports/latest.json').read_text())['path']).resolve()
    account = account_dir()
    marker = json.loads((archive / 'archive.json').read_text())
    if marker['account_id'] != account.name:
        raise ValueError('archive_account_mismatch')
    return {'account': account, 'archive': archive, 'keys': keys_file()}


def node():
    """Node.js used for local media codecs; override with WECHAT_NODE."""
    candidate = os.environ.get('WECHAT_NODE') or shutil.which('node')
    if candidate and Path(candidate).exists():
        return str(candidate)
    roots = sorted((Path.home() / '.nvm/versions/node').glob('*/bin/node'),
                   key=lambda p: p.stat().st_mtime, reverse=True)
    for path in roots:
        if path.exists():
            return str(path)
    raise RuntimeError('node_missing; install Node or set WECHAT_NODE')


def documents_python():
    """Python with pypdf/openpyxl for document text extraction.

    The service venv intentionally stays minimal, so document extraction runs in a
    separate interpreter. Probe candidates once and cache the first working one.
    """
    global _DOCUMENT_RUNTIME
    if _DOCUMENT_RUNTIME:
        return _DOCUMENT_RUNTIME
    codex = Path.home() / '.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3'
    candidates = [os.environ.get('WECHAT_DOC_RUNTIME'), str(codex), sys.executable,
                  shutil.which('python3'), '/Library/Frameworks/Python.framework/Versions/3.14/bin/python3.14']
    for candidate in candidates:
        if not candidate or not Path(candidate).exists():
            continue
        probe = subprocess.run([str(candidate), '-c', 'import pypdf, openpyxl'],
                               capture_output=True, timeout=30)
        if probe.returncode == 0:
            _DOCUMENT_RUNTIME = str(candidate)
            return _DOCUMENT_RUNTIME
    raise RuntimeError('document_runtime_missing; install pypdf and openpyxl or set WECHAT_DOC_RUNTIME')


def wcdb_library():
    """SQLCipher-compatible library used only for read-only database snapshots."""
    return Path(os.environ.get('SQLCIPHER_LIBRARY') or WCDB_LIBRARY)


def system_ca():
    """Pinned CA bundle shipped with the archive reader (local localhost CDN only)."""
    return Path(__file__).with_name('archive') / 'system-ca.pem'
