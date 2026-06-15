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


def load_price_history(ticker_symbol: str, period: str = "5y") -> tuple[pd.DataFrame, list[str]]:
    ticker = yf.Ticker(ticker_symbol.upper())
    warnings: list[str] = []

    try:
        history = ticker.history(period=period, auto_adjust=False)
    except Exception as exc:
        return pd.DataFrame(), [f"NepodaĹ™ilo se naÄŤĂ­st cenovou historii pro {ticker_symbol.upper()}: {exc}"]

    if history.empty:
        warnings.append(
            f"CenovĂˇ historie pro {ticker_symbol.upper()} za obdobĂ­ {period} nenĂ­ v Yahoo Finance dostupnĂˇ."
        )
        return pd.DataFrame(), warnings

    if "Close" not in history.columns:
        warnings.append(
            f"Yahoo Finance nevrĂˇtil sloupec Close pro {ticker_symbol.upper()} za obdobĂ­ {period}."
        )
        return pd.DataFrame(), warnings

    close_history = history[["Close"]].copy()
    close_history.index = pd.to_datetime(close_history.index)
    close_history.rename(columns={"Close": "Close Price"}, inplace=True)
    return close_history, warnings


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
    five_year_avg_dividend_yield = _normalize_number(_safe_get(info, "fiveYearAvgDividendYield"))
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
    capital_expenditures = _find_statement_value(
        cashflow,
        "Capital Expenditure",
        "Capital Expenditures",
        "Capital Expenditure Reported",
    )
    free_cash_flow = _find_statement_value(cashflow, "Free Cash Flow")
    if free_cash_flow is None and operating_cash_flow is not None and capital_expenditures is not None:
        free_cash_flow = (
            operating_cash_flow + capital_expenditures
            if capital_expenditures < 0
            else operating_cash_flow - capital_expenditures
        )

    if trailing_eps is None and current_price is not None and trailing_pe not in (None, 0):
        trailing_eps = current_price / trailing_pe

    for value, label in (
        (current_price, "Current Price"),
        (market_cap, "Market Cap"),
        (shares_outstanding, "Shares Outstanding"),
        (trailing_pe, "Trailing P/E"),
        (last_year_dividend_yield, "Last Year Dividend Yield"),
        (five_year_avg_dividend_yield, "5Y Average Dividend Yield"),
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
        warnings=warnings,
        raw_info=info,
    )
