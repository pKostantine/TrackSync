#!/usr/bin/env bash
# Ground-truth tests: synthetic recordings with known offsets and known drift,
# so the numbers are checked rather than eyeballed.
set -uo pipefail
cd "$(dirname "$0")"
unset PYTHONHOME PYTHONPATH
export PYTHONNOUSERSITE=1

[ -x .venv/bin/python ] || { echo "Run ./run_dev.sh once first."; exit 1; }

echo "=== engine ==="
.venv/bin/python -m tests.test_engine
engine=$?
echo
echo "=== app ==="
QT_QPA_PLATFORM=offscreen .venv/bin/python -m tests.test_app
app=$?
echo
[ $engine -eq 0 ] && [ $app -eq 0 ] && echo "All suites passed." || echo "Something failed above."
exit $(( engine || app ))
