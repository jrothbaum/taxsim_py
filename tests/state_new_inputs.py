"""Cases for the inputs added after the states were built (ages, pensions,
Social Security, rent, transfers, other income, business income, dependent
filers), run for every state by scripts/validate_states.py."""

from typing import Any


def _case(state: int, year: int, description: str, **inputs: Any) -> dict[str, Any]:
    return {"state": state, "year": year, "mstat": 1, "description": f"{description} [year={year}]", **inputs}


def build_new_input_cases(state: int, prefix: str, years: list[int]) -> list[dict[str, Any]]:
    rows = []
    for year in years:
        def case(description: str, **inputs: Any) -> None:
            rows.append(_case(state, year, f"{prefix} new inputs: {description}", **inputs))

        case("pensions with wages, single", pwages=30000, pensions=25000)
        case("pensions only, couple", mstat=2, pensions=40000)
        case("social security and pensions, couple", mstat=2, gssi=30000, pensions=10000)
        case("social security with wages, single", gssi=18000, pwages=25000)
        case("aged single, wages and pensions", page=70, pwages=20000, pensions=10000, gssi=12000)
        case("aged couple, pensions and benefits", mstat=2, page=70, sage=67, pensions=30000, gssi=20000, pwages=20000)
        case("aged low income, property tax", page=72, pensions=12000, gssi=8000, proptax=2000)
        case("aged low income renter", page=72, gssi=9000, rentpaid=6000)
        # Low-income aged filers get the federal elderly credit.
        case("aged couple, low wages", mstat=2, page=73, sage=77, pwages=15000, proptax=1650)
        case("aged single, low wages", page=70, pwages=9000)
        case("low income renter with transfers", pwages=12000, rentpaid=6000, transfers=6000)
        case("renter with child", depx=1, dep13=1, dep17=1, dep18=1, pwages=18000, rentpaid=8000)
        # TAXSIM's federal dependent standard deduction is stale in these years.
        stale = year in (1995, 1996, 2008, 2009, 2022)
        case("dependent filer, wages", mstat=8, pwages=5000, oracle_divergent=stale)
        case("dependent filer, interest", mstat=8, pwages=1000, intrec=3000, oracle_divergent=stale)
        case("unemployment compensation with wages, couple", mstat=2, pwages=30000, ui=9000, pui=6000, sui=3000)
        case("business income, single", pbusinc=40000)
        case("professional and S corporation income, couple", mstat=2, pwages=50000, pprofinc=30000, scorp=30000)
        case("other property income", pwages=40000, otherprop=15000)
        case("other non-property income", pwages=40000, nonprop=8000)
    return rows
