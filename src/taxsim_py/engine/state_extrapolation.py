"""State tax year extrapolation."""


from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import polars as pl

from taxsim_py.behavior import BehaviorProfile
from taxsim_py.engine.schema import PARAMETERS_ROOT, load_yaml

_PARAMS = load_yaml(PARAMETERS_ROOT / "national" / "state_cpi_extrapolation.yaml")
LASTAT = int(_PARAMS["lastat"])
_XNDXA = {int(y): float(v) for y, v in _PARAMS["xndxa"].items()}


def _index(year: int) -> float:
    """The CPI proxy, continued past its last year at the source's assumed 2.5% a year."""
    last = max(_XNDXA)
    if year <= last:
        return _XNDXA[year]
    return _XNDXA[last] * 1.025 ** (year - last)


def resolve_state_year(year: int, behavior: BehaviorProfile, params: Mapping[str, Any] | None = None) -> tuple[int, float]:
    """Return the effective state-law year and inflation factor.

    Statutory mode uses actual law through the state's `law_coded_through`
    year and extrapolates that year's law past it. TAXSIM mode, and states
    without the key, extrapolate TAXSIM's last state-law year (`lastat`).
    """
    through = LASTAT
    if behavior.mode.value == "statutory" and params is not None and "law_coded_through" in params:
        through = int(params["law_coded_through"])
    if year <= through:
        return year, 1.0
    return through, _index(year) / _index(through)


# Dollar amounts TAXSIM divides by the inflation factor before running the
# last coded state law for a later year: inputs stored in `data(11-99)` and
# `data(111-199)` and federal results in `comnew(1-98)` other than 26, 65,
# 72 and 73. Business and S corporation income (`data(211-215)`), the
# uncapped child care credit (`comnew(176)`), self-employment tax
# (`comnew(175)`) and tax before credits (`comnew(154)`) are not deflated.
PROJECTED_YEAR_DEFLATED_COLUMNS = (
    "actc", "agi", "amt", "amt_income", "cares", "ccc", "charity_cash", "childcare", "dividends",
    "earned_income", "eitc", "federal_chcr", "federal_elder", "fica", "fiitax", "gssi", "intrec",
    "itemized_before_limit", "itemized_deduction", "ltcg", "ltg", "making_work_pay", "mortgage", "nonprop",
    "nonrefundable_credits",
    "odc", "otheritem", "otherprop", "pensions", "personal_exemptions", "pre1987_amex", "pre1987_capgn",
    "pre1987_chcr", "pre1987_deduc", "pre1987_pref", "proptax", "psemp", "pui", "pwages", "regular_tax",
    "rentpaid", "salt_capped", "schedule_tax", "se_adjustment", "ssemp", "standard_deduction",
    "state_sales_or_income_tax_ded", "stcg", "sui", "swages", "taxable_income",
    "taxable_social_security", "taxable_unemployment", "transfers", "ui", "wages",
)


def deflate_for_extrapolation(df: pl.DataFrame, flate: float, extra: tuple[str, ...] = ()) -> pl.DataFrame:
    """Deflate `PROJECTED_YEAR_DEFLATED_COLUMNS` and `extra` for a projected state-law year."""
    if flate == 1.0:
        return df
    present = set(df.collect_schema().names())
    to_scale = [c for c in (*PROJECTED_YEAR_DEFLATED_COLUMNS, *extra) if c in present]
    return df.with_columns([(pl.col(c) / flate).alias(c) for c in to_scale])
