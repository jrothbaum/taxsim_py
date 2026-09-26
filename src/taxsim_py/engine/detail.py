"""TAXSIM's detailed federal output (`idtl=2`) from resolved federal columns."""

import polars as pl

from taxsim_py.engine.schema import PARAMETERS_ROOT, load_yaml

# Detailed federal outputs, in TAXSIM's order.
FEDERAL_DETAIL_COLUMNS = (
    "credits", *[f"v{i}" for i in range(10, 30)], "v42", "v43", "v44", "v45",
)


def federal_detail(frame: pl.DataFrame, year: pl.Expr) -> list[pl.Expr]:
    """Expressions for `FEDERAL_DETAIL_COLUMNS` over a resolved frame.

    Each is the federal figure TAXSIM prints in that position; pre-1987 law
    fills different slots, so those rows read the `pre1987_` columns.
    """
    present = set(frame.columns)

    def col(name: str) -> pl.Expr:
        return pl.col(name).fill_null(0.0) if name in present else pl.lit(0.0)

    pre = year < 1987
    itemizes = pl.col("itemizes").fill_null(False) if "itemizes" in present else pl.lit(False)
    # Nonrefundable credits, reported as tax before credits once they reach it.
    nonrefundable = pl.when(year == 2021).then(0.0).otherwise(col("ccc") + col("odc")) + col("elderly_credit_raw")
    credits_1998 = pl.when(year < 1998).then(0.0).otherwise(
        pl.min_horizontal(nonrefundable, col("tax_before_credits").clip(0, None))
    )
    return [
        pl.when(pre).then(col("credit")).otherwise(credits_1998).alias("credits"),
        col("agi").alias("v10"),
        col("taxable_unemployment").alias("v11"),
        col("taxable_social_security").alias("v12"),
        pl.when(pre).then(col("pre1987_zbr"))
        .when(itemizes).then(0.0)
        .otherwise(col("standard_deduction")).alias("v13"),
        pl.when(pre).then(col("pre1987_amex")).otherwise(col("personal_exemptions")).alias("v14"),
        col("exemption_phaseout").alias("v15"),
        col("deduction_phaseout").alias("v16"),
        pl.when(pre).then(col("pre1987_deduc"))
        .when(itemizes).then(col("itemized_deduction"))
        .otherwise(0.0).alias("v17"),
        pl.when(pre).then(col("pre1987_taxinc")).otherwise(col("taxable_income")).alias("v18"),
        pl.when(pre).then(col("regular_tax")).otherwise(col("schedule_tax")).alias("v19"),
        col("exemption_surtax").alias("v20"),
        pl.when(pre).then(col("pre1987_gencr")).otherwise(0.0).alias("v21"),
        pl.when(year == 2021).then(col("odc") + col("actc")).otherwise(col("odc")).alias("v22"),
        col("actc").alias("v23"),
        col("federal_chcr").alias("v24"),
        pl.when(pre).then(col("pre1987_earncr")).otherwise(col("eitc")).alias("v25"),
        pl.when(pre).then(col("pre1987_alminy")).otherwise(col("amt_income")).alias("v26"),
        pl.when(pre).then(col("pre1987_almtax")).otherwise(col("amt")).alias("v27"),
        pl.when(pre).then(col("pre1987_taxbc")).otherwise(col("regular_tax")).alias("v28"),
        col("fica").alias("v29"),
        # TAXSIM's slot 182 (`ssearn`) is never assigned.
        pl.lit(0.0).alias("v42"),
        col("niit").alias("v43"),
        col("addmed").alias("v44"),
        col("cares").alias("v45"),
    ]


# Detailed state outputs, in TAXSIM's order (`idtl=2`, the state routines'
# shared `/calc/` block).
STATE_DETAIL_COLUMNS = (*[f"v{i}" for i in range(30, 42)], "staxbc")

# The column each state calculator sets for a detail output, in the law
# year's dollars (deflated in projected years, as TAXSIM reports them).
# A calculator that does not set one reports 0, as TAXSIM zeroes the block
# before every state call.
STATE_DETAIL_SOURCES = {
    "v32": "state_agi",
    "v33": "state_exemptions",
    "v34": "state_standard_deduction",
    "v35": "state_itemized_deductions",
    "v36": "state_taxable_income",
    "v37": "state_property_credit",
    "v38": "state_child_care_credit",
    "v39": "state_eic",
    "v40": "state_credits",
    "v41": "state_rate",
}

_ADJUSTMENTS = load_yaml(PARAMETERS_ROOT / "national" / "state_adjustments.yaml")


def state_detail(frame: pl.DataFrame) -> list[pl.Expr]:
    """Expressions for `STATE_DETAIL_COLUMNS` over a resolved frame.

    Household income and rent are the undeflated inputs; the other outputs
    come from the state calculator's detail columns. TAXSIM never sets the
    tax before credits it prints (`staxbc`), so it is 0.
    """
    from taxsim_py.engine.state import household_income

    present = set(frame.columns)

    def col(name: str) -> pl.Expr:
        return pl.col(name).fill_null(0.0) if name in present else pl.lit(0.0)

    has_state = pl.col("state") > 0
    hy = household_income(
        float(_ADJUSTMENTS["household_income_dividend_adjustment"][1960]),
        float(_ADJUSTMENTS["household_income_record_adjustment"][1960]),
    )
    return [
        pl.when(has_state).then(hy).otherwise(0.0).alias("v30"),
        pl.when(has_state).then(col("rentpaid")).otherwise(0.0).alias("v31"),
        *[
            pl.when(has_state).then(col(source) * (100.0 if name == "v41" else 1.0)).otherwise(0.0).alias(name)
            for name, source in STATE_DETAIL_SOURCES.items()
        ],
        pl.lit(0.0).alias("staxbc"),
    ]
