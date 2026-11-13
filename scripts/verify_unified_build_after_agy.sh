#!/usr/bin/env bash
set -u
cd /root/multi-exchange-paper || exit 1
echo "VERIFICATION_AT_UTC=$(date -u +%FT%TZ)"
if [[ ! -f UNIFIED_TREASURY_IMPLEMENTATION_REPORT.md ]]; then
  echo "RESULT=INCOMPLETE_MISSING_REPORT"
  exit 2
fi
PYTHONDONTWRITEBYTECODE=1 .venv/bin/pytest -q -p no:cacheprovider
test_status=$?
echo "TEST_EXIT=$test_status"
systemctl show multi-exchange-paper.service -p ActiveState -p NRestarts -p MainPID
python3 - <<'PY'
import json
p='data/readiness_watch.json'
d=json.load(open(p))
print('LIVE_EXECUTION='+str(d.get('live_execution')))
print('LIVE_READINESS='+str(d.get('live_readiness')))
PY
if [[ $test_status -ne 0 ]]; then echo "RESULT=TESTS_FAILED"; exit "$test_status"; fi
echo "RESULT=TESTS_PASS_REQUIRES_DEPLOYMENT_REVIEW"
