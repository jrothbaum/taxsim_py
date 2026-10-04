"""Shared expressions used by state tax calculators."""


from __future__ import annotations
from collections.abc import Iterable, Mapping
from typing import Any

import polars as pl

from taxsim_py.engine.inputs import separate_divisor
from taxsim_py.engine.schema import PARAMETERS_ROOT, load_yaml, resolve_year

_PRE1987_PARAMS = load_yaml(PARAMETERS_ROOT / "national" / "pre1987.yaml")
_SOCSEC_PARAMS = load_yaml(PARAMETERS_ROOT / "national" / "state_socsec.yaml")
_ADJUSTMENT_PARAMS = load_yaml(PARAMETERS_ROOT / "national" / "state_adjustments.yaml")
_CAPITAL_GAINS_PARAMS = load_yaml(PARAMETERS_ROOT / "national" / "capital_gains.yaml")


def with_default(
    df: pl.DataFrame | pl.LazyFrame, column: str, default: Any = 0.0
) -> pl.DataFrame | pl.LazyFrame:
    """Add a column with a default value when it is absent."""
    return with_defaults(df, (column,), default)


def with_defaults(
    df: pl.DataFrame | pl.LazyFrame, columns: Iterable[str], default: Any = 0.0
) -> pl.DataFrame | pl.LazyFrame:
    """Add each absent column with a default value, resolving the schema once."""
    present = set(df.collect_schema().names())
    missing = [column for column in columns if column not in present]
    if not missing:
        return df
    return df.with_columns(pl.lit(default).alias(column) for column in missing)


def by_filing_status(values: Mapping[str, float]) -> pl.Expr:
    """Select a numeric value using the ``filing_status`` column."""
    expression = pl.lit(None, dtype=pl.Float64)
    for status, value in values.items():
        expression = (
            pl.when(pl.col("filing_status") == status)
            .then(pl.lit(float(value)))
            .otherwise(expression)
        )
    return expression


def interpolate_table(income: pl.Expr, rows: list[list[float]]) -> pl.Expr:
    """Interpolate a TAXSIM ``tablki`` table of ``[threshold, value]`` rows.

    Below the first threshold the first value applies and at or above the
    last threshold the last value applies; in between, TAXSIM's weighting
    between the neighbouring rows is used.
    """
    if any(isinstance(value, pl.Expr) for row in rows for value in row):
        return _interpolate_table_by_row(income, rows)
    thresholds = [float(row[0]) for row in rows[:-1]]
    values = [float(row[1]) for row in rows]
    last = len(thresholds)
    count = pl.lit(pl.Series(thresholds, dtype=pl.Float64)).search_sorted(income, side="right").cast(pl.Int64)
    threshold_series = pl.lit(pl.Series(thresholds, dtype=pl.Float64))
    value_series = pl.lit(pl.Series(values, dtype=pl.Float64))
    lower_index = (count - 1).clip(0, last - 1)
    upper_index = count.clip(0, last - 1)
    lower_threshold = threshold_series.gather(lower_index)
    upper_threshold = threshold_series.gather(upper_index)
    lower_value = value_series.gather((count - 1).clip(0, last))
    upper_value = value_series.gather(count.clip(0, last))
    weight = (income - lower_threshold) / (upper_threshold - lower_threshold)
    between = (
        pl.when(upper_value > lower_value)
        .then(weight * lower_value + (1 - weight) * upper_value)
        .otherwise(weight * upper_value + (1 - weight) * lower_value)
    )
    return (
        pl.when(count == 0)
        .then(pl.lit(values[0]))
        .when(count >= last)
        .then(pl.lit(values[-1]))
        .otherwise(between)
    )


def _interpolate_table_by_row(income: pl.Expr, rows: list[list]) -> pl.Expr:
    """`interpolate_table` for thresholds or values that vary by row."""
    thresholds = [row[0] for row in rows[:-1]]
    values = [row[1] for row in rows]
    expression = pl.lit(values[-1]) if not isinstance(values[-1], pl.Expr) else values[-1]
    for index in range(len(thresholds) - 1, -1, -1):
        upper_threshold = thresholds[index]
        upper_value = values[index]
        if index == 0:
            below = upper_value if isinstance(upper_value, pl.Expr) else pl.lit(upper_value)
        else:
            lower_threshold = thresholds[index - 1]
            lower_value = values[index - 1]
            weight = (income - lower_threshold) / (upper_threshold - lower_threshold)
            below = (
                pl.when(pl.lit(upper_value) > lower_value if not isinstance(upper_value, pl.Expr) else upper_value > lower_value)
                .then(weight * lower_value + (1 - weight) * upper_value)
                .otherwise(weight * upper_value + (1 - weight) * lower_value)
            )
        expression = pl.when(income < upper_threshold).then(below).otherwise(expression)
    return expression


def unemployment_total() -> pl.Expr:
    """Unemployment compensation, preferring the spouse split when larger."""
    return pl.max_horizontal(pl.col("ui"), pl.col("pui") + pl.col("sui"))


def federal_capital_gain_in_agi(year: int, flate: float = 1.0) -> pl.Expr:
    """Net capital gain in federal AGI (TAXSIM `comnew(6)`) in the state frame's (deflated) dollars."""
    if year <= 1986:
        return pl.col("pre1987_capgn")
    loss_limit = float(resolve_year(_CAPITAL_GAINS_PARAMS["net_capital_loss_limit"], year))
    return pl.max_horizontal(pl.col("stcg") + pl.col("ltcg"), -loss_limit / flate / separate_divisor())


def dividend_input_adjustment() -> float:
    """Amount TAXSIM adds to the dividend input."""
    return float(_ADJUSTMENT_PARAMS["household_income_dividend_adjustment"])


def household_income() -> pl.Expr:
    """TAXSIM's household-income total (`data(159)`) from raw inputs.

    Sums positive wages, dividends, pensions, unemployment compensation,
    Social Security benefits, transfers, interest, business and S
    corporation income, other property income and positive net capital
    gains, plus the two TAXSIM input adjustments. Self-employment and other
    non-property income are not included.
    """
    record_adjustment = float(_ADJUSTMENT_PARAMS["household_income_record_adjustment"])
    return (
        pl.col("pwages").clip(0, None)
        + pl.col("swages").clip(0, None)
        + pl.col("dividends")
        + dividend_input_adjustment()
        + pl.col("pensions")
        + unemployment_total()
        + pl.col("gssi")
        + pl.col("transfers")
        + pl.col("intrec")
        + pl.col("pbusinc") + pl.col("pprofinc") + pl.col("sbusinc") + pl.col("sprofinc") + pl.col("scorp")
        + pl.col("otherprop")
        + (pl.col("stcg") + pl.col("ltcg")).clip(0, None)
        + record_adjustment
    )


def higher_earner_share(income: pl.Expr) -> pl.Expr:
    """The higher earner's share of joint income: their wages plus half the rest."""
    return pl.max_horizontal(pl.col("pwages"), pl.col("swages")) + (income - pl.col("wages")) / 2.0


def dividend_exclusion_addback(year: int) -> pl.Expr:
    """Federal dividend exclusion that states add back to federal AGI (`divexc`).

    Before 1987 this is the dividends (plus interest in 1981) excluded from
    federal AGI; from 1987 there is no exclusion and the addback is zero.
    """
    if year >= 1987:
        return pl.lit(0.0)
    excluded = pl.col("dividends") + dividend_input_adjustment()
    if year == 1981:
        excluded = excluded + pl.col("intrec")
    limit = by_filing_status(
        {status: resolve_year(amounts, year) for status, amounts in _PRE1987_PARAMS["dividend_exclusion"].items()}
    )
    return pl.min_horizontal(excluded, limit).clip(0, None)


def tier_values(value: pl.Expr, uppers: list[float], *columns: list[float], strict: bool = False) -> list[pl.Expr]:
    """Values from the first tier whose upper bound is at least `value` (the last tier beyond).

    With `strict`, the first tier whose upper bound exceeds `value` (TAXSIM `tablk`).
    """
    side = "right" if strict else "left"
    position = pl.lit(pl.Series(uppers, dtype=pl.Float64)).search_sorted(value, side=side)
    index = position.cast(pl.Int64).clip(0, len(uppers) - 1)
    return [pl.lit(pl.Series(column, dtype=pl.Float64)).gather(index) for column in columns]


def checkpoint(df: pl.DataFrame, **expressions: pl.Expr) -> tuple[pl.DataFrame, list[pl.Expr]]:
    """Store expressions as columns and return references to them.

    Later formulas then read the stored column instead of repeating the
    expression, which keeps expression trees small.
    """
    df = df.with_columns(**expressions)
    return df, [pl.col(name) for name in expressions]


# `force_itemize` column: True or False when the itemize choice is forced
# (the federal-state resolver runs both), null when it is not.
FORCE_ITEMIZE = "force_itemize"


def _forced() -> pl.Expr:
    return pl.col(FORCE_ITEMIZE).cast(pl.Boolean)


def itemize_choice(natural: pl.Expr) -> pl.Expr:
    """The forced itemize choice where set, otherwise `natural`."""
    return pl.when(_forced().is_null()).then(natural).otherwise(_forced())


def forced_itemized() -> pl.Expr:
    """True where itemizing is forced."""
    return _forced().fill_null(False)


def forced_standard() -> pl.Expr:
    """True where the standard deduction is forced."""
    return _forced().not_().fill_null(False)


def pre1987_federal_itemizing(year: int) -> tuple[pl.Expr, pl.Expr, pl.Expr]:
    """Federal itemizing through 1986 (`comnew(30)`, `comnew(26)`, `comnew(3)`).

    Returns the gross itemized total, whether the federal return itemizes, and
    the zero bracket amount, the latter two as the pre-1987 federal
    calculator reports them.
    """
    gross = (
        pl.col("state_sales_or_income_tax_ded") + pl.col("proptax") + pl.col("otheritem")
        + pl.col("mortgage") + pl.col("charity_cash")
    )
    return gross, pl.col("pre1987_itemizes"), pl.col("pre1987_zbr")


def taxsim_socsec(year: int, setax: pl.Expr, addmed: pl.Expr | None = None) -> pl.Expr:
    """Payroll tax as TAXSIM's `socsec` reports it to states.

    Employee FICA on each spouse's wages (joint returns) or on total wages,
    plus self-employment tax and, from 2013, the additional Medicare tax.
    `year` is the state law year; wages are the (deflated) input columns.
    """
    rate = float(resolve_year(_SOCSEC_PARAMS["rate"], year))
    ceiling = float(resolve_year(_SOCSEC_PARAMS["wage_ceiling"], year))
    above = float(_SOCSEC_PARAMS["rate_above_ceiling"])

    def fica(wages: pl.Expr) -> pl.Expr:
        return pl.min_horizontal(wages, pl.lit(ceiling)) * rate + (wages - ceiling).clip(0, None) * above

    pw = pl.col("pwages").clip(0, None)
    sw = pl.col("swages").clip(0, None)
    total = pl.when(pl.col("filing_status") == "married_joint").then(fica(pw) + fica(sw)).otherwise(fica(pw + sw))
    total = total + setax
    if year >= 2013 and addmed is not None:
        total = total + addmed
    return total


_DETAIL_NAMES = {
    "agi": "state_adjusted_gross_income",
    "exemptions": "state_exemptions",
    "standard_deduction": "state_standard_deduction",
    "itemized_deductions": "state_itemized_deductions",
    "taxable_income": "state_taxable_income",
    "property_credit": "state_property_credit",
    "child_care_credit": "state_child_and_dependent_care_credit",
    "eic": "state_earned_income_tax_credit",
    "credits": "state_total_credits",
    "rate": "state_marginal_rate",
}


def with_state_detail(df: pl.DataFrame | pl.LazyFrame, **values: pl.Expr | float) -> pl.DataFrame | pl.LazyFrame:
    """Set the state's detail outputs (TAXSIM's `/calc/` block, `idtl=2`).

    Keywords are `agi`, `exemptions`, `standard_deduction`,
    `itemized_deductions`, `taxable_income`, `property_credit`,
    `child_care_credit`, `eic`, `credits` and `rate` (the bracket rate as a
    fraction), each the final value the TAXSIM routine leaves in that
    variable, in the law year's dollars. Outputs not given are 0.
    """
    unknown = set(values) - set(_DETAIL_NAMES)
    if unknown:
        raise ValueError(f"Unknown state detail outputs: {sorted(unknown)}")
    return df.with_columns(
        (value if isinstance(value, pl.Expr) else pl.lit(float(value))).cast(pl.Float64).alias(_DETAIL_NAMES[name])
        for name, value in values.items()
    )
