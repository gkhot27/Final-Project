#!/usr/bin/env bash
# One-shot: set up, download, load, backfill, reconcile.
#   ./run_all.sh AAPL                 # all quarters 2006 -> now (~1 GB download)
#   ./run_all.sh AAPL 2024q1          # only from 2024q1 (fast first run)
set -euo pipefail
cd "$(dirname "$0")"
TICKER="${1:?usage: ./run_all.sh TICKER [START_QUARTER]}"
START="${2:-2006q1}"

if [ -z "${SEC_USER_AGENT:-}" ] && grep -q 'CHANGE ME' config.py; then
  echo 'Set your SEC contact first:  export SEC_USER_AGENT="Your Name you@email.com"'; exit 1
fi

[ -d .venv ] || python3 -m venv .venv
source .venv/bin/activate
pip install -q -r requirements.txt

python step1_lookup_cik.py "$TICKER"
python step2_download_bulk.py --start "$START"
python step3_load_duckdb.py
python step4_backfill_api.py "$TICKER"
python step5_reconcile.py "$TICKER"
