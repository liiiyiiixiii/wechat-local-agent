"""Compatibility entry point referenced by the registered Codex MCP configuration."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))

from wxlocal.api.mcp import main  # noqa: E402

if __name__ == '__main__':
    main()
