#!/bin/zsh
set -eu
validation_root="${0:A:h}"
workspace_root="${validation_root:h}"
export PYTHONPATH="$workspace_root/src${PYTHONPATH:+:$PYTHONPATH}"
python="$workspace_root/.venv/bin/python"
[[ -x "$python" ]] || python="$workspace_root/private/agent-venv/bin/python"
exec "$python" -m wxlocal.archive.pipeline "$@"
