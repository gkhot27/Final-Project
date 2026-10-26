"""Parse one Form 3/4/5 XML (ownershipDocument) into rows shaped exactly like the
bulk data set tables, so API-fetched filings and bulk filings are interchangeable."""
import datetime as dt
import re
import xml.etree.ElementTree as ET

_DOC_RE = re.compile(r"<ownershipDocument.*?</ownershipDocument>", re.S)


def _bulk_date(s):
    """'2026-05-22' -> '22-MAY-2026' (bulk format)."""
    if not s:
        return None
    try:
        return dt.date.fromisoformat(s.strip()[:10]).strftime("%d-%b-%Y").upper()
    except ValueError:
        return None


def _txt(el, path):
    """Text at path; handles the <x><value>..</value></x> wrapping Form 4 uses."""
    if el is None:
        return None
    node = el.find(path)
    if node is None:
        return None
    v = node.find("value")
    t = (v.text if v is not None else node.text) or ""
    t = t.strip()
    return t or None


def _fn(el, path):
    """Comma-joined footnote ids attached to the element at path."""
    node = el.find(path) if el is not None else None
    if node is None:
        return None
    ids = [f.get("id") for f in node.iter("footnoteId") if f.get("id")]
    return ",".join(ids) or None


def extract_xml(text):
    m = _DOC_RE.search(text or "")
    return m.group(0) if m else None


def parse(text, accession, filing_date_iso=None):
    """Return {TABLE_NAME: [row dicts]} or None if the text has no ownershipDocument
    (e.g. pre-2003 plain-text filings)."""
    xml = extract_xml(text)
    if xml is None:
        return None
    root = ET.fromstring(xml)
    out = {k: [] for k in ("SUBMISSION", "REPORTINGOWNER", "NONDERIV_TRANS",
                           "NONDERIV_HOLDING", "FOOTNOTES")}
    issuer = root.find("issuer")
    out["SUBMISSION"].append({
        "ACCESSION_NUMBER": accession,
        "FILING_DATE": _bulk_date(filing_date_iso),
        "PERIOD_OF_REPORT": _bulk_date(_txt(root, "periodOfReport")),
        "DOCUMENT_TYPE": _txt(root, "documentType"),
        "ISSUERCIK": (_txt(issuer, "issuerCik") or "").lstrip("0") or None,
        "ISSUERNAME": _txt(issuer, "issuerName"),
        "ISSUERTRADINGSYMBOL": _txt(issuer, "issuerTradingSymbol"),
        "REMARKS": _txt(root, "remarks"),
    })
    for ro in root.findall("reportingOwner"):
        rel = ro.find("reportingOwnerRelationship")
        flags = [name for tag, name in (("isDirector", "Director"), ("isOfficer", "Officer"),
                                        ("isTenPercentOwner", "TenPercentOwner"),
                                        ("isOther", "Other"))
                 if (_txt(rel, tag) or "").lower() in ("1", "true")]
        out["REPORTINGOWNER"].append({
            "ACCESSION_NUMBER": accession,
            "RPTOWNERCIK": (_txt(ro, "reportingOwnerId/rptOwnerCik") or "").lstrip("0") or None,
            "RPTOWNERNAME": _txt(ro, "reportingOwnerId/rptOwnerName"),
            "RPTOWNER_RELATIONSHIP": ",".join(flags) or None,
            "RPTOWNER_TITLE": _txt(rel, "officerTitle"),
        })

    def common(el, sk):
        return {
            "ACCESSION_NUMBER": accession, "SECURITY_TITLE": _txt(el, "securityTitle"),
            "SECURITY_TITLE_FN": _fn(el, "securityTitle"),
            "SHRS_OWND_FOLWNG_TRANS": _txt(el, "postTransactionAmounts/sharesOwnedFollowingTransaction"),
            "SHRS_OWND_FOLWNG_TRANS_FN": _fn(el, "postTransactionAmounts/sharesOwnedFollowingTransaction"),
            "DIRECT_INDIRECT_OWNERSHIP": _txt(el, "ownershipNature/directOrIndirectOwnership"),
            "NATURE_OF_OWNERSHIP": _txt(el, "ownershipNature/natureOfOwnership"),
            "NATURE_OF_OWNERSHIP_FN": _fn(el, "ownershipNature/natureOfOwnership"),
        }

    for i, t in enumerate(root.findall("nonDerivativeTable/nonDerivativeTransaction"), 1):
        row = common(t, i)
        row.update({
            "NONDERIV_TRANS_SK": str(i),
            "TRANS_DATE": _bulk_date(_txt(t, "transactionDate")),
            "TRANS_DATE_FN": _fn(t, "transactionDate"),
            "DEEMED_EXECUTION_DATE": _bulk_date(_txt(t, "deemedExecutionDate")),
            "TRANS_FORM_TYPE": _txt(t, "transactionCoding/transactionFormType"),
            "TRANS_CODE": _txt(t, "transactionCoding/transactionCode"),
            "TRANS_TIMELINESS": _txt(t, "transactionTimeliness"),
            "TRANS_SHARES": _txt(t, "transactionAmounts/transactionShares"),
            "TRANS_SHARES_FN": _fn(t, "transactionAmounts/transactionShares"),
            "TRANS_PRICEPERSHARE": _txt(t, "transactionAmounts/transactionPricePerShare"),
            "TRANS_PRICEPERSHARE_FN": _fn(t, "transactionAmounts/transactionPricePerShare"),
            "TRANS_ACQUIRED_DISP_CD": _txt(t, "transactionAmounts/transactionAcquiredDisposedCode"),
        })
        out["NONDERIV_TRANS"].append(row)

    for i, h in enumerate(root.findall("nonDerivativeTable/nonDerivativeHolding"), 1):
        row = common(h, i)
        row["NONDERIV_HOLDING_SK"] = str(i)
        out["NONDERIV_HOLDING"].append(row)

    for f in root.findall("footnotes/footnote"):
        out["FOOTNOTES"].append({"ACCESSION_NUMBER": accession, "FOOTNOTE_ID": f.get("id"),
                                 "FOOTNOTE_TXT": " ".join("".join(f.itertext()).split())})
    return out
