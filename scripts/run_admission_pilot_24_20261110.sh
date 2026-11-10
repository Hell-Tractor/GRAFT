#!/usr/bin/env bash
# Default is read-only verification. --execute explicitly starts the paid pilot.
set -euo pipefail
cd /home/cym/workspace/GRAFT-2.0/GRAFTTool
pilot_mode="${1:---check-only}"
if [[ "$#" -gt 1 || ( "$pilot_mode" != "--check-only" && "$pilot_mode" != "--execute" ) ]]; then
  echo 'Usage: bash scripts/run_admission_pilot_24_20261110.sh [--check-only|--execute]' >&2
  exit 2
fi
python3 - <<'PY'
import hashlib,json
from pathlib import Path
manifest=Path('/home/cym/workspace/graft-admission-development-20261110/pilot-24-ready/calibration-manifest.json')
m=json.loads(manifest.read_text())
records=[m['admission_profile']]
for section in ['implementation','binaries']:
    records.extend(m[section].values())
for case in m['cases']:
    records.extend([case['original_generation'],case['original_plan']])
for record in records:
    if not record or hashlib.sha256(Path(record['path']).read_bytes()).hexdigest()!=record['sha256']:
        raise SystemExit('Frozen manifest mismatch; reconcile without regeneration: '+str(record and record['path']))
identities={(c['crate'],c['slot']) for c in m['cases']}
if len(m['cases'])!=24 or len(identities)!=24 or m['max_calibration_budget_cny']!='5.76':
    raise SystemExit('Unexpected pilot selection or budget')
print('Verified: 24 fixed targets; implementation/binary/source hashes match; model budget <= CNY 5.76.')
PY
if [[ "$pilot_mode" == "--check-only" ]]; then
  echo 'Check only: no requests sent; no output directory created.'
  exit 0
fi
pilot_output=/home/cym/workspace/graft-admission-pilot-24-execution-20261110
if [[ -e "$pilot_output" ]]; then
  echo "Existing pilot output: $pilot_output. Reconcile its paid ledger; do not retry in a new directory." >&2
  exit 1
fi
exec python3 scripts/calibrate_parameter_repair.py \
  --source-batch /home/cym/workspace/graft-cost-controlled-20261110 \
  --output "$pilot_output" \
  --planner /tmp/graft-admission-v4-target/debug/graft-plan \
  --harness /tmp/graft-admission-v4-target/debug/graft-harness \
  --admission-profile experiments/admission-v2/development-complete.json \
  --case-manifest /home/cym/workspace/graft-admission-development-20261110/pilot-24-ready/calibration-manifest.json \
  --include-accepted-controls --execute
