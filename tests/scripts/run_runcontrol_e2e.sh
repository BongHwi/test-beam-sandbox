#!/usr/bin/env bash
# SPDX-License-Identifier: LGPL-3.0-or-later
set -u -o pipefail
cd "$(dirname "$0")/../.."
[[ $(uname -s) == Linux ]] || { echo 'E2E checks require a Linux host' >&2; exit 1; }
source scripts/lib/native_arch.sh || exit 1
require_native_image local/public-stack:native || exit 1
docker build -f docker/runcontrol/Dockerfile -t local/runcontrol:native . || exit 1
prefix="runcontrol-e2e-$$"
cases=(scenarios error launcher compare1 compare4 compare5 compare9 compare10)
cleanup() { for case_name in "${cases[@]}"; do docker rm -f "${prefix}-${case_name}" >/dev/null 2>&1 || true; done; }
trap cleanup EXIT
mkdir -p artifacts/runcontrol
for n in $(seq 1 14); do rm -f "artifacts/runcontrol/H-E${n}.json"; done
for diagnostic in harness-error comparison-error launcher-error launcher-debug; do rm -f "artifacts/runcontrol/${diagnostic}.json"; done
for case_name in "${cases[@]}"; do
    name="${prefix}-${case_name}"
    docker create --name "$name" --network none local/runcontrol:native sleep 600 >/dev/null || exit 1
    for file in runcontrol_scenarios.py runcontrol_comparisons.py runcontrol_error.py runcontrol_launcher.py runcontrol_producer.py; do
        docker cp "tests/e2e/$file" "$name:/tmp/$file" || exit 1
    done
    docker cp tests/e2e/runcontrol_observer "$name:/tmp/runcontrol_observer" || exit 1
    docker cp tests/e2e/runcontrol_ready_observer "$name:/tmp/runcontrol_ready_observer" || exit 1
    docker start "$name" >/dev/null || exit 1
    docker exec "$name" mkdir -p /tmp/runcontrol-results || exit 1
done
failed=0
# At most four independent, network-isolated scenarios execute at a time.
for batch in 'scenarios error launcher compare4' 'compare1 compare5 compare9 compare10'; do
    pids=()
    for case_name in $batch; do
        if [[ $case_name == compare* ]]; then
            target=${case_name#compare}
            docker exec -e "RUNCONTROL_TEST_TARGET=$target" "${prefix}-${case_name}" python /tmp/runcontrol_comparisons.py >"/tmp/${prefix}-${case_name}.log" 2>&1 &
        else
            docker exec "${prefix}-${case_name}" python "/tmp/runcontrol_${case_name}.py" >"/tmp/${prefix}-${case_name}.log" 2>&1 &
        fi
        pids+=("$!")
    done
    for pid in "${pids[@]}"; do wait "$pid" || failed=1; done
done
for pair in 'scenarios:2 3 6 7 11' 'error:8' 'launcher:12 13 14' 'compare1:1' 'compare4:4' 'compare5:5' 'compare9:9' 'compare10:10'; do
    case_name=${pair%%:*}; numbers=${pair#*:}
    for n in $numbers; do
        docker cp "${prefix}-${case_name}:/tmp/runcontrol-results/H-E${n}.json" "artifacts/runcontrol/H-E${n}.json" || failed=1
    done
done
for pair in 'scenarios:harness-error' 'launcher:launcher-error' 'launcher:launcher-debug' 'compare1:comparison-error' 'compare4:comparison-error' 'compare5:comparison-error' 'compare9:comparison-error' 'compare10:comparison-error'; do
    case_name=${pair%%:*}; diagnostic=${pair#*:}
    docker cp "${prefix}-${case_name}:/tmp/runcontrol-results/${diagnostic}.json" "artifacts/runcontrol/${case_name}-${diagnostic}.json" 2>/dev/null || true
done
HARNESS_FAILED="$failed" python3 - <<'PY'
import json,os,pathlib,sys
p=pathlib.Path('artifacts/runcontrol'); records={}
for i in range(1,15):
    path=p/f'H-E{i}.json'
    try: records[f'H-E{i}']=json.loads(path.read_text())['status']
    except (FileNotFoundError,ValueError,KeyError): records[f'H-E{i}']='not_run'
print(json.dumps(records,indent=2))
sys.exit(0 if os.environ['HARNESS_FAILED']=='0' and all(v=='pass' for v in records.values()) else 1)
PY
