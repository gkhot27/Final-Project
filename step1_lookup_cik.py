"""Step 1: ticker -> CIK.

Usage:  python step1_lookup_cik.py AAPL
"""
import sys

import config
import sec_client


def lookup(ticker_or_cik: str):
    """Return (cik:int, name, ticker). Accepts a ticker ('AAPL') or a CIK ('320193')."""
    data = sec_client.get_json_cached(config.TICKERS_URL, "company_tickers.json")
    rows = list(data.values())
    q = ticker_or_cik.strip().upper()
    if q.isdigit():
        cik = int(q)
        for r in rows:
            if r["cik_str"] == cik:
                return cik, r["title"], r["ticker"]
        return cik, None, None        # valid CIK that no longer has a ticker
    for r in rows:
        if r["ticker"].upper() == q:
            return r["cik_str"], r["title"], r["ticker"]
    sys.exit(f"Ticker {q} not found in SEC's current ticker list. "
             "If it was delisted/renamed, pass the CIK number instead.")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    cik, name, ticker = lookup(sys.argv[1])
    print(f"{ticker}  CIK={cik}  ({name})")
