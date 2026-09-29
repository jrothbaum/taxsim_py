"""Rhode Island personal income tax calculator."""

import polars as pl

from taxsim_py.engine.brackets import bracket_rate, bracket_tax
from taxsim_py.engine.inputs import aged_count, federal_exemption_count, files_head_of_household, files_joint, files_separate, files_single, is_dependent_filer, taxpayer_count
from taxsim_py.engine.schema import PARAMETERS_ROOT, YearParams, load_yaml, resolve_year
from taxsim_py.engine.state import checkpoint, household_income, interpolate_table, unemployment_total, with_state_detail
from taxsim_py.behavior import BehaviorProfile, TAXSIM_BEHAVIOR
from taxsim_py.engine.state_extrapolation import deflate_for_extrapolation, resolve_state_year

RI_PARAMS = load_yaml(PARAMETERS_ROOT / "states" / "ri" / "income_tax.yaml")


def _scaled(brackets: list[list[float]], factor: float) -> list[list[float]]:
    return [[float(start) * factor, float(rate)] for start, rate in brackets]


def _by_class(values: dict, is_single: pl.Expr, is_hoh: pl.Expr) -> pl.Expr:
    """Value by filing class: single, head of household, else married."""
    return pl.when(is_single).then(values["single"]).when(is_hoh).then(values["head_of_household"]).otherwise(
        values["married"]
    )


def _schedule_tax(
    income: pl.Expr, y: int, is_single: pl.Expr, is_hoh: pl.Expr, sep: pl.Expr, rate: bool = False
) -> pl.Expr:
    """2001-2010 schedule tax (or bracket rate); married tables apply to income times `sep`, divided back."""
    p = YearParams(RI_PARAMS, y)
    if y == 2001:
        tables = RI_PARAMS["brackets_2001"]
        factor = 1.0
    else:
        tables = RI_PARAMS["brackets_2002"]
        factor = p.num("bracket_index")
    if rate:
        return (
            pl.when(is_single).then(bracket_rate(income, _scaled(tables["single"], factor)))
            .when(is_hoh).then(bracket_rate(income, _scaled(tables["head_of_household"], factor)))
            .otherwise(bracket_rate(income * sep, _scaled(tables["married"], factor)))
        )
    return (
        pl.when(is_single).then(bracket_tax(income, _scaled(tables["single"], factor)))
        .when(is_hoh).then(bracket_tax(income, _scaled(tables["head_of_household"], factor)))
        .otherwise(bracket_tax(income * sep, _scaled(tables["married"], factor)) / sep)
    )


def _amt_rate_tax(income: pl.Expr, sep: pl.Expr) -> pl.Expr:
    p = RI_PARAMS
    return pl.when(income < p["amt_rate_breakpoint"] / sep).then(p["amt_rate_low"] * income).otherwise(
        (p["amt_rate_high"] * income - p["amt_rate_backout"] / sep).clip(0, None)
    )


def compute_ri_tax(df: pl.DataFrame, year: int, behavior: BehaviorProfile = TAXSIM_BEHAVIOR) -> pl.DataFrame:
    """Calculate Rhode Island income tax for each row."""
    effective_year, flate = resolve_state_year(year)
    y = effective_year
    p = YearParams(RI_PARAMS, effective_year)
    # Household income (`hy`) is read before TAXSIM's projected-year deflation.
    df = df.with_columns(ri_ui=unemployment_total(), ri_household_income_undeflated=household_income())
    df = deflate_for_extrapolation(df, flate, extra=("ri_ui",))

    is_single = files_single()
    is_hoh = files_head_of_household()
    is_joint = files_joint()
    sep = pl.when(files_separate()).then(2.0).otherwise(1.0)
    fed_agi = pl.col("agi")
    eitc = pl.col("eitc") if y >= 1987 else pl.col("pre1987_earncr")
    aged = aged_count()
    txp = taxpayer_count()
    married = is_joint | (files_separate())

    # --- AGI ---
    agi = fed_agi
    if y >= 2016:
        index = p.num("social_security_index")
        limits = p["social_security_agi_limit"]
        ss = pl.col("taxable_social_security")
        joint_sub = pl.when(fed_agi < limits["married_joint"] * index).then(
            pl.when(aged == 1).then(0.5 * ss).otherwise(ss)
        ).otherwise(0.0)
        # TAXSIM's test for other returns covers single, head of household
        # and separate returns.
        other_sub = pl.when(fed_agi < limits["single"] * index).then(ss).otherwise(0.0)
        agi = agi - pl.when((aged > 0) & (ss > 0)).then(pl.when(is_joint).then(joint_sub).otherwise(other_sub)).otherwise(0.0)
    if y >= 2017:
        index = p.num("retirement_exclusion_index")
        limits = p["retirement_exclusion_agi_limit"]
        fagi = pl.when(married).then(limits["married"] * index / sep).otherwise(limits["single"] * index)
        agi = agi - pl.when(fed_agi <= fagi).then(
            pl.min_horizontal(aged * float(p["retirement_exclusion_per_aged_taxpayer"]), pl.col("pensions"))
        ).otherwise(0.0)
    if y in (2009, 2020):
        # Unemployment compensation is taxed in full.
        agi = agi - pl.col("taxable_unemployment") + pl.col("ri_ui")
    df, (agi,) = checkpoint(df, ri_agi=agi)
    ltg = (pl.col("stcg") + pl.col("ltcg")).clip(0, None)

    if y <= 1986:
        taxbc = pl.col("pre1987_taxbc")
        fed_ccc = pl.col("federal_chcr")
    else:
        taxbc = pl.col("regular_tax")
        fed_ccc = pl.col("federal_chcr")

    tax25 = pl.lit(0.0)
    tax50 = pl.lit(0.0)
    detail_stded = pl.lit(0.0)
    detail_xitded = pl.lit(0.0)
    detail_exemp = pl.lit(0.0)
    detail_taxinc = pl.lit(0.0)
    if y <= 2000:
        # A share of federal tax less the child care credit (and the EITC from 1986).
        fedtax = taxbc - fed_ccc
        if y >= 1987:
            fedtax = fedtax - pl.col("federal_elder")
        if y >= 1986:
            fedtax = fedtax - eitc
        statax = p.num("federal_tax_share") * fedtax.clip(0, None)
        rate = p.num("federal_tax_share") * pl.col("federal_source_rate") / 100.0
    elif y <= 2010:
        if y <= 2002:
            taxinc = pl.col("taxable_income")
        else:
            stded = _by_class(
                {k: float(resolve_year(v, y)) for k, v in p["standard_deduction"].items()}, is_single, is_hoh
            )
            aged_add = p["standard_deduction_aged_addition"]
            stded = stded + aged * pl.when(is_single | is_hoh).then(float(resolve_year(aged_add["single"], y))).otherwise(
                float(resolve_year(aged_add["married"], y))
            )
            dependent_limit = pl.max_horizontal(
                pl.lit(p.num("dependent_standard_deduction_floor")),
                pl.col("earned_income") + float(p["dependent_standard_deduction_earned_addition"]),
            )
            stded = pl.when(is_dependent_filer()).then(pl.min_horizontal(stded, dependent_limit)).otherwise(stded)
            deduc = pl.max_horizontal(stded, pl.col("itemized_deduction").clip(0, None))
            detail_stded = stded
            detail_xitded = pl.col("itemized_deduction").clip(0, None)
            taxinc = (agi - deduc - pl.col("personal_exemptions")).clip(0, None)
        df, (taxinc,) = checkpoint(df, ri_taxinc=taxinc)
        statax = _schedule_tax(taxinc, y, is_single, is_hoh, sep)
        rate = _schedule_tax(taxinc, y, is_single, is_hoh, sep, rate=True)
        detail_taxinc = taxinc
        if 2003 <= y <= 2009:
            # Capital gains worksheet.
            taxnon = (taxinc - ltg).clip(0, None)
            stanon = _schedule_tax(taxnon, y, is_single, is_hoh, sep)
            br = pl.min_horizontal(taxinc, _by_class(p["capital_gains_bracket"][y], is_single, is_hoh) / sep)
            taxy25 = br - pl.min_horizontal(taxnon, br)
            taxy50 = pl.min_horizontal(ltg, taxinc) - taxy25
            tax25 = pl.when(ltg > 0).then(p["capital_gains_low_rate"] * taxy25).otherwise(0.0)
            tax50 = pl.when(ltg > 0).then(p["capital_gains_high_rate"] * taxy50).otherwise(0.0)
            statax = pl.when(ltg > 0).then(pl.min_horizontal(stanon + tax25 + tax50, statax)).otherwise(statax)
            rate = pl.when(ltg > 0).then(_schedule_tax(taxnon, y, is_single, is_hoh, sep, rate=True)).otherwise(rate)
    else:
        stded = _by_class(
            {k: float(resolve_year(v, y)) for k, v in p["standard_deduction"].items()}, is_single, is_hoh
        )
        # The federal exemption count is deflated with dollar amounts in projected years.
        exemps = federal_exemption_count(y) / flate
        exemp = exemps * p.num("exemption_amount")
        index = p.num("bracket_index")
        start = p["deduction_phaseout_start"] * index
        end = p["deduction_phaseout_end"] * index
        step = p["deduction_phaseout_step"] * index
        number = ((agi - start) / step + 1).floor()
        perc = pl.lit(1.0)
        for n, share in reversed(list(enumerate(p["deduction_phaseout_shares"], start=1))):
            perc = pl.when(number == n).then(share).otherwise(perc)
        ratio = (p["deduction_phaseout_smooth_rate"] * (agi - start).clip(0, None) / step).clip(None, 1)
        kept = perc * (1 - ratio)
        factor = pl.when(agi > end).then(0.0).when(agi > start).then(kept).otherwise(1.0)
        taxinc = (agi - stded * factor / sep - exemp * factor).clip(0, None)
        df, (taxinc,) = checkpoint(df, ri_taxinc=taxinc)
        statax = bracket_tax(taxinc, _scaled(p["brackets_2011"], index))
        rate = bracket_rate(taxinc, _scaled(p["brackets_2011"], index))
        detail_stded = stded * factor
        detail_exemp = exemp * factor
        detail_taxinc = taxinc
    df, (statax,) = checkpoint(df, ri_statax=statax)

    # --- Minimum tax ---
    if 2001 <= y <= 2002:
        statax = statax + pl.when(pl.col("amt") > 0).then(
            (p["federal_amt_share"] * pl.col("amt") - statax).clip(0, None)
        ).otherwise(0.0)
    elif 2003 <= y <= 2010:
        alminy_federal = pl.col("amt_income")
        exemption = _by_class(p.value("amt_exemption"), is_single, is_hoh) / sep
        start = _by_class(p.value("amt_exemption_phaseout_start"), is_single, is_hoh) / sep
        exclnt = (exemption - p["amt_exemption_phaseout_rate"] * (alminy_federal - start).clip(0, None)).clip(0, None)
        alminy = (alminy_federal - exclnt).clip(0, None)
        # Without gains, tax is subtracted twice (logged).
        without_gains = (_amt_rate_tax(alminy, sep) - 2 * statax).clip(0, None)
        with_gains = (_amt_rate_tax((alminy - ltg).clip(0, None), sep) + tax25 + tax50 - statax).clip(0, None)
        statax = statax + pl.when(ltg < 1).then(without_gains).otherwise(with_gains)
    if 2006 <= y <= 2010:
        statax = pl.min_horizontal(statax, p.num("flat_tax_rate") * fed_agi.clip(0, None))

    # --- Property tax relief credit for taxpayers 65 or older ---
    hy = pl.col("ri_household_income_undeflated")
    relief = p.value("property_relief")
    ptax = pl.col("proptax") + float(p["property_relief_rent_share"]) * pl.col("rentpaid")

    def frac_for(c: dict) -> pl.Expr:
        within = hy <= c["limit"] if c.get("inclusive") else hy < c["limit"]
        return pl.when(within).then(interpolate_table(hy, c["table"])).otherwise(0.0)

    frac = (
        pl.when((txp > 1) | (pl.col("depx") > 0)).then(frac_for(relief["multi"]))
        .when(txp == 1).then(frac_for(relief["single"]))
        .otherwise(0.0)
    )
    pcred = pl.when((aged > 0) & (frac > 0)).then((ptax - frac * hy).clip(0, p.num("property_relief_max"))).otherwise(0.0)

    # --- Credits ---
    chcr = pl.lit(0.0)
    earncr = pl.lit(0.0)
    if y >= 2001:
        share = p.num("child_care_share")
        chcr = share * pl.min_horizontal(fed_ccc, taxbc.clip(0, None))
        statax = (statax - chcr).clip(0, None)
    if y <= 2000:
        statax = (statax - pcred).clip(0, None)
        if y >= 1986:
            # Reported only: a share of the federal EITC, limited by federal tax.
            fed = taxbc - fed_ccc
            if y >= 1987:
                fed = fed - pl.col("federal_elder")
            earncr = p.num("federal_tax_share") * pl.min_horizontal(eitc, fed.clip(0, None))
    if 2001 <= y <= 2002:
        earncr = p.num("eitc_share") * eitc
        statax = (statax - pcred - earncr).clip(0, None)
    elif y >= 2003:
        refundable = p.num("eitc_refundable_share")
        if y <= 2014:
            nonrefundable = pl.min_horizontal(statax, p["eitc_nonrefundable_share"] * eitc)
            earncr = nonrefundable + refundable * (p["eitc_nonrefundable_share"] * eitc - nonrefundable)
        else:
            earncr = refundable * eitc
        statax = statax - earncr
        statax = statax - pcred

    df = df.with_columns(siitax=statax * flate)
    return with_state_detail(
        df,
        agi=agi,
        exemptions=detail_exemp,
        standard_deduction=detail_stded,
        itemized_deductions=detail_xitded,
        taxable_income=detail_taxinc,
        property_credit=pcred,
        child_care_credit=chcr,
        eic=earncr,
        credits=pcred + earncr,
        rate=rate,
    )
