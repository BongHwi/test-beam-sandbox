#!/usr/bin/env bash
# SPDX-License-Identifier: LGPL-3.0-or-later
set -euo pipefail
cd "$(dirname "$0")/../.."
[[ $(uname -s) == Linux ]] || { echo 'E2E checks require a Linux host' >&2; exit 1; }
source scripts/lib/native_arch.sh
image=${PUBLIC_STACK_IMAGE:-local/public-stack:native}
require_native_image "$image"
cid=$(docker create --network=none "$image" /bin/bash /tmp/build-user-module.sh smoketest)
trap 'docker rm -f "$cid" >/dev/null 2>&1 || true' EXIT
docker cp tests/e2e/public_stack_module "$cid:/opt/eudaq-src/user/smoketest"
docker cp docker/base/build-user-module.sh "$cid:/tmp/build-user-module.sh"
docker start -a "$cid"
docker cp "$cid:/opt/eudaq/lib/libeudaq_module_smoketest.so" /tmp/public-stack-test-module.so
