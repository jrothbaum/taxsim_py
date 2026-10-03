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
    "al": 2025,
    "ar": 2025,
    "nh": 2025,
    "ca": 2025,
    "co": 2025,
    "az": 2025,
    "ct": 2025,
    "dc": 2025,
    "de": 2025,
    "ga": 2025,
    "in": 2025,
    "il": 2025,
    "id": 2025,
    "ky": 2025,
    "mi": 2025,
    "nc": 2025,
    "pa": 2025,
    "ut": 2025,
    "oh": 2025,
    "ok": 2025,
    "or": 2025,
    "hi": 2025,
    "vt": 2025,
    "nm": 2025,
    "wv": 2025,
    "sc": 2025,
    "ri": 2025,
    "nd": 2025,
    "ne": 2025,
    "mt": 2025,
    "mo": 2025,
    "ms": 2025,
    "me": 2025,
    "la": 2025,
    "ks": 2025,
    "ia": 2025,
    "md": 2025,
    "wi": 2025,
    "mn": 2025,
    "nj": 2025,
    "va": 2025,
    "ny": 2025,
    "ma": 2025,
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
