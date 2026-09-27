"""Alaska individual income tax calculator."""

import polars as pl

from taxsim_py.engine.inputs import files_head_of_household, files_joint, files_separate
from taxsim_py.engine.brackets import bracket_rate, bracket_tax
from taxsim_py.engine.schema import PARAMETERS_ROOT, YearParams, load_yaml
from taxsim_py.engine.state import with_state_detail

AK_PARAMS = load_yaml(PARAMETERS_ROOT / "states" / "ak" / "income_tax.yaml")


def compute_ak_tax(df: pl.DataFrame, year: int) -> pl.DataFrame:
    if year > 1979:
        return df.with_columns(siitax=pl.lit(0.0))

    p = YearParams(AK_PARAMS, year)
    std_single = p.num("standard_deduction_single")
    std_joint = p.num("standard_deduction_married_joint")
    std_sep = p.num("standard_deduction_married_separate")
    std_hoh = p.num("standard_deduction_head_of_household")
    flat_credit = p.num("flat_credit")

    df = df.with_columns(
        ak_stded=pl.when(files_joint())
        .then(std_joint)
        .when(files_separate())
        .then(std_sep)
        .when(files_head_of_household())
        .then(std_hoh)
        .otherwise(std_single)
    )
    # AGI is federal AGI directly, no addbacks of any kind.
    # Reported taxable income may be negative; the bracket lookup floors it.
    df = df.with_columns(ak_taxinc=pl.col("agi") - pl.col("ak_stded"))

    # married_separate reuses SINGLE's own bracket table (not its own, not
    # married_joint/2) - only its standard deduction differs; head_of_
    # household gets its own table.
    brackets_single = p["brackets_single"]
    brackets_joint = p["brackets_married_joint"]
    brackets_hoh = p["brackets_head_of_household"]
    def by_status(lookup) -> pl.Expr:
        taxinc = pl.col("ak_taxinc").clip(0, None)
        return (
            pl.when(files_joint())
            .then(lookup(taxinc, brackets_joint))
            .when(files_head_of_household())
            .then(lookup(taxinc, brackets_hoh))
            .otherwise(lookup(taxinc, brackets_single))
        )

    df = df.with_columns(ak_regtax=by_status(bracket_tax))

    # Flat, year-specific credit, doubled on joint returns.
    df = df.with_columns(
        ak_flat_credit=pl.when(files_joint()).then(2.0 * flat_credit).otherwise(flat_credit)
    )
    df = df.with_columns(siitax=(pl.col("ak_regtax") - pl.col("ak_flat_credit")).clip(0, None))
    # The reported credit excludes the flat credit.
    return with_state_detail(
        df,
        agi=pl.col("agi"),
        standard_deduction=pl.col("ak_stded"),
        taxable_income=pl.col("ak_taxinc"),
        rate=by_status(bracket_rate),
    )
