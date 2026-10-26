"""Step 5: rebuild each insider's share timeline, check it against the totals they
reported, explain the mismatches, and flag late Form 4s.

Usage:  python step5_reconcile.py AAPL          (or a CIK: python step5_reconcile.py 320193)

Writes to output/<TICKER>/:
  timeline.csv       every event with running balance, reported total, status
  mismatches.csv     rows where our running total != sharesOwnedFollowingTransaction
  late_filings.csv   Form 4s filed after the deadline
  summary.txt        headline numbers
"""
import re
import sys

import numpy as np
import pandas as pd
from pandas.tseries.holiday import USFederalHolidayCalendar
from pandas.tseries.offsets import CustomBusinessDay

import config
from step3_load_duckdb import connect, create_views

BDAY = CustomBusinessDay(calendar=USFederalHolidayCalendar())

# ---------------------------------------------------------------- helpers

def norm_security(title):
    """Collapse 'Common Stock', 'COMMON', '$.01 PV Com Stk', ... into one key, but keep
    share classes apart (Class A vs Class B are different balances)."""
    s = re.sub(r"[^a-z0-9 ]", " ", str(title or "").lower())
    s = " ".join(s.split())
    m = re.search(r"\bclass ([a-z])\b", s) or re.search(r"\bseries ([a-z0-9]+)\b", s)
    cls = f" class {m.group(1)}" if m else ""
    if any(k in s for k in ("common", "com stk", "ordinary", "ord shs")) or s in ("stock", "shares"):
        return "common" + cls
    return s or "unknown"


def norm_nature(direct_indirect, nature):
    if (direct_indirect or "D") == "D":
        return "direct"
    n = " ".join(re.sub(r"[^a-z0-9 ]", " ", str(nature or "").lower()).split())
    return "indirect: " + (n or "unspecified")


SPLIT_RE = re.compile(r"\bsplit|stock dividend|reverse split", re.I)
ACCRUAL_RE = re.compile(r"reinvest|drip|401\(?k\)?|espp|employee stock purchase|dividend equivalent", re.I)
TRANSFER_RE = re.compile(r"transfer|gift|distribut|contribut|trust|spouse|reclass", re.I)


def classify(row, fn_text, expected):
    rep, diff = row["shares_after"], row["diff"]
    if str(row["form_type"]).endswith("/A"):
        return "amendment (re-filed earlier report)"
    if SPLIT_RE.search(fn_text):
        return "stock split (in footnote)"
    if expected and expected > 0:
        ratio = rep / expected
        for r in (2, 3, 4, 5, 10, 1.5, 0.5, 1 / 3, 0.25, 0.1, 20, 1 / 20):
            if abs(ratio - r) < 0.002 * r:
                return f"stock split (inferred ratio {ratio:.3g}:1)"
    if abs(diff) < 1:
        return "rounding / fractional shares"
    if ACCRUAL_RE.search(fn_text):
        return "unreported accrual (DRIP / 401k / ESPP)"
    if TRANSFER_RE.search(fn_text):
        return "transfer between accounts / trust (see footnote)"
    if row.get("gap_days", 0) and row["gap_days"] > 400:
        return "long gap - likely missing filing (Form 5, pre-2003, or not loaded)"
    return "unexplained - inspect filing XML"


# ---------------------------------------------------------------- data

def resolve_cik(con, arg):
    if arg.isdigit():
        return int(arg), arg
    r = con.execute("SELECT issuer_cik FROM v_submission WHERE ticker=? GROUP BY 1 "
                    "ORDER BY max(filing_date) DESC LIMIT 1", [arg.upper()]).fetchone()
    if r:
        return int(r[0]), arg.upper()
    from step1_lookup_cik import lookup            # needs network
    cik, _, t = lookup(arg)
    return cik, t or arg.upper()


def load_events(con, cik):
    q = """
    WITH s AS (SELECT * FROM v_submission WHERE issuer_cik = ?)
    SELECT 'trans' AS event_type, s.accession, s.filing_date, s.form_type, s.source,
           o.owner_cik, o.owner_name, o.relationship, o.title,
           coalesce(t.trans_date, s.period_of_report) AS event_date, t.deemed_date,
           t.row_sk, t.security_title, t.direct_indirect, t.nature, t.trans_code,
           t.timeliness, t.acq_disp, t.shares, t.price, t.shares_after, t.footnote_ids
    FROM s JOIN v_trans t USING (accession) JOIN v_owner o USING (accession)
    UNION ALL
    SELECT 'holding', s.accession, s.filing_date, s.form_type, s.source,
           o.owner_cik, o.owner_name, o.relationship, o.title,
           coalesce(s.period_of_report, s.filing_date), NULL,
           h.row_sk, h.security_title, h.direct_indirect, h.nature, NULL,
           NULL, NULL, NULL, NULL, h.shares_after, h.footnote_ids
    FROM s JOIN v_holding h USING (accession) JOIN v_owner o USING (accession)
    """
    return con.execute(q, [cik]).df()


def load_footnotes(con, accessions):
    if not accessions:
        return {}
    con.register("acc_list", pd.DataFrame({"a": list(accessions)}))
    rows = con.execute("SELECT ACCESSION_NUMBER, FOOTNOTE_ID, FOOTNOTE_TXT FROM footnotes "
                       "WHERE ACCESSION_NUMBER IN (SELECT a FROM acc_list)").fetchall()
    return {(a, f): t or "" for a, f, t in rows}


# ---------------------------------------------------------------- reconciliation

def reconcile(ev, footnotes):
    ev = ev.copy()
    ev["security_key"] = ev["security_title"].map(norm_security)
    ev["ownership_key"] = [norm_nature(d, n) for d, n in zip(ev["direct_indirect"], ev["nature"])]
    sign = ev["acq_disp"].map({"A": 1.0, "D": -1.0})
    ev["delta"] = ev["shares"] * sign
    ev["is_amendment"] = ev["form_type"].astype(str).str.endswith("/A")
    ev = ev.sort_values(["owner_cik", "security_key", "ownership_key", "event_date",
                         "filing_date", "accession", "event_type", "row_sk"],
                        kind="mergesort").reset_index(drop=True)

    expected_col, balance_col, status_col, diff_col, gap_col = [], [], [], [], []
    for _, g in ev.groupby(["owner_cik", "security_key", "ownership_key"], sort=False):
        bal, last_date = None, None
        for _, r in g.iterrows():
            rep, delta, exp, status = r["shares_after"], r["delta"], np.nan, ""
            gap = (r["event_date"] - last_date).days if (last_date is not None and
                                                          pd.notna(r["event_date"])) else 0
            if r["event_type"] == "holding":
                if pd.isna(rep):
                    status = "holding (no total)"
                elif bal is not None and abs(rep - bal) > max(0.01, 1e-6 * abs(rep)):
                    exp, status = bal, "MISMATCH"
                else:
                    status = "baseline (holding)" if bal is None else "match"
                    exp = rep if bal is None else bal
                bal = rep if pd.notna(rep) else bal
            else:
                if pd.isna(delta):
                    status = "no A/D code or shares"
                    bal = rep if pd.notna(rep) else bal
                elif pd.isna(rep):
                    status = "no reported total"
                    bal = None if bal is None else bal + delta
                elif bal is None:
                    status, exp = "opening (baseline inferred)", rep
                    bal = rep
                else:
                    exp = bal + delta
                    status = "match" if abs(rep - exp) <= max(0.01, 1e-6 * abs(rep)) else "MISMATCH"
                    bal = rep                          # re-anchor so one miss doesn't cascade
            expected_col.append(exp)
            balance_col.append(bal)
            status_col.append(status)
            diff_col.append(rep - exp if pd.notna(rep) and pd.notna(exp) else np.nan)
            gap_col.append(gap)
            if pd.notna(r["event_date"]):
                last_date = r["event_date"]
    ev["expected_after"], ev["running_balance"] = expected_col, balance_col
    ev["status"], ev["diff"], ev["gap_days"] = status_col, diff_col, gap_col

    reasons, texts = [], []
    for _, r in ev.iterrows():
        ids = [i for i in str(r["footnote_ids"] or "").replace(" ", "").split(",") if i]
        txt = " | ".join(footnotes.get((r["accession"], i), "") for i in ids)
        texts.append(txt)
        reasons.append(classify(r, txt, r["expected_after"]) if r["status"] == "MISMATCH" else "")
    ev["mismatch_reason"], ev["footnote_text"] = reasons, texts
    return ev


# ---------------------------------------------------------------- late filings

def deadline(trade_date):
    if trade_date >= pd.Timestamp(config.SOX_EFFECTIVE):
        return trade_date + 2 * BDAY
    # pre-SOX: 10th day of the month after the trade
    nxt = (trade_date + pd.offsets.MonthBegin(1)).normalize()
    return nxt + pd.Timedelta(days=9)


def late_filings(ev):
    t = ev[(ev["event_type"] == "trans") & (ev["form_type"] == "4")].copy()
    if t.empty:
        return t
    t["trade_date"] = t["deemed_date"].fillna(t["event_date"])
    t = t.dropna(subset=["trade_date", "filing_date"])
    t = (t.sort_values("trade_date")
          .groupby("accession", as_index=False)
          .agg(owner_name=("owner_name", "first"), owner_cik=("owner_cik", "first"),
               filing_date=("filing_date", "first"), earliest_trade=("trade_date", "first"),
               trans_codes=("trans_code", lambda s: ",".join(sorted(set(map(str, s))))),
               self_reported_late=("timeliness", lambda s: (s == "L").any())))
    t["deadline"] = [deadline(pd.Timestamp(d)) for d in t["earliest_trade"]]
    t = t[pd.to_datetime(t["filing_date"]) > t["deadline"]].copy()
    t["business_days_late"] = [len(pd.bdate_range(dl + pd.Timedelta(days=1), fd, freq=BDAY))
                               for dl, fd in zip(t["deadline"], pd.to_datetime(t["filing_date"]))]
    gift_pre2023 = t["trans_codes"].str.contains("G") & (t["earliest_trade"] < pd.Timestamp("2023-02-27"))
    t["note"] = np.where(gift_pre2023, "gift: Form 5 deferral allowed before 2023-02-27", "")
    return t.sort_values("business_days_late", ascending=False)


# ---------------------------------------------------------------- main

def main(arg):
    con = connect()
    create_views(con)
    cik, label = resolve_cik(con, arg)
    ev = load_events(con, cik)
    if ev.empty:
        sys.exit(f"No Form 3/4/5 rows for CIK {cik}. Load bulk data (steps 2-3) or run step 4.")
    for c in ("event_date", "deemed_date", "filing_date"):
        ev[c] = pd.to_datetime(ev[c])
    fns = load_footnotes(con, set(ev["accession"]))
    con.close()

    tl = reconcile(ev, fns)
    late = late_filings(tl)
    out = config.OUTPUT_DIR / label
    out.mkdir(parents=True, exist_ok=True)
    cols = ["owner_name", "owner_cik", "title", "security_key", "ownership_key", "event_date",
            "filing_date", "form_type", "event_type", "trans_code", "acq_disp", "shares", "price",
            "delta", "expected_after", "shares_after", "diff", "running_balance", "status",
            "mismatch_reason", "footnote_text", "accession", "source", "security_title"]
    tl[cols].to_csv(out / "timeline.csv", index=False)
    mm = tl[tl["status"] == "MISMATCH"]
    mm[cols].to_csv(out / "mismatches.csv", index=False)
    late.to_csv(out / "late_filings.csv", index=False)

    checked = tl["status"].isin(["match", "MISMATCH"]).sum()
    matched = (tl["status"] == "match").sum()
    lines = [
        f"{label} (CIK {cik})",
        f"Filings: {tl['accession'].nunique():,}   insiders: {tl['owner_cik'].nunique():,}   "
        f"events: {len(tl):,}   ({tl['event_date'].min():%Y-%m-%d} -> {tl['event_date'].max():%Y-%m-%d})",
        f"Reconciliation: {matched:,}/{checked:,} checked rows match "
        f"({(matched / checked * 100 if checked else 0):.1f}%)",
        "Mismatch reasons:",
        *(f"  {n:5d}  {r}" for r, n in mm["mismatch_reason"].value_counts().items()),
        f"Late Form 4 filings: {len(late):,}",
        f"Output folder: {out}",
    ]
    (out / "summary.txt").write_text("\n".join(lines) + "\n")
    print("\n".join(lines))


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    main(sys.argv[1])
