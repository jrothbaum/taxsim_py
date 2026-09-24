"""Arkansas individual income tax (`artax`, taxsim_2022_10_21.f:1172-2197,
state id 4). See parameters/states/ar/income_tax.yaml for the full scope
note - this is the largest state built so far, dominated by a fully
hand-coded per-year "low income table" override (verified row-by-row
against a live compiled probe of `artax` itself, see
scripts/gen_ar_low_income_csv.py).

Full real-law range (1977-2021) validated via scripts/validate_states.py:
**2,385/2,385 exact (100%)**. Building this surfaced a real, general bug
in the shared federal calculator (see `federal.py`'s `compute_regular_tax`
and `federal_pre1987.py`): `compute_regular_tax` was silently dropping the
`force_itemize` parameter for every year<=1986 instead of forwarding it,
and `federal_pre1987.py` itself needed a further year-gate
(`force_itemize` genuinely has no effect on the real source's own
itemize-vs-standard choice for lawyr<=1981 - only lawyr>=1982). This
single fix, needed here because Arkansas's own `ided`-gated table
selection (`compute_ar_tax`'s own `force_itemize` parameter) is the first
state calculator that actually threads `force_itemize` through to a
state's own logic, retroactively brought Arizona from 95.4% to 100% and
Alabama from 95.6% to 98.9% (its remaining 14 failures, married_separate
at high income 2008+, are a separately-confirmed, genuine oracle
staleness artifact - see al.py's own docstring).
"""

import polars as pl

from taxsim_py.engine.brackets import bracket_tax
from taxsim_py.engine.schema import PARAMETERS_ROOT, load_yaml, resolve_year
from taxsim_py.engine.state_extrapolation import deflate_for_extrapolation, resolve_state_year

AR_PARAMS = load_yaml(PARAMETERS_ROOT / "states" / "ar" / "income_tax.yaml")
PRE1987_PARAMS = load_yaml(PARAMETERS_ROOT / "national" / "pre1987.yaml")
AR_LOW_INCOME_TABLE = pl.read_csv(PARAMETERS_ROOT / "states" / "ar" / "low_income_table.csv")

_PRE1987_STATUSES = ["single", "married_joint", "married_separate", "head_of_household"]
_MST_MAP = {"single": 1, "married_joint": 2, "married_separate": 6, "head_of_household": 4}

AIF92 = {
    1992: 1.0525, 1993: 1.0845, 1994: 1.118, 1995: 1.147, 1996: 1.1795,
    1997: 1.212, 1998: 1.245, 1999: 1.266, 2000: 1.2895, 2001: 1.3295,
    2002: 1.373, 2003: 1.395, 2004: 1.427, 2005: 1.4595, 2006: 1.505,
    2007: 1.564, 2008: 1.5995, 2009: 1.668, 2010: 1.6955, 2011: 1.6955,
    2012: 1.7365,
}
AIF13 = {2013: 1.0, 2014: 1.0168, 2015: 1.033, 2016: 1.0376, 2017: 1.046}


def _with_default(df: pl.DataFrame, column: str, default: float = 0.0) -> pl.DataFrame:
    if column in df.columns:
        return df
    return df.with_columns(pl.lit(default).alias(column))


def _tabst_brackets(raw_pairs: list[list[float]]) -> list[list[float]]:
    """Convert `look`'s own (upper_bound, rate_pct) pairs into this
    project's (start, rate_fraction) convention (see engine.brackets)."""
    out = [[0.0, raw_pairs[0][1] / 100.0]]
    for i in range(1, len(raw_pairs)):
        out.append([raw_pairs[i - 1][0], raw_pairs[i][1] / 100.0])
    return out


TABST1 = _tabst_brackets(
    [[3000, 0.90], [4000, 1.70], [5000, 2.20], [6000, 2.30], [7000, 2.50],
     [10000, 3.150], [16000, 4.50], [17000, 5.90], [25000, 6.0], [1.0e20, 7.0]]
)
TABST2 = _tabst_brackets(
    [[3000, 0.90], [4000, 1.70], [5000, 2.20], [6000, 2.50], [7000, 3.0],
     [9000, 3.50], [10000, 3.90], [15000, 4.50], [16000, 5.20], [24000, 6.0],
     [25000, 6.50], [1.0e20, 7.0]]
)


def _rate_lookup_brackets(rows: list[list[float]]) -> pl.Expr:
    """AR's 2017-2021 `tab17t`-style rate/subtraction lookup: find the
    first row whose threshold exceeds income, tax = rate*income/100 -
    subtraction (taxsim_2022_10_21.f:~1454-1565). NOT a cumulative
    bracket table - the subtraction amounts genuinely decrease across the
    flat-rate tail rows (a real recapture mechanic)."""

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
            for row in tier_rows.iter_rows(named=True):
                offset = float(row["formula_offset"])
                base = float(row["base"])
                rate = float(row["rate"])
                upper = float(row["upper_bound"])
                excl = bool(row["upper_exclusive"])
                cond = (pl.col("ar_agi") < upper) if excl else (pl.col("ar_agi") <= upper)
                tier_val = pl.when(cond & tier_val.is_null()).then(base + rate * (pl.col("ar_agi") - offset)).otherwise(tier_val)
            value = pl.when(in_range).then(tier_val.fill_null(0.0)).otherwise(value)
    return pl.when(applies).then(value).otherwise(None)


def compute_ar_tax(df: pl.DataFrame, year: int, force_itemize: bool | None = None) -> pl.DataFrame:
    effective_year, flate = resolve_state_year(year)
    p = AR_PARAMS
    for col in ("proptax", "otheritem", "mortgage", "dividends", "ltcg", "stcg", "intrec", "depx", "dep17", "ui"):
        df = _with_default(df, col)

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
    df = deflate_for_extrapolation(
        df,
        flate,
        [
            "pwages", "swages", "wages", "proptax", "otheritem", "mortgage",
            "dividends", "ltcg", "stcg", "intrec", "ui", "ccc",
        ],
    )

    df = df.with_columns(
        ar_txp=pl.when(pl.col("filing_status") == "married_joint").then(2.0).otherwise(1.0),
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
        pcrcg = {1999: 0.70, 2015: 0.55, 2016: 0.50}
        rate = float(resolve_year(pcrcg, effective_year))
        capgn = pl.col("stcg") + rate * pl.col("ltcg")
        if effective_year >= 2014:
            capgn = capgn.clip(None, 10_000_000.0)
        df = df.with_columns(ar_capgn=capgn)

    # --- AGI ---
    df = df.with_columns(
        ar_agi=pl.col("wages") + (pl.col("dividends") + 0.001) + pl.col("intrec") + pl.col("ar_capgn")
    )
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
            aif92 = AIF92.get(effective_year, 1.0)
            threshold = float(p["itemized_phaseout_income_pre2013"]) * aif92
            df = df.with_columns(ar_phas=threshold / pl.col("ar_sep"))
        else:
            aif13 = AIF13[effective_year]
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
    if force_itemize is False:
        df = df.with_columns(ar_xitded=pl.lit(0.0))

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
    if force_itemize is True:
        prefers_itemized_or_forced = pl.lit(True)
    else:
        prefers_itemized_or_forced = pl.col("ar_stded") < pl.col("ar_xitded")
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
    elif effective_year == 2016:
        brackets = p["brackets_2016"]
        stat = bracket_tax(pl.col("ar_taxinc"), brackets)
        stat_h = bracket_tax(pl.col("ar_taxinh"), brackets)
        stat_w = bracket_tax(pl.col("ar_taxinw"), brackets)
        df = df.with_columns(ar_statax=pl.when(is_joint_split).then(pl.min_horizontal(stat, stat_h + stat_w)).otherwise(stat))
    else:
        rows = [[float(t), float(r), float(s)] for t, r, s in p["rate_lookup_table"][effective_year]]
        lookup = _rate_lookup_brackets(rows)
        stat = lookup(pl.col("ar_taxinc"))
        stat_h = lookup(pl.col("ar_taxinh"))
        stat_w = lookup(pl.col("ar_taxinw"))
        df = df.with_columns(ar_statax=pl.when(is_joint_split).then(pl.min_horizontal(stat, stat_h + stat_w)).otherwise(stat))

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
        gcred = 17.50 * pl.col("ar_txp") + 6.0 * pl.col("depx")
    else:
        pcr = float(resolve_year(p["personal_credit_rate_by_year"], effective_year))
        gcred = pcr * (pl.col("ar_txp") + pl.col("depx"))
        gcred = gcred + pl.when(pl.col("filing_status") == "head_of_household").then(pcr).otherwise(0.0)
    df = df.with_columns(ar_gcred=gcred)

    child_rate = float(p["child_care_credit_rate_1998plus"] if effective_year >= 1998 else p["child_care_credit_rate_pre1998"])
    # `child=min(comnew(53)[fed chcr],max(0,comnew(52)[taxbc]-data(34)[0]))`
    # - federal.py's own `ccc` is already capped at `tax_before_credits`,
    # matching this exactly for years>=1987. Approximated as $0 for
    # years<=1986 (federal_pre1987.py doesn't expose its own internal chcr
    # column) - a documented gap, revisit if the harness shows it matters.
    child = pl.col("ccc").clip(0, None) if "ccc" in df.columns else pl.lit(0.0)
    df = df.with_columns(ar_chcr=(child_rate * child).clip(0, None))
    if effective_year == 1982:
        df = df.with_columns(ar_chcr=pl.min_horizontal(pl.col("ar_chcr"), 40.0 * pl.col("depx").clip(0, 2)))

    df = df.with_columns(ar_credit=pl.col("ar_chcr") + pl.col("ar_gcred"))
    df = df.with_columns(siitax=(pl.col("ar_statax") - pl.col("ar_credit")).clip(0, None) * flate)
    return df
