from __future__ import annotations

from models import CompanySnapshot


def score_company(snapshot: CompanySnapshot) -> tuple[int | None, int, str]:
    cash_conversion = None
    if snapshot.free_cash_flow is not None and snapshot.net_income not in (None, 0):
        cash_conversion = snapshot.free_cash_flow / snapshot.net_income

    price_check_available = snapshot.current_price is not None and snapshot.buy_under_price is not None
    price_ok = price_check_available and snapshot.current_price <= snapshot.buy_under_price

    checks = [
        (snapshot.return_on_equity, lambda value: value is not None and value >= 0.15, "ROE >= 15 %"),
        (snapshot.debt_to_equity, lambda value: value is not None and value <= 100, "Debt/Equity <= 100"),
        (snapshot.operating_margin, lambda value: value is not None and value >= 0.15, "Operating margin >= 15 %"),
        (snapshot.net_margin, lambda value: value is not None and value >= 0.10, "Net margin >= 10 %"),
        (snapshot.free_cash_flow, lambda value: value is not None and value > 0, "Free cash flow > 0"),
        (cash_conversion, lambda value: value is not None and value >= 0.75, "FCF / Net Income >= 75 %"),
        (snapshot.dcf_growth_rate, lambda value: value is not None and value >= 0, "Conservative growth >= 0 %"),
        (
            (snapshot.current_price, snapshot.buy_under_price),
            lambda value: value[0] is not None and value[1] is not None and value[0] <= value[1],
            "Current price <= buy-under price",
        ),
    ]

    available_checks = 0
    points = 0
    for value, evaluator, _label in checks:
        if value is None or (isinstance(value, tuple) and any(item is None for item in value)):
            continue
        available_checks += 1
        if evaluator(value):
            points += 1

    if available_checks == 0:
        return None, len(checks), "Nelze skórovat"

    ratio = points / available_checks
    if ratio >= 0.8 and price_check_available and not price_ok:
        verdict = "Kvalitní, čekat na cenu"
    elif ratio >= 0.8:
        verdict = "Silný Buffett-style profil"
    elif ratio >= 0.6:
        verdict = "Dobrý Buffett-style profil"
    elif ratio >= 0.45:
        verdict = "Smíšený Buffett-style profil"
    else:
        verdict = "Slabší Buffett-style profil"

    return points, available_checks, verdict
