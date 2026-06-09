from __future__ import annotations

from typing import Any

import pandas as pd
import yfinance as yf

from models import CompanySnapshot


def _safe_get(mapping: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in mapping and mapping[key] is not None:
            return mapping[key]
    return None


def _normalize_number(value: Any) -> float | None:
    if value is None or pd.isna(value):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _find_statement_value(frame: pd.DataFrame | None, *row_names: str) -> float | None:
    if frame is None or frame.empty:
        return None

    for row_name in row_names:
        if row_name in frame.index:
            series = frame.loc[row_name]
            if isinstance(series, pd.Series):
                for value in series.tolist():
                    normalized = _normalize_number(value)
                    if normalized is not None:
                        return normalized
            else:
                return _normalize_number(series)
    return None


def _append_warning_if_missing(warnings: list[str], value: Any, label: str) -> None:
    if value is None:
        warnings.append(f"{label} není v datech Yahoo Finance dostupné.")


def load_company_snapshot(ticker_symbol: str) -> CompanySnapshot:
    ticker = yf.Ticker(ticker_symbol.upper())
    warnings: list[str] = []
    notes: list[str] = ["Data source: Yahoo Finance via yfinance."]

    try:
        info = ticker.get_info()
    except Exception as exc:
        info = {}
        warnings.append(f"Nepodařilo se načíst profil firmy: {exc}")

    try:
        fast_info = dict(getattr(ticker, "fast_info", {}) or {})
    except Exception as exc:
        fast_info = {}
        warnings.append(f"Nepodařilo se načíst fast market data: {exc}")

    try:
        income_stmt = ticker.income_stmt
    except Exception as exc:
        income_stmt = pd.DataFrame()
        warnings.append(f"Nepodařilo se načíst income statement: {exc}")

    try:
        balance_sheet = ticker.balance_sheet
    except Exception as exc:
        balance_sheet = pd.DataFrame()
        warnings.append(f"Nepodařilo se načíst balance sheet: {exc}")

    try:
        cashflow = ticker.cashflow
    except Exception as exc:
        cashflow = pd.DataFrame()
        warnings.append(f"Nepodařilo se načíst cash flow: {exc}")

    company_name = _safe_get(info, "longName", "shortName", "displayName") or ticker_symbol.upper()
    current_price = _normalize_number(_safe_get(fast_info, "lastPrice", "regularMarketPrice"))
    if current_price is None:
        current_price = _normalize_number(_safe_get(info, "currentPrice", "regularMarketPrice", "previousClose"))
    market_cap = _normalize_number(_safe_get(info, "marketCap"))
    trailing_pe = _normalize_number(_safe_get(info, "trailingPE"))
    trailing_eps = _normalize_number(_safe_get(info, "trailingEps"))
    current_ratio = _normalize_number(_safe_get(info, "currentRatio"))
    return_on_equity = _normalize_number(_safe_get(info, "returnOnEquity"))
    debt_to_equity = _normalize_number(_safe_get(info, "debtToEquity"))
    operating_margin = _normalize_number(_safe_get(info, "operatingMargins"))
    net_margin = _normalize_number(_safe_get(info, "profitMargins"))
    revenue_growth = _normalize_number(_safe_get(info, "revenueGrowth"))
    earnings_growth = _normalize_number(_safe_get(info, "earningsGrowth"))

    total_revenue = _find_statement_value(income_stmt, "Total Revenue", "Operating Revenue")
    net_income = _find_statement_value(income_stmt, "Net Income", "Net Income Common Stockholders")
    total_debt = _find_statement_value(balance_sheet, "Total Debt")
    stockholders_equity = _find_statement_value(
        balance_sheet,
        "Stockholders Equity",
        "Common Stock Equity",
        "Total Equity Gross Minority Interest",
    )
    cash_and_equivalents = _find_statement_value(
        balance_sheet,
        "Cash And Cash Equivalents",
        "Cash Cash Equivalents And Short Term Investments",
        "Cash Financial",
    )
    operating_cash_flow = _find_statement_value(
        cashflow,
        "Operating Cash Flow",
        "Cash Flow From Continuing Operating Activities",
    )
    free_cash_flow = _find_statement_value(cashflow, "Free Cash Flow")

    if trailing_eps is None and current_price is not None and trailing_pe not in (None, 0):
        trailing_eps = current_price / trailing_pe

    buy_under_price = None
    if trailing_eps is not None and trailing_eps > 0:
        # Simple conservative rule of thumb: buy-under at 15x trailing EPS.
        buy_under_price = trailing_eps * 15

    for value, label in (
        (current_price, "Current Price"),
        (market_cap, "Market Cap"),
        (trailing_pe, "Trailing P/E"),
        (return_on_equity, "ROE"),
        (debt_to_equity, "Debt/Equity"),
        (operating_margin, "Operating Margin"),
        (free_cash_flow, "Free Cash Flow"),
        (total_revenue, "Total Revenue"),
        (net_income, "Net Income"),
    ):
        _append_warning_if_missing(warnings, value, label)

    return CompanySnapshot(
        ticker=ticker_symbol.upper(),
        company_name=company_name,
        sector=_safe_get(info, "sector"),
        industry=_safe_get(info, "industry"),
        currency=_safe_get(info, "currency", "financialCurrency"),
        current_price=current_price,
        market_cap=market_cap,
        trailing_pe=trailing_pe,
        trailing_eps=trailing_eps,
        buy_under_price=buy_under_price,
        current_ratio=current_ratio,
        return_on_equity=return_on_equity,
        debt_to_equity=debt_to_equity,
        operating_margin=operating_margin,
        net_margin=net_margin,
        revenue_growth=revenue_growth,
        earnings_growth=earnings_growth,
        free_cash_flow=free_cash_flow,
        operating_cash_flow=operating_cash_flow,
        total_revenue=total_revenue,
        net_income=net_income,
        total_debt=total_debt,
        stockholders_equity=stockholders_equity,
        cash_and_equivalents=cash_and_equivalents,
        source_notes=notes,
        warnings=warnings,
        raw_info=info,
    )
