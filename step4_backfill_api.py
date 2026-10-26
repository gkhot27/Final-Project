"""Step 4: fill the gaps the bulk data doesn't cover (current quarter, 2003-2005) by
listing the company's Form 4s via the EDGAR API and fetching each XML.

Usage:
  python step4_backfill_api.py AAPL                       # every Form 4 missing from the DB
  python step4_backfill_api.py AAPL --since 2003-01-01 --until 2005-12-31
  python step4_backfill_api.py AAPL --fts                 # also query full-text search

How filings are found:
  * data.sec.gov/submissions/CIK##########.json is EDGAR's index of every filing that
    lists the company (Form 4s appear under the issuer). It's complete and exact, so it
    is the primary source.
  * efts.sec.gov full-text search (--fts) is the lookup from the project plan; it only
    covers 2001+ and matches text, so hits are filtered to this issuer's CIK.
Any accession already in the database (from bulk data) is skipped.
"""
import argparse
import datetime as dt

import pandas as pd

import config
import form4_parser
import sec_client
from step1_lookup_cik import lookup
from step3_load_duckdb import _table_exists, append_relation, connect, create_views


def list_from_submissions(cik):
    data = sec_client.get_json_cached(config.SUBMISSIONS_URL.format(cik=cik), f"sub_{cik}.json")
    blocks = [data["filings"]["recent"]]
    for f in data["filings"].get("files", []):
        blocks.append(sec_client.get_json_cached(
            config.SUBMISSIONS_PAGE_URL.format(name=f["name"]), f["name"], max_age_hours=24 * 30))
    out = []
    for b in blocks:
        for form, acc, fdate, doc in zip(b["form"], b["accessionNumber"],
                                         b["filingDate"], b["primaryDocument"]):
            if form in ("4", "4/A"):
                out.append({"accession": acc, "filing_date": fdate, "form": form,
                            "doc": doc.split("/")[-1] if doc else ""})
    return out


def list_from_fts(cik, name, since, until):
    out, start = [], 0
    while True:
        params = {"q": f'"{name}"', "forms": "4", "from": start,
                  "dateRange": "custom", "startdt": since, "enddt": until}
        r = sec_client.get(config.FTS_URL, params=params)
        if r.status_code != 200:
            print(f"  full-text search returned HTTP {r.status_code}; skipping")
            break
        hits = r.json().get("hits", {}).get("hits", [])
        if not hits:
            break
        for h in hits:
            src = h.get("_source", {})
            if str(cik).zfill(10) not in src.get("ciks", []):
                continue          # text matched, but filing is about another issuer
            acc, _, doc = h["_id"].partition(":")
            out.append({"accession": acc, "filing_date": src.get("file_date"),
                        "form": src.get("form", "4"), "doc": doc})
        start += len(hits)
        if start >= r.json()["hits"]["total"]["value"]:
            break
    return out


def quarter_of(date_iso):
    d = dt.date.fromisoformat(date_iso)
    return f"{d.year}q{(d.month - 1) // 3 + 1}"


def insert_rows(con, parsed, quarter):
    for table, rows in parsed.items():
        if not rows:
            continue
        df = pd.DataFrame(rows).astype("string")
        df["quarter"] = quarter
        df["source"] = "api"
        con.register("df_in", df)
        append_relation(con, table.lower(), "SELECT * FROM df_in")
        con.unregister("df_in")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("ticker")
    ap.add_argument("--since", default="2001-01-01")
    ap.add_argument("--until", default=dt.date.today().isoformat())
    ap.add_argument("--fts", action="store_true", help="also use EDGAR full-text search")
    a = ap.parse_args()

    cik, name, ticker = lookup(a.ticker)
    print(f"{ticker or a.ticker}: CIK {cik} ({name})")
    found = {f["accession"]: f for f in list_from_submissions(cik)}
    if a.fts and name:
        for f in list_from_fts(cik, name, a.since, a.until):
            found.setdefault(f["accession"], f)
    filings = [f for f in found.values() if a.since <= f["filing_date"] <= a.until]

    con = connect()
    have = set()
    if _table_exists(con, "submission"):
        have = {r[0] for r in con.execute("SELECT DISTINCT ACCESSION_NUMBER FROM submission").fetchall()}
    todo = sorted((f for f in filings if f["accession"] not in have), key=lambda f: f["filing_date"])
    print(f"{len(filings)} Form 4 filings in range, {len(filings) - len(todo)} already in DB, "
          f"{len(todo)} to fetch")

    ok = unstructured = missing = 0
    for i, f in enumerate(todo, 1):
        acc_nodash = f["accession"].replace("-", "")
        doc = f["doc"] if f["doc"].lower().endswith(".xml") else f"{f['accession']}.txt"
        url = config.ARCHIVE_URL.format(cik=cik, acc_nodash=acc_nodash, doc=doc)
        text = sec_client.get_text_cached(url, config.XML_CACHE / str(cik) / f"{acc_nodash}_{doc}")
        if text is None:
            missing += 1
            continue
        parsed = form4_parser.parse(text, f["accession"], f["filing_date"])
        if parsed is None:
            unstructured += 1          # pre-XML plain-text filing
            continue
        insert_rows(con, parsed, quarter_of(f["filing_date"]))
        ok += 1
        if i % 50 == 0:
            print(f"  {i}/{len(todo)} ...")
    create_views(con)
    con.close()
    print(f"Done: {ok} parsed and stored, {unstructured} plain-text (no XML), {missing} not found")


if __name__ == "__main__":
    main()
