"""Missouri individual income tax calculator."""

import polars as pl

from taxsim_py.calculators.federal_pre1987 import PRE1987_PARAMS
from taxsim_py.engine.brackets import bracket_rate, bracket_tax, scale_brackets
from taxsim_py.engine.inputs import aged_count, files_head_of_household, files_joint, files_separate, is_dependent_filer, taxpayer_count
from taxsim_py.engine.schema import PARAMETERS_ROOT, YearParams, load_yaml, resolve_year
from taxsim_py.engine.state import (
    by_filing_status,
    checkpoint,
    forced_standard,
    household_income,
    itemize_choice,
    taxsim_socsec,
    with_state_detail,
)
from taxsim_py.behavior import BehaviorProfile, TAXSIM_BEHAVIOR
from taxsim_py.engine.state_extrapolation import deflate_for_extrapolation, resolve_state_year

MO_PARAMS = load_yaml(PARAMETERS_ROOT / "states" / "mo" / "income_tax.yaml")
_STATUSES = ["single", "married_joint", "married_separate", "head_of_household"]


def compute_mo_tax(df: pl.DataFrame, year: int, behavior: BehaviorProfile = TAXSIM_BEHAVIOR) -> pl.DataFrame:
    """Calculate Missouri income tax for each row."""
    state_year = "mo" if behavior.mode.value == "statutory" else None
    effective_year, flate = resolve_state_year(year, state_year)
    y = effective_year
    p = YearParams(MO_PARAMS, effective_year)

    setax = pl.col("setax")  # `comnew(175)`, real-year and undeflated
    df = df.with_columns(
        mo_setax=setax,
        mo_household_income=household_income(),
    )
    df = deflate_for_extrapolation(df, flate, extra=("mo_household_income",))

    is_joint = files_joint()
    is_sep = files_separate()
    is_hoh = files_head_of_household()
    sep = pl.when(is_sep).then(2.0).otherwise(1.0)
    txp = taxpayer_count()
    aged = aged_count()
    dependent_filer = is_dependent_filer()
    depx = pl.col("depx")
    salt_ded = pl.col("state_sales_or_income_tax_ded")
    setax = pl.col("mo_setax")
    agi = pl.col("agi")
    wages_total = pl.col("pwages").clip(0, None) + pl.col("swages").clip(0, None)
    split_share = p.num("joint_split_other_income_share")
    agih = pl.max_horizontal(pl.col("pwages"), pl.col("swages")) + split_share * (agi - wages_total)
    agiw = agi - agih

    # --- Federal return values as Missouri reads them ---
    if y <= 1986:
        deducp = pl.col("proptax") + pl.col("otheritem") + pl.col("mortgage") + salt_ded
        fed_zbr = by_filing_status(
            {s: resolve_year(PRE1987_PARAMS["standard_deduction"][s], y) for s in _STATUSES}
        )
        fed_itemizes = deducp > fed_zbr if y <= 1981 else itemize_choice(deducp > fed_zbr)
        dedphs = pl.lit(0.0)
    else:
        fed_itemizes = pl.col("itemizes")
        fed_zbr = pl.when(fed_itemizes).then(0.0).otherwise(pl.col("standard_deduction"))
        agix = agi.clip(0, None)
        alim50 = pl.lit(1.0e20) if y in (2020, 2021) else 0.5 * agix
        fed_char = pl.when(agi < 0).then(0.0).otherwise(pl.min_horizontal(pl.col("charity_cash"), alim50)).clip(0, None)
        if y <= 2017:
            deducp = (pl.col("proptax") + pl.col("otheritem") + salt_ded).clip(0, None) + pl.col("mortgage") + fed_char
        else:
            deducp = pl.col("itemized_deduction")
        dedphs = deducp - pl.col("itemized_deduction") if 1991 <= y <= 2017 else pl.lit(0.0)

    # --- Exemptions ---
    exemp = (
        pl.when(is_hoh)
        .then(p.num("exemption_head_of_household"))
        .otherwise(p.num("exemption_per_taxpayer") * txp)
        + depx * p.num("exemption_per_dependent")
    )
    if y == 2017:
        bonus = p.num("low_income_exemption")
        limit = p.num("low_income_exemption_limit")
        joint_bonus = pl.when(agih < limit).then(bonus).otherwise(0.0) + pl.when(agiw < limit).then(bonus).otherwise(0.0)
        exemp = exemp + pl.when(dependent_filer).then(0.0).otherwise(
            pl.when(is_joint).then(joint_bonus).otherwise(pl.when(agi < limit).then(bonus).otherwise(0.0))
        )

    # Social Security exemption (2007+), reduced by AGI over the phaseout start.
    phases = MO_PARAMS["social_security_exemption_phaseout_start"]
    phase = pl.when(is_joint).then(float(phases["married_joint"])).otherwise(float(phases["single"]))
    ssaxmp = pl.lit(0.0)
    if y >= 2007:
        ssaxmp = (
            p.num("social_security_exemption_share") * pl.col("taxable_social_security") - (agi - phase).clip(0, None)
        ).clip(0, None)
        exemp = exemp + ssaxmp

    # Public pension exemption for taxpayers 65 or older (1990+).
    if y >= 1990:
        magi = agi - pl.col("taxable_social_security")
        exc = (magi - phase).clip(0, None)
        pension = pl.col("pensions")
        limits = MO_PARAMS["pension_exemption_income_limit"]
        slim = pl.when(is_joint | is_sep).then(float(limits["married"]) / sep).otherwise(float(limits["single"]))
        if y <= 1998:
            scred = pl.when(magi <= slim).then(
                pl.min_horizontal(pl.lit(float(MO_PARAMS["pension_exemption_1990_1998"])), pension)
            ).otherwise(0.0)
        elif y <= 2006:
            cap = float(MO_PARAMS["pension_exemption_1999_2013"])
            scred = pl.when(magi - slim <= cap * txp).then(pl.min_horizontal(pl.lit(cap), pension)).otherwise(0.0)
        else:
            ssmax = p.num("pension_exemption_maximum")
            if y <= 2013:
                cap = float(MO_PARAMS["pension_exemption_1999_2013"])
                limited = pl.min_horizontal(pl.lit(ssmax), p.num("social_security_exemption_share") * pension)
                if y <= 2008:
                    nontaxable = pl.col("gssi") - pl.col("taxable_social_security")
                    allowed = pl.max_horizontal((limited - nontaxable).clip(0, None), pl.min_horizontal(pl.lit(cap), pension))
                else:
                    allowed = (pl.max_horizontal(limited, pl.min_horizontal(pl.lit(cap), pension)) - ssaxmp).clip(0, None)
            else:
                allowed = (pl.min_horizontal(pl.lit(ssmax), pension) - ssaxmp).clip(0, None)
            scred = (allowed - exc).clip(0, None)
        exemp = exemp + pl.when(aged > 0).then(scred).otherwise(0.0)
    exemp = pl.when(dependent_filer).then(0.0).otherwise(exemp)

    # --- Itemized deductions (only when itemizing federally) ---
    socsec = taxsim_socsec(y, setax, pl.col("addmed"))
    xitded = (deducp + pl.min_horizontal(socsec, p.num("payroll_tax_deduction_cap") * txp) - salt_ded).clip(0, None)
    if y == 1993:
        septx = pl.min_horizontal(p.num("self_employment_tax_deduction_cap_1993") * txp, setax)
    elif y in (2011, 2012):
        septx = pl.when(setax <= p.num("self_employment_tax_deduction_threshold")).then(
            p.num("self_employment_tax_deduction_lower_rate") * setax
        ).otherwise(
            p.num("self_employment_tax_deduction_upper_rate") * setax + p.num("self_employment_tax_deduction_amount")
        )
    else:
        septx = p.num("self_employment_tax_deduction_rate") * setax
    xitded = (xitded - septx).clip(0, None)
    stax = salt_ded
    if 1993 <= y <= 2017:
        if y >= 2013:
            threshold = (
                p.num("itemized_limit_threshold_base")
                * p.num("itemized_limit_inflation")
                * by_filing_status(MO_PARAMS["itemized_limit_status_multiplier"])
            )
        else:
            threshold = p.num("itemized_limit_threshold_base") * p.num("itemized_limit_inflation") / sep
        xtot = salt_ded + pl.col("proptax") + pl.col("mortgage") + pl.col("charity_cash")
        over = agi - threshold
        xconst = pl.min_horizontal(p.num("itemized_limit_max_share") * xtot, p.num("itemized_limit_rate") * over)
        stax = pl.when((xtot > 0) & (over > 0)).then((xtot - xconst) / xtot * salt_ded).otherwise(salt_ded)
    xitded = (xitded - dedphs + (salt_ded - stax)).clip(0, None)
    if y == 1999:
        xitded = pl.when(forced_standard()).then(0.0).otherwise(xitded)
    detail_xitded = pl.when(fed_itemizes).then(xitded).otherwise(0.0)
    df, (deduc,) = checkpoint(
        df, mo_deduc=pl.when(fed_itemizes).then(pl.max_horizontal(fed_zbr, xitded)).otherwise(fed_zbr)
    )

    # --- Deduction for federal income tax ---
    if y <= 1986:
        fedtax = pl.col("pre1987_taxbc") - pl.col("federal_chcr") - pl.col("pre1987_earncr")
    else:
        if y == 2021:
            ccc_used = pl.lit(0.0)
        else:
            ccc_used = pl.col("federal_chcr")
        if year == 2021:
            ctc_used = pl.col("actc")
        elif year >= 1998:
            ctc_used = pl.col("odc")
        else:
            ctc_used = pl.lit(0.0)
        # `comnew(52)-comnew(53)-comnew(54)-comnew(59)-comnew(81)-comnew(94)`.
        fedtax = (
            pl.col("regular_tax") - ccc_used - pl.col("federal_elder") - pl.col("eitc") - ctc_used - pl.col("making_work_pay")
        )
    fedtax = fedtax.clip(0, None)
    if 1994 <= y <= 2018:
        fedtax = pl.min_horizontal(fedtax, p.num("federal_tax_deduction_cap_per_taxpayer") * txp)
    if y >= 2019:
        share = pl.lit(0.0)
        for upper, value in reversed(MO_PARAMS["federal_tax_deduction_share_2019"]):
            share = pl.when(agi <= upper).then(pl.lit(float(value))).otherwise(share)
        fedtax = share * fedtax

    df, (fedtax, exemp) = checkpoint(df, mo_fedtax=fedtax, mo_exemp=exemp)
    cg_subtraction = pl.lit(0.0)
    if y >= 2025 and behavior.mode.value == "statutory":
        cg_subtraction = float(resolve_year(MO_PARAMS["capital_gains_subtraction_2025plus"], y)) * (
            pl.col("stcg") + pl.col("ltcg")
        ).clip(0, None)
    df, (taxinc,) = checkpoint(df, mo_taxinc=(agi - deduc - fedtax - exemp - cg_subtraction).clip(0, None))
    aif = p.num("bracket_inflation")
    brackets = scale_brackets(resolve_year(MO_PARAMS["brackets"], y), aif)
    df, (taxh, taxw) = checkpoint(df, mo_taxinc_h=taxinc * agih / agi, mo_taxinc_w=taxinc * agiw / agi)
    statax = bracket_tax(taxinc, brackets)
    split_tax = bracket_tax(taxh, brackets) + bracket_tax(taxw, brackets)
    rate_expr = pl.when(is_joint & (agi > 0)).then(bracket_rate(taxw, brackets)).otherwise(
        bracket_rate(taxinc, brackets)
    )
    statax = pl.when(is_joint & (agi > 0)).then(pl.min_horizontal(statax, split_tax)).otherwise(statax)
    statax = pl.when(taxinc < p.num("minimum_taxable_income") * aif).then(0.0).otherwise(statax)

    # --- Property tax credit for taxpayers 65 or older ---
    proptax, rentpaid = pl.col("proptax"), pl.col("rentpaid")
    hy1 = pl.col("mo_household_income")
    if y <= 2007:
        married_sub = p.num("property_credit_married_subtraction")
    else:
        married_sub = (
            pl.when(proptax > 0).then(float(MO_PARAMS["property_credit_married_owner_subtraction_2008"])).otherwise(0.0)
            + pl.when(rentpaid > 0).then(p.num("property_credit_married_subtraction")).otherwise(0.0)
        )
    hy1 = pl.when(is_joint).then((hy1 - married_sub).clip(0, None)).otherwise(hy1)
    schedule = resolve_year(MO_PARAMS["property_credit_schedule"], y)
    base, step = float(schedule["base"]), float(schedule["step"])
    income_limit, maximum = p.num("property_credit_income_limit"), p.num("property_credit_maximum")
    steps = pl.lit(float(schedule["steps"]))
    if y >= 2008:
        owner = MO_PARAMS["property_credit_owner_2008"]
        is_owner = proptax > 0
        income_limit = pl.when(is_owner).then(float(owner["income_limit"])).otherwise(income_limit)
        maximum = pl.when(is_owner).then(float(owner["maximum"])).otherwise(maximum)
        steps = pl.when(is_owner).then(float(owner["steps"])).otherwise(steps)
    # The last step reached sets the income percentage; income within the
    # first step above the base gets no credit.
    reached = pl.min_horizontal(((hy1 - base) / step).floor(), steps - 1.0)
    stepped = pl.when(reached >= 1).then(
        (maximum - hy1 * float(MO_PARAMS["property_credit_rate_step"]) * (reached + 1.0)).clip(0, None)
    ).otherwise(0.0)
    ptax = proptax + float(MO_PARAMS["property_credit_rent_share"]) * rentpaid
    pcred = pl.when((aged > 0) & (hy1 < income_limit) & (ptax > 0)).then(
        pl.when(hy1 <= base).then(maximum).otherwise(stepped)
    ).otherwise(0.0)
    if y <= 1978:
        pcred = pl.min_horizontal(pcred, statax)
    statax = statax - pcred
    wftc = pl.lit(0.0)
    if y >= 2023:
        c = MO_PARAMS["working_family_credit_2023plus"]
        investment = (
            pl.col("dividends") + pl.col("intrec") + (pl.col("stcg") + pl.col("ltcg")).clip(0, None) + pl.col("otherprop").clip(0, None)
        )
        eligible = (investment <= float(resolve_year(c["investment_income_limit"], y))) & ~is_dependent_filer()
        wftc = pl.when(eligible).then(pl.min_horizontal(float(resolve_year(c["match"], y)) * pl.col("eitc"), statax.clip(0, None))).otherwise(0.0)
        statax = statax - wftc

    detail_stded = fed_zbr
    if year == 2023:
        stale_aged_adjustment = pl.when(
            pl.col("filing_status").is_in(["single", "head_of_household"])
        ).then(100.0 * aged / flate).otherwise(0.0)
        detail_stded = (detail_stded - stale_aged_adjustment).clip(0, None)
    return with_state_detail(
        df.with_columns(siitax=statax * flate),
        agi=agi,
        exemptions=exemp,
        standard_deduction=detail_stded,
        itemized_deductions=detail_xitded,
        taxable_income=taxinc,
        property_credit=pcred,
        credits=pcred,
        rate=rate_expr,
    )
