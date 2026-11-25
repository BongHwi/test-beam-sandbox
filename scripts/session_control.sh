#!/usr/bin/env bash
# SPDX-License-Identifier: LGPL-3.0-or-later
set -euo pipefail
usage() { echo "usage: $0 CONTAINER {status|on|off|beam-status|stop|repeat|terminate|set|settings|end-producer|stall|nestdaq} [--json] [--id ID]" >&2; echo "       $0 CONTAINER nestdaq {start|stop|status|qa} [--json]" >&2; exit 2; }
[[ $# -ge 2 ]] || usage
name=$1; action=$2; shift 2
if [[ $action == nestdaq ]]; then
  [[ $# -ge 1 && $# -le 2 ]] || usage
  case "$1" in start|stop) [[ $# == 1 ]] || usage ;; status|qa) [[ $# == 1 || ( $# == 2 && $2 == --json ) ]] || usage ;; *) usage ;; esac
  mode=$(docker exec "$name" cat /tmp/session-run/daq-mode 2>/dev/null || true)
  if [[ "${mode//[[:space:]]/}" != nestdaq ]]; then echo 'error: this session runs in the EUDAQ mode' >&2; exit 2; fi
  exec docker exec "$name" python -m nestdaq_mode.control "$@"
fi
if [[ $action == set || $action == settings || $action == end-producer || $action == stall ]]; then
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
