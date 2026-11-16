#!/usr/bin/env bash
# SPDX-License-Identifier: LGPL-3.0-or-later
set -euo pipefail
cd "$(dirname "$0")/.."
source scripts/lib/native_arch.sh || exit 1
image='local/public-session'
target=full
while (($#)); do
  case "$1" in
    --acquisition-only) target=eudaq; shift ;;
    --image) image=${2:?missing image name}; shift 2 ;;
    *) echo "usage: $0 [--acquisition-only] [--image REPOSITORY]" >&2; exit 2 ;;
  esac
done
[[ "$image" =~ ^[a-z0-9][a-z0-9._/-]*$ ]] || { echo 'image must be an untagged lowercase repository name' >&2; exit 2; }
base="${image}-base:native"
docker build --platform "$NATIVE_PLATFORM" --memory 8g --target "$target" --build-arg "ROOT_IMAGE=$NATIVE_ROOT_IMAGE" --build-arg "TARGETARCH=$NATIVE_ARCH" -f docker/base/Dockerfile -t "$base" .
require_native_image "$base"
docker build --platform "$NATIVE_PLATFORM" --memory 8g --build-arg "PUBLIC_BASE=$base" -f docker/setup/Dockerfile -t "${image}:native" .
require_native_image "${image}:native"
if [[ "$target" == full ]]; then docker tag "$base" local/public-stack:native; fi
printf 'Built %s and %s\n' "$base" "${image}:native"
