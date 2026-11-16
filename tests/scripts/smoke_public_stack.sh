#!/usr/bin/env bash
# SPDX-License-Identifier: LGPL-3.0-or-later
set -uo pipefail
cd "$(dirname "$0")/../.."
[[ $(uname -s) == Linux ]] || { echo 'E2E checks require a Linux host' >&2; exit 1; }
source scripts/lib/native_arch.sh || exit 1
image=${PUBLIC_STACK_IMAGE:-local/public-stack:native}
require_native_image "$image" || exit 1
mkdir -p artifacts/public-stack
image_id=$(docker image inspect "$image" --format '{{.Id}}') || exit 1
commit=$(cat SOURCE_REVISION) || exit 1
cid=$(docker create --network=none -e "IMAGE_ID=$image_id" -e "TEST_COMMIT=$commit" -e "EXPECTED_MACHINE=$(uname -m)" -e "INJECT_FAILURE=${INJECT_FAILURE:-0}" "$image" python /tmp/public_stack_checks.py) || exit 1
trap 'docker rm -f "$cid" >/dev/null 2>&1 || true' EXIT
for file in tests/e2e/public_stack_checks.py tests/e2e/public_stack_acquire.py docs/alpide-board-surface.md docs/public-stack.md; do
  docker cp "$file" "$cid:/tmp/${file##*/}" || exit 1
done
docker start -a "$cid"
status=$?
docker cp "$cid:/tmp/public_stack_smoke.json" artifacts/public-stack/public_stack_smoke.json || exit 1
exit "$status"
