#!/usr/bin/env bash
# SPDX-License-Identifier: LGPL-3.0-or-later
# Run the session core, the msensor runners and the trigger board runner on this host (Python 3 and numpy, no Docker).
set -eu
cd "$(dirname "$0")/../.."
export PYTHONDONTWRITEBYTECODE=1
# BOARD_CHECK_DIR keeps the streams, frames and logs of the run there; otherwise a temporary directory is used and removed
if [ -n "${BOARD_CHECK_DIR:-}" ]; then
    work=$BOARD_CHECK_DIR; mkdir -p "$work"
else
    work=$(mktemp -d)
    trap 'rm -rf "$work"' EXIT
fi
# the setup tool must refuse a clock offset of half a period or more, which the frame builder could not match
sed 's/^clock_offset_ns = 3000000$/clock_offset_ns = 11000000/' configs/setups/nestdaq-board.ini > "$work/offset.ini"
if python3 -m session_setup.setupfile prepare "$work/offset.ini" "$work/offset-layout" > /dev/null 2> "$work/offset.err"; then
    echo "setup with a large clock offset was accepted" >&2; exit 1
fi
grep -q "less than half of the periodic trigger period" "$work/offset.err"
python3 tests/e2e/board_backlog_check.py
python3 tests/e2e/nestdaq_board_check.py configs/setups/nestdaq-board.ini "$work" "${1:-6}" "${2:-0}"
