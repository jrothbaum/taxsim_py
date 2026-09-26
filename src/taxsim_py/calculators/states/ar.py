"""Arkansas individual income tax calculator."""

import polars as pl

from taxsim_py.engine.inputs import aged_count, taxpayer_count
from taxsim_py.engine.brackets import bracket_rate, bracket_tax
from taxsim_py.engine.schema import PARAMETERS_ROOT, load_yaml, resolve_year
from taxsim_py.engine.state import with_defaults, forced_itemized, forced_standard, with_state_detail
from taxsim_py.engine.state_extrapolation import deflate_for_extrapolation, resolve_state_year

AR_PARAMS = load_yaml(PARAMETERS_ROOT / "states" / "ar" / "income_tax.yaml")
PRE1987_PARAMS = load_yaml(PARAMETERS_ROOT / "national" / "pre1987.yaml")
STATE_ADJUSTMENT_PARAMS = load_yaml(PARAMETERS_ROOT / "national" / "state_adjustments.yaml")
AR_LOW_INCOME_TABLE = pl.read_csv(PARAMETERS_ROOT / "states" / "ar" / "low_income_table.csv")

_PRE1987_STATUSES = ["single", "married_joint", "married_separate", "head_of_household"]
_MST_MAP = {"single": 1, "married_joint": 2, "married_separate": 6, "head_of_household": 4}

def _tabst_brackets(raw_pairs: list[list[float]]) -> list[list[float]]:
    """Convert `look`'s own (upper_bound, rate_pct) pairs into this
    project's (start, rate_fraction) convention (see engine.brackets)."""
    out = [[0.0, raw_pairs[0][1] / 100.0]]
    for i in range(1, len(raw_pairs)):
        out.append([raw_pairs[i - 1][0], raw_pairs[i][1] / 100.0])
    return out


TABST1 = _tabst_brackets(AR_PARAMS["pre1987_brackets_primary"])
TABST2 = _tabst_brackets(AR_PARAMS["pre1987_brackets_secondary"])


def _rate_lookup_brackets(rows: list[list[float]]) -> pl.Expr:
    """Build Arkansas's rate and subtraction lookup."""

    def build(income: pl.Expr) -> pl.Expr:
        expr = pl.lit(rows[-1][1] / 100.0) * income - pl.lit(rows[-1][2])
        for threshold, rate, subtraction in reversed(rows[:-1]):
            expr = pl.when(income < threshold).then((rate / 100.0) * income - subtraction).otherwise(expr)
        return expr

    return build


def _low_income_override(df: pl.DataFrame, effective_year: int) -> pl.Expr | None:
    table = AR_LOW_INCOME_TABLE.filter(pl.col("year") == effective_year)
    if table.height == 0:
        return None
    ge_lower = effective_year >= 2017
    applies = pl.lit(False)
    value = pl.lit(0.0)
    for status in ("single", "married_joint", "head_of_household"):
        rows = table.filter(pl.col("status") == status).sort("tier_order")
        if rows.height == 0:
            continue
        for dep_tier in sorted(set(rows["dep_tier"].to_list())):
            tier_rows = rows.filter(pl.col("dep_tier") == dep_tier).sort("tier_order")
            if dep_tier == 0:
                dep_cond = pl.lit(True)
            elif dep_tier == 1:
                dep_cond = pl.col("depx") <= 1
            else:
                dep_cond = pl.col("depx") >= 2
            status_cond = (pl.col("filing_status") == status) & dep_cond
            group_upper = float(tier_rows["upper_bound"][-1])
            group_lower0 = float(tier_rows["lower_bound"][0])
            lower_test = (pl.col("ar_agi") >= group_lower0) if ge_lower else (pl.col("ar_agi") > group_lower0)
            in_range = status_cond & lower_test & (pl.col("ar_agi") <= group_upper)
            applies = applies | (status_cond & (pl.col("ar_agi") <= group_upper))
            zero_upper = float(tier_rows["lower_bound"][0])
            tier_val = pl.when(pl.col("ar_agi") <= zero_upper).then(0.0).otherwise(pl.lit(None, dtype=pl.Float64))
            policy = tier_rows.select(
                "formula_offset", "base", "rate", "upper_bound", "upper_exclusive"
            ).to_dict(as_series=False)
            for offset, base, rate, upper, excl in zip(
                policy["formula_offset"],
                policy["base"],
                policy["rate"],
                policy["upper_bound"],
                policy["upper_exclusive"],
            ):
                cond = (pl.col("ar_agi") < upper) if excl else (pl.col("ar_agi") <= upper)
                tier_val = pl.when(cond & tier_val.is_null()).then(base + rate * (pl.col("ar_agi") - offset)).otherwise(tier_val)
            value = pl.when(in_range).then(tier_val.fill_null(0.0)).otherwise(value)
    return pl.when(applies).then(value).otherwise(None)


def compute_ar_tax(df: pl.DataFrame, year: int) -> pl.DataFrame:
    effective_year, flate = resolve_state_year(year)
    p = AR_PARAMS
    df = with_defaults(df, ("federal_chcr", "proptax", "otheritem", "mortgage", "dividends", "ltcg", "stcg", "intrec", "depx", "dep17", "ui"))

    # Year>LASTAT (2021): deflate every dollar-valued raw/federal-computed
    # input by `flate`, run 2021's REAL law (`effective_year`, already
    # forced to 2021 by `resolve_state_year`) on the deflated figures, then
    # reinflate the final tax at the very end (see
    # engine/state_extrapolation.py). A no-op for year<=2021 (`flate==1`).
    # Unlike AL, AR never re-invokes `compute_regular_tax` internally and
    # only reads ONE federal-computed column (`ccc`, comnew(53) - within
    # the dispatcher's `comnew(1:98)` generic-deflate range, so it's
    # divided by `flate` for real too) - `wages` is federal.py's own
    # pwages+swages sum, computed at the real year before this module
    # runs, so it needs deflating directly (deflating pwages/swages alone
    # wouldn't reach the already-materialized `wages` column).
    # Business income (TAXSIM slots above 199) is not deflated.
    df = df.with_columns(
        ar_business=pl.col("pbusinc") + pl.col("pprofinc") + pl.col("sbusinc") + pl.col("sprofinc") + pl.col("scorp")
    )
    df = deflate_for_extrapolation(
        df,
        flate,
        [
            "federal_chcr",
            "pwages", "swages", "wages", "proptax", "otheritem", "mortgage",
            "dividends", "ltcg", "stcg", "intrec", "ui", "ccc", "psemp", "ssemp", "pensions", "otherprop",
            "scorp",
        ],
    )

    df = df.with_columns(
        ar_txp=taxpayer_count(),
        ar_sep=pl.when(pl.col("filing_status") == "married_separate").then(2.0).otherwise(1.0),
        ar_low_income_eligible=pl.col("filing_status") != "married_separate",
    )

    # --- Capital gains inclusion (`capgn`) ---
    if effective_year <= 1986:
        excl = float(resolve_year(PRE1987_PARAMS["capital_gains_exclusion_rate"], effective_year))
        df = df.with_columns(ar_capgn=pl.col("stcg") + pl.col("ltcg") * (1.0 - excl))
    elif effective_year <= 1998:
        df = df.with_columns(ar_capgn=pl.col("stcg") + pl.col("ltcg"))
    else:
        exclusion_rate = float(
            resolve_year(
                p["capital_gains_ltcg_exclusion_rate_1999plus"],
                effective_year,
            )
        )
        rate = 1.0 - exclusion_rate
        capgn = pl.col("stcg") + rate * pl.col("ltcg")
        if effective_year >= 2014:
            capgn = capgn.clip(None, float(p["capital_gains_cap_2014plus"]))
        df = df.with_columns(ar_capgn=capgn)

    # --- AGI ---
    # Arkansas builds income from its parts: wages, dividends, interest,
    # self-employment income, pensions, gains, the federal Schedule E figure
    # (other property income, and from 1987 S corporation income) and
    # business income including S corporation income again.
    schedule_e = pl.col("otherprop") + (pl.col("scorp") if effective_year >= 1987 else 0.0)
    business = pl.col("ar_business")
    df = df.with_columns(
        ar_agi=pl.col("wages") + (pl.col("dividends") + 0.001) + pl.col("intrec") + pl.col("ar_capgn")
        + pl.col("psemp") + pl.col("ssemp") + pl.col("pensions") + schedule_e + business
    )
    # Pension exclusion.
    if effective_year >= 1983:
        amount = float(resolve_year(p["pension_exclusion_amount"], effective_year))
        basis = resolve_year(p["pension_exclusion_basis"], effective_year)
        count = {"return": pl.lit(1.0), "taxpayer": pl.col("ar_txp"), "aged": aged_count()}[basis]
        df = df.with_columns(ar_agi=pl.col("ar_agi") - pl.min_horizontal(pl.col("pensions"), amount * count))
    if 2018 <= effective_year <= 2019:
        df = df.with_columns(ar_agi=pl.col("ar_agi") + pl.col("ui"))
    df = df.with_columns(ar_ag=pl.col("ar_agi").clip(0, None))

    # --- Standard deduction ---
    if effective_year <= 1986:
        pct = float(p["standard_deduction_pct_pre1987"])
        cap = 4000.0
    elif effective_year <= 1997:
        pct = float(p["standard_deduction_pct_1987_1997"])
        cap = 2000.0
    else:
        pct = None
    if pct is not None:
        df = df.with_columns(
            ar_stded=(pct * pl.col("ar_agi")).clip(0, cap * pl.col("ar_txp") / pl.col("ar_sep"))
        )
    else:
        flat = float(resolve_year(p["standard_deduction_flat_1998plus"], effective_year))
        df = df.with_columns(ar_stded=flat * pl.col("ar_txp"))

    # --- Itemized deduction: raw proptax+otheritem+mortgage (comnew(30)
    # minus the SALT term cancels exactly, same technique as AL/IL/AZ<=1990)
    # + AR's own Pease-style phaseout, 1991-2017 (a real, AR-only 1% - not
    # 3% - reduction rate for 2009 specifically). ---
    df = df.with_columns(ar_xitded_base=pl.col("proptax") + pl.col("otheritem") + pl.col("mortgage"))
    if 1991 <= effective_year <= 2017:
        if effective_year <= 2012:
            aif92 = float(resolve_year(STATE_ADJUSTMENT_PARAMS["itemized_phaseout_inflation"], effective_year))
            threshold = float(p["itemized_phaseout_income_pre2013"]) * aif92
            df = df.with_columns(ar_phas=threshold / pl.col("ar_sep"))
        else:
            aif13 = float(resolve_year(STATE_ADJUSTMENT_PARAMS["itemized_phaseout_inflation"], effective_year))
            thr_expr = pl.lit(None, dtype=pl.Float64)
            for status in _PRE1987_STATUSES:
                v = float(p["itemized_phaseout_income_2013_2017"][status]) * aif13
                thr_expr = pl.when(pl.col("filing_status") == status).then(pl.lit(v)).otherwise(thr_expr)
            df = df.with_columns(ar_phas=thr_expr)
        reduce_rate = 0.01 if effective_year == 2009 else 0.03
        if 2010 <= effective_year <= 2012:
            df = df.with_columns(ar_reduce=pl.lit(0.0))
        else:
            df = df.with_columns(
                ar_reduce=pl.when(pl.col("ar_agi") > pl.col("ar_phas"))
                .then(pl.min_horizontal(0.8 * pl.col("ar_xitded_base"), reduce_rate * (pl.col("ar_agi") - pl.col("ar_phas"))))
                .otherwise(0.0)
            )
        df = df.with_columns(ar_xitded=(pl.col("ar_xitded_base") - pl.col("ar_reduce")).clip(0, None))
    else:
        df = df.with_columns(ar_xitded=pl.col("ar_xitded_base"))

    # `if(ided.eq.-2) xitded=0` - `ided`=`data(4)` is the SAME flag
    # `federal_state.py`'s forced-standard pass sets, so AR's own
    # itemized deduction is unconditionally zeroed on that pass,
    # independent of whether stded>=xitded would otherwise have chosen
    # standard anyway. Confirmed via a live oracle probe (single, $50,000
    # wages, no itemizable expenses, 1977-1997: the winning branch is
    # actually the FORCED-ITEMIZE one using the `tab`/`tbase` table, not
    # the `tabst1` standard-AGI table a naive stded>=xitded check would
    # have picked - because federal's own combined tax is lower there for
    # this era, not because AR's OWN state tax is lower).
    df = df.with_columns(ar_xitded=pl.when(forced_standard()).then(0.0).otherwise(pl.col("ar_xitded")))

    df = df.with_columns(ar_deduc=pl.max_horizontal(pl.col("ar_stded"), pl.col("ar_xitded")))
    df = df.with_columns(ar_taxinc=(pl.col("ar_agi") - pl.col("ar_deduc")).clip(0, None))

    # --- Married earner-split (agih/agiw), taxsim_2022_10_21.f:~1358-1375
    # - used by every bracket era EXCEPT the pre-1998 standard-deduction
    # path (which has its own, different look()-internal split). ---
    df = df.with_columns(
        ar_agih=pl.max_horizontal(pl.col("pwages"), pl.col("swages")) + 0.5 * (pl.col("ar_agi") - pl.col("wages")),
    )
    df = df.with_columns(ar_agiw=pl.col("ar_agi") - pl.col("ar_agih"))
    df = df.with_columns(
        ar_xitdh=pl.when(pl.col("ar_agi") != 0).then(pl.col("ar_xitded") * pl.col("ar_agih") / pl.col("ar_agi")).otherwise(0.0)
    )
    df = df.with_columns(ar_xitdw=pl.col("ar_xitded") - pl.col("ar_xitdh"))
    df = df.with_columns(
        ar_dedh=pl.max_horizontal(0.5 * pl.col("ar_stded"), pl.col("ar_xitdh")),
        ar_dedw=pl.max_horizontal(0.5 * pl.col("ar_stded"), pl.col("ar_xitdw")),
    )
    df = df.with_columns(
        ar_taxinh=(pl.col("ar_agih") - pl.col("ar_dedh")).clip(0, None),
        ar_taxinw=(pl.col("ar_agiw") - pl.col("ar_dedw")).clip(0, None),
    )
    is_joint_split = (pl.col("filing_status") == "married_joint") & (pl.col("ar_agi") > 0)

    # --- Main bracket computation ---
    # `if(stded.ge.xitded.and.ided.ne.-1)` -> standard/AGI-based table;
    # else itemized/taxinc-based table. `force_itemize=True` always takes
    # the itemized branch (even with $0 itemized deductions - a real,
    # confirmed-via-probe oracle behavior, not a heuristic).
    prefers_itemized_or_forced = forced_itemized() | (pl.col("ar_stded") < pl.col("ar_xitded"))
    if effective_year <= 1997:
        brackets_std1 = TABST1
        brackets_std2 = TABST2
        brackets_item = p["brackets_base"]
        stat_std = pl.when(pl.col("filing_status") == "married_separate").then(
            bracket_tax(pl.col("ar_agi"), brackets_std2)
        ).otherwise(bracket_tax(pl.col("ar_agi"), brackets_std1))
        # look2's own ajnt=2 doubling (method A) vs look's earner-split (method B); take the min.
        method_a = 2.0 * bracket_tax(pl.col("ar_agi") / 2.0, TABST1)
        agih_wage = pl.max_horizontal(pl.col("pwages"), pl.col("swages")) + 0.5 * (pl.col("ar_agi") - pl.col("wages"))
        agiw_wage = pl.col("ar_agi") - agih_wage
        method_b = bracket_tax(agih_wage, TABST1) + bracket_tax(agiw_wage, TABST1)
        stat_std = pl.when(pl.col("filing_status") == "married_joint").then(
            pl.min_horizontal(method_a, method_b)
        ).otherwise(stat_std)

        stat_item = bracket_tax(pl.col("ar_taxinc"), brackets_item)
        stat_item_h = bracket_tax(pl.col("ar_taxinh"), brackets_item)
        stat_item_w = bracket_tax(pl.col("ar_taxinw"), brackets_item)
        stat_item = pl.when(is_joint_split).then(pl.min_horizontal(stat_item, stat_item_h + stat_item_w)).otherwise(stat_item)

        df = df.with_columns(ar_statax=pl.when(prefers_itemized_or_forced).then(stat_item).otherwise(stat_std))
        # The last bracket lookup: the husband's share of the standard
        # table on joint returns (the full table halves income), else the
        # wife's share on itemized joint returns.
        rate_std = (
            pl.when(pl.col("filing_status") == "married_joint").then(bracket_rate(agih_wage, TABST1))
            .when(pl.col("filing_status") == "married_separate").then(bracket_rate(pl.col("ar_agi"), TABST2))
            .otherwise(bracket_rate(pl.col("ar_agi"), TABST1))
        )
        rate_item = pl.when(is_joint_split).then(bracket_rate(pl.col("ar_taxinw"), brackets_item)).otherwise(
            bracket_rate(pl.col("ar_taxinc"), brackets_item)
        )
        rate_expr = pl.when(prefers_itemized_or_forced).then(rate_item).otherwise(rate_std)
    elif effective_year <= 2015:
        brackets = [list(b) for b in p["brackets_base"]]
        thresholds_by_year = p["bracket_thresholds_by_year"]
        if effective_year in thresholds_by_year:
            # raw `tab(1,1..5)` = upper bounds of brackets 1-5 = the START
            # of (my 0-indexed) brackets 1-5 too (bracket 0 always starts
            # at 0; bracket 5's own start, the 6th and last entry, is the
            # 5th override value).
            new_thr = thresholds_by_year[effective_year]
            for i in range(1, 6):
                brackets[i][0] = float(new_thr[i - 1])
        if effective_year >= 2014:
            brackets[0][1] = float(p["bracket_rate1_override_2014plus"])
        if effective_year == 2015:
            r2, r3, r4 = p["bracket_rates_2_3_4_override_2015"]
            brackets[1][1] = float(r2)
            brackets[2][1] = float(r3)
            brackets[3][1] = float(r4)
        stat = bracket_tax(pl.col("ar_taxinc"), brackets)
        stat_h = bracket_tax(pl.col("ar_taxinh"), brackets)
        stat_w = bracket_tax(pl.col("ar_taxinw"), brackets)
        df = df.with_columns(ar_statax=pl.when(is_joint_split).then(pl.min_horizontal(stat, stat_h + stat_w)).otherwise(stat))
        rate_expr = pl.when(is_joint_split).then(bracket_rate(pl.col("ar_taxinw"), brackets)).otherwise(
            bracket_rate(pl.col("ar_taxinc"), brackets)
        )
    elif effective_year == 2016:
        brackets = p["brackets_2016"]
        stat = bracket_tax(pl.col("ar_taxinc"), brackets)
        stat_h = bracket_tax(pl.col("ar_taxinh"), brackets)
        stat_w = bracket_tax(pl.col("ar_taxinw"), brackets)
        df = df.with_columns(ar_statax=pl.when(is_joint_split).then(pl.min_horizontal(stat, stat_h + stat_w)).otherwise(stat))
        rate_expr = pl.when(is_joint_split).then(bracket_rate(pl.col("ar_taxinw"), brackets)).otherwise(
            bracket_rate(pl.col("ar_taxinc"), brackets)
        )
    else:
        rows = [[float(t), float(r), float(s)] for t, r, s in p["rate_lookup_table"][effective_year]]
        lookup = _rate_lookup_brackets(rows)
        stat = lookup(pl.col("ar_taxinc"))
        stat_h = lookup(pl.col("ar_taxinh"))
        stat_w = lookup(pl.col("ar_taxinw"))
        df = df.with_columns(ar_statax=pl.when(is_joint_split).then(pl.min_horizontal(stat, stat_h + stat_w)).otherwise(stat))
        # 2017 on the tax comes from a formula table, not a bracket lookup.
        rate_expr = pl.lit(0.0)

    # --- 2003-2004 surcharge ---
    if 2003 <= effective_year <= 2004:
        df = df.with_columns(ar_statax=pl.col("ar_statax") * (1.0 + float(p["surcharge_2003_2004"])))

    # --- 1998-2002 Working Taxpayer Credit ---
    if 1998 <= effective_year <= 2002:
        rate = float(p["working_taxpayer_credit_rate"])
        cap = float(p["working_taxpayer_credit_cap"])
        floor = float(p["working_taxpayer_credit_floor"])
        winc85 = pl.max_horizontal(pl.col("pwages"), pl.col("swages"))
        winc86 = pl.min_horizontal(pl.col("pwages"), pl.col("swages"))
        work85 = pl.when(winc85 > floor).then(pl.min_horizontal(cap, rate * winc85)).otherwise(0.0)
        work86 = pl.when((winc86 > floor) & (pl.col("pwages") > 0) & (pl.col("swages") > 0)).then(
            pl.min_horizontal(cap, rate * winc86)
        ).otherwise(0.0)
        winc_single = pl.col("wages")
        work_single = pl.when(winc_single > floor).then(pl.min_horizontal(cap, rate * winc_single)).otherwise(0.0)
        two_earner = (pl.col("pwages") > 0) & (pl.col("swages") > 0)
        work = pl.when(two_earner).then(work85 + work86).otherwise(work_single)
        df = df.with_columns(ar_work=work)
        df = df.with_columns(ar_statax=(pl.col("ar_statax") - pl.col("ar_work")).clip(0, None))
    else:
        df = df.with_columns(ar_work=pl.lit(0.0))

    # --- Low income table override (pre-1991 special formulas + the
    # verified 1991-2021 CSV) - married_separate is excluded entirely. ---
    if effective_year <= 1990:
        depx = pl.col("depx")
        single_ov = pl.when(pl.col("ar_agi") < 3010).then(0.0).when(pl.col("ar_agi") <= 3100).then(
            ((pl.col("ar_agi") - 2990) / 10).floor()
        ).otherwise(None)
        married_nodep_ov = pl.when(pl.col("ar_agi") < 4008).then(0.0).when(pl.col("ar_agi") <= 4100).then(
            ((pl.col("ar_agi") - 3990) / 10).floor() * 1.2
        ).otherwise(None)
        richer_dep01_ov = pl.when(pl.col("ar_agi") < 4505).then(0.0).when(pl.col("ar_agi") <= 4600).then(
            ((pl.col("ar_agi") - 4490) / 10).floor() * 1.7
        ).otherwise(None)
        richer_dep2_ov = pl.when(pl.col("ar_agi") < 5004).then(0.0).when(pl.col("ar_agi") <= 5100).then(
            ((pl.col("ar_agi") - 4990) / 10).floor() * 1.1
        ).otherwise(None)
        ov = pl.when((pl.col("filing_status") == "single") & single_ov.is_not_null()).then(single_ov)
        ov = ov.when(
            (pl.col("filing_status") == "married_joint") & (depx < 1) & married_nodep_ov.is_not_null()
        ).then(married_nodep_ov)
        richer = pl.col("filing_status").is_in(["married_joint", "head_of_household"])
        ov = ov.when(richer & (depx <= 1) & richer_dep01_ov.is_not_null()).then(richer_dep01_ov)
        ov = ov.when(richer & (depx >= 2) & richer_dep2_ov.is_not_null()).then(richer_dep2_ov)
        df = df.with_columns(ar_statax=pl.when(ov.is_not_null()).then(ov).otherwise(pl.col("ar_statax")))
    else:
        override_expr = _low_income_override(df, effective_year)
        if override_expr is not None:
            df = df.with_columns(
                ar_override=pl.when(pl.col("ar_low_income_eligible")).then(override_expr).otherwise(None)
            )
            df = df.with_columns(
                ar_statax=pl.when(pl.col("ar_override").is_not_null()).then(pl.col("ar_override")).otherwise(pl.col("ar_statax"))
            )

    # --- AR capital gains adjustment, 1991-1998 ---
    if 1991 <= effective_year <= 1998:
        df = df.with_columns(
            ar_statax=pl.col("ar_statax")
            - 0.01
            * pl.when(pl.col("ar_taxinc") > 25000)
            .then(pl.min_horizontal(pl.col("ar_taxinc") - 25000, 15000.0, pl.col("ar_capgn").clip(0, None)))
            .otherwise(0.0)
        )

    # --- Personal Tax Credit + Child/Dependent Care Credit ---
    if effective_year <= 1986:
        # `gcred=17.50*(data(7)+data(9)+data(10))+6.*data(8)` - dependents
        # get a SEPARATE, smaller $6 rate pre-1987 (not $17.50), and there
        # is no head_of_household bonus at all in this era's formula.
        gcred = p["personal_credit_pre1987"] * (pl.col("ar_txp") + aged_count()) + p["dependent_credit_pre1987"] * pl.col("depx")
    else:
        pcr = float(resolve_year(p["personal_credit_rate_by_year"], effective_year))
        gcred = pcr * (pl.col("ar_txp") + aged_count() + pl.col("depx"))
        gcred = gcred + pl.when(pl.col("filing_status") == "head_of_household").then(pcr).otherwise(0.0)
        # Taxpayers 65 or older without pension income get one more credit.
        gcred = gcred + pl.when((pl.col("pensions") == 0) & (aged_count() > 0)).then(pcr).otherwise(0.0)
    df = df.with_columns(ar_gcred=gcred)

    child_rate = float(p["child_care_credit_rate_1998plus"] if effective_year >= 1998 else p["child_care_credit_rate_pre1998"])
    # A share of the federal child care credit (`comnew(53)`).
    child = pl.col("federal_chcr").clip(0, None)
    df = df.with_columns(ar_chcr=(child_rate * child).clip(0, None))
    if effective_year == 1982:
        df = df.with_columns(ar_chcr=pl.min_horizontal(pl.col("ar_chcr"), 40.0 * pl.col("depx").clip(0, 2)))

    df = df.with_columns(ar_credit=pl.col("ar_chcr") + pl.col("ar_gcred"))
    df = df.with_columns(siitax=(pl.col("ar_statax") - pl.col("ar_credit")).clip(0, None) * flate)
    return with_state_detail(
        df,
        agi=pl.col("ar_agi"),
        standard_deduction=pl.col("ar_stded"),
        itemized_deductions=pl.col("ar_xitded"),
        taxable_income=pl.col("ar_taxinc"),
        child_care_credit=pl.col("ar_chcr"),
        credits=pl.col("ar_credit") + pl.col("ar_work"),
        rate=rate_expr,
    )
