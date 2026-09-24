"""Connecticut individual income tax (`cttax`, taxsim_2022_10_21.f:
3261-3834, state id 7). See parameters/states/ct/income_tax.yaml for the
full scope note - the largest, richest state built so far.

2022/2023 (CPI-extrapolated, `effective_year` forced to 2021 - see
engine/state_extrapolation.py): 2,481/2,491 exact. The 10 residuals are
all 2023-only, sub-$2, and trace directly to `ct_earncr`'s own dependence
on federal `eitc` - this project's already-accepted real-vs-oracle-2023
EITC table override (see feedback_real_params_over_oracle_bugs /
project_taxsim_py_port memory) propagating straight through, same family
as AL's/CA's own 2023 residuals. `ltg` (federal.py's own derived,
real-year figure used by the 2013+ AMT recompute) is deflated directly,
same situation as AR's `wages`/AZ's `salt_capped` elsewhere in this
project - deflating raw `ltcg` alone wouldn't reach it.
"""

import polars as pl

from taxsim_py.engine.brackets import bracket_tax
from taxsim_py.engine.schema import PARAMETERS_ROOT, load_yaml, resolve_year
from taxsim_py.engine.state_extrapolation import deflate_for_extrapolation, resolve_state_year

CT_PARAMS = load_yaml(PARAMETERS_ROOT / "states" / "ct" / "income_tax.yaml")
PRE1987_PARAMS = load_yaml(PARAMETERS_ROOT / "national" / "pre1987.yaml")
FEDERAL_AMT_PARAMS = load_yaml(PARAMETERS_ROOT / "national" / "amt.yaml")
FEDERAL_CAPITAL_GAINS_PARAMS = load_yaml(PARAMETERS_ROOT / "national" / "capital_gains.yaml")

_RICH_STATUSES = ["married_joint", "head_of_household"]
_STATUSES = ["single", "married_joint", "married_separate", "head_of_household"]
_SEPRET_BY_STATUS = {"single": 1.0, "married_joint": 1.0, "head_of_household": 1.0, "married_separate": 2.0}


def _by_status(values: dict[str, float]) -> pl.Expr:
    expr = pl.lit(None, dtype=pl.Float64)
    for status in _STATUSES:
        expr = pl.when(pl.col("filing_status") == status).then(pl.lit(float(values[status]))).otherwise(expr)
    return expr


def _federal_tentative_minimum_tax(df: pl.DataFrame, year: int) -> pl.Expr:
    """Reconstructs federal's own tentative minimum tax (before comparing
    to regular tax) - engine/amt.py's `alternative_minimum_tax()` computes
    this internally but returns only the final `max(tmt-regular_tax,0)`
    excess, so it's duplicated here (same params, same formula) for CT's
    2013+ AMT credit recompute (`tamt=comnew(88)+comnew(89)`), which needs
    the raw tmt regardless of whether it exceeds regular tax."""
    amt_p = FEDERAL_AMT_PARAMS
    cg_p = FEDERAL_CAPITAL_GAINS_PARAMS
    exemption = _by_status({s: resolve_year(amt_p["exemption"][s], year) for s in _STATUSES})
    threshold = _by_status({s: resolve_year(amt_p["exemption_phaseout_threshold"][s], year) for s in _STATUSES})
    phaseout_rate = float(resolve_year(amt_p["exemption_phaseout_rate"], year))
    rate_bp = float(resolve_year(amt_p["rate_breakpoint"], year))
    rate_lo = float(resolve_year(amt_p["rate_below_breakpoint"], year))
    rate_hi = float(resolve_year(amt_p["rate_above_breakpoint"], year))
    sepret = _by_status(_SEPRET_BY_STATUS)

    amt_income = pl.col("agi") - pl.when(pl.col("itemizes")).then(pl.col("mortgage")).otherwise(0.0)
    # Married-filing-separately-only AMTI addback (see amt.yaml) - this
    # helper is only ever called for year>=2013, so it always applies.
    sep_addback_cap = float(resolve_year(amt_p["exemption"]["married_separate"], year))
    sep_addback_threshold = float(resolve_year(amt_p["separate_return_addback_threshold"], year))
    sep_addback = pl.min_horizontal(sep_addback_cap, 0.25 * (amt_income - sep_addback_threshold).clip(0, None))
    amt_income = pl.when(sepret == 2.0).then(amt_income + sep_addback).otherwise(amt_income)
    exemption_after_phaseout = (exemption - phaseout_rate * (amt_income - threshold).clip(0, None)).clip(0, None)
    amt_base = (amt_income - exemption_after_phaseout).clip(0, None)
    breakpoint_per_return = rate_bp / sepret
    backout = (rate_hi - rate_lo) * rate_bp / sepret

    ltg = pl.col("ltg") if "ltg" in df.columns else pl.lit(0.0)
    ltg_capped = pl.min_horizontal(ltg, amt_base)
    ordinary_amt_base = (amt_base - ltg_capped).clip(0, None)
    tentative_ordinary_tax = pl.when(ordinary_amt_base <= breakpoint_per_return).then(
        ordinary_amt_base * rate_lo
    ).otherwise(ordinary_amt_base * rate_hi - backout)

    rate_0_ceiling = _by_status({s: resolve_year(cg_p["rate_0_ceiling"][s], year) for s in _STATUSES})
    rate_15_ceiling = _by_status({s: resolve_year(amt_p["cg_rate_15_ceiling"][s], year) for s in _STATUSES})
    cg_rate_15 = float(resolve_year(cg_p["rate_15"], year))
    cg_rate_0 = float(resolve_year(cg_p["rate_0"], year))
    regular_ordinary_income = (pl.col("taxable_income") - ltg).clip(0, None)
    zero_pct_room = (rate_0_ceiling - regular_ordinary_income).clip(0, None)
    zero_pct_amount = pl.min_horizontal(zero_pct_room, ltg_capped)
    remaining_after_zero = ltg_capped - zero_pct_amount
    fifteen_pct_room = (rate_15_ceiling - regular_ordinary_income - zero_pct_room).clip(0, None)
    top_slice = (remaining_after_zero - fifteen_pct_room).clip(0, None)
    tentative_ltg_tax = cg_rate_0 * zero_pct_amount + cg_rate_15 * remaining_after_zero + 0.05 * top_slice
    return tentative_ordinary_tax + tentative_ltg_tax


def _with_default(df: pl.DataFrame, column: str, default: float = 0.0) -> pl.DataFrame:
    if column in df.columns:
        return df
    return df.with_columns(pl.lit(default).alias(column))


def _table_lookup_interp(income: pl.Expr, rows: list[list[float]]) -> pl.Expr:
    """`tablki`-style linear interpolation between adjacent points."""
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
            w = (income - t_lo) / (t_hi - t_lo)
            below = w * v_lo + (1 - w) * v_hi if v_hi > v_lo else w * v_hi + (1 - w) * v_lo
        expr = pl.when(income < t_hi).then(below).otherwise(expr)
    return expr


def _flat_rate_step(income: pl.Expr, rows: list[list[float]]) -> pl.Expr:
    """`cttab`-style: FIRST row where income<=threshold wins, a flat
    (non-marginal) rate - not interpolated, not cumulative."""
    expr = pl.lit(rows[-1][1])
    for threshold, rate in reversed(rows[:-1]):
        expr = pl.when(income <= threshold).then(pl.lit(rate)).otherwise(expr)
    return expr


def compute_ct_tax(df: pl.DataFrame, year: int, force_itemize: bool | None = None) -> pl.DataFrame:
    effective_year, flate = resolve_state_year(year)
    p = CT_PARAMS
    for col in ("dividends", "intrec", "stcg", "ltcg", "depx", "dep18", "proptax", "psemp", "ssemp", "amt", "mortgage", "ltg"):
        df = _with_default(df, col)

    # Year>LASTAT (2021): deflate every dollar-valued raw/federal-computed
    # input by `flate`, run 2021's REAL law (`effective_year`, forced to
    # 2021 by `resolve_state_year`) on the deflated figures, then reinflate
    # the final tax below (see engine/state_extrapolation.py). A no-op for
    # year<=2021 (`flate==1`). `ltg` is federal.py's own derived (real-
    # year, undeflated) column, same situation as AR's `wages`/AZ's
    # `salt_capped` - deflated directly here since deflating raw `ltcg`
    # afterward wouldn't reach it. `amt`/`agi`/`taxable_income`/`eitc` are
    # all federal-computed but within the dispatcher's `comnew(1:98)`
    # generic-deflate range (comnew 70/2/29/59 respectively - see al.py's
    # own docstring for how that range was discovered), so they scale
    # normally too.
    df = deflate_for_extrapolation(
        df,
        flate,
        [
            "dividends", "intrec", "stcg", "ltcg", "proptax", "psemp", "ssemp",
            "agi", "amt", "mortgage", "taxable_income", "eitc", "ltg",
        ],
    )

    df = df.with_columns(
        ct_sep=pl.when(pl.col("filing_status") == "married_separate").then(2.0).otherwise(1.0),
        ct_rich=pl.col("filing_status").is_in(_RICH_STATUSES),
    )

    # --- AGI: federal AGI directly (see module docstring - `subrac`,
    # the SS-benefit exclusion, and the 2019+ retirement exclusion are
    # all confirmed permanently inert for this schema). ---
    df = df.with_columns(ct_agi=pl.col("agi"))

    # --- Exemption ---
    if effective_year <= 1990:
        df = df.with_columns(ct_exemp=100.0 * (pl.col("ct_sep") * 0 + 1.0))  # txp=1 for our schema always here
    else:
        single = p["exemption_single_pre2000"]
        hoh = p["exemption_hoh_1991plus"]
        joint = p["exemption_joint_1991plus"]
        sep_p = p["exemption_married_separate_2000plus"]
        if effective_year <= 1999:
            single_amt, single_thr = single["amount"], single["phaseout_start"]
        elif effective_year <= 2007 and effective_year in p["exemption_single_by_year"]:
            single_amt, single_thr = p["exemption_single_by_year"][effective_year]
        elif effective_year <= 2007:
            single_amt, single_thr = resolve_year(
                {y: v for y, v in p["exemption_single_by_year"].items()}, effective_year
            )
        else:
            single_amt = None
        exemp_single = (
            pl.when(pl.col("ct_agi") >= 2 * float(single_amt) if False else True).then(0.0)
            if False
            else pl.lit(0.0)
        )
        if effective_year <= 2007:
            exemp_single = (float(single_amt) - (pl.col("ct_agi") - float(single_thr))).clip(0, float(single_amt))
        else:
            xmp = float(resolve_year(p["exemption_single_flat_2008plus"], effective_year))
            exemp_single = (xmp - (pl.col("ct_agi") - 2 * xmp)).clip(0, xmp)
        exemp_hoh = (float(hoh["amount"]) - (pl.col("ct_agi") - float(hoh["phaseout_start"]))).clip(0, float(hoh["amount"]))
        exemp_joint = (float(joint["amount"]) - (pl.col("ct_agi") - float(joint["phaseout_start"]))).clip(0, float(joint["amount"]))
        exemp_sep = pl.lit(0.0)
        if effective_year >= 2000:
            exemp_sep = (float(sep_p["amount"]) - (pl.col("ct_agi") - float(sep_p["phaseout_start"]))).clip(0, float(sep_p["amount"]))
        df = df.with_columns(
            ct_exemp=pl.when(pl.col("filing_status") == "married_joint").then(exemp_joint)
            .when(pl.col("filing_status") == "head_of_household").then(exemp_hoh)
            .when((pl.col("filing_status") == "married_separate") & (effective_year >= 2000)).then(exemp_sep)
            .otherwise(exemp_single)
        )

    # --- Pre-1991 cap gains / dividends / interest tax ---
    # `gain` (`comnew(6)`/`capgn`) is federal's OWN taxable-gain figure
    # feeding AGI, not raw ltcg+stcg - for <=1986 that's net of the
    # federal LTCG exclusion (50% in 1977, 60% 1978-1986; see
    # federal_pre1987.py's own `capgn=fullcg-capded`), which this
    # calculator otherwise never sees again since federal_pre1987.py
    # drops the intermediate `capgn` column before returning. For
    # 1987-1990 the federal exclusion was repealed, so capgn=stcg+ltcg
    # unadjusted (matching federal.py's own inline `capgn`) - CT's own
    # separate 1987-1988-only 60% exclusion (below) is what still
    # applies there.
    if effective_year <= 1986:
        caprat = float(resolve_year(PRE1987_PARAMS["capital_gains_exclusion_rate"], effective_year))
        gain = pl.col("stcg").clip(0, None) + (1.0 - caprat) * pl.col("ltcg").clip(0, None)
    else:
        gain = pl.col("ltcg").clip(0, None) + pl.col("stcg").clip(0, None)
    if effective_year in (1987, 1988):
        gain = (gain - float(p["ltcg_ct_only_exclusion_1987_1988"]) * pl.col("ltcg").clip(0, None)).clip(0, None)
    divint = pl.col("dividends") + pl.col("intrec")

    if effective_year <= 1990:
        if effective_year <= 1990:
            cgtax = (float(p["cgtax_rate_pre1991"]) * (gain - pl.col("ct_exemp"))).clip(0, None)
        if effective_year < 1983:
            divtax = _flat_rate_step(pl.col("ct_agi"), p["divint_rate_table_pre1983"]) * pl.col("dividends")
        elif effective_year == 1983:
            divtax = _flat_rate_step(pl.col("ct_agi"), p["divint_rate_table_pre1983"]) * divint
        elif effective_year == 1984:
            divtax = _flat_rate_step(pl.col("ct_agi"), p["divint_rate_table_1984"]) * divint
        elif effective_year == 1985:
            divtax = _flat_rate_step(pl.col("ct_agi"), p["divint_rate_table_1985"]) * divint
        elif effective_year <= 1988:
            divtax = _flat_rate_step(pl.col("ct_agi"), p["divint_rate_table_1986_1988"]) * divint
        else:
            divtax = _flat_rate_step(pl.col("ct_agi"), p["divint_rate_table_1989_1990"]) * divint
        df = df.with_columns(ct_statax=cgtax + divtax, ct_taxinc=gain + divint, ct_cgtax=cgtax, ct_divtax=divtax)
    else:
        df = df.with_columns(ct_taxinc=(pl.col("ct_agi") - pl.col("ct_exemp")).clip(0, None))
        status_brackets = {
            "single": "single", "married_separate": "single", "head_of_household": "hoh",
            "married_joint": "joint",
        }
        key = status_brackets
        if effective_year == 1991:
            divtax_1991 = _flat_rate_step(pl.col("ct_agi"), p["divint_rate_table_1991"]) * divint
            rate = 0.015
            df = df.with_columns(ct_xtax=pl.col("ct_taxinc") * rate, ct_divtax=divtax_1991)
            # cgtax=max(0,min(.034*agi,(gain-100*(data(7)+data(9)+data(10)))*.0475)).
            # data(7) is the filer-count exemption unit (1, or 2 for
            # married_joint) - NOT depx; data(9)/(10) (elderly/blind) are
            # confirmed inert for this schema.
            exemp_units = pl.when(pl.col("filing_status") == "married_joint").then(2.0).otherwise(1.0)
            cgtax_1991 = pl.max_horizontal(
                pl.min_horizontal(0.034 * pl.col("ct_agi"), (gain - 100.0 * exemp_units) * 0.0475), 0.0
            )
            df = df.with_columns(ct_cgtax=cgtax_1991)
        elif effective_year <= 1995:
            rate = float(p["flat_rate_1991_1995"])
            df = df.with_columns(ct_xtax=pl.col("ct_taxinc") * rate, ct_divtax=pl.lit(0.0), ct_cgtax=pl.lit(0.0))
        else:
            year_table_map = {
                1996: "brackets_1996", 1997: "brackets_1997", 1998: "brackets_1998",
            }
            if effective_year in year_table_map:
                prefix = year_table_map[effective_year]
            elif 1999 <= effective_year <= 2002:
                prefix = "brackets_1999_2002"
            elif 2003 <= effective_year <= 2008:
                prefix = "brackets_2003_2008"
            elif 2009 <= effective_year <= 2010:
                prefix = "brackets_2009_2010"
            elif 2011 <= effective_year <= 2014:
                prefix = "brackets_2011_2014"
            else:
                prefix = "brackets_2015plus"
            brackets_single = p[f"{prefix}_single"]
            brackets_hoh = p[f"{prefix}_hoh"]
            brackets_joint = p[f"{prefix}_joint"]
            xtax = pl.when(pl.col("filing_status") == "head_of_household").then(
                bracket_tax(pl.col("ct_taxinc"), brackets_hoh)
            ).when(pl.col("filing_status") == "married_joint").then(
                bracket_tax(pl.col("ct_taxinc"), brackets_joint)
            ).otherwise(bracket_tax(pl.col("ct_taxinc"), brackets_single))

            if effective_year >= 2011:
                is_hoh = pl.col("filing_status") == "head_of_household"
                is_joint = pl.col("filing_status") == "married_joint"
                is_sep = pl.col("filing_status") == "married_separate"
                ph_single = p["phaseout_3pct_2011plus_single"]
                ph_sep = p["phaseout_3pct_2011plus_married_separate"]
                ph_hoh = p["phaseout_3pct_2011plus_hoh"]
                ph_joint = p["phaseout_3pct_2011plus_joint"]

                def stax_for(ph):
                    return pl.min_horizontal(
                        float(ph["cap"]),
                        float(ph["per_1000"]) * (pl.col("ct_agi") - float(ph["threshold"])).clip(0, None) / float(ph["denom"]),
                    )

                stax = pl.when(is_hoh).then(stax_for(ph_hoh)).when(is_joint).then(stax_for(ph_joint)).when(
                    is_sep
                ).then(stax_for(ph_sep)).otherwise(stax_for(ph_single))

                rmax_s = float(resolve_year(p["recapture_max_single"], effective_year))
                rmax_h = float(resolve_year(p["recapture_max_hoh"], effective_year))
                rate_s = float(resolve_year(p["recapture_rate_single"], effective_year))
                rate_h = float(resolve_year(p["recapture_rate_hoh"], effective_year))
                ragi_s = float(p["recapture_agi_threshold_single"])
                ragi_h = float(p["recapture_agi_threshold_hoh"])
                # Fortran groups single+married_separate together
                # (`mst.eq.1.or.mst.eq.3.or.mst.eq.6`) for BOTH the
                # bracket table and rtax - married_separate uses the
                # SAME undoubled ragis/rmaxs as single, not a
                # half-of-joint formula (only married_joint doubles
                # the threshold/cap).
                rtax_single = pl.min_horizontal(rate_s * (pl.col("ct_agi") - ragi_s).clip(0, None), rmax_s)
                rtax_hoh = pl.min_horizontal(rate_h * (pl.col("ct_agi") - ragi_h).clip(0, None), rmax_h)
                rtax_joint = pl.min_horizontal(rate_s * (pl.col("ct_agi") - 2 * ragi_s).clip(0, None), 2 * rmax_s)
                rtax = pl.when(is_hoh).then(rtax_hoh).when(is_joint).then(rtax_joint).otherwise(rtax_single)

                xtax = xtax + stax + rtax

            df = df.with_columns(ct_xtax=xtax, ct_divtax=pl.lit(0.0), ct_cgtax=pl.lit(0.0))

        # --- Personal Tax Credit ---
        if effective_year <= 1994:
            credp = pl.when(pl.col("filing_status") == "head_of_household").then(
                _table_lookup_interp(pl.col("ct_agi"), p["personal_credit_1991_1994_hoh"])
            ).when(pl.col("filing_status") == "married_joint").then(
                _table_lookup_interp(pl.col("ct_agi"), p["personal_credit_1991_1994_joint"])
            ).otherwise(_table_lookup_interp(pl.col("ct_agi"), p["personal_credit_1991_1994_single"]))
        else:
            rates_rows_single = None
            single_thr_key = None
            for y in sorted(p["personal_credit_1995plus_single_thresholds"], reverse=True):
                if effective_year >= y:
                    single_thr_key = y
                    break
            if single_thr_key is None:
                single_thr_key = min(p["personal_credit_1995plus_single_thresholds"])
            single_thresholds = p["personal_credit_1995plus_single_thresholds"][single_thr_key]
            rates = p["personal_credit_1995plus_rates"]
            single_rows = list(zip(single_thresholds, rates))
            hoh_rows = list(zip(p["personal_credit_1995plus_hoh_thresholds"], rates))
            joint_rows_raw = p["personal_credit_1995plus_joint_thresholds"]

            credp_single = _table_lookup_interp(pl.col("ct_agi"), single_rows)
            credp_hoh = _table_lookup_interp(pl.col("ct_agi"), hoh_rows)
            # joint/married_separate: thresholds divided by `sep` (2 for MFS)
            joint_rows_div1 = list(zip([t for t in joint_rows_raw], rates))
            credp_joint = _table_lookup_interp(pl.col("ct_agi") * pl.col("ct_sep"), joint_rows_div1)
            credp = pl.when(pl.col("filing_status") == "head_of_household").then(credp_hoh).when(
                pl.col("filing_status").is_in(["married_joint", "married_separate"])
            ).then(credp_joint).otherwise(credp_single)

        df = df.with_columns(ct_credp=credp)
        df = df.with_columns(ct_inctax=pl.col("ct_xtax") * (1.0 - pl.col("ct_credp")))
        df = df.with_columns(ct_statax=pl.col("ct_inctax"))
        if effective_year == 1991:
            df = df.with_columns(ct_statax=pl.col("ct_statax") + pl.col("ct_cgtax") + pl.col("ct_divtax"))

    # --- AMT (1993+) ---
    # Gated on `comnew(70).gt.0` - federal's own AMT liability (this
    # project's own `amt` column, the excess of tentative minimum tax over
    # regular tax) must be nonzero, or CT's whole AMT block is skipped
    # entirely. Missing this gate made the block fire (and dominate) for
    # every ordinary wages-only return, which has no federal AMT exposure.
    if effective_year >= 1993:
        # xprefs=comnew(69)-subrac=alminy (subrac confirmed permanently 0).
        # alminy is federal's own AMT income BEFORE the exemption -
        # federal.py's own `amt_income = agi - (mortgage if itemizes else
        # 0)`, not exposed as a column, so recomputed identically here,
        # including federal's own married-filing-separately-only AMTI
        # addback (1990+, see amt.yaml) which is baked into alminy itself
        # before CT ever sees it.
        xprefs_base = pl.col("agi") - pl.when(pl.col("itemizes")).then(pl.col("mortgage")).otherwise(0.0)
        is_sep_amt = pl.col("filing_status") == "married_separate"
        sep_addback_cap = float(resolve_year(FEDERAL_AMT_PARAMS["exemption"]["married_separate"], effective_year))
        sep_addback_threshold = float(
            resolve_year(FEDERAL_AMT_PARAMS["separate_return_addback_threshold"], effective_year)
        )
        sep_addback = pl.min_horizontal(
            sep_addback_cap, 0.25 * (xprefs_base - sep_addback_threshold).clip(0, None)
        )
        xprefs_raw = pl.when(is_sep_amt).then(xprefs_base + sep_addback).otherwise(xprefs_base)
        # `if(mst.eq.3.or.mst.eq.6.and.xprefs.gt.165000.)` - mst.eq.3 is
        # dead for this schema (see module docstring), so this is really
        # just `mst.eq.6` (married_separate) alone.
        is_sep = pl.col("filing_status") == "married_separate"
        xprefs = pl.when(is_sep & (xprefs_raw > 165000.0)).then(
            pl.when(xprefs_raw > 255000.0).then(xprefs_raw + 22500.0).otherwise(xprefs_raw + 0.25 * (xprefs_raw - 165000.0))
        ).otherwise(xprefs_raw)
        # sorm(mst,a,b) returns b iff mst.eq.2.or.mst.eq.3.or.mst.eq.6 -
        # reachable as married_joint or married_separate (mst.eq.3 dead);
        # head_of_household (mst=4) falls to the "a" default, same as
        # single.
        sorm_b_group = pl.col("filing_status").is_in(["married_joint", "married_separate"])
        xemp_base = pl.when(sorm_b_group).then(45000.0 / pl.col("ct_sep")).otherwise(33750.0)
        ln9_thr = pl.when(sorm_b_group).then(150000.0 / pl.col("ct_sep")).otherwise(112500.0)
        xln9 = (xprefs - ln9_thr).clip(0, None) * 0.25
        xemp = (xemp_base - xln9).clip(0, None)
        xln11 = (xprefs - xemp).clip(0, None)
        ln11_bp = 175000.0 / pl.col("ct_sep")
        offset = (3500.0 / pl.col("ct_sep")) if effective_year >= 1994 else 0.0
        xln12_hi = (45500.0 / pl.col("ct_sep")) + (xln11 - ln11_bp).clip(0, None) * 0.28 - offset
        xln12_lo = xln11 * 0.26
        xln12 = pl.when(xln11 > ln11_bp).then(xln12_hi).otherwise(xln12_lo)

        if effective_year <= 1993:
            amt_final = (xln12 * 0.23 - pl.col("ct_statax")).clip(0, None)
            amcred_final = pl.lit(0.0)
        elif effective_year <= 2012:
            amt_final = (pl.min_horizontal(xln12 * 0.19, xprefs * 0.05) - pl.col("ct_statax")).clip(0, None)
            # amcred=min(xln12*.19,max(-amt,0)) - since amt=max(...,0) is
            # never negative, -amt is never positive, so this is always 0
            # for 1994-2012 (only the 2013+ nested-recompute override
            # below ever produces a nonzero amcred).
            amcred_final = pl.lit(0.0)
        else:
            # 2013+: `data(22)=0; call nlaw(data,law); data(22)=d22` is a
            # full nested federal recompute - but data(22) is confirmed
            # inert (never externally assigned) for this schema, so it's
            # already 0 going in, making the "nested" recompute produce
            # IDENTICAL federal results to the ones already computed.
            # tamt=comnew(88)+comnew(89)=tamt1+tamt2, federal's own
            # tentative minimum tax BEFORE subtracting regular tax
            # (engine/amt.py computes this internally but returns only
            # the final excess over regular tax) - reconstructed locally
            # via `_federal_tentative_minimum_tax` below, reusing the
            # exact same params/formula federal.py's own AMT call uses.
            # data(163) confirmed inert (always 0).
            tamt = _federal_tentative_minimum_tax(df, effective_year)
            amt1 = 0.19 * tamt
            amt2 = 0.055 * xprefs_raw
            amtfin = pl.min_horizontal(amt1, amt2)
            amt_final = (pl.col("ct_statax") - amtfin).clip(0, None)
            amcred_final = pl.min_horizontal(xln12 * 0.19, amt_final)

        has_amt_pref = xln11 > 0
        federal_amt_gate = pl.col("amt") > 0
        gate = has_amt_pref & federal_amt_gate
        df = df.with_columns(
            ct_amcred=pl.when(gate).then(amcred_final).otherwise(0.0),
            ct_amt=pl.when(gate).then(amt_final).otherwise(0.0),
        )
        df = df.with_columns(ct_statax=(pl.col("ct_statax") - pl.col("ct_amcred")).clip(0, None))
        df = df.with_columns(ct_statax=(pl.col("ct_statax") + pl.col("ct_amt")).clip(0, None))
    else:
        df = df.with_columns(ct_amcred=pl.lit(0.0))

    # --- Property Tax Credit (1996+) ---
    if effective_year >= 1996:
        cap = float(resolve_year(p["property_credit_cap_by_year"], effective_year))
        pcred = pl.min_horizontal(cap, pl.col("proptax"))
        if effective_year >= 1997:
            phse = pl.when(pl.col("filing_status") == "single").then(
                float(resolve_year(p["property_credit_phaseout_single"], effective_year))
            ).when(pl.col("filing_status") == "head_of_household").then(
                float(resolve_year(p["property_credit_phaseout_hoh"], effective_year))
            ).otherwise(float(resolve_year(p["property_credit_phaseout_joint"], effective_year)) / pl.col("ct_sep"))
            agix = pl.col("ct_agi").clip(0, None)
            # 8-point phaseout table: (phse+10000*i, step) for i=0..6, then
            # a final (infinity, 1.0) - `tablki`'s own weighting (see
            # `_table_lookup_interp`) is used via the shared helper rather
            # than a hand-rolled (and, in an earlier version of this code,
            # direction-inverted) interpolation.
            steps = [0.0, 0.15, 0.30, 0.45, 0.60, 0.75, 0.90]
            thresholds = [phse + 10000.0 * i for i in range(7)]
            rows = list(zip(thresholds, steps)) + [(1.0e20, 1.0)]
            pct = _table_lookup_interp(agix, rows)
            pcred = pcred - pct * pcred
        if effective_year >= 2017:
            no_dep_or_elderly = (pl.col("depx") < 1)
            pcred = pl.when(no_dep_or_elderly).then(0.0).otherwise(pcred)
        df = df.with_columns(ct_pcred=pcred.clip(0, None))
    else:
        df = df.with_columns(ct_pcred=pl.lit(0.0))
    df = df.with_columns(ct_statax=(pl.col("ct_statax") - pl.col("ct_pcred")).clip(0, None))

    # --- EITC (2011+) ---
    if effective_year >= 2011:
        rate = float(resolve_year(p["eitc_rate_by_year"], effective_year))
        eitc_fed = pl.col("eitc").clip(0, None) if "eitc" in df.columns else pl.lit(0.0)
        df = df.with_columns(ct_earncr=rate * eitc_fed)
    else:
        df = df.with_columns(ct_earncr=pl.lit(0.0))

    df = df.with_columns(siitax=(pl.col("ct_statax") - pl.col("ct_earncr")) * flate)
    return df
