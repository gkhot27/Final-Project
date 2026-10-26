# Insider Trade Reconstruction

Rebuilds every insider's share-ownership timeline for a company from SEC Form 3/4/5
filings, checks each step against the total the insider reported
(`sharesOwnedFollowingTransaction`), explains mismatches, and flags late Form 4s.

## Quick start (Mac Terminal)

```bash
cd ~/Final_project_raj
export SEC_USER_AGENT="Your Name you@email.com"   # SEC requires a real contact
chmod +x run_all.sh
./run_all.sh AAPL 2024q1      # fast first run: bulk data from 2024 on
./run_all.sh AAPL             # full history: every quarter 2006 -> now (~1 GB)
```

Results land in `output/AAPL/`. To check the code works without internet first:
`python3 selftest.py` (all 8 checks should PASS).

## The steps

| Step | Script | What it does |
|---|---|---|
| 1 | `step1_lookup_cik.py AAPL` | Ticker -> CIK via `company_tickers.json` (also accepts a CIK) |
| 2 | `step2_download_bulk.py [--start 2024q1]` | Downloads quarterly zips into `data/bulk_zips/` (skips ones you have) |
| 3 | `step3_load_duckdb.py` | Loads each zip into `data/insider.duckdb`; skips quarters already loaded |
| 4 | `step4_backfill_api.py AAPL [--fts]` | Lists the company's Form 4s via the EDGAR API, fetches XML for any not in the DB (current quarter, 2003-2005), parses into the same tables |
| 5 | `step5_reconcile.py AAPL` | Builds timelines, reconciles, classifies mismatches, flags late filings |

## Outputs (`output/<TICKER>/`)

- `timeline.csv`: every event per insider, security, and account, with our expected total, the reported total, the difference, and a status
- `mismatches.csv`: rows that didn't reconcile, with a reason (stock split, amendment, DRIP/401k accrual, transfer between accounts, long gap or missing filing, rounding, unexplained) and the footnote text
- `late_filings.csv`: Form 4s filed after the deadline, with business days late, the filer's own late flag (`TRANS_TIMELINESS = L`), and notes
- `summary.txt`: match rate and headline counts

## How reconciliation works

- **Chain key:** insider CIK + normalized security (Common, COMMON, and $.01 PV Com Stk all map to `common`, while Class A and Class B stay separate) + account (direct, or indirect by trust, spouse, and so on).
- **Sign:** `TRANS_SHARES` is always positive, so the A/D code sets the sign.
- **Check:** expected = previous balance + delta; this is compared to the reported total (tolerance 0.01 shares). After each check the balance **re-anchors to the reported total**, so one miss doesn't cascade.
- **Starting balance:** a chain's first row infers its starting balance (reported - delta). Holding rows (Form 3 and Table I holdings) reset the baseline.
- **Joint filings:** rows are attributed to every reporting owner on the filing.

## Late-filing rule

- Trades on or after **2002-08-29** (Sarbanes-Oxley): due 2 business days after the trade (or after the deemed execution date). The holiday calendar is US federal holidays.
- Earlier trades: due on the 10th of the following month.
- Gifts (code G) before 2023-02-27 could legally be deferred to Form 5, so they're noted.

## Known limits (v1)

- Only non-derivative (Table I) shares are reconciled. Derivative tables are loaded but not yet reconciled.
- Amendments (4/A) are kept alongside the originals and labelled in the mismatch reasons, rather than replacing them.
- Pre-2003 filings are mostly paper or plain text. Step 4 counts them as "plain-text (no XML)" and skips them.
- Bulk data: about 13 MB per quarter, and likely a few GB of DuckDB for the full 2006+ history. Use `--start` to limit it.

## Data sources

- Insider Transactions Data Sets: https://www.sec.gov/data-research/sec-markets-data/insider-transactions-data-sets
- Submissions API: `https://data.sec.gov/submissions/CIK##########.json`
- Full-text search: `https://efts.sec.gov/LATEST/search-index` (`--fts`)
- Filing archive: `https://www.sec.gov/Archives/edgar/data/<cik>/<accession>/<doc>.xml`

The SEC fair-access policy applies: the scripts send your User-Agent, stay under 10 requests per second, and cache everything under `data/`.
