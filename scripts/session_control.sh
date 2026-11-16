#!/usr/bin/env bash
# SPDX-License-Identifier: LGPL-3.0-or-later
set -euo pipefail
usage() { echo "usage: $0 CONTAINER {status|on|off|beam-status|stop|repeat|terminate|set|settings|end-producer} [--json] [--id ID]" >&2; exit 2; }
[[ $# -ge 2 ]] || usage
name=$1; action=$2; shift 2
if [[ $action == set || $action == settings || $action == end-producer ]]; then
  exec docker exec "$name" python -m session_setup.control "$action" "$@"
fi
args=()
while (($#)); do
  case "$1" in
    --json) args+=(--json); shift ;;
    --id) [[ $# -ge 2 ]] || usage; args+=(--id "$2"); shift 2 ;;
    *) usage ;;
  esac
done
exec docker exec "$name" python -m session_setup.control "$action" ${args[@]+"${args[@]}"}
