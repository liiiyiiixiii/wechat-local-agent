#!/bin/zsh
set -eu
TASK_ROOT="${0:A:h}"
export PYTHONPATH="$TASK_ROOT/src${PYTHONPATH:+:$PYTHONPATH}"
exec "$TASK_ROOT/private/agent-venv/bin/python" -m wxlocal.archive.pipeline "$@"
