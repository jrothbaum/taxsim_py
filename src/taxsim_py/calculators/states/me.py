"""Maine individual income tax calculator."""

import polars as pl

from taxsim_py.calculators.federal_pre1987 import PRE1987_PARAMS
from taxsim_py.engine.brackets import bracket_rate, bracket_tax
from taxsim_py.engine.inputs import aged_count, federal_exemption_count, files_head_of_household, files_joint, files_separate, files_single, is_dependent_filer, taxpayer_count
from taxsim_py.engine.schema import PARAMETERS_ROOT, YearParams, load_yaml, resolve_year
from taxsim_py.engine.state import (
    by_filing_status,
    household_income,
    interpolate_table,
    pre1987_federal_itemizing,
    unemployment_total,
    with_state_detail,
)
from taxsim_py.behavior import BehaviorProfile, TAXSIM_BEHAVIOR
from taxsim_py.engine.state_extrapolation import deflate_for_extrapolation, resolve_state_year

ME_PARAMS = load_yaml(PARAMETERS_ROOT / "states" / "me" / "income_tax.yaml")
_STATUSES = ["single", "married_joint", "married_separate", "head_of_household"]

def _max2(a: pl.Expr, b: pl.Expr) -> pl.Expr:
    """Return the row-wise maximum of two expressions."""
    return pl.when(a >= b).then(a).otherwise(b)


def compute_me_tax(df: pl.DataFrame, year: int, behavior: BehaviorProfile = TAXSIM_BEHAVIOR) -> pl.DataFrame:
    state_year = "me" if behavior.mode.value == "statutory" else None
    effective_year, flate = resolve_state_year(year, state_year)
    p = YearParams(ME_PARAMS, effective_year)
    df = df.with_columns(
        me_household_income=household_income()
    )

    is_joint = files_joint()
    is_hoh = files_head_of_household()
    is_sep = files_separate()
    df = df.with_columns(
        me_sep=pl.when(is_sep).then(2.0).otherwise(1.0),
        # `txp` (`data(7)`): 2 only on joint returns; unlike `sep`, 1 for
        # separate returns.
        me_txp=pl.when(is_joint).then(2.0).otherwise(1.0),
    )
    # `texp` - the bracket-lookup divisor: 1.5 for HoH (real, unique to
    # Maine), 2 for married_joint, 1 otherwise.
    divisor = p["bracket_income_divisor"]
    me_texp = pl.when(is_joint).then(float(divisor["married_joint"])).otherwise(
        pl.when(is_hoh).then(float(divisor["head_of_household"])).otherwise(float(divisor["other"]))
    )

    setax = pl.col("payroll_setax")  # `comnew(175)`, real-year and undeflated
    # Federal total income (`comnew(65)`), which TAXSIM does not deflate.
    df = df.with_columns(me_setax=setax, me_total_income=pl.col("agi") + 0.5 * setax)

    df = deflate_for_extrapolation(df, flate, extra=("me_household_income",))

    # --- AGI ---
    me_agi = pl.col("agi")
    if effective_year == 1981:
        excl_cap = float(p["dividend_exclusion_maine_1981_per_filer"][1960])
        excl_table = PRE1987_PARAMS["dividend_exclusion"]
        fed_excl = by_filing_status({s: resolve_year(excl_table[s], effective_year) for s in _STATUSES})
        divexc = pl.min_horizontal(pl.col("dividends") + pl.col("intrec"), fed_excl)
        own_cap = pl.min_horizontal(pl.col("dividends"), excl_cap * pl.col("me_txp"))
        me_agi = me_agi + divexc - own_cap
    # Social Security benefits are exempt from 1984.
    if effective_year >= 1984:
        me_agi = me_agi - pl.col("taxable_social_security")
    # Pension deduction (2000+), per taxpayer, reduced by Social Security benefits.
    if effective_year >= 2000:
        pended = p.num("pension_deduction_per_taxpayer")
        pension_deduction = pl.min_horizontal(
            pl.col("pensions"), (pended * pl.col("me_txp") - pl.col("gssi")).clip(0, None)
        )
        if effective_year >= 2025 and behavior.mode.value == "statutory":
            # Phased out against federal AGI above a start, over a width (no deduction beyond it).
            po = resolve_year(p["pension_deduction_phaseout_2025plus"], effective_year)
            start = by_filing_status({k: float(v) for k, v in po["start"].items()})
            width = by_filing_status({k: float(v) for k, v in po["width"].items()})
            share = ((pl.col("agi") - start).clip(0, None) / width).clip(None, 1.0)
            pension_deduction = pension_deduction * (1.0 - share)
        me_agi = me_agi - pension_deduction
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
        # `comnew(3)`: the federal standard deduction, 0 when itemizing federally.
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
            old = p.num("standard_deduction_aged_addition_2003_2017")
            override_val = (
                p.num("standard_deduction_married_override") / pl.col("me_sep")
                + old * aged_count()
            )
            stded = pl.when(gets_override).then(override_val).otherwise(stded)
        if 2016 <= effective_year <= 2017:
            olds = float(p["standard_deduction_aged_addition_2016_2017"][1960]) * aged_count()
            hoh_val = float(p["standard_deduction_hoh_2016_2017"][1960]) + olds
            single_val = float(p["standard_deduction_single_2016_2017"][1960]) + olds
            stded = pl.when(is_hoh).then(hoh_val).when(files_single()).then(single_val).otherwise(stded)

    if effective_year >= 2025 and behavior.mode.value == "statutory":
        sd = resolve_year(p["standard_deduction_2025plus"], effective_year)
        base = pl.when(files_single() | is_sep).then(float(sd["single"])).when(is_hoh).then(float(sd["head_of_household"])).otherwise(float(sd["married_joint"]))
        aged_amount = pl.when(files_single() | is_hoh).then(float(sd["aged_single"])).otherwise(float(sd["aged_married"]))
        stded = pl.when(pl.col("itemizes")).then(0.0).otherwise(base + aged_amount * aged_count())
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
        # 1988 also subtracts `comnew(3)`, which is 0 when itemizing federally.
    elif effective_year <= 2012:
        xitded = (itemized_deduction_local - pl.col("state_sales_or_income_tax_ded")).clip(0, None)
    else:
        statit = pl.col("state_sales_or_income_tax_ded") - pl.col("state_sales_or_income_tax_ded") * (
            salt_plus_mortgage - itemized_deduction_local
        ) / salt_plus_mortgage.clip(1e-9, None)
        xitd_cap = p.num("itemized_deduction_phaseout_cap_2013_2021")
        xitded = pl.min_horizontal(xitd_cap, (itemized_deduction_local - statit).clip(0, None))

    xitded = pl.when(itemizing_gate).then(xitded).otherwise(0.0)

    df = df.with_columns(me_xitded=xitded, me_deduc=_max2(stded, xitded))

    if 2016 <= effective_year <= 2017:
        phased_hoh = float(p["deduction_phaseout_2016_2017_hoh_phased"][1960])
        thrsh_hoh = float(p["deduction_phaseout_2016_2017_hoh_threshold"][1960])
        phased_pf = float(p["deduction_phaseout_2016_2017_other_phased_per_filer"][1960])
        thrsh_pf = float(p["deduction_phaseout_2016_2017_other_threshold_per_filer"][1960])
        # `phased/thrsh=70000/75000*max(1,data(7))` except head of household.
        phased = pl.when(is_hoh).then(phased_hoh).otherwise(phased_pf * pl.col("me_txp"))
        thrsh = pl.when(is_hoh).then(thrsh_hoh).otherwise(thrsh_pf * pl.col("me_txp"))
        over = pl.col("me_agi") > phased
        df = df.with_columns(
            me_deduc=pl.when(over).then((pl.col("me_deduc") - pl.col("me_deduc") * (pl.col("me_agi") - phased) / thrsh).clip(0, None)).otherwise(pl.col("me_deduc"))
        )
    if effective_year >= 2018:
        xmp18 = p.num("xmp18_index")
        thr_p = p["deduction_phaseout_2018plus_threshold"]
        rng_p = p["deduction_phaseout_2018plus_range"]
        phased = by_filing_status(thr_p) * xmp18
        if effective_year >= 2022:
            phased = by_filing_status(p["deduction_phaseout_2022plus"][effective_year])
        xl4 = by_filing_status(rng_p)
        over = pl.col("me_agi") >= phased
        df = df.with_columns(
            me_deduc=pl.when(over).then(pl.col("me_deduc") * (1.0 - ((pl.col("me_agi") - phased) / xl4).clip(0, 1))).otherwise(pl.col("me_deduc"))
        )

    # --- Exemptions ---
    exemps_count = federal_exemption_count(effective_year)
    if effective_year <= 2012:
        # 1988's extra exemptions for the aged are at that year's $0 amount.
        xmp_amt = p.num("personal_exemption_amount")
        exemp = exemps_count * xmp_amt
    elif effective_year <= 2017:
        exemp = pl.col("personal_exemptions")  # the federal exemption amount
    else:
        xmp_amt = p.num("personal_exemption_amount")
        exemp = xmp_amt * pl.col("me_txp")
        xmp18 = p.num("xmp18_index")
        phasex = by_filing_status(p["exemption_phaseout_2018plus_threshold"]) * xmp18
        if effective_year >= 2022:
            phasex = by_filing_status(p["exemption_phaseout_2022plus"][effective_year])
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
        aif = p.num("bracket_inflation_factor_1993_2012")
    elif 2013 <= effective_year <= 2015:
        table = p["brackets_2013_2015"]
        aif = 1.0
    elif effective_year == 2016:
        table = p["brackets_2016"]
        aif = 1.0
    else:
        table = p["brackets_2017plus"]
        aif = p.num("bracket_inflation_factor_2017plus")
        if effective_year >= 2022:
            # Statutory dollars (single schedule; joint and head of household scale from it).
            table = p["brackets_2022plus"][effective_year]
            aif = 1.0

    tinc = pl.col("me_taxinc") / me_texp
    stat = bracket_tax(tinc / aif, table) * aif
    df = df.with_columns(me_statax=stat * me_texp)

    if 1997 <= effective_year <= 2002:
        ceiling = float(p["low_income_credit_taxinc_ceiling"][1960])
        df = df.with_columns(me_statax=pl.when(pl.col("me_taxinc") <= ceiling).then(0.0).otherwise(pl.col("me_statax")))

    # --- Extra Tax (minimum tax) ---
    if effective_year <= 1979:
        comnew28 = pl.col("regular_tax")
    elif effective_year <= 1990:
        comnew28 = pl.col("fiitax")
    else:
        comnew28 = pl.col("regular_tax")
    comnew69 = pl.col("agi") - pl.when(pl.col("itemizes")).then(pl.col("mortgage")).otherwise(0.0)
    amt = pl.col("amt")
    txm = pl.lit(0.0)
    if 1986 <= effective_year <= 1990:
        txm = pl.when(amt > 0).then((p["minimum_tax_rate_1986_1990"] * comnew69 - pl.col("me_statax")).clip(0, None)).otherwise(0.0)
    elif effective_year <= 2011:
        share = float(resolve_year(p["minimum_tax_share_of_federal_amt"], effective_year))
        txm = pl.when(amt > 0).then(share * (amt - comnew28).clip(0, None)).otherwise(0.0)
    df = df.with_columns(me_statax=pl.col("me_statax") + txm)  # `iratax` (`data(42)`) has no TAXSIM input.

    # --- Credits --- A share of the federal child care credit (`comnew(53)`),
    # limited by federal tax before credits (`comnew(52)`).
    ccc_for_chcr = pl.col("federal_chcr")
    if effective_year <= 1979:
        comnew52 = pl.col("regular_tax")
    elif effective_year <= 1986:
        comnew52 = pl.col("fiitax")
    else:
        comnew52 = pl.col("regular_tax")
    child_rate = p.num("child_care_credit_rate")
    chcr = child_rate * pl.min_horizontal(ccc_for_chcr, comnew52.clip(0, None))
    refundable_cap = float(p["child_care_credit_refundable_cap"][1960])
    chcrr = pl.min_horizontal(refundable_cap, chcr)
    chcrn = chcr - chcrr

    depcrd = pl.lit(0.0)
    dep_refundable = pl.lit(0.0)
    if effective_year >= 2018:
        per_child = float(p["dependent_credit_per_child"][1960])
        depcrd = pl.min_horizontal(per_child * pl.col("dep17"), pl.col("me_statax"))
        if effective_year >= 2022:
            # Every dependent, reduced $7.50 per $1,000 (or part) of AGI above the start.
            dep = resolve_year(p["dependent_exemption_credit_2022plus"], effective_year)
            if "start_joint" in dep and "start_hoh" in dep:
                start = (
                    pl.when(is_joint).then(float(dep["start_joint"])).when(is_hoh).then(float(dep["start_hoh"]))
                    .when(is_sep).then(float(dep["start_separate"])).otherwise(float(dep["start_single"]))
                )
            else:
                start = pl.when(is_joint).then(float(dep["start_joint"])).otherwise(float(dep["start_other"]))
            steps = ((pl.col("me_agi") - start).clip(0, None) / float(dep["increment"])).ceil()
            # A dependent under 6 counts `young_multiplier` times.
            dependents = pl.col("depx") + (float(dep["young_multiplier"]) - 1.0) * pl.col("dep6").clip(None, pl.col("depx"))
            statutory = (float(dep["amount"]) * dependents - float(dep["step"]) * steps).clip(0, None)
            depcrd = pl.min_horizontal(statutory, pl.col("me_statax"))
            if effective_year >= 2024:
                # Refundable from 2024: applied with the other refundable credits below.
                dep_refundable = statutory
                depcrd = pl.lit(0.0)

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
            pl.when(files_single()).then(float(std_caps["single"]))
            .when(is_joint).then(float(std_caps["married_joint"]))
            .when(is_hoh).then(float(std_caps["head_of_household"]))
            .otherwise(float(std_caps["married_separate"]))
        )
        credit = credit + (pct88 * pl.col("earned_income")).clip(floor88, std_cap)
        exemp_credit = (
            pl.when(pl.col("filing_status").is_in(["single", "married_separate"])).then(interpolate_table(pl.col("me_agi"), p["credit_1988_exemption_table_single"]))
            .when(is_hoh).then(interpolate_table(pl.col("me_agi"), p["credit_1988_exemption_table_hoh"]))
            .otherwise(interpolate_table(pl.col("me_agi"), p["credit_1988_exemption_table_married"]))
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
        rate_eitc = p.num("eitc_rate")
        earncr = rate_eitc * pl.col("eitc")
        if effective_year == 2020:
            # 2020 reads the federal credit before its minimum-age test.
            federal = pl.col("eitc_before_age_test")
            earncr = pl.when(pl.col("dep18") < 1).then(p["eitc_rate_childless_2020"] * federal).otherwise(rate_eitc * federal)
    if effective_year >= 2022:
        # 25% of the federal credit with a qualifying child; 50% for childless
        # filers aged 18 or older (the federal minimum age of 25 does not apply).
        rates = p["eitc_rate_2022plus"]
        older = pl.max_horizontal(pl.col("page"), pl.col("sage"))
        childless_ok = (older == 0) | (older >= 18)
        earncr = pl.when(pl.col("dep18") > 0).then(float(rates["with_child"]) * pl.col("eitc")).when(childless_ok).then(
            float(rates["childless"]) * pl.col("eitc_before_age_test")
        ).otherwise(0.0)
    df = df.with_columns(me_earncr=earncr)
    if 2000 <= effective_year <= 2015:
        df = df.with_columns(me_statax=(pl.col("me_statax") - pl.col("me_earncr")).clip(0, None))

    # --- Property Tax Fairness Credit (2013+, refundable) ---
    # Total income (`comnew(65)`) plus Social Security benefits.
    ti = (pl.col("me_total_income") + pl.col("gssi")).clip(0, None)
    aged_any = aged_count() > 0
    # `nexem=int(comnew(68))`: the federal exemption count is deflated in
    # projected years before truncation, which can zero it.
    nexem = (exemps_count / flate).floor()
    ptfc = pl.lit(0.0)
    if effective_year >= 2013:
        rent_share = p.num("ptfc_rent_share")
    if effective_year == 2013:
        ceiling = float(p["ptfc_2013_income_ceiling"][1960])
        cap = float(p["ptfc_2013_cap"][1960])
        agix = pl.col("me_agi").clip(0, None)
        bagix = p["ptfc_2013"]["income_share"] * agix
        base = pl.col("proptax") + rent_share * pl.col("rentpaid")
        cap = pl.when(aged_any).then(float(p["ptfc_2013_elderly_cap"][1960])).otherwise(cap)
        eligible = (nexem > 0) & (agix <= ceiling) & (base > bagix)
        ptfc = pl.when(eligible).then(pl.min_horizontal(cap / pl.col("me_sep"), p["ptfc_2013"]["benefit_share"] * (base - bagix))).otherwise(0.0)
    elif 2014 <= effective_year <= 2017:
        base_raw = pl.col("proptax") + rent_share * pl.col("rentpaid")
        cap_single = float(p["ptfc_2014_2017_single_base_cap"][1960])
        cap_2e = float(p["ptfc_2014_2017_2exempt_base_cap"][1960])
        cap_3e = float(p["ptfc_2014_2017_3plus_base_cap"][1960])
        inc_single = float(p["ptfc_2014_2017_single_income_cap"][1960])
        inc_2e = float(p["ptfc_2014_2017_2exempt_income_cap"][1960])
        inc_3e = float(p["ptfc_2014_2017_3plus_income_cap"][1960])
        is_single = files_single()
        base = pl.when(is_single).then(pl.min_horizontal(cap_single, base_raw)).when(nexem <= 2).then(pl.min_horizontal(cap_2e / pl.col("me_sep"), base_raw)).otherwise(pl.min_horizontal(cap_3e / pl.col("me_sep"), base_raw))
        tix = pl.when(is_single).then(pl.min_horizontal(inc_single / pl.col("me_sep"), ti)).when(nexem <= 2).then(pl.min_horizontal(inc_2e / pl.col("me_sep"), ti)).otherwise(pl.min_horizontal(inc_3e / pl.col("me_sep"), ti))
        cap_out = pl.when(aged_any).then(float(p["ptfc_2014_2017_elderly_cap"][1960])).otherwise(float(p["ptfc_2014_2017_cap"][1960]))
        eligible = nexem > 0
        ptfc = pl.when(eligible).then(pl.min_horizontal(cap_out / pl.col("me_sep"), p["ptfc_2014_2017_benefit_share"] * (base - p["ptfc_income_share_2014plus"] * tix).clip(0, None))).otherwise(0.0)
    elif effective_year >= 2018:
        base_raw = pl.col("proptax") + rent_share * pl.col("rentpaid")
        cap_single = float(p["ptfc_2018plus_single_base_cap"][1960])
        cap_2e = float(p["ptfc_2018plus_2exempt_base_cap"][1960])
        cap_3e = float(p["ptfc_2018plus_3plus_base_cap"][1960])
        inc_single = float(p["ptfc_2018plus_single_income_cap"][1960])
        inc_2e = float(p["ptfc_2018plus_2exempt_income_cap"][1960])
        inc_3e = float(p["ptfc_2018plus_3plus_income_cap"][1960])
        # 2018+ caps are not divided by `sep`.
        is_single = files_single()
        base = pl.when(is_single).then(pl.min_horizontal(cap_single, base_raw)).when(nexem <= 2).then(pl.min_horizontal(cap_2e, base_raw)).otherwise(pl.min_horizontal(cap_3e, base_raw))
        tix = pl.when(is_single).then(pl.min_horizontal(inc_single, ti)).when(nexem <= 2).then(pl.min_horizontal(inc_2e, ti)).otherwise(pl.min_horizontal(inc_3e, ti))
        cap_out = pl.when(aged_any).then(float(p["ptfc_2018plus_elderly_cap"][1960])).otherwise(float(p["ptfc_2018plus_cap"][1960]))
        eligible = nexem > 0
        ptfc = pl.when(eligible).then(pl.min_horizontal(cap_out, (base - p["ptfc_income_share_2014plus"] * tix).clip(0, None))).otherwise(0.0)
    if effective_year >= 2017:
        ptfc = pl.when(is_sep).then(0.0).otherwise(ptfc)
    if effective_year >= 2022 and behavior.mode.value == "statutory":
        # Current statute: a benefit base set by filing status and children (a higher one for a
        # taxpayer 65 or older), limited to property tax plus part of rent, less 4% of income.
        d = resolve_year(p["ptfc_2022plus"], effective_year)
        kids = pl.col("dep17")
        base = (
            pl.when(files_single()).then(float(d["single"]))
            .when(is_joint & (kids < 1) | is_hoh & (kids <= 1)).then(float(d["hoh_one_child"]))
            .otherwise(float(d["joint_or_multi"]))
        )
        older = pl.max_horizontal(pl.col("page"), pl.col("sage"))
        senior = (older >= 65) & (float(d["senior"]) > 0)
        base = pl.when(senior).then(float(d["senior"])).otherwise(base)
        countable = pl.col("proptax") + float(d["rent_share"]) * pl.col("rentpaid")
        capgn = pl.col("stcg") + pl.col("ltcg")
        fair_income = (
            pl.col("agi") + pl.col("me_setax") * 0.5 / flate + pl.col("gssi") - pl.col("taxable_social_security")
            - pl.min_horizontal(capgn, 0.0)
        )
        credit = (pl.min_horizontal(base, countable) - float(d["income_rate"]) * fair_income).clip(0, None)
        credit = pl.min_horizontal(credit, pl.when(senior).then(float(d["cap_senior"])).otherwise(float(d["cap"])))
        ptfc = pl.when(is_sep).then(0.0).otherwise(credit)

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
            ui_total = unemployment_total()
            tis = tis + ui_total - pl.col("taxable_unemployment")
        nmst_eligible = ~is_sep & ~is_dependent_filer()
        nexem_capped = nexem.clip(0, 4)
        threshold = (
            pl.when(files_single()).then(_by_status_year(p["stfc_income_threshold"]["single"], effective_year))
            .when(is_hoh).then(_by_status_year(p["stfc_income_threshold"]["head_of_household"], effective_year))
            .otherwise(_by_status_year(p["stfc_income_threshold"]["married_joint"], effective_year))
        )
        credit_amt = pl.lit(0.0)
        for n in (1, 2, 3, 4):
            amt_n = float(resolve_year(p["stfc_credit_amount"][n], effective_year))
            credit_amt = pl.when(nexem_capped == n).then(amt_n).otherwise(credit_amt)
        stfc = pl.when((nexem > 0) & nmst_eligible).then(
            (credit_amt - ((tis - threshold) / p["stfc_phaseout_step"]).clip(0, None)).clip(0, None)
        ).otherwise(0.0)

    if effective_year >= 2022:
        # Dated statutory credit: base plus a children supplement, reduced per
        # increment of income above the start.
        d = p["stfc_2022plus"][effective_year]
        children = pl.col("dep17")
        extra_joint = bind = pl.lit(0.0)
        for threshold, amount in d["additional"]["joint"]:
            extra_joint = pl.when(children >= threshold).then(float(amount)).otherwise(extra_joint)
        extra_hoh = pl.lit(0.0)
        for threshold, amount in d["additional"]["head_of_household"]:
            extra_hoh = pl.when(children >= threshold).then(float(amount)).otherwise(extra_hoh)
        statuses = {"single": pl.lit(0.0), "married_joint": extra_joint, "head_of_household": extra_hoh, "married_separate": pl.lit(0.0)}
        credit = pl.lit(0.0)
        for status, extra in statuses.items():
            row = d[status]
            if not row["increment"]:
                continue
            reduction = ((tis - float(row["start"])).clip(0, None) / float(row["increment"])).ceil() * float(row["step"])
            credit = pl.when(pl.col("filing_status") == status).then(
                (float(row["base"]) + extra - reduction).clip(0, None)
            ).otherwise(credit)
        stfc = pl.when(nmst_eligible).then(credit).otherwise(0.0)

    df = df.with_columns(me_ptfc=ptfc, me_stfc=stfc)
    df = df.with_columns(
        me_statax=pl.col("me_statax") - pl.col("me_chcrr") - pl.col("me_ptfc") - pl.col("me_stfc") - dep_refundable
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
        + pl.col("me_chcrr") + pl.col("me_ptfc") + pl.col("me_stfc") + dep_refundable,
        rate=bracket_rate(tinc / aif, table),
    )


def _by_status_year(table: dict, year: int) -> float:
    return float(resolve_year(table, year))
