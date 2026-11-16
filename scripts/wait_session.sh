#!/usr/bin/env bash
# SPDX-License-Identifier: LGPL-3.0-or-later
set -euo pipefail
[[ $# == 2 && ( "$2" == ready || "$2" == closed ) ]] || { echo "usage: $0 CONTAINER {ready|closed}" >&2; exit 2; }
docker exec "$1" python -m session_setup.wait "$2"
