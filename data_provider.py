from __future__ import annotations

from typing import Any

import pandas as pd
import yfinance as yf

from models import CompanySnapshot
from sec_edgar_provider import load_sec_statement_data


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
        if row_name not in frame.index:
            continue
        series = frame.loc[row_name]
        if isinstance(series, pd.Series):
            for value in series.tolist():
                normalized = _normalize_number(value)
                if normalized is not None:
                    return normalized
        else:
            normalized = _normalize_number(series)
            if normalized is not None:
                return normalized
    return None


def _append_warning_if_missing(warnings: list[str], value: Any, label: str, source_name: str) -> None:
    if value is None:
        warnings.append(f"{label} neni v datech {source_name} dostupne.")


def _safe_ratio(numerator: float | None, denominator: float | None) -> float | None:
    if numerator is None or denominator in (None, 0):
        return None
    return numerator / denominator


def _safe_growth(current_value: float | None, previous_value: float | None) -> float | None:
    if current_value is None or previous_value in (None, 0) or previous_value <= 0:
        return None
    return (current_value - previous_value) / previous_value


def _coalesce(*values: float | None) -> float | None:
    for value in values:
        if value is not None:
            return value
    return None


def _derive_free_cash_flow(operating_cash_flow: float | None, capital_expenditures: float | None) -> float | None:
    if operating_cash_flow is None or capital_expenditures is None:
        return None
    return (
        operating_cash_flow + capital_expenditures
        if capital_expenditures < 0
        else operating_cash_flow - capital_expenditures
    )


def load_price_history(ticker_symbol: str, period: str = "5y") -> tuple[pd.DataFrame, list[str]]:
    ticker = yf.Ticker(ticker_symbol.upper())
    warnings: list[str] = []

    try:
        history = ticker.history(period=period, auto_adjust=False)
    except Exception as exc:
        return pd.DataFrame(), [f"Nepodarilo se nacist cenovou historii pro {ticker_symbol.upper()}: {exc}"]

    if history.empty:
        warnings.append(
            f"Cenova historie pro {ticker_symbol.upper()} za obdobi {period} neni v Yahoo Finance dostupna."
        )
        return pd.DataFrame(), warnings

    if "Close" not in history.columns:
        warnings.append(
            f"Yahoo Finance nevratil sloupec Close pro {ticker_symbol.upper()} za obdobi {period}."
        )
        return pd.DataFrame(), warnings

    close_history = history[["Close"]].copy()
    close_history.index = pd.to_datetime(close_history.index)
    close_history.rename(columns={"Close": "Close Price"}, inplace=True)
    return close_history, warnings


def load_company_snapshot(ticker_symbol: str, use_sec_statements: bool = False) -> CompanySnapshot:
    ticker = yf.Ticker(ticker_symbol.upper())
    warnings: list[str] = []
    notes: list[str] = ["Zdroj dat: Yahoo Finance pres yfinance."]
    raw_info: dict[str, Any] = {}

    try:
        info = ticker.get_info()
        raw_info.update(info)
    except Exception as exc:
        info = {}
        warnings.append(f"Nepodarilo se nacist profil firmy: {exc}")

    try:
        fast_info = dict(getattr(ticker, "fast_info", {}) or {})
    except Exception as exc:
        fast_info = {}
        warnings.append(f"Nepodarilo se nacist fast market data: {exc}")

    try:
        income_stmt = ticker.income_stmt
    except Exception as exc:
        income_stmt = pd.DataFrame()
        warnings.append(f"Nepodarilo se nacist income statement z Yahoo Finance: {exc}")

    try:
        balance_sheet = ticker.balance_sheet
    except Exception as exc:
        balance_sheet = pd.DataFrame()
        warnings.append(f"Nepodarilo se nacist balance sheet z Yahoo Finance: {exc}")

    try:
        cashflow = ticker.cashflow
    except Exception as exc:
        cashflow = pd.DataFrame()
        warnings.append(f"Nepodarilo se nacist cash flow z Yahoo Finance: {exc}")

    company_name = _safe_get(info, "longName", "shortName", "displayName") or ticker_symbol.upper()
    current_price = _normalize_number(_safe_get(fast_info, "lastPrice", "regularMarketPrice"))
    if current_price is None:
        current_price = _normalize_number(_safe_get(info, "currentPrice", "regularMarketPrice", "previousClose"))

    market_cap = _normalize_number(_safe_get(info, "marketCap"))
    shares_outstanding = _normalize_number(_safe_get(fast_info, "shares"))
    if shares_outstanding is None:
        shares_outstanding = _normalize_number(_safe_get(info, "sharesOutstanding", "impliedSharesOutstanding"))

    trailing_pe = _normalize_number(_safe_get(info, "trailingPE"))
    trailing_eps = _normalize_number(_safe_get(info, "trailingEps"))
    last_year_dividend_yield = _normalize_number(_safe_get(info, "trailingAnnualDividendYield"))
    if last_year_dividend_yield is not None:
        last_year_dividend_yield *= 100
    else:
        last_year_dividend_yield = _normalize_number(_safe_get(info, "dividendYield"))
        if last_year_dividend_yield is not None:
            last_year_dividend_yield *= 100
    five_year_avg_dividend_yield = _normalize_number(_safe_get(info, "fiveYearAvgDividendYield"))

    yahoo_current_ratio = _normalize_number(_safe_get(info, "currentRatio"))
    yahoo_return_on_equity = _normalize_number(_safe_get(info, "returnOnEquity"))
    yahoo_debt_to_equity = _normalize_number(_safe_get(info, "debtToEquity"))
    yahoo_operating_margin = _normalize_number(_safe_get(info, "operatingMargins"))
    yahoo_net_margin = _normalize_number(_safe_get(info, "profitMargins"))
    yahoo_revenue_growth = _normalize_number(_safe_get(info, "revenueGrowth"))
    yahoo_earnings_growth = _normalize_number(_safe_get(info, "earningsGrowth"))

    yahoo_total_revenue = _find_statement_value(income_stmt, "Total Revenue", "Operating Revenue")
    yahoo_net_income = _find_statement_value(income_stmt, "Net Income", "Net Income Common Stockholders")
    yahoo_total_debt = _find_statement_value(balance_sheet, "Total Debt")
    yahoo_stockholders_equity = _find_statement_value(
        balance_sheet,
        "Stockholders Equity",
        "Common Stock Equity",
        "Total Equity Gross Minority Interest",
    )
    yahoo_cash_and_equivalents = _find_statement_value(
        balance_sheet,
        "Cash And Cash Equivalents",
        "Cash Cash Equivalents And Short Term Investments",
        "Cash Financial",
    )
    yahoo_operating_cash_flow = _find_statement_value(
        cashflow,
        "Operating Cash Flow",
        "Cash Flow From Continuing Operating Activities",
    )
    yahoo_capital_expenditures = _find_statement_value(
        cashflow,
        "Capital Expenditure",
        "Capital Expenditures",
        "Capital Expenditure Reported",
    )
    yahoo_free_cash_flow = _find_statement_value(cashflow, "Free Cash Flow")
    if yahoo_free_cash_flow is None:
        yahoo_free_cash_flow = _derive_free_cash_flow(yahoo_operating_cash_flow, yahoo_capital_expenditures)

    sec_data = None
    sec_failed = False
    sec_has_values = False
    if use_sec_statements:
        sec_data = load_sec_statement_data(ticker_symbol)
        if sec_data.cik:
            raw_info["sec_cik"] = sec_data.cik
        if sec_data.entity_name and (not company_name or company_name == ticker_symbol.upper()):
            company_name = sec_data.entity_name
        sec_has_values = any(
            value is not None
            for value in (
                sec_data.total_revenue,
                sec_data.net_income,
                sec_data.total_debt,
                sec_data.stockholders_equity,
                sec_data.cash_and_equivalents,
                sec_data.operating_cash_flow,
                sec_data.capital_expenditures,
            )
        )
        sec_failed = bool(sec_data.warnings) and not sec_has_values
        if sec_has_values:
            notes = [
                "Zdroj dat: Yahoo Finance pres yfinance pro cenu a zakladni trzni metriky.",
                f"Ucetni data jsou primarne z Yahoo Finance, chybejici polozky doplnuje SEC EDGAR{f' (CIK {sec_data.cik})' if sec_data.cik else ''}.",
            ]
    total_revenue = yahoo_total_revenue
    net_income = yahoo_net_income
    total_debt = yahoo_total_debt
    stockholders_equity = yahoo_stockholders_equity
    cash_and_equivalents = yahoo_cash_and_equivalents
    operating_cash_flow = yahoo_operating_cash_flow
    capital_expenditures = yahoo_capital_expenditures
    free_cash_flow = yahoo_free_cash_flow

    if use_sec_statements and sec_data is not None:
        total_revenue = _coalesce(yahoo_total_revenue, sec_data.total_revenue)
        net_income = _coalesce(yahoo_net_income, sec_data.net_income)
        total_debt = _coalesce(yahoo_total_debt, sec_data.total_debt)
        stockholders_equity = _coalesce(yahoo_stockholders_equity, sec_data.stockholders_equity)
        cash_and_equivalents = _coalesce(yahoo_cash_and_equivalents, sec_data.cash_and_equivalents)
        operating_cash_flow = _coalesce(yahoo_operating_cash_flow, sec_data.operating_cash_flow)
        capital_expenditures = _coalesce(yahoo_capital_expenditures, sec_data.capital_expenditures)
        free_cash_flow = _coalesce(
            yahoo_free_cash_flow,
            _derive_free_cash_flow(operating_cash_flow, capital_expenditures),
        )

    if sec_failed and any(
        value is None
        for value in (
            total_revenue,
            net_income,
            total_debt,
            stockholders_equity,
            cash_and_equivalents,
            operating_cash_flow,
            free_cash_flow,
        )
    ):
        warnings.append(
            "SEC EDGAR je docasne nedostupny nebo omezuje pozadavky, proto aplikace pouzila dostupna data z Yahoo Finance a cast ucetnich poli muze chybet."
        )

    derived_current_ratio = None
    derived_return_on_equity = None
    derived_debt_to_equity = None
    derived_operating_margin = None
    derived_net_margin = None
    derived_revenue_growth = None
    derived_earnings_growth = None

    if use_sec_statements and sec_data is not None and sec_has_values:
        derived_current_ratio = _safe_ratio(sec_data.current_assets, sec_data.current_liabilities)
        derived_return_on_equity = _safe_ratio(net_income, stockholders_equity)
        raw_debt_to_equity = _safe_ratio(total_debt, stockholders_equity)
        derived_debt_to_equity = None if raw_debt_to_equity is None else raw_debt_to_equity * 100
        derived_operating_margin = _safe_ratio(sec_data.operating_income, total_revenue)
        derived_net_margin = _safe_ratio(net_income, total_revenue)
        derived_revenue_growth = _safe_growth(sec_data.total_revenue, sec_data.previous_total_revenue)
        derived_earnings_growth = _safe_growth(sec_data.net_income, sec_data.previous_net_income)

    current_ratio = _coalesce(yahoo_current_ratio, derived_current_ratio)
    return_on_equity = _coalesce(yahoo_return_on_equity, derived_return_on_equity)
    debt_to_equity = _coalesce(yahoo_debt_to_equity, derived_debt_to_equity)
    operating_margin = _coalesce(yahoo_operating_margin, derived_operating_margin)
    net_margin = _coalesce(yahoo_net_margin, derived_net_margin)
    revenue_growth = _coalesce(yahoo_revenue_growth, derived_revenue_growth)
    earnings_growth = _coalesce(yahoo_earnings_growth, derived_earnings_growth)

    if trailing_eps is None and current_price is not None and trailing_pe not in (None, 0):
        trailing_eps = current_price / trailing_pe

    statement_source_name = "Yahoo Finance" if not use_sec_statements else "Yahoo Finance a SEC EDGAR"
    for value, label, source_name in (
        (current_price, "Current Price", "Yahoo Finance"),
        (market_cap, "Market Cap", "Yahoo Finance"),
        (shares_outstanding, "Shares Outstanding", "Yahoo Finance"),
        (trailing_pe, "Trailing P/E", "Yahoo Finance"),
        (last_year_dividend_yield, "Last Year Dividend Yield", "Yahoo Finance"),
        (five_year_avg_dividend_yield, "5Y Average Dividend Yield", "Yahoo Finance"),
        (return_on_equity, "ROE", statement_source_name),
        (debt_to_equity, "Debt/Equity", statement_source_name),
        (operating_margin, "Operating Margin", statement_source_name),
        (free_cash_flow, "Free Cash Flow", statement_source_name),
        (total_revenue, "Total Revenue", statement_source_name),
        (net_income, "Net Income", statement_source_name),
    ):
        _append_warning_if_missing(warnings, value, label, source_name)

    return CompanySnapshot(
        ticker=ticker_symbol.upper(),
        company_name=company_name,
        sector=_safe_get(info, "sector"),
        industry=_safe_get(info, "industry"),
        currency=_safe_get(info, "currency", "financialCurrency"),
        current_price=current_price,
        market_cap=market_cap,
        shares_outstanding=shares_outstanding,
        trailing_pe=trailing_pe,
        trailing_eps=trailing_eps,
        last_year_dividend_yield=last_year_dividend_yield,
        five_year_avg_dividend_yield=five_year_avg_dividend_yield,
        current_ratio=current_ratio,
        return_on_equity=return_on_equity,
        debt_to_equity=debt_to_equity,
        operating_margin=operating_margin,
        net_margin=net_margin,
        revenue_growth=revenue_growth,
        earnings_growth=earnings_growth,
        free_cash_flow=free_cash_flow,
        operating_cash_flow=operating_cash_flow,
        capital_expenditures=capital_expenditures,
        total_revenue=total_revenue,
        net_income=net_income,
        total_debt=total_debt,
        stockholders_equity=stockholders_equity,
        cash_and_equivalents=cash_and_equivalents,
        source_notes=notes,
        warnings=list(dict.fromkeys(warnings)),
        raw_info=raw_info,
    )
