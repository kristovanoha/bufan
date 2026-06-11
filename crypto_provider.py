from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from json import load
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import pandas as pd
import yfinance as yf


COINGECKO_API_BASE = "https://api.coingecko.com/api/v3"
MEMPOOL_API_BASE = "https://mempool.space/api"
ALTERNATIVE_API_BASE = "https://api.alternative.me"


@dataclass(slots=True)
class BitcoinMarketData:
    price_usd: float | None = None
    market_cap_usd: float | None = None
    volume_24h_usd: float | None = None
    price_change_24h_pct: float | None = None
    price_change_7d_pct: float | None = None
    price_change_30d_pct: float | None = None
    price_change_1y_pct: float | None = None
    ath_usd: float | None = None
    ath_change_pct: float | None = None
    last_updated: str | None = None
    history: pd.DataFrame = field(default_factory=pd.DataFrame)


@dataclass(slots=True)
class BitcoinNetworkData:
    fastest_fee: float | None = None
    half_hour_fee: float | None = None
    hour_fee: float | None = None
    economy_fee: float | None = None
    minimum_fee: float | None = None
    tip_height: int | None = None
    mempool_count: int | None = None
    mempool_vsize: float | None = None
    mempool_total_fee: float | None = None


@dataclass(slots=True)
class FearGreedData:
    value: int | None = None
    classification: str | None = None
    updated_at: str | None = None
    history: pd.DataFrame = field(default_factory=pd.DataFrame)


@dataclass(slots=True)
class CryptoDashboard:
    market: BitcoinMarketData | None = None
    network: BitcoinNetworkData | None = None
    sentiment: FearGreedData | None = None
    errors: list[str] = field(default_factory=list)


def _request_json(url: str) -> dict | list:
    request = Request(url, headers={"User-Agent": "BuffettAnalyzer/1.0"})
    try:
        with urlopen(request, timeout=20) as response:
            return load(response)
    except HTTPError as exc:
        message = exc.read().decode("utf-8", errors="ignore")
        raise RuntimeError(f"API vratilo chybu {exc.code}. {message}") from exc
    except URLError as exc:
        raise RuntimeError("Nepodarilo se pripojit k API.") from exc


def _normalize_number(value) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _normalize_int(value) -> int | None:
    number = _normalize_number(value)
    if number is None:
        return None
    return int(number)


def fetch_bitcoin_price_history(days: int | str = 365) -> pd.DataFrame:
    if days == "max":
        period = "max"
    else:
        period = f"{max(int(days), 1)}d"

    ticker = yf.Ticker("BTC-USD")
    history = ticker.history(period=period, interval="1d", auto_adjust=False)
    if history.empty:
        return pd.DataFrame()

    frame = history.reset_index()
    date_column = "Date" if "Date" in frame.columns else frame.columns[0]
    frame = frame.rename(columns={date_column: "date", "Close": "Cena BTC"})
    frame = frame[["date", "Cena BTC"]].dropna()
    frame["date"] = pd.to_datetime(frame["date"]).dt.tz_localize(None)
    return frame.sort_values("date").set_index("date")


def fetch_bitcoin_market_data(days: int | str = 365) -> BitcoinMarketData:
    coin_params = urlencode(
        {
            "localization": "false",
            "tickers": "false",
            "market_data": "true",
            "community_data": "false",
            "developer_data": "false",
            "sparkline": "false",
        }
    )
    coin_url = f"{COINGECKO_API_BASE}/coins/bitcoin?{coin_params}"
    coin_payload = _request_json(coin_url)
    market_data = coin_payload.get("market_data", {}) if isinstance(coin_payload, dict) else {}
    history = fetch_bitcoin_price_history(days)

    return BitcoinMarketData(
        price_usd=_normalize_number(market_data.get("current_price", {}).get("usd")),
        market_cap_usd=_normalize_number(market_data.get("market_cap", {}).get("usd")),
        volume_24h_usd=_normalize_number(market_data.get("total_volume", {}).get("usd")),
        price_change_24h_pct=_normalize_number(market_data.get("price_change_percentage_24h")),
        price_change_7d_pct=_normalize_number(market_data.get("price_change_percentage_7d")),
        price_change_30d_pct=_normalize_number(market_data.get("price_change_percentage_30d")),
        price_change_1y_pct=_normalize_number(market_data.get("price_change_percentage_1y")),
        ath_usd=_normalize_number(market_data.get("ath", {}).get("usd")),
        ath_change_pct=_normalize_number(market_data.get("ath_change_percentage", {}).get("usd")),
        last_updated=market_data.get("last_updated"),
        history=history,
    )


def fetch_bitcoin_network_data() -> BitcoinNetworkData:
    fees_payload = _request_json(f"{MEMPOOL_API_BASE}/v1/fees/recommended")
    tip_height_payload = _request_json(f"{MEMPOOL_API_BASE}/blocks/tip/height")
    mempool_payload = _request_json(f"{MEMPOOL_API_BASE}/mempool")

    fees = fees_payload if isinstance(fees_payload, dict) else {}
    mempool = mempool_payload if isinstance(mempool_payload, dict) else {}

    return BitcoinNetworkData(
        fastest_fee=_normalize_number(fees.get("fastestFee")),
        half_hour_fee=_normalize_number(fees.get("halfHourFee")),
        hour_fee=_normalize_number(fees.get("hourFee")),
        economy_fee=_normalize_number(fees.get("economyFee")),
        minimum_fee=_normalize_number(fees.get("minimumFee")),
        tip_height=_normalize_int(tip_height_payload),
        mempool_count=_normalize_int(mempool.get("count")),
        mempool_vsize=_normalize_number(mempool.get("vsize")),
        mempool_total_fee=_normalize_number(mempool.get("total_fee")),
    )


def fetch_fear_greed_data(limit: int = 30) -> FearGreedData:
    payload = _request_json(f"{ALTERNATIVE_API_BASE}/fng/?{urlencode({'limit': limit, 'format': 'json'})}")
    rows = payload.get("data", []) if isinstance(payload, dict) else []

    history_rows = []
    for row in rows:
        timestamp = _normalize_int(row.get("timestamp"))
        value = _normalize_int(row.get("value"))
        if timestamp is None or value is None:
            continue
        history_rows.append(
            {
                "date": pd.to_datetime(timestamp, unit="s"),
                "Fear & Greed": value,
                "classification": row.get("value_classification"),
            }
        )

    history = pd.DataFrame(history_rows)
    if not history.empty:
        history = history.sort_values("date").set_index("date")

    latest = rows[0] if rows else {}
    updated_at = None
    latest_timestamp = _normalize_int(latest.get("timestamp"))
    if latest_timestamp is not None:
        updated_at = pd.to_datetime(latest_timestamp, unit="s").strftime("%d.%m.%Y")

    return FearGreedData(
        value=_normalize_int(latest.get("value")),
        classification=latest.get("value_classification"),
        updated_at=updated_at,
        history=history,
    )


def fetch_crypto_dashboard(days: int | str = 365) -> CryptoDashboard:
    dashboard = CryptoDashboard()
    tasks = {
        "market": lambda: fetch_bitcoin_market_data(days),
        "network": fetch_bitcoin_network_data,
        "sentiment": fetch_fear_greed_data,
    }

    with ThreadPoolExecutor(max_workers=len(tasks)) as executor:
        future_map = {executor.submit(task): name for name, task in tasks.items()}
        for future in as_completed(future_map):
            name = future_map[future]
            try:
                setattr(dashboard, name, future.result())
            except Exception as exc:
                dashboard.errors.append(f"{name}: {exc}")

    return dashboard
