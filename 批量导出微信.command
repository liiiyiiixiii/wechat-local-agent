#!/bin/zsh
set -eu
TASK_ROOT="${0:A:h}"
export PYTHONPATH="$TASK_ROOT/src${PYTHONPATH:+:$PYTHONPATH}"
python="$TASK_ROOT/.venv/bin/python"
[[ -x "$python" ]] || python="$TASK_ROOT/private/agent-venv/bin/python"
exec "$python" -m wxlocal.archive.pipeline "$@"
