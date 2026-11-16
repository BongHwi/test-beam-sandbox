#!/usr/bin/env bash
# SPDX-License-Identifier: LGPL-3.0-or-later
# QA of a run. --page also starts the result page server and prints its address (in a terminal the page opens in a
# browser unless --no-browser is given).
set -euo pipefail
usage() { echo "usage: $0 CONTAINER [--run NUMBER | --list] [--page [--no-browser]]" >&2; exit 2; }
[[ $# -ge 1 ]] || usage
name=$1; shift
browser=1; page=0; list=0; run=''
while (($#)); do
  case "$1" in
    --no-browser) browser=0; shift ;;
    --page) page=1; shift ;;
    --list) list=1; shift ;;
    --run) run=${2:?missing run number}; shift 2 ;;
    *) usage ;;
  esac
done
if [[ $list == 1 && -n "$run" ]]; then usage; fi
if [[ $list == 1 ]]; then
  exec docker exec "$name" python -m session_setup.analyze --list
fi
code=0
if [[ -n "$run" ]]; then
  docker exec "$name" python -m session_setup.analyze --run "$run" || code=$?
else
  docker exec "$name" python -m session_setup.analyze || code=$?
fi
if [[ $page == 1 ]]; then
  open_args=(open "$name")
  if [[ $browser == 0 || ! -t 1 ]]; then open_args+=(--no-browser); fi
  (cd "$(dirname "$0")/.." && python3 -m session_setup.webpage "${open_args[@]}") || true
fi
exit "$code"
