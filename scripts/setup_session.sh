#!/usr/bin/env bash
# SPDX-License-Identifier: LGPL-3.0-or-later
# Create, show or check a session setup file: new FILE, show FILE, check FILE.
set -euo pipefail
cd "$(dirname "$0")/.."
exec python3 -m session_setup.setupfile "$@"
