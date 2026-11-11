"""West Virginia personal income tax calculator."""

import polars as pl

from taxsim_py.engine.brackets import bracket_rate, bracket_tax
from taxsim_py.engine.inputs import aged_count, federal_exemption_count, files_head_of_household, files_joint, files_separate, is_dependent_filer, taxpayer_count
from taxsim_py.engine.schema import PARAMETERS_ROOT, YearParams, load_yaml
from taxsim_py.engine.state import allocate_pensions, checkpoint, pre1987_federal_itemizing, split_agi, tier_values, with_state_detail
from taxsim_py.behavior import BehaviorProfile, TAXSIM_BEHAVIOR
from taxsim_py.engine.state_extrapolation import deflate_for_extrapolation, resolve_state_year

WV_PARAMS = load_yaml(PARAMETERS_ROOT / "states" / "wv" / "income_tax.yaml")


def compute_wv_tax(df: pl.DataFrame, year: int, behavior: BehaviorProfile = TAXSIM_BEHAVIOR) -> pl.DataFrame:
    """Calculate West Virginia income tax for each row."""
    effective_year, flate = resolve_state_year(year, behavior, WV_PARAMS)
    y = effective_year
    p = YearParams(WV_PARAMS, effective_year)
    # Federal total income (`comnew(65)`) is not deflated in projected years.
    df = df.with_columns(wv_total_income=pl.col("agi") + 0.5 * pl.col("payroll_setax"))
    df = deflate_for_extrapolation(df, flate)

    is_joint = files_joint()
    is_hoh = files_head_of_household()
    sep = pl.when(files_separate()).then(2.0).otherwise(1.0)
    txp = taxpayer_count()
    aged = aged_count()
    dependent_filer = is_dependent_filer()
    depx = pl.col("depx")
    fed_agi = pl.col("agi")
    ss = pl.col("taxable_social_security")
    # The federal exemption count is deflated in projected years.
    exemps = federal_exemption_count(y) / flate

    agi = fed_agi
    if 1982 <= y <= 1986:
        agi = agi + pl.col("pre1987_twoded")
    if 1984 <= y <= 1986:
        agi = agi - ss
    taxsim_senior_subtraction = (
        p.in_effect("social_security_subtraction_share") and not behavior.subtract_west_virginia_social_security_per_statute
    )
    if taxsim_senior_subtraction and y >= 2022:
        # TAXSIM's 2022 routine: the benefit comes out at the year's share (nobody aged), or, with someone
        # aged, each spouse's share of it plus the senior cap on their own income, less that share.
        cap = float(p["senior_deduction"])
        limit = p["social_security_subtraction_agi_per_taxpayer"] * txp
        over = fed_agi > limit
        share = p.num("social_security_subtraction_share")
        above_share = p.num("social_security_above_limit_share") if p.in_effect("social_security_above_limit_share") else 0.0
        fraction = pl.when(over).then(above_share).otherwise(share)
        any_aged = (pl.col("page") >= 65) | (pl.col("sage") >= 65) | (aged > 0)
        benefit_primary, benefit_spouse = allocate_pensions(ss, 0)
        income_primary, income_spouse = split_agi(fed_agi, 0.0, 0.0)
        counted = pl.when(over).then(above_share).otherwise(1.0)
        exempt_primary, exempt_spouse = counted * benefit_primary, counted * benefit_spouse
        senior_primary = pl.when(pl.col("page") >= 65).then((income_primary.clip(0, cap) - exempt_primary).clip(0, None)).otherwise(0.0)
        senior_spouse = pl.when(pl.col("sage") >= 65).then((income_spouse.clip(0, cap) - exempt_spouse).clip(0, None)).otherwise(0.0)
        subtraction = pl.when(any_aged & (ss > 1)).then(exempt_primary + exempt_spouse + senior_primary + senior_spouse).when(
            any_aged
        ).then(senior_primary + senior_spouse).otherwise(fraction * ss)
        agi = (agi - subtraction).clip(0, None)
    elif taxsim_senior_subtraction:
        # TAXSIM: only when someone is 65 or older, the whole taxable benefit comes out below the
        # limit and each spouse's own income up to the senior cap, less the benefit, comes out too.
        cap = float(p["senior_deduction"])
        any_aged = (pl.col("page") >= 65) | (pl.col("sage") >= 65) | (aged > 0)
        low = fed_agi <= p["social_security_subtraction_agi_per_taxpayer"] * txp
        benefit_primary, benefit_spouse = allocate_pensions(ss, 65)
        income_primary, income_spouse = split_agi(fed_agi, 0.0, 0.0)
        deducted_primary = pl.when(low).then(pl.min_horizontal(income_primary, benefit_primary)).otherwise(0.0)
        deducted_spouse = pl.when(low).then(pl.min_horizontal(income_spouse, benefit_spouse)).otherwise(0.0)
        senior_primary = (income_primary.clip(0, cap) - deducted_primary).clip(0, None)
        senior_spouse = (income_spouse.clip(0, cap) - deducted_spouse).clip(0, None)
        exempt_benefit = pl.when(low).then(ss).otherwise(0.0)
        agi = (agi - pl.when(any_aged).then(exempt_benefit + senior_primary + senior_spouse).otherwise(0.0)).clip(0, None)
    elif p.in_effect("social_security_subtraction_share"):
        above_limit = p.num("social_security_above_limit_share") if p.in_effect("social_security_above_limit_share") else 0.0
        agi = agi - pl.when(fed_agi <= p["social_security_subtraction_agi_per_taxpayer"] * txp).then(
            p.num("social_security_subtraction_share") * ss
        ).otherwise(above_limit * ss)
    # Senior citizen deduction.
    cap = float(p["senior_deduction"])
    total_income = pl.col("wv_total_income")
    ti = total_income + ss
    pw = pl.col("pwages").clip(0, None)
    sw = pl.col("swages").clip(0, None)
    one_joint = pl.min_horizontal(0.5 * (ti - pw - ss) + ss + pl.col("pensions") + sw, pl.lit(cap))
    one = pl.when(is_joint).then(one_joint).otherwise(pl.min_horizontal(total_income, pl.lit(cap)))
    half_other = 0.5 * (ti - pw - sw)
    two = pl.min_horizontal(half_other + pw, pl.lit(cap)) + pl.min_horizontal(half_other + sw, pl.lit(cap))
    oldded = pl.when(aged == 1).then(one).when(aged == 2).then(two).otherwise(0.0)
    if behavior.allow_west_virginia_disability_deduction:
        # Under 65 with Social Security benefits in AGI: the disability
        # deduction, which TAXSIM's year test never reaches.
        agimod = agi - pw - sw - ss
        disabled_joint = pl.min_horizontal(0.5 * agimod + pw + 0.5 * ss, pl.lit(cap)) + pl.min_horizontal(
            0.5 * agimod + sw + 0.5 * ss, pl.lit(cap)
        )
        disabled = pl.when(is_joint).then(disabled_joint).otherwise(pl.min_horizontal(agi, pl.lit(cap)))
        oldded = pl.when((aged == 0) & (ss > 0)).then(disabled.clip(0, None)).otherwise(oldded)
    if not taxsim_senior_subtraction:
        agi = (agi - oldded).clip(0, None)
    df, (agi,) = checkpoint(df, wv_agi=agi)

    # --- Deductions (none from 1987) ---
    if p.in_effect("standard_deduction_share"):
        c = p.value("standard_deduction_share")
        stded = (c["rate"] * agi).clip(None, c["cap"])
        # Federal itemized deductions less state income tax (TAXSIM reads
        # the tax from an input it never sets, so it stays in).
        gross, fed_itemizes, _ = pre1987_federal_itemizing(y)
        if behavior.remove_state_tax_from_west_virginia_itemized_deductions:
            gross = (gross - pl.col("state_sales_or_income_tax_ded")).clip(0, None)
        xitded = pl.when(fed_itemizes).then(gross).otherwise(0.0)
        deduc = pl.max_horizontal(stded, xitded)
    else:
        stded = xitded = deduc = pl.lit(0.0)
    exemp = exemps * p.num("exemption")
    if p.in_effect("dependent_filer_exemption"):
        exemp = pl.when(dependent_filer).then(p.num("dependent_filer_exemption")).otherwise(exemp)
    taxinc = (agi - deduc - exemp).clip(0, None)
    if p.in_effect("low_income_exclusion"):
        limit = p.num("low_income_exclusion") / sep
        taxinc = pl.when(fed_agi <= limit).then(
            (taxinc - pl.min_horizontal(limit, pl.col("earned_income"))).clip(0, None)
        ).otherwise(taxinc)
    df, (taxinc,) = checkpoint(df, wv_taxinc=taxinc)

    # --- Tax ---
    tables = p.value("brackets")
    if y <= 1986:
        general = tables["general"] if isinstance(tables, dict) else tables
        split = pl.when(is_joint).then(2.0 * bracket_tax(taxinc / 2.0, general)).otherwise(bracket_tax(taxinc, general))
        rate = pl.when(is_joint).then(bracket_rate(taxinc / 2.0, general)).otherwise(bracket_rate(taxinc, general))
        if isinstance(tables, dict):
            hoh_table = tables["head_of_household"]
            if y >= 1984 and not behavior.complete_west_virginia_1984_head_of_household_table:
                # TAXSIM reads one row too few of the 1984-1986 table.
                hoh_table = hoh_table[:-1]
            statax = pl.when(is_hoh).then(bracket_tax(taxinc, hoh_table)).otherwise(split)
            rate = pl.when(is_hoh).then(bracket_rate(taxinc, hoh_table)).otherwise(rate)
        else:
            statax = split
    else:
        table = tables
        bracket_sep = sep
        if y >= 2022 and not behavior.halve_west_virginia_separate_brackets_per_statute:
            # TAXSIM's 2022 on routine halves income and doubles the tax for every filer but "status 3"
            # (never used), so a separate return gets the joint brackets undivided.
            bracket_sep = pl.lit(1.0)
        statax = bracket_tax(taxinc * bracket_sep, table) / bracket_sep
        rate = bracket_rate(taxinc * bracket_sep, table)
    if p.in_effect("surtax"):
        statax = pl.when(taxinc > p["surtax_income_per_taxpayer"] * txp).then(p.num("surtax") * statax).otherwise(statax)
    df, (statax,) = checkpoint(df, wv_tax_before_credits=statax)

    credits = pl.lit(0.0)
    pcred = pl.lit(0.0)

    # --- Family tax credit ---
    if p.in_effect("family_credit_limit"):
        limits = p.value("family_credit_limit")
        if p.in_effect("taxsim_family_credit_limit") and not behavior.read_west_virginia_family_credit_limits_per_statute:
            limits = p.value("taxsim_family_credit_limit")
        nexemp = exemps.floor()
        (fcp,) = tier_values(nexemp, [float(n) for n in range(1, 8)] + [1.0e20], [float(v) for v in limits])
        fcp = fcp / sep
        full = p.num("family_credit_full_share")
        points = p.num("family_credit_phaseout_points")
        share = pl.when(fed_agi <= fcp).then(full).otherwise(
            (0.01 * (100.0 * full - points * (fed_agi - fcp) / (p["family_credit_phaseout_step"] / sep))).clip(0, None)
        )
        if p.in_effect("family_credit_stepped_phaseout") and behavior.step_west_virginia_family_credit_phaseout_per_statute:
            stepped = p.value("family_credit_stepped_phaseout")
            steps = ((fed_agi - fcp).clip(0, None) / (p["family_credit_phaseout_step"] / sep)).floor()
            share = pl.when(fed_agi < fcp).then(1.0).otherwise(
                (stepped["first_share"] - stepped["share_per_step"] * steps).clip(0, None)
            )
        famcrd = pl.when(dependent_filer).then(0.0).otherwise(share * statax)
        famcrd = pl.when((statax > 0) & (nexemp > 0)).then(famcrd).otherwise(0.0)
        statax = (statax - famcrd).clip(0, None)
        credits = credits + famcrd

    # --- Homestead excess property tax credit (refundable) ---
    if p.in_effect("property_credit_base"):
        per_person = p.num("property_credit_per_person")
        pagi = p.num("property_credit_base") + depx * per_person + pl.when(is_joint).then(per_person).otherwise(0.0)
        income = (fed_agi + pl.col("gssi") - ss).clip(0, None)
        pcred = (pl.col("proptax") - p["property_credit_rate"] * income).clip(0, p["property_credit_cap"])
        pcred = pl.when(fed_agi < pagi).then(pcred).otherwise(0.0)
        statax = statax - pcred
        credits = credits + pcred

    df = df.with_columns(siitax=statax * flate)
    return with_state_detail(
        df,
        agi=agi,
        exemptions=exemp,
        standard_deduction=stded,
        itemized_deductions=xitded,
        taxable_income=taxinc,
        property_credit=pcred,
        credits=credits,
        rate=rate,
    )
