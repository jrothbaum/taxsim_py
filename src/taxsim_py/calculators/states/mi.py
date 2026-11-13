"""Michigan individual income tax calculator."""


from __future__ import annotations
import polars as pl

from taxsim_py.engine.inputs import aged_count, files_joint, files_separate, is_dependent_filer, taxpayer_count
from taxsim_py.engine.schema import PARAMETERS_ROOT, YearParams, load_yaml
from taxsim_py.engine.state import (
    household_income,
    interpolate_table,
    unemployment_total,
    with_state_detail,
)
from taxsim_py.behavior import BehaviorProfile, TAXSIM_BEHAVIOR
from taxsim_py.engine.state_extrapolation import deflate_for_extrapolation, resolve_state_year

MI_PARAMS = load_yaml(PARAMETERS_ROOT / "states" / "mi" / "income_tax.yaml")


def compute_mi_tax(df: pl.DataFrame, year: int, behavior: BehaviorProfile = TAXSIM_BEHAVIOR) -> pl.DataFrame:
    """Calculate Michigan income tax for each row."""
    effective_year, flate = resolve_state_year(year, behavior, MI_PARAMS)
    y = effective_year
    p = YearParams(MI_PARAMS, effective_year)

    is_joint = files_joint()
    is_sep = files_separate()
    n_tp = taxpayer_count()
    aged = aged_count()

    # Self-employment tax is not deflated in projected years.
    setax = pl.col("payroll_setax")
    ui_total = unemployment_total()
    hh_income = household_income(behavior)
    df = df.with_columns(mi_setax=setax, mi_household_income_undeflated=hh_income, mi_household_income=hh_income)

    df = deflate_for_extrapolation(df, flate, extra=("mi_household_income",))

    # --- AGI ---
    agi = pl.col("agi")
    if 1982 <= y <= 1986:
        agi = agi + pl.col("pre1987_twoded")
    if y >= 1984:
        agi = agi - pl.col("taxable_social_security")
    if p.in_effect("self_employment_tax_agi_addback_holiday"):
        holiday = p.value("self_employment_tax_agi_addback_holiday")
        agi = agi + pl.when(pl.col("mi_setax") <= holiday["threshold"]).then(
            holiday["lower_rate"] * pl.col("mi_setax")
        ).otherwise(holiday["upper_rate"] * pl.col("mi_setax") + float(holiday["amount"]))
    elif p.in_effect("self_employment_tax_agi_addback_rate") and (y < 2021 or behavior.add_back_michigan_self_employment_tax_from_2021_per_statute):
        agi = agi + p.num("self_employment_tax_agi_addback_rate") * pl.col("mi_setax")

    if y >= 2017 and not behavior.leave_out_michigan_schedule_r_income_subtraction_per_statute:
        # TAXSIM subtracts the income behind the federal credit for the elderly (the credit over its 15% rate).
        agi = agi - pl.col("federal_elder") / 0.15

    # --- Retirement subtraction for taxpayers 65 or older ---
    pensions = pl.col("pensions")
    if p.in_effect("pension_exclusion_by_return"):
        caps = p.value("pension_exclusion_by_return")
        penexc = pl.when(is_joint | is_sep).then(
            pl.min_horizontal(pensions / pl.when(is_sep).then(2.0).otherwise(1.0), float(caps["joint"]))
        ).otherwise(pl.min_horizontal(pensions, float(caps["single"])))
    else:
        penexc = pl.min_horizontal(pensions, p.num("pension_exclusion_per_taxpayer") * n_tp)
        if p.in_effect("pension_exclusion_joint_cap"):
            penexc = pl.when(is_joint).then(pl.min_horizontal(pensions, p.num("pension_exclusion_joint_cap"))).otherwise(penexc)
    penexc = pl.when(aged >= 1).then(penexc).otherwise(0.0)
    # From 1994, interest and dividends (and from 1997 capital gains) instead.
    divded = pl.lit(0.0)
    if p.in_effect("senior_investment_deduction_per_taxpayer"):
        unearned = pl.col("dividends") + pl.col("intrec")
        if y >= 1997:
            unearned = unearned + (pl.col("stcg") + pl.col("ltcg")).clip(0, None)
        # TAXSIM tests only that a taxpayer was born in 1946 or earlier, so a younger taxpayer gets it too.
        senior = aged > 0
        if not behavior.require_age_65_for_michigan_investment_deduction_per_statute:
            senior = (1946 + pl.max_horizontal(pl.col("page"), pl.col("sage"))) >= year
        divded = pl.when(senior).then(
            pl.min_horizontal(unearned, p.num("senior_investment_deduction_per_taxpayer") * n_tp)
        ).otherwise(0.0)
    if behavior.subtract_michigan_social_security_with_investment_deduction_per_statute:
        pens = pl.max_horizontal(penexc, divded)
    else:
        # TAXSIM takes the larger of the investment deduction and the pension plus Social Security subtractions, so
        # the benefits are taxed when the investment deduction is larger.
        pens = pl.max_horizontal(penexc, divded - pl.col("taxable_social_security"))
    tiered = p.in_effect("senior_standard_deduction") and y <= 2022 and behavior.apply_michigan_retirement_tiers
    taxsim_tiers = False
    taxsim_tiers_2021 = False
    if tiered:
        older = pl.max_horizontal(pl.col("page"), pl.col("sage"))
        born = year - older
        tier_two = (born >= p.num("senior_tier_two_first_birth_year")) & (born <= p.num("senior_tier_two_last_birth_year"))
        tier_three = born > p.num("senior_tier_two_last_birth_year")
        reached = older >= p.num("senior_standard_deduction_age")
        tier_amount = p.num("senior_standard_deduction") * pl.when(is_joint).then(2.0).otherwise(1.0)
        tier_two_deduction = pl.when(reached).then(tier_amount).otherwise(pl.min_horizontal(pensions, tier_amount))
        pens = pl.when(tier_two).then(tier_two_deduction).when(tier_three).then(0.0).otherwise(pens)
    elif p.in_effect("senior_standard_deduction") and not behavior.apply_michigan_retirement_tiers:
        # TAXSIM has no senior deduction before 2020; its 2020 and 2021-on routines differ.
        taxsim_tiers = y == 2020
        taxsim_tiers_2021 = y >= 2021
    elif p.in_effect("senior_standard_deduction"):
        # A flat deduction per taxpayer 65 or older replaces it.
        pens = pl.when(aged > 0).then(p.num("senior_standard_deduction") * aged).otherwise(pens)

    # --- Exemptions ---
    num = n_tp + pl.col("depx").floor() + aged
    xmp1 = p.num("personal_exemption")
    if y < 1987:
        exemp = num * xmp1
    else:
        ump = pl.when(
            ui_total >= p.num("special_exemption_ui_share") * pl.col("agi")
        ).then(1.0).otherwise(0.0)
        exemp = (num - aged) * xmp1 + (aged + ump) * p.num("special_exemption")
        exemp = exemp + p.num("dependent_exemption") * pl.col("depx")
    dependent_exemption = p.num("dependent_filer_exemption")
    if effective_year >= 2021 and not behavior.give_michigan_dependent_filers_exemption_per_statute:
        # TAXSIM's 2022 on routine counts no taxpayers for a dependent filer, so no exemption at all.
        dependent_exemption = 0.0
    exemp = pl.when(is_dependent_filer()).then(dependent_exemption).otherwise(exemp)

    if taxsim_tiers_2021:
        # TAXSIM's 2021 on routine: by birth year, the first tier keeps the pension subtraction (and a capped
        # investment one), the second takes 20,000 per taxpayer limited to income, the third that less the
        # exemptions (not below the benefit), then 75% of the pension cap for those born 1959-1966.
        older = pl.max_horizontal(pl.col("page"), pl.col("sage"))
        older = pl.when((older == 0) & (aged > 0)).then(74).otherwise(older)
        born = year - older
        pend = p.num("pension_exclusion_per_taxpayer")
        ssben = pl.col("taxable_social_security")
        tier2 = pl.min_horizontal(pl.col("agi"), p.num("senior_standard_deduction") * n_tp)
        tier3 = pl.max_horizontal(tier2 - exemp, ssben)
        known = older >= 59
        first, last = p.num("senior_tier_two_first_birth_year"), p.num("senior_tier_two_last_birth_year")
        stded_t = (
            pl.when(known & (born >= first) & (born <= last)).then(tier2)
            .when(known & (born > last) & (born <= pl.min_horizontal(pl.lit(1958), year - 66))).then(tier3)
            .otherwise(0.0)
        )
        penex_t = (
            pl.when(known & (born < first)).then(pl.min_horizontal(pl.lit(pend), pensions))
            .when(known & (born >= 1959) & (born <= 1966)).then(pl.min_horizontal(pl.lit(0.75 * pend), pensions))
            .otherwise(0.0)
        )
        subtr = pl.max_horizontal(stded_t, penex_t)
        capinc = pl.col("dividends") + pl.col("intrec") + (pl.col("stcg") + pl.col("ltcg")).clip(0, None)
        old_deduction = p.num("senior_investment_deduction_per_taxpayer")
        subtr = pl.when((born < first) & (old_deduction > subtr)).then(pl.min_horizontal(capinc, pl.lit(old_deduction))).otherwise(subtr)
        pens = subtr
    if taxsim_tiers:
        older = pl.max_horizontal(pl.col("page"), pl.col("sage"))
        older = pl.when((older == 0) & (aged > 0)).then(74).otherwise(older)
        born = year - older
        amount = p.num("senior_standard_deduction") * pl.when(is_joint).then(2.0).otherwise(1.0)
        reached = older >= p.num("senior_standard_deduction_age")
        first, last = p.num("senior_tier_two_first_birth_year"), p.num("senior_tier_two_last_birth_year")
        ssben = pl.col("taxable_social_security")
        tier_two = reached & (born >= first) & (born <= last)
        tier_three = reached & (born > last) & (born <= p.num("taxsim_last_tier_three_birth_year"))
        tier_deduction = (
            pl.when(tier_two).then(((amount - exemp).clip(0, None) - ssben).clip(0, None))
            .when(tier_three).then((amount - ssben - exemp).clip(0, None))
            .otherwise(0.0)
        )
        pens = pl.max_horizontal(pens, tier_deduction, divded - ssben)
    if tiered:
        # Born after the second tier: the deduction less exemptions from the age.
        pens = pl.when(tier_three & reached).then((tier_amount - exemp).clip(0, None)).otherwise(pens)
    if y >= 2023 and not taxsim_tiers_2021:
        # MI-1040 Worksheet 2: the senior standard deduction less exemptions.
        pens = pl.when(aged > 0).then((pens - exemp).clip(0, None)).otherwise(pens)
        cap = pl.when(is_joint).then(p.num("expanded_retirement_maximum", "joint")).otherwise(p.num("expanded_retirement_maximum", "single"))
        expanded = pl.min_horizontal(pl.col("pensions"), p.num("expanded_retirement_share") * cap)
        pens = pl.when(aged > 0).then(pl.max_horizontal(pens, expanded)).otherwise(pens)
    taxinc = (agi - pens - exemp).clip(0, None)
    regtax = p.num("rate") * taxinc
    regtax = pl.when(is_dependent_filer() & (agi < dependent_exemption)).then(0.0).otherwise(regtax)

    # --- Homestead property tax credit (non-elderly branch) ---
    hhy = (
        pl.col("mi_household_income")
        - (0.0 if y >= 2021 and not behavior.add_back_michigan_self_employment_tax_from_2021_per_statute else p.num("property_credit_self_employment_tax_deduction_rate"))
        * pl.col("mi_setax")
    ).clip(0, None)
    allow = p.num("property_credit_phaseout_start")
    share = p.num("property_credit_income_share")
    rentpaid = pl.col("rentpaid")
    ptax = pl.col("proptax") + p.num("property_credit_rent_share") * rentpaid
    pcred_under65 = p.num("property_credit_rate") * (ptax - share * hhy).clip(0, None)
    # Taxpayers 65 or older: property tax over a sliding share of income,
    # from 2012 scaled by an income percentage; renters may instead claim
    # rent over 40% of income.
    if y < 1980:
        pcred_aged = ptax
    else:
        senior_table = p.value("property_credit_senior_income_share")
        pcred_aged = (ptax - interpolate_table(hhy, senior_table) * hhy).clip(0, None)
        if y >= 2012:
            pcred_aged = pcred_aged * interpolate_table(hhy, MI_PARAMS["property_credit_senior_percentage"])
        pcred_aged = pl.when(rentpaid > 0).then(
            pl.max_horizontal(pcred_aged, rentpaid - p.num("property_credit_senior_rent_income_share") * hhy)
        ).otherwise(pcred_aged)
    if y >= 2021 and not behavior.cap_michigan_senior_renter_credit_at_statute_amount:
        # TAXSIM caps the alternative renter credit for taxpayers 65 or older at a fixed $1,500 (applied after the main cap).
        pcred_aged_main = (ptax - interpolate_table(hhy, p.value("property_credit_senior_income_share")) * hhy).clip(0, None)
        pcred_aged_main = pcred_aged_main * interpolate_table(hhy, MI_PARAMS["property_credit_senior_percentage"]) if y >= 2012 else pcred_aged_main
        pcred_aged = pcred_aged_main.clip(None, p.num("property_credit_cap"))
        older_65 = pl.max_horizontal(pl.col("page"), pl.col("sage")) >= 65
        rent_alternative = pl.min_horizontal(
            pl.lit(1500.0),
            pl.max_horizontal(pcred_aged, pl.col("rentpaid") - p.num("property_credit_senior_rent_income_share") * hhy),
        )
        pcred_aged = pl.when(older_65 & (pl.col("rentpaid") > 0)).then(pl.max_horizontal(pcred_aged, rent_alternative)).otherwise(pcred_aged)
        pcred = pl.when(aged > 0).then(pcred_aged).otherwise(pcred_under65.clip(None, p.num("property_credit_cap")))
    else:
        pcred = pl.when(aged > 0).then(pcred_aged).otherwise(pcred_under65).clip(None, p.num("property_credit_cap"))
    pcred = pl.when(hhy > allow).then(
        pcred
        * (
            1.0
            - p.num("property_credit_phaseout_rate")
            * (hhy - allow)
            / p.num("property_credit_phaseout_step")
        ).clip(0, None)
    ).otherwise(pcred)
    pcred = pl.when(
        hhy <= allow + p.num("property_credit_phaseout_range")
    ).then(pcred).otherwise(0.0)

    # --- Home heating credit ---
    amex = pl.when(is_dependent_filer()).then(0.0).otherwise(n_tp + pl.col("depx"))
    nexemp = amex.clip(None, 6.0).floor()
    # TAXSIM reads the household income from its deflated copy (`data(159)`) in a projected year.
    hy = pl.col("mi_household_income_undeflated") / flate
    if p.in_effect("heating_credit_inflation"):
        base = p.value("heating_credit_base_by_exemptions")
        fc = pl.lit(0.0)
        for k in range(1, 7):
            fc = pl.when(nexemp == k).then(float(base[k - 1])).otherwise(fc)
        fc = fc * p.num("heating_credit_inflation")
    elif p.in_effect("heating_credit_base_by_exemptions"):
        base = p.value("heating_credit_base_by_exemptions")
        fc = pl.lit(0.0)
        for k in range(1, 7):
            fc = pl.when(nexemp == k).then(float(base[k - 1])).otherwise(fc)
        fc = fc + (amex - 6.0).clip(0, None) * p.num("heating_credit_per_extra_exemption")
    else:
        fc = p.num("heating_credit_base") + (amex - 1.0).clip(0, None) * p.num("heating_credit_per_extra_exemption")
    fuel = (
        pl.when(nexemp > 0)
        .then((fc - p.num("heating_credit_income_rate") * hy).clip(0, None))
        .otherwise(0.0 if behavior.give_michigan_dependent_filers_exemption_per_statute or effective_year < 2021 else pl.when(is_dependent_filer()).then((fc - p.num("heating_credit_income_rate") * hy).clip(0, None)).otherwise(0.0))
        * p.num("heating_credit_share")
    )

    earncr = p.num("eitc_rate") * pl.col("eitc") if p.in_effect("eitc_rate") else pl.lit(0.0)

    statax = regtax.clip(0, None) - pcred - fuel - earncr
    dependent_below_exemption = is_dependent_filer() & (agi < dependent_exemption)
    detail_exemp = pl.when(dependent_below_exemption).then(0.0).otherwise(exemp)
    detail_taxinc = pl.when(dependent_below_exemption).then(0.0).otherwise(taxinc)
    detail_stded = p.num("senior_standard_deduction") * aged if p.in_effect("senior_standard_deduction") else pl.lit(0.0)
    return with_state_detail(
        df.with_columns(siitax=statax * flate),
        agi=agi,
        exemptions=detail_exemp,
        standard_deduction=detail_stded,
        taxable_income=detail_taxinc,
        property_credit=pcred,
        eic=earncr,
        credits=fuel + pcred + earncr,
        rate=p.num("rate"),
    )
