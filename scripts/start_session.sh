#!/usr/bin/env bash
# SPDX-License-Identifier: LGPL-3.0-or-later
set -euo pipefail
source "$(dirname "$0")/lib/native_arch.sh"
layout=''; setup=''; alignment=''; name='public-session'; image='local/public-session:native'; attach=1; example=0
while (($#)); do
  case "$1" in
    --layout) layout=${2:?missing layout directory}; shift 2 ;;
    --setup) setup=${2:?missing setup file}; shift 2 ;;
    --alignment) alignment=${2:?missing alignment file}; shift 2 ;;
    --name) name=${2:?missing container name}; shift 2 ;;
    --image) image=${2:?missing image tag}; shift 2 ;;
    --no-attach) attach=0; shift ;;
    --example) example=1; shift ;;
    *) echo "usage: $0 (--setup FILE [--alignment FILE] | --layout DIRECTORY) [--name CONTAINER] [--image IMAGE] [--no-attach] [--example]" >&2; exit 2 ;;
  esac
done
if [[ -n "$setup" && -n "$layout" ]]; then echo 'use either --setup or --layout, not both' >&2; exit 2; fi
if [[ -n "$alignment" && -z "$setup" ]]; then echo '--alignment needs --setup' >&2; exit 2; fi
if [[ -n "$setup" ]]; then
  # Check the setup and generate the session files in a private temporary
  # directory; nothing the user generated or edited is touched.
  [[ -f "$setup" ]] || { echo "setup file is missing: $setup" >&2; exit 2; }
  setup="$(cd "$(dirname "$setup")" && pwd)/$(basename "$setup")"
  layout=$(mktemp -d "${TMPDIR:-/tmp}/session-setup.XXXXXX")
  trap 'rm -rf "$layout"' EXIT
  prepare=(prepare "$setup" "$layout")
  if [[ $example == 1 ]]; then prepare+=(--example); fi
  if [[ -n "$alignment" ]]; then
    [[ -f "$alignment" ]] || { echo "alignment file is missing: $alignment" >&2; exit 2; }
    prepare+=(--alignment "$(cd "$(dirname "$alignment")" && pwd)/$(basename "$alignment")")
  fi
  (cd "$(dirname "$0")/.." && python3 -m session_setup.setupfile "${prepare[@]}") || exit 2
fi
require_native_image "$image"
[[ -d "$layout" ]] || { echo 'layout directory is missing' >&2; exit 2; }
container_id=$(docker create --name "$name" --network none "$image" sleep infinity)
start_in_container() {
  if [[ $example == 1 ]]; then
    docker exec "$container_id" python -m session_setup.start /tmp/session-run --example
  else
    docker exec "$container_id" python -m session_setup.start /tmp/session-run
  fi
}
if ! docker cp "$layout/." "$container_id:/tmp/session-run" || ! docker start "$container_id" >/dev/null || ! start_in_container; then
  docker rm -f "$container_id" >/dev/null 2>&1 || true
  exit 1
fi
if [[ -n "$setup" ]]; then rm -rf "$layout"; trap - EXIT; fi
printf 'Container: %s\n' "$name"
if [[ $attach == 1 && -t 0 && -t 1 ]]; then
  exec "$(dirname "$0")/attach_session.sh" "$name"
fi
printf 'Attach to the operator screen: scripts/attach_session.sh %s\n' "$name"
