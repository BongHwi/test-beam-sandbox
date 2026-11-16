#!/usr/bin/env bash
# SPDX-License-Identifier: LGPL-3.0-or-later
set -eu
cd "$(dirname "$0")/../.."
[[ $(uname -s) == Linux ]] || { echo 'E2E checks require a Linux host' >&2; exit 1; }
source scripts/lib/native_arch.sh || exit 1
require_native_image local/public-stack:native || exit 1
export PYTHONDONTWRITEBYTECODE=1
exec python3 tests/e2e/tracksim_runner.py
