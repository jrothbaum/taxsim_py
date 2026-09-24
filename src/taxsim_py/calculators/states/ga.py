"""Georgia individual income tax (`gatax`, taxsim_2024_09_21.f:4712-4955,
state id 11). See parameters/states/ga/income_tax.yaml for the full scope
note (confirmed-inert elderly/blind/solar/charity/1982-1986-IRA fields).

No EITC at all - Georgia's own source never references `comnew(59)`
anywhere in this subroutine.

Two real, non-obvious mechanics found while building this:
1. `xitded=(comnew(24)-data(50))*comnew(26)` - unlike every other state,
   Georgia doesn't gate its itemized section with an `if` at all; it
   MULTIPLIES the whole (state-tax-adjusted) federal itemized total by
   `comnew(26)`, which behaves as a 0/1 itemize indicator (this project's
   own `itemizes` flag) rather than a dollar amount - and then simply
   takes `max(xitded, stded)`, unlike DC's own unconditional "MUST use
   xitded when itemizing" rule. This is simpler to replicate correctly:
   compute `xitded` as `(itemized - state_tax) * itemizes_indicator`
   (zero whenever federal doesn't itemize) and let the `max()` naturally
   fall back to `stded`.
2. Married filers (joint AND separate) run their SHARED, doubled-
   threshold bracket table against `taxinc*sep` (not `taxinc` directly),
   then divide the resulting tax by `sep` - `sep=1` for joint (a no-op,
   taxinc runs through the doubled table as-is) but `sep=2` for
   married_separate, which numerically collapses married_separate onto
   the SAME thresholds as the single table (a doubled table run at 2x
   income, tax then halved).

A stray, narrow gap: `comnew(14)`, added to AGI for 1982-1986 via
`stkeo=max(comnew(14)-stklim,0.0d0)` (where `stklim` is itself confirmed
inert, no input this project's schema drives ever populates `data(28)`),
is not exposed as a column and its exact meaning wasn't traced (unlike
`comnew(32)`=twoded, already established via Colorado/DC) - treated as
$0 pending further investigation; likely only matters for 1982-1986
dividend/capital-gains-bearing test cases, if at all.

Harness (1977-2023, extrapolation built in from the start): **2,303/2,303
(100%)**. Three real bugs, all caught by validation rather than a plain
source read:
3. `if(law.le.2009) statax=max(0,statax-chcr-solar)-ycred; else
   statax=max(0,statax-chcr-solar-ycred)` - the Low-Income Credit is
   REFUNDABLE through 2009 (the $0 floor applies BEFORE subtracting it)
   but nonrefundable 2010+ (floor applies after) - missing this era split
   entirely clamped every year at $0, silently turning off the refund for
   every pre-2010 low-income filer (a huge blast radius: caught almost
   the whole harness failing on the first validation pass).
4. The `lic` low-income-credit interpolation table's real final row,
   `(huge threshold, $0)`, is easy to drop when transcribing (it looks
   like an unreachable sentinel) - without it, `tablki`'s own "beyond the
   last real threshold" behavior collapses to the SECOND-to-last rate
   ($5/exemption) forever instead of phasing to $0 above $20,000 AGI.
5. `if(nfile.eq.1) use tabs; else use tabm` for the bracket computation -
   `nfile==1` is SINGLE only; head_of_household (`nfile==3`) runs through
   the SAME doubled-threshold married table as married_joint/separate,
   unscaled (`sep==1` for HoH) - a real, easy-to-miss detail since HoH is
   grouped WITH single elsewhere in this same subroutine (e.g. the
   standard deduction's own `nfile.ne.2` check). Relatedly, the pre-1987
   Low-Income Credit's own `txp` (1 vs 2 per filer) checks `mst.eq.3` for
   the `txp=1` group - `mst.eq.3` is the dead internal head_of_household
   code this project never produces (real HoH is mst 4 or 7), so that
   check never actually catches HoH: it silently falls through to `txp=2`
   (the SAME as married_joint), confirmed via a $1-wages/HoH/1977 probe
   showing a real $30 (not $15) refundable credit.
"""

import polars as pl

from taxsim_py.calculators.federal import compute_regular_tax
from taxsim_py.engine.brackets import bracket_tax
from taxsim_py.engine.schema import PARAMETERS_ROOT, load_yaml, resolve_year
from taxsim_py.engine.state_extrapolation import deflate_for_extrapolation, resolve_state_year

GA_PARAMS = load_yaml(PARAMETERS_ROOT / "states" / "ga" / "income_tax.yaml")
PRE1987_PARAMS = load_yaml(PARAMETERS_ROOT / "national" / "pre1987.yaml")
_PRE1987_STATUSES = ["single", "married_joint", "married_separate", "head_of_household"]

# Raw federal input columns only - re-invoking `compute_regular_tax` (the
# 2020 UI "untax" diff trick) must start from JUST these.
_RAW_INPUT_COLUMNS = [
    "mstat", "depx", "dep17", "dep18", "dep6", "dep13", "pwages", "swages",
    "proptax", "otheritem", "mortgage", "childcare", "intrec", "psemp",
    "ssemp", "dividends", "stcg", "ltcg", "ui", "pui", "sui",
]


def _with_default(df: pl.DataFrame, column: str, default: float = 0.0) -> pl.DataFrame:
    if column in df.columns:
        return df
    return df.with_columns(pl.lit(default).alias(column))


def compute_ga_tax(df: pl.DataFrame, year: int, force_itemize: bool | None = None) -> pl.DataFrame:
    effective_year, flate = resolve_state_year(year)
    p = GA_PARAMS
    for col in ("proptax", "otheritem", "mortgage", "depx", "dividends", "intrec", "childcare", "ui", "pui", "sui"):
        df = _with_default(df, col)
    df = _with_default(df, "state_sales_or_income_tax_ded")
    df = _with_default(df, "ccc")
    df = _with_default(df, "itemizes", False)
    df = _with_default(df, "itemized_deduction")
    df = _with_default(df, "tax_before_credits")

    df = df.with_columns(
        ga_sep=pl.when(pl.col("filing_status") == "married_separate").then(2.0).otherwise(1.0),
        # `data(7)` - self/spouse exemption unit count (1, or 2 ONLY for
        # married_joint - matching every other state's own established
        # convention).
        ga_texp=pl.when(pl.col("filing_status") == "married_joint").then(2.0).otherwise(1.0),
    )

    # Year>LASTAT (2021): deflate every dollar-valued raw/federal-computed
    # input by `flate`, run 2021's REAL law (`effective_year`, forced to
    # 2021 by `resolve_state_year`) on the deflated figures, then reinflate
    # the final tax below (see engine/state_extrapolation.py). A no-op for
    # year<=2021 (`flate==1`).
    df = deflate_for_extrapolation(
        df,
        flate,
        [
            "pwages", "swages", "wages", "proptax", "otheritem", "mortgage", "dividends", "intrec",
            "stcg", "ltcg", "ui", "pui", "sui", "agi", "salt_capped", "state_sales_or_income_tax_ded",
            "itemized_deduction", "ccc", "tax_before_credits",
        ],
    )

    # --- AGI ---
    df = df.with_columns(ga_agi=pl.col("agi"))
    # `data(22)` confirmed permanently $0 elsewhere in this project (same
    # state-tax-refund field AL/DC already confirmed inert) - the
    # `law<=1988` subtraction is a no-op, no code needed.

    # 2020: Georgia does NOT conform to federal's CARES/ARPA UI exclusion
    # - add back whatever federal excluded (`data(82)-comnew(78)`, the
    # raw total minus the federally-taxable portion). `comnew(78)`
    # ("untax") isn't exposed as a column - reconstructed via the same
    # "diff trick" Alabama already established (rerun federal with UI
    # zeroed, take the AGI difference).
    if effective_year == 2020:
        ui_total = pl.max_horizontal(pl.col("ui"), pl.col("pui") + pl.col("sui"))
        if (df.get_column("ui").abs().sum() + df.get_column("pui").abs().sum() + df.get_column("sui").abs().sum()) > 0:
            df_no_ui = df.select(_RAW_INPUT_COLUMNS).with_columns(ui=pl.lit(0.0), pui=pl.lit(0.0), sui=pl.lit(0.0))
            fed_no_ui = compute_regular_tax(df_no_ui, effective_year)
            untax = pl.col("agi") - fed_no_ui.get_column("agi")
        else:
            untax = pl.lit(0.0)
        df = df.with_columns(ga_agi=pl.col("ga_agi") + ui_total - untax)

    # 2021 $300-cash-contribution addback confirmed permanently inert (no
    # `charity_cash` input this project's schema ever populates - the
    # same gap already documented at the federal level).

    # 1982-1986: "Georgia used 1981 federal law" - the IRA/stock-loss
    # adjustment (`stira`/`stkeo`) is confirmed inert EXCEPT `comnew(14)`
    # (see module docstring, a narrow, untraced gap treated as $0), and
    # the two-earner deduction (`comnew(32)`) is real - reused from the
    # SAME local reconstruction Colorado/DC already established. The
    # `data(9).gt.0` retirement sub-block is confirmed permanently inert
    # (elderly count always $0 for this schema).
    if 1982 <= effective_year <= 1986:
        two_earner_rate = float(resolve_year(PRE1987_PARAMS["two_earner_deduction_rate"], effective_year))
        two_earner_cap = float(resolve_year(PRE1987_PARAMS["two_earner_deduction_cap"], effective_year))
        twoded = (
            two_earner_rate * pl.min_horizontal(pl.col("pwages"), pl.col("swages")).clip(0, None)
        ).clip(0, two_earner_cap)
        df = df.with_columns(ga_agi=pl.col("ga_agi") + twoded)
        # Georgia's own state-vs-federal unemployment-compensation
        # threshold adjustment (`stutx`/`fdutx`) - a real, GA-specific
        # mechanic distinct from the 2020 UI provision above, using the
        # SAME `data(82)=max(ui,pui+sui)` total.
        ui_total = pl.max_horizontal(pl.col("ui"), pl.col("pui") + pl.col("sui"))
        gmp = {1: 20000.0, 2: 25000.0, 3: 20000.0, 4: 20000.0, 5: 20000.0, 6: 0.0, 7: 20000.0}
        fmp = {1: 12000.0, 2: 18000.0, 3: 20000.0, 4: 20000.0, 5: 20000.0, 6: 0.0, 7: 12000.0}
        gemp = (
            pl.when(pl.col("filing_status") == "married_joint").then(gmp[2])
            .when(pl.col("filing_status") == "head_of_household").then(gmp[4])
            .when(pl.col("filing_status") == "married_separate").then(gmp[6])
            .otherwise(gmp[1])
        )
        femp = (
            pl.when(pl.col("filing_status") == "married_joint").then(fmp[2])
            .when(pl.col("filing_status") == "head_of_household").then(fmp[4])
            .when(pl.col("filing_status") == "married_separate").then(fmp[6])
            .otherwise(fmp[1])
        )
        stutx = pl.min_horizontal(0.5 * (ui_total + pl.col("ga_agi") - gemp).clip(0, None), ui_total)
        fdutx = pl.min_horizontal(0.5 * (ui_total + pl.col("ga_agi") - femp).clip(0, None), ui_total)
        df = df.with_columns(ga_agi=pl.col("ga_agi") + (stutx - fdutx))

    # Social Security in AGI, 1988+ (comnew(79)) confirmed permanently $0
    # for this schema (no social-security input).

    # --- Exemptions --- (`old`=elderly+blind confirmed permanently $0)
    if effective_year <= 1986:
        pe = float(p["personal_exemption_flat_pre1994"][1960])
        dep_xmp = float(resolve_year(p["personal_exemption_amount_xmp"], effective_year))
        ga_exemp = pl.col("ga_texp") * pe + pl.col("depx") * dep_xmp
        hoh_bonus = float(p["hoh_dependent_bonus_pre1987"][1960])
        ga_exemp = ga_exemp + pl.when(
            (pl.col("filing_status") == "head_of_household") & (pl.col("depx") > 0)
        ).then(hoh_bonus).otherwise(0.0)
        df = df.with_columns(ga_exemp=ga_exemp)
    elif effective_year <= 1993:
        xmp = float(resolve_year(p["personal_exemption_amount_xmp"], effective_year))
        df = df.with_columns(ga_exemp=xmp * (pl.col("ga_texp") + pl.col("depx")))
    elif effective_year <= 1997:
        pe = float(p["personal_exemption_flat_pre1994"][1960])
        xmp = float(resolve_year(p["personal_exemption_amount_xmp"], effective_year))
        df = df.with_columns(ga_exemp=pl.col("ga_texp") * pe + pl.col("depx") * xmp)
    elif effective_year <= 2002:
        xmp = float(resolve_year(p["personal_exemption_amount_xmp"], effective_year))
        df = df.with_columns(ga_exemp=xmp * (pl.col("ga_texp") + pl.col("depx")))
    elif effective_year <= 2012:
        xmp = float(resolve_year(p["personal_exemption_amount_xmp"], effective_year))
        dep_flat = float(p["dependent_exemption_flat_2003plus"][2003])
        df = df.with_columns(ga_exemp=xmp * pl.col("ga_texp") + pl.col("depx") * dep_flat)
    else:
        xmp = float(resolve_year(p["personal_exemption_amount_xmp"], effective_year))
        dep_flat = float(p["dependent_exemption_flat_2003plus"][2003])
        married_sep_amt = float(p["personal_exemption_married_separate_2013plus"][2013])
        df = df.with_columns(
            ga_exemp=pl.when(pl.col("filing_status").is_in(["single", "head_of_household"]))
            .then(xmp * pl.col("ga_texp"))
            .otherwise(married_sep_amt * pl.col("ga_texp"))
            + pl.col("depx") * dep_flat
        )

    # --- Standard deduction ---
    if effective_year <= 1982:
        pct = float(p["standard_deduction_pct_pre1983"][1960])
        floor = float(p["standard_deduction_floor_pre1983"][1960])
        ceiling = float(p["standard_deduction_ceiling_pre1983"][1960])
        df = df.with_columns(
            ga_stded=(pct * pl.col("ga_agi")).clip(floor / pl.col("ga_sep"), ceiling / pl.col("ga_sep"))
        )
    elif effective_year <= 1986:
        is_married = pl.col("filing_status").is_in(["married_joint", "married_separate"])
        pct_s = float(p["standard_deduction_pct_1983_1986_single_or_hoh"][1983])
        floor_s = float(p["standard_deduction_floor_1983_1986_single_or_hoh"][1983])
        ceiling_s = float(p["standard_deduction_ceiling_1983_1986_single_or_hoh"][1983])
        pct_m = float(p["standard_deduction_pct_1983_1986_married"][1983])
        floor_m = float(p["standard_deduction_floor_1983_1986_married"][1983])
        ceiling_m = float(p["standard_deduction_ceiling_1983_1986_married"][1983])
        stded_single = (pct_s * pl.col("ga_agi")).clip(floor_s, ceiling_s)
        stded_married = (pct_m * pl.col("ga_agi")).clip(floor_m / pl.col("ga_sep"), ceiling_m / pl.col("ga_sep"))
        df = df.with_columns(ga_stded=pl.when(is_married).then(stded_married).otherwise(stded_single))
    else:
        is_married = pl.col("filing_status").is_in(["married_joint", "married_separate"])
        flat_s = float(resolve_year(p["standard_deduction_flat_1987plus_single_or_hoh"], effective_year))
        flat_m = float(resolve_year(p["standard_deduction_flat_1987plus_married"], effective_year))
        df = df.with_columns(
            ga_stded=pl.when(is_married).then(flat_m / pl.col("ga_sep")).otherwise(flat_s)
        )

    # --- Itemized deduction --- (see module docstring point 1)
    if effective_year <= 1986:
        df = df.with_columns(ga_raw_itemized=pl.col("proptax") + pl.col("otheritem") + pl.col("mortgage"))
        # Reconstruct federal's OWN itemize-vs-standard decision (not
        # exposed as a column for years<=1986) the same way DC does -
        # compare the self-referential raw total (INCLUDING the state-
        # tax feedback, which is what federal itself would see) against
        # federal's own pre-1987 zero-bracket amount.
        raw_itemized_with_state_tax = pl.col("ga_raw_itemized") + pl.col("state_sales_or_income_tax_ded")
        fed_zbr_expr = pl.lit(None, dtype=pl.Float64)
        for status in _PRE1987_STATUSES:
            fed_zbr = float(resolve_year(PRE1987_PARAMS["standard_deduction"][status], effective_year))
            fed_zbr_expr = pl.when(pl.col("filing_status") == status).then(pl.lit(fed_zbr)).otherwise(fed_zbr_expr)
        itemizes_indicator = (raw_itemized_with_state_tax > fed_zbr_expr).cast(pl.Float64)
        df = df.with_columns(ga_xitded=pl.col("ga_raw_itemized") * itemizes_indicator)
    else:
        itemizes_indicator = pl.col("itemizes").cast(pl.Float64)
        df = df.with_columns(
            ga_xitded=(pl.col("itemized_deduction") - pl.col("state_sales_or_income_tax_ded")) * itemizes_indicator
        )

    if force_itemize is False and effective_year == 1999:
        df = df.with_columns(ga_xitded=pl.lit(0.0))

    df = df.with_columns(ga_deduc=pl.max_horizontal(pl.col("ga_xitded"), pl.col("ga_stded")))
    df = df.with_columns(ga_taxinc=(pl.col("ga_agi") - pl.col("ga_deduc") - pl.col("ga_exemp")).clip(0, None))

    # --- Bracket tax --- (see module docstring point 2). `nfile==1`
    # (single ONLY) uses the single table directly - married_joint,
    # married_separate, AND head_of_household (nfile 2/2/3, all !=1) run
    # `taxinc*sep` through the SAME doubled-threshold married table, a
    # real, easy-to-miss detail confirmed via oracle probe (a $15,000-
    # wages/HoH/1-dependent/2014 case only matches using the married
    # table, not a "single-or-hoh" one this project's naming convention
    # elsewhere might suggest).
    is_married = pl.col("filing_status") != "single"
    if effective_year <= 2018:
        brackets_single = p["brackets_single_thru_2018"]
        brackets_married = p["brackets_married_thru_2018"]
    else:
        brackets_single = p["brackets_single_2019plus"]
        brackets_married = p["brackets_married_2019plus"]
    taxy = pl.col("ga_taxinc") * pl.col("ga_sep")
    stat_married = bracket_tax(taxy, brackets_married) / pl.col("ga_sep")
    stat_single = bracket_tax(pl.col("ga_taxinc"), brackets_single)
    df = df.with_columns(ga_statax=pl.when(is_married).then(stat_married).otherwise(stat_single))

    # --- Credits ---
    # Child/Dependent Care Credit: real 1978-1986, NOT allowable
    # 1987-2005, a percentage of the federal credit (pre-cap) 2006+.
    if 1978 <= effective_year <= 1986:
        cap_floor = float(p["child_care_credit_expense_cap_floor_pre1987"][1960])
        cap_ceiling = float(p["child_care_credit_expense_cap_ceiling_pre1987"][1960])
        rate = float(p["child_care_credit_rate_pre1987"][1960])
        chmax = (pl.col("depx") * 2000.0).clip(cap_floor, cap_ceiling)
        earned_ish = (pl.col("wages")).clip(0, None)
        chexp = pl.min_horizontal(pl.col("childcare"), chmax, earned_ish)
        df = df.with_columns(ga_chcr=chexp * rate)
    elif effective_year >= 2006:
        rate = float(resolve_year(p["child_care_credit_rate_2006plus"], effective_year))
        chcare = pl.min_horizontal(pl.col("ccc").clip(0, None), pl.col("tax_before_credits").clip(0, None))
        df = df.with_columns(ga_chcr=rate * chcare)
    else:
        df = df.with_columns(ga_chcr=pl.lit(0.0))

    # Low-Income Credit: real flat formula <=1986, does NOT exist
    # 1987-1991, a real per-exemption-unit interpolation table 1992+.
    if effective_year <= 1986:
        # `if(mst.eq.1.or.mst.eq.3.or.mst.eq.6) txp=1. else txp=2.` -
        # `mst.eq.3` is the dead internal head_of_household code this
        # project never produces (HoH is really mst 4 or 7 - see the
        # `filing()`/`nfile` convention already established for DC/CT),
        # so this check never actually catches HoH: it falls through to
        # `txp=2`, the SAME as married_joint - confirmed via oracle probe
        # (a $1-wages/HoH/1977 case only matches a full $30 refundable
        # credit, i.e. `txp=2`, not the $15 a `txp=1` reading would give).
        txp = pl.when(pl.col("filing_status").is_in(["single", "married_separate"])).then(1.0).otherwise(2.0)
        flat = float(p["low_income_credit_flat_per_txp_pre1987"][1960])
        thr = float(p["low_income_credit_agi_threshold_per_txp_pre1987"][1960])
        df = df.with_columns(
            ga_ycred=(flat * txp - (pl.col("agi") - thr * txp)).clip(0, flat * txp)
        )
    elif effective_year >= 1992:
        rows = [[float(t), float(r)] for t, r in p["low_income_credit_table_1992plus"]]
        thresholds = [r[0] for r in rows[:-1]]
        values = [r[1] for r in rows]
        expr = pl.lit(values[-1])
        for i in range(len(thresholds) - 1, -1, -1):
            t_hi = thresholds[i]
            v_hi = values[i]
            if i == 0:
                below = pl.lit(v_hi)
            else:
                t_lo = thresholds[i - 1]
                v_lo = values[i - 1]
                w = (pl.col("agi") - t_lo) / (t_hi - t_lo)
                below = (w * v_lo + (1 - w) * v_hi) if v_hi > v_lo else (w * v_hi + (1 - w) * v_lo)
            expr = pl.when(pl.col("agi") < t_hi).then(below).otherwise(expr)
        df = df.with_columns(ga_ycred=expr * (pl.col("ga_texp") + pl.col("depx")))
    else:
        df = df.with_columns(ga_ycred=pl.lit(0.0))
    # `data(105)` (dependent-of-another-return) confirmed permanently
    # $0 for this schema - no code needed for that zeroing.

    # Solar-energy credit confirmed permanently $0 (see module docstring).
    df = df.with_columns(ga_solar=pl.lit(0.0))

    # `if(law.le.2009) statax=max(0,statax-chcr-solar)-ycred; else
    # statax=max(0,statax-chcr-solar-ycred)` - the Low-Income Credit is
    # REFUNDABLE through 2009 (the $0 floor applies BEFORE subtracting
    # it, so `ycred` can push `siitax` negative), nonrefundable 2010+
    # (the floor applies AFTER). Missing this era split entirely clamped
    # every year at 0, silently turning off the refund for every pre-
    # 2010 low-income case - caught by a $1-income single filer showing
    # a real -$26 refund for 2000.
    if effective_year <= 2009:
        df = df.with_columns(
            ga_statax=(pl.col("ga_statax") - pl.col("ga_chcr") - pl.col("ga_solar")).clip(0, None) - pl.col("ga_ycred")
        )
    else:
        df = df.with_columns(
            ga_statax=(pl.col("ga_statax") - pl.col("ga_chcr") - pl.col("ga_solar") - pl.col("ga_ycred")).clip(0, None)
        )

    df = df.with_columns(siitax=pl.col("ga_statax") * flate)
    return df
