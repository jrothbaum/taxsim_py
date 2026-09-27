"""Meaningfully named detailed outputs and their TAXSIM compatibility labels."""

import polars as pl


# Detailed federal outputs, in TAXSIM's order. Internal and default public
# names describe the value; the opaque TAXSIM labels are applied only at the
# API boundary when explicitly requested.
FEDERAL_DETAIL_NAME_TO_TAXSIM = {
    "federal_nonrefundable_credits": "credits",
    "federal_adjusted_gross_income": "v10",
    "federal_taxable_unemployment_compensation": "v11",
    "federal_taxable_social_security_benefits": "v12",
    "federal_standard_deduction": "v13",
    "federal_personal_exemptions": "v14",
    "federal_exemption_phaseout": "v15",
    "federal_itemized_deduction_phaseout": "v16",
    "federal_itemized_deductions": "v17",
    "federal_taxable_income": "v18",
    "federal_income_tax_before_credits": "v19",
    "federal_exemption_surtax": "v20",
    "federal_general_tax_credit": "v21",
    "federal_child_tax_credit": "v22",
    "federal_additional_child_tax_credit": "v23",
    "federal_child_and_dependent_care_credit": "v24",
    "federal_earned_income_tax_credit": "v25",
    "federal_alternative_minimum_taxable_income": "v26",
    "federal_alternative_minimum_tax": "v27",
    "federal_regular_income_tax": "v28",
    "federal_payroll_tax": "v29",
    "federal_social_security_taxable_earnings": "v42",
    "federal_net_investment_income_tax": "v43",
    "federal_additional_medicare_tax": "v44",
    "federal_recovery_rebate_credit": "v45",
}
FEDERAL_DETAIL_COLUMNS = tuple(FEDERAL_DETAIL_NAME_TO_TAXSIM)
TAXSIM_FEDERAL_DETAIL_COLUMNS = tuple(FEDERAL_DETAIL_NAME_TO_TAXSIM.values())


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
    credits_1998 = pl.when(year < 1998).then(col("nonrefundable_credits")).otherwise(
        pl.min_horizontal(nonrefundable, col("tax_before_credits").clip(0, None))
    )
    return [
        pl.when(pre).then(col("credit")).otherwise(credits_1998).alias("federal_nonrefundable_credits"),
        col("agi").alias("federal_adjusted_gross_income"),
        col("taxable_unemployment").alias("federal_taxable_unemployment_compensation"),
        col("taxable_social_security").alias("federal_taxable_social_security_benefits"),
        pl.when(pre).then(col("pre1987_zbr"))
        .when(itemizes).then(0.0)
        .otherwise(col("standard_deduction")).alias("federal_standard_deduction"),
        pl.when(pre)
        .then(col("pre1987_amex"))
        .otherwise(col("personal_exemptions"))
        .alias("federal_personal_exemptions"),
        col("exemption_phaseout").alias("federal_exemption_phaseout"),
        col("deduction_phaseout").alias("federal_itemized_deduction_phaseout"),
        pl.when(pre).then(col("pre1987_deduc"))
        .when(itemizes).then(col("itemized_deduction"))
        .otherwise(0.0).alias("federal_itemized_deductions"),
        pl.when(pre).then(col("pre1987_taxinc")).otherwise(col("taxable_income")).alias("federal_taxable_income"),
        pl.when(pre)
        .then(col("regular_tax"))
        .otherwise(col("schedule_tax"))
        .alias("federal_income_tax_before_credits"),
        col("exemption_surtax").alias("federal_exemption_surtax"),
        pl.when(pre).then(col("pre1987_gencr")).otherwise(0.0).alias("federal_general_tax_credit"),
        pl.when(year == 2021).then(col("odc") + col("actc")).otherwise(col("odc")).alias("federal_child_tax_credit"),
        col("actc").alias("federal_additional_child_tax_credit"),
        col("federal_chcr").alias("federal_child_and_dependent_care_credit"),
        pl.when(pre).then(col("pre1987_earncr")).otherwise(col("eitc")).alias("federal_earned_income_tax_credit"),
        pl.when(pre)
        .then(col("pre1987_alminy"))
        .otherwise(col("amt_income"))
        .alias("federal_alternative_minimum_taxable_income"),
        pl.when(pre).then(col("pre1987_almtax")).otherwise(col("amt")).alias("federal_alternative_minimum_tax"),
        pl.when(pre).then(col("pre1987_taxbc")).otherwise(col("regular_tax")).alias("federal_regular_income_tax"),
        col("fica").alias("federal_payroll_tax"),
        # TAXSIM's slot 182 (`ssearn`) is never assigned.
        pl.lit(0.0).alias("federal_social_security_taxable_earnings"),
        col("niit").alias("federal_net_investment_income_tax"),
        col("addmed").alias("federal_additional_medicare_tax"),
        col("cares").alias("federal_recovery_rebate_credit"),
    ]


# Detailed state outputs, in TAXSIM's order (`idtl=2`, the state routines'
# shared `/calc/` block).
STATE_DETAIL_NAME_TO_TAXSIM = {
    "state_household_income": "v30",
    "state_rent_paid": "v31",
    "state_adjusted_gross_income": "v32",
    "state_exemptions": "v33",
    "state_standard_deduction": "v34",
    "state_itemized_deductions": "v35",
    "state_taxable_income": "v36",
    "state_property_tax_credit": "v37",
    "state_child_and_dependent_care_credit": "v38",
    "state_earned_income_tax_credit": "v39",
    "state_total_credits": "v40",
    "state_marginal_rate_percent": "v41",
    "state_tax_before_credits": "staxbc",
}
STATE_DETAIL_COLUMNS = tuple(STATE_DETAIL_NAME_TO_TAXSIM)
TAXSIM_STATE_DETAIL_COLUMNS = tuple(STATE_DETAIL_NAME_TO_TAXSIM.values())
DETAIL_NAME_TO_TAXSIM = {**FEDERAL_DETAIL_NAME_TO_TAXSIM, **STATE_DETAIL_NAME_TO_TAXSIM}

# The column each state calculator sets for a detail output, in the law
# year's dollars (deflated in projected years, as TAXSIM reports them).
# A calculator that does not set one reports 0, as TAXSIM zeroes the block
# before every state call.
STATE_DETAIL_SOURCES = {
    "state_adjusted_gross_income": "state_adjusted_gross_income",
    "state_exemptions": "state_exemptions",
    "state_standard_deduction": "state_standard_deduction",
    "state_itemized_deductions": "state_itemized_deductions",
    "state_taxable_income": "state_taxable_income",
    "state_property_tax_credit": "state_property_credit",
    "state_child_and_dependent_care_credit": "state_child_and_dependent_care_credit",
    "state_earned_income_tax_credit": "state_earned_income_tax_credit",
    "state_total_credits": "state_total_credits",
    "state_marginal_rate_percent": "state_marginal_rate",
}

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
    hy = household_income()
    return [
        pl.when(has_state).then(hy).otherwise(0.0).alias("state_household_income"),
        pl.when(has_state).then(col("rentpaid")).otherwise(0.0).alias("state_rent_paid"),
        *[
            pl.when(has_state)
            .then(col(source) * (100.0 if name == "state_marginal_rate_percent" else 1.0))
            .otherwise(0.0)
            .alias(name)
            for name, source in STATE_DETAIL_SOURCES.items()
        ],
        pl.lit(0.0).alias("state_tax_before_credits"),
    ]
