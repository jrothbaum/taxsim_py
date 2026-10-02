"""Pennsylvania personal income tax calculator."""

import polars as pl

from taxsim_py.engine.inputs import aged_count, files_joint, taxpayer_count
from taxsim_py.engine.schema import PARAMETERS_ROOT, YearParams, load_yaml, resolve_year
from taxsim_py.engine.state import with_state_detail, dividend_input_adjustment
from taxsim_py.behavior import BehaviorProfile, TAXSIM_BEHAVIOR
from taxsim_py.engine.state_extrapolation import deflate_for_extrapolation, resolve_state_year

PA_PARAMS = load_yaml(PARAMETERS_ROOT / "states" / "pa" / "income_tax.yaml")


def compute_pa_tax(df: pl.DataFrame, year: int, behavior: BehaviorProfile = TAXSIM_BEHAVIOR) -> pl.DataFrame:
    """Calculate Pennsylvania income tax for each row."""
    state_year = "pa" if behavior.mode.value == "statutory" else None
    effective_year, flate = resolve_state_year(year, state_year)
    y = effective_year
    p = YearParams(PA_PARAMS, effective_year)
    dividend_adjustment = dividend_input_adjustment()
    # Federal Schedule E income (`comnew(8)`): other property income, plus S
    # corporation income from 1987.
    df = df.with_columns(pa_schede=pl.col("otherprop") + (pl.col("scorp") if year >= 1987 else 0.0))
    df = deflate_for_extrapolation(df, flate, extra=("pa_schede",))

    # Positive income classes only; negative wages and capital losses do not
    # offset. Business income is not deflated in projected years. Pensions
    # are exempt for taxpayers 65 or older.
    business = (
        pl.col("psemp") + pl.col("ssemp") + pl.col("pbusinc") + pl.col("pprofinc") + pl.col("sbusinc")
        + pl.col("sprofinc")
    )
    income = (
        pl.col("pwages").clip(0, None) + pl.col("swages").clip(0, None)
        + pl.col("dividends") + dividend_adjustment + pl.col("intrec")
        + business.clip(0, None)
        + (pl.col("stcg") + pl.col("ltcg")).clip(0, None)
        + pl.col("pa_schede")
        + pl.when(aged_count() > 0).then(0.0).otherwise(pl.col("pensions"))
    )
    taxinc = income.clip(0, None)
    statax = taxinc * p.num("rate")

    # --- Tax forgiveness ---
    is_joint = files_joint()
    taxpayers = taxpayer_count()
    nkid = pl.col("depx")
    if y <= 1994:
        # The spouse is counted as the first claimant after the filer.
        # A dependent filer (no taxpayer) counts no one.
        dep1 = pl.when(taxpayers < 1).then(0.0).when(is_joint | (nkid >= 1)).then(1.0).otherwise(0.0)
        dep2 = pl.when(taxpayers < 1).then(0.0).when(is_joint).then(nkid).otherwise((nkid - 1).clip(0, None))
    else:
        dep1 = taxpayers
        dep2 = nkid
    a = resolve_year(PA_PARAMS["forgiveness_allowance"], y)
    if y <= 1993:
        allow = a["base"] + dep1 * a["first"] + dep2 * a["other"]
    elif y == 1994:
        allow = pl.when(is_joint).then(a["joint_base"]).otherwise(a["base"]) + a["dependent"] * nkid
    elif y <= 1996:
        allow = a["base"] + (dep1 - 1 + dep2) * a["dependent"]
    elif y == 1998:
        single_allow = a["first"] * (dep1 + dep2.clip(None, 1)) + (dep2 - 1).clip(0, None) * a["other"]
        allow = pl.when(is_joint).then(a["first"] * dep1 + dep2 * a["other"]).otherwise(single_allow)
    else:
        allow = a["first"] * dep1 + a["other"] * dep2
    remain = (taxinc - allow).clip(0, None)
    forgiven_share = (1.0 - remain / p.num("forgiveness_phaseout")).clip(0, None)
    if y >= 2022:
        # Actual-law years: forgiveness falls 10 points for each $250 (or part) over the allowance.
        step = PA_PARAMS["forgiveness_step_2022plus"]
        forgiven_share = (1.0 - float(step["share"]) * (remain / float(step["income"])).ceil()).clip(0, None)
    credit = statax * forgiven_share
    statax = statax - credit

    df = df.with_columns(pa_taxinc=taxinc, siitax=statax * flate)
    return with_state_detail(
        df, agi=income, taxable_income=taxinc, credits=credit, rate=pl.lit(p.num("rate"))
    )
