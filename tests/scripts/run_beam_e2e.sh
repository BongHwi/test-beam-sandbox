#!/usr/bin/env bash
# SPDX-License-Identifier: LGPL-3.0-or-later
set -uo pipefail
cd "$(dirname "$0")/../.."
[[ $(uname -s) == Linux ]] || { echo 'E2E checks require a Linux host' >&2; exit 1; }
source scripts/lib/native_arch.sh || exit 1
require_native_image local/public-stack:native || exit 1
scripts/build_session.sh || exit 1
docker build --platform "$NATIVE_PLATFORM" --memory 8g -f docker/runcontrol/Dockerfile -t local/runcontrol:native . || exit 1
docker build --platform "$NATIVE_PLATFORM" --memory 8g -f docker/virtual-alpide/Dockerfile -t local/virtual-alpide:native . || exit 1
docker build --platform "$NATIVE_PLATFORM" --memory 8g -f docker/session-core/Dockerfile -t local/session-core:native . || exit 1
python3 tests/e2e/beam_driver.py
