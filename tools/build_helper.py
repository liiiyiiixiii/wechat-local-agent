"""Build the dedicated macOS app identity used for background Full Disk Access.

Only needed when the helper binary itself changes; Python-only refactors do not require
rebuilding (and rebuilding can prompt for the "other app data" permission again).
"""
import plistlib
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from wxlocal.config import ROOT

APP = ROOT / 'validation/微信 Agent 服务.app'
BUNDLE_ID = 'local.wechat.agent.service'
ENTRY = ROOT / 'agent_service/daemon.py'  # stable compatibility entry
HELPER_SOURCE = Path(__file__).with_name('helper.c')
BINARY = APP / 'Contents/MacOS/WeChatAgentService'


def main():
    contents = APP / 'Contents'
    contents.mkdir(parents=True, exist_ok=True)
    info = {'CFBundleIdentifier': BUNDLE_ID, 'CFBundleName': '微信 Agent 服务',
            'CFBundleDisplayName': '微信 Agent 服务', 'CFBundleExecutable': 'WeChatAgentService',
            'CFBundlePackageType': 'APPL', 'CFBundleVersion': '1', 'CFBundleShortVersionString': '1.0',
            'LSUIElement': True,
            'NSDocumentsFolderUsageDescription': '读取本机微信 Agent 工具、归档和索引。',
            'NSSystemAdministrationUsageDescription': '仅为当前已配置微信账号提供本机只读消息与附件索引。'}
    BINARY.parent.mkdir(parents=True, exist_ok=True)
    (contents / 'Info.plist').write_bytes(plistlib.dumps(info))
    # Resolve the installation from the live bundle, including after a directory move.
    source = r"""#include <mach-o/dyld.h>
#include <limits.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
int main(void) {
    char exe[PATH_MAX], root[PATH_MAX], py[PATH_MAX], script[PATH_MAX];
    uint32_t size = sizeof(exe);
    if (_NSGetExecutablePath(exe, &size) || !realpath(exe, root)) return 70;
    for (int i = 0; i < 5; i++) {
        char *slash = strrchr(root, '/');
        if (!slash) return 70;
        *slash = 0;
    }
    if (snprintf(py, sizeof(py), "%s/.venv/bin/python", root) >= sizeof(py)) return 70;
    if (access(py, X_OK) && snprintf(py, sizeof(py), "%s/private/agent-venv/bin/python", root) >= sizeof(py)) return 70;
    if (snprintf(script, sizeof(script), "%s/agent_service/daemon.py", root) >= sizeof(script)) return 70;
    execl(py, py, script, (char *)0);
    perror("service python");
    return 70;
}
"""
    HELPER_SOURCE.write_text(source)
    subprocess.run(['/usr/bin/clang', '-O2', str(HELPER_SOURCE), '-o', str(BINARY)], check=True)
    subprocess.run(['/usr/bin/codesign', '--force', '--sign', '-', '--identifier', BUNDLE_ID, str(APP)], check=True)
    subprocess.run(['/usr/bin/codesign', '--verify', '--strict', str(APP)], check=True)
    (APP.parent/'helper-relocatable').write_text('1\\n')
    print(str(APP))


if __name__ == '__main__':
    main()
