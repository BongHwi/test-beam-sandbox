#!/usr/bin/env bash
# SPDX-License-Identifier: LGPL-3.0-or-later
set -uo pipefail
cd "$(dirname "$0")/../.."
[[ $(uname -s) == Linux ]] || { echo 'E2E checks require a Linux host' >&2; exit 1; }
source scripts/lib/native_arch.sh || exit 1
require_native_image local/session-core:native || exit 1
name="session-example-$$"
trap 'docker rm -f "$name" >/dev/null 2>&1 || true' EXIT
docker create --name "$name" --network none local/session-core:native sleep 600 >/dev/null || exit 1
for file in session_example.py session_scenario.py session_inspect.py alpide_convert.cc; do
  docker cp "tests/e2e/$file" "$name:/tmp/$file" || exit 1
done
for dir in runcontrol_observer session_observer; do docker cp "tests/e2e/$dir" "$name:/tmp/$dir" || exit 1; done
docker cp configs/session/geometry.json "$name:/tmp/session_example_geometry.json" || exit 1
docker start "$name" >/dev/null || exit 1
docker exec "$name" python /tmp/session_example.py || exit 1
docker exec "$name" python /tmp/session_inspect.py || exit 1
mkdir -p artifacts/session-core/example
docker cp "$name:/tmp/session-results/." artifacts/session-core/example/ || exit 1
python3 - <<'PY'
import json,pathlib,platform,subprocess,sys
p=pathlib.Path('artifacts/session-core/example')
image=subprocess.check_output(['docker','image','inspect','local/session-core:native','--format','{{.Id}}'],text=True).strip()
commit=(__import__('pathlib').Path('SOURCE_REVISION').read_text().strip())
results={}
for name in ('C-E1','C-E2','C-E3'):
 path=p/(name+'.json');data=json.loads(path.read_text())
 data.update(id=name,repository_commit=commit,image_id=image,architecture=platform.machine(),
             input_conditions={'geometry':'configs/session/geometry.json','two_runs':True},
             criterion='documented end-to-end acceptance criteria')
 path.write_text(json.dumps(data,indent=2,ensure_ascii=False)+'\n')
 results[name]=data['status']
print(json.dumps(results))
sys.exit(0 if all(v=='pass' for v in results.values()) else 1)
PY
