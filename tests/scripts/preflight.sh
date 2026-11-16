#!/usr/bin/env bash
# SPDX-License-Identifier: LGPL-3.0-or-later
set -uo pipefail
cd "$(dirname "$0")/../.."
source scripts/lib/native_arch.sh || exit 1
python3 - <<'PY'
import datetime, hashlib, json, os, platform, shutil, subprocess, tempfile
from pathlib import Path

def check(cmd, timeout=30, stdin=None):
    try:
        r = subprocess.run(cmd, input=stdin, text=True, capture_output=True, timeout=timeout)
        return {"status": "pass" if r.returncode == 0 else "fail", "exit_code": r.returncode,
                "output": (r.stdout + r.stderr)[-4000:]}
    except subprocess.TimeoutExpired:
        return {"status": "fail", "reason": f"timeout after {timeout}s"}
    except OSError as e:
        return {"status": "fail", "reason": str(e)}

arch = platform.machine()
os_name = platform.system()
available = shutil.disk_usage('.').free
corry = check(["git", "ls-remote", "https://gitlab.cern.ch/corryvreckan/corryvreckan.git", "refs/heads/master"], 60)
if corry["status"] == "pass" and "22fca3e43ceec7ba6263a5cf179cd57d5fff42fe" not in corry["output"]:
    # The branch may advance; the build fetches and verifies the immutable commit.
    corry["note"] = "master has advanced; build checks the pinned commit"
checks = {
    "platform": {"status": "pass" if os_name == "Linux" and arch in ("aarch64", "x86_64") else "fail", "os": os_name, "arch": arch},
    "docker_build": check(["docker", "build", "--network=none", "-t", "local-preflight-native", "-f", "-", "."], 60, "FROM scratch\nLABEL preflight=native\n"),
    "docker_run": check(["docker", "run", "--rm", "--network=none", "--entrypoint", "/bin/sh", "ubuntu:24.04", "-c", "uname -m"], 60),
    "eudaq_url": check(["curl", "-fsSL", "--connect-timeout", "10", "--max-time", "20", "-o", "/dev/null", "https://github.com/eudaq/eudaq"], 25),
    "corryvreckan_official_repository": corry,
    "disk": {"status": "pass" if available >= 40 * 1024**3 else "fail", "available_bytes": available, "minimum_bytes": 40 * 1024**3},
}
result = {"timestamp_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(), "checks": checks,
          "status": "pass" if all(x["status"] == "pass" for x in checks.values()) else "fail",
          "other_architecture": {"status": "not_run", "reason": "Only the native architecture is tested by this execution."}}
Path('artifacts/public-stack').mkdir(parents=True, exist_ok=True)
Path('artifacts/public-stack/preflight.json').write_text(json.dumps(result, indent=2) + '\n')
print(json.dumps(result, indent=2))
raise SystemExit(0 if result['status'] == 'pass' else 1)
PY
