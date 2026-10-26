"""Offline self-test: builds a fake quarter with known problems (a stock split, a late
filing, a DRIP gap, an indirect trust account), plus one API-style XML filing, then runs
steps 3 and 5 and checks the answers. Needs no internet.

Usage:  python selftest.py
"""
import os
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent

SUB_COLS = ["ACCESSION_NUMBER", "FILING_DATE", "PERIOD_OF_REPORT", "DATE_OF_ORIG_SUB",
            "NO_SECURITIES_OWNED", "NOT_SUBJECT_SEC16", "FORM3_HOLDINGS_REPORTED",
            "FORM4_TRANS_REPORTED", "DOCUMENT_TYPE", "ISSUERCIK", "ISSUERNAME",
            "ISSUERTRADINGSYMBOL", "REMARKS"]
OWN_COLS = ["ACCESSION_NUMBER", "RPTOWNERCIK", "RPTOWNERNAME", "RPTOWNER_RELATIONSHIP",
            "RPTOWNER_TITLE", "RPTOWNER_TXT"]
TR_COLS = ["ACCESSION_NUMBER", "NONDERIV_TRANS_SK", "SECURITY_TITLE", "SECURITY_TITLE_FN",
           "TRANS_DATE", "TRANS_DATE_FN", "DEEMED_EXECUTION_DATE", "DEEMED_EXECUTION_DATE_FN",
           "TRANS_FORM_TYPE", "TRANS_FORM_TYPE_FN", "TRANS_CODE", "EQUITY_SWAP_INVOLVED",
           "EQUITY_SWAP_TRANS_CD_FN", "TRANS_TIMELINESS", "TRANS_TIMELINESS_FN", "TRANS_SHARES",
           "TRANS_SHARES_FN", "TRANS_PRICEPERSHARE", "TRANS_PRICEPERSHARE_FN",
           "TRANS_ACQUIRED_DISP_CD", "TRANS_ACQUIRED_DISP_CD_FN", "SHRS_OWND_FOLWNG_TRANS",
           "SHRS_OWND_FOLWNG_TRANS_FN", "VALU_OWND_FOLWNG_TRANS", "VALU_OWND_FOLWNG_TRANS_FN",
           "DIRECT_INDIRECT_OWNERSHIP", "DIRECT_INDIRECT_OWNERSHIP_FN", "NATURE_OF_OWNERSHIP",
           "NATURE_OF_OWNERSHIP_FN"]
HO_COLS = ["ACCESSION_NUMBER", "NONDERIV_HOLDING_SK", "SECURITY_TITLE", "SECURITY_TITLE_FN",
           "TRANS_FORM_TYPE", "TRANS_FORM_TYPE_FN", "SHRS_OWND_FOLWNG_TRANS",
           "SHRS_OWND_FOLWNG_TRANS_FN", "VALU_OWND_FOLWNG_TRANS", "VALU_OWND_FOLWNG_TRANS_FN",
           "DIRECT_INDIRECT_OWNERSHIP", "DIRECT_INDIRECT_OWNERSHIP_FN", "NATURE_OF_OWNERSHIP",
           "NATURE_OF_OWNERSHIP_FN"]
FN_COLS = ["ACCESSION_NUMBER", "FOOTNOTE_ID", "FOOTNOTE_TXT"]

CEO, DIR = ("111", "DOE JANE", "Officer", "CEO"), ("222", "ROE RICHARD", "Director", "")


def sub(acc, filed, period, form):
    return dict(ACCESSION_NUMBER=acc, FILING_DATE=filed, PERIOD_OF_REPORT=period,
                DOCUMENT_TYPE=form, ISSUERCIK="999999", ISSUERNAME="TEST CORP",
                ISSUERTRADINGSYMBOL="TST")


def own(acc, o):
    return dict(ACCESSION_NUMBER=acc, RPTOWNERCIK=o[0], RPTOWNERNAME=o[1],
                RPTOWNER_RELATIONSHIP=o[2], RPTOWNER_TITLE=o[3])


def tr(acc, sk, date, code, shares, ad, after, title="Common Stock", di="D", nature="", fn=""):
    return dict(ACCESSION_NUMBER=acc, NONDERIV_TRANS_SK=str(sk), SECURITY_TITLE=title,
                TRANS_DATE=date, TRANS_FORM_TYPE="4", TRANS_CODE=code, TRANS_SHARES=str(shares),
                TRANS_PRICEPERSHARE="10.00", TRANS_ACQUIRED_DISP_CD=ad,
                SHRS_OWND_FOLWNG_TRANS=str(after), SHRS_OWND_FOLWNG_TRANS_FN=fn,
                DIRECT_INDIRECT_OWNERSHIP=di, NATURE_OF_OWNERSHIP=nature)


def tsv(cols, rows):
    return "\t".join(cols) + "\n" + "".join(
        "\t".join(r.get(c, "") or "" for c in cols) + "\n" for r in rows)


def build_zip(path):
    subs = [sub("A3", "03-JAN-2024", "02-JAN-2024", "3"),
            sub("A", "04-MAR-2024", "01-MAR-2024", "4"),        # Fri trade -> Tue deadline: on time
            sub("B", "10-MAY-2024", "01-MAY-2024", "4"),        # Wed trade -> Fri deadline: LATE
            sub("C", "02-JUL-2024", "01-JUL-2024", "4"),        # split explained in footnote
            sub("D", "02-AUG-2024", "01-AUG-2024", "4"),
            sub("E", "04-JUN-2024", "03-JUN-2024", "4"),
            sub("F", "04-SEP-2024", "03-SEP-2024", "4")]        # DRIP shares never reported
    owners = [own("A3", CEO), own("A", CEO), own("B", CEO), own("C", CEO), own("D", CEO),
              own("E", DIR), own("F", DIR)]
    trans = [tr("A", 1, "01-MAR-2024", "P", 1000, "A", 11000),
             tr("B", 2, "01-MAY-2024", "S", 500, "D", 10500),
             tr("C", 3, "01-JUL-2024", "P", 100, "A", 21200, fn="F1"),
             tr("D", 4, "01-AUG-2024", "S", 200, "D", 21000, title="COMMON STOCK"),
             tr("D", 5, "01-AUG-2024", "P", 300, "A", 5300, di="I", nature="By Trust"),
             tr("E", 6, "03-JUN-2024", "P", 50, "A", 1050, title="$.01 PV Com Stk"),
             tr("F", 7, "03-SEP-2024", "P", 50, "A", 1150, fn="F1")]
    holds = [dict(ACCESSION_NUMBER="A3", NONDERIV_HOLDING_SK="1", SECURITY_TITLE="Common Stock",
                  SHRS_OWND_FOLWNG_TRANS="10000", DIRECT_INDIRECT_OWNERSHIP="D")]
    fns = [dict(ACCESSION_NUMBER="C", FOOTNOTE_ID="F1",
                FOOTNOTE_TXT="Reflects the 2-for-1 stock split effective June 15, 2024."),
           dict(ACCESSION_NUMBER="F", FOOTNOTE_ID="F1",
                FOOTNOTE_TXT="Includes 50 shares acquired under the dividend reinvestment plan.")]
    deriv = HERE / "sample_data" / "DERIV_HOLDING_sample.tsv"
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("SUBMISSION.tsv", tsv(SUB_COLS, subs))
        z.writestr("REPORTINGOWNER.tsv", tsv(OWN_COLS, owners))
        z.writestr("NONDERIV_TRANS.tsv", tsv(TR_COLS, trans))
        z.writestr("NONDERIV_HOLDING.tsv", tsv(HO_COLS, holds))
        z.writestr("FOOTNOTES.tsv", tsv(FN_COLS, fns))
        if deriv.exists():                 # real SEC sample, if present next to the project
            z.write(deriv, "DERIV_HOLDING.tsv")


XML = """<?xml version="1.0"?>
<ownershipDocument>
  <documentType>4</documentType><periodOfReport>2024-10-01</periodOfReport>
  <issuer><issuerCik>0000999999</issuerCik><issuerName>TEST CORP</issuerName>
          <issuerTradingSymbol>TST</issuerTradingSymbol></issuer>
  <reportingOwner><reportingOwnerId><rptOwnerCik>0000000111</rptOwnerCik>
      <rptOwnerName>DOE JANE</rptOwnerName></reportingOwnerId>
    <reportingOwnerRelationship><isOfficer>1</isOfficer><officerTitle>CEO</officerTitle>
    </reportingOwnerRelationship></reportingOwner>
  <nonDerivativeTable><nonDerivativeTransaction>
    <securityTitle><value>Common Stock</value></securityTitle>
    <transactionDate><value>2024-10-01</value></transactionDate>
    <transactionCoding><transactionFormType>4</transactionFormType>
      <transactionCode>S</transactionCode></transactionCoding>
    <transactionAmounts><transactionShares><value>1000</value></transactionShares>
      <transactionPricePerShare><value>12.5</value><footnoteId id="F1"/></transactionPricePerShare>
      <transactionAcquiredDisposedCode><value>D</value></transactionAcquiredDisposedCode>
    </transactionAmounts>
    <postTransactionAmounts><sharesOwnedFollowingTransaction><value>20000</value>
    </sharesOwnedFollowingTransaction></postTransactionAmounts>
    <ownershipNature><directOrIndirectOwnership><value>D</value></directOrIndirectOwnership>
    </ownershipNature></nonDerivativeTransaction></nonDerivativeTable>
  <footnotes><footnote id="F1">Weighted average price.</footnote></footnotes>
</ownershipDocument>"""


def main():
    tmp = Path(tempfile.mkdtemp(prefix="insider_selftest_"))
    env = dict(os.environ, INSIDER_DATA_DIR=str(tmp / "data"), INSIDER_OUTPUT_DIR=str(tmp / "out"))
    (tmp / "data" / "bulk_zips").mkdir(parents=True)
    build_zip(tmp / "data" / "bulk_zips" / "2024q3_form345.zip")

    def run(*args):
        r = subprocess.run([sys.executable, *args], cwd=HERE, env=env, text=True, capture_output=True)
        print(r.stdout, r.stderr, sep="")
        if r.returncode:
            sys.exit("FAILED")

    run("step3_load_duckdb.py")
    # API path: parse an XML filing and insert it exactly as step 4 does
    run("-c", "import form4_parser, step4_backfill_api as s4, step3_load_duckdb as s3\n"
              f"p = form4_parser.parse({XML!r}, 'G', '2024-10-02')\n"
              "con = s3.connect(); s4.insert_rows(con, p, '2024q4'); s3.create_views(con); con.close()\n"
              "print('API XML parsed:', {k: len(v) for k, v in p.items()})")
    run("step5_reconcile.py", "TST")

    import pandas as pd
    out = tmp / "out" / "TST"
    tl, mm, late = (pd.read_csv(out / f) for f in ("timeline.csv", "mismatches.csv", "late_filings.csv"))
    checks = {
        "9 events reconstructed": len(tl) == 9,
        "split caught with footnote reason": (mm["accession"].eq("C") & mm["mismatch_reason"].str.contains("split")).any(),
        "DRIP gap explained": (mm["accession"].eq("F") & mm["mismatch_reason"].str.contains("DRIP")).any(),
        "only 2 mismatches": len(mm) == 2,
        "API filing reconciles": tl.loc[tl["accession"].eq("G"), "status"].eq("match").all(),
        "late filing B flagged": late["accession"].tolist() == ["B"],
        "Common/COMMON/$.01 PV Com Stk unified": set(tl["security_key"]) == {"common"},
        "trust account kept separate": tl["ownership_key"].str.startswith("indirect").sum() == 1,
    }
    print("\nSELF-TEST")
    for k, v in checks.items():
        print(f"  {'PASS' if v else 'FAIL'}  {k}")
    print(f"\n(outputs in {out})")
    sys.exit(0 if all(checks.values()) else 1)


if __name__ == "__main__":
    main()
