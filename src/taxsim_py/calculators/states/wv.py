"""West Virginia personal income tax calculator."""

import polars as pl

from taxsim_py.engine.brackets import bracket_rate, bracket_tax
from taxsim_py.calculators.payroll import payroll_parts
from taxsim_py.engine.inputs import aged_count, federal_exemption_count, files_head_of_household, files_joint, files_separate, is_dependent_filer, taxpayer_count
from taxsim_py.engine.schema import PARAMETERS_ROOT, YearParams, load_yaml
from taxsim_py.engine.state import checkpoint, pre1987_federal_itemizing, tier_values, with_state_detail
from taxsim_py.behavior import BehaviorProfile, TAXSIM_BEHAVIOR
from taxsim_py.engine.state_extrapolation import deflate_for_extrapolation, resolve_state_year

WV_PARAMS = load_yaml(PARAMETERS_ROOT / "states" / "wv" / "income_tax.yaml")


def compute_wv_tax(df: pl.DataFrame, year: int, behavior: BehaviorProfile = TAXSIM_BEHAVIOR) -> pl.DataFrame:
    """Calculate West Virginia income tax for each row."""
    state_year = "wv" if behavior.mode.value == "statutory" else None
    effective_year, flate = resolve_state_year(year, state_year)
    y = effective_year
    p = YearParams(WV_PARAMS, effective_year)
    # Federal total income (`comnew(65)`) is not deflated in projected years.
    df = df.with_columns(wv_total_income=pl.col("agi") + 0.5 * payroll_parts(year)["setax"])
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
    if y >= 2020:
        above_limit = p.num("social_security_above_limit_share") if y >= 2022 and behavior.mode.value == "statutory" else 0.0
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
    agi = (agi - oldded).clip(0, None)
    df, (agi,) = checkpoint(df, wv_agi=agi)

    # --- Deductions (none from 1987) ---
    if y <= 1986:
        c = p["standard_deduction_1986"]
        stded = (c["rate"] * agi).clip(None, c["cap"])
        # The state tax subtraction reads an input that is never set, so
        # federal itemized deductions keep state income tax (logged).
        gross, fed_itemizes, _ = pre1987_federal_itemizing(y)
        xitded = pl.when(fed_itemizes).then(gross).otherwise(0.0)
        deduc = pl.max_horizontal(stded, xitded)
    else:
        stded = xitded = deduc = pl.lit(0.0)
    exemp = exemps * p.num("exemption")
    if y >= 1987:
        exemp = pl.when(dependent_filer).then(float(p["dependent_filer_exemption"])).otherwise(exemp)
    taxinc = (agi - deduc - exemp).clip(0, None)
    if y >= 1997:
        limit = p["low_income_exclusion"] / sep
        taxinc = pl.when(fed_agi <= limit).then(
            (taxinc - pl.min_horizontal(limit, pl.col("earned_income"))).clip(0, None)
        ).otherwise(taxinc)
    df, (taxinc,) = checkpoint(df, wv_taxinc=taxinc)

    # --- Tax ---
    brackets = p["brackets"]
    if y <= 1986:
        key = 1977 if y <= 1982 else (1983 if y == 1983 else 1984)
        general = brackets[key]
        split = pl.when(is_joint).then(2.0 * bracket_tax(taxinc / 2.0, general)).otherwise(bracket_tax(taxinc, general))
        rate = pl.when(is_joint).then(bracket_rate(taxinc / 2.0, general)).otherwise(bracket_rate(taxinc, general))
        if y >= 1983:
            statax = pl.when(is_hoh).then(bracket_tax(taxinc, brackets[f"{key}_head_of_household"])).otherwise(split)
            rate = pl.when(is_hoh).then(bracket_rate(taxinc, brackets[f"{key}_head_of_household"])).otherwise(rate)
        else:
            statax = split
    else:
        table = brackets[2025 if y >= 2025 else 2023] if y >= 2023 else brackets[1987]
        statax = bracket_tax(taxinc * sep, table) / sep
        rate = bracket_rate(taxinc * sep, table)
    if 1983 <= y <= 1985:
        statax = pl.when(taxinc > p["surtax_income_per_taxpayer"] * txp).then(p.num("surtax") * statax).otherwise(statax)
    df, (statax,) = checkpoint(df, wv_tax_before_credits=statax)

    credits = pl.lit(0.0)
    pcred = pl.lit(0.0)

    # --- Family tax credit ---
    if y >= 2007:
        limits = p.value("family_credit_limit")
        nexemp = exemps.floor()
        (fcp,) = tier_values(nexemp, [float(n) for n in range(1, 8)] + [1.0e20], [float(v) for v in limits])
        fcp = fcp / sep
        full = p.num("family_credit_full_share")
        points = p.num("family_credit_phaseout_points")
        share = pl.when(agi <= fcp).then(full).otherwise(
            (0.01 * (100.0 * full - points * (agi - fcp) / (p["family_credit_phaseout_step"] / sep))).clip(0, None)
        )
        if y >= 2022:
            # The statutory fraction falls in steps: 90% for the first $300 over the poverty line,
            # then 10 points less for each further $300.
            # Tested against federal AGI (state subtractions do not lower it).
            steps = ((fed_agi - fcp).clip(0, None) / (p["family_credit_phaseout_step"] / sep)).floor()
            share = pl.when(fed_agi < fcp).then(1.0).otherwise((0.9 - 0.1 * steps).clip(0, None))
        famcrd = pl.when(dependent_filer).then(0.0).otherwise(share * statax)
        famcrd = pl.when((statax > 0) & (nexemp > 0)).then(famcrd).otherwise(0.0)
        statax = (statax - famcrd).clip(0, None)
        credits = credits + famcrd

    # --- Homestead excess property tax credit (refundable) ---
    if y >= 2008:
        per_person = p.num("property_credit_per_person")
        pagi = p.num("property_credit_base") + depx * per_person + pl.when(is_joint).then(per_person).otherwise(0.0)
        income = fed_agi + pl.col("gssi") - ss
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
