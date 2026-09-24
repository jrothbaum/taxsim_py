"""Federal and state tax resolution."""

from collections.abc import Callable, Mapping

import polars as pl

from taxsim_py.calculators.federal import compute_regular_tax
from taxsim_py.calculators.payroll import compute_payroll_tax
from taxsim_py.engine.sales_tax import state_sales_tax_deduction
from taxsim_py.engine.state import FORCE_ITEMIZE

ITERATIONS = 3
_ROW = "__resolve_row"

StateCalculator = Callable[[pl.LazyFrame, int], pl.LazyFrame]

_SALES_TAX = "__sales_tax"
_DEDUCTION = "state_sales_or_income_tax_ded"
_KEYS = [_ROW, FORCE_ITEMIZE]


def _pass_plans(
    frame: pl.DataFrame, year: int, calculators: StateCalculator | Mapping[int, StateCalculator]
) -> list[pl.LazyFrame]:
    """Lazy plans for one feedback pass: one plan, or one per state partition."""
    family_size = pl.when(pl.col("filing_status") == "married_joint").then(2.0).otherwise(1.0) + pl.col("depx")
    federal = compute_payroll_tax(compute_regular_tax(frame.lazy(), year), year).with_columns(
        state_sales_tax_deduction(pl.col("agi"), family_size, pl.col("state"), year).alias(_SALES_TAX)
    )
    if callable(calculators):
        return [calculators(federal, year)]
    partitions = federal.collect().partition_by("state", as_dict=True)
    return [
        calculators[state_code](rows.lazy(), year)
        for (state_code,), rows in partitions.items()
        if state_code in calculators
    ]


def resolve_federal_and_state(
    raw_df: pl.DataFrame,
    year: int,
    compute_state_tax_fn: StateCalculator | Mapping[int, StateCalculator],
) -> pl.DataFrame:
    """Resolve the federal and state tax interaction.

    Each record is computed with itemizing forced on and forced off, in one
    stacked frame. On every pass the state's income tax (or TAXSIM's
    estimated general sales tax, when larger) feeds the federal itemized
    deduction. The choice with the lower combined tax is kept.
    `compute_state_tax_fn` is one calculator, or a mapping from TAXSIM state
    code to calculator for records from several states.
    """
    base = raw_df.with_row_index(_ROW)
    if _DEDUCTION not in base.columns:
        base = base.with_columns(pl.lit(0.0).alias(_DEDUCTION))
    stacked = pl.concat(
        [base.with_columns(pl.lit(True).alias(FORCE_ITEMIZE)), base.with_columns(pl.lit(False).alias(FORCE_ITEMIZE))]
    )

    current = stacked
    for _ in range(ITERATIONS - 1):
        # TAXSIM deducts the larger of state income tax and its estimated
        # general sales tax federally.
        deduction = pl.concat(
            pl.collect_all(
                plan.select(
                    *_KEYS,
                    pl.max_horizontal(pl.col("siitax").clip(0, None), _SALES_TAX).alias(_DEDUCTION),
                )
                for plan in _pass_plans(current, year, compute_state_tax_fn)
            )
        )
        current = stacked.drop(_DEDUCTION).join(deduction, on=_KEYS, how="left")
    out = pl.concat(
        pl.collect_all(_pass_plans(current, year, compute_state_tax_fn)), how="diagonal_relaxed"
    ).drop(_SALES_TAX)

    itemized = out.filter(pl.col(FORCE_ITEMIZE)).sort(_ROW)
    standard = out.filter(~pl.col(FORCE_ITEMIZE)).sort(_ROW)
    itemize_wins = (
        (itemized.get_column("fiitax") + itemized.get_column("siitax"))
        < (standard.get_column("fiitax") + standard.get_column("siitax"))
    )
    columns = [c for c in itemized.columns if c not in _KEYS]
    return pl.DataFrame(
        {col: itemized.get_column(col).zip_with(itemize_wins, standard.get_column(col)) for col in columns}
    )
