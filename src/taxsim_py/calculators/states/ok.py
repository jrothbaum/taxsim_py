"""Oklahoma individual income tax calculator."""

import polars as pl

from taxsim_py.engine.brackets import bracket_rate, bracket_tax
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
from taxsim_py.engine.state_extrapolation import deflate_for_extrapolation, resolve_state_year

OK_PARAMS = load_yaml(PARAMETERS_ROOT / "states" / "ok" / "income_tax.yaml")


def _table(kind: str, suffix: str) -> list[list[float]]:
    return OK_PARAMS["brackets"][f"{kind}{suffix}"]


def _by_table(income: pl.Expr, suffix: str, single_like: pl.Expr, lookup=bracket_tax) -> pl.Expr:
    """Tax (or `lookup`) on the single table for single and separate filers, else the married table."""
    return pl.when(single_like).then(lookup(income, _table("s", suffix))).otherwise(
        lookup(income, _table("m", suffix))
    )


def compute_ok_tax(df: pl.DataFrame, year: int) -> pl.DataFrame:
    """Calculate Oklahoma income tax for each row."""
    effective_year, flate = resolve_state_year(year)
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
    if y <= 2006:
        limit = float(p["pension_exclusion_agi_limit_per_aged_taxpayer"]) * aged
        penmax = pl.when(agi <= limit).then(p.num("pension_exclusion_per_aged_taxpayer") * aged).otherwise(0.0)
    elif y <= 2009:
        limits = p["pension_exclusion_agi_limit_2007_2009"][y]
        under = pl.when(txp == 1).then(agi <= limits["one"]).when(txp == 2).then(agi <= limits["two"]).otherwise(False)
        penmax = pl.when(under).then(float(p["pension_exclusion_per_taxpayer_2010"]) * aged).otherwise(0.0)
    else:
        penmax = float(p["pension_exclusion_per_taxpayer_2010"]) * txp
    agi = pl.when(pensions > 0).then((agi - pl.min_horizontal(penmax, pensions)).clip(0, None)).otherwise(agi)
    if y == 2009:
        # Unemployment compensation is taxed in full.
        agi = agi - pl.col("taxable_unemployment") + pl.col("ok_ui")
    df, (agi,) = checkpoint(df, ok_agi=agi)

    exemp = (txp + depx) * p.num("exemption_amount")
    if y <= 1986:
        exemp = exemp + aged * p.num("exemption_amount")
    else:
        aged_limit = by_filing_status(p["aged_exemption_agi_limit_1987"])
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
    if y <= 2005:
        low, high = p["standard_deduction_limits_2005"]
        stded = (p["standard_deduction_rate_2005"] * agi).clip(low / sep, high / sep)
    elif y == 2006:
        amounts = p["standard_deduction_2006"]
        stded = pl.when(single_like).then(amounts["single"]).otherwise(amounts["married"])
    else:
        stded = pl.when(is_hoh).then(p.num("standard_deduction_head_of_household")).otherwise(
            p.num("standard_deduction_per_taxpayer") * txp
        )

    perc2 = pl.lit(1.0)
    if y <= 2005:
        # --- Federal tax deducted ---
        if y <= 1978:
            c = p["federal_tax_deduction_1977_1978"]
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
        if y <= 1978:
            def by_status(lookup) -> pl.Expr:
                return (
                    pl.when(is_joint).then(lookup(taxyb, _table("m", "77")))
                    .when(is_hoh).then(lookup(taxyb, _table("h", "77")))
                    .otherwise(lookup(taxyb, _table("s", "77")))
                )

            statax = by_status(bracket_tax)
            detail_taxinc = taxyb
            rate = by_status(bracket_rate)
        else:
            long_suffix = p.value("long_form_tables")
            short_suffix = p.value("short_form_tables")
            stax1 = _by_table(taxya, long_suffix, single_like)
            stax2 = _by_table(taxyb, short_suffix, single_like)
            statax = pl.min_horizontal(stax1, stax2)
            short = stax2 < stax1
            detail_taxinc = pl.when(short).then(taxyb).otherwise(taxya)
            rate = pl.when(short).then(_by_table(taxyb, short_suffix, single_like, bracket_rate)).otherwise(
                _by_table(taxya, long_suffix, single_like, bracket_rate)
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
                sttax = pl.min_horizontal(p["salt_cap_2018"] / sep, other + salt)
                stt = pl.min_horizontal(salt, sttax - other)
                charity = pl.col("charity_cash")
                xitded = pl.when(deducp - stt - charity > p["itemized_cap_2018"]).then(
                    p["itemized_cap_2018"] + charity
                ).otherwise(deducp - stt)
            deduc = pl.when(fed_itemizes & (deducp > 0)).then(xitded).otherwise(stded)
            detail_xitded = pl.when(fed_itemizes & (deducp > 0)).then(xitded).otherwise(detail_xitded)
        taxinc = pl.when(fed_agi > 0).then((agi - deduc - exemp).clip(0, None)).otherwise(0.0)
        df, (taxinc,) = checkpoint(df, ok_taxinc=taxinc)
        suffix = "56" if y == 2006 else p.value("long_form_tables")
        statax = _by_table(taxinc, suffix, single_like)
        detail_taxinc = taxinc
        rate = _by_table(taxinc, suffix, single_like, bracket_rate)
    df, (statax,) = checkpoint(df, ok_tax_before_credits=statax)

    # --- Credits ---
    chcr = pl.lit(0.0)
    if y <= 2007:
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
        eligible = (fed_agi <= p["child_credit_agi_limit_2008"]) & (fed_agi > 0) & (agi >= 0)
        chcr = pl.when(eligible).then(credit).otherwise(0.0)

    earncr = pl.lit(0.0)
    if y >= 2002:
        earncr = pl.when(fed_agi > 0).then(p["eitc_rate"] * pl.col("eitc") * agi / fed_agi).otherwise(0.0)

    # Property tax credit for taxpayers 65 or older.
    hhy_prop = pl.col("ok_household_income_undeflated") + pl.col("eitc")
    pcred = pl.when((aged > 0) & (hhy_prop < p.num("property_credit_income_limit"))).then(
        (pl.col("proptax") - float(p["property_credit_income_share"]) * hhy_prop).clip(0, float(p["property_credit_max"]))
    ).otherwise(0.0)

    scred = pl.lit(0.0)
    per_person = p["sales_tax_credit"]
    if 1990 <= y <= 1998 or y == 2003:
        scred = pl.when(pl.col("ok_household_income_undeflated") < p["sales_tax_credit_income_limit_1990"]).then(per_person * (txp + depx)).otherwise(0.0)
    if 1999 <= y <= 2002 or y >= 2004:
        limits = p["sales_tax_credit_income_limit"]
        hhy = pl.col("ok_household_income") + pl.col("eitc")
        with_dependents = (depx > 0) | (aged > 0)
        limit = pl.when(with_dependents).then(_p_nested(limits["with_dependents"], y)).otherwise(_p_nested(limits["alone"], y))
        amount = pl.when(with_dependents).then(per_person * (txp + depx)).otherwise(per_person * txp)
        scred = pl.when(hhy <= limit).then(amount).otherwise(scred)

    scred = pl.when(dependent_filer).then(0.0).otherwise(scred)
    if y <= 2015:
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


def _p_nested(values: dict, year: int) -> float:
    return float(resolve_year(values, year))
