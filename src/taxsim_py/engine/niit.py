"""Net Investment Income Tax: 3.8% on the lesser of net investment income
or AGI over a threshold, after subtracting the state/local tax
attributable to that investment income (pro-rated by its share of AGI,
capped). Added on top of regular tax + AMT, outside the pool nonrefundable
credits compete for (taxsim_2022_10_21.f:25999-26026: the credit-cap
"avail" never includes it, but the final tax total does).
"""

import polars as pl


def net_investment_income_tax(
    net_investment_income: pl.Expr,
    agi: pl.Expr,
    threshold: pl.Expr,
    rate: float,
    state_tax_deduction_claimed: pl.Expr,
    expense_cap: float,
) -> pl.Expr:
    investment_share = pl.when(agi > 0).then((net_investment_income / agi).clip(0, 1)).otherwise(0.0)
    expense = state_tax_deduction_claimed.clip(0, expense_cap) * investment_share
    net_income_after_expense = (net_investment_income - expense).clip(0, None)
    excess_agi = (agi - threshold).clip(0, None)
    return pl.when(agi > threshold).then(
        rate * pl.min_horizontal(net_income_after_expense, excess_agi)
    ).otherwise(0.0)
