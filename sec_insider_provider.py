from __future__ import annotations

import concurrent.futures
import csv
import gzip
import io
import json
import time
import urllib.error
import urllib.request
import zipfile
from datetime import date, datetime
from typing import Any
from xml.etree import ElementTree as ET

import pandas as pd
import yfinance as yf

from sec_edgar_provider import _load_sec_user_agent, _throttle_sec_requests, load_sec_ticker_to_cik_map


SEC_SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik}.json"
SEC_ARCHIVES_BASE_URL = "https://www.sec.gov/Archives/edgar/data"
SEC_DATASET_URL_TEMPLATE = (
    "https://www.sec.gov/files/structureddata/data/insider-transactions-data-sets/{year}q{quarter}_form345.zip"
)

FORM_TYPES = {"4", "4/A", "5", "5/A"}
BUY_CODES = {"P", "A", "M", "C", "K"}
SELL_CODES = {"S", "D", "F", "I", "U"}
TRANSACTION_CODE_LABELS = {
    "P": "Open market purchase",
    "S": "Open market sale",
    "A": "Grant or award",
    "M": "Option exercise or derivative conversion",
    "F": "Tax or exercise price payment",
    "D": "Disposition to issuer",
    "G": "Gift",
    "C": "Conversion",
    "K": "Swap or similar event",
}


def _is_management_relationship(value: str) -> bool:
    text = str(value or "")
    return "Officer" in text or "Director" in text


def _request_with_retry(url: str, accept: str, timeout: int) -> bytes:
    last_error: Exception | None = None
    for attempt in range(4):
        _throttle_sec_requests()
        request = urllib.request.Request(
            url,
            headers={
                "User-Agent": _load_sec_user_agent(),
                "Accept": accept,
                "Accept-Encoding": "gzip, deflate",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                payload = response.read()
                if response.headers.get("Content-Encoding", "").lower() == "gzip":
                    payload = gzip.decompress(payload)
                return payload
        except urllib.error.HTTPError as exc:
            last_error = exc
            if exc.code != 429 or attempt == 3:
                raise
            time.sleep(1.0 * (attempt + 1))
    raise last_error or RuntimeError(f"Request failed for {url}")


def _fetch_text(url: str, accept: str = "application/json,text/plain,*/*", timeout: int = 45) -> str:
    return _request_with_retry(url, accept, timeout).decode("utf-8", errors="ignore")


def _fetch_json(url: str) -> dict[str, Any]:
    return json.loads(_fetch_text(url))


def _fetch_binary(url: str, timeout: int = 60) -> bytes:
    return _request_with_retry(url, "application/zip,*/*", timeout)


def _safe_date(value: str) -> date | None:
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def _safe_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _safe_text(element: ET.Element | None, path: str) -> str:
    if element is None:
        return ""
    found = element.find(path)
    if found is None or found.text is None:
        return ""
    return found.text.strip()


def _transaction_kind(code: str, acquired_disposed_code: str) -> str:
    if acquired_disposed_code == "A" or code in BUY_CODES:
        return "Nakup / navyseni"
    if acquired_disposed_code == "D" or code in SELL_CODES:
        return "Prodej / snizeni"
    return "Jiny pohyb"


def _transaction_code_description(code: str) -> str:
    return TRANSACTION_CODE_LABELS.get(code, "Jiny SEC transaction code")


def _relationship_label(owner: ET.Element) -> str:
    relationship = owner.find("reportingOwnerRelationship")
    if relationship is None:
        return "Neuvedeno"

    labels: list[str] = []
    if _safe_text(relationship, "isDirector").lower() == "true":
        labels.append("Director")
    if _safe_text(relationship, "isOfficer").lower() == "true":
        officer_title = _safe_text(relationship, "officerTitle")
        labels.append(f"Officer ({officer_title})" if officer_title else "Officer")
    if _safe_text(relationship, "isTenPercentOwner").lower() == "true":
        labels.append("10% Owner")
    if _safe_text(relationship, "isOther").lower() == "true":
        other_text = _safe_text(relationship, "otherText")
        labels.append(f"Other ({other_text})" if other_text else "Other")
    return ", ".join(labels) if labels else "Neuvedeno"


def _parse_xml_filing(
    cik: str,
    accession_number: str,
    primary_document: str,
    filing_date: str,
    form_type: str,
    management_only: bool = False,
) -> list[dict[str, Any]]:
    accession_compact = accession_number.replace("-", "")
    xml_document = primary_document.split("/")[-1]
    filing_url = f"{SEC_ARCHIVES_BASE_URL}/{int(cik)}/{accession_compact}/{xml_document}"
    root = ET.fromstring(_fetch_text(filing_url, "application/xml,text/xml,*/*"))

    owners = root.findall("reportingOwner")
    owner_pairs = [
        (
            _safe_text(owner, "reportingOwnerId/rptOwnerName"),
            _relationship_label(owner),
        )
        for owner in owners
        if _safe_text(owner, "reportingOwnerId/rptOwnerName")
    ]
    if management_only:
        owner_pairs = [pair for pair in owner_pairs if _is_management_relationship(pair[1])]
    if not owner_pairs:
        return []

    owner_names = [pair[0] for pair in owner_pairs]
    owner_relationships = [pair[1] for pair in owner_pairs]

    rows: list[dict[str, Any]] = []
    for transaction in root.findall("nonDerivativeTable/nonDerivativeTransaction"):
        transaction_date = _safe_text(transaction, "transactionDate/value")
        shares = _safe_float(_safe_text(transaction, "transactionAmounts/transactionShares/value"))
        if not transaction_date or shares is None:
            continue

        transaction_code = _safe_text(transaction, "transactionCoding/transactionCode")
        acquired_disposed_code = _safe_text(
            transaction,
            "transactionAmounts/transactionAcquiredDisposedCode/value",
        )
        rows.append(
            {
                "filing_date": filing_date,
                "transaction_date": transaction_date,
                "reporting_owner": "; ".join(owner_names) if owner_names else "Neuvedeno",
                "relationship": "; ".join(owner_relationships) if owner_relationships else "Neuvedeno",
                "security_title": _safe_text(transaction, "securityTitle/value") or "Common Stock",
                "transaction_code": transaction_code or "N/A",
                "transaction_code_description": _transaction_code_description(transaction_code),
                "transaction_kind": _transaction_kind(transaction_code, acquired_disposed_code),
                "shares": shares,
                "price_per_share": _safe_float(_safe_text(transaction, "transactionAmounts/transactionPricePerShare/value")),
                "shares_following": _safe_float(
                    _safe_text(transaction, "postTransactionAmounts/sharesOwnedFollowingTransaction/value")
                ),
                "acquired_disposed_code": acquired_disposed_code or "N/A",
                "form_type": form_type,
                "filing_url": filing_url,
                "issuer_name": _safe_text(root, "issuer/issuerName"),
                "issuer_symbol": _safe_text(root, "issuer/issuerTradingSymbol"),
                "aff_10b5_one": _safe_text(root, "aff10b5One").lower() == "true",
            }
        )
    return rows


def _collect_filings(cik: str, cutoff: date) -> tuple[list[tuple[str, str, str, str]], list[str]]:
    warnings: list[str] = []
    submissions = _fetch_json(SEC_SUBMISSIONS_URL.format(cik=cik))
    filing_groups: list[dict[str, Any]] = [submissions.get("filings", {}).get("recent", {})]

    recent_dates = [_safe_date(str(value)) for value in filing_groups[0].get("filingDate", [])]
    valid_recent_dates = [value for value in recent_dates if value is not None]
    if not valid_recent_dates or min(valid_recent_dates) > cutoff:
        for file_info in submissions.get("filings", {}).get("files", []):
            historical_name = str(file_info.get("name") or "").strip()
            if not historical_name:
                continue
            try:
                historical_group = _fetch_json(f"https://data.sec.gov/submissions/{historical_name}")
                filing_groups.append(historical_group)
            except Exception as exc:
                warnings.append(f"Nepodarilo se nacist historicky SEC submissions soubor {historical_name}: {exc}")
                continue

            historical_dates = [_safe_date(str(value)) for value in historical_group.get("filingDate", [])]
            valid_historical_dates = [value for value in historical_dates if value is not None]
            if valid_historical_dates and min(valid_historical_dates) <= cutoff:
                break

    filings: list[tuple[str, str, str, str]] = []
    seen_accessions: set[str] = set()
    for group in filing_groups:
        for form_type, accession_number, primary_document, filing_date in zip(
            group.get("form", []),
            group.get("accessionNumber", []),
            group.get("primaryDocument", []),
            group.get("filingDate", []),
        ):
            filing_date_value = _safe_date(str(filing_date))
            accession_number_text = str(accession_number)
            if filing_date_value is None or filing_date_value < cutoff:
                continue
            if str(form_type) not in FORM_TYPES:
                continue
            if not str(primary_document).lower().endswith(".xml"):
                continue
            if accession_number_text in seen_accessions:
                continue
            seen_accessions.add(accession_number_text)
            filings.append((accession_number_text, str(primary_document), str(filing_date), str(form_type)))
    return filings, warnings


def _quarter_start(year: int, quarter: int) -> date:
    month = ((quarter - 1) * 3) + 1
    return date(year, month, 1)


def _quarter_end(year: int, quarter: int) -> date:
    month = quarter * 3
    if month in (1, 3, 5, 7, 8, 10, 12):
        day = 31
    elif month in (4, 6, 9, 11):
        day = 30
    else:
        day = 29 if year % 4 == 0 and (year % 100 != 0 or year % 400 == 0) else 28
    return date(year, month, day)


def _dataset_links_for_years(years: int) -> tuple[list[tuple[str, str]], date]:
    cutoff = date.today().replace(year=date.today().year - years)
    current_quarter = ((date.today().month - 1) // 3) + 1
    quarter = current_quarter - 1
    year = date.today().year
    if quarter == 0:
        quarter = 4
        year -= 1

    links: list[tuple[str, str]] = []
    last_published_end = _quarter_end(year, quarter)
    while True:
        quarter_start = _quarter_start(year, quarter)
        quarter_finish = _quarter_end(year, quarter)
        if quarter_finish < cutoff and quarter_start < cutoff.replace(month=1, day=1):
            break
        label = f"{year} Q{quarter} 345"
        url = SEC_DATASET_URL_TEMPLATE.format(year=year, quarter=quarter)
        links.append((label, url))
        quarter -= 1
        if quarter == 0:
            quarter = 4
            year -= 1
    return links, last_published_end


def _load_rows_from_single_dataset(
    ticker_symbol: str,
    cutoff: date,
    label: str,
    url: str,
    management_only: bool = False,
) -> tuple[list[dict[str, Any]], str | None]:
    rows: list[dict[str, Any]] = []
    try:
        archive = zipfile.ZipFile(io.BytesIO(_fetch_binary(url)))
        with archive.open("SUBMISSION.tsv") as submission_file:
            submission_reader = csv.DictReader(
                io.TextIOWrapper(submission_file, encoding="utf-8"), delimiter="\t"
            )
            submission_map = {
                row["ACCESSION_NUMBER"]: row
                for row in submission_reader
                if str(row.get("ISSUERTRADINGSYMBOL") or "").upper() == ticker_symbol.upper()
                and str(row.get("DOCUMENT_TYPE") or "") in FORM_TYPES
            }
        if not submission_map:
            return rows, None

        with archive.open("REPORTINGOWNER.tsv") as owner_file:
            owner_reader = csv.DictReader(io.TextIOWrapper(owner_file, encoding="utf-8"), delimiter="\t")
            owners_by_accession: dict[str, list[dict[str, str]]] = {}
            for row in owner_reader:
                accession_number = str(row.get("ACCESSION_NUMBER") or "")
                if accession_number not in submission_map:
                    continue
                owners_by_accession.setdefault(accession_number, []).append(row)

        with archive.open("NONDERIV_TRANS.tsv") as trans_file:
            trans_reader = csv.DictReader(io.TextIOWrapper(trans_file, encoding="utf-8"), delimiter="\t")
            for row in trans_reader:
                accession_number = str(row.get("ACCESSION_NUMBER") or "")
                if accession_number not in submission_map:
                    continue

                transaction_date_raw = str(row.get("TRANS_DATE") or "").strip()
                transaction_date = None
                for fmt in ("%d-%b-%Y", "%Y-%m-%d"):
                    try:
                        transaction_date = datetime.strptime(transaction_date_raw, fmt).date()
                        break
                    except ValueError:
                        continue
                if transaction_date is None or transaction_date < cutoff:
                    continue

                shares = _safe_float(row.get("TRANS_SHARES"))
                if shares is None:
                    continue

                transaction_code = str(row.get("TRANS_CODE") or "").strip()
                acquired_disposed_code = str(row.get("ACQ_DISP_CD") or "").strip()
                owner_rows = owners_by_accession.get(accession_number, [])
                owner_names = sorted(
                    {str(owner.get("RPTOWNERNAME") or "") for owner in owner_rows if owner.get("RPTOWNERNAME")}
                )
                owner_roles = sorted(
                    {
                        str(owner.get("RPTOWNER_RELATIONSHIP") or "")
                        for owner in owner_rows
                        if owner.get("RPTOWNER_RELATIONSHIP")
                    }
                )
                if management_only and not any(_is_management_relationship(role) for role in owner_roles):
                    continue

                rows.append(
                    {
                        "filing_date": str(submission_map[accession_number].get("FILING_DATE") or ""),
                        "transaction_date": transaction_date.isoformat(),
                        "reporting_owner": "; ".join(owner_names) if owner_names else "Neuvedeno",
                        "relationship": "; ".join(owner_roles) if owner_roles else "Neuvedeno",
                        "security_title": str(row.get("SECURITY_TITLE") or "Common Stock"),
                        "transaction_code": transaction_code or "N/A",
                        "transaction_code_description": _transaction_code_description(transaction_code),
                        "transaction_kind": _transaction_kind(transaction_code, acquired_disposed_code),
                        "shares": shares,
                        "price_per_share": _safe_float(row.get("TRANS_PRICEPERSHARE")),
                        "shares_following": _safe_float(row.get("SHRS_OWND_FOLWNG_TRANS")),
                        "acquired_disposed_code": acquired_disposed_code or "N/A",
                        "form_type": str(submission_map[accession_number].get("DOCUMENT_TYPE") or ""),
                        "filing_url": "",
                        "issuer_name": str(submission_map[accession_number].get("ISSUERNAME") or ""),
                        "issuer_symbol": str(submission_map[accession_number].get("ISSUERTRADINGSYMBOL") or ""),
                        "aff_10b5_one": False,
                    }
                )
        return rows, None
    except Exception as exc:
        return [], f"Nepodarilo se nacist SEC insider dataset {label}: {exc}"


def _load_rows_from_dataset(
    ticker_symbol: str,
    years: int,
    management_only: bool = False,
) -> tuple[list[dict[str, Any]], list[str], date]:
    warnings: list[str] = []
    cutoff = date.today().replace(year=date.today().year - years)
    links, last_published_end = _dataset_links_for_years(years)
    rows: list[dict[str, Any]] = []

    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as executor:
        futures = [
            executor.submit(_load_rows_from_single_dataset, ticker_symbol, cutoff, label, url, management_only)
            for label, url in links
        ]
        for future in concurrent.futures.as_completed(futures):
            quarter_rows, warning = future.result()
            rows.extend(quarter_rows)
            if warning:
                warnings.append(warning)

    return rows, warnings, last_published_end


def _yahoo_transaction_code(text: str) -> str:
    normalized = text.lower()
    if "sale" in normalized:
        return "S"
    if "purchase" in normalized:
        return "P"
    if "gift" in normalized:
        return "G"
    if "conversion" in normalized or "exercise" in normalized:
        return "M"
    if "award" in normalized or "grant" in normalized:
        return "A"
    return "N/A"


def _load_rows_from_yfinance(
    ticker_symbol: str,
    cutoff: date,
    management_only: bool = False,
) -> tuple[list[dict[str, Any]], list[str]]:
    warnings: list[str] = [
        "Primarni SEC EDGAR endpointy pro insider transakce nebyly dostupne. "
        "Pouzivam zalozni tabulku Yahoo Finance insider_transactions, ktera muze byt mene kompletni nez SEC Form 4/5."
    ]

    try:
        frame = yf.Ticker(ticker_symbol.upper()).get_insider_transactions()
    except Exception as exc:
        return [], warnings + [f"Nepodarilo se nacist zalozni insider transakce z Yahoo Finance: {exc}"]

    if frame is None or frame.empty:
        return [], warnings + ["Yahoo Finance nevratil zadne zalozni insider transakce."]

    rows: list[dict[str, Any]] = []
    for _, row in frame.iterrows():
        transaction_date = pd.to_datetime(row.get("Start Date"), errors="coerce")
        if pd.isna(transaction_date) or transaction_date.date() < cutoff:
            continue

        position = str(row.get("Position") or "Neuvedeno")
        if management_only and not _is_management_relationship(position):
            continue

        shares = _safe_float(row.get("Shares"))
        if shares is None:
            continue

        text = str(row.get("Text") or "")
        transaction_code = _yahoo_transaction_code(text)
        value = _safe_float(row.get("Value"))
        price_per_share = value / shares if value is not None and shares else None

        rows.append(
            {
                "filing_date": transaction_date.date().isoformat(),
                "transaction_date": transaction_date.date().isoformat(),
                "reporting_owner": str(row.get("Insider") or "Neuvedeno"),
                "relationship": position,
                "security_title": "Common Stock",
                "transaction_code": transaction_code,
                "transaction_code_description": _transaction_code_description(transaction_code),
                "transaction_kind": _transaction_kind(
                    transaction_code,
                    "D" if transaction_code == "S" else ("A" if transaction_code in BUY_CODES else ""),
                ),
                "shares": shares,
                "price_per_share": price_per_share,
                "shares_following": None,
                "acquired_disposed_code": "N/A",
                "form_type": "Yahoo Finance",
                "filing_url": str(row.get("URL") or ""),
                "issuer_name": "",
                "issuer_symbol": ticker_symbol.upper(),
                "aff_10b5_one": False,
            }
        )

    return rows, warnings


def _compact_warnings(warnings: list[str], used_yahoo_fallback: bool = False) -> list[str]:
    if not used_yahoo_fallback:
        return list(dict.fromkeys(warnings))

    compacted: list[str] = []
    sec_blocked = False
    for warning in warnings:
        if "HTTP Error 403: Forbidden" in warning and (
            "SEC insider dataset" in warning
            or "SEC mapovani tickeru" in warning
            or "SEC mapování tickeru" in warning
        ):
            sec_blocked = True
            continue
        if warning.startswith("Primarni SEC EDGAR endpointy pro insider transakce nebyly dostupne."):
            sec_blocked = True
            continue
        if "Ticker " in warning and "SEC mapovani ticker/CIK" in warning:
            continue
        compacted.append(warning)

    if sec_blocked:
        compacted.insert(
            0,
            "SEC EDGAR momentalne odmita prime automaticke dotazy z hostingu aplikace. "
            "Aplikace proto zobrazila zalozni insider transakce z Yahoo Finance; ty mohou byt mene kompletni nez SEC Form 4/5.",
        )

    return list(dict.fromkeys(compacted))


def load_insider_transactions(
    ticker_symbol: str,
    years: int = 5,
    cik_override: str | None = None,
    management_only: bool = False,
) -> tuple[pd.DataFrame, list[str]]:
    warnings: list[str] = []
    cutoff = date.today().replace(year=date.today().year - years)

    rows: list[dict[str, Any]] = []
    used_yahoo_fallback = False

    dataset_rows, dataset_warnings, dataset_until = _load_rows_from_dataset(
        ticker_symbol,
        years,
        management_only=management_only,
    )
    rows.extend(dataset_rows)
    warnings.extend(dataset_warnings)

    cik = str(cik_override or "").strip()
    if not cik:
        try:
            ticker_to_cik = load_sec_ticker_to_cik_map()
            cik = str(ticker_to_cik.get(ticker_symbol.upper()) or "").strip()
        except Exception as exc:
            cik = ""
            warnings.append(
                "Nepodarilo se nacist SEC mapovani tickeru pro nejnovejsi insider filingy. "
                f"Zobrazuji dostupna data z kvartalnich SEC datasetu. Detail: {exc}"
            )

    if not cik:
        warnings.append(
            f"Ticker {ticker_symbol.upper()} nebyl nalezen v SEC mapovani ticker/CIK. "
            "Nejnovejsi individualni filings se proto nedotahnou, ale kvartalni SEC datasety se pouziji."
        )

    filings: list[tuple[str, str, str, str]] = []
    if cik:
        try:
            filings, filing_warnings = _collect_filings(cik, cutoff)
            warnings.extend(filing_warnings)
        except Exception as exc:
            warnings.append(f"Nepodarilo se pripravit seznam insider filings z SEC pro {ticker_symbol.upper()}: {exc}")

    filings = [
        filing
        for filing in filings
        if (_safe_date(filing[2]) or cutoff) > dataset_until
    ]

    if filings:
        parse_failures = 0
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
            futures = [
                executor.submit(
                    _parse_xml_filing,
                    cik,
                    accession_number,
                    primary_document,
                    filing_date,
                    form_type,
                    management_only,
                )
                for accession_number, primary_document, filing_date, form_type in filings
            ]
            for future in concurrent.futures.as_completed(futures):
                try:
                    rows.extend(future.result())
                except Exception:
                    parse_failures += 1
        if parse_failures:
            warnings.append(
                f"U casti SEC Form 4/5 souboru se nepodarilo nacist detail transakci ({parse_failures} souboru)."
            )

    if not rows:
        yahoo_rows, yahoo_warnings = _load_rows_from_yfinance(
            ticker_symbol,
            cutoff,
            management_only=management_only,
        )
        rows.extend(yahoo_rows)
        warnings.extend(yahoo_warnings)
        used_yahoo_fallback = bool(yahoo_rows)

    if not rows:
        warnings.append(
            f"Za poslednich {years} let nebyly pro ticker {ticker_symbol.upper()} nalezeny zadne insider transakce SEC Form 4/5."
        )
        return pd.DataFrame(), _compact_warnings(warnings, used_yahoo_fallback)

    frame = pd.DataFrame(rows)
    frame["transaction_date"] = pd.to_datetime(frame["transaction_date"], errors="coerce", format="mixed")
    frame["filing_date"] = pd.to_datetime(frame["filing_date"], errors="coerce", format="mixed")
    frame = frame[frame["transaction_date"].notna()].copy()
    frame = frame[frame["transaction_date"].dt.date >= cutoff].copy()

    if frame.empty:
        warnings.append(
            f"Za poslednich {years} let nebyly pro ticker {ticker_symbol.upper()} nalezeny zadne insider transakce SEC Form 4/5."
        )
        return pd.DataFrame(), _compact_warnings(warnings, used_yahoo_fallback)

    frame["signed_shares"] = frame.apply(
        lambda row: row["shares"]
        if row["transaction_kind"] == "Nakup / navyseni"
        else (-row["shares"] if row["transaction_kind"] == "Prodej / snizeni" else 0.0),
        axis=1,
    )
    frame.sort_values(["transaction_date", "filing_date"], ascending=[False, False], inplace=True)
    return frame.reset_index(drop=True), _compact_warnings(warnings, used_yahoo_fallback)
