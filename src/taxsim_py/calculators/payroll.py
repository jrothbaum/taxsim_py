"""Federal payroll tax calculator."""

import polars as pl

from taxsim_py.behavior import BehaviorProfile, TAXSIM_BEHAVIOR
from taxsim_py.engine.payroll_tax import PAYROLL_ITEMS, allocated_self_employment_items, taxsim_payroll
from taxsim_py.engine.schema import PARAMETERS_ROOT, YearParams, load_yaml, resolve_year
from taxsim_py.engine.state import by_filing_status, with_defaults

PAYROLL_TAX_PARAMS = load_yaml(PARAMETERS_ROOT / "national" / "payroll_tax.yaml")

FILING_STATUSES = ["single", "married_joint", "married_separate", "head_of_household"]
PAYROLL_INPUTS = tuple(name for names in PAYROLL_ITEMS for name in names)


def payroll_parts(year: int, behavior: BehaviorProfile = TAXSIM_BEHAVIOR) -> dict[str, pl.Expr]:
    """TAXSIM's payroll figures for `year` (see `engine.payroll_tax.taxsim_payroll`)."""
    p = YearParams(PAYROLL_TAX_PARAMS, year)
    threshold_expr = by_filing_status(
        {status: resolve_year(p["additional_medicare_threshold"][status], year) for status in FILING_STATUSES}
    )
    return taxsim_payroll(
        wage_base=p.num("oasdi_wage_base"),
        hi_wage_base=p.num("hi_wage_base"),
        oasdi_rate=p.num("oasdi_rate_combined"),
        se_oasdi_rate=p.num("se_oasdi_rate"),
        hi_rate=p.num("se_hi_rate"),
        net_earnings_factor=p.num("se_net_earnings_factor"),
        addmed_rate=p.num("additional_medicare_rate"),
        addmed_threshold=threshold_expr,
        own_share_self_employment=float(p["own_share_self_employment"]),
        behavior=behavior,
    )


def payroll_prerequisites(year: int, behavior: BehaviorProfile = TAXSIM_BEHAVIOR) -> dict[str, pl.Expr]:
    """Columns to add before `payroll_parts(year, behavior)` expressions are evaluated."""
    return allocated_self_employment_items(YearParams(PAYROLL_TAX_PARAMS, year).num("se_net_earnings_factor"), behavior)


def compute_payroll_tax(
    df: pl.DataFrame | pl.LazyFrame,
    year: int,
    behavior: BehaviorProfile = TAXSIM_BEHAVIOR,
) -> pl.DataFrame | pl.LazyFrame:
    df = with_defaults(df, PAYROLL_INPUTS)
    parts = payroll_parts(year, behavior)
    # Reuse the figures the federal calculation already computed.
    present = set(df.collect_schema().names())
    prerequisites = {} if all(f"__payroll_{name}" in present for name in parts) else payroll_prerequisites(year, behavior)
    df = df.with_columns(**prerequisites)
    parts = {name: pl.col(f"__payroll_{name}") if f"__payroll_{name}" in present else expr for name, expr in parts.items()}
    return df.with_columns(
        fica=parts["fica"],
        tfica=parts["tfica"],
        addmed=parts["addmed"],
        payroll_setax=parts["setax"],
        own_fica_primary=parts["own_fica_primary"],
        own_wage_fica_primary=parts["own_wage_fica_primary"],
        own_fica_secondary=parts["own_fica_secondary"],
        own_wage_fica_secondary=parts["own_wage_fica_secondary"],
        ficar=((parts["oasdi_rate_primary"] + parts["hi_rate_primary"]) * 100).round(2),
    ).drop([f"__payroll_{name}" for name in parts if f"__payroll_{name}" in present], *prerequisites)
