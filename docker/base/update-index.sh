#!/usr/bin/env bash
# SPDX-License-Identifier: LGPL-3.0-or-later
# apt may return zero after an index download failed; reject that case too.
set -o pipefail
export LC_ALL=C
log=$(mktemp)
trap 'rm -f "$log"' EXIT
if ! apt-get update 2>&1 | tee "$log"; then
    exit 1
fi
if grep -Eq '^(W: Failed to fetch |W: Some index files failed|E:)' "$log"; then
    echo 'Package index update was incomplete; see the failed index above.' >&2
    exit 1
fi
