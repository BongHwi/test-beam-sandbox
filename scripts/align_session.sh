#!/usr/bin/env bash
# SPDX-License-Identifier: LGPL-3.0-or-later
# Align the session's analysis geometry; --save FILE keeps the result for later sessions (start with --alignment FILE).
set -euo pipefail
usage() { echo "usage: $0 CONTAINER [--run NUMBER] [--save FILE]" >&2; exit 2; }
[[ $# -ge 1 ]] || usage
name=$1; shift
run=''; save=''
while (($#)); do
  case "$1" in
    --run) run=${2:?missing run number}; shift 2 ;;
    --save) save=${2:?missing file}; shift 2 ;;
    *) usage ;;
  esac
done
if [[ -n "$run" ]]; then
  docker exec "$name" python -m session_setup.align --run "$run"
else
  docker exec "$name" python -m session_setup.align
fi
if [[ -n "$save" ]]; then
  docker cp "$name:/tmp/session-run/alignment/alignment.json" "$save" >/dev/null
  printf 'Saved the alignment to %s\n' "$save"
fi
