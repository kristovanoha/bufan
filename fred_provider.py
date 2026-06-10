from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from functools import lru_cache
from json import load
from typing import Callable
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import urlopen

import pandas as pd


FRED_API_BASE = "https://api.stlouisfed.org/fred"


@dataclass(frozen=True, slots=True)
class FredSeriesDefinition:
    series_id: str
    title: str
    description: str
    category: str
    observation_start: str = "1990-01-01"


@dataclass(slots=True)
class FredSeriesResult:
    definition: FredSeriesDefinition
    units: str
    frequency: str
    notes: str
    observations: pd.DataFrame
    latest_value: float | None
    latest_date: pd.Timestamp | None


FRED_SERIES_DEFINITIONS = [
    FredSeriesDefinition(
        series_id="FEDFUNDS",
        title="Fed Funds Rate",
        description="Zakladni kratkodoba sazba Fedu. Rust sazby obvykle zdrazuje uvery, tlumi inflaci a zvysuje naroky na valuace akcii. Pokles sazby naopak casto znamena podporu ekonomiky a levnejsi financovani.",
        category="Menova politika a inflace",
    ),
    FredSeriesDefinition(
        series_id="CPIAUCSL",
        title="CPI",
        description="Index spotrebitelskych cen v USA. Ukazuje dlouhodoby vyvoj cenove hladiny, ne primo mezirocni inflaci. Strmejsi rust indexu znamena silnejsi inflacni tlak, ktery muze nutit Fed drzet vyssi sazby.",
        category="Menova politika a inflace",
    ),
    FredSeriesDefinition(
        series_id="T10YIE",
        title="10Y Breakeven Inflation",
        description="Trzni odhad prumerne inflace na dalsich 10 let. Vyssi hodnota znamena, ze trh ocenuje vyssi dlouhodoba inflacni ocekavani, coz muze tlacit nahoru vynosy dluhopisu a diskontni sazby.",
        category="Menova politika a inflace",
        observation_start="2003-01-01",
    ),
    FredSeriesDefinition(
        series_id="DGS10",
        title="10Y Treasury Yield",
        description="Vynos 10leteho americkeho statniho dluhopisu. Je dulezity pro valuace, protoze predstavuje referencni vynos bezpecnejsich aktiv. Kdyz roste, akcie casto potrebuji silnejsi zisky, aby ospravedlnily vysoke oceneni.",
        category="Menova politika a inflace",
        observation_start="2000-01-01",
    ),
    FredSeriesDefinition(
        series_id="T10Y2Y",
        title="10Y-2Y Yield Spread",
        description="Rozdil mezi 10letym a 2letym vynosem americkych dluhopisu. Kladna hodnota je bezne prostredi, zaporna hodnota znamena inverzni vynosovou krivku, kterou investori casto sleduji jako varovani pred zpomalenim ekonomiky.",
        category="Menova politika a inflace",
        observation_start="2000-01-01",
    ),
    FredSeriesDefinition(
        series_id="GDP",
        title="US GDP",
        description="Nominalni HDP USA v aktualnich cenach. Ukazuje velikost ekonomiky vcetne vlivu inflace. Je uzitecny pro kontext dluhu, trzeb firem a celkoveho ekonomickeho prostredi.",
        category="Realna ekonomika",
        observation_start="1990-01-01",
    ),
    FredSeriesDefinition(
        series_id="GDPC1",
        title="Real GDP",
        description="Realny HDP ocisteny o inflaci. Lepe ukazuje skutecny rust ekonomicke aktivity. Slabnuti nebo pokles muze signalizovat horsi prostredi pro zisky firem.",
        category="Realna ekonomika",
        observation_start="1990-01-01",
    ),
    FredSeriesDefinition(
        series_id="UNRATE",
        title="Unemployment Rate",
        description="Mira nezamestnanosti U-3. Nizka hodnota obvykle znamena silny pracovni trh, ale muze podporovat mzdove a inflacni tlaky. Rychly rust nezamestnanosti byva varovanim pred ekonomickym zpomalenim.",
        category="Realna ekonomika",
    ),
    FredSeriesDefinition(
        series_id="SAHMREALTIME",
        title="Sahm Recession Indicator",
        description="Indikator zalozeny na zhorseni nezamestnanosti. Kdyz prudce roste, signalizuje, ze pracovni trh se rychle lame. Pouziva se jako prakticky vcasny recesni signal.",
        category="Realna ekonomika",
        observation_start="1960-01-01",
    ),
    FredSeriesDefinition(
        series_id="USREC",
        title="NBER Recession Indicator",
        description="Historicky indikator recesi podle NBER. Hodnota 1 znamena obdobi recese, hodnota 0 normalni obdobi. Pomaha videt, jak se ostatni ukazatele chovaly pred recesemi a behem nich.",
        category="Realna ekonomika",
        observation_start="1960-01-01",
    ),
    FredSeriesDefinition(
        series_id="M1SL",
        title="M1 Money Supply",
        description="Uzsi penezni zasoba, tedy nejlikvidnejsi forma penez v ekonomice. Prudke zmeny mohou ukazovat zmeny v likvidite, ale po metodickych zmenach je vhodne ji cist opatrne.",
        category="Penezni zasoba a dluh",
    ),
    FredSeriesDefinition(
        series_id="M2SL",
        title="M2 Money Supply",
        description="Sirsi penezni zasoba zahrnujici hotovost, bezne vklady a dalsi likvidni ulozeni penez. Rust M2 muze znamenat vice likvidity v systemu, pokles naopak utahovani financnich podminek.",
        category="Penezni zasoba a dluh",
    ),
    FredSeriesDefinition(
        series_id="WALCL",
        title="Fed Balance Sheet",
        description="Celkovy objem aktiv Federal Reserve. Rust rozvahy obvykle znamena dodavani likvidity do systemu, pokles naopak stahovani likvidity. To muze ovlivnovat ochotu trhu platit vyssi valuace.",
        category="Penezni zasoba a dluh",
        observation_start="2003-01-01",
    ),
    FredSeriesDefinition(
        series_id="GFDEBTN",
        title="Federal Debt",
        description="Celkovy federalni dluh USA v absolutni hodnote. Samotne cislo roste dlouhodobe, proto je dobre ho cist spolecne s HDP a urokovymi sazbami.",
        category="Penezni zasoba a dluh",
        observation_start="1990-01-01",
    ),
    FredSeriesDefinition(
        series_id="GFDEGDQ188S",
        title="Federal Debt to GDP",
        description="Federalni dluh jako podil na HDP. Dava dluh do kontextu velikosti ekonomiky. Vyssi pomer muze znamenat mensi fiskalni prostor a vyssi citlivost rozpoctu na urokove sazby.",
        category="Penezni zasoba a dluh",
        observation_start="1990-01-01",
    ),
]


def _fred_request(path: str, api_key: str, **params) -> dict:
    query = urlencode(
        {
            "api_key": api_key,
            "file_type": "json",
            **params,
        }
    )
    url = f"{FRED_API_BASE}/{path}?{query}"

    try:
        with urlopen(url, timeout=20) as response:
            return load(response)
    except HTTPError as exc:
        message = exc.read().decode("utf-8", errors="ignore")
        raise RuntimeError(f"FRED API vratilo chybu {exc.code}. {message}") from exc
    except URLError as exc:
        raise RuntimeError("Nepodarilo se pripojit k FRED API.") from exc


@lru_cache(maxsize=64)
def _fetch_series_metadata(api_key: str, series_id: str) -> dict:
    payload = _fred_request("series", api_key, series_id=series_id)
    series_rows = payload.get("seriess", [])
    if not series_rows:
        raise RuntimeError(f"Serie {series_id} nebyla ve FRED nalezena.")
    return series_rows[0]


def _fetch_series_observations(api_key: str, definition: FredSeriesDefinition) -> pd.DataFrame:
    payload = _fred_request(
        "series/observations",
        api_key,
        series_id=definition.series_id,
        observation_start=definition.observation_start,
        sort_order="asc",
    )

    rows: list[dict[str, object]] = []
    for observation in payload.get("observations", []):
        value = observation.get("value")
        if value in (None, "."):
            continue
        try:
            numeric_value = float(value)
        except (TypeError, ValueError):
            continue
        rows.append(
            {
                "date": pd.to_datetime(observation["date"]),
                "value": numeric_value,
            }
        )

    if not rows:
        raise RuntimeError(f"Serie {definition.series_id} nema dostupna numericka data.")

    frame = pd.DataFrame(rows).set_index("date")
    return frame


def fetch_fred_series(api_key: str, definition: FredSeriesDefinition) -> FredSeriesResult:
    metadata = _fetch_series_metadata(api_key, definition.series_id)
    observations = _fetch_series_observations(api_key, definition)
    latest_date = observations.index.max()
    latest_value = float(observations.iloc[-1]["value"]) if not observations.empty else None

    return FredSeriesResult(
        definition=definition,
        units=metadata.get("units", "N/A"),
        frequency=metadata.get("frequency", "N/A"),
        notes=metadata.get("notes", "") or "",
        observations=observations,
        latest_value=latest_value,
        latest_date=latest_date,
    )


def fetch_macro_dashboard(
    api_key: str,
    progress_callback: Callable[[list[FredSeriesResult], list[str], int, int], None] | None = None,
) -> tuple[list[FredSeriesResult], list[str]]:
    results: list[FredSeriesResult] = []
    errors: list[str] = []
    total = len(FRED_SERIES_DEFINITIONS)

    with ThreadPoolExecutor(max_workers=min(8, total)) as executor:
        future_map = {
            executor.submit(fetch_fred_series, api_key, definition): definition
            for definition in FRED_SERIES_DEFINITIONS
        }

        done = 0
        for future in as_completed(future_map):
            definition = future_map[future]
            try:
                results.append(future.result())
            except Exception as exc:
                error_text = str(exc)
                if "api_key is not registered" in error_text or "variable api_key" in error_text:
                    return [], ["FRED API key neni registrovany nebo je neplatny."]
                errors.append(f"{definition.title} ({definition.series_id}): {error_text}")
            done += 1
            ordered_results = sorted(
                results,
                key=lambda item: FRED_SERIES_DEFINITIONS.index(item.definition),
            )
            if progress_callback is not None:
                progress_callback(ordered_results, list(errors), done, total)

    results.sort(key=lambda item: FRED_SERIES_DEFINITIONS.index(item.definition))

    return results, errors
