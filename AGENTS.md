# AGENTS.md

Tento soubor určuje pracovní pravidla pro vývoj agenta v projektu **Buffett Analyzer**.
Jeho cílem je zajistit, aby aplikace působila profesionálně, byla přehledná a zároveň zůstala věrná hlavní specifikaci projektu.

## Hlavní zdroj pravdy

- Primární produktová a technická specifikace je v souboru `docs/BUFFETT_ANALYZER_SPEC.md`.
- Pokud vznikne rozpor mezi tímto souborem a implementační specifikací, přednost má `docs/BUFFETT_ANALYZER_SPEC.md`.

## Účel aplikace

Buffett Analyzer je jednoduchá, důvěryhodná a profesionálně působící aplikace pro osobní fundamentální analýzu amerických akcií podle principů Warrena Buffetta.

## Povinná pravidla

- Nikdy nevymýšlet finanční data, metriky ani odhady.
- Veškerá finanční data musí pocházet z Yahoo Finance přes knihovnu `yfinance`.
- Pokud nějaká hodnota chybí nebo není dostupná, zobrazit `N/A`.
- Chybějící nebo neúplná data vždy doplnit viditelným varováním pro uživatele.
- Aplikace nesmí předstírat přesnost tam, kde zdrojová data nejsou dostupná.

## Kvalita a profesionalita

- Rozhraní musí být jednoduché, čisté a srozumitelné i pro běžného uživatele.
- Výstupy mají být konzistentně pojmenované, čitelné a vizuálně uspořádané.
- U každé důležité metriky je vhodné krátce vysvětlit, co znamená.
- Je vhodné oddělit surová data, interpretaci a výsledné skóre do samostatných sekcí.
- Varování, chyby a omezení dat mají být uživateli jasně viditelné.
- Aplikace má působit jako seriózní analytický nástroj, ne jako marketingová nebo spekulativní aplikace.

## Doporučení pro návrh funkcí

- Umožnit výběr tickeru z předem připraveného seznamu v `companies.txt`.
- Zobrazit základní profil firmy, pokud je dostupný.
- Zobrazit klíčové fundamentální ukazatele relevantní pro Buffett-style analýzu.
- Oddělit výpočetní logiku od načítání dat a od uživatelského rozhraní.
- Připravit kód tak, aby bylo možné snadno přidat další pravidla hodnocení.

## Doporučení pro Buffett-style analýzu

- Upřednostňovat ukazatele, které dávají smysl pro dlouhodobou kvalitu firmy.
- Zaměřit se na stabilitu, ziskovost, rozumné zadlužení a kvalitu cash flow.
- Pokud nelze metriky spolehlivě spočítat z dostupných dat, raději uvést `N/A` než vytvářet náhradní odhad.
- Interpretace výsledků má být opatrná a věcná.

## Technická pravidla

- Použít Python 3.11+.
- Použít `Streamlit`, `yfinance`, `pandas`, `numpy`.
- Kód rozdělit do menších odpovědností podle souborů uvedených ve specifikaci.
- Preferovat čitelnost, jednoduchost a odolnost vůči chybějícím datům.
- Každou důležitou transformaci nebo rozhodovací logiku psát tak, aby byla snadno testovatelná.

## Uživatelská komunikace v aplikaci

- Jasně uvádět, odkud data pocházejí.
- Upozornit, že jde o nástroj pro osobní analýzu, nikoli investiční doporučení.
- Chybová hlášení psát srozumitelně a prakticky.
- Pokud načtení dat selže, aplikace má nabídnout bezpečné a srozumitelné vysvětlení problému.

## Co zlepšuje aplikaci

- Přehledné členění výsledků do bloků jako profil firmy, finanční metriky, skóre a varování.
- Konzistentní práce s chybějícími daty napříč celou aplikací.
- Jednotné formátování čísel, procent a měnových hodnot.
- Stručné vysvětlení metodiky skórování.
- Přehledný README a dobře oddělené moduly.

## Co nedělat

- Nepřidávat smyšlené hodnoty, aby aplikace vypadala úplněji.
- Neskrývat omezení zdrojových dat.
- Nemíchat obchodní logiku, datový zdroj a UI do jednoho souboru.
- Nevytvářet příliš složité rozhraní, které by zhoršilo přehlednost.
