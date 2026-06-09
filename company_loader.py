from __future__ import annotations

from pathlib import Path

from models import Company


def load_companies(file_path: str | Path) -> list[Company]:
    path = Path(file_path)
    if not path.exists():
        return []

    companies: list[Company] = []
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue

        if "|" in line:
            ticker, name = [part.strip() for part in line.split("|", maxsplit=1)]
        else:
            ticker = line.strip()
            name = ticker

        if ticker:
            companies.append(Company(ticker=ticker.upper(), name=name or ticker.upper()))

    return companies
