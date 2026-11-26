#!/usr/bin/env bash
# SPDX-License-Identifier: LGPL-3.0-or-later
# Run the session core, the msensor runners and the trigger board runner on this host (Python 3 and numpy, no Docker).
set -eu
cd "$(dirname "$0")/../.."
export PYTHONDONTWRITEBYTECODE=1
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT
python3 tests/e2e/nestdaq_board_check.py configs/setups/nestdaq-board.ini "$work" "${1:-6}"
