from __future__ import annotations

from models import CompanySnapshot


def score_company(snapshot: CompanySnapshot) -> tuple[int | None, int, str]:
    checks = [
        (snapshot.return_on_equity, lambda value: value is not None and value >= 0.15, "ROE >= 15 %"),
        (snapshot.debt_to_equity, lambda value: value is not None and value <= 100, "Debt/Equity <= 100"),
        (snapshot.operating_margin, lambda value: value is not None and value >= 0.15, "Operating margin >= 15 %"),
        (snapshot.net_margin, lambda value: value is not None and value >= 0.10, "Net margin >= 10 %"),
        (snapshot.free_cash_flow, lambda value: value is not None and value > 0, "Free cash flow > 0"),
        (snapshot.earnings_growth, lambda value: value is not None and value > 0, "Earnings growth > 0"),
    ]

    available_checks = 0
    points = 0
    for value, evaluator, _label in checks:
        if value is None:
            continue
        available_checks += 1
        if evaluator(value):
            points += 1

    if available_checks == 0:
        return None, len(checks), "Nelze skórovat"

    ratio = points / available_checks
    if ratio >= 0.8:
        verdict = "Silný Buffett-style profil"
    elif ratio >= 0.5:
        verdict = "Smíšený Buffett-style profil"
    else:
        verdict = "Slabší Buffett-style profil"

    return points, len(checks), verdict
