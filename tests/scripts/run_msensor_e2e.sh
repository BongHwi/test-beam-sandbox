#!/usr/bin/env bash
# SPDX-License-Identifier: LGPL-3.0-or-later
set -uo pipefail
cd "$(dirname "$0")/../.."
[[ $(uname -s) == Linux ]] || { echo 'E2E checks require a Linux host' >&2; exit 1; }
source scripts/lib/native_arch.sh || exit 1
require_native_image local/public-stack:native || exit 1
mkdir -p artifacts/msensor
docker build --platform "$NATIVE_PLATFORM" --memory 8g -t local/msensor-e2e:native -f docker/msensor/Dockerfile . || exit 1
image_id=$(docker image inspect local/msensor-e2e:native --format '{{.Id}}') || exit 1
commit=$(cat SOURCE_REVISION) || exit 1
mark_not_run() {
  RESULT_FILE="$1" IMAGE_ID="$image_id" TEST_COMMIT="$commit" python3 - <<'PYCODE'
import json, os
from pathlib import Path
p=Path('artifacts/msensor')/os.environ['RESULT_FILE']
p.write_text(json.dumps({'status':'not_run','reason':'current execution has not produced a result','image_id':os.environ['IMAGE_ID'],'repository_commit':os.environ['TEST_COMMIT'],'corryvreckan_source':'official master commit 22fca3e43ceec7ba6263a5cf179cd57d5fff42fe'},indent=2,ensure_ascii=False)+'\n')
PYCODE
}
run_container() {
  local mode=$1 cid status output
  case "$mode" in
    device) output='' ;;
    its3) output=E2.json; mark_not_run S1-eudaq.json ;;
    kill) output=S7.json ;;
    write) output=WRITE.json ;;
    *) output="eudaq-$mode-small.json" ;;
  esac
  if [ -n "$output" ]; then mark_not_run "$output"; fi
  if [ "$mode" = device ]; then
    cid=$(docker create --network=none -e "IMAGE_ID=$image_id" -e "TEST_COMMIT=$commit" local/msensor-e2e:native python /work/tests/e2e/msensor_device.py) || return 1
  elif [ "$mode" = write ]; then
    cid=$(docker create --network=none -e LD_PRELOAD=/work/tests/e2e/eudaq_timing.so -e EUDAQ_TIMING_TRACE=/tmp/msensor-eudaq-timing.jsonl -e MSENSOR_TEST_WRITE_FAIL=1 local/msensor-e2e:native python /work/tests/e2e/msensor_eudaq.py) || return 1
  elif [ "$mode" = kill ]; then
    cid=$(docker create --network=none -e LD_PRELOAD=/work/tests/e2e/eudaq_timing.so -e EUDAQ_TIMING_TRACE=/tmp/msensor-eudaq-timing.jsonl -e MSENSOR_TEST_KILL=1 local/msensor-e2e:native python /work/tests/e2e/msensor_eudaq.py) || return 1
  elif [ "$mode" = its3 ]; then
    cid=$(docker create --network=none -e LD_PRELOAD=/work/tests/e2e/eudaq_timing.so -e EUDAQ_TIMING_TRACE=/tmp/msensor-eudaq-timing.jsonl -e MSENSOR_TEST_MODE=continuous -e MSENSOR_TEST_COLLECTOR=its3 local/msensor-e2e:native python /work/tests/e2e/msensor_eudaq.py) || return 1
  else
    cid=$(docker create --network=none -e LD_PRELOAD=/work/tests/e2e/eudaq_timing.so -e EUDAQ_TIMING_TRACE=/tmp/msensor-eudaq-timing.jsonl -e "MSENSOR_TEST_MODE=$mode" local/msensor-e2e:native python /work/tests/e2e/msensor_eudaq.py) || return 1
  fi
  docker start -a "$cid"; status=$?
  if [ "$mode" = device ]; then
    docker cp "$cid:/out/." artifacts/msensor/ || status=1
  else
    if [ "$mode" = its3 ]; then output=E2.json; elif [ "$mode" = kill ]; then output=S7.json; elif [ "$mode" = write ]; then output=WRITE.json; else output="eudaq-$mode-small.json"; fi
    docker cp "$cid:/tmp/msensor-eudaq-result.json" "artifacts/msensor/$output" || status=1
    IMAGE_ID="$image_id" TEST_COMMIT="$commit" RESULT_FILE="$output" MODE="$mode" python3 - <<'PY'
import json, os
from pathlib import Path
p=Path('artifacts/msensor')/os.environ['RESULT_FILE']
if p.exists():
    d=json.loads(p.read_text()); d.update(image_id=os.environ['IMAGE_ID'],repository_commit=os.environ['TEST_COMMIT'],corryvreckan_source='official master commit 22fca3e43ceec7ba6263a5cf179cd57d5fff42fe',note='source revision at export')
    p.write_text(json.dumps(d,indent=2,ensure_ascii=False)+'\n')
    if os.environ['MODE']=='its3':
        copy=dict(d,id='S1-eudaq'); (p.parent/'S1-eudaq.json').write_text(json.dumps(copy,indent=2,ensure_ascii=False)+'\n')
PY
  fi
  docker rm -f "$cid" >/dev/null 2>&1 || true
  return "$status"
}
status=0
run_container device || status=1
run_container triggered || status=1
run_container continuous || status=1
run_container its3 || status=1
run_container kill || status=1
run_container write || status=1
run_fixture() {
  local fixture=$1 threshold=$2 delay=$3 output=$4 corry=${5:-0} cid rc
  mark_not_run "$output.json"
  cid=$(docker create --network=none -e LD_PRELOAD=/work/tests/e2e/eudaq_timing.so -e EUDAQ_TIMING_TRACE=/tmp/msensor-eudaq-timing.jsonl -e "MSENSOR_TEST_FIXTURE=$fixture" -e "MSENSOR_TEST_THRESHOLD=$threshold" -e "MSENSOR_TEST_DELAY=$delay" -e "MSENSOR_RUN_CORRY=$corry" local/msensor-e2e:native python /work/tests/e2e/msensor_eudaq.py) || return 1
  docker start -a "$cid"; rc=$?
  docker cp "$cid:/tmp/msensor-eudaq-result.json" "artifacts/msensor/$output.json" || rc=1
  IMAGE_ID="$image_id" TEST_COMMIT="$commit" RESULT_FILE="$output.json" python3 - <<'PYCODE'
import json,os
from pathlib import Path
p=Path('artifacts/msensor')/os.environ['RESULT_FILE']
if p.exists():
 d=json.loads(p.read_text()); d.update(image_id=os.environ['IMAGE_ID'], repository_commit=os.environ['TEST_COMMIT'],corryvreckan_source='official master commit 22fca3e43ceec7ba6263a5cf179cd57d5fff42fe',note='source revision at export'); p.write_text(json.dumps(d,indent=2,ensure_ascii=False)+'\n')
PYCODE
  docker rm -f "$cid" >/dev/null 2>&1 || true
  return "$rc"
}
run_fixture T_thr 100 0 T_thr_100 & p1=$!
run_fixture T_thr 200 0 T_thr_200 & p2=$!
run_fixture C_thr 100 0 C_thr_100 & p3=$!
wait "$p1" || status=1
wait "$p2" || status=1
wait "$p3" || status=1
run_fixture C_thr 200 0 C_thr_200 & p1=$!
run_fixture T_strobe 100 0 T_strobe_0 & p2=$!
run_fixture T_strobe 100 5000 T_strobe_5000 & p3=$!
wait "$p1" || status=1
wait "$p2" || status=1
wait "$p3" || status=1
run_fixture C_rate0 80 0 C_rate0_eudaq & p1=$!
run_fixture C_offset 100 0 C_offset_eudaq & p2=$!
wait "$p1" || status=1
wait "$p2" || status=1
run_fixture T_thr 100 0 T_thr_corry_final 1 & p1=$!
run_fixture C_thr 100 0 C_thr_corry_final 1 & p2=$!
wait "$p1" || status=1
wait "$p2" || status=1
run_fixture T_thr 200 0 T_thr_corry_200 1 & p1=$!
run_fixture C_thr 200 0 C_thr_corry_200 1 & p2=$!
wait "$p1" || status=1
wait "$p2" || status=1
IMAGE_ID="$image_id" TEST_COMMIT="$commit" python3 tests/e2e/msensor_aggregate.py || status=1
python3 - <<'PYCODE' || status=1
import json
from pathlib import Path
required=['CFG','D1','D2','D3','D4','A1','A2','A3','A4','A5','Z1','Z2','Z3','Z4','Z2-eudaq','Z3-eudaq','Z4-eudaq','S1','S1-eudaq','S3','S4a','S4b','S5a','S5b','S5c','S5d','S6a','S6b','S6c','S6d','S6e','S7','S8','E1','E2','E3','FLUSH','WRITE','SECRET']
errors=[]
for name in required:
    path=Path('artifacts/msensor')/(name+'.json')
    if not path.exists(): errors.append((name,'missing'))
    else:
        status=json.loads(path.read_text()).get('status')
        if status!='pass': errors.append((name,status))
print(json.dumps({'required':len(required),'not_passed':errors}))
raise SystemExit(1 if errors else 0)
PYCODE
exit "$status"
