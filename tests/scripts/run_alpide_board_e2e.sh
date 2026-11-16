#!/usr/bin/env bash
# SPDX-License-Identifier: LGPL-3.0-or-later
set -u -o pipefail
cd "$(dirname "$0")/../.."
[[ $(uname -s) == Linux ]] || { echo 'E2E checks require a Linux host' >&2; exit 1; }
source scripts/lib/native_arch.sh || exit 1
require_native_image local/public-stack:native || exit 1
docker build -f docker/runcontrol/Dockerfile -t local/runcontrol:native . || exit 1
docker build -f docker/virtual-alpide/Dockerfile -t local/virtual-alpide:native . || exit 1
name="alpide-e2e-$$"
trap 'docker rm -f "$name" >/dev/null 2>&1 || true' EXIT
docker create --name "$name" --network none local/virtual-alpide:native sleep 900 >/dev/null || exit 1
for f in alpide_main.py alpide_test_source.py alpide_scenarios.py alpide_convert.cc; do docker cp "tests/e2e/$f" "$name:/tmp/$f" || exit 1; done
docker cp tests/e2e/alpide_observer "$name:/tmp/alpide-observer" || exit 1
docker start "$name" >/dev/null || exit 1
docker exec "$name" python /tmp/alpide_main.py || exit 1
for scenario in boundary six idle absent unknown; do docker exec "$name" python /tmp/alpide_scenarios.py "$scenario" || exit 1; done
# The sole module-only check allowed by V-E8 checks that unknown members raise.
docker exec "$name" python -c 'import alpidedaqboard; b=alpidedaqboard.ALPIDEDAQBoard("VIRTUAL0"); getattr(b,"outside_public_surface")' >/dev/null 2>&1
surface_exit=$?
docker exec -i -e "SURFACE_EXIT=$surface_exit" "$name" python - <<'PY'
import json,os,pathlib
out=pathlib.Path('/tmp/alpide-results')
def read(n):
 try:return json.loads((out/n).read_text())
 except (OSError,ValueError):return {'status':'not_run'}
a=read('absent-observation.json');u=read('unknown-observation.json');inside=read('V-E14.json')
status='pass' if int(os.environ['SURFACE_EXIT'])!=0 and a['status']=='pass' and u['status']=='pass' else 'fail'
(out/'V-E8.json').write_text(json.dumps({'status':status,'measurement':{'surface_exit':int(os.environ['SURFACE_EXIT']),'without_source':a,'unknown_serial':u}},indent=2)+'\n')
(out/'V-E14.json').write_text(json.dumps({'status':'not_run','reason':'deferred; not checked'},indent=2)+'\n')
PY
mkdir -p artifacts/virtual-board
docker cp "$name:/tmp/alpide-results/." artifacts/virtual-board/ || exit 1
python3 - <<'PY'
import json,pathlib,sys,subprocess,platform
p=pathlib.Path('artifacts/virtual-board'); image=subprocess.check_output(['docker','image','inspect','local/virtual-alpide:native','--format','{{.Id}}'],text=True).strip();commit=(__import__('pathlib').Path('SOURCE_REVISION').read_text().strip())
for i in range(1,15):
 path=p/f'V-E{i}.json'; d=json.loads(path.read_text());d.update(repository_commit=commit,image_id=image,architecture=platform.machine(),input_conditions={'scenario':f'V-E{i}','public_eudaq_commit':'bfe26c7b73844344b2cbfa37ab853f2a0cb1f23e'},criterion='virtual board acceptance criteria; deferred checks are not run')
 path.write_text(json.dumps(d,indent=2,ensure_ascii=False)+'\n')
r={f'V-E{i}':json.loads((p/f'V-E{i}.json').read_text())['status'] for i in range(1,15)}
required=[1,2,3,4,5,6,7,8,10,11,13]
print(json.dumps(r,indent=2));sys.exit(0 if all(r[f'V-E{i}']=='pass' for i in required) else 1)
PY
