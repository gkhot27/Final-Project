"""Step 2: download the SEC Insider Transactions Data Sets (one zip per quarter).

Usage:
  python step2_download_bulk.py                     # every quarter 2006q1 -> latest available
  python step2_download_bulk.py --start 2024q1      # just recent quarters (good first test)
  python step2_download_bulk.py --start 2025q1 --end 2025q4

Already-downloaded zips are skipped, so it is safe to re-run.
"""
import argparse
import datetime as dt
import zipfile

import config
import sec_client


def quarters(start: str, end: str):
    y, q = int(start[:4]), int(start[-1])
    ey, eq = int(end[:4]), int(end[-1])
    while (y, q) <= (ey, eq):
        yield f"{y}q{q}"
        q += 1
        if q == 5:
            y, q = y + 1, 1


def current_quarter():
    today = dt.date.today()
    return f"{today.year}q{(today.month - 1) // 3 + 1}"


def download(q: str) -> str:
    path = config.ZIP_DIR / f"{q}_form345.zip"
    if path.exists() and zipfile.is_zipfile(path):
        return "cached"
    r = sec_client.get(config.BULK_URL.format(q=q), stream=True)
    if r.status_code == 404:
        return "not published yet"
    r.raise_for_status()
    tmp = path.with_suffix(".part")
    with open(tmp, "wb") as f:
        for chunk in r.iter_content(1 << 20):
            f.write(chunk)
    if not zipfile.is_zipfile(tmp):
        tmp.unlink()
        return "bad zip (SEC returned something else - check User-Agent)"
    tmp.rename(path)
    return f"downloaded {path.stat().st_size / 1e6:.1f} MB"


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default=config.FIRST_BULK_QUARTER)
    ap.add_argument("--end", default=current_quarter())
    a = ap.parse_args()
    for q in quarters(a.start, a.end):
        print(f"{q}: {download(q)}")
