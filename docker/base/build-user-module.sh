#!/usr/bin/env bash
# SPDX-License-Identifier: LGPL-3.0-or-later
# Run inside the image after copying a public EUDAQ user module into /opt/eudaq-src/user/<name>.
set -euo pipefail
name=${1:?usage: build-user-module.sh NAME}
case "$name" in *[!A-Za-z0-9_-]*|'') echo 'invalid module name' >&2; exit 2;; esac
src=/opt/eudaq-src/user/$name
[[ -f "$src/CMakeLists.txt" ]] || { echo "missing $src/CMakeLists.txt" >&2; exit 1; }
line="add_subdirectory($name)"
if ! grep -Fqx "$line" /opt/eudaq-src/user/CMakeLists.txt; then printf '\n%s\n' "$line" >> /opt/eudaq-src/user/CMakeLists.txt; fi
cmake -S /opt/eudaq-src -B /opt/eudaq-build "-DUSER_${name}_BUILD=ON"
cmake --build /opt/eudaq-build --parallel 4
cmake --install /opt/eudaq-build
