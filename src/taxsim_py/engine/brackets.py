"""Marginal tax bracket calculations."""

import polars as pl


def _bracket_index(income: pl.Expr, starts: list[float]) -> pl.Expr:
    """Index of the bracket containing income (a bracket starts at its threshold)."""
    position = pl.lit(pl.Series(starts, dtype=pl.Float64)).search_sorted(income, side="right")
    return (position.cast(pl.Int64) - 1).clip(0, None)


def _bracket_tax_by_row(income: pl.Expr, brackets: list[list]) -> pl.Expr:
    """`bracket_tax` for thresholds or rates that vary by row."""
    thresholds = [b[0] for b in brackets]
    rates = [b[1] for b in brackets]
    upper = thresholds[1:] + [None]
    tax = pl.lit(0.0)
    for lower, up, rate in zip(thresholds, upper, rates):
        span = (income - lower) if up is None else (income - lower).clip(0, up - lower)
        tax = tax + span.clip(0, None) * rate
    return tax


def bracket_tax(income: pl.Expr, brackets: list[list[float]]) -> pl.Expr:
    """Build an expression for tax under a marginal bracket schedule."""
    if any(isinstance(value, pl.Expr) for bracket in brackets for value in bracket):
        return _bracket_tax_by_row(income, brackets)
    starts = [float(b[0]) for b in brackets]
    rates = [float(b[1]) for b in brackets]
    cumulative = [0.0]
    for lower, upper, rate in zip(starts, starts[1:], rates):
        cumulative.append(cumulative[-1] + (upper - lower) * rate)
    index = _bracket_index(income, starts)
    start = pl.lit(pl.Series(starts, dtype=pl.Float64)).gather(index)
    rate = pl.lit(pl.Series(rates, dtype=pl.Float64)).gather(index)
    base = pl.lit(pl.Series(cumulative, dtype=pl.Float64)).gather(index)
    return base + rate * (income - start).clip(0, None)


def bracket_rate(income: pl.Expr, brackets: list[list[float]]) -> pl.Expr:
    """Marginal rate of the bracket containing income (a bracket starts at its threshold)."""
    starts = [float(b[0]) for b in brackets]
    rates = [float(b[1]) for b in brackets]
    return pl.lit(pl.Series(rates, dtype=pl.Float64)).gather(_bracket_index(income, starts))


def scale_brackets(brackets: list[list[float]], factor: float) -> list[list[float]]:
    """Brackets with every threshold multiplied by `factor`."""
    return [[float(start) * factor, float(rate)] for start, rate in brackets]


def bracket_tax_by_status(income: pl.Expr, brackets_by_status: dict[str, list[list[float]]]) -> pl.Expr:
    """`bracket_tax` with a schedule chosen by the ``filing_status`` column."""
    expression = pl.lit(None, dtype=pl.Float64)
    for status, brackets in brackets_by_status.items():
        expression = pl.when(pl.col("filing_status") == status).then(bracket_tax(income, brackets)).otherwise(expression)
    return expression


def bracket_rate_by_status(income: pl.Expr, brackets_by_status: dict[str, list[list[float]]]) -> pl.Expr:
    """`bracket_rate` with a schedule chosen by the ``filing_status`` column."""
    expression = pl.lit(None, dtype=pl.Float64)
    for status, brackets in brackets_by_status.items():
        expression = pl.when(pl.col("filing_status") == status).then(bracket_rate(income, brackets)).otherwise(expression)
    return expression
