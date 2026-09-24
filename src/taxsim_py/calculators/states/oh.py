"""Ohio individual income tax calculator."""

import polars as pl

from taxsim_py.engine.brackets import bracket_tax
from taxsim_py.engine.schema import PARAMETERS_ROOT, load_yaml, resolve_year
from taxsim_py.engine.state import checkpoint, interpolate_table, tier_values, with_defaults
from taxsim_py.engine.state_extrapolation import deflate_for_extrapolation, resolve_state_year

OH_PARAMS = load_yaml(PARAMETERS_ROOT / "states" / "oh" / "income_tax.yaml")


def _p(name: str, year: int) -> float:
    return float(resolve_year(OH_PARAMS[name], year))


def _brackets(y: int) -> list[list[float]]:
    brackets = resolve_year(OH_PARAMS["brackets"], y)
    if 2008 <= y <= 2012:
        factor = _p("bracket_inflation", y)
        return [[float(start) * factor, float(rate)] for start, rate in brackets]
    return brackets


def _joint_credit(statax: pl.Expr, taxinc: pl.Expr, businc: pl.Expr, y: int, eligible: pl.Expr) -> pl.Expr:
    """Tax after the joint filing credit (`ohjoin`)."""
    p = OH_PARAMS
    if y < 1983:
        share = interpolate_table(taxinc, p["joint_credit_1977"])
    elif y == 1983:
        share = interpolate_table(taxinc, p["joint_credit_1983"])
    else:
        rows = p["joint_credit_1984"]
        (share,) = tier_values(taxinc + businc, [r[0] for r in rows], [r[1] for r in rows], strict=True)
    jcred = (share * statax).clip(0, None)
    if y >= 1989:
        jcred = jcred.clip(None, p["joint_credit_cap_1989"])
    return pl.when(eligible).then((statax - jcred).clip(0, None)).otherwise(statax)


def compute_oh_tax(df: pl.DataFrame, year: int) -> pl.DataFrame:
    """Calculate Ohio income tax for each row."""
    effective_year, flate = resolve_state_year(year)
    y = effective_year
    p = OH_PARAMS
    df = with_defaults(df, (
        "psemp", "ssemp", "depx", "taxable_unemployment", "earned_income", "ccc_uncapped", "eitc",
        "se_adjustment", "pre1987_twoded",
    ))
    df = deflate_for_extrapolation(
        df, flate,
        ["pwages", "swages", "psemp", "ssemp", "agi", "taxable_unemployment", "earned_income", "eitc", "se_adjustment"],
    )

    status = pl.col("filing_status")
    is_joint = status == "married_joint"
    sep = pl.when(status == "married_separate").then(2.0).otherwise(1.0)
    taxpayers = pl.when(is_joint).then(2.0).otherwise(1.0)
    depx = pl.col("depx")
    exemptions = taxpayers + depx

    # --- AGI and the business income deduction ---
    agi = pl.col("agi")
    businc = pl.lit(0.0)
    statb = pl.lit(0.0)
    if y >= 2015:
        business = (pl.col("psemp") + pl.col("ssemp")).clip(0, None)
        busded = pl.min_horizontal(_p("business_deduction_share", y) * business, _p("business_deduction_cap", y) / sep)
        agi = agi - busded
        businc = (business - busded).clip(0, None)
        statb = bracket_tax(businc, p["business_brackets"])
    df, (agi, businc) = checkpoint(df, oh_agi=agi, oh_businc=businc)

    # --- Exemptions ---
    amount = _p("exemption", y)
    if 1996 <= y <= 1998:
        exemp = amount * taxpayers + _p("dependent_exemption_1996_1998", y) * depx
    else:
        if y >= 2014:
            amount = (
                pl.when(agi <= p["exemption_low_income_agi"]).then(_p("exemption_low_income", y))
                .when(agi <= p["exemption_middle_income_agi"]).then(_p("exemption_middle_income", y))
                .otherwise(amount)
            )
        exemp = amount * exemptions
    df, (taxinc,) = checkpoint(df, oh_taxinc=(agi - businc - exemp).clip(0, None))

    # --- Child care credit (share of the federal credit before its tax limit) ---
    chcrbc = pl.col("ccc_uncapped")
    regcr = pl.lit(0.0)
    if 1989 <= y <= 1992:
        c = p["child_care_1989_1992"]
        regcr = pl.when(agi < c["agi_limit"]).then(pl.min_horizontal(c["rate"] * chcrbc, pl.lit(c["cap"]))).otherwise(0.0)
    elif y >= 1993:
        c = p["child_care_1993_1996"] if y <= 1996 else p["child_care_1997"]
        regcr = pl.when(agi < c["agi_limit"]).then(
            pl.when(agi < c["low_agi"]).then(c["low_rate"] * chcrbc).otherwise(c["rate"] * chcrbc)
        ).otherwise(0.0)

    excred = pl.lit(0.0)
    if 1983 <= y <= 2012:
        excred = p["exemption_credit"] * exemptions
    elif y >= 2013:
        excred = pl.when(taxinc < p["exemption_credit_income_limit_2013"]).then(p["exemption_credit"] * exemptions).otherwise(0.0)

    # --- Joint filing credit eligibility: each spouse's income share ---
    wages = pl.col("pwages").clip(0, None) + pl.col("swages").clip(0, None)
    adjust = pl.col("se_adjustment") if y >= 1987 else (pl.col("pre1987_twoded") if y >= 1982 else pl.lit(0.0))
    xlind = wages + pl.col("psemp") + pl.col("ssemp") + pl.col("taxable_unemployment") - adjust
    hagi = pl.col("pwages").clip(0, None) + 0.5 * (xlind - wages)
    wagi = xlind - hagi
    min_income = p["joint_credit_min_spouse_income"]
    joint_eligible = (hagi >= min_income) & (wagi >= min_income) & (pl.col("earned_income") >= p["joint_credit_min_earned"])
    df, (joint_eligible,) = checkpoint(df, oh_joint_eligible=joint_eligible)

    brackets = _brackets(y)
    if y <= 1982:
        # Through 1988 the joint filing credit is not limited to joint returns.
        statax = (bracket_tax(taxinc, brackets) - regcr).clip(0, None)
        statax = _joint_credit(statax, taxinc, businc, y, joint_eligible)
    elif y <= 1988:
        # Method 1 (a deduction per exemption, then the joint credit) unless
        # method 2 (a credit per exemption) is lower than method 1 before the
        # joint credit, as the oracle behaves; the exemption credit then
        # applies again.
        dedy = p["exemption_deduction_1983_1988"] * exemptions
        stax1 = bracket_tax((taxinc - dedy).clip(0, None), brackets) - regcr
        stax2 = bracket_tax(taxinc, brackets) - regcr - p["exemption_credit"] * exemptions
        if y != 1983:
            stax1 = stax1.clip(0, None)
            stax2 = stax2.clip(0, None)
        df, (stax1, stax2) = checkpoint(df, oh_stax1=stax1, oh_stax2=stax2)
        statax = pl.when(stax2 < stax1).then(stax2).otherwise(_joint_credit(stax1, taxinc, businc, y, joint_eligible))
        statax = (statax - excred).clip(0, None)
    else:
        statax = bracket_tax(taxinc, brackets)
        if y >= 2017:
            statax = pl.when(taxinc <= _p("zero_bracket", y)).then(0.0).otherwise(statax)
        statax = statax + statb
        statax = (statax - regcr).clip(0, None)
        statax = (statax - excred).clip(0, None)
        df, (statax,) = checkpoint(df, oh_statax=statax)
        earncr = pl.lit(0.0)
        if y >= 2013:
            earncr = _p("eitc_rate", y) * pl.col("eitc")
            if y <= 2018:
                earncr = pl.when(taxinc + businc > p["eitc_income_limit"]).then(
                    pl.min_horizontal(earncr, p["eitc_tax_share_limit"] * statax)
                ).otherwise(earncr)
        statax = _joint_credit(statax, taxinc, businc, y, is_joint & joint_eligible)
        statax = (statax - earncr).clip(0, None)

    if 2005 <= y <= 2016:
        statax = pl.when(taxinc <= p["low_income_exemption_2005_2016"]).then(0.0).otherwise(statax)

    df = df.with_columns(siitax=statax * flate)
    return df
