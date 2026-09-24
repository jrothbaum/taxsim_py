"""Alaska individual income tax calculator."""

import polars as pl

from taxsim_py.engine.brackets import bracket_tax
from taxsim_py.engine.schema import PARAMETERS_ROOT, load_yaml, resolve_year

AK_PARAMS = load_yaml(PARAMETERS_ROOT / "states" / "ak" / "income_tax.yaml")


def compute_ak_tax(df: pl.DataFrame, year: int) -> pl.DataFrame:
    if year > 1979:
        return df.with_columns(siitax=pl.lit(0.0))

    p = AK_PARAMS
    std_single = float(resolve_year(p["standard_deduction_single"], year))
    std_joint = float(resolve_year(p["standard_deduction_married_joint"], year))
    std_sep = float(resolve_year(p["standard_deduction_married_separate"], year))
    std_hoh = float(resolve_year(p["standard_deduction_head_of_household"], year))
    flat_credit = float(resolve_year(p["flat_credit"], year))

    df = df.with_columns(
        ak_stded=pl.when(pl.col("filing_status") == "married_joint")
        .then(std_joint)
        .when(pl.col("filing_status") == "married_separate")
        .then(std_sep)
        .when(pl.col("filing_status") == "head_of_household")
        .then(std_hoh)
        .otherwise(std_single)
    )
    # AGI is federal AGI directly, no addbacks of any kind.
    df = df.with_columns(ak_taxinc=(pl.col("agi") - pl.col("ak_stded")).clip(0, None))

    # married_separate reuses SINGLE's own bracket table (not its own, not
    # married_joint/2) - only its standard deduction differs; head_of_
    # household gets its own table.
    brackets_single = p["brackets_single"]
    brackets_joint = p["brackets_married_joint"]
    brackets_hoh = p["brackets_head_of_household"]
    df = df.with_columns(
        ak_regtax=pl.when(pl.col("filing_status") == "married_joint")
        .then(bracket_tax(pl.col("ak_taxinc"), brackets_joint))
        .when(pl.col("filing_status") == "head_of_household")
        .then(bracket_tax(pl.col("ak_taxinc"), brackets_hoh))
        .otherwise(bracket_tax(pl.col("ak_taxinc"), brackets_single))
    )

    # Flat, year-specific credit (doubled for married_joint only) - the
    # "minimum tax" term and the small win-credit-based credit are both
    # confirmed inert given this project's schema (see the YAML note).
    df = df.with_columns(
        ak_flat_credit=pl.when(pl.col("filing_status") == "married_joint").then(2.0 * flat_credit).otherwise(flat_credit)
    )
    df = df.with_columns(siitax=(pl.col("ak_regtax") - pl.col("ak_flat_credit")).clip(0, None))
    return df
