#!/usr/bin/env bash
# SPDX-License-Identifier: LGPL-3.0-or-later
set -euo pipefail
[[ $# == 1 ]] || { echo "usage: $0 CONTAINER" >&2; exit 2; }
[[ -t 0 && -t 1 ]] || { echo 'An interactive terminal is required to attach.' >&2; exit 1; }
exec docker exec -it "$1" tmux attach-session -t ITS3:rc
