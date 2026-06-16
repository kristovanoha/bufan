from __future__ import annotations

import gzip
import json
import os
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import date
from functools import lru_cache
from pathlib import Path
from threading import Lock
from typing import Any


SEC_TICKER_MAP_URL = "https://www.sec.gov/files/company_tickers.json"
SEC_COMPANY_FACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json"
SEC_REQUEST_INTERVAL_SECONDS = 0.12
ANNUAL_FORMS = {"10-K", "10-K/A", "10-KT", "10-KT/A", "20-F", "20-F/A", "40-F", "40-F/A"}
ANNUAL_DURATION_MIN_DAYS = 300
ANNUAL_DURATION_MAX_DAYS = 380

_request_lock = Lock()
_last_request_at = 0.0

REVENUE_CONCEPTS = (
    "RevenueFromContractWithCustomerExcludingAssessedTax",
    "SalesRevenueNet",
    "Revenues",
    "RevenueFromContractWithCustomerIncludingAssessedTax",
)
NET_INCOME_CONCEPTS = ("NetIncomeLoss", "ProfitLoss")
OPERATING_INCOME_CONCEPTS = ("OperatingIncomeLoss",)
CASH_CONCEPTS = (
    "CashAndCashEquivalentsAtCarryingValue",
    "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents",
    "CashCashEquivalentsAndShortTermInvestments",
)
TOTAL_DEBT_CONCEPTS = (
    "DebtAndFinanceLeaseObligations",
    "DebtAndCapitalLeaseObligations",
    "DebtLongtermAndShorttermCombinedAmount",
    "LongTermDebtAndCapitalLeaseObligations",
    "LongTermDebtAndFinanceLeaseObligations",
)
CURRENT_DEBT_CONCEPTS = (
    "LongTermDebtCurrent",
    "LongTermDebtMaturitiesRepaymentsOfPrincipalInNextTwelveMonths",
    "ShortTermBorrowings",
    "ShortTermDebt",
    "CommercialPaper",
)
NONCURRENT_DEBT_CONCEPTS = (
    "LongTermDebtNoncurrent",
    "LongTermDebt",
)
EQUITY_CONCEPTS = (
    "StockholdersEquity",
    "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest",
)
OPERATING_CASH_FLOW_CONCEPTS = (
    "NetCashProvidedByUsedInOperatingActivities",
    "NetCashProvidedByUsedInContinuingOperations",
)
CAPEX_CONCEPTS = (
    "PaymentsToAcquirePropertyPlantAndEquipment",
    "PropertyPlantAndEquipmentAdditions",
    "PaymentsToAcquireProductiveAssets",
)
CURRENT_ASSETS_CONCEPTS = ("AssetsCurrent",)
CURRENT_LIABILITIES_CONCEPTS = ("LiabilitiesCurrent",)


@dataclass(slots=True)
class SecStatementData:
    cik: str | None = None
    entity_name: str | None = None
    total_revenue: float | None = None
    previous_total_revenue: float | None = None
    net_income: float | None = None
    previous_net_income: float | None = None
    operating_income: float | None = None
    cash_and_equivalents: float | None = None
    total_debt: float | None = None
    stockholders_equity: float | None = None
    operating_cash_flow: float | None = None
    capital_expenditures: float | None = None
    current_assets: float | None = None
    current_liabilities: float | None = None
    warnings: list[str] = field(default_factory=list)
    source_notes: list[str] = field(default_factory=list)


@dataclass(slots=True)
class SecFact:
    value: float
    end: date
    filed: date | None
    fiscal_year: int | None
    form: str
    concept: str
    unit: str


def _load_sec_user_agent() -> str:
    config_path = Path(__file__).with_name("config.json")
    if config_path.exists():
        try:
            with config_path.open("r", encoding="utf-8") as config_file:
                config = json.load(config_file)
            configured = str(config.get("sec_user_agent", "")).strip()
            if configured:
                return configured
        except (OSError, json.JSONDecodeError):
            pass

    return str(
        os.getenv("SEC_USER_AGENT", "")
        or "Buffett Analyzer/1.0 personal analysis app https://github.com/kristovanoha/bufan"
    ).strip()


def _throttle_sec_requests() -> None:
    global _last_request_at

    with _request_lock:
        now = time.monotonic()
        wait_seconds = SEC_REQUEST_INTERVAL_SECONDS - (now - _last_request_at)
        if wait_seconds > 0:
            time.sleep(wait_seconds)
        _last_request_at = time.monotonic()


def _fetch_json(url: str) -> dict[str, Any]:
    _throttle_sec_requests()
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": _load_sec_user_agent(),
            "Accept": "application/json,text/plain,*/*",
            "Accept-Encoding": "gzip, deflate",
        },
    )

    with urllib.request.urlopen(request, timeout=30) as response:
        payload = response.read()
        if response.headers.get("Content-Encoding", "").lower() == "gzip":
            payload = gzip.decompress(payload)
        return json.loads(payload)


@lru_cache(maxsize=1)
def load_sec_ticker_to_cik_map() -> dict[str, str]:
    raw_data = _fetch_json(SEC_TICKER_MAP_URL)
    return {
        str(item["ticker"]).upper(): f"{int(item['cik_str']):010d}"
        for item in raw_data.values()
        if item.get("ticker") and item.get("cik_str") is not None
    }


@lru_cache(maxsize=256)
def load_company_facts(cik: str) -> dict[str, Any]:
    return _fetch_json(SEC_COMPANY_FACTS_URL.format(cik=cik))


def _parse_date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def _normalize_number(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _is_duration_annual(item: dict[str, Any]) -> bool:
    start = _parse_date(item.get("start"))
    end = _parse_date(item.get("end"))
    if start is None or end is None:
        return False

    duration_days = (end - start).days + 1
    return ANNUAL_DURATION_MIN_DAYS <= duration_days <= ANNUAL_DURATION_MAX_DAYS


def _extract_candidates(
    facts: dict[str, Any],
    concepts: tuple[str, ...],
    *,
    instant: bool,
) -> list[SecFact]:
    us_gaap = facts.get("facts", {}).get("us-gaap", {})
    candidates: list[SecFact] = []

    for concept in concepts:
        concept_payload = us_gaap.get(concept)
        if not concept_payload:
            continue

        for unit, unit_facts in concept_payload.get("units", {}).items():
            if not isinstance(unit_facts, list):
                continue

            for item in unit_facts:
                form = str(item.get("form", "")).upper()
                if form not in ANNUAL_FORMS:
                    continue

                value = _normalize_number(item.get("val"))
                end_date = _parse_date(item.get("end"))
                filed_date = _parse_date(item.get("filed"))
                if value is None or end_date is None:
                    continue

                if not instant and not _is_duration_annual(item):
                    continue

                candidates.append(
                    SecFact(
                        value=value,
                        end=end_date,
                        filed=filed_date,
                        fiscal_year=item.get("fy"),
                        form=form,
                        concept=concept,
                        unit=unit,
                    )
                )

    deduped_by_period: dict[tuple[date, str], SecFact] = {}
    for candidate in candidates:
        key = (candidate.end, candidate.unit)
        existing = deduped_by_period.get(key)
        if existing is None:
            deduped_by_period[key] = candidate
            continue

        existing_filed = existing.filed or existing.end
        candidate_filed = candidate.filed or candidate.end
        if candidate_filed >= existing_filed:
            deduped_by_period[key] = candidate

    return sorted(
        deduped_by_period.values(),
        key=lambda item: (item.end, item.filed or item.end, item.fiscal_year or 0),
        reverse=True,
    )


def _latest_value(facts: dict[str, Any], concepts: tuple[str, ...], *, instant: bool) -> float | None:
    candidates = _extract_candidates(facts, concepts, instant=instant)
    if not candidates:
        return None
    return candidates[0].value


def _latest_two_values(facts: dict[str, Any], concepts: tuple[str, ...]) -> tuple[float | None, float | None]:
    candidates = _extract_candidates(facts, concepts, instant=False)
    if not candidates:
        return None, None
    latest = candidates[0].value
    previous = candidates[1].value if len(candidates) > 1 else None
    return latest, previous


def _build_total_debt(facts: dict[str, Any]) -> float | None:
    direct_total_debt = _latest_value(facts, TOTAL_DEBT_CONCEPTS, instant=True)
    if direct_total_debt is not None:
        return direct_total_debt

    current_debt = _latest_value(facts, CURRENT_DEBT_CONCEPTS, instant=True)
    noncurrent_debt = _latest_value(facts, NONCURRENT_DEBT_CONCEPTS, instant=True)
    if current_debt is not None and noncurrent_debt is not None:
        return current_debt + noncurrent_debt

    return noncurrent_debt if noncurrent_debt is not None else current_debt


def load_sec_statement_data(ticker_symbol: str) -> SecStatementData:
    ticker = ticker_symbol.upper()
    warnings: list[str] = []

    try:
        ticker_map = load_sec_ticker_to_cik_map()
    except Exception as exc:
        return SecStatementData(
            warnings=[f"Nepodařilo se načíst SEC mapování tickeru na CIK pro {ticker}: {exc}"]
        )

    cik = ticker_map.get(ticker)
    if not cik:
        return SecStatementData(
            warnings=[f"Ticker {ticker} nebyl nalezen v oficiálním SEC mapování ticker/CIK."]
        )

    try:
        facts = load_company_facts(cik)
    except urllib.error.HTTPError as exc:
        return SecStatementData(
            cik=cik,
            warnings=[f"SEC EDGAR nevrátil účetní fakta pro {ticker} (HTTP {exc.code})."]
        )
    except Exception as exc:
        return SecStatementData(
            cik=cik,
            warnings=[f"Nepodařilo se načíst účetní data z SEC EDGAR pro {ticker}: {exc}"]
        )

    total_revenue, previous_total_revenue = _latest_two_values(facts, REVENUE_CONCEPTS)
    net_income, previous_net_income = _latest_two_values(facts, NET_INCOME_CONCEPTS)

    return SecStatementData(
        cik=cik,
        entity_name=str(facts.get("entityName", "")).strip() or None,
        total_revenue=total_revenue,
        previous_total_revenue=previous_total_revenue,
        net_income=net_income,
        previous_net_income=previous_net_income,
        operating_income=_latest_value(facts, OPERATING_INCOME_CONCEPTS, instant=False),
        cash_and_equivalents=_latest_value(facts, CASH_CONCEPTS, instant=True),
        total_debt=_build_total_debt(facts),
        stockholders_equity=_latest_value(facts, EQUITY_CONCEPTS, instant=True),
        operating_cash_flow=_latest_value(facts, OPERATING_CASH_FLOW_CONCEPTS, instant=False),
        capital_expenditures=_latest_value(facts, CAPEX_CONCEPTS, instant=False),
        current_assets=_latest_value(facts, CURRENT_ASSETS_CONCEPTS, instant=True),
        current_liabilities=_latest_value(facts, CURRENT_LIABILITIES_CONCEPTS, instant=True),
        warnings=warnings,
        source_notes=[
            "Zdroj dat: Yahoo Finance pres yfinance pro cenu a zakladni trzni metriky.",
            f"Ucetni vykazy: SEC EDGAR companyfacts API pro americka podani (CIK {cik}).",
        ],
    )
