"""Build the dedicated macOS app identity used for background Full Disk Access.

Only needed when the helper binary itself changes; Python-only refactors do not require
rebuilding (and rebuilding can prompt for the "other app data" permission again).
"""
import json
import plistlib
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from wxlocal.config import PYTHON, ROOT

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
    quote = lambda s: json.dumps(str(s), ensure_ascii=False)
    source = '''#include <dlfcn.h>
#include <stdio.h>
#include <stdlib.h>
int main(void) {
  void *library = dlopen("/Library/Frameworks/Python.framework/Versions/3.14/Python", RTLD_NOW | RTLD_GLOBAL);
  if (!library) { fputs("Python runtime unavailable\\n", stderr); return 70; }
  int (*entry)(int,char **) = dlsym(library, "Py_BytesMain");
  if (!entry) { fputs("Python entry unavailable\\n", stderr); return 70; }
  char *args[] = {PYTHON_PATH, SCRIPT_PATH, NULL};
  return entry(2, args);
}
'''.replace('PYTHON_PATH', quote(PYTHON)).replace('SCRIPT_PATH', quote(ENTRY))
    HELPER_SOURCE.write_text(source)
    subprocess.run(['/usr/bin/clang', '-O2', str(HELPER_SOURCE), '-o', str(BINARY)], check=True)
    subprocess.run(['/usr/bin/codesign', '--force', '--sign', '-', '--identifier', BUNDLE_ID, str(APP)], check=True)
    subprocess.run(['/usr/bin/codesign', '--verify', '--strict', str(APP)], check=True)
    print(str(APP))


if __name__ == '__main__':
    main()
