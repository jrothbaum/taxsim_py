"""Maryland individual income tax calculator."""

import polars as pl

from taxsim_py.calculators.federal_pre1987 import PRE1987_PARAMS
from taxsim_py.engine.brackets import bracket_rate, bracket_tax
from taxsim_py.engine.inputs import aged_count, federal_exemption_count, is_dependent_filer, taxpayer_count
from taxsim_py.engine.schema import PARAMETERS_ROOT, load_yaml, resolve_year
from taxsim_py.engine.state import (
    by_filing_status as _by_status,
    pre1987_federal_itemizing,
    with_default as _with_default,
    with_defaults,
    with_state_detail,
)
from taxsim_py.engine.state_extrapolation import deflate_for_extrapolation, resolve_state_year

MD_PARAMS = load_yaml(PARAMETERS_ROOT / "states" / "md" / "income_tax.yaml")
_STATUSES = ["single", "married_joint", "married_separate", "head_of_household"]


def _tiered_replace(income: pl.Expr, tiers: list[list[float]]) -> pl.Expr:
    """Select a replacement value from an income tier."""
    expr = pl.lit(None, dtype=pl.Float64)
    for lo, hi, value in tiers:
        cond = (income > lo) & (income <= hi)
        expr = pl.when(cond).then(pl.lit(float(value))).otherwise(expr)
    return expr


def compute_md_tax(df: pl.DataFrame, year: int) -> pl.DataFrame:
    effective_year, flate = resolve_state_year(year)
    p = MD_PARAMS
    df = with_defaults(df, ("federal_chcr", "proptax", "otheritem", "mortgage", "dividends", "intrec", "depx", "dep17", "dep18", "childcare"))
    df = _with_default(df, "eitc")
    df = _with_default(df, "ccc")
    df = _with_default(df, "itemized_deduction")
    df = _with_default(df, "salt_capped")
    df = _with_default(df, "state_sales_or_income_tax_ded")
    df = _with_default(df, "itemizes", False)
    df = _with_default(df, "fiitax")
    df = _with_default(df, "regular_tax")
    df = _with_default(df, "credit")
    df = _with_default(df, "earned_income")
    df = with_defaults(df, ("pensions", "gssi", "taxable_social_security", "charity_cash"))

    is_joint = pl.col("filing_status") == "married_joint"
    is_hoh = pl.col("filing_status") == "head_of_household"
    is_sep = pl.col("filing_status") == "married_separate"
    is_single = pl.col("filing_status") == "single"
    is_single_or_separate = is_single | is_sep
    df = df.with_columns(
        md_sep=pl.when(is_sep).then(2.0).otherwise(1.0),
        md_txp=taxpayer_count(),
    )
    df = df.with_columns(md_txpded=pl.col("md_txp") + pl.when(is_hoh).then(1.0).otherwise(0.0))

    df = deflate_for_extrapolation(
        df,
        flate,
        [
            "federal_chcr",
            "pwages", "swages", "proptax", "otheritem", "mortgage", "dividends", "intrec",
            "stcg", "ltcg", "ui", "pui", "sui", "agi", "earned_income", "eitc", "ccc",
            "itemized_deduction", "salt_capped", "state_sales_or_income_tax_ded", "fiitax",
            "regular_tax", "childcare", "pensions", "gssi", "taxable_social_security",
        ],
    )

    # --- AGI ---
    chnum = pl.col("depx").clip(0, 2)
    if effective_year <= 1977:
        chexp = pl.lit(0.0)
    elif effective_year <= 2002:
        chexp = pl.min_horizontal(2400.0 * chnum, pl.col("childcare"))
    else:
        chexp = pl.min_horizontal(3000.0 * chnum, pl.col("childcare"))
    # Pension exclusion per taxpayer 65 or older, less Social Security benefits.
    pex = float(resolve_year(p["pension_exclusion"], effective_year))
    penexc = pl.col("pensions").clip(0, (pex * aged_count() - pl.col("gssi")).clip(0, None))
    md_agi = pl.col("agi") - chexp - penexc
    if effective_year >= 1985:
        md_agi = md_agi - pl.col("taxable_social_security")

    if effective_year <= 1986:
        excl_table = PRE1987_PARAMS["dividend_exclusion"]
        excl_cap = _by_status({s: resolve_year(excl_table[s], effective_year) for s in _STATUSES})
        divexc_base = pl.col("dividends") + pl.col("intrec") if effective_year == 1981 else pl.col("dividends")
        divexc = pl.min_horizontal(divexc_base, excl_cap)
        md_agi = md_agi + divexc
    if effective_year <= 1986:
        # Half of federal preference income (`comnew(36)`) over $10,000 per taxpayer.
        pref = pl.col("pre1987_pref")
        md_agi = md_agi + 0.5 * (pref - 10000.0 * pl.col("md_txp")).clip(0, None)
    if effective_year == 1980:
        md_agi = md_agi - pl.min_horizontal(pl.col("intrec"), 200.0 * pl.col("md_txp")).clip(0, None)
    if 1982 <= effective_year <= 1986:
        md_agi = md_agi + pl.col("pre1987_twoded")

    df = df.with_columns(md_agi=md_agi)

    # --- Maryland's own two-earner subtraction (1992+, joint returns only) ---
    wages_total = pl.col("pwages") + pl.col("swages")
    agih = pl.col("pwages") + 0.5 * (pl.col("agi") - wages_total)
    agiw = pl.col("swages") + 0.5 * (pl.col("agi") - wages_total)
    # Exclusions are split evenly, except that one aged spouse's pension
    # exclusion comes from the spouse's share.
    ss_subtracted = pl.col("taxable_social_security") if effective_year >= 1985 else pl.lit(0.0)
    shared = chexp + ss_subtracted
    one_aged_pension = (aged_count() == 1) & (penexc > 0)
    twoh = pl.when(one_aged_pension).then(agih - 0.5 * shared).otherwise(agih - 0.5 * (shared + penexc)).clip(0, None)
    twow = pl.when(one_aged_pension).then(agiw - 0.5 * shared - penexc).otherwise(agiw - 0.5 * (shared + penexc)).clip(0, None)
    if effective_year <= 1991:
        twoear = pl.lit(0.0)
    elif effective_year <= 1994:
        twoear = pl.when(pl.col("agi") <= 150000.0).then(1200.0).otherwise(1000.0)
        twoear = pl.min_horizontal(twoear, twoh, twow)
    else:
        flat = 1154.0 if effective_year == 1998 else 1200.0
        twoear = pl.min_horizontal(pl.lit(flat), twoh, twow)
    two_earner_eligible = is_joint & (pl.col("agi") > 0) & (agih * agiw > 0)
    twoear = pl.when(two_earner_eligible).then(twoear).otherwise(0.0)
    df = df.with_columns(md_agi=(pl.col("md_agi") - twoear).clip(0, None))

    # --- Capital gains (discontinued 1992) ---
    capgn = pl.col("stcg") + pl.col("ltcg")
    if 1987 <= effective_year <= 1990:
        md_agi_cg = pl.when(capgn > 0).then(pl.col("md_agi") - 0.4 * capgn).otherwise(pl.col("md_agi"))
        df = df.with_columns(md_agi=md_agi_cg)
    elif effective_year == 1991:
        astep = pl.when(is_joint).then(pl.min_horizontal(0.3 * capgn, 15000.0)).otherwise(pl.min_horizontal(0.3 * capgn, 7500.0))
        bstep = pl.when(is_joint).then(((pl.col("agi") - capgn) - 100000.0).clip(0, None) * 0.5).otherwise(((pl.col("agi") - capgn) - 50000.0).clip(0, None) * 0.5)
        capded = (astep - bstep).clip(0, None)
        df = df.with_columns(md_agi=pl.when(capgn > 0).then(pl.col("md_agi") - capded).otherwise(pl.col("md_agi")))

    # --- Standard deduction ---
    agi_pos = pl.col("md_agi").clip(0, None)
    if effective_year <= 1978:
        stded = pl.min_horizontal(pl.col("md_txpded") * 500.0, 0.1 * agi_pos)
    elif effective_year <= 1986:
        stded = pl.min_horizontal(pl.col("md_txpded") * 1500.0, 0.13 * agi_pos)
    elif effective_year <= 1989:
        floor_mult = float(p["standard_deduction_floor_multiplier_1987_1989"][1960])
        ceil_mult = float(p["standard_deduction_ceiling_multiplier_1987_1989"][1960])
        stded = (0.15 * pl.col("md_agi")).clip(pl.col("md_txpded") * floor_mult, pl.col("md_txpded") * ceil_mult)
        stded = stded + float(p["standard_deduction_aged_addition_1987_1989"]) * aged_count()
    elif effective_year <= 2017:
        floor_mult = float(p["standard_deduction_floor_multiplier_1990_2017"][1960])
        ceil_mult = float(p["standard_deduction_ceiling_multiplier_1990_2017"][1960])
        stded = (0.15 * pl.col("md_agi")).clip(pl.col("md_txpded") * floor_mult, pl.col("md_txpded") * ceil_mult)
    elif effective_year == 2018:
        floor_mult = float(p["standard_deduction_floor_multiplier_2018"][1960])
        ceil_mult = float(p["standard_deduction_ceiling_multiplier_2018"][1960])
        stded = (0.15 * pl.col("md_agi")).clip(pl.col("md_txpded") * floor_mult, pl.col("md_txpded") * ceil_mult)
    else:
        floor_ss = float(resolve_year(p["standard_deduction_single_separate_floor_2019_2021"], effective_year))
        ceil_ss = float(resolve_year(p["standard_deduction_single_separate_ceiling_2019_2021"], effective_year))
        floor_jh = float(resolve_year(p["standard_deduction_joint_hoh_floor_2019_2021"], effective_year))
        ceil_jh = float(resolve_year(p["standard_deduction_joint_hoh_ceiling_2019_2021"], effective_year))
        stded = pl.when(is_joint | is_hoh).then((0.15 * pl.col("md_agi")).clip(floor_jh, ceil_jh)).otherwise(
            (0.15 * pl.col("md_agi")).clip(floor_ss, ceil_ss)
        )

    df = df.with_columns(md_stded=stded)

    # --- Itemized deduction ---
    if effective_year <= 1986:
        gross, _, zbr = pre1987_federal_itemizing(effective_year)
        xitded = pl.max_horizontal(gross - pl.col("state_sales_or_income_tax_ded"), zbr)
    else:
        salt_plus_mortgage = pl.col("salt_capped") + pl.col("mortgage")
        itemizing_gate = pl.col("itemizes") & (salt_plus_mortgage > 0)
        xitded_base = (salt_plus_mortgage - pl.col("state_sales_or_income_tax_ded")).clip(0, None)
        if 1991 <= effective_year <= 2017:
            if effective_year <= 2012:
                aif92 = (
                    float(resolve_year(p["itemized_phaseout_inflation_factor_1992_2012"], effective_year))
                    if effective_year >= 1992
                    else 1.0
                )
                phas92 = 100000.0 / pl.col("md_sep") * aif92
            else:
                aif13 = float(resolve_year(p["itemized_phaseout_inflation_factor_2013_2017"], effective_year))
                mult = _by_status(p["itemized_phaseout_status_multiplier_2013_2017"])
                phas92 = aif13 * 250000.0 * mult
            xitded_high = (pl.col("itemized_deduction") - pl.col("state_sales_or_income_tax_ded") * pl.col("itemized_deduction") / salt_plus_mortgage.clip(1e-9, None)).clip(0, None)
            xitded_base = pl.when((pl.col("md_agi") > phas92) & (salt_plus_mortgage > 0)).then(xitded_high).otherwise(xitded_base)
        xitded = pl.when(itemizing_gate).then(xitded_base).otherwise(0.0)

    df = df.with_columns(
        md_xitded=xitded,
        md_deduc=pl.when(xitded >= pl.col("md_stded")).then(xitded).otherwise(pl.col("md_stded")),
    )

    # --- Exemption ---
    exemps_count = federal_exemption_count(effective_year)
    if effective_year <= 1989:
        xmp_amt = float(resolve_year(p["personal_exemption_amount"], effective_year))
        exemp = exemps_count * xmp_amt
    else:
        xmp_amt = float(resolve_year(p["personal_exemption_amount"], effective_year))
        blage = float(p["aged_exemption"]) * aged_count()
        base_exemp = pl.col("md_txp") + pl.col("depx")
        exemp = blage + base_exemp * xmp_amt
        fed_agi = pl.col("agi")
        if 2008 <= effective_year <= 2011:
            ss_tiers = p["exemption_phaseout_2008_2011_single_separate"]
            jh_tiers = p["exemption_phaseout_2008_2011_joint_hoh"]
            replaced = pl.when(is_single_or_separate).then(_tiered_replace(fed_agi, ss_tiers)).otherwise(_tiered_replace(fed_agi, jh_tiers))
            exemp = pl.when((fed_agi > 100000.0) & replaced.is_not_null()).then(blage + base_exemp * replaced).otherwise(exemp)
        elif effective_year >= 2012:
            ss_tiers = p["exemption_phaseout_2012plus_single_separate"]
            jh_tiers = p["exemption_phaseout_2012plus_joint_hoh"]
            replaced = pl.when(is_single_or_separate).then(_tiered_replace(fed_agi, ss_tiers)).otherwise(_tiered_replace(fed_agi, jh_tiers))
            exemp = pl.when((fed_agi > 100000.0) & replaced.is_not_null()).then(blage + base_exemp * replaced).otherwise(exemp)
        # A dependent filer gets no exemptions from 2008; the high-income
        # reductions apply only to other returns.
        if effective_year >= 2008:
            exemp = pl.when(is_dependent_filer()).then(0.0).otherwise(exemp)

    df = df.with_columns(md_exemp=exemp)
    df = df.with_columns(md_taxinc=(pl.col("md_agi") - pl.col("md_deduc") - pl.col("md_exemp")).clip(0, None))

    # --- Bracket tax --- (single/married_separate get a compressed
    # top-bracket schedule from 1992 on - see module docstring point 2)
    if effective_year <= 1991:
        table = p["brackets_1977_1991"]
        statax = bracket_tax(pl.col("md_taxinc"), table)
        rate_expr = bracket_rate(pl.col("md_taxinc"), table)
    elif effective_year <= 2007:
        if effective_year <= 1994:
            table_ss = p["brackets_1992_1994_single_separate"]
            table_jh = p["brackets_1992_1994_joint_hoh"]
        else:
            top_rate = float(resolve_year(p["brackets_1995_2007_top_rate"], effective_year))
            table_ss = table_jh = [[0, 0.02], [1000, 0.03], [2000, 0.04], [3000, top_rate]]
        statax = pl.when(is_single_or_separate).then(bracket_tax(pl.col("md_taxinc"), table_ss)).otherwise(
            bracket_tax(pl.col("md_taxinc"), table_jh)
        )
        rate_expr = pl.when(is_single_or_separate).then(
            bracket_rate(pl.col("md_taxinc"), table_ss)
        ).otherwise(bracket_rate(pl.col("md_taxinc"), table_jh))
    elif effective_year <= 2011:
        statax = pl.when(is_single_or_separate).then(
            bracket_tax(pl.col("md_taxinc"), p["brackets_2008_2011_single_separate"])
        ).otherwise(bracket_tax(pl.col("md_taxinc"), p["brackets_2008_2011_joint_hoh"]))
        rate_expr = pl.when(is_single_or_separate).then(
            bracket_rate(pl.col("md_taxinc"), p["brackets_2008_2011_single_separate"])
        ).otherwise(bracket_rate(pl.col("md_taxinc"), p["brackets_2008_2011_joint_hoh"]))
    else:
        statax = pl.when(is_single_or_separate).then(
            bracket_tax(pl.col("md_taxinc"), p["brackets_2012plus_single_separate"])
        ).otherwise(bracket_tax(pl.col("md_taxinc"), p["brackets_2012plus_joint_hoh"]))
        rate_expr = pl.when(is_single_or_separate).then(
            bracket_rate(pl.col("md_taxinc"), p["brackets_2012plus_single_separate"])
        ).otherwise(bracket_rate(pl.col("md_taxinc"), p["brackets_2012plus_joint_hoh"]))

    df = df.with_columns(md_taxbc=statax)

    # --- Credits ---
    earncr = pl.lit(0.0)
    if effective_year >= 1987:
        earncr = 0.5 * pl.col("eitc")
    earncr = pl.min_horizontal(pl.col("md_taxbc"), earncr)
    df = df.with_columns(md_pretax=(pl.col("md_taxbc") - earncr).clip(0, None))
    df = df.with_columns(md_statax=pl.col("md_pretax"))

    # Refundable EITC.
    refcr = pl.lit(0.0)
    if effective_year >= 1998:
        eicr = float(resolve_year(p["refundable_eitc_rate"], effective_year))
        base_refcr = (eicr * pl.col("eitc") - pl.col("md_taxbc")).clip(0, None)
        if effective_year <= 2008:
            refcr = pl.when(pl.col("depx") > 0).then(base_refcr).otherwise(0.0)
        elif effective_year <= 2019:
            refcr = base_refcr
        else:
            refcr = pl.when(pl.col("depx") > 0).then(base_refcr).otherwise(0.0)
            childless_floor = float(p["refundable_eitc_childless_floor_2020plus"][1960])
            wl2 = pl.min_horizontal(childless_floor, pl.col("eitc"))
            childless_gate = (pl.col("md_pretax") < 1.0) & (pl.col("depx") < 1.0)
            childless_refcr = pl.when(pl.col("md_taxbc") < wl2).then(wl2 - pl.col("md_taxbc")).otherwise(0.0)
            refcr = pl.when(childless_gate).then(childless_refcr).otherwise(refcr)

    # Nonrefundable Child/Dependent Care Credit.
    if effective_year == 2000:
        chr_ = 0.001 * (250.0 - ((pl.col("agi") - 30000.0 / pl.col("md_sep")) / (40.0 / pl.col("md_sep"))).clip(0, None)).clip(0, None)
    elif 2001 <= effective_year <= 2018:
        chr_ = 0.0001 * (3250.0 - ((pl.col("agi") - 41000.0 / pl.col("md_sep")) / (1000.0 / (325.0 * pl.col("md_sep")))).clip(0, None)).clip(0, None)
    elif effective_year >= 2019:
        chr_joint = 0.01 * (32.0 - ((pl.col("agi") - 50000.0) / 3000.0).clip(0, None)).clip(0, None)
        chr_other = 0.01 * (32.0 - ((pl.col("agi") - 30000.0) / 2000.0).clip(0, None)).clip(0, None)
        chr_ = pl.when(is_joint).then(chr_joint).otherwise(chr_other)
        fagim = pl.when(is_joint).then(75000.0).otherwise(50000.0)
    else:
        chr_ = pl.lit(0.0)

    ccc_cap = pl.col("federal_chcr")
    if effective_year <= 1979:
        comnew52 = pl.col("regular_tax")
    elif effective_year <= 1986:
        comnew52 = pl.col("fiitax")
    else:
        comnew52 = pl.col("regular_tax")
    chcr = chr_ * pl.min_horizontal(ccc_cap, comnew52.clip(0, None))
    df = df.with_columns(md_statax=(pl.col("md_statax") - chcr).clip(0, None))

    # Poverty Level Credit (1997+, nonrefundable).
    if effective_year >= 1997:
        # `comnew(68)` is deflated in projected years.
        exemps_count = exemps_count / flate
        pov1 = float(resolve_year(p["poverty_credit_income_base"], effective_year))
        pov2 = float(resolve_year(p["poverty_credit_income_per_additional_exemption"], effective_year))
        xlin3 = pov1 + pov2 * (exemps_count - 1.0)
        xlin4 = pl.max_horizontal(pl.col("agi"), pl.col("earned_income"))
        ptcr = pl.when((xlin3 >= xlin4) & ~is_dependent_filer()).then(0.05 * pl.col("earned_income")).otherwise(0.0)
    else:
        ptcr = pl.lit(0.0)
    df = df.with_columns(md_statax=(pl.col("md_statax") - ptcr).clip(0, None))

    df = df.with_columns(md_statax=pl.col("md_statax") - refcr)

    # 2019+ refundable portion of the Child/Dependent Care Credit.
    chref = pl.lit(0.0)
    if effective_year >= 2019:
        chref = pl.when(pl.col("agi") <= fagim).then((chcr - pl.col("md_taxbc")).clip(0, None)).otherwise(0.0)
        df = df.with_columns(md_statax=pl.col("md_statax") - chref)

    df = df.with_columns(siitax=pl.col("md_statax") * flate)
    return with_state_detail(
        df,
        agi=pl.col("md_agi"),
        exemptions=pl.col("md_exemp"),
        standard_deduction=pl.col("md_stded"),
        itemized_deductions=pl.col("md_xitded"),
        taxable_income=pl.col("md_taxinc"),
        child_care_credit=chcr,
        eic=earncr + refcr,
        credits=earncr + ptcr + refcr + chcr + chref,
        rate=rate_expr,
    )
