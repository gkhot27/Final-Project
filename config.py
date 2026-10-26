"""Shared settings for the Insider Trade Reconstruction pipeline."""
import os
from pathlib import Path

# ---------------------------------------------------------------------------
# REQUIRED: the SEC blocks requests without a real contact in the User-Agent.
# Either edit the default below or run:  export SEC_USER_AGENT="Your Name you@email.com"
# ---------------------------------------------------------------------------
USER_AGENT = os.environ.get("SEC_USER_AGENT", "CHANGE ME your-email@example.com")

ROOT = Path(__file__).resolve().parent
DATA_DIR = Path(os.environ.get("INSIDER_DATA_DIR", ROOT / "data"))
ZIP_DIR = DATA_DIR / "bulk_zips"          # quarterly zips from SEC
XML_CACHE = DATA_DIR / "xml_cache"        # individual Form 4 XML files
JSON_CACHE = DATA_DIR / "json_cache"      # API responses
DB_PATH = DATA_DIR / "insider.duckdb"
OUTPUT_DIR = Path(os.environ.get("INSIDER_OUTPUT_DIR", ROOT / "output"))

BULK_URL = ("https://www.sec.gov/files/structureddata/data/"
            "insider-transactions-data-sets/{q}_form345.zip")
TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik:010d}.json"
SUBMISSIONS_PAGE_URL = "https://data.sec.gov/submissions/{name}"
ARCHIVE_URL = "https://www.sec.gov/Archives/edgar/data/{cik}/{acc_nodash}/{doc}"
FTS_URL = "https://efts.sec.gov/LATEST/search-index"

FIRST_BULK_QUARTER = "2006q1"
MAX_REQUESTS_PER_SEC = 8                  # SEC limit is 10/s; stay under it

# Tables we keep from each quarterly zip (OWNER_SIGNATURE is skipped).
BULK_TABLES = ["SUBMISSION", "REPORTINGOWNER", "NONDERIV_TRANS", "NONDERIV_HOLDING",
               "DERIV_TRANS", "DERIV_HOLDING", "FOOTNOTES"]

# Sarbanes-Oxley: 2-business-day Form 4 deadline applies to trades on/after this date.
SOX_EFFECTIVE = "2002-08-29"

for d in (DATA_DIR, ZIP_DIR, XML_CACHE, JSON_CACHE, OUTPUT_DIR):
    d.mkdir(parents=True, exist_ok=True)
