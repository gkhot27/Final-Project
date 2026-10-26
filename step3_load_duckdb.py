"""Step 3: load every downloaded quarterly zip into a local DuckDB database.

Usage:  python step3_load_duckdb.py
        python step3_load_duckdb.py --reload 2025q4    # force one quarter to reload

Raw tables keep every column as text (exactly as the SEC published it) plus
`quarter` and `source` ('bulk' or 'api'). Typed views (v_*) sit on top.
Quarters already loaded are skipped, so re-running is cheap.
"""
import argparse
import shutil
import tempfile
import zipfile
from pathlib import Path

import duckdb

import config


def connect():
    return duckdb.connect(str(config.DB_PATH))


def _table_exists(con, name):
    return con.execute("SELECT count(*) FROM information_schema.tables "
                       "WHERE lower(table_name)=lower(?)", [name]).fetchone()[0] > 0


def _columns(con, name):
    return {r[0].upper() for r in con.execute(f"DESCRIBE {name}").fetchall()}


def lit(v):
    """SQL string literal (views can't use bound parameters)."""
    return "'" + str(v).replace("'", "''") + "'"


def append_relation(con, table, select_sql):
    """Create `table` from the SELECT, or append to it BY NAME, adding any new columns
    first (the SEC added columns over the years, e.g. AFF10B5ONE in 2023)."""
    con.execute(f"CREATE OR REPLACE TEMP VIEW _incoming AS {select_sql}")
    if not _table_exists(con, table):
        con.execute(f"CREATE TABLE {table} AS SELECT * FROM _incoming")
        return
    existing = _columns(con, table)
    for col in _columns(con, "_incoming") - existing:
        con.execute(f'ALTER TABLE {table} ADD COLUMN "{col}" VARCHAR')
    con.execute(f"INSERT INTO {table} BY NAME SELECT * FROM _incoming")


def load_quarter(con, zip_path: Path, quarter: str):
    tmp = Path(tempfile.mkdtemp(prefix=f"f345_{quarter}_"))
    try:
        with zipfile.ZipFile(zip_path) as z:
            members = {Path(n).stem.upper(): n for n in z.namelist() if n.lower().endswith(".tsv")}
            for table in config.BULK_TABLES:
                if table not in members:
                    print(f"   {table}: not in zip, skipped")
                    continue
                out = tmp / f"{table}.tsv"
                with z.open(members[table]) as src, open(out, "wb") as dst:
                    shutil.copyfileobj(src, dst)
                sql = (f"SELECT *, {lit(quarter)} AS quarter, 'bulk' AS source "
                       f"FROM read_csv({lit(out)}, delim='\t', "
                       f"header=true, all_varchar=true, quote='', escape='', "
                       f"null_padding=true, ignore_errors=true, max_line_size=10000000)")
                append_relation(con, table.lower(), sql)
                n = con.execute(f"SELECT count(*) FROM {table.lower()} WHERE quarter=?",
                                [quarter]).fetchone()[0]
                print(f"   {table}: {n:,} rows")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


VIEW_COLUMNS = {
    "submission": ["ACCESSION_NUMBER", "FILING_DATE", "PERIOD_OF_REPORT", "DOCUMENT_TYPE",
                   "ISSUERCIK", "ISSUERNAME", "ISSUERTRADINGSYMBOL", "quarter", "source"],
    "reportingowner": ["ACCESSION_NUMBER", "RPTOWNERCIK", "RPTOWNERNAME",
                       "RPTOWNER_RELATIONSHIP", "RPTOWNER_TITLE"],
    "nonderiv_trans": ["ACCESSION_NUMBER", "NONDERIV_TRANS_SK", "SECURITY_TITLE",
                       "SECURITY_TITLE_FN", "TRANS_DATE", "TRANS_DATE_FN", "DEEMED_EXECUTION_DATE",
                       "TRANS_CODE", "TRANS_TIMELINESS", "TRANS_SHARES", "TRANS_SHARES_FN",
                       "TRANS_PRICEPERSHARE", "TRANS_PRICEPERSHARE_FN", "TRANS_ACQUIRED_DISP_CD",
                       "SHRS_OWND_FOLWNG_TRANS", "SHRS_OWND_FOLWNG_TRANS_FN",
                       "DIRECT_INDIRECT_OWNERSHIP", "NATURE_OF_OWNERSHIP", "NATURE_OF_OWNERSHIP_FN"],
    "nonderiv_holding": ["ACCESSION_NUMBER", "NONDERIV_HOLDING_SK", "SECURITY_TITLE",
                         "SECURITY_TITLE_FN", "SHRS_OWND_FOLWNG_TRANS", "SHRS_OWND_FOLWNG_TRANS_FN",
                         "DIRECT_INDIRECT_OWNERSHIP", "NATURE_OF_OWNERSHIP", "NATURE_OF_OWNERSHIP_FN"],
    "footnotes": ["ACCESSION_NUMBER", "FOOTNOTE_ID", "FOOTNOTE_TXT"],
}


def create_views(con):
    """Typed views. Bulk dates look like '22-MAY-2034'."""
    for t, cols in VIEW_COLUMNS.items():          # tolerate tables missing a column
        if _table_exists(con, t):
            have = _columns(con, t)
            for c in cols:
                if c.upper() not in have:
                    con.execute(f'ALTER TABLE {t} ADD COLUMN "{c}" VARCHAR')
    d = lambda c: f"try_strptime({c}, '%d-%b-%Y')::DATE"
    num = lambda c: f"try_cast(replace({c}, ',', '') AS DOUBLE)"
    if _table_exists(con, "submission"):
        con.execute(f"""CREATE OR REPLACE VIEW v_submission AS SELECT
            ACCESSION_NUMBER AS accession, {d('FILING_DATE')} AS filing_date,
            {d('PERIOD_OF_REPORT')} AS period_of_report, DOCUMENT_TYPE AS form_type,
            try_cast(ISSUERCIK AS BIGINT) AS issuer_cik, ISSUERNAME AS issuer_name,
            upper(trim(ISSUERTRADINGSYMBOL)) AS ticker, quarter, source
            FROM submission""")
    if _table_exists(con, "reportingowner"):
        con.execute("""CREATE OR REPLACE VIEW v_owner AS SELECT DISTINCT
            ACCESSION_NUMBER AS accession, try_cast(RPTOWNERCIK AS BIGINT) AS owner_cik,
            RPTOWNERNAME AS owner_name, RPTOWNER_RELATIONSHIP AS relationship,
            RPTOWNER_TITLE AS title FROM reportingowner""")
    if _table_exists(con, "nonderiv_trans"):
        con.execute(f"""CREATE OR REPLACE VIEW v_trans AS SELECT
            ACCESSION_NUMBER AS accession, try_cast(NONDERIV_TRANS_SK AS BIGINT) AS row_sk,
            SECURITY_TITLE AS security_title, {d('TRANS_DATE')} AS trans_date,
            {d('DEEMED_EXECUTION_DATE')} AS deemed_date, TRANS_CODE AS trans_code,
            TRANS_TIMELINESS AS timeliness, {num('TRANS_SHARES')} AS shares,
            {num('TRANS_PRICEPERSHARE')} AS price, upper(TRANS_ACQUIRED_DISP_CD) AS acq_disp,
            {num('SHRS_OWND_FOLWNG_TRANS')} AS shares_after,
            upper(DIRECT_INDIRECT_OWNERSHIP) AS direct_indirect,
            NATURE_OF_OWNERSHIP AS nature,
            concat_ws(',', SECURITY_TITLE_FN, TRANS_DATE_FN, TRANS_SHARES_FN,
                      TRANS_PRICEPERSHARE_FN, SHRS_OWND_FOLWNG_TRANS_FN,
                      NATURE_OF_OWNERSHIP_FN) AS footnote_ids
            FROM nonderiv_trans""")
    if _table_exists(con, "nonderiv_holding"):
        con.execute(f"""CREATE OR REPLACE VIEW v_holding AS SELECT
            ACCESSION_NUMBER AS accession, try_cast(NONDERIV_HOLDING_SK AS BIGINT) AS row_sk,
            SECURITY_TITLE AS security_title, {num('SHRS_OWND_FOLWNG_TRANS')} AS shares_after,
            upper(DIRECT_INDIRECT_OWNERSHIP) AS direct_indirect, NATURE_OF_OWNERSHIP AS nature,
            concat_ws(',', SECURITY_TITLE_FN, SHRS_OWND_FOLWNG_TRANS_FN,
                      NATURE_OF_OWNERSHIP_FN) AS footnote_ids
            FROM nonderiv_holding""")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--reload", help="quarter to force-reload, e.g. 2025q4")
    a = ap.parse_args()
    con = connect()
    con.execute("CREATE TABLE IF NOT EXISTS _loaded_quarters (quarter VARCHAR PRIMARY KEY, "
                "loaded_at TIMESTAMP DEFAULT current_timestamp)")
    if a.reload:
        for t in config.BULK_TABLES:
            if _table_exists(con, t.lower()):
                con.execute(f"DELETE FROM {t.lower()} WHERE quarter=?", [a.reload])
        con.execute("DELETE FROM _loaded_quarters WHERE quarter=?", [a.reload])

    done = {r[0] for r in con.execute("SELECT quarter FROM _loaded_quarters").fetchall()}
    zips = sorted(config.ZIP_DIR.glob("*_form345.zip"))
    if not zips:
        print("No zips found - run step2_download_bulk.py first.")
    for z in zips:
        q = z.name.split("_")[0]
        if q in done:
            continue
        print(f"Loading {q} ...")
        con.execute("BEGIN")
        try:
            load_quarter(con, z, q)
            con.execute("INSERT INTO _loaded_quarters (quarter) VALUES (?)", [q])
            con.execute("COMMIT")
        except Exception:
            con.execute("ROLLBACK")
            raise
    create_views(con)
    if _table_exists(con, "submission"):
        n, lo, hi = con.execute("SELECT count(*), min(filing_date), max(filing_date) "
                                "FROM v_submission").fetchone()
        print(f"\nDatabase: {config.DB_PATH}\n{n:,} filings, {lo} -> {hi}")
    con.close()


if __name__ == "__main__":
    main()
