"""Ohio individual income tax calculator."""

import polars as pl

from taxsim_py.engine.brackets import bracket_rate, bracket_tax, scale_brackets
from taxsim_py.engine.inputs import aged_count, files_joint, files_separate, is_dependent_filer, taxpayer_count
from taxsim_py.engine.schema import PARAMETERS_ROOT, YearParams, load_yaml
from taxsim_py.engine.state import checkpoint, interpolate_table, tier_values, with_state_detail
from taxsim_py.behavior import BehaviorProfile, TAXSIM_BEHAVIOR
from taxsim_py.engine.state_extrapolation import deflate_for_extrapolation, resolve_state_year

OH_PARAMS = load_yaml(PARAMETERS_ROOT / "states" / "oh" / "income_tax.yaml")


def _taxsim_tax(taxinc: pl.Expr, table: dict) -> tuple[pl.Expr, pl.Expr]:
    """Tax and rate from the TAXSIM build's own table (`tplus` and `tminus`)."""
    rows = table["rows"]
    tops = [float(r[0]) for r in rows]
    rate_pct, third = tier_values(taxinc, tops, [float(r[1]) for r in rows], [float(r[2]) for r in rows], strict=True)
    if table["kind"] == "minus":
        tax = taxinc * rate_pct / 100.0 - third
    else:
        (lower,) = tier_values(taxinc, tops, [0.0] + tops[:-1], strict=True)
        tax = third + rate_pct * (taxinc - lower) / 100.0
    tax = pl.when(taxinc <= float(table["no_tax_at_or_below"])).then(0.0).otherwise(tax)
    return tax, rate_pct / 100.0


def _joint_credit(statax: pl.Expr, taxinc: pl.Expr, businc: pl.Expr, p: YearParams, eligible: pl.Expr) -> pl.Expr:
    """Tax after the joint filing credit."""
    rows = p.value("joint_credit_share")
    if p.year <= 1983:
        share = interpolate_table(taxinc, rows)
    else:
        (share,) = tier_values(taxinc + businc, [r[0] for r in rows], [r[1] for r in rows], strict=True)
    jcred = (share * statax).clip(0, None)
    if p.in_effect("joint_credit_cap"):
        jcred = jcred.clip(None, p.num("joint_credit_cap"))
    return pl.when(eligible).then((statax - jcred).clip(0, None)).otherwise(statax)


def compute_oh_tax(df: pl.DataFrame, year: int, behavior: BehaviorProfile = TAXSIM_BEHAVIOR) -> pl.DataFrame:
    """Calculate Ohio income tax for each row."""
    effective_year, flate = resolve_state_year(year, behavior, OH_PARAMS)
    y = effective_year
    p = YearParams(OH_PARAMS, effective_year)
    df = deflate_for_extrapolation(df, flate)

    is_joint = files_joint()
    sep = pl.when(files_separate()).then(2.0).otherwise(1.0)
    taxpayers = taxpayer_count()
    aged = aged_count()
    depx = pl.col("depx")
    exemptions = taxpayers + depx

    # --- AGI and the business income deduction ---
    agi = pl.col("agi")
    if p.in_effect("pension_exclusion"):
        agi = agi - pl.when(aged > 0).then(
            pl.min_horizontal(pl.col("pensions"), p.num("pension_exclusion"))
        ).otherwise(0.0)
    if y >= 1984:
        agi = agi - pl.col("taxable_social_security")
    businc = pl.lit(0.0)
    statb = pl.lit(0.0)
    business_rate = pl.lit(0.0)
    if p.in_effect("business_deduction_share"):
        business = (pl.col("psemp") + pl.col("ssemp")).clip(0, None)
        busded = pl.min_horizontal(p.num("business_deduction_share") * business, p.num("business_deduction_cap") / sep)
        agi = agi - busded
        businc = (business - busded).clip(0, None)
        statb = bracket_tax(businc, p.value("business_brackets"))
        business_rate = bracket_rate(businc, p.value("business_brackets"))
    df, (agi, businc) = checkpoint(df, oh_agi=agi, oh_businc=businc)

    # --- Exemptions ---
    amount = p.num("exemption")
    if p.in_effect("dependent_exemption"):
        exemp = amount * taxpayers + p.num("dependent_exemption") * depx
    else:
        if p.in_effect("exemption_low_income"):
            amount = (
                pl.when(agi <= p["exemption_low_income_agi"]).then(p.num("exemption_low_income"))
                .when(agi <= p["exemption_middle_income_agi"]).then(p.num("exemption_middle_income"))
                .otherwise(amount)
            )
            if p.in_effect("high_income_limit"):
                amount = pl.when(agi > p.num("high_income_limit")).then(0.0).otherwise(amount)
        if p.in_effect("taxsim_exemption_table") and not behavior.step_ohio_exemption_by_income:
            amount = interpolate_table(agi, p.value("taxsim_exemption_table"))
        exemp = amount * exemptions
    exemp = pl.when(is_dependent_filer()).then(0.0).otherwise(exemp)
    df, (taxinc,) = checkpoint(df, oh_taxinc=(agi - businc - exemp).clip(0, None))

    # --- Child care credit (share of the federal credit before its tax limit) ---
    chcrbc = pl.col("ccc_uncapped")
    regcr = pl.lit(0.0)
    earncr = pl.lit(0.0)
    c = p.value("child_care_credit") if p.in_effect("child_care_credit") else {}
    if "cap" in c:
        regcr = pl.when(agi < c["agi_limit"]).then(pl.min_horizontal(c["rate"] * chcrbc, pl.lit(c["cap"]))).otherwise(0.0)
    elif c:
        regcr = pl.when(agi < c["agi_limit"]).then(
            pl.when(agi < c["low_agi"]).then(c["low_rate"] * chcrbc).otherwise(c["rate"] * chcrbc)
        ).otherwise(0.0)

    # Senior citizen credit, and from 1983 a retirement income credit by
    # pensions, for taxpayers 65 or older.
    if not p.in_effect("retirement_income_credit"):
        senior = pl.lit(p.num("senior_credit"))
    else:
        retcr = interpolate_table(pl.col("pensions"), p.value("retirement_income_credit"))
        if p.in_effect("retirement_income_credit_cap"):
            retcr = retcr.clip(None, p.num("retirement_income_credit_cap"))
        senior = pl.when(taxinc <= p.num("senior_credit_taxable_income_limit")).then(
            p.num("senior_credit") + retcr
        ).otherwise(0.0)
    chcr = regcr
    if p.in_effect("retirement_income_credit") and not behavior.require_age_65_for_ohio_retirement_credit:
        # TAXSIM pays the retirement income credit at any age, from pensions less a cent.
        retcr_any_age = interpolate_table((pl.col("pensions") - 0.01), p.value("retirement_income_credit"))
        if p.in_effect("retirement_income_credit_cap"):
            retcr_any_age = retcr_any_age.clip(None, p.num("retirement_income_credit_cap"))
        limit = p.num("senior_credit_taxable_income_limit")
        within = (taxinc + businc <= limit) if y < 2021 else (taxinc <= limit)
        retcr_any_age = pl.when(within).then(retcr_any_age).otherwise(0.0)
        senior_only = pl.when(within).then(p.num("senior_credit")).otherwise(0.0)
        regcr = regcr + pl.when(aged > 0).then(senior_only).otherwise(0.0) + retcr_any_age
    else:
        regcr = regcr + pl.when(aged > 0).then(senior).otherwise(0.0)

    excred = pl.lit(0.0)
    if p.in_effect("exemption_credit"):
        excred = p.num("exemption_credit") * exemptions
        if p.in_effect("exemption_credit_income_limit"):
            excred = pl.when(taxinc < p.num("exemption_credit_income_limit")).then(excred).otherwise(0.0)

    # --- Joint filing credit eligibility: each spouse's income share ---
    wages = pl.col("pwages").clip(0, None) + pl.col("swages").clip(0, None)
    # Federal adjustments (`comnew(17)`), where non-property income enters negatively.
    adjust = pl.col("se_adjustment") if y >= 1987 else (pl.col("pre1987_twoded") if y >= 1982 else pl.lit(0.0))
    adjust = adjust - pl.col("nonprop")
    xlind = wages + pl.col("psemp") + pl.col("ssemp") + pl.col("pensions") + pl.col("taxable_unemployment") - adjust
    hagi = pl.col("pwages").clip(0, None) + 0.5 * (xlind - wages)
    wagi = xlind - hagi
    min_income = p["joint_credit_min_spouse_income"]
    joint_eligible = (hagi >= min_income) & (wagi >= min_income)
    if p.in_effect("joint_credit_min_earned"):
        joint_eligible = joint_eligible & (pl.col("earned_income") >= p.num("joint_credit_min_earned"))
    if y <= 1988 and behavior.limit_ohio_joint_credit_to_joint_returns:
        # TAXSIM does not test the filing status through 1988.
        joint_eligible = joint_eligible & is_joint
    df, (joint_eligible,) = checkpoint(df, oh_joint_eligible=joint_eligible)

    brackets = scale_brackets(p.value("brackets"), p.num("bracket_inflation"))
    if y <= 1982:
        # Through 1988 the joint filing credit is not limited to joint returns.
        statax = (bracket_tax(taxinc, brackets) - regcr).clip(0, None)
        statax = _joint_credit(statax, taxinc, businc, p, joint_eligible)
        rate = bracket_rate(taxinc, brackets)
    elif p.in_effect("exemption_deduction_alternative"):
        # Method 1 (a deduction per exemption, then the joint credit) unless
        # method 2 (a credit per exemption) is lower than method 1 before the
        # joint credit, as the oracle behaves; the exemption credit then
        # applies again.
        dedy = p.num("exemption_deduction_alternative") * exemptions
        stax1 = bracket_tax((taxinc - dedy).clip(0, None), brackets) - regcr
        stax2 = bracket_tax(taxinc, brackets) - regcr - p.num("exemption_credit") * exemptions
        if y != 1983:
            stax1 = stax1.clip(0, None)
            stax2 = stax2.clip(0, None)
        df, (stax1, stax2) = checkpoint(df, oh_stax1=stax1, oh_stax2=stax2)
        method1 = _joint_credit(stax1, taxinc, businc, p, joint_eligible)
        rate = pl.when(stax2 < stax1).then(bracket_rate(taxinc, brackets)).otherwise(
            bracket_rate((taxinc - dedy).clip(0, None), brackets)
        )
        if behavior.subtract_ohio_exemption_credit_once:
            # Method 2 already includes the exemption credit; both methods get
            # the joint filing credit and the lower tax applies.
            method2 = _joint_credit(stax2, taxinc, businc, p, joint_eligible)
            final1 = (method1 - excred).clip(0, None)
            statax = pl.min_horizontal(final1, method2)
            rate = pl.when(method2 < final1).then(bracket_rate(taxinc, brackets)).otherwise(
                bracket_rate((taxinc - dedy).clip(0, None), brackets)
            )
        else:
            # TAXSIM subtracts the credit again from whichever method wins.
            # It also applies the joint credit to method 1 a second time, and compares method 2
            # with the once-credited method 1.
            twice = _joint_credit(method1, taxinc, businc, p, joint_eligible)
            statax = (pl.when(stax2 < method1).then(stax2).otherwise(twice) - excred).clip(0, None)
            rate = pl.when(stax2 < method1).then(bracket_rate(taxinc, brackets)).otherwise(
                bracket_rate((taxinc - dedy).clip(0, None), brackets)
            )
    else:
        statax = bracket_tax(taxinc, brackets)
        rate = bracket_rate(taxinc, brackets)
        if p.in_effect("taxsim_tax_table") and not behavior.use_ohio_published_tax_brackets:
            statax, rate = _taxsim_tax(taxinc, p.value("taxsim_tax_table"))
        elif p.in_effect("zero_bracket"):
            statax = pl.when(taxinc <= p.num("zero_bracket")).then(0.0).otherwise(statax)
        statax = statax + statb
        statax = (statax - regcr).clip(0, None)
        statax = (statax - excred).clip(0, None)
        df, (statax,) = checkpoint(df, oh_statax=statax)
        if p.in_effect("eitc_rate"):
            earncr = p.num("eitc_rate") * pl.col("eitc")
            if p.in_effect("eitc_income_limit"):
                earncr = pl.when(taxinc + businc > p.num("eitc_income_limit")).then(
                    pl.min_horizontal(earncr, p["eitc_tax_share_limit"] * statax)
                ).otherwise(earncr)
        joint_ok = is_joint & joint_eligible
        if p.in_effect("high_income_limit"):
            joint_ok = joint_ok & ((taxinc + businc) <= p.num("high_income_limit"))
        statax = _joint_credit(statax, taxinc, businc, p, joint_ok)
        statax = (statax - earncr).clip(0, None)

    credits = regcr + excred + earncr
    if p.in_effect("no_tax_threshold"):
        exempt = taxinc <= p.num("no_tax_threshold")
        statax = pl.when(exempt).then(0.0).otherwise(statax)
        chcr = pl.when(exempt).then(0.0).otherwise(chcr)
        earncr = pl.when(exempt).then(0.0).otherwise(earncr)
        credits = pl.when(exempt).then(0.0).otherwise(credits)
        rate = pl.when(exempt).then(business_rate).otherwise(rate)

    df = df.with_columns(siitax=statax * flate)
    return with_state_detail(
        df,
        agi=agi,
        exemptions=exemp,
        taxable_income=taxinc,
        child_care_credit=chcr,
        eic=earncr,
        credits=credits,
        rate=rate,
    )
