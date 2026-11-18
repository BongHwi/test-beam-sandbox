#!/usr/bin/env bash
# SPDX-License-Identifier: LGPL-3.0-or-later
set -euo pipefail
cd "$(dirname "$0")/.."
source scripts/lib/native_arch.sh || exit 1
image='local/public-session'
target=full
nestdaq=false
while (($#)); do
  case "$1" in
    --nestdaq) nestdaq=true; shift ;;
    --acquisition-only) target=eudaq; shift ;;
    --image) image=${2:?missing image name}; shift 2 ;;
    *) echo "usage: $0 [--acquisition-only] [--image REPOSITORY] [--nestdaq]" >&2; exit 2 ;;
  esac
done
[[ "$image" =~ ^[a-z0-9][a-z0-9._/-]*$ ]] || { echo 'image must be an untagged lowercase repository name' >&2; exit 2; }
if $nestdaq && [[ "$target" != full ]]; then echo "--nestdaq requires the full session image" >&2; exit 2; fi
base="${image}-base:native"
docker build --platform "$NATIVE_PLATFORM" --memory 8g --target "$target" --build-arg "ROOT_IMAGE=$NATIVE_ROOT_IMAGE" --build-arg "TARGETARCH=$NATIVE_ARCH" -f docker/base/Dockerfile -t "$base" .
require_native_image "$base"
docker build --platform "$NATIVE_PLATFORM" --memory 8g --build-arg "PUBLIC_BASE=$base" -f docker/setup/Dockerfile -t "${image}:native" .
require_native_image "${image}:native"
if [[ "$target" == full ]]; then docker tag "$base" local/public-stack:native; fi
if $nestdaq; then
  docker build --platform "$NATIVE_PLATFORM" --memory 8g --build-arg "SESSION_IMAGE=${image}:native" --build-arg "BASE_IMAGE=$base" --build-arg "TARGETARCH=$NATIVE_ARCH" -f docker/nestdaq/Dockerfile -t "${image}-nestdaq:native" .
  require_native_image "${image}-nestdaq:native"
  printf 'Built %s, %s and %s\n' "$base" "${image}:native" "${image}-nestdaq:native"
else
  printf 'Built %s and %s\n' "$base" "${image}:native"
fi
