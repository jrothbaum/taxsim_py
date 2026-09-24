"""Shared expressions used by state tax calculators."""

from collections.abc import Iterable, Mapping
from typing import Any

import polars as pl

from taxsim_py.engine.schema import PARAMETERS_ROOT, load_yaml, resolve_year

_PRE1987_PARAMS = load_yaml(PARAMETERS_ROOT / "national" / "pre1987.yaml")


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


def household_income(dividend_adjustment: float, record_adjustment: float) -> pl.Expr:
    """TAXSIM's household-income total (`data(159)`) from raw inputs.

    Sums positive wages, dividends, unemployment compensation, interest and
    positive net capital gains, plus the two TAXSIM input adjustments.
    """
    return (
        pl.col("pwages").clip(0, None)
        + pl.col("swages").clip(0, None)
        + pl.col("dividends")
        + dividend_adjustment
        + unemployment_total()
        + pl.col("intrec")
        + (pl.col("stcg") + pl.col("ltcg")).clip(0, None)
        + record_adjustment
    )


def dividend_exclusion_addback(year: int, dividend_adjustment: float) -> pl.Expr:
    """Federal dividend exclusion that states add back to federal AGI (`divexc`).

    Before 1987 this is the dividends (plus interest in 1981) excluded from
    federal AGI; from 1987 there is no exclusion and the addback is zero.
    """
    if year >= 1987:
        return pl.lit(0.0)
    excluded = pl.col("dividends") + dividend_adjustment
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
