"""Indiana individual income tax calculator."""

import polars as pl

from taxsim_py.calculators.federal_pre1987 import PRE1987_PARAMS
from taxsim_py.calculators.payroll import payroll_parts
from taxsim_py.engine.inputs import aged_count, files_joint, is_dependent_filer, separate_divisor, taxpayer_count
from taxsim_py.engine.schema import PARAMETERS_ROOT, YearParams, load_yaml, resolve_year
from taxsim_py.engine.state import by_filing_status, household_income, interpolate_table, unemployment_total, with_state_detail
from taxsim_py.engine.state_extrapolation import deflate_for_extrapolation, resolve_state_year

IN_PARAMS = load_yaml(PARAMETERS_ROOT / "states" / "in" / "income_tax.yaml")

def compute_in_tax(df: pl.DataFrame, year: int) -> pl.DataFrame:
    effective_year, flate = resolve_state_year(year)
    p = YearParams(IN_PARAMS, effective_year)
    df = df.with_columns(
        in_household_income=household_income(),
        in_sep=separate_divisor(),
        in_taxpayers=taxpayer_count(),
    )

    df = deflate_for_extrapolation(df, flate, extra=("in_household_income",))

    rate = p.num("rate_by_year")

    # --- AGI ---
    # Social Security benefits are exempt.
    df = df.with_columns(in_agi=pl.col("agi").clip(0, None) - pl.col("taxable_social_security"))

    ui_total = unemployment_total()

    if effective_year == 1981:
        excl_table = PRE1987_PARAMS["dividend_exclusion"]
        fed_excl = by_filing_status({status: resolve_year(amounts, effective_year) for status, amounts in excl_table.items()})
        divexc = pl.min_horizontal(pl.col("dividends") + pl.col("intrec"), fed_excl)
        own_excl_per_filer = float(p["dividend_exclusion_indiana_1981_per_filer"][1960])
        own_excl = pl.min_horizontal(pl.col("dividends"), own_excl_per_filer * pl.col("in_taxpayers"))
        df = df.with_columns(in_agi=pl.col("in_agi") + divexc - own_excl)

    df = df.with_columns(in_untax=pl.col("taxable_unemployment"))

    if effective_year == 2020:
        df = df.with_columns(in_agi=pl.col("in_agi") + ui_total - pl.col("in_untax"))
        # The $300 charitable deduction (`data(58)`) has no TAXSIM input.

    if effective_year == 2009:
        cap = float(p["ui_2009_addback_cap_per_filer"][1960])
        df = df.with_columns(in_agi=pl.col("in_agi") + pl.min_horizontal(ui_total, cap * pl.col("in_taxpayers")))

    # Indiana's own UI exclusion is zero when no UI is present.
    thr_single = float(p["ui_exclusion_threshold_single"][1960])
    thr_joint = float(p["ui_exclusion_threshold_married_joint"][1960])
    threshold = pl.when(files_joint()).then(thr_joint).otherwise(thr_single)
    if effective_year <= 2008:
        xlin6 = pl.min_horizontal(pl.col("in_untax"), 0.5 * (pl.col("agi") - threshold).clip(0, None))
        unded = pl.col("in_untax") - xlin6
    else:
        xlin7 = 0.5 * (pl.col("agi") + ui_total - pl.col("in_untax") - threshold).clip(0, None)
        unded = (ui_total - xlin7).clip(0, None)
    df = df.with_columns(in_agi=pl.col("in_agi") - unded)

    # --- Deductions ---
    # Renter's deduction (1979+): rent paid up to a cap, halved for
    # married separate filers.
    if effective_year >= 1979:
        key = (
            "renter_deduction_cap_1979_1998" if effective_year <= 1998
            else "renter_deduction_cap_1999_2002" if effective_year <= 2002
            else "renter_deduction_cap_2003_2007" if effective_year <= 2007
            else "renter_deduction_cap_2008plus"
        )
        dedr = pl.min_horizontal(pl.col("rentpaid"), float(p[key][1960])) / pl.col("in_sep")
    else:
        dedr = pl.lit(0.0)

    if effective_year >= 1999:
        home_cap = float(p["homeowner_proptax_deduction_cap_1999plus"][1960])
        dedown = pl.min_horizontal(pl.col("proptax"), home_cap)
        dedown = pl.when(pl.col("in_sep") == 2).then(dedown / pl.col("in_sep")).otherwise(dedown)
    else:
        dedown = pl.lit(0.0)

    if effective_year in (1997, 1998):
        ded = p["earned_income_deduction_1997_1998"]
        wages_plus_se = pl.col("wages") + pl.col("psemp") + pl.col("ssemp")
        eligible = (
            (pl.col("depx") > 0)
            & (pl.col("in_agi") < ded["income_limit"])
            & (ded["earnings_share"] * pl.col("in_agi").clip(0, None) < wages_plus_se)
        )
        dedei = pl.when(eligible).then(ded["income_limit"] - pl.col("in_agi").clip(0, None)).otherwise(0.0)
    else:
        dedei = pl.lit(0.0)

    df = df.with_columns(in_deduc=dedr + dedown + dedei)

    # --- Exemptions ---
    # The federal exemption count (`comnew(68)`) is deflated in projected
    # years; `depx` (`data(8)`) is not.
    aged = aged_count()
    if effective_year <= 1986:
        exemps_count = (pl.col("in_taxpayers") + pl.col("depx") + aged) / flate
    else:
        exemps_count = pl.when(is_dependent_filer()).then(0.0).otherwise(pl.col("in_taxpayers") + pl.col("depx")) / flate
    per_taxpayer = p.num("exemption_per_taxpayer")
    if effective_year <= 1979:
        exemp = (pl.col("in_taxpayers") + aged) * per_taxpayer + pl.col("depx") * p["exemption_per_dependent_pre1980"]
    elif effective_year <= 1984:
        base = (exemps_count + aged) * per_taxpayer
        is_joint = files_joint()
        addition = p["exemption_income_addition_1980_1984"]
        floor, maximum = addition["floor"], addition["maximum"]
        xtra1 = (pl.col("in_agi") / 3.0 - floor).clip(0, maximum)
        xtra2 = (pl.col("in_agi") * 2.0 / 3.0 - floor).clip(0, maximum)
        xtra = pl.when(is_joint).then(xtra1 + xtra2).otherwise((pl.col("in_agi") - floor).clip(0, maximum))
        exemp = base + xtra
    elif effective_year <= 1986:
        exemp = exemps_count * per_taxpayer
    else:
        # A dependent filer claims one exemption.
        exemp = (is_dependent_filer().cast(pl.Float64) + exemps_count + aged) * per_taxpayer
        if effective_year >= 1999:
            low = pl.col("agi") < p["aged_low_income_exemption_agi_limit"]
            exemp = exemp + pl.when(low).then(p["aged_low_income_exemption"] * aged).otherwise(0.0)

    if effective_year >= 1997:
        exemp = exemp + p.num("dependent_exemption_addition") * pl.col("depx")

    df = df.with_columns(in_exemp=exemp)

    df = df.with_columns(in_taxinc=(pl.col("in_agi") - pl.col("in_deduc") - pl.col("in_exemp")).clip(0, None))
    df = df.with_columns(in_statax=pl.col("in_taxinc") * rate)
    if effective_year == 1979:
        df = df.with_columns(in_statax=pl.col("in_statax") * p["tax_share_1979"])

    # --- Credits ---
    if 1999 <= effective_year <= 2002:
        rate_cr = float(p["eitc_1999_2002_rate"][1960])
        cap = float(p["eitc_1999_2002_income_cap"][1960])
        # Tested against federal total income (`comnew(65)`).
        total_income = pl.col("agi") + 0.5 * payroll_parts(year)["setax"]
        eligible = (
            (pl.col("depx") > 0)
            & ((pl.col("earned_income") >= p["eitc_1999_2002_earnings_share"] * total_income) | (total_income < 1.0))
            & (total_income < cap)
        )
        earncr = pl.when(eligible).then(rate_cr * (cap - total_income.clip(0, None))).otherwise(0.0)
    elif 2003 <= effective_year <= 2008:
        rate_cr = float(p["eitc_2003_2008_rate"][1960])
        floor = float(p["eitc_2003_2008_federal_floor"][1960])
        earncr = pl.when(pl.col("eitc") >= floor).then(rate_cr * pl.col("eitc")).otherwise(0.0)
    elif effective_year >= 2009:
        rate_cr = float(p["eitc_2009plus_rate"][1960])
        floor = float(p["eitc_2009plus_federal_floor"][1960])
        fed_eitc = pl.col("eitc")
        earncr = pl.when(fed_eitc >= floor).then(rate_cr * fed_eitc).otherwise(0.0)
        if effective_year >= 2011:
            ieic_expr = pl.min_horizontal(pl.col("dep18"), 2.0)
            crm = pl.when(ieic_expr == 0).then(
                pl.lit(p.num("eitc_crmax_0kids"))
            ).when(ieic_expr == 1).then(
                pl.lit(p.num("eitc_crmax_1kid"))
            ).otherwise(pl.lit(p.num("eitc_crmax_2kids")))
            ym = pl.when(ieic_expr == 0).then(
                pl.lit(p.num("eitc_ymax_0kids"))
            ).when(ieic_expr == 1).then(
                pl.lit(p.num("eitc_ymax_1kid"))
            ).otherwise(pl.lit(p.num("eitc_ymax_2kids")))
            rtbs = pl.when(ieic_expr == 0).then(
                pl.lit(float(p["eitc_rtbase_0kids"][1960]))
            ).when(ieic_expr == 1).then(
                pl.lit(float(p["eitc_rtbase_1kid"][1960]))
            ).otherwise(pl.lit(float(p["eitc_rtbase_2kids"][1960])))
            rtlw = pl.when(ieic_expr == 0).then(
                pl.lit(float(p["eitc_rtless_0kids"][1960]))
            ).when(ieic_expr == 1).then(
                pl.lit(float(p["eitc_rtless_1kid"][1960]))
            ).otherwise(pl.lit(float(p["eitc_rtless_2kids"][1960])))
            earny = pl.col("earned_income")
            # Uses federal AGI (`comnew(2)`), not Indiana AGI.
            modagi = pl.col("agi").clip(0, None)
            eic11 = pl.min_horizontal(rtbs * earny, crm)
            over = (modagi > ym) | (earny > ym)
            eicpo = rtlw * pl.max_horizontal(modagi, earny, ym) - rtlw * ym
            eic11 = pl.when(over).then(pl.min_horizontal(eic11, (crm - eicpo).clip(0, None))).otherwise(eic11)
            earncr = pl.min_horizontal(earncr, eic11)
    else:
        earncr = pl.lit(0.0)

    # Property tax credit (through 1980) and elderly credit, taxpayers 65 or older.
    fed_agi = pl.col("agi")
    if effective_year <= 1980:
        ptax = pl.col("proptax") + p["property_credit_rent_share"] * pl.col("rentpaid")
        pcred = pl.when(aged > 0).then(
            (interpolate_table(pl.col("in_household_income"), p["property_credit_share_table"]) * ptax).clip(0, p["property_credit_cap"])
        ).otherwise(0.0)
        ecred = pl.when(pl.col("in_household_income") < p["elderly_credit_pre1981_income_limit"]).then(
            float(p["elderly_credit_pre1981"])
        ).otherwise(0.0)
    else:
        pcred = pl.lit(0.0)
        table = p["elderly_credit_table_1981"] if effective_year <= 1984 else p["elderly_credit_table_1985"]
        second = p.num("elderly_credit_second_aged")
        ecred = interpolate_table(fed_agi, table) + pl.when(
            (aged > 1) & (files_joint())
        ).then(second).otherwise(0.0)
    ecred = pl.when((aged > 0) & (fed_agi < p["elderly_credit_agi_limit"])).then(ecred).otherwise(0.0)
    df = df.with_columns(in_credit=pcred + ecred + earncr)
    df = df.with_columns(in_statax=pl.col("in_statax") - pl.col("in_credit"))

    refund_cr = pl.lit(0.0)
    if effective_year == 2012:
        refund_cr = float(p["automatic_taxpayer_refund_credit_2012_per_filer"][1960]) * pl.col("in_taxpayers")
        df = df.with_columns(
            in_refund_cr=pl.when(pl.col("in_statax") > 0).then(refund_cr).otherwise(0.0)
        )
        df = df.with_columns(
            in_statax=pl.col("in_statax") - pl.col("in_refund_cr")
        )
    else:
        df = df.with_columns(in_refund_cr=pl.lit(0.0))
    # The solar and wind credit carryover (`data(38)`) has no TAXSIM input.

    df = df.with_columns(siitax=pl.col("in_statax") * flate)
    return with_state_detail(
        df,
        agi=pl.col("in_agi"),
        exemptions=pl.col("in_exemp"),
        taxable_income=pl.col("in_taxinc"),
        property_credit=pcred,
        eic=earncr,
        credits=pl.col("in_credit") + pl.col("in_refund_cr"),
        rate=rate,
    )
