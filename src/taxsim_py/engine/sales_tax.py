"""Optional state/local sales tax deduction (the itemizer's alternative to
deducting state income tax - see parameters/states/tx/sales_tax_deduction.yaml
for where these constants come from and their scope).
"""

import polars as pl


def sales_tax_deduction(
    household_resources: pl.Expr, family_size: pl.Expr, a: float, b: float, c: float
) -> pl.Expr:
    return (
        pl.when((household_resources > 0) & (family_size > 0))
        .then((a + b * household_resources.log() + c * family_size.log()).exp())
        .otherwise(0.0)
    )
