from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from json import load
from typing import Callable
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import pandas as pd


CSU_DATA_API_BASE = "https://data.csu.gov.cz/api/dotaz/v1"
CNB_REPO_HISTORY_URL = "https://www.cnb.cz/cs/casto-kladene-dotazy/.galleries/vyvoj_repo_historie.txt"
CNB_PRIBOR_YEAR_URL = (
    "https://www.cnb.cz/cs/financni-trhy/penezni-trh/pribor/"
    "fixing-urokovych-sazeb-na-mezibankovnim-trhu-depozit-pribor/rok.txt"
)
CNB_FX_YEAR_URL = (
    "https://www.cnb.cz/cs/financni-trhy/devizovy-trh/kurzy-devizoveho-trhu/"
    "kurzy-devizoveho-trhu/rok.txt"
)
EUROSTAT_API_BASE = "https://ec.europa.eu/eurostat/api/dissemination/statistics/1.0/data"


@dataclass(frozen=True, slots=True)
class CzechSeriesDefinition:
    key: str
    title: str
    category: str
    source: str
    source_url: str
    unit: str
    description: str
    interpretation: str


@dataclass(slots=True)
class CzechSeriesResult:
    definition: CzechSeriesDefinition
    observations: pd.DataFrame
    latest_value: float | None
    latest_date: pd.Timestamp | None
    primary_column: str | None


CZECH_SERIES_DEFINITIONS = [
    CzechSeriesDefinition(
        key="csu_inflation",
        title="Inflace podle ČSÚ",
        category="Inflace",
        source="ČSÚ DataStat",
        source_url="https://data.csu.gov.cz/api/dotaz/v1/data/vybery/CEN0101HT02?format=JSON_STAT",
        unit="%",
        description=(
            "Oficiální česká inflace z Českého statistického úřadu. Graf ukazuje meziroční inflaci, "
            "meziměsíční změnu a průměrnou roční míru inflace."
        ),
        interpretation=(
            "Rychlý růst meziroční inflace ukazuje tlak na kupní sílu a často vede k přísnější politice ČNB. "
            "Meziměsíční změna pomáhá zachytit nový inflační impuls dříve než roční číslo."
        ),
    ),
    CzechSeriesDefinition(
        key="eurostat_hicp_compare",
        title="HICP inflace: ČR vs Německo vs EU",
        category="Inflace",
        source="Eurostat",
        source_url="https://ec.europa.eu/eurostat/web/user-guides/data-browser/api-data-access/api-introduction",
        unit="%",
        description=(
            "Harmonizovaná inflace HICP je vhodná pro mezinárodní srovnání. Tady srovnává Česko, Německo "
            "a EU stejnou metodikou Eurostatu."
        ),
        interpretation=(
            "Když česká inflace zrychluje zároveň s Německem a EU, může jít o širší evropský tlak. "
            "Když ČR výrazně utíká nahoru sama, bývá důležitější domácí faktor: mzdy, kurz, poptávka nebo regulované ceny."
        ),
    ),
    CzechSeriesDefinition(
        key="cnb_repo_rate",
        title="2T repo sazba ČNB",
        category="Sazby a měna",
        source="ČNB",
        source_url=CNB_REPO_HISTORY_URL,
        unit="%",
        description="Základní měnověpolitická sazba ČNB. Ovlivňuje krátkodobé tržní sazby, úvěry, vklady a kurz koruny.",
        interpretation=(
            "Vyšší repo sazba obvykle zdražuje financování, tlumí poptávku a působí proti inflaci se zpožděním. "
            "Pro akcie a nemovitosti je to často přísnější prostředí."
        ),
    ),
    CzechSeriesDefinition(
        key="cnb_pribor_3m",
        title="3M PRIBOR",
        category="Sazby a měna",
        source="ČNB",
        source_url="https://www.cnb.cz/cs/financni-trhy/penezni-trh/pribor/fixing-urokovych-sazeb-na-mezibankovnim-trhu-depozit-pribor/index.html",
        unit="%",
        description="Tříměsíční mezibankovní sazba PRIBOR. Je praktický obraz toho, za jaké krátkodobé sazby si banky oceňují koruny.",
        interpretation=(
            "PRIBOR rychle reaguje na repo sazbu a očekávání trhu. Vysoký PRIBOR znamená dražší úvěry pro firmy i domácnosti "
            "a může brzdit ekonomiku."
        ),
    ),
    CzechSeriesDefinition(
        key="cnb_fx_rates",
        title="Kurz koruny: EUR/CZK a USD/CZK",
        category="Sazby a měna",
        source="ČNB",
        source_url="https://www.cnb.cz/cs/financni-trhy/devizovy-trh/kurzy-devizoveho-trhu/kurzy-devizoveho-trhu/",
        unit="CZK",
        description="Denní kurzy ČNB ukazují, kolik korun stojí jedno euro a jeden dolar.",
        interpretation=(
            "Slabší koruna zdražuje dovoz a může přidávat inflační tlak, silnější koruna ho naopak tlumí. "
            "U exportérů a firem s eurovými tržbami ale dopad nemusí být jednostranný."
        ),
    ),
    CzechSeriesDefinition(
        key="eurostat_gdp_compare",
        title="Reálný růst HDP: ČR vs Německo vs EU",
        category="Reálná ekonomika",
        source="Eurostat",
        source_url="https://ec.europa.eu/eurostat/web/user-guides/data-browser/api-data-access/api-introduction",
        unit="%",
        description="Meziroční růst reálného HDP počítaný z kvartálních dat Eurostatu ve stálých cenách.",
        interpretation=(
            "Slábnoucí nebo záporný růst HDP zhoršuje prostředí pro zisky firem. Pokud zároveň roste nezaměstnanost "
            "a klesá průmysl nebo maloobchod, riziko tvrdšího zpomalení je vyšší."
        ),
    ),
    CzechSeriesDefinition(
        key="eurostat_industry",
        title="Průmyslová výroba ČR",
        category="Reálná ekonomika",
        source="Eurostat",
        source_url="https://ec.europa.eu/eurostat/web/user-guides/data-browser/api-data-access/api-introduction",
        unit="index 2021=100",
        description="Měsíční index průmyslové produkce, sezonně a kalendářně očištěný.",
        interpretation=(
            "Průmysl je pro ČR velmi důležitý. Dlouhodobý pokles indexu může signalizovat slabší exportní poptávku, "
            "tlak na marže a horší podmínky pro cyklické firmy."
        ),
    ),
    CzechSeriesDefinition(
        key="eurostat_retail",
        title="Maloobchodní tržby ČR",
        category="Reálná ekonomika",
        source="Eurostat",
        source_url="https://ec.europa.eu/eurostat/web/user-guides/data-browser/api-data-access/api-introduction",
        unit="index 2021=100",
        description="Objem maloobchodních tržeb bez motorových vozidel, sezonně a kalendářně očištěný.",
        interpretation=(
            "Maloobchod ukazuje kondici spotřebitele. Slabé tržby často znamenají tlak na firmy závislé na domácí poptávce "
            "a mohou ukazovat dopad vysokých sazeb nebo inflace na domácnosti."
        ),
    ),
    CzechSeriesDefinition(
        key="eurostat_construction",
        title="Stavebnictví ČR",
        category="Reálná ekonomika",
        source="Eurostat",
        source_url="https://ec.europa.eu/eurostat/web/user-guides/data-browser/api-data-access/api-introduction",
        unit="index 2021=100",
        description="Měsíční index stavební produkce, sezonně a kalendářně očištěný.",
        interpretation=(
            "Stavebnictví je citlivé na úrokové sazby a dostupnost financování. Pokles může předbíhat slabost v realitním trhu, "
            "investicích a navazujících odvětvích."
        ),
    ),
    CzechSeriesDefinition(
        key="eurostat_unemployment_compare",
        title="Nezaměstnanost: ČR vs Německo vs EU",
        category="Trh práce a mzdy",
        source="Eurostat",
        source_url="https://ec.europa.eu/eurostat/web/user-guides/data-browser/api-data-access/api-introduction",
        unit="%",
        description="Měsíční harmonizovaná míra nezaměstnanosti podle Eurostatu.",
        interpretation=(
            "Rychlý růst nezaměstnanosti je varování před zpomalením ekonomiky. Nízká nezaměstnanost naopak může držet mzdy vysoko "
            "a podporovat domácí inflační tlaky."
        ),
    ),
    CzechSeriesDefinition(
        key="eurostat_wages",
        title="Mzdové náklady ČR",
        category="Trh práce a mzdy",
        source="Eurostat",
        source_url="https://ec.europa.eu/eurostat/web/user-guides/data-browser/api-data-access/api-introduction",
        unit="%",
        description="Meziroční změna indexu mezd a platů v nákladech práce.",
        interpretation=(
            "Silný růst mezd může podporovat spotřebu, ale pokud běží rychleji než produktivita, může držet vyšší inflaci ve službách "
            "a ztěžovat ČNB snižování sazeb."
        ),
    ),
]


def _request(url: str) -> Request:
    return Request(url, headers={"User-Agent": "BuffettAnalyzer/1.0"})


def _load_json(url: str) -> dict:
    try:
        with urlopen(_request(url), timeout=25) as response:
            return load(response)
    except HTTPError as exc:
        message = exc.read().decode("utf-8", errors="ignore")
        raise RuntimeError(f"API vratilo chybu {exc.code}. {message}") from exc
    except URLError as exc:
        raise RuntimeError("Nepodarilo se pripojit k datovemu zdroji.") from exc


def _load_text(url: str) -> str:
    try:
        with urlopen(_request(url), timeout=25) as response:
            return response.read().decode("utf-8-sig", errors="ignore")
    except HTTPError as exc:
        message = exc.read().decode("utf-8", errors="ignore")
        raise RuntimeError(f"API vratilo chybu {exc.code}. {message}") from exc
    except URLError as exc:
        raise RuntimeError("Nepodarilo se pripojit k datovemu zdroji.") from exc


def _parse_time_code(code: str) -> pd.Timestamp:
    if "-Q" in code:
        return pd.Period(code, freq="Q").to_timestamp()
    if "Q" in code and len(code) >= 6:
        return pd.Period(code.replace("Q", "-Q"), freq="Q").to_timestamp()
    if len(code) == 7 and code[4] == "-":
        return pd.to_datetime(f"{code}-01")
    if len(code) == 4 and code.isdigit():
        return pd.to_datetime(f"{code}-01-01")
    return pd.to_datetime(code)


def _jsonstat_records(payload: dict) -> list[dict[str, object]]:
    dimensions = payload.get("id", [])
    sizes = payload.get("size", [])
    values = payload.get("value", {})
    if not dimensions or not sizes or not values:
        return []

    position_to_code: dict[str, dict[int, str]] = {}
    labels: dict[str, dict[str, str]] = {}
    for dimension in dimensions:
        category = payload["dimension"][dimension].get("category", {})
        index = category.get("index", {})
        label_map = category.get("label", {})
        position_to_code[dimension] = {position: code for code, position in index.items()}
        labels[dimension] = label_map

    records: list[dict[str, object]] = []
    if isinstance(values, list):
        value_items = enumerate(values)
    else:
        value_items = ((int(index), value) for index, value in values.items())

    for flat_index, raw_value in value_items:
        if raw_value is None:
            continue
        try:
            value = float(raw_value)
        except (TypeError, ValueError):
            continue

        remaining = int(flat_index)
        positions = [0] * len(sizes)
        for index in range(len(sizes) - 1, -1, -1):
            size = sizes[index]
            if size == 0:
                break
            positions[index] = remaining % size
            remaining //= size

        record: dict[str, object] = {"value": value}
        for dimension, position in zip(dimensions, positions):
            code = position_to_code.get(dimension, {}).get(position)
            if code is None:
                continue
            record[dimension] = code
            record[f"{dimension}_label"] = labels.get(dimension, {}).get(code, code)
        records.append(record)

    return records


def _frame_from_jsonstat(payload: dict, value_column: str, metric_contains: str | None = None) -> pd.DataFrame:
    records = _jsonstat_records(payload)
    if not records:
        return pd.DataFrame()

    time_dimensions = payload.get("role", {}).get("time", [])
    time_dimension = time_dimensions[0] if time_dimensions else "time"
    rows: list[dict[str, object]] = []
    for record in records:
        if metric_contains:
            metric_label = str(record.get("IndicatorType_label", ""))
            if metric_contains.lower() not in metric_label.lower():
                continue
        time_code = record.get(time_dimension)
        if not time_code:
            continue
        rows.append({"date": _parse_time_code(str(time_code)), value_column: record["value"]})

    if not rows:
        return pd.DataFrame()
    return pd.DataFrame(rows).drop_duplicates("date").set_index("date").sort_index()


def _latest_from_frame(frame: pd.DataFrame, primary_column: str | None) -> tuple[float | None, pd.Timestamp | None]:
    if frame.empty:
        return None, None
    column = primary_column or frame.columns[0]
    series = frame[column].dropna()
    if series.empty:
        return None, None
    return float(series.iloc[-1]), pd.Timestamp(series.index[-1])


def _build_result(
    definition: CzechSeriesDefinition,
    observations: pd.DataFrame,
    primary_column: str | None = None,
) -> CzechSeriesResult:
    if observations.empty:
        raise RuntimeError("Datovy zdroj nevratil zadna numericka data.")
    latest_value, latest_date = _latest_from_frame(observations, primary_column)
    return CzechSeriesResult(
        definition=definition,
        observations=observations,
        latest_value=latest_value,
        latest_date=latest_date,
        primary_column=primary_column or observations.columns[0],
    )


def _fetch_csu_inflation(definition: CzechSeriesDefinition) -> CzechSeriesResult:
    url = f"{CSU_DATA_API_BASE}/data/vybery/CEN0101HT02?format=JSON_STAT"
    payload = _load_json(url)
    metrics = {
        "Mezirocni inflace": "ke stejnému měsíci předchozího roku",
        "Mezimesicni inflace": "k předchozímu měsíci",
        "Prumerna rocni inflace": "průměrného ročního indexu",
    }
    frames = [
        _frame_from_jsonstat(payload, column, metric_contains=phrase)
        for column, phrase in metrics.items()
    ]
    frame = pd.concat(frames, axis=1).sort_index()
    return _build_result(definition, frame, "Mezirocni inflace")


def _eurostat_url(dataset: str, params: dict[str, str]) -> str:
    return f"{EUROSTAT_API_BASE}/{dataset}?{urlencode({**params, 'lang': 'en'})}"


def _fetch_eurostat_series(dataset: str, params: dict[str, str], column: str) -> pd.DataFrame:
    payload = _load_json(_eurostat_url(dataset, params))
    return _frame_from_jsonstat(payload, column)


def _fetch_eurostat_comparison(
    definition: CzechSeriesDefinition,
    dataset: str,
    params: dict[str, str],
    transform: Callable[[pd.Series], pd.Series] | None = None,
) -> CzechSeriesResult:
    countries = {"CZ": "ČR", "DE": "Německo", "EU27_2020": "EU"}
    frames: list[pd.DataFrame] = []
    for geo, label in countries.items():
        frame = _fetch_eurostat_series(dataset, {**params, "geo": geo}, label)
        if transform is not None and not frame.empty:
            frame[label] = transform(frame[label])
            frame = frame.dropna()
        frames.append(frame)
    combined = pd.concat(frames, axis=1).sort_index()
    return _build_result(definition, combined, "ČR")


def _fetch_eurostat_czech_only(
    definition: CzechSeriesDefinition,
    dataset: str,
    params: dict[str, str],
    column: str,
) -> CzechSeriesResult:
    frame = _fetch_eurostat_series(dataset, {**params, "geo": "CZ"}, column)
    return _build_result(definition, frame, column)


def _fetch_cnb_repo(definition: CzechSeriesDefinition) -> CzechSeriesResult:
    text = _load_text(CNB_REPO_HISTORY_URL)
    rows: list[dict[str, object]] = []
    for token in text.replace("\n", " ").split():
        if "|" not in token or token.startswith("PLATNA_OD"):
            continue
        date_text, value_text = token.split("|", 1)
        if not date_text.isdigit() or not value_text:
            continue
        rows.append(
            {
                "date": pd.to_datetime(date_text, format="%Y%m%d"),
                "2T repo sazba": float(value_text.replace(",", ".")),
            }
        )
    frame = pd.DataFrame(rows).set_index("date").sort_index()
    return _build_result(definition, frame, "2T repo sazba")


def _parse_cnb_decimal(value: str) -> float | None:
    value = value.strip().replace(",", ".")
    if not value:
        return None
    try:
        return float(value)
    except ValueError:
        return None


def _fetch_cnb_pribor(definition: CzechSeriesDefinition) -> CzechSeriesResult:
    current_year = pd.Timestamp.now().year
    rows: list[dict[str, object]] = []
    for year in range(2015, current_year + 1):
        text = _load_text(f"{CNB_PRIBOR_YEAR_URL}?year={year}")
        for line in text.splitlines()[2:]:
            parts = line.split("|")
            if len(parts) <= 12:
                continue
            value = _parse_cnb_decimal(parts[12])
            if value is None:
                continue
            rows.append({"date": pd.to_datetime(parts[0], format="%d.%m.%Y"), "3M PRIBOR": value})
    frame = pd.DataFrame(rows).drop_duplicates("date").set_index("date").sort_index()
    return _build_result(definition, frame, "3M PRIBOR")


def _fetch_cnb_fx(definition: CzechSeriesDefinition) -> CzechSeriesResult:
    current_year = pd.Timestamp.now().year
    rows: list[dict[str, object]] = []
    for year in range(2015, current_year + 1):
        text = _load_text(f"{CNB_FX_YEAR_URL}?rok={year}")
        lines = text.splitlines()
        if not lines:
            continue
        header = lines[0].split("|")
        try:
            eur_index = header.index("1 EUR")
            usd_index = header.index("1 USD")
        except ValueError:
            continue
        for line in lines[1:]:
            parts = line.split("|")
            if len(parts) <= max(eur_index, usd_index):
                continue
            eur = _parse_cnb_decimal(parts[eur_index])
            usd = _parse_cnb_decimal(parts[usd_index])
            if eur is None or usd is None:
                continue
            rows.append(
                {
                    "date": pd.to_datetime(parts[0], format="%d.%m.%Y"),
                    "EUR/CZK": eur,
                    "USD/CZK": usd,
                }
            )
    frame = pd.DataFrame(rows).drop_duplicates("date").set_index("date").sort_index()
    return _build_result(definition, frame, "EUR/CZK")


def fetch_czech_series(definition: CzechSeriesDefinition) -> CzechSeriesResult:
    if definition.key == "csu_inflation":
        return _fetch_csu_inflation(definition)
    if definition.key == "eurostat_hicp_compare":
        return _fetch_eurostat_comparison(
            definition,
            "prc_hicp_manr",
            {"coicop": "CP00", "unit": "RCH_A"},
        )
    if definition.key == "cnb_repo_rate":
        return _fetch_cnb_repo(definition)
    if definition.key == "cnb_pribor_3m":
        return _fetch_cnb_pribor(definition)
    if definition.key == "cnb_fx_rates":
        return _fetch_cnb_fx(definition)
    if definition.key == "eurostat_gdp_compare":
        return _fetch_eurostat_comparison(
            definition,
            "namq_10_gdp",
            {"na_item": "B1GQ", "s_adj": "SCA", "unit": "CLV10_MEUR"},
            transform=lambda series: series.pct_change(4) * 100,
        )
    if definition.key == "eurostat_industry":
        return _fetch_eurostat_czech_only(
            definition,
            "sts_inpr_m",
            {"indic_bt": "PRD", "nace_r2": "B-D", "s_adj": "SCA", "unit": "I21"},
            "Prumyslova vyroba",
        )
    if definition.key == "eurostat_retail":
        return _fetch_eurostat_czech_only(
            definition,
            "sts_trtu_m",
            {"indic_bt": "VOL_SLS", "nace_r2": "G47", "s_adj": "SCA", "unit": "I21"},
            "Maloobchodni trzby",
        )
    if definition.key == "eurostat_construction":
        return _fetch_eurostat_czech_only(
            definition,
            "sts_copr_m",
            {"indic_bt": "PRD", "nace_r2": "F", "s_adj": "SCA", "unit": "I21"},
            "Stavebnictvi",
        )
    if definition.key == "eurostat_unemployment_compare":
        return _fetch_eurostat_comparison(
            definition,
            "une_rt_m",
            {"sex": "T", "age": "TOTAL", "unit": "PC_ACT", "s_adj": "SA"},
        )
    if definition.key == "eurostat_wages":
        return _fetch_eurostat_czech_only(
            definition,
            "lc_lci_r2_q",
            {"lcstruct": "D11", "nace_r2": "B-S", "s_adj": "SCA", "unit": "PCH_SM"},
            "Mzdy a platy",
        )
    raise RuntimeError(f"Neznamy cesky makro ukazatel: {definition.key}")


def fetch_czech_macro_dashboard(
    progress_callback: Callable[[list[CzechSeriesResult], list[str], int, int], None] | None = None,
) -> tuple[list[CzechSeriesResult], list[str]]:
    results: list[CzechSeriesResult] = []
    errors: list[str] = []
    total = len(CZECH_SERIES_DEFINITIONS)

    with ThreadPoolExecutor(max_workers=min(8, total)) as executor:
        future_map = {
            executor.submit(fetch_czech_series, definition): definition
            for definition in CZECH_SERIES_DEFINITIONS
        }

        done = 0
        for future in as_completed(future_map):
            definition = future_map[future]
            try:
                results.append(future.result())
            except Exception as exc:
                errors.append(f"{definition.title}: {exc}")
            done += 1

            ordered_results = sorted(
                results,
                key=lambda item: CZECH_SERIES_DEFINITIONS.index(item.definition),
            )
            if progress_callback is not None:
                progress_callback(ordered_results, list(errors), done, total)

    results.sort(key=lambda item: CZECH_SERIES_DEFINITIONS.index(item.definition))
    return results, errors
