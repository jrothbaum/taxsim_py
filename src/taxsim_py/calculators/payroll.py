"""Federal payroll tax calculator."""

import polars as pl

from taxsim_py.engine.payroll_tax import PAYROLL_ITEMS, taxsim_payroll
from taxsim_py.engine.schema import PARAMETERS_ROOT, load_yaml, resolve_year
from taxsim_py.engine.state import with_defaults

PAYROLL_TAX_PARAMS = load_yaml(PARAMETERS_ROOT / "national" / "payroll_tax.yaml")

FILING_STATUSES = ["single", "married_joint", "married_separate", "head_of_household"]
PAYROLL_INPUTS = tuple(name for names in PAYROLL_ITEMS for name in names)


def payroll_parts(year: int) -> dict[str, pl.Expr]:
    """TAXSIM's payroll figures for `year` (see `engine.payroll_tax.taxsim_payroll`)."""
    p = PAYROLL_TAX_PARAMS
    threshold_expr = pl.lit(None, dtype=pl.Float64)
    for status in FILING_STATUSES:
        value = float(resolve_year(p["additional_medicare_threshold"][status], year))
        threshold_expr = pl.when(pl.col("filing_status") == status).then(pl.lit(value)).otherwise(threshold_expr)
    return taxsim_payroll(
        wage_base=float(resolve_year(p["oasdi_wage_base"], year)),
        hi_wage_base=float(resolve_year(p["hi_wage_base"], year)),
        oasdi_rate=float(resolve_year(p["oasdi_rate_combined"], year)),
        se_oasdi_rate=float(resolve_year(p["se_oasdi_rate"], year)),
        hi_rate=float(resolve_year(p["se_hi_rate"], year)),
        net_earnings_factor=float(resolve_year(p["se_net_earnings_factor"], year)),
        addmed_rate=float(resolve_year(p["additional_medicare_rate"], year)),
        addmed_threshold=threshold_expr,
    )


def compute_payroll_tax(
    df: pl.DataFrame | pl.LazyFrame, year: int
) -> pl.DataFrame | pl.LazyFrame:
    df = with_defaults(df, PAYROLL_INPUTS)
    parts = payroll_parts(year)
    # Reuse the figures the federal calculation already computed.
    present = set(df.collect_schema().names())
    parts = {name: pl.col(f"__payroll_{name}") if f"__payroll_{name}" in present else expr for name, expr in parts.items()}
    return df.with_columns(
        fica=parts["fica"],
        tfica=parts["tfica"],
        addmed=parts["addmed"],
        ficar=((parts["oasdi_rate_primary"] + parts["hi_rate_primary"]) * 100).round(2),
    ).drop([f"__payroll_{name}" for name in parts if f"__payroll_{name}" in present])
