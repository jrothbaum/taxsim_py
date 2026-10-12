"""Oklahoma individual income tax calculator."""


from __future__ import annotations
import polars as pl

from taxsim_py.engine.brackets import bracket_rate, bracket_tax
from taxsim_py.engine.eitc import eitc_age_eligible, eitc_filer_eligible, federal_eitc
from taxsim_py.engine.inputs import aged_count, files_head_of_household, files_joint, files_separate, files_single, is_dependent_filer, taxpayer_count
from taxsim_py.engine.schema import PARAMETERS_ROOT, YearParams, load_yaml, resolve_year
from taxsim_py.engine.state import (
    dividend_input_adjustment,
    by_filing_status,
    checkpoint,
    dividend_exclusion_addback,
    household_income,
    pre1987_federal_itemizing,
    unemployment_total,
    with_state_detail,
)
from taxsim_py.behavior import BehaviorProfile, TAXSIM_BEHAVIOR
from taxsim_py.engine.state_extrapolation import deflate_for_extrapolation, resolve_state_year

OK_PARAMS = load_yaml(PARAMETERS_ROOT / "states" / "ok" / "income_tax.yaml")
FEDERAL_EITC_PARAMS = pl.read_csv(PARAMETERS_ROOT / "national" / "eitc.csv")
FEDERAL_EITC_MISC = load_yaml(PARAMETERS_ROOT / "national" / "eitc_misc.yaml")


def _by_table(income: pl.Expr, tables: dict, single_like: pl.Expr, lookup=bracket_tax) -> pl.Expr:
    """Tax (or `lookup`) on the single table for single and separate filers, else the married table."""
    return pl.when(single_like).then(lookup(income, tables["single"])).otherwise(
        lookup(income, tables["married"])
    )


def compute_ok_tax(df: pl.DataFrame, year: int, behavior: BehaviorProfile = TAXSIM_BEHAVIOR) -> pl.DataFrame:
    """Calculate Oklahoma income tax for each row."""
    effective_year, flate = resolve_state_year(year, behavior, OK_PARAMS)
    y = effective_year
    p = YearParams(OK_PARAMS, effective_year)
    dividend_input_adjustment()
    # Household income is taken before projected years are deflated (`hy`);
    # the 1999+ sales tax credit reads the deflated copy (`data(159)`).
    df = df.with_columns(
        ok_household_income_undeflated=household_income(),
        ok_ui=unemployment_total(),
    )
    df = df.with_columns(ok_household_income=pl.col("ok_household_income_undeflated"))
    df = deflate_for_extrapolation(df, flate, extra=("ok_ui", "ok_household_income"))

    is_joint = files_joint()
    is_sep = files_separate()
    is_hoh = files_head_of_household()
    single_like = (files_single()) | is_sep
    sep = pl.when(is_sep).then(2.0).otherwise(1.0)
    txp = taxpayer_count()
    aged = aged_count()
    dependent_filer = is_dependent_filer()
    depx = pl.col("depx")
    fed_agi = pl.col("agi")

    # --- AGI ---
    agi = fed_agi
    if y <= 1986:
        agi = agi + dividend_exclusion_addback(y)
    if y >= 1985:
        agi = agi - pl.col("taxable_social_security")
    # Retirement income exclusion.
    pensions = pl.col("pensions")
    amount = p.num("pension_exclusion")
    limits = p.value("pension_exclusion_agi_limit") if p.in_effect("pension_exclusion_agi_limit") else None
    if limits is None:
        penmax = amount * txp
    elif isinstance(limits, dict):
        under = pl.when(txp == 1).then(agi <= limits["one"]).when(txp == 2).then(agi <= limits["two"]).otherwise(False)
        penmax = pl.when(under).then(amount * aged).otherwise(0.0)
    else:
        penmax = pl.when(agi <= float(limits) * aged).then(amount * aged).otherwise(0.0)
    agi = pl.when(pensions > 0).then((agi - pl.min_horizontal(penmax, pensions)).clip(0, None)).otherwise(agi)
    if y == 2009:
        # Unemployment compensation is taxed in full.
        agi = agi - pl.col("taxable_unemployment") + pl.col("ok_ui")
    df, (agi,) = checkpoint(df, ok_agi=agi)

    exemp = (txp + depx) * p.num("exemption_amount")
    if not p.in_effect("aged_exemption_agi_limit"):
        exemp = exemp + aged * p.num("exemption_amount")
    else:
        aged_limit = by_filing_status(p.value("aged_exemption_agi_limit"))
        exemp = exemp + pl.when(fed_agi <= aged_limit).then(aged * p.num("exemption_amount")).otherwise(0.0)
    exemp = pl.when(dependent_filer).then(0.0).otherwise(exemp)

    # --- Federal values ---
    if y <= 1986:
        gross, fed_itemizes, _ = pre1987_federal_itemizing(y)
        xitded_federal = pl.when(fed_itemizes).then(gross).otherwise(0.0)
        taxbc = pl.col("pre1987_taxbc")
        fed_ccc = pl.col("federal_chcr")
    else:
        fed_itemizes = pl.col("itemizes")
        xitded_federal = pl.when(fed_itemizes).then(pl.col("itemized_deduction")).otherwise(0.0)
        taxbc = pl.col("regular_tax")
        # The federal child care credit column starts in 1998; before that it
        # is the uncapped credit limited to regular tax.
        fed_ccc = pl.col("federal_chcr")

    # --- Standard deduction ---
    if p.in_effect("standard_deduction_share"):
        c = p.value("standard_deduction_share")
        stded = (c["rate"] * agi).clip(c["minimum"] / sep, c["maximum"] / sep)
    elif p.in_effect("standard_deduction_flat"):
        amounts = p.value("standard_deduction_flat")
        stded = pl.when(single_like).then(amounts["single"]).otherwise(amounts["married"])
    else:
        stded = pl.when(is_hoh).then(p.num("standard_deduction_head_of_household")).otherwise(
            p.num("standard_deduction_per_taxpayer") * txp
        )

    perc2 = pl.lit(1.0)
    if y <= 2005:
        # --- Federal tax deducted ---
        if p.in_effect("federal_tax_deduction_cap"):
            c = p.value("federal_tax_deduction_cap")
            five = (pl.col("fiitax") - c["threshold"]).clip(0, None)
            fedtax = pl.when(five > 0).then(c["threshold"] + c["rate"] * five).otherwise(taxbc.clip(0, None))
            fedtax = fedtax.clip(None, c["cap"])
        else:
            fedtax = pl.col("fiitax").clip(0, None)
        deduc = pl.max_horizontal(xitded_federal, stded)
        taxya = (agi - (deduc + exemp)).clip(0, None)
        perc2 = pl.when(fed_agi > 0).then((agi / fed_agi).clip(0, 1)).otherwise(1.0)
        df, (taxya, perc2) = checkpoint(df, ok_taxya=taxya, ok_perc2=perc2)
        df, (taxyb,) = checkpoint(df, ok_taxyb=(taxya - perc2 * fedtax).clip(0, None))
        detail_xitded = xitded_federal
        tables = p.value("brackets")
        if not p.in_effect("federal_tax_brackets"):
            def by_status(lookup) -> pl.Expr:
                return (
                    pl.when(is_joint).then(lookup(taxyb, tables["married"]))
                    .when(is_hoh).then(lookup(taxyb, tables["head_of_household"]))
                    .otherwise(lookup(taxyb, tables["single"]))
                )

            statax = by_status(bracket_tax)
            detail_taxinc = taxyb
            rate = by_status(bracket_rate)
        else:
            federal_tables = p.value("federal_tax_brackets")
            stax1 = _by_table(taxya, tables, single_like)
            stax2 = _by_table(taxyb, federal_tables, single_like)
            statax = pl.min_horizontal(stax1, stax2)
            short = stax2 < stax1
            detail_taxinc = pl.when(short).then(taxyb).otherwise(taxya)
            rate = pl.when(short).then(_by_table(taxyb, federal_tables, single_like, bracket_rate)).otherwise(
                _by_table(taxya, tables, single_like, bracket_rate)
            )
    else:
        xitded = pl.col("itemized_deduction")
        detail_xitded = xitded_federal
        if y <= 2015:
            deduc = pl.max_horizontal(stded, xitded_federal)
        else:
            deducp = pl.col("itemized_before_limit")
            salt = pl.col("state_sales_or_income_tax_ded")
            if y <= 2017:
                # State income tax added back, net of its share of the federal limit.
                xitded = pl.when(salt > 0).then((xitded - salt * xitded / deducp).clip(0, None)).otherwise(xitded)
            else:
                other = pl.col("proptax") + pl.col("otheritem")
                sttax = pl.min_horizontal(p.num("salt_cap") / sep, other + salt)
                stt = pl.min_horizontal(salt, sttax - other)
                charity = pl.col("charity_cash")
                xitded = pl.when(deducp - stt - charity > p.num("itemized_cap")).then(
                    p.num("itemized_cap") + charity
                ).otherwise(deducp - stt)
            deduc = pl.when(fed_itemizes & (deducp > 0)).then(xitded).otherwise(stded)
            detail_xitded = pl.when(fed_itemizes & (deducp > 0)).then(xitded).otherwise(detail_xitded)
        taxinc = pl.when(fed_agi > 0).then((agi - deduc - exemp).clip(0, None)).otherwise(0.0)
        df, (taxinc,) = checkpoint(df, ok_taxinc=taxinc)
        tables = p.value("brackets")
        statax = _by_table(taxinc, tables, single_like)
        detail_taxinc = taxinc
        rate = _by_table(taxinc, tables, single_like, bracket_rate)
    df, (statax,) = checkpoint(df, ok_tax_before_credits=statax)

    # --- Credits ---
    chcr = pl.lit(0.0)
    if not p.in_effect("child_credit_agi_limit"):
        chcr = p["child_care_credit_rate"] * perc2 * pl.min_horizontal(fed_ccc, taxbc.clip(0, None))
    else:
        perc = (agi / fed_agi).clip(None, 1)
        chcare = pl.min_horizontal(fed_ccc, taxbc.clip(0, None))
        room = (taxbc - fed_ccc).clip(0, None) + pl.col("actc")
        # The federal credit slot (`comnew(81)`) holds the whole child tax
        # credit only when the federal year itself is 2021, when all of it was
        # refundable; projected years run their own federal law.
        federal_credit = pl.col("odc") + pl.col("actc") if year == 2021 else pl.col("odc")
        if y == 2021:
            chtax = pl.min_horizontal(federal_credit, room)
        else:
            chtax = pl.min_horizontal(federal_credit + pl.col("actc"), room)
        credit = pl.max_horizontal(p["child_care_credit_rate"] * chcare, p["child_tax_credit_rate"] * chtax) * perc
        eligible = (fed_agi <= p.num("child_credit_agi_limit")) & (fed_agi > 0) & (agi >= 0)
        chcr = pl.when(eligible).then(credit).otherwise(0.0)

    earncr = pl.lit(0.0)
    if p.in_effect("eitc_rate"):
        eitc_base = pl.col("eitc")
        if p.in_effect("eitc_federal_law_year"):
            law = int(p.value("eitc_federal_law_year"))
            nkids = pl.col("dep18").clip(0, 3)
            disqy = (pl.col("stcg") + pl.col("ltcg")).clip(0, None) + pl.col("dividends") + pl.col("intrec") + pl.col("otherprop").clip(0, None)
            childless = nkids == 0
            eitc_base = pl.when(eitc_filer_eligible(childless, law) & eitc_age_eligible(childless, law)).then(
                federal_eitc(
                    pl.col("earned_income"), pl.col("agi"), disqy, pl.col("filing_status"), nkids,
                    FEDERAL_EITC_PARAMS.filter(pl.col("year") == law), float(resolve_year(FEDERAL_EITC_MISC["dylim"], law)),
                )
            ).otherwise(0.0)
        earncr = pl.when(fed_agi > 0).then(p.num("eitc_rate") * eitc_base * agi / fed_agi).otherwise(0.0)

    # Property tax credit for taxpayers 65 or older.
    hhy_prop = pl.col("ok_household_income_undeflated") + pl.col("eitc")
    pcred = pl.when((aged > 0) & (hhy_prop < p.num("property_credit_income_limit"))).then(
        (pl.col("proptax") - float(p["property_credit_income_share"]) * hhy_prop).clip(0, float(p["property_credit_max"]))
    ).otherwise(0.0)

    scred = pl.lit(0.0)
    per_person = p["sales_tax_credit"]
    limits = p.value("sales_tax_credit_income_limit") if p.in_effect("sales_tax_credit_income_limit") else None
    if limits is not None and not isinstance(limits, dict):
        scred = pl.when(pl.col("ok_household_income_undeflated") < float(limits)).then(per_person * (txp + depx)).otherwise(0.0)
    elif limits is not None:
        hhy = pl.col("ok_household_income") + pl.col("eitc")
        with_dependents = (depx > 0) | (aged > 0)
        limit = pl.when(with_dependents).then(float(limits["with_dependents"])).otherwise(float(limits["alone"]))
        amount = pl.when(with_dependents).then(per_person * (txp + depx)).otherwise(per_person * txp)
        scred = pl.when(hhy <= limit).then(amount).otherwise(scred)

    scred = pl.when(dependent_filer).then(0.0).otherwise(scred)
    if not p.in_effect("eitc_refundable") or p.value("eitc_refundable"):
        statax = (statax - chcr).clip(0, None) - pcred - scred - earncr
    else:
        statax = (statax - chcr - earncr).clip(0, None) - pcred - scred

    df = df.with_columns(siitax=statax * flate)
    return with_state_detail(
        df,
        agi=agi,
        exemptions=exemp,
        standard_deduction=stded,
        itemized_deductions=detail_xitded,
        taxable_income=detail_taxinc,
        property_credit=pcred,
        child_care_credit=chcr,
        eic=earncr,
        credits=chcr + pcred + scred + earncr,
        rate=rate,
    )
