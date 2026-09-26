"""Maine individual income tax calculator."""

import polars as pl

from taxsim_py.calculators.payroll import payroll_parts
from taxsim_py.calculators.federal_pre1987 import PRE1987_PARAMS
from taxsim_py.engine.brackets import bracket_rate, bracket_tax
from taxsim_py.engine.inputs import aged_count, federal_exemption_count, is_dependent_filer, taxpayer_count
from taxsim_py.engine.schema import PARAMETERS_ROOT, load_yaml, resolve_year
from taxsim_py.engine.state import (
    with_defaults,
    by_filing_status as _by_status,
    household_income,
    pre1987_federal_itemizing,
    interpolate_table as _tablki,
    with_default as _with_default,
    with_state_detail,
)
from taxsim_py.engine.state_extrapolation import deflate_for_extrapolation, resolve_state_year

ME_PARAMS = load_yaml(PARAMETERS_ROOT / "states" / "me" / "income_tax.yaml")
STATE_ADJUSTMENT_PARAMS = load_yaml(PARAMETERS_ROOT / "national" / "state_adjustments.yaml")
_STATUSES = ["single", "married_joint", "married_separate", "head_of_household"]

def _max2(a: pl.Expr, b: pl.Expr) -> pl.Expr:
    """Return the row-wise maximum of two expressions."""
    return pl.when(a >= b).then(a).otherwise(b)


def compute_me_tax(df: pl.DataFrame, year: int) -> pl.DataFrame:
    effective_year, flate = resolve_state_year(year)
    p = ME_PARAMS
    df = with_defaults(df, ("federal_chcr", "proptax", "otheritem", "mortgage", "dividends", "intrec", "depx", "dep18", "childcare"))
    df = _with_default(df, "eitc")
    df = _with_default(df, "eitc_before_age_test")
    df = _with_default(df, "ccc")
    df = _with_default(df, "itemized_deduction")
    df = _with_default(df, "salt_capped")
    df = _with_default(df, "state_sales_or_income_tax_ded")
    df = _with_default(df, "itemizes", False)
    df = _with_default(df, "fiitax")
    df = _with_default(df, "regular_tax")
    df = _with_default(df, "amt")
    df = _with_default(df, "standard_deduction")
    df = _with_default(df, "credit")
    df = _with_default(df, "earned_income")
    df = _with_default(df, "taxable_unemployment")
    df = with_defaults(
        df, ("pensions", "gssi", "taxable_social_security", "rentpaid", "federal_elder", "personal_exemptions", "charity_cash")
    )
    df = df.with_columns(
        me_household_income=household_income(
            float(resolve_year(STATE_ADJUSTMENT_PARAMS["household_income_dividend_adjustment"], effective_year)),
            float(resolve_year(STATE_ADJUSTMENT_PARAMS["household_income_record_adjustment"], effective_year)),
        )
    )

    is_joint = pl.col("filing_status") == "married_joint"
    is_hoh = pl.col("filing_status") == "head_of_household"
    is_sep = pl.col("filing_status") == "married_separate"
    df = df.with_columns(
        me_sep=pl.when(is_sep).then(2.0).otherwise(1.0),
        # `txp`/`data(7)` - real ONLY for married_joint (2); married_
        # separate files as an individual return so gets 1, same as
        # single/HoH - a genuinely DIFFERENT concept from `sep`
        # (data(9)... `mst.eq.3.or.mst.eq.6`), which is 2 for
        # married_separate specifically (confirmed via a live diff: the
        # 1987 flat `9*(txp+depx)` credit came out $9 too generous for
        # married_separate until this was corrected to 1).
        me_txp=pl.when(is_joint).then(2.0).otherwise(1.0),
    )
    # `texp` - the bracket-lookup divisor: 1.5 for HoH (real, unique to
    # Maine), 2 for married_joint, 1 otherwise.
    me_texp = pl.when(is_joint).then(2.0).otherwise(pl.when(is_hoh).then(1.5).otherwise(1.0))

    setax = payroll_parts(year)["setax"]  # `comnew(175)`, real-year and undeflated
    # Federal total income (`comnew(65)`), which TAXSIM does not deflate.
    df = df.with_columns(me_setax=setax, me_total_income=pl.col("agi") + 0.5 * setax)

    df = deflate_for_extrapolation(
        df,
        flate,
        [
            "federal_chcr",
            "pwages", "swages", "proptax", "otheritem", "mortgage", "dividends", "intrec",
            "stcg", "ltcg", "ui", "pui", "sui", "agi", "earned_income", "eitc", "ccc",
            "itemized_deduction", "salt_capped", "state_sales_or_income_tax_ded", "fiitax",
            "regular_tax", "amt", "standard_deduction", "childcare", "pensions", "gssi",
            "taxable_social_security", "rentpaid", "federal_elder", "personal_exemptions", "me_household_income",
        ],
    )

    # --- AGI ---
    me_agi = pl.col("agi")
    if effective_year == 1981:
        excl_cap = float(p["dividend_exclusion_maine_1981_per_filer"][1960])
        excl_table = PRE1987_PARAMS["dividend_exclusion"]
        fed_excl = _by_status({s: resolve_year(excl_table[s], effective_year) for s in _STATUSES})
        divexc = pl.min_horizontal(pl.col("dividends") + pl.col("intrec"), fed_excl)
        own_cap = pl.min_horizontal(pl.col("dividends"), excl_cap * pl.col("me_txp"))
        me_agi = me_agi + divexc - own_cap
    # Social Security benefits are exempt from 1984.
    if effective_year >= 1984:
        me_agi = me_agi - pl.col("taxable_social_security")
    # Pension deduction (2000+), per taxpayer, reduced by Social Security benefits.
    if effective_year >= 2000:
        pended = float(resolve_year(p["pension_deduction_per_taxpayer"], effective_year))
        me_agi = me_agi - pl.min_horizontal(
            pl.col("pensions"), (pended * pl.col("me_txp") - pl.col("gssi")).clip(0, None)
        )
    df = df.with_columns(me_agi=me_agi)

    # --- Standard deduction ---
    if effective_year <= 1982:
        pct = float(p["standard_deduction_pct_pre1988"][1960])
        floor_sh = float(p["standard_deduction_floor_single_or_hoh_1977_1982"][1960])
        ceil_sh = float(p["standard_deduction_ceiling_single_or_hoh_1977_1982"][1960])
        floor_m = float(p["standard_deduction_floor_married_1977_1982"][1960])
        ceil_m = float(p["standard_deduction_ceiling_married_1977_1982"][1960])
        is_single_group = pl.col("filing_status").is_in(["single", "head_of_household"])
        stded = pl.when(is_single_group).then((pct * pl.col("me_agi")).clip(floor_sh, ceil_sh)).otherwise(
            (pct * pl.col("me_agi")).clip(floor_m / pl.col("me_sep"), ceil_m / pl.col("me_sep"))
        )
    elif effective_year <= 1987:
        pct = float(p["standard_deduction_pct_pre1988"][1960])
        floor_sh = float(p["standard_deduction_floor_single_or_hoh_1983_1987"][1960])
        ceil_sh = float(p["standard_deduction_ceiling_single_or_hoh_1983_1987"][1960])
        floor_m = float(p["standard_deduction_floor_married_1983_1987"][1960])
        ceil_m = float(p["standard_deduction_ceiling_married_1983_1987"][1960])
        floor_s = float(p["standard_deduction_floor_separate_1983_1987"][1960])
        ceil_s = float(p["standard_deduction_ceiling_separate_1983_1987"][1960])
        stded = (
            pl.when(pl.col("filing_status").is_in(["single", "head_of_household"])).then((pct * pl.col("me_agi")).clip(floor_sh, ceil_sh))
            .when(is_joint).then((pct * pl.col("me_agi")).clip(floor_m, ceil_m))
            .otherwise((pct * pl.col("me_agi")).clip(floor_s, ceil_s))
        )
    elif effective_year == 1988:
        stded = pl.lit(0.0)
    else:
        # `comnew(3)` - real ONLY when federal does not itemize, same
        # finding Idaho's own build already established for this array
        # position (confirmed here to be a universal, not Idaho-
        # specific, quirk).
        stded = pl.when(~pl.col("itemizes")).then(pl.col("standard_deduction")).otherwise(0.0)
        if year == 2023:
            # TAXSIM's 2023 aged addition is $100 below the statutory value
            # used by the federal port; Maine reads that federal result.
            stale_aged_adjustment = pl.when(
                pl.col("filing_status").is_in(["single", "head_of_household"])
            ).then(100.0 * aged_count() / flate).otherwise(0.0)
            stded = (stded - stale_aged_adjustment).clip(0, None)
        if (2003 <= effective_year <= 2011) or (2013 <= effective_year <= 2017):
            gets_override = is_joint | is_sep
            old = float(resolve_year(p["standard_deduction_aged_addition_2003_2017"], effective_year))
            override_val = (
                float(resolve_year(p["standard_deduction_married_override"], effective_year)) / pl.col("me_sep")
                + old * aged_count()
            )
            stded = pl.when(gets_override).then(override_val).otherwise(stded)
        if 2016 <= effective_year <= 2017:
            olds = float(p["standard_deduction_aged_addition_2016_2017"][1960]) * aged_count()
            hoh_val = float(p["standard_deduction_hoh_2016_2017"][1960]) + olds
            single_val = float(p["standard_deduction_single_2016_2017"][1960]) + olds
            stded = pl.when(is_hoh).then(hoh_val).when(pl.col("filing_status") == "single").then(single_val).otherwise(stded)

    df = df.with_columns(me_stded=stded)

    # --- Itemized deduction --- (when the federal return itemizes a
    # positive gross total, `comnew(26)` and `comnew(30)`)
    if effective_year <= 1986:
        salt_plus_mortgage, itemizes_local, _ = pre1987_federal_itemizing(effective_year)
        itemized_deduction_local = pl.col("pre1987_deduc")
    else:
        salt_plus_mortgage = pl.col("salt_capped") + pl.col("mortgage")
        itemized_deduction_local = pl.col("itemized_deduction")
        itemizes_local = pl.col("itemizes")

    itemizing_gate = itemizes_local & (salt_plus_mortgage > 0)

    if effective_year <= 1988:
        xitded = (itemized_deduction_local - pl.col("state_sales_or_income_tax_ded")).clip(0, None)
        if effective_year == 1988:
            # `xitded=xitded-comnew(3)` in the source, but `comnew(3)` is
            # $0 whenever federal itemizes (see the `comnew(3)` finding
            # above) - which the outer `comnew(26)>0` gate already
            # guarantees here, so this second subtraction is always a
            # no-op and is omitted.
            pass
    elif effective_year <= 2012:
        xitded = (itemized_deduction_local - pl.col("state_sales_or_income_tax_ded")).clip(0, None)
    else:
        statit = pl.col("state_sales_or_income_tax_ded") - pl.col("state_sales_or_income_tax_ded") * (
            salt_plus_mortgage - itemized_deduction_local
        ) / salt_plus_mortgage.clip(1e-9, None)
        xitd_cap = float(resolve_year(p["itemized_deduction_phaseout_cap_2013_2021"], effective_year))
        xitded = pl.min_horizontal(xitd_cap, (itemized_deduction_local - statit).clip(0, None))

    xitded = pl.when(itemizing_gate).then(xitded).otherwise(0.0)

    df = df.with_columns(me_xitded=xitded, me_deduc=_max2(stded, xitded))

    if 2016 <= effective_year <= 2017:
        phased_hoh = float(p["deduction_phaseout_2016_2017_hoh_phased"][1960])
        thrsh_hoh = float(p["deduction_phaseout_2016_2017_hoh_threshold"][1960])
        phased_pf = float(p["deduction_phaseout_2016_2017_other_phased_per_filer"][1960])
        thrsh_pf = float(p["deduction_phaseout_2016_2017_other_threshold_per_filer"][1960])
        # real: `phased/thrsh=70000/75000*max(1,data(7))` for everyone
        # but HoH - `data(7)` is `txp` (1 single/HoH, 2 married), not
        # `sep`.
        phased = pl.when(is_hoh).then(phased_hoh).otherwise(phased_pf * pl.col("me_txp"))
        thrsh = pl.when(is_hoh).then(thrsh_hoh).otherwise(thrsh_pf * pl.col("me_txp"))
        over = pl.col("me_agi") > phased
        df = df.with_columns(
            me_deduc=pl.when(over).then((pl.col("me_deduc") - pl.col("me_deduc") * (pl.col("me_agi") - phased) / thrsh).clip(0, None)).otherwise(pl.col("me_deduc"))
        )
    if effective_year >= 2018:
        xmp18 = float(resolve_year(p["xmp18_index"], effective_year))
        thr_p = p["deduction_phaseout_2018plus_threshold"]
        rng_p = p["deduction_phaseout_2018plus_range"]
        phased = _by_status(thr_p) * xmp18
        xl4 = _by_status(rng_p)
        over = pl.col("me_agi") >= phased
        df = df.with_columns(
            me_deduc=pl.when(over).then(pl.col("me_deduc") * (1.0 - ((pl.col("me_agi") - phased) / xl4).clip(0, 1))).otherwise(pl.col("me_deduc"))
        )

    # --- Exemptions ---
    exemps_count = federal_exemption_count(effective_year)
    if effective_year <= 2012:
        # 1988's extra exemptions for the aged are at that year's $0 amount.
        xmp_amt = float(resolve_year(p["personal_exemption_amount"], effective_year))
        exemp = exemps_count * xmp_amt
    elif effective_year <= 2017:
        exemp = pl.col("personal_exemptions")  # the federal exemption amount
    else:
        xmp_amt = float(resolve_year(p["personal_exemption_amount"], effective_year))
        exemp = xmp_amt * pl.col("me_txp")
        xmp18 = float(resolve_year(p["xmp18_index"], effective_year))
        phasex = _by_status(p["exemption_phaseout_2018plus_threshold"]) * xmp18
        rng = float(p["exemption_phaseout_2018plus_range"][1960])
        over = pl.col("me_agi") > phasex
        exemp = pl.when(over).then(exemp * (1.0 - ((pl.col("me_agi") - phasex) / (rng / pl.col("me_sep"))).clip(0, 1))).otherwise(exemp)
        exemp = pl.when(is_dependent_filer()).then(0.0).otherwise(exemp)

    df = df.with_columns(me_exemp=exemp)
    df = df.with_columns(me_taxinc=(pl.col("me_agi") - pl.col("me_deduc") - pl.col("me_exemp")).clip(0, None))

    # --- Bracket tax ---
    year_tables = {
        1977: "brackets_1977", 1978: "brackets_1978", 1983: "brackets_1983",
        1984: "brackets_1984", 1985: "brackets_1985", 1986: "brackets_1986", 1987: "brackets_1987",
    }
    if effective_year in year_tables:
        table = p[year_tables[effective_year]]
        aif = 1.0
    elif 1979 <= effective_year <= 1982:
        table = p["brackets_1979_1982"]
        aif = 1.0
    elif effective_year == 1988:
        table = p["brackets_1988"]
        aif = 1.0
    elif 1989 <= effective_year <= 1990:
        table = p["brackets_1989_1990"]
        aif = 1.0
    elif 1991 <= effective_year <= 1992:
        table = p["brackets_1991_1992"]
        aif = 1.0
    elif 1993 <= effective_year <= 2012:
        table = p["brackets_1993plus"]
        aif = float(resolve_year(p["bracket_inflation_factor_1993_2012"], effective_year))
    elif 2013 <= effective_year <= 2015:
        table = p["brackets_2013_2015"]
        aif = 1.0
    elif effective_year == 2016:
        table = p["brackets_2016"]
        aif = 1.0
    else:
        table = p["brackets_2017plus"]
        aif = float(resolve_year(p["bracket_inflation_factor_2017plus"], effective_year))

    tinc = pl.col("me_taxinc") / me_texp
    stat = bracket_tax(tinc / aif, table) * aif
    df = df.with_columns(me_statax=stat * me_texp)

    if 1997 <= effective_year <= 2002:
        ceiling = float(p["low_income_credit_taxinc_ceiling"][1960])
        df = df.with_columns(me_statax=pl.when(pl.col("me_taxinc") <= ceiling).then(0.0).otherwise(pl.col("me_statax")))

    # --- Extra Tax (minimum tax, low-materiality - AMT is $0 in nearly
    # every case this project's own scope reaches) ---
    if effective_year <= 1979:
        comnew28 = pl.col("regular_tax")
    elif effective_year <= 1990:
        comnew28 = pl.col("fiitax")
    else:
        comnew28 = pl.col("regular_tax")
    comnew69 = pl.col("agi") - pl.when(pl.col("itemizes")).then(pl.col("mortgage")).otherwise(0.0)
    amt = pl.col("amt") if "amt" in df.collect_schema().names() else pl.lit(0.0)
    txm = pl.lit(0.0)
    if effective_year <= 1985:
        txm = pl.when(amt > 0).then(0.15 * (amt - comnew28).clip(0, None)).otherwise(0.0)
    elif 1986 <= effective_year <= 1990:
        txm = pl.when(amt > 0).then((0.03 * comnew69 - pl.col("me_statax")).clip(0, None)).otherwise(0.0)
    elif 1991 <= effective_year <= 2011:
        txm = pl.when(amt > 0).then(0.27 * (amt - comnew28).clip(0, None)).otherwise(0.0)
    df = df.with_columns(me_statax=pl.col("me_statax") + txm)  # `iratax` (`data(42)`) confirmed inert.

    # --- Credits --- A share of the federal child care credit (`comnew(53)`),
    # limited by federal tax before credits (`comnew(52)`).
    ccc_for_chcr = pl.col("federal_chcr")
    if effective_year <= 1979:
        comnew52 = pl.col("regular_tax")
    elif effective_year <= 1986:
        comnew52 = pl.col("fiitax")
    else:
        comnew52 = pl.col("regular_tax")
    child_rate = float(resolve_year(p["child_care_credit_rate"], effective_year))
    chcr = child_rate * pl.min_horizontal(ccc_for_chcr, comnew52.clip(0, None))
    refundable_cap = float(p["child_care_credit_refundable_cap"][1960])
    chcrr = pl.min_horizontal(refundable_cap, chcr)
    chcrn = chcr - chcrr

    depcrd = pl.lit(0.0)
    if effective_year >= 2018:
        per_child = float(p["dependent_credit_per_child"][1960])
        depcrd = pl.min_horizontal(per_child * pl.col("dep17"), pl.col("me_statax"))

    # Credit for the elderly: a share of the federal credit, 1978-2016.
    eldcr = pl.lit(0.0)
    if 1987 <= effective_year <= 2016:
        eldcr = float(p["elderly_credit_share"]) * pl.col("federal_elder")
    credit = chcrn + depcrd + eldcr

    if effective_year == 1987:
        amt_ex = float(p["credit_1987_per_exemption"][1960])
        credit = credit + amt_ex * (taxpayer_count() + pl.col("depx") + aged_count())
    if effective_year == 1978:
        cap_r = float(p["credit_1978_rentpaid_cap"][1960])
        cap_p = float(p["credit_1978_proptax_cap"][1960])
        credit = credit + pl.max_horizontal(pl.lit(0.0), pl.min_horizontal(pl.col("rentpaid"), cap_r), pl.min_horizontal(cap_p, pl.col("proptax")))
    if effective_year == 1988:
        pct88 = float(p["credit_1988_earned_income_pct"][1960])
        floor88 = float(p["credit_1988_earned_income_floor"][1960])
        std_caps = p["credit_1988_std_deduction_cap_by_status"]
        std_cap = (
            pl.when(pl.col("filing_status") == "single").then(float(std_caps["single"]))
            .when(is_joint).then(float(std_caps["married_joint"]))
            .when(is_hoh).then(float(std_caps["head_of_household"]))
            .otherwise(float(std_caps["married_separate"]))
        )
        credit = credit + (pct88 * pl.col("earned_income")).clip(floor88, std_cap)
        exemp_credit = (
            pl.when(pl.col("filing_status").is_in(["single", "married_separate"])).then(_tablki(pl.col("me_agi"), p["credit_1988_exemption_table_single"]))
            .when(is_hoh).then(_tablki(pl.col("me_agi"), p["credit_1988_exemption_table_hoh"]))
            .otherwise(_tablki(pl.col("me_agi"), p["credit_1988_exemption_table_married"]))
        )
        credit = credit + exemp_credit

    df = df.with_columns(me_chcr=chcr, me_chcrr=chcrr, me_credit=credit)
    df = df.with_columns(me_statax=(pl.col("me_statax") - pl.col("me_credit")).clip(0, None))

    # --- Property tax credit (1977-1988), for taxpayers 65 or older ---
    pcred = pl.lit(0.0)
    if effective_year <= 1988:
        hy = pl.col("me_household_income")
        aged_any = aged_count().clip(0, 1)
        ptax = pl.max_horizontal(pl.col("proptax"), p["property_tax_credit_rent_share"] * pl.col("rentpaid"))
        cap = float(p["property_tax_credit_cap"])
        if effective_year <= 1987:
            limit = pl.when(pl.col("me_txp") == 2).then(
                float(resolve_year(p["property_tax_credit_income_limit"]["two_taxpayers"], effective_year))
            ).otherwise(float(resolve_year(p["property_tax_credit_income_limit"]["one_taxpayer"], effective_year)))
            if effective_year == 1977:
                reduction = p["property_tax_credit_1977_reduction_rate"] * (hy - p["property_tax_credit_1977_income_floor"]).clip(0, None)
                pcred = (ptax - reduction).clip(0, cap)
            else:
                pcred = ptax.clip(0, cap)
            pcred = pl.when(hy <= limit).then(pcred * aged_any).otherwise(0.0)
        else:
            tiers = p["property_tax_credit_1988"]
            pcred_one = pl.lit(0.0)
            pcred_two = pl.lit(0.0)
            for rows, name in ((tiers["one_taxpayer"], "one"), (tiers["two_taxpayers"], "two")):
                expr = pl.lit(0.0)
                for upper, max_credit, share in reversed(rows):
                    expr = pl.when(hy <= upper).then(pl.min_horizontal(pl.lit(float(max_credit)), share * ptax)).otherwise(expr)
                if name == "one":
                    pcred_one = expr
                else:
                    pcred_two = expr
            pcred = pl.when(pl.col("me_txp") == 2).then(pcred_two).otherwise(pcred_one)
        df = df.with_columns(me_pcred=pcred * aged_any)
        df = df.with_columns(me_statax=(pl.col("me_statax") - pl.col("me_pcred")).clip(0, None))
    else:
        df = df.with_columns(me_pcred=pl.lit(0.0))

    # --- EITC ---
    earncr = pl.lit(0.0)
    if effective_year >= 2000:
        rate_eitc = float(resolve_year(p["eitc_rate"], effective_year))
        earncr = rate_eitc * pl.col("eitc")
        if effective_year == 2020:
            # 2020 reads the federal credit before its minimum-age test.
            federal = pl.col("eitc_before_age_test")
            earncr = pl.when(pl.col("dep18") < 1).then(p["eitc_rate_childless_2020"] * federal).otherwise(rate_eitc * federal)
    df = df.with_columns(me_earncr=earncr)
    if 2000 <= effective_year <= 2015:
        df = df.with_columns(me_statax=(pl.col("me_statax") - pl.col("me_earncr")).clip(0, None))

    # --- Property Tax Fairness Credit (2013+, refundable) --- (`pcred`
    # itself confirmed permanently inert - see module docstring)
    # Total income (`comnew(65)`) plus Social Security benefits.
    ti = (pl.col("me_total_income") + pl.col("gssi")).clip(0, None)
    aged_any = aged_count() > 0
    # `nexem=int(comnew(68))` - `comnew(68)` is itself divided by
    # `flate` for extrapolated years (the same generic comnew(1:98)
    # deflate-loop quirk Indiana/Kansas's own builds already
    # established for this exact array position), and the `int()`
    # TRUNCATION then genuinely zeroes PTFC/STFC eligibility for most
    # ordinary households once `flate`>1 - confirmed via a direct oracle
    # probe (a single filer's PTFC/STFC-driven refund present at
    # `flate=1` in 2021 vanishes entirely in 2022/2023).
    nexem = (exemps_count / flate).floor()
    ptfc = pl.lit(0.0)
    if effective_year >= 2013:
        rent_share = float(resolve_year(p["ptfc_rent_share"], effective_year))
    if effective_year == 2013:
        ceiling = float(p["ptfc_2013_income_ceiling"][1960])
        cap = float(p["ptfc_2013_cap"][1960])
        agix = pl.col("me_agi").clip(0, None)
        bagix = 0.1 * agix
        base = pl.col("proptax") + rent_share * pl.col("rentpaid")
        cap = pl.when(aged_any).then(float(p["ptfc_2013_elderly_cap"][1960])).otherwise(cap)
        eligible = (nexem > 0) & (agix <= ceiling) & (base > bagix)
        ptfc = pl.when(eligible).then(pl.min_horizontal(cap / pl.col("me_sep"), 0.4 * (base - bagix))).otherwise(0.0)
    elif 2014 <= effective_year <= 2017:
        base_raw = pl.col("proptax") + rent_share * pl.col("rentpaid")
        cap_single = float(p["ptfc_2014_2017_single_base_cap"][1960])
        cap_2e = float(p["ptfc_2014_2017_2exempt_base_cap"][1960])
        cap_3e = float(p["ptfc_2014_2017_3plus_base_cap"][1960])
        inc_single = float(p["ptfc_2014_2017_single_income_cap"][1960])
        inc_2e = float(p["ptfc_2014_2017_2exempt_income_cap"][1960])
        inc_3e = float(p["ptfc_2014_2017_3plus_income_cap"][1960])
        is_single = pl.col("filing_status") == "single"
        base = pl.when(is_single).then(pl.min_horizontal(cap_single, base_raw)).when(nexem <= 2).then(pl.min_horizontal(cap_2e / pl.col("me_sep"), base_raw)).otherwise(pl.min_horizontal(cap_3e / pl.col("me_sep"), base_raw))
        tix = pl.when(is_single).then(pl.min_horizontal(inc_single / pl.col("me_sep"), ti)).when(nexem <= 2).then(pl.min_horizontal(inc_2e / pl.col("me_sep"), ti)).otherwise(pl.min_horizontal(inc_3e / pl.col("me_sep"), ti))
        cap_out = pl.when(aged_any).then(float(p["ptfc_2014_2017_elderly_cap"][1960])).otherwise(float(p["ptfc_2014_2017_cap"][1960]))
        eligible = nexem > 0
        ptfc = pl.when(eligible).then(pl.min_horizontal(cap_out / pl.col("me_sep"), 0.5 * (base - 0.06 * tix).clip(0, None))).otherwise(0.0)
    elif effective_year >= 2018:
        base_raw = pl.col("proptax") + rent_share * pl.col("rentpaid")
        cap_single = float(p["ptfc_2018plus_single_base_cap"][1960])
        cap_2e = float(p["ptfc_2018plus_2exempt_base_cap"][1960])
        cap_3e = float(p["ptfc_2018plus_3plus_base_cap"][1960])
        inc_single = float(p["ptfc_2018plus_single_income_cap"][1960])
        inc_2e = float(p["ptfc_2018plus_2exempt_income_cap"][1960])
        inc_3e = float(p["ptfc_2018plus_3plus_income_cap"][1960])
        # 2018+ caps are NOT divided by `sep` (unlike 2014-2017's own
        # caps) - confirmed directly against the source, which has no
        # `/sep` anywhere in this branch.
        is_single = pl.col("filing_status") == "single"
        base = pl.when(is_single).then(pl.min_horizontal(cap_single, base_raw)).when(nexem <= 2).then(pl.min_horizontal(cap_2e, base_raw)).otherwise(pl.min_horizontal(cap_3e, base_raw))
        tix = pl.when(is_single).then(pl.min_horizontal(inc_single, ti)).when(nexem <= 2).then(pl.min_horizontal(inc_2e, ti)).otherwise(pl.min_horizontal(inc_3e, ti))
        cap_out = pl.when(aged_any).then(float(p["ptfc_2018plus_elderly_cap"][1960])).otherwise(float(p["ptfc_2018plus_cap"][1960]))
        eligible = nexem > 0
        ptfc = pl.when(eligible).then(pl.min_horizontal(cap_out, (base - 0.06 * tix).clip(0, None))).otherwise(0.0)
    if effective_year >= 2017:
        ptfc = pl.when(is_sep).then(0.0).otherwise(ptfc)

    # --- Sales Tax Fairness Credit (2016+, refundable) ---
    stfc = pl.lit(0.0)
    if effective_year >= 2016:
        # Federal AGI plus half of self-employment tax and untaxed Social
        # Security, with any net capital loss added back.
        capgn = pl.col("stcg") + pl.col("ltcg")
        tis = (
            pl.col("agi") + pl.col("me_setax") * 0.5 / flate + pl.col("gssi") - pl.col("taxable_social_security")
            - pl.min_horizontal(capgn, 0.0)
        )
        if effective_year == 2020:
            ui_total = pl.max_horizontal(pl.col("ui"), pl.col("pui") + pl.col("sui"))
            tis = tis + ui_total - pl.col("taxable_unemployment")
        nmst_eligible = ~is_sep & ~is_dependent_filer()
        nexem_capped = nexem.clip(0, 4)
        threshold = (
            pl.when(pl.col("filing_status") == "single").then(_by_status_year(p["stfc_income_threshold"]["single"], effective_year))
            .when(is_hoh).then(_by_status_year(p["stfc_income_threshold"]["head_of_household"], effective_year))
            .otherwise(_by_status_year(p["stfc_income_threshold"]["married_joint"], effective_year))
        )
        credit_amt = pl.lit(0.0)
        for n in (1, 2, 3, 4):
            amt_n = float(resolve_year(p["stfc_credit_amount"][n], effective_year))
            credit_amt = pl.when(nexem_capped == n).then(amt_n).otherwise(credit_amt)
        stfc = pl.when((nexem > 0) & nmst_eligible).then(
            (credit_amt - ((tis - threshold) / 50.0).clip(0, None)).clip(0, None)
        ).otherwise(0.0)

    df = df.with_columns(me_ptfc=ptfc, me_stfc=stfc)
    df = df.with_columns(
        me_statax=pl.col("me_statax") - pl.col("me_chcrr") - pl.col("me_ptfc") - pl.col("me_stfc")
    )
    if effective_year >= 2016:
        df = df.with_columns(me_statax=pl.col("me_statax") - pl.col("me_earncr"))

    df = df.with_columns(siitax=pl.col("me_statax") * flate)
    return with_state_detail(
        df,
        agi=pl.col("me_agi"),
        exemptions=pl.col("me_exemp"),
        standard_deduction=pl.col("me_stded"),
        itemized_deductions=pl.col("me_xitded"),
        taxable_income=pl.col("me_taxinc"),
        property_credit=pl.col("me_pcred"),
        child_care_credit=pl.col("me_chcr"),
        eic=pl.col("me_earncr"),
        credits=pl.col("me_credit") + pl.col("me_pcred") + pl.col("me_earncr")
        + pl.col("me_chcrr") + pl.col("me_ptfc") + pl.col("me_stfc"),
        rate=bracket_rate(tinc / aif, table),
    )


def _by_status_year(table: dict, year: int) -> float:
    from taxsim_py.engine.schema import resolve_year as _ry
    return float(_ry(table, year))
