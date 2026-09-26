"""Indiana individual income tax calculator."""

import polars as pl

from taxsim_py.calculators.federal_pre1987 import PRE1987_PARAMS
from taxsim_py.calculators.payroll import payroll_parts
from taxsim_py.engine.inputs import aged_count, is_dependent_filer, taxpayer_count
from taxsim_py.engine.schema import PARAMETERS_ROOT, load_yaml, resolve_year
from taxsim_py.engine.state import household_income, interpolate_table, with_default as _with_default, with_defaults, with_state_detail
from taxsim_py.engine.state_extrapolation import deflate_for_extrapolation, resolve_state_year

STATE_ADJUSTMENT_PARAMS = load_yaml(PARAMETERS_ROOT / "national" / "state_adjustments.yaml")
IN_PARAMS = load_yaml(PARAMETERS_ROOT / "states" / "in" / "income_tax.yaml")

def compute_in_tax(df: pl.DataFrame, year: int) -> pl.DataFrame:
    effective_year, flate = resolve_state_year(year)
    p = IN_PARAMS
    df = with_defaults(df, ("proptax", "dividends", "intrec", "ui", "pui", "sui", "depx", "dep17", "dep18"))
    df = _with_default(df, "earned_income")
    df = _with_default(df, "eitc")
    df = _with_default(df, "taxable_unemployment")
    df = _with_default(df, "taxable_social_security")
    df = df.with_columns(
        in_household_income=household_income(
            float(resolve_year(STATE_ADJUSTMENT_PARAMS["household_income_dividend_adjustment"], effective_year)),
            float(resolve_year(STATE_ADJUSTMENT_PARAMS["household_income_record_adjustment"], effective_year)),
        )
    )

    df = df.with_columns(
        in_sep=pl.when(pl.col("filing_status") == "married_separate").then(2.0).otherwise(1.0),
        in_num_filers=taxpayer_count(),
    )

    df = deflate_for_extrapolation(
        df,
        flate,
        [
            "pwages", "swages", "proptax", "otheritem", "mortgage", "dividends", "intrec",
            "stcg", "ltcg", "ui", "pui", "sui", "agi", "earned_income", "eitc", "taxable_unemployment",
            "taxable_social_security", "rentpaid", "in_household_income",
        ],
    )

    rate = float(resolve_year(p["rate_by_year"], effective_year))

    # --- AGI ---
    # Social Security benefits are exempt.
    df = df.with_columns(in_agi=pl.col("agi").clip(0, None) - pl.col("taxable_social_security"))

    ui_total = pl.max_horizontal(pl.col("ui"), pl.col("pui") + pl.col("sui"))

    if effective_year == 1981:
        excl_table = PRE1987_PARAMS["dividend_exclusion"]
        fed_excl = pl.lit(None, dtype=pl.Float64)
        for status in ("single", "head_of_household", "married_separate", "married_joint"):
            fed_excl = (
                pl.when(pl.col("filing_status") == status)
                .then(pl.lit(float(resolve_year(excl_table[status], effective_year))))
                .otherwise(fed_excl)
            )
        divexc = pl.min_horizontal(pl.col("dividends") + pl.col("intrec"), fed_excl)
        own_excl_per_filer = float(p["dividend_exclusion_indiana_1981_per_filer"][1960])
        own_excl = pl.min_horizontal(pl.col("dividends"), own_excl_per_filer * pl.col("in_num_filers"))
        df = df.with_columns(in_agi=pl.col("in_agi") + divexc - own_excl)

    df = df.with_columns(in_untax=pl.col("taxable_unemployment"))

    if effective_year == 2020:
        df = df.with_columns(in_agi=pl.col("in_agi") + ui_total - pl.col("in_untax"))
        # `comnew(26)<1` (not itemizing) $300 cash-charity addback: `data(58)`
        # (charity_cash) has no corresponding input column - confirmed inert.

    if effective_year == 2009:
        cap = float(p["ui_2009_addback_cap_per_filer"][1960])
        df = df.with_columns(in_agi=pl.col("in_agi") + pl.min_horizontal(ui_total, cap * pl.col("in_num_filers")))

    # Indiana's own UI exclusion is zero when no UI is present.
    thr_single = float(p["ui_exclusion_threshold_single"][1960])
    thr_joint = float(p["ui_exclusion_threshold_married_joint"][1960])
    threshold = pl.when(pl.col("filing_status") == "married_joint").then(thr_joint).otherwise(thr_single)
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
        wages_plus_se = pl.col("wages") + pl.col("psemp") + pl.col("ssemp")
        eligible = (
            (pl.col("depx") > 0)
            & (pl.col("in_agi") < 12000.0)
            & (0.8 * pl.col("in_agi").clip(0, None) < wages_plus_se)
        )
        dedei = pl.when(eligible).then(12000.0 - pl.col("in_agi").clip(0, None)).otherwise(0.0)
    else:
        dedei = pl.lit(0.0)

    df = df.with_columns(in_deduc=dedr + dedown + dedei)

    # --- Exemptions ---
    # `comnew(68)` (exemps count) sits at array position 68 - inside the
    # dispatcher's own generic comnew(1:98) deflate loop
    # (taxsim_2024_09_21.f:62-65), which divides by `flate` blindly by
    # POSITION, not by whether the value is really a dollar amount. A
    # pure integer count getting divided by a CPI ratio is a genuine,
    # replicated-as-found quirk (already flagged in state_cpi_
    # extrapolation.yaml's own module note) - real and nonzero only for
    # extrapolated years (flate!=1). `depx` itself (`data(8)`, position 8,
    # below the loop's own >=11 floor) is NEVER divided.
    aged = aged_count()
    if effective_year <= 1986:
        exemps_count = (pl.col("in_num_filers") + pl.col("depx") + aged) / flate
    else:
        exemps_count = pl.when(is_dependent_filer()).then(0.0).otherwise(pl.col("in_num_filers") + pl.col("depx")) / flate
    if effective_year <= 1979:
        exemp = (pl.col("in_num_filers") + aged) * 1000.0 + pl.col("depx") * 500.0
    elif effective_year <= 1984:
        base = (exemps_count + aged) * 500.0
        is_joint = pl.col("filing_status") == "married_joint"
        xtra1 = (pl.col("in_agi") / 3.0 - 500.0).clip(0, 500)
        xtra2 = (pl.col("in_agi") * 2.0 / 3.0 - 500.0).clip(0, 500)
        xtra = pl.when(is_joint).then(xtra1 + xtra2).otherwise((pl.col("in_agi") - 500.0).clip(0, 500))
        exemp = base + xtra
    elif effective_year <= 1986:
        exemp = exemps_count * 1000.0
    else:
        # A dependent filer claims one exemption.
        exemp = (is_dependent_filer().cast(pl.Float64) + exemps_count + aged) * 1000.0
        if effective_year >= 1999:
            low = pl.col("agi") < p["aged_low_income_exemption_agi_limit"]
            exemp = exemp + pl.when(low).then(p["aged_low_income_exemption"] * aged).otherwise(0.0)

    if effective_year in (1997, 1998):
        exemp = exemp + 500.0 * pl.col("depx")
    elif effective_year >= 1999:
        exemp = exemp + 1500.0 * pl.col("depx")

    df = df.with_columns(in_exemp=exemp)

    df = df.with_columns(in_taxinc=(pl.col("in_agi") - pl.col("in_deduc") - pl.col("in_exemp")).clip(0, None))
    df = df.with_columns(in_statax=pl.col("in_taxinc") * rate)
    if effective_year == 1979:
        df = df.with_columns(in_statax=pl.col("in_statax") * 0.85)

    # --- Credits --- (`pcred`/`ecred` confirmed inert - see module docstring)
    if 1999 <= effective_year <= 2002:
        rate_cr = float(p["eitc_1999_2002_rate"][1960])
        cap = float(p["eitc_1999_2002_income_cap"][1960])
        # Tested against federal total income (`comnew(65)`).
        total_income = pl.col("agi") + 0.5 * payroll_parts(year)["setax"]
        eligible = (
            (pl.col("depx") > 0)
            & ((pl.col("earned_income") >= 0.8 * total_income) | (total_income < 1.0))
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
                pl.lit(float(resolve_year(p["eitc_crmax_0kids"], effective_year)))
            ).when(ieic_expr == 1).then(
                pl.lit(float(resolve_year(p["eitc_crmax_1kid"], effective_year)))
            ).otherwise(pl.lit(float(resolve_year(p["eitc_crmax_2kids"], effective_year))))
            ym = pl.when(ieic_expr == 0).then(
                pl.lit(float(resolve_year(p["eitc_ymax_0kids"], effective_year)))
            ).when(ieic_expr == 1).then(
                pl.lit(float(resolve_year(p["eitc_ymax_1kid"], effective_year)))
            ).otherwise(pl.lit(float(resolve_year(p["eitc_ymax_2kids"], effective_year))))
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
            # `comnew(6)` (net capital gain in AGI) loss add-back: no-op in
            # this project's scope (stcg/ltcg assumed non-negative). Uses
            # federal's own ORIGINAL `comnew(2)`/`agi`, NOT Indiana's own
            # progressively-adjusted `in_agi` (which by this point already
            # reflects Indiana's own UI-exclusion worksheet reduction) -
            # confirmed via a live oracle probe (2020/single/$10,000 wages/
            # $8,000 UI: using `in_agi`=$13,000 wrongly pushed this deep
            # into the phaseout band, capping the credit at $19.79 instead
            # of the real $40.09).
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
        second = float(resolve_year(p["elderly_credit_second_aged"], effective_year))
        ecred = interpolate_table(fed_agi, table) + pl.when(
            (aged > 1) & (pl.col("filing_status") == "married_joint")
        ).then(second).otherwise(0.0)
    ecred = pl.when((aged > 0) & (fed_agi < p["elderly_credit_agi_limit"])).then(ecred).otherwise(0.0)
    df = df.with_columns(in_credit=pcred + ecred + earncr)
    df = df.with_columns(in_statax=pl.col("in_statax") - pl.col("in_credit"))

    refund_cr = pl.lit(0.0)
    if effective_year == 2012:
        refund_cr = float(p["automatic_taxpayer_refund_credit_2012_per_filer"][1960]) * pl.col("in_num_filers")
        df = df.with_columns(
            in_refund_cr=pl.when(pl.col("in_statax") > 0).then(refund_cr).otherwise(0.0)
        )
        df = df.with_columns(
            in_statax=pl.col("in_statax") - pl.col("in_refund_cr")
        )
    else:
        df = df.with_columns(in_refund_cr=pl.lit(0.0))
    # `data(38)` (solar/wind carryover credit) confirmed inert, 1980-1994.

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
