"""Federal and state tax resolution."""

from collections.abc import Callable, Mapping

import polars as pl

from taxsim_py.calculators.federal import compute_federal_income_tax
from taxsim_py.calculators.payroll import compute_payroll_tax
from taxsim_py.engine.inputs import taxpayer_count
from taxsim_py.engine.sales_tax import state_sales_tax_deduction
from taxsim_py.engine.state import FORCE_ITEMIZE

ITERATIONS = 3
_ROW = "__resolve_row"

StateCalculator = Callable[[pl.LazyFrame, int], pl.LazyFrame]

_SALES_TAX = "__sales_tax"
_DEDUCTION = "state_sales_or_income_tax_ded"
_TIE_TOLERANCE = 1e-6
_KEYS = [_ROW, FORCE_ITEMIZE]

# Federal columns state calculators read that an era's federal calculator
# does not compute; states see them as 0 (not itemizing).
_PRE1987_ONLY = (
    "credit", "pre1987_almtax", "pre1987_amex", "pre1987_capded", "pre1987_capgn", "pre1987_chcr",
    "pre1987_deduc", "pre1987_earncr", "pre1987_pref", "pre1987_pretax", "pre1987_taxbc", "pre1987_twoded",
)
_LAW87_ONLY = (
    "actc", "amt", "amt_income", "cares", "ccc", "ccc_uncapped", "charity_cash", "eitc", "eitc_before_age_test",
    "federal_elder", "itemized_before_limit", "nonrefundable_credits", "itemized_deduction", "ltg", "making_work_pay", "odc",
    "personal_exemptions", "qbi_deduction", "salt_capped", "schedule_tax", "se_adjustment", "setax",
    "standard_deduction", "tax_before_credits",
)
_LAW60_MISSING = (
    "federal_source_rate", "taxable_social_security", "pre1987_capded", "pre1987_capgn", "pre1987_pref",
    "pre1987_pretax", "pre1987_twoded",
)


def _uncomputed_federal_columns(year: int) -> list[pl.Expr]:
    """Zero (not itemizing) for the state-read federal columns `year`'s law lacks."""
    if year >= 1987:
        return [pl.lit(0.0).alias(c) for c in _PRE1987_ONLY]
    missing = _LAW87_ONLY + (_LAW60_MISSING if year <= 1976 else ())
    return [*(pl.lit(0.0).alias(c) for c in missing), pl.lit(False).alias("itemizes")]


def _federal(frame: pl.DataFrame, year: int) -> pl.LazyFrame:
    """Lazy federal and payroll plan for one feedback pass, with the sales tax estimate."""
    family_size = taxpayer_count() + pl.col("depx")
    # Resources: AGI plus nontaxable Social Security benefits and transfers.
    resources = pl.col("agi") + pl.col("gssi") - pl.col("taxable_social_security") + pl.col("transfers")
    return compute_payroll_tax(compute_federal_income_tax(frame.lazy(), year), year).with_columns(
        *_uncomputed_federal_columns(year),
        state_sales_tax_deduction(resources, family_size, pl.col("state"), year).alias(_SALES_TAX),
    )


def _state_plans(
    federal: pl.LazyFrame | pl.DataFrame, year: int, calculators: StateCalculator | Mapping[int, StateCalculator]
) -> list[pl.LazyFrame]:
    """Lazy state plans: one plan, or one per state partition."""
    if callable(calculators):
        return [calculators(federal.lazy(), year)]
    collected = federal.collect() if isinstance(federal, pl.LazyFrame) else federal
    partitions = collected.partition_by("state", as_dict=True)
    missing = sorted(state_code for (state_code,) in partitions if state_code not in calculators)
    if missing:
        raise NotImplementedError(f"No calculator supplied for TAXSIM state code(s): {missing}")
    return [
        calculators[state_code](rows.lazy(), year)
        for (state_code,), rows in partitions.items()
    ]


def resolve_federal_and_state(
    raw_df: pl.DataFrame,
    year: int,
    compute_state_tax_fn: StateCalculator | Mapping[int, StateCalculator],
    *,
    keep_intermediate: bool = True,
    keep_columns: tuple[str, ...] = (),
    result_columns: tuple[str, ...] | None = None,
) -> pl.DataFrame:
    """Resolve the federal and state tax interaction.

    Each record is computed with itemizing forced on and forced off, in one
    stacked frame. On every pass the state's income tax (or TAXSIM's
    estimated general sales tax, when larger) feeds the federal itemized
    deduction. The choice with the lower combined tax is kept.
    `compute_state_tax_fn` is one calculator, or a mapping from TAXSIM state
    code to calculator for records from several states. Without
    `keep_intermediate` the state plans keep only `siitax` and whichever of
    `keep_columns` they set. When `result_columns` is supplied, intermediates
    are also discarded before choosing between itemized and standard results.
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
                for plan in _state_plans(_federal(current, year), year, compute_state_tax_fn)
            )
        )
        current = stacked.drop(_DEDUCTION).join(deduction, on=_KEYS, how="left")
    # State calculators may rewrite federal columns (projected years deflate
    # them in place), so the result keeps the federal pass's own columns and
    # takes only the columns each state adds.
    federal = _federal(current, year).collect()
    final_plans = _state_plans(federal, year, compute_state_tax_fn)
    if not keep_intermediate:
        # Only the state tax, plus any requested columns the calculator sets.
        final_plans = [
            plan.select(*_KEYS, "siitax", *[c for c in keep_columns if c in plan.collect_schema().names()])
            for plan in final_plans
        ]
    state_out = pl.concat(pl.collect_all(final_plans), how="diagonal_relaxed")
    state_columns = [c for c in state_out.columns if c not in federal.columns]
    out = federal.join(state_out.select(*_KEYS, *state_columns), on=_KEYS).drop(_SALES_TAX)
    if result_columns is not None:
        out = out.select(*_KEYS, *result_columns)

    itemized = out.filter(pl.col(FORCE_ITEMIZE)).sort(_ROW)
    standard = out.filter(~pl.col(FORCE_ITEMIZE)).sort(_ROW)
    # Ties (to round-off) go to the standard deduction, as in TAXSIM.
    itemize_wins = (
        (itemized.get_column("fiitax") + itemized.get_column("siitax"))
        < (standard.get_column("fiitax") + standard.get_column("siitax")) - _TIE_TOLERANCE
    )
    columns = [c for c in itemized.columns if c not in _KEYS]
    return pl.DataFrame(
        {col: itemized.get_column(col).zip_with(itemize_wins, standard.get_column(col)) for col in columns}
    )
