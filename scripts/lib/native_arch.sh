#!/usr/bin/env bash
# SPDX-License-Identifier: LGPL-3.0-or-later
# Source from another script. Do not enable cross-platform emulation implicitly.
_native_arch() {
  case "$1" in aarch64|arm64) printf arm64;; x86_64|amd64) printf amd64;; *) return 1;; esac
}
_docker_os=$(docker info --format '{{.OSType}}') || { echo 'Docker daemon unavailable' >&2; return 1 2>/dev/null || exit 1; }
[[ "$_docker_os" == linux ]] || { echo "Docker daemon must run Linux containers (reported: $_docker_os)" >&2; return 1 2>/dev/null || exit 1; }
_docker_machine=$(docker info --format '{{.Architecture}}') || { echo 'Docker daemon unavailable' >&2; return 1 2>/dev/null || exit 1; }
NATIVE_ARCH=$(_native_arch "$_docker_machine") || { echo "unsupported Docker daemon architecture: $_docker_machine" >&2; return 1 2>/dev/null || exit 1; }
source "$(dirname "${BASH_SOURCE[0]}")/../../docker/architectures.lock"
case "$NATIVE_ARCH" in arm64) NATIVE_ROOT_IMAGE=$ROOT_IMAGE_arm64;; amd64) NATIVE_ROOT_IMAGE=$ROOT_IMAGE_amd64;; esac
NATIVE_PLATFORM="linux/$NATIVE_ARCH"
DOCKER_DEFAULT_PLATFORM=$NATIVE_PLATFORM
export NATIVE_ARCH NATIVE_PLATFORM NATIVE_ROOT_IMAGE DOCKER_DEFAULT_PLATFORM
require_native_image() {
  local observed
  observed=$(docker image inspect "$1" --format '{{.Os}}/{{.Architecture}}') || return 1
  [[ "$observed" == "$NATIVE_PLATFORM" ]] || { printf 'Image %s is %s, expected %s\n' "$1" "$observed" "$NATIVE_PLATFORM" >&2; return 1; }
}
