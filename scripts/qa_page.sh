#!/usr/bin/env bash
# SPDX-License-Identifier: LGPL-3.0-or-later
# Show the experiment view and QA results in a browser (without CONTAINER the page opens at its setup tab):
# scripts/qa_page.sh [CONTAINER] [--port N] [--refresh SECONDS] [--no-browser] [--view-only] [--foreground]
# Stop the page server: scripts/qa_page.sh --stop [--port N]
set -euo pipefail
cd "$(dirname "$0")/.."
command=start; args=()
while (($#)); do
  case "$1" in
    --stop) command=stop; shift ;;
    --foreground) command=serve; shift ;;
    --port|--refresh) args+=("$1" "${2:?missing value for $1}"); shift 2 ;;
    --no-browser|--view-only) args+=("$1"); shift ;;
    -*) echo "usage: $0 [CONTAINER] [--port N] [--refresh SECONDS] [--no-browser] [--view-only] [--foreground] | --stop [--port N]" >&2; exit 2 ;;
    *) args+=("$1"); shift ;;
  esac
done
exec python3 -m session_setup.webpage "$command" ${args[@]+"${args[@]}"}
