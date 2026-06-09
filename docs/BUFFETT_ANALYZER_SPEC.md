# Buffett Analyzer – přesné vývojové zadání

Tento dokument je hlavní implementační specifikace pro projekt **Buffett Analyzer**.
Při vývoji aplikace se tímto souborem budeme řídit jako zdrojem požadavků.

## Zadání

Vytvoř jednoduchou Python aplikaci pro osobní fundamentální analýzu USA akcií podle principů Warrena Buffetta.

Aplikace nesmí vymýšlet žádná data. Všechny finanční hodnoty musí pocházet z Yahoo Finance přes knihovnu `yfinance`. Pokud některá hodnota chybí, zobraz `N/A` a přidej varování.

## Technologie

Použij:

- Python 3.11+
- Streamlit
- yfinance
- pandas
- numpy

## Struktura projektu

```text
buffett-analyzer/
├── app.py
├── analyzer.py
├── company_loader.py
├── data_provider.py
├── scoring.py
├── models.py
├── companies.txt
├── requirements.txt
├── README.md
└── BUFFETT_ANALYZER_SPEC.md
```

## Implementační pravidla

- Nepoužívat žádná smyšlená nebo odhadnutá finanční data.
- Jako datový zdroj používat pouze `yfinance`, pokud zadání nebude později rozšířeno.
- Chybějící hodnoty vždy zobrazit jako `N/A`.
- Každou chybějící důležitou hodnotu doplnit varováním v aplikaci.
- Cílem je jednoduchá osobní analytická aplikace, ne investiční poradenství.
