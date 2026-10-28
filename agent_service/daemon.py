"""Compatibility entry point for the macOS LaunchAgent.

The compiled helper app runs this exact path, so keep it stable after refactors.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))

from wxlocal.service import main  # noqa: E402

if __name__ == '__main__':
    main()
