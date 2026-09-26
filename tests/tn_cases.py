"""Tennessee state-tax test cases."""

from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]

YEARS = list(range(1977, 2024))
STATE_TN = 43

_DEFAULTS: dict[str, Any] = {
    "state": STATE_TN,
    "mstat": 1,
    "depx": 0,
    "dep17": 0,
    "dep18": 0,
    "dep6": 0,
    "pwages": 0,
    "swages": 0,
    "proptax": 0,
    "otheritem": 0,
    "mortgage": 0,
    "dep13": 0,
    "childcare": 0,
    "intrec": 0,
    "psemp": 0,
    "ssemp": 0,
    "dividends": 0,
    "stcg": 0,
    "ltcg": 0,
    "ui": 0,
    "pui": 0,
    "sui": 0,
}


def case(year: int, description: str, **overrides: Any) -> dict[str, Any]:
    row = dict(_DEFAULTS)
    row["year"] = year
    row["description"] = f"{description} [year={year}]"
    row.update(overrides)
    return row


def build_tn_test_cases() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for year in YEARS:
        for amount in (0, 10, 20, 30, 60, 500, 1250, 1300, 2600, 5000, 40000):
            rows.append(case(year, f"TN interest, single, amount={amount}", mstat=1, pwages=30000, intrec=amount))
            rows.append(case(year, f"TN dividends, single, amount={amount}", mstat=1, pwages=30000, dividends=amount))
            rows.append(case(year, f"TN interest and dividends, married_joint, amount={amount}", mstat=2, pwages=60000, intrec=amount // 2, dividends=amount - amount // 2))
            rows.append(case(year, f"TN interest, married_separate, amount={amount}", mstat=6, pwages=30000, intrec=amount))
            rows.append(case(year, f"TN dividends, HoH, amount={amount}", mstat=3, depx=1, dep17=1, dep18=1, pwages=30000, dividends=amount))
        for wages in (0, 20000, 100000):
            rows.append(case(year, f"TN wages only, single, wages={wages}", mstat=1, pwages=wages))
            rows.append(case(year, f"TN itemizer with interest, joint, wages={wages}", mstat=2, pwages=wages, intrec=8000, dividends=4000, proptax=5000, mortgage=12000))
    return rows
