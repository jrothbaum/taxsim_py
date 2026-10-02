"""State tax year extrapolation."""

import polars as pl

from taxsim_py.engine.schema import PARAMETERS_ROOT, load_yaml

_PARAMS = load_yaml(PARAMETERS_ROOT / "national" / "state_cpi_extrapolation.yaml")
LASTAT = int(_PARAMS["lastat"])
_XNDXA = {int(y): float(v) for y, v in _PARAMS["xndxa"].items()}

# These states have been reviewed against the installed PolicyEngine-US
# parameter tree for actual 2022-2024 values. Other states remain on the
# historical TAXSIM-style extrapolation path until their mappings are reviewed.
ACTUAL_STATE_PARAMETER_YEARS = {
    "al": 2024,
    "ar": 2024,
    "nh": 2024,
    "ca": 2024,
    "co": 2024,
    "az": 2024,
    "ct": 2024,
    "dc": 2024,
    "de": 2024,
    "ga": 2024,
    "in": 2024,
    "il": 2024,
    "id": 2024,
    "ky": 2024,
    "mi": 2024,
    "nc": 2024,
    "pa": 2024,
    "ut": 2024,
    "oh": 2024,
    "ok": 2024,
    "or": 2024,
    "hi": 2024,
    "vt": 2024,
    "nm": 2024,
    "wv": 2024,
    "sc": 2024,
    "ri": 2024,
    "nd": 2024,
    "ne": 2024,
    "mt": 2024,
    "mo": 2024,
    "ms": 2024,
    "me": 2024,
    "la": 2024,
    "ks": 2024,
    "ia": 2024,
    "md": 2024,
    "wi": 2024,
    "mn": 2024,
    "nj": 2024,
    "va": 2024,
    "ny": 2024,
    "ma": 2024,
}


def _index(year: int) -> float:
    """The CPI proxy, continued past its last year at the source's assumed 2.5% a year."""
    last = max(_XNDXA)
    if year <= last:
        return _XNDXA[year]
    return _XNDXA[last] * 1.025 ** (year - last)


def resolve_state_year(year: int, state: str | None = None) -> tuple[int, float]:
    """Return the effective state-law year and inflation factor."""
    if state in ACTUAL_STATE_PARAMETER_YEARS and year <= ACTUAL_STATE_PARAMETER_YEARS[state]:
        return year, 1.0
    if year <= LASTAT:
        return year, 1.0
    flate = _index(year) / _XNDXA[LASTAT]
    return LASTAT, flate


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
