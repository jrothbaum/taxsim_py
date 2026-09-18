"""Generic piecewise marginal-rate bracket evaluator - the Polars replacement
for the Fortran's `look2` (taxsim_2022_10_21.f:315), vectorized over a whole
column instead of one record at a time.

Tax on income y given brackets [(t_0, r_0), (t_1, r_1), ...] where t_i is the
threshold each rate starts at:

    tax(y) = sum_i  clip(y - t_i, 0, t_(i+1) - t_i) * r_i
"""

import polars as pl


def bracket_tax(income: pl.Expr, brackets: list[list[float]]) -> pl.Expr:
    """Build a Polars expression computing bracket tax for a single, fixed
    bracket schedule applied to every row of `income`.

    `brackets` is a list of [threshold, rate] pairs, sorted ascending by
    threshold (validated by `engine.schema.validate_brackets`).
    """
    thresholds = [b[0] for b in brackets]
    rates = [b[1] for b in brackets]
    upper = thresholds[1:] + [None]  # last bracket has no upper bound

    tax = pl.lit(0.0)
    for lower, up, rate in zip(thresholds, upper, rates):
        span_in_bracket = (
            (income - lower) if up is None else (income - lower).clip(0, up - lower)
        )
        tax = tax + span_in_bracket.clip(0, None) * rate
    return tax
