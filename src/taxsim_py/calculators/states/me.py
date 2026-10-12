"""Maine individual income tax calculator."""


from __future__ import annotations
import polars as pl

from taxsim_py.engine.brackets import bracket_rate, bracket_tax
from taxsim_py.engine.inputs import aged_count, federal_exemption_count, files_head_of_household, files_joint, files_separate, files_single, is_dependent_filer, taxpayer_count
from taxsim_py.engine.schema import PARAMETERS_ROOT, YearParams, load_yaml, resolve_year
from taxsim_py.engine.state import (
    federal_dividend_exclusion_limit,
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
    effective_year, flate = resolve_state_year(year, behavior, ME_PARAMS)
    p = YearParams(ME_PARAMS, effective_year)
    df = df.with_columns(
        me_household_income=household_income(behavior)
    )

    is_joint = files_joint()
    is_hoh = files_head_of_household()
    is_sep = files_separate()
    df = df.with_columns(
        me_sep=pl.when(is_sep).then(2.0).otherwise(1.0),
        me_txp=pl.when(is_joint).then(2.0).otherwise(1.0),
    )
    # The bracket income divisor: 2 joint, 1.5 head of household, 1 otherwise.
    divisor = p["bracket_income_divisor"]
    me_texp = pl.when(is_joint).then(float(divisor["married_joint"])).otherwise(
        pl.when(is_hoh).then(float(divisor["head_of_household"])).otherwise(float(divisor["other"]))
    )

    # Self-employment tax and federal total income are not deflated in
    # projected years.
    setax = pl.col("payroll_setax")
    df = df.with_columns(me_setax=setax, me_total_income=pl.col("agi") + 0.5 * setax)

    df = deflate_for_extrapolation(df, flate, extra=("me_household_income",))

    # --- AGI ---
    me_agi = pl.col("agi")
    if p.in_effect("dividend_exclusion_per_taxpayer"):
        excl_cap = p.num("dividend_exclusion_per_taxpayer")
        fed_excl = federal_dividend_exclusion_limit(effective_year, behavior)
        divexc = pl.min_horizontal(pl.col("dividends") + pl.col("intrec"), fed_excl)
        own_cap = pl.min_horizontal(pl.col("dividends"), excl_cap * pl.col("me_txp"))
        me_agi = me_agi + divexc - own_cap
    # Social Security benefits are exempt from 1984.
    if effective_year >= 1984:
        me_agi = me_agi - pl.col("taxable_social_security")
    # Pension deduction (2000+), per taxpayer, reduced by Social Security benefits.
    if p.in_effect("pension_deduction_per_taxpayer"):
        pended = p.num("pension_deduction_per_taxpayer")
        pension_deduction = pl.min_horizontal(
            pl.col("pensions"), (pended * pl.col("me_txp") - pl.col("gssi")).clip(0, None)
        )
        if p.in_effect("pension_deduction_phaseout"):
            po = p.value("pension_deduction_phaseout")
            start = by_filing_status({k: float(v) for k, v in po["start"].items()})
            width = by_filing_status({k: float(v) for k, v in po["width"].items()})
            share = ((pl.col("agi") - start).clip(0, None) / width).clip(None, 1.0)
            pension_deduction = pension_deduction * (1.0 - share)
        me_agi = me_agi - pension_deduction
    df = df.with_columns(me_agi=me_agi)

    # --- Standard deduction ---
    if p.in_effect("standard_deduction_percent"):
        sd = p.value("standard_deduction_percent")
        pct = float(sd["rate"])
        is_single_group = pl.col("filing_status").is_in(["single", "head_of_household"])
        single_d = (pct * pl.col("me_agi")).clip(float(sd["single"]["floor"]), float(sd["single"]["ceiling"]))
        if "separate" in sd:
            stded = (
                pl.when(is_single_group).then(single_d)
                .when(is_joint).then((pct * pl.col("me_agi")).clip(float(sd["married"]["floor"]), float(sd["married"]["ceiling"])))
                .otherwise((pct * pl.col("me_agi")).clip(float(sd["separate"]["floor"]), float(sd["separate"]["ceiling"])))
            )
        else:
            stded = pl.when(is_single_group).then(single_d).otherwise(
                (pct * pl.col("me_agi")).clip(float(sd["married"]["floor"]) / pl.col("me_sep"), float(sd["married"]["ceiling"]) / pl.col("me_sep"))
            )
    elif effective_year == 1988:
        stded = pl.lit(0.0)
    else:
        # The federal standard deduction, 0 when itemizing federally.
        stded = pl.when(~pl.col("itemizes")).then(pl.col("standard_deduction")).otherwise(0.0)
        if year == 2023:
            # TAXSIM's 2023 aged addition is $100 below the statutory value
            # used by the federal port; Maine reads that federal result.
            stale_aged_adjustment = pl.when(
                pl.col("filing_status").is_in(["single", "head_of_household"])
            ).then(100.0 * aged_count() / flate).otherwise(0.0)
            stded = (stded - stale_aged_adjustment).clip(0, None)
        if p.in_effect("standard_deduction_married_override"):
            gets_override = is_joint | is_sep
            old = p.num("standard_deduction_aged_addition")
            override_val = (
                p.num("standard_deduction_married_override") / pl.col("me_sep")
                + old * aged_count()
            )
            stded = pl.when(gets_override).then(override_val).otherwise(stded)
        if p.in_effect("standard_deduction_single_and_hoh"):
            sh = p.value("standard_deduction_single_and_hoh")
            olds = float(sh["aged"]) * aged_count()
            hoh_val = float(sh["head_of_household"]) + olds
            single_val = float(sh["single"]) + olds
            stded = pl.when(is_hoh).then(hoh_val).when(files_single()).then(single_val).otherwise(stded)

    if p.in_effect("standard_deduction_nonconforming"):
        sd = p.value("standard_deduction_nonconforming")
        base = pl.when(files_single() | is_sep).then(float(sd["single"])).when(is_hoh).then(float(sd["head_of_household"])).otherwise(float(sd["married_joint"]))
        aged_amount = pl.when(files_single() | is_hoh).then(float(sd["aged_single"])).otherwise(float(sd["aged_married"]))
        stded = pl.when(pl.col("itemizes")).then(0.0).otherwise(base + aged_amount * aged_count())
    df = df.with_columns(me_stded=stded)

    # --- Itemized deduction --- (when the federal return itemizes a
    # positive gross total)
    if effective_year <= 1986:
        salt_plus_mortgage, itemizes_local, _ = pre1987_federal_itemizing(effective_year)
        itemized_deduction_local = pl.col("pre1987_deduc")
    else:
        salt_plus_mortgage = pl.col("salt_capped") + pl.col("mortgage")
        itemized_deduction_local = pl.col("itemized_deduction")
        itemizes_local = pl.col("itemizes")

    itemizing_gate = itemizes_local & (salt_plus_mortgage > 0)

    if not p.in_effect("itemized_deduction_cap"):
        xitded = (itemized_deduction_local - pl.col("state_sales_or_income_tax_ded")).clip(0, None)
    else:
        statit = pl.col("state_sales_or_income_tax_ded") - pl.col("state_sales_or_income_tax_ded") * (
            salt_plus_mortgage - itemized_deduction_local
        ) / salt_plus_mortgage.clip(1e-9, None)
        xitd_cap = p.num("itemized_deduction_cap")
        xitded = pl.min_horizontal(xitd_cap, (itemized_deduction_local - statit).clip(0, None))

    xitded = pl.when(itemizing_gate).then(xitded).otherwise(0.0)

    df = df.with_columns(me_xitded=xitded, me_deduc=_max2(stded, xitded))

    if p.in_effect("deduction_phaseout_proportional"):
        dp = p.value("deduction_phaseout_proportional")
        phased_hoh, thrsh_hoh = float(dp["head_of_household"]["phased"]), float(dp["head_of_household"]["threshold"])
        phased_pf, thrsh_pf = float(dp["per_taxpayer"]["phased"]), float(dp["per_taxpayer"]["threshold"])
        phased = pl.when(is_hoh).then(phased_hoh).otherwise(phased_pf * pl.col("me_txp"))
        thrsh = pl.when(is_hoh).then(thrsh_hoh).otherwise(thrsh_pf * pl.col("me_txp"))
        over = pl.col("me_agi") > phased
        df = df.with_columns(
            me_deduc=pl.when(over).then((pl.col("me_deduc") - pl.col("me_deduc") * (pl.col("me_agi") - phased) / thrsh).clip(0, None)).otherwise(pl.col("me_deduc"))
        )
    if p.in_effect("deduction_phaseout_start"):
        phased = by_filing_status(p.value("deduction_phaseout_start")) * p.num("phaseout_index")
        xl4 = by_filing_status(p["deduction_phaseout_range"])
        over = pl.col("me_agi") >= phased
        df = df.with_columns(
            me_deduc=pl.when(over).then(pl.col("me_deduc") * (1.0 - ((pl.col("me_agi") - phased) / xl4).clip(0, 1))).otherwise(pl.col("me_deduc"))
        )

    # --- Exemptions ---
    exemps_count = federal_exemption_count(effective_year)
    if effective_year <= 2012:
        # 1988's extra exemptions for the aged are at that year's $0 amount.
        xmp_amt = p.num("personal_exemption")
        exemp = exemps_count * xmp_amt
    elif effective_year <= 2017:
        exemp = pl.col("personal_exemptions")  # the federal exemption amount
    else:
        xmp_amt = p.num("personal_exemption")
        exemp = xmp_amt * pl.col("me_txp")
        phasex = by_filing_status(p.value("exemption_phaseout_start")) * p.num("phaseout_index")
        rng = float(p["exemption_phaseout_range"])
        over = pl.col("me_agi") > phasex
        exemp = pl.when(over).then(exemp * (1.0 - ((pl.col("me_agi") - phasex) / (rng / pl.col("me_sep"))).clip(0, 1))).otherwise(exemp)
        exemp = pl.when(is_dependent_filer()).then(0.0).otherwise(exemp)

    df = df.with_columns(me_exemp=exemp)
    df = df.with_columns(me_taxinc=(pl.col("me_agi") - pl.col("me_deduc") - pl.col("me_exemp")).clip(0, None))

    # --- Tax ---
    table = p.value("brackets")
    aif = p.num("bracket_inflation")

    tinc = pl.col("me_taxinc") / me_texp
    stat = bracket_tax(tinc / aif, table) * aif
    df = df.with_columns(me_statax=stat * me_texp)

    if p.in_effect("low_income_taxable_income_limit"):
        ceiling = p.num("low_income_taxable_income_limit")
        df = df.with_columns(me_statax=pl.when(pl.col("me_taxinc") <= ceiling).then(0.0).otherwise(pl.col("me_statax")))

    # --- Minimum tax ---
    if effective_year <= 1979:
        comnew28 = pl.col("regular_tax")
    elif effective_year <= 1990:
        comnew28 = pl.col("fiitax")
    else:
        comnew28 = pl.col("regular_tax")
    comnew69 = pl.col("agi") - pl.when(pl.col("itemizes")).then(pl.col("mortgage")).otherwise(0.0)
    amt = pl.col("amt")
    txm = pl.lit(0.0)
    if p.in_effect("minimum_tax_rate"):
        txm = pl.when(amt > 0).then((p.num("minimum_tax_rate") * comnew69 - pl.col("me_statax")).clip(0, None)).otherwise(0.0)
    elif p.in_effect("minimum_tax_share_of_federal_amt"):
        share = p.num("minimum_tax_share_of_federal_amt")
        txm = pl.when(amt > 0).then(share * (amt - comnew28).clip(0, None)).otherwise(0.0)
    df = df.with_columns(me_statax=pl.col("me_statax") + txm)

    # --- Credits --- A share of the federal child care credit, limited by
    # federal tax before credits.
    ccc_for_chcr = pl.col("federal_chcr")
    if effective_year <= 1979:
        comnew52 = pl.col("regular_tax")
    elif effective_year <= 1986:
        comnew52 = pl.col("fiitax")
    else:
        comnew52 = pl.col("regular_tax")
    child_rate = p.num("child_care_credit_rate")
    chcr = child_rate * pl.min_horizontal(ccc_for_chcr, comnew52.clip(0, None))
    refundable_cap = float(p["child_care_credit_refundable_cap"])
    chcrr = pl.min_horizontal(refundable_cap, chcr)
    chcrn = chcr - chcrr

    depcrd = pl.lit(0.0)
    dep_refundable = pl.lit(0.0)
    if p.in_effect("dependent_credit_per_child"):
        depcrd = pl.min_horizontal(p.num("dependent_credit_per_child") * pl.col("dep17"), pl.col("me_statax"))
    elif p.in_effect("dependent_exemption_credit"):
        dep = p.value("dependent_exemption_credit")
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

    eldcr = pl.lit(0.0)
    if p.in_effect("elderly_credit_share"):
        eldcr = p.num("elderly_credit_share") * pl.col("federal_elder")
    credit = chcrn + depcrd + eldcr

    if p.in_effect("credit_per_exemption"):
        credit = credit + p.num("credit_per_exemption") * (taxpayer_count() + pl.col("depx") + aged_count())
    if p.in_effect("rent_or_property_tax_credit"):
        rp = p.value("rent_or_property_tax_credit")
        cap_r, cap_p = float(rp["rent_cap"]), float(rp["property_tax_cap"])
        credit = credit + pl.max_horizontal(pl.lit(0.0), pl.min_horizontal(pl.col("rentpaid"), cap_r), pl.min_horizontal(cap_p, pl.col("proptax")))
    if p.in_effect("deduction_credits"):
        c88 = p.value("deduction_credits")
        pct88 = float(c88["earned_income_pct"])
        floor88 = float(c88["earned_income_floor"])
        std_caps = c88["std_deduction_cap"]
        std_cap = (
            pl.when(files_single()).then(float(std_caps["single"]))
            .when(is_joint).then(float(std_caps["married_joint"]))
            .when(is_hoh).then(float(std_caps["head_of_household"]))
            .otherwise(float(std_caps["married_separate"]))
        )
        credit = credit + (pct88 * pl.col("earned_income")).clip(floor88, std_cap)
        exemp_credit = (
            pl.when(pl.col("filing_status").is_in(["single", "married_separate"])).then(interpolate_table(pl.col("me_agi"), c88["exemption_table"]["single"]))
            .when(is_hoh).then(interpolate_table(pl.col("me_agi"), c88["exemption_table"]["head_of_household"]))
            .otherwise(interpolate_table(pl.col("me_agi"), c88["exemption_table"]["married"]))
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
            if p.in_effect("property_tax_credit_reduction"):
                red = p.value("property_tax_credit_reduction")
                reduction = red["rate"] * (hy - red["income_floor"]).clip(0, None)
                pcred = (ptax - reduction).clip(0, cap)
            else:
                pcred = ptax.clip(0, cap)
            pcred = pl.when(hy <= limit).then(pcred * aged_any).otherwise(0.0)
        else:
            tiers = p.value("property_tax_credit_tiers")
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
    if p.in_effect("eitc_rate"):
        rate_eitc = p.num("eitc_rate")
        earncr = rate_eitc * pl.col("eitc")
        if p.in_effect("eitc_rate_childless"):
            # On the federal credit before its minimum-age test.
            federal = pl.col("eitc_before_age_test")
            earncr = pl.when(pl.col("dep18") < 1).then(p.num("eitc_rate_childless") * federal).otherwise(rate_eitc * federal)
    if p.in_effect("eitc_rates_by_children"):
        # Childless filers from age 18 (not the federal 25).
        rates = p.value("eitc_rates_by_children")
        older = pl.max_horizontal(pl.col("page"), pl.col("sage"))
        childless_ok = (older == 0) | (older >= 18)
        earncr = pl.when(pl.col("dep18") > 0).then(float(rates["with_child"]) * pl.col("eitc")).when(childless_ok).then(
            float(rates["childless"]) * pl.col("eitc_before_age_test")
        ).otherwise(0.0)
    df = df.with_columns(me_earncr=earncr)
    if p.in_effect("eitc_rate") and not p.value("eitc_refundable"):
        df = df.with_columns(me_statax=(pl.col("me_statax") - pl.col("me_earncr")).clip(0, None))

    # --- Property tax fairness credit (2013+, refundable) ---
    # Federal total income plus Social Security benefits.
    ti = (pl.col("me_total_income") + pl.col("gssi")).clip(0, None)
    aged_any = aged_count() > 0
    # The federal exemption count is deflated in projected years before
    # truncation, which can zero it.
    nexem = (exemps_count / flate).floor()
    ptfc = pl.lit(0.0)
    if p.in_effect("property_tax_fairness_credit"):
        rent_share = p.num("ptfc_rent_share")
        pf = p.value("property_tax_fairness_credit")
        if "income_ceiling" in pf:
            ceiling = float(pf["income_ceiling"])
            agix = pl.col("me_agi").clip(0, None)
            bagix = pf["income_share"] * agix
            base = pl.col("proptax") + rent_share * pl.col("rentpaid")
            cap = pl.when(aged_any).then(float(pf["elderly_cap"])).otherwise(float(pf["cap"]))
            eligible = (nexem > 0) & (agix <= ceiling) & (base > bagix)
            ptfc = pl.when(eligible).then(pl.min_horizontal(cap / pl.col("me_sep"), pf["benefit_share"] * (base - bagix))).otherwise(0.0)
        else:
            base_raw = pl.col("proptax") + rent_share * pl.col("rentpaid")
            base_cap, income_cap = pf["base_cap"], pf["income_cap"]
            # Through 2017 the caps for two or more exemptions are halved on
            # separate returns.
            div = pl.col("me_sep") if effective_year <= 2017 else 1.0
            is_single = files_single()
            base = pl.when(is_single).then(pl.min_horizontal(float(base_cap["single"]), base_raw)).when(nexem <= 2).then(pl.min_horizontal(float(base_cap["two"]) / div, base_raw)).otherwise(pl.min_horizontal(float(base_cap["more"]) / div, base_raw))
            single_income_cap = float(income_cap["single"]) / div if effective_year <= 2017 else float(income_cap["single"])
            tix = pl.when(is_single).then(pl.min_horizontal(single_income_cap, ti)).when(nexem <= 2).then(pl.min_horizontal(float(income_cap["two"]) / div, ti)).otherwise(pl.min_horizontal(float(income_cap["more"]) / div, ti))
            cap_out = pl.when(aged_any).then(float(pf["elderly_cap"])).otherwise(float(pf["cap"]))
            eligible = nexem > 0
            excess = (base - pf["income_share"] * tix).clip(0, None)
            if "benefit_share" in pf:
                ptfc = pl.when(eligible).then(pl.min_horizontal(cap_out / pl.col("me_sep"), pf["benefit_share"] * excess)).otherwise(0.0)
            else:
                ptfc = pl.when(eligible).then(pl.min_horizontal(cap_out, excess)).otherwise(0.0)
    if effective_year >= 2017:
        ptfc = pl.when(is_sep).then(0.0).otherwise(ptfc)
    if p.in_effect("property_tax_fairness_credit_schedule"):
        d = p.value("property_tax_fairness_credit_schedule")
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
    if p.in_effect("sales_tax_fairness_credit") or p.in_effect("sales_tax_fairness_credit_schedule"):
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
    if p.in_effect("sales_tax_fairness_credit"):
        sc = p.value("sales_tax_fairness_credit")
        nexem_capped = nexem.clip(0, 4)
        threshold = (
            pl.when(files_single()).then(float(sc["threshold"]["single"]))
            .when(is_hoh).then(float(sc["threshold"]["head_of_household"]))
            .otherwise(float(sc["threshold"]["married_joint"]))
        )
        credit_amt = pl.lit(0.0)
        for n in (1, 2, 3, 4):
            credit_amt = pl.when(nexem_capped == n).then(float(sc["amount"][n - 1])).otherwise(credit_amt)
        stfc = pl.when((nexem > 0) & nmst_eligible).then(
            (credit_amt - ((tis - threshold) / p["stfc_phaseout_step"]).clip(0, None)).clip(0, None)
        ).otherwise(0.0)
    elif p.in_effect("sales_tax_fairness_credit_schedule"):
        d = p.value("sales_tax_fairness_credit_schedule")
        children = pl.col("dep17")
        extra_joint = pl.lit(0.0)
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
    if p.in_effect("eitc_rate") and p.value("eitc_refundable"):
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
