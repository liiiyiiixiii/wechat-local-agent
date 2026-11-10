#!/bin/zsh
set -e
TASK_ROOT="${0:A:h}"
if (( $# == 0 )); then
  set -- status
fi
export PYTHONPATH="$TASK_ROOT/src${PYTHONPATH:+:$PYTHONPATH}"
python="$TASK_ROOT/.venv/bin/python"
[[ -x "$python" ]] || python="$TASK_ROOT/private/agent-venv/bin/python"
exec "$python" -m wxlocal.api.cli "$@"
