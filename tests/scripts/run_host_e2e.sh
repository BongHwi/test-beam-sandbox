#!/usr/bin/env bash
# SPDX-License-Identifier: LGPL-3.0-or-later
set -euo pipefail
[[ $(uname -s) == Linux ]] || { echo 'E2E checks require a Linux host' >&2; exit 1; }
cd "$(dirname "$0")/../.."
python3 tests/e2e/host_checks.py "$@"
