#!/bin/zsh
set -eu
validation_root="${0:A:h}"
workspace_root="${validation_root:h}"
export PYTHONPATH="$workspace_root/src${PYTHONPATH:+:$PYTHONPATH}"
exec "$workspace_root/private/agent-venv/bin/python" -m wxlocal.archive.pipeline "$@"
