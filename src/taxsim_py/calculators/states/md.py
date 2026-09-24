"""Maryland individual income tax (`mdtax`, taxsim_2024_09_21.f:7893-8281,
state id 21). See parameters/states/md/income_tax.yaml for the full scope
note (confirmed-inert elderly/pension/political-contribution/tax-
preference fields).

Harness: 2,855/2,867 (99.6%); the 12 residuals are all small 2023-only
cases in the standing real-vs-oracle EITC-table divergence family.

The densest state built this session by real mechanism count (unlike
Maine, where much of the length came from confirmed-inert provisions,
almost everything here is a real, active formula). Built by reading the
full 388-line source directly. Real, non-obvious mechanics:
0. `comnew(N)` mirrors the federal `/newshr/` common block
   (taxsim_2024_09_21.f:23350) slot for slot - the key that decoded
   `comnew(36)` as federal PREFERENCE income (see the AGI section) and
   also pins (4)=divall, (5)=fullcg, (6)=capgn, (7)=capded,
   (25)=polcon, (32)=twoded, (37)=earned. The 1977-1978 preference
   figure includes WAGES but only for federal itemizers, and a pure-wage
   filer becomes a federal itemizer once their own Maryland tax (fed back
   as a SALT deduction) exceeds the federal standard deduction.
1. `divexc()` (the federal dividend/interest exclusion swap other states
   only ever call for 1981) is called here for EVERY year<=1986 -
   generalized to `min(dividends[+intrec if 1981], dividend_exclusion
   table[status,year])`, reusing the same per-status table (already
   covering 1977-1986, not just 1981) every other pre-1987 state build
   already established.
2. Single/married_separate filers get a COMPRESSED top-bracket schedule
   from 2008 on - genuinely lower dollar thresholds for the SAME
   marginal rates as joint/head_of_household filers, not a transcription
   quirk.
3. TWO distinct two-earner-couple provisions coexist: the pre-1987
   federal `comnew(32)` addback (1982-1986, reused from DC/Colorado/
   Georgia's own established reconstruction) and Maryland's OWN, much
   later two-earner SUBTRACTION (1992+, using an explicit husband/wife
   AGI split - `agih`/`agiw` - the same mechanic Iowa's own build
   already established, just with Maryland's own dollar caps).
4. The 2008+ exemption phaseout REPLACES the per-exemption dollar amount
   outright by federal-AGI bracket (not a percentage reduction) - and
   the bracket boundaries themselves shift between the 2008-2011 and
   2012+ eras.
5. The refundable Earned Income Credit is gated differently by year:
   1998-2008 and 2020+ require having a qualifying dependent; 2009-2019
   is available to childless filers too; 2020+ ALSO has a separate,
   smaller flat-floor provision specifically for childless filers whose
   nonrefundable credit already zeroed out their tax.
6. The 2019-2021 standard-deduction floor/ceiling DATA statements fill in
   strict Fortran column-major order for a `(2,2019:2021)` array, not the
   "three floors, then three ceilings" grouping their continuation-line
   layout suggests (2019 joint/HoH: floor $3,050, ceiling $3,100).

Probing caveat found here: the oracle executable can give a DIFFERENT
answer for the first row of an input file than for the identical row
placed after any other row (a $200k-wages/$100k-LTCG 1983 case gave
$14,785 alone but $13,035 as row 2). The batched harness values are the
consistent ones - live probes should always put a throwaway row first.
"""

import polars as pl

from taxsim_py.calculators.federal_pre1987 import PRE1987_PARAMS
from taxsim_py.engine.brackets import bracket_tax
from taxsim_py.engine.credits import child_care_credit_rate_pre2021
from taxsim_py.engine.schema import PARAMETERS_ROOT, load_yaml, resolve_year
from taxsim_py.engine.state_extrapolation import deflate_for_extrapolation, resolve_state_year

MD_PARAMS = load_yaml(PARAMETERS_ROOT / "states" / "md" / "income_tax.yaml")
FEDERAL_CREDITS_PARAMS = load_yaml(PARAMETERS_ROOT / "national" / "credits.yaml")
_STATUSES = ["single", "married_joint", "married_separate", "head_of_household"]


def _with_default(df: pl.DataFrame, column: str, default: float = 0.0) -> pl.DataFrame:
    if column in df.columns:
        return df
    return df.with_columns(pl.lit(default).alias(column))


def _by_status(values: dict) -> pl.Expr:
    expr = pl.lit(None, dtype=pl.Float64)
    for status, v in values.items():
        expr = pl.when(pl.col("filing_status") == status).then(pl.lit(float(v))).otherwise(expr)
    return expr


def _tiered_replace(income: pl.Expr, tiers: list[list[float]]) -> pl.Expr:
    """`>lo and <=hi -> value` bracket-replacement (not additive) - the
    2008+ exemption phaseout REPLACES the per-exemption dollar amount by
    federal-AGI bracket rather than reducing it proportionally."""
    expr = pl.lit(None, dtype=pl.Float64)
    for lo, hi, value in tiers:
        cond = (income > lo) & (income <= hi)
        expr = pl.when(cond).then(pl.lit(float(value))).otherwise(expr)
    return expr


def _raw_ccc_pre1987(df: pl.DataFrame, year: int) -> pl.Expr:
    """`comnew(53)` for years<=1986 - the raw federal Child Care Credit
    amount before combining with the unrelated federal General Tax
    Credit and before the nonrefundable cap, matching Maine's own
    already-established reconstruction (`federal_pre1987.py`'s own
    exposed `credit` column is the COMBINED, capped total instead)."""
    expense_cap = float(resolve_year(PRE1987_PARAMS["child_care_credit_expense_cap"], year))
    chmax = expense_cap * pl.col("dep13").clip(0, 2)
    chwage = (pl.col("pwages") + pl.col("swages")).clip(0, None)
    child_expense = pl.min_horizontal(chmax, chwage, pl.col("childcare"))
    if year <= 1981:
        rate = float(resolve_year(PRE1987_PARAMS["child_care_credit_flat_rate"], year))
        ccc_rate = pl.lit(rate)
    else:
        ccc_rate = (0.30 - ((pl.col("agi") - 8000.0) / 200000.0).clip(0, None)).clip(0.20, None)
    return child_expense * ccc_rate


def _raw_ccc_1987_1997(df: pl.DataFrame, year: int) -> pl.Expr:
    """`comnew(53)` for 1987-1997 - federal.py's own `ccc` column is
    deliberately $0 for years<1998 (a real, separately-documented
    federal-side quirk about the credit-stacking mechanism), reused from
    the same Kansas/Kentucky/Louisiana/Maine reconstruction."""
    ccc_p = FEDERAL_CREDITS_PARAMS["child_care_credit"]
    max_qualifying_persons = float(resolve_year(ccc_p["max_qualifying_persons"], year))
    max_expense_per_person = float(resolve_year(ccc_p["max_expense_per_person_pre2021"], year))
    ccc_rate = child_care_credit_rate_pre2021(
        pl.col("agi"),
        phase_start=float(resolve_year(ccc_p["pre2021_phase_start"], year)),
        top_rate=float(resolve_year(ccc_p["pre2021_rate_top"], year)),
        floor_rate=float(resolve_year(ccc_p["pre2021_rate_floor"], year)),
        step_amount=float(resolve_year(ccc_p["pre2021_step_amount"], year)),
    )
    num_qualifying_persons = pl.col("dep13").clip(0, max_qualifying_persons)
    qualifying_expense = pl.col("childcare").clip(0, num_qualifying_persons * max_expense_per_person)
    ccc_earned_income_cap = pl.when(pl.col("filing_status") == "married_joint").then(
        pl.min_horizontal(pl.col("pwages"), pl.col("swages"))
    ).otherwise(pl.col("pwages") + pl.col("swages"))
    ccc_expense = pl.min_horizontal(qualifying_expense, ccc_earned_income_cap).clip(0, None)
    return ccc_rate * ccc_expense


def compute_md_tax(df: pl.DataFrame, year: int, force_itemize: bool | None = None) -> pl.DataFrame:
    effective_year, flate = resolve_state_year(year)
    p = MD_PARAMS
    for col in ("proptax", "otheritem", "mortgage", "dividends", "intrec", "depx", "dep17", "dep18", "childcare"):
        df = _with_default(df, col)
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

    is_joint = pl.col("filing_status") == "married_joint"
    is_hoh = pl.col("filing_status") == "head_of_household"
    is_sep = pl.col("filing_status") == "married_separate"
    is_single = pl.col("filing_status") == "single"
    is_single_or_separate = is_single | is_sep
    df = df.with_columns(
        md_sep=pl.when(is_sep).then(2.0).otherwise(1.0),
        # `txp`/`data(7)` - real ONLY for married_joint (2), matching
        # the SAME finding Maine's own build already established.
        md_txp=pl.when(is_joint).then(2.0).otherwise(1.0),
    )
    df = df.with_columns(md_txpded=pl.col("md_txp") + pl.when(is_hoh).then(1.0).otherwise(0.0))

    df = deflate_for_extrapolation(
        df,
        flate,
        [
            "pwages", "swages", "proptax", "otheritem", "mortgage", "dividends", "intrec",
            "stcg", "ltcg", "ui", "pui", "sui", "agi", "earned_income", "eitc", "ccc",
            "itemized_deduction", "salt_capped", "state_sales_or_income_tax_ded", "fiitax",
            "regular_tax", "childcare",
        ],
    )

    # --- AGI --- (state-refund/pension/SS/xjobs/political-contribution/
    # tax-preference terms all confirmed inert - see module docstring)
    chnum = pl.col("depx").clip(0, 2)
    if effective_year <= 1977:
        chexp = pl.lit(0.0)
    elif effective_year <= 2002:
        chexp = pl.min_horizontal(2400.0 * chnum, pl.col("childcare"))
    else:
        chexp = pl.min_horizontal(3000.0 * chnum, pl.col("childcare"))
    md_agi = pl.col("agi") - chexp

    if effective_year <= 1986:
        excl_table = PRE1987_PARAMS["dividend_exclusion"]
        excl_cap = _by_status({s: resolve_year(excl_table[s], effective_year) for s in _STATUSES})
        divexc_base = pl.col("dividends") + pl.col("intrec") if effective_year == 1981 else pl.col("dividends")
        divexc = pl.min_horizontal(divexc_base, excl_cap)
        md_agi = md_agi + divexc
    if effective_year <= 1986:
        # `comnew(36)` is federal PREFERENCE INCOME (`pref`, the 36th
        # slot of the federal `/newshr/` common block that `comnew`
        # mirrors - which also pins comnew(5)=fullcg, (6)=capgn,
        # (7)=capded, (25)=polcon, (32)=twoded, (37)=earned).
        # taxsim_2024_09_21.f:23734-23749 and 24111:
        # - 1977-1978: only for federal itemizers - capital-gains
        #   deduction + UI + WAGES + an excess-itemized-deductions
        #   preference. A pure-wage filer starts itemizing federally once
        #   their own Maryland tax (a federal SALT deduction via the
        #   fed<->state loop) exceeds the federal standard deduction,
        #   which is why this looks like a sharp wage threshold (~$48,200
        #   single in 1977) from the outside.
        # - 1979-1982: `data(164)`, never populated -> $0.
        # - 1983-1986: the minimum-tax block overwrites it with the
        #   capital-gains deduction (the excluded share of net LTCG).
        # federal_pre1987.py computes neither `pref` nor the itemize flag
        # as columns, so both are reconstructed here.
        caprat = float(resolve_year(PRE1987_PARAMS["capital_gains_exclusion_rate"], effective_year))
        net_ltcg = pl.min_horizontal(pl.col("ltcg"), pl.col("stcg") + pl.col("ltcg")).clip(0, None)
        capded = caprat * net_ltcg
        if effective_year <= 1978:
            fed_itemized = pl.col("proptax") + pl.col("otheritem") + pl.col("mortgage") + pl.col("state_sales_or_income_tax_ded")
            fed_zbr = _by_status({s: resolve_year(PRE1987_PARAMS["standard_deduction"][s], effective_year) for s in _STATUSES})
            exded = pl.min_horizontal((fed_itemized - 0.6 * pl.col("agi")).clip(0, None), 0.4 * pl.col("agi"))
            ui_total = pl.max_horizontal(pl.col("ui"), pl.col("pui") + pl.col("sui"))
            pref = pl.when(fed_itemized > fed_zbr).then(
                (capded + ui_total + pl.col("pwages") + pl.col("swages") + exded).clip(0, None)
            ).otherwise(0.0)
        elif effective_year <= 1982:
            pref = pl.lit(0.0)
        else:
            pref = capded
        md_agi = md_agi + 0.5 * (pref - 10000.0 * pl.col("md_txp")).clip(0, None)
    if effective_year == 1980:
        md_agi = md_agi - pl.min_horizontal(pl.col("intrec"), 200.0 * pl.col("md_txp")).clip(0, None)
    if 1982 <= effective_year <= 1986:
        two_earner_rate = float(resolve_year(PRE1987_PARAMS["two_earner_deduction_rate"], effective_year))
        two_earner_cap = float(resolve_year(PRE1987_PARAMS["two_earner_deduction_cap"], effective_year))
        twoded = (
            two_earner_rate * pl.min_horizontal(pl.col("pwages"), pl.col("swages")).clip(0, None)
        ).clip(0, two_earner_cap)
        md_agi = md_agi + twoded

    df = df.with_columns(md_agi=md_agi)

    # --- Maryland's own two-earner subtraction (1992+, joint returns only) ---
    wages_total = pl.col("pwages") + pl.col("swages")
    agih = pl.col("pwages") + 0.5 * (pl.col("agi") - wages_total)
    agiw = pl.col("swages") + 0.5 * (pl.col("agi") - wages_total)
    subtr = chexp  # data(22)/penexc/comnew(79) confirmed inert.
    twoh = (agih - 0.5 * subtr).clip(0, None)
    twow = (agiw - 0.5 * subtr).clip(0, None)
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
        # `(data(9)+data(10))*800` elderly/blind addback confirmed inert.
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
        salt_plus_mortgage = pl.col("proptax") + pl.col("otheritem") + pl.col("mortgage") + pl.col("state_sales_or_income_tax_ded")
        zbr = _by_status({s: resolve_year(PRE1987_PARAMS["standard_deduction"][s], effective_year) for s in _STATUSES})
        xitded = pl.max_horizontal(salt_plus_mortgage - pl.col("state_sales_or_income_tax_ded"), zbr)
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

    df = df.with_columns(md_deduc=pl.when(xitded >= pl.col("md_stded")).then(xitded).otherwise(pl.col("md_stded")))

    # --- Exemption ---
    exemps_count = (pl.when(is_joint).then(2.0).otherwise(1.0)) + pl.col("depx")  # `comnew(68)`
    if effective_year <= 1989:
        xmp_amt = float(resolve_year(p["personal_exemption_amount"], effective_year))
        exemp = exemps_count * xmp_amt
    else:
        xmp_amt = float(resolve_year(p["personal_exemption_amount"], effective_year))
        base_exemp = pl.col("md_txp") + pl.col("depx")  # `data(7)+data(8)` (blage/elderly confirmed inert)
        exemp = base_exemp * xmp_amt
        fed_agi = pl.col("agi")
        if 2008 <= effective_year <= 2011:
            ss_tiers = p["exemption_phaseout_2008_2011_single_separate"]
            jh_tiers = p["exemption_phaseout_2008_2011_joint_hoh"]
            replaced = pl.when(is_single_or_separate).then(_tiered_replace(fed_agi, ss_tiers)).otherwise(_tiered_replace(fed_agi, jh_tiers))
            exemp = pl.when(fed_agi > 100000.0).then(base_exemp * replaced).otherwise(exemp)
        elif effective_year >= 2012:
            ss_tiers = p["exemption_phaseout_2012plus_single_separate"]
            jh_tiers = p["exemption_phaseout_2012plus_joint_hoh"]
            replaced = pl.when(is_single_or_separate).then(_tiered_replace(fed_agi, ss_tiers)).otherwise(_tiered_replace(fed_agi, jh_tiers))
            exemp = pl.when(fed_agi > 100000.0).then(base_exemp * replaced).otherwise(exemp)
        # `data(105)` (dependent of another return) confirmed inert.

    df = df.with_columns(md_exemp=exemp)
    df = df.with_columns(md_taxinc=(pl.col("md_agi") - pl.col("md_deduc") - pl.col("md_exemp")).clip(0, None))

    # --- Bracket tax --- (single/married_separate get a compressed
    # top-bracket schedule from 1992 on - see module docstring point 2)
    if effective_year <= 1991:
        statax = bracket_tax(pl.col("md_taxinc"), p["brackets_1977_1991"])
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
    elif effective_year <= 2011:
        statax = pl.when(is_single_or_separate).then(
            bracket_tax(pl.col("md_taxinc"), p["brackets_2008_2011_single_separate"])
        ).otherwise(bracket_tax(pl.col("md_taxinc"), p["brackets_2008_2011_joint_hoh"]))
    else:
        statax = pl.when(is_single_or_separate).then(
            bracket_tax(pl.col("md_taxinc"), p["brackets_2012plus_single_separate"])
        ).otherwise(bracket_tax(pl.col("md_taxinc"), p["brackets_2012plus_joint_hoh"]))

    df = df.with_columns(md_taxbc=statax)

    # --- Credits ---
    earncr = pl.lit(0.0)
    if effective_year >= 1987:
        earncr = 0.5 * pl.col("eitc")
    earncr = pl.min_horizontal(pl.col("md_taxbc"), earncr)
    df = df.with_columns(md_statax=(pl.col("md_taxbc") - earncr).clip(0, None))

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
            childless_gate = (pl.col("md_statax") < 1.0) & (pl.col("depx") < 1.0)
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

    if effective_year <= 1986:
        ccc_cap = pl.col("credit")
    elif effective_year <= 1997:
        ccc_cap = _raw_ccc_1987_1997(df, effective_year)
    else:
        ccc_cap = pl.col("ccc")
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
        pov1 = float(resolve_year(p["poverty_credit_income_base"], effective_year))
        pov2 = float(resolve_year(p["poverty_credit_income_per_additional_exemption"], effective_year))
        xlin3 = pov1 + pov2 * (exemps_count - 1.0)
        xlin4 = pl.max_horizontal(pl.col("agi"), pl.col("earned_income"))
        ptcr = pl.when(xlin3 >= xlin4).then(0.05 * pl.col("earned_income")).otherwise(0.0)
    else:
        ptcr = pl.lit(0.0)
    df = df.with_columns(md_statax=(pl.col("md_statax") - ptcr).clip(0, None))

    df = df.with_columns(md_statax=pl.col("md_statax") - refcr)

    # 2019+ refundable portion of the Child/Dependent Care Credit.
    if effective_year >= 2019:
        chref = pl.when(pl.col("agi") <= fagim).then((chcr - pl.col("md_taxbc")).clip(0, None)).otherwise(0.0)
        df = df.with_columns(md_statax=pl.col("md_statax") - chref)

    df = df.with_columns(siitax=pl.col("md_statax") * flate)
    return df
