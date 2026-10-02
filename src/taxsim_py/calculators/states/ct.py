"""Connecticut individual income tax calculator."""

import polars as pl

from taxsim_py.engine.brackets import bracket_rate, bracket_tax
from taxsim_py.engine.inputs import aged_count, files_head_of_household, files_joint, files_separate, files_single, separate_divisor, taxpayer_count
from taxsim_py.engine.schema import PARAMETERS_ROOT, YearParams, load_yaml, resolve_year
from taxsim_py.engine.state import (
    by_filing_status,
    checkpoint,
    federal_capital_gain_in_agi,
    household_income,
    interpolate_table,
    tier_values,
    with_state_detail,
)
from taxsim_py.behavior import BehaviorProfile, TAXSIM_BEHAVIOR
from taxsim_py.engine.state_extrapolation import deflate_for_extrapolation, resolve_state_year

CT_PARAMS = load_yaml(PARAMETERS_ROOT / "states" / "ct" / "income_tax.yaml")
FEDERAL_AMT_PARAMS = load_yaml(PARAMETERS_ROOT / "national" / "amt.yaml")
FEDERAL_CAPITAL_GAINS_PARAMS = load_yaml(PARAMETERS_ROOT / "national" / "capital_gains.yaml")

_RICH_STATUSES = ["married_joint", "head_of_household"]
_STATUSES = ["single", "married_joint", "married_separate", "head_of_household"]
_SEPRET_BY_STATUS = {"single": 1.0, "married_joint": 1.0, "head_of_household": 1.0, "married_separate": 2.0}


def _federal_tentative_minimum_tax(df: pl.DataFrame, year: int) -> tuple[pl.DataFrame, pl.Expr]:
    """Reconstruct federal tentative minimum tax; returns the frame and the tax."""
    amt_p = YearParams(FEDERAL_AMT_PARAMS, year)
    cg_p = YearParams(FEDERAL_CAPITAL_GAINS_PARAMS, year)
    exemption = by_filing_status({s: resolve_year(amt_p["exemption"][s], year) for s in _STATUSES})
    threshold = by_filing_status({s: resolve_year(amt_p["exemption_phaseout_threshold"][s], year) for s in _STATUSES})
    phaseout_rate = amt_p.num("exemption_phaseout_rate")
    rate_bp = amt_p.num("rate_breakpoint")
    rate_lo = amt_p.num("rate_below_breakpoint")
    rate_hi = amt_p.num("rate_above_breakpoint")
    sepret = by_filing_status(_SEPRET_BY_STATUS)

    amt_income = pl.col("agi") - pl.when(pl.col("itemizes")).then(pl.col("mortgage")).otherwise(0.0)
    # Married-filing-separately-only AMTI addback (see amt.yaml) - this
    # helper is only ever called for year>=2013, so it always applies.
    sep_addback_cap = float(resolve_year(amt_p["exemption"]["married_separate"], year))
    sep_addback_threshold = amt_p.num("separate_return_addback_threshold")
    sep_addback = pl.min_horizontal(
        sep_addback_cap, amt_p.num("separate_return_addback_rate") * (amt_income - sep_addback_threshold).clip(0, None)
    )
    df, (amt_income,) = checkpoint(
        df, ct_tmt_income=pl.when(sepret == 2.0).then(amt_income + sep_addback).otherwise(amt_income)
    )
    exemption_after_phaseout = (exemption - phaseout_rate * (amt_income - threshold).clip(0, None)).clip(0, None)
    df, (amt_base,) = checkpoint(df, ct_tmt_base=(amt_income - exemption_after_phaseout).clip(0, None))
    breakpoint_per_return = rate_bp / sepret
    backout = (rate_hi - rate_lo) * rate_bp / sepret

    ltg = pl.col("ltg")
    df, (ltg_capped, regular_ordinary_income) = checkpoint(
        df,
        ct_tmt_ltg=pl.min_horizontal(ltg, amt_base),
        ct_tmt_regular_ordinary=(pl.col("taxable_income") - ltg).clip(0, None),
    )
    ordinary_amt_base = (amt_base - ltg_capped).clip(0, None)
    tentative_ordinary_tax = pl.when(ordinary_amt_base <= breakpoint_per_return).then(
        ordinary_amt_base * rate_lo
    ).otherwise(ordinary_amt_base * rate_hi - backout)

    rate_0_ceiling = by_filing_status({s: resolve_year(cg_p["rate_0_ceiling"][s], year) for s in _STATUSES})
    rate_15_ceiling = by_filing_status({s: resolve_year(amt_p["cg_rate_15_ceiling"][s], year) for s in _STATUSES})
    cg_rate_15 = cg_p.num("rate_15")
    cg_rate_0 = cg_p.num("rate_0")
    zero_pct_room = (rate_0_ceiling - regular_ordinary_income).clip(0, None)
    zero_pct_amount = pl.min_horizontal(zero_pct_room, ltg_capped)
    remaining_after_zero = ltg_capped - zero_pct_amount
    fifteen_pct_room = (rate_15_ceiling - regular_ordinary_income - zero_pct_room).clip(0, None)
    top_slice = (remaining_after_zero - fifteen_pct_room).clip(0, None)
    tentative_ltg_tax = cg_rate_0 * zero_pct_amount + cg_rate_15 * remaining_after_zero + amt_p.num("gains_top_rate_addition") * top_slice
    return checkpoint(df, ct_tmt=tentative_ordinary_tax + tentative_ltg_tax)[0], pl.col("ct_tmt")


def _flat_rate_step(income: pl.Expr, rows: list[list[float]]) -> pl.Expr:
    """Select the first Connecticut rate whose threshold contains income."""
    expr = pl.lit(rows[-1][1])
    for threshold, rate in reversed(rows[:-1]):
        expr = pl.when(income <= threshold).then(pl.lit(rate)).otherwise(expr)
    return expr


def _step_value(income: pl.Expr, rows: list[list[float]]) -> pl.Expr:
    """A dated amount scale: row i's value applies above its threshold, up to and including the
    next one (PolicyEngine's right-closed rule)."""
    expr = pl.lit(float(rows[0][1]))
    for threshold, value in rows[1:]:
        expr = pl.when(income > float(threshold)).then(float(value)).otherwise(expr)
    return expr


def compute_ct_tax(df: pl.DataFrame, year: int, behavior: BehaviorProfile = TAXSIM_BEHAVIOR) -> pl.DataFrame:
    state_year = "ct" if behavior.mode.value == "statutory" else None
    effective_year, flate = resolve_state_year(year, state_year)
    p = YearParams(CT_PARAMS, effective_year)

    df = df.with_columns(
        ct_household_income=household_income()
    )
    df = deflate_for_extrapolation(df, flate, extra=("ct_household_income",))

    df = df.with_columns(
        ct_sep=separate_divisor(),
        ct_rich=pl.col("filing_status").is_in(_RICH_STATUSES),
    )

    # --- AGI: federal AGI less the pension deduction (2019+) and exempt
    # Social Security benefits (1985+). ---
    agi = pl.col("agi")
    if effective_year >= 2019:
        joint = files_joint()
        if behavior.mode.value == "statutory" and effective_year >= 2022:
            pension_rates = p.value("pension_deduction_rates")
            rate = pl.when(joint).then(
                _step_value(agi, pension_rates["joint"])
            ).otherwise(_step_value(agi, pension_rates["non_joint"]))
            agi = agi - rate * pl.col("pensions")
        else:
            limit_p = p["pension_deduction_agi_limit"]
            limit = pl.when(joint).then(float(limit_p["joint"])).otherwise(float(limit_p["single"]))
            share = p.num("pension_deduction_share")
            agi = agi - (1 - (agi - limit) / limit).clip(0, 1) * share * pl.col("pensions")
    if effective_year >= 1985:
        couple = pl.col("filing_status").is_in(["married_joint", "head_of_household"])
        lim = p["social_security_agi_limit"]
        ssbmax = pl.when(couple).then(float(resolve_year(lim["joint"], effective_year))).otherwise(
            float(resolve_year(lim["single"], effective_year))
        )
        base_p = p["social_security_base_amount"]
        excl = (
            pl.when(files_joint()).then(float(base_p["joint"]))
            .when(files_separate()).then(float(base_p["married_separate"]))
            .otherwise(float(base_p["single"]))
        )
        taxable_ss = pl.col("taxable_social_security")
        benefits = pl.col("gssi")
        if effective_year <= 1999:
            adjustments = pl.col("se_adjustment") - pl.col("nonprop")
            kept = 0.5 * pl.min_horizontal(
                0.5 * (pl.col("ct_household_income") - 0.5 * benefits - adjustments - excl).clip(0, None), 0.5 * benefits
            )
            partial = (taxable_ss - kept).clip(0, None)
        else:
            if behavior.mode.value == "statutory" and effective_year >= 2022:
                # PolicyEngine follows IRC section 86: above the state
                # threshold, CT removes 25% of the lesser of gross SS and
                # federal combined income in excess of the federal base amount.
                combined_income = agi - taxable_ss + 0.5 * benefits
                combined_excess = (combined_income - excl).clip(0, None)
                partial = (taxable_ss - p["social_security_partial_share"] * pl.min_horizontal(benefits, combined_excess)).clip(0, None)
            else:
                partial = pl.when(excl > 0).then(
                    taxable_ss - p["social_security_partial_share"] * pl.min_horizontal(benefits, excl)
                ).otherwise(0.0)
        agi = pl.when(taxable_ss > 0).then(
            agi - pl.when(agi < ssbmax).then(taxable_ss).otherwise(partial)
        ).otherwise(agi)
    df = df.with_columns(ct_agi=agi)

    # --- Exemption ---
    if effective_year <= 1990:
        df = df.with_columns(ct_exemp=100.0 * (taxpayer_count() + aged_count()))
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
            xmp = p.num("exemption_single_flat_2008plus")
            exemp_single = (xmp - (pl.col("ct_agi") - 2 * xmp)).clip(0, xmp)
        exemp_hoh = (float(hoh["amount"]) - (pl.col("ct_agi") - float(hoh["phaseout_start"]))).clip(0, float(hoh["amount"]))
        exemp_joint = (float(joint["amount"]) - (pl.col("ct_agi") - float(joint["phaseout_start"]))).clip(0, float(joint["amount"]))
        exemp_sep = pl.lit(0.0)
        if effective_year >= 2000:
            exemp_sep = (float(sep_p["amount"]) - (pl.col("ct_agi") - float(sep_p["phaseout_start"]))).clip(0, float(sep_p["amount"]))
        if effective_year >= 2022:
            # The exemption falls $1,000 per $1,000 (or part) of AGI over the
            # start (PolicyEngine-US gov.states.ct.tax.income.exemptions.personal).
            def stepped(amount: float, start: float) -> pl.Expr:
                steps = ((pl.col("ct_agi") - start).clip(0, None) / 1000.0).ceil()
                return (amount - 1000.0 * steps).clip(0, amount)

            exemp_single = stepped(15000.0, 30000.0)
            exemp_hoh = stepped(float(hoh["amount"]), float(hoh["phaseout_start"]))
            exemp_joint = stepped(float(joint["amount"]), float(joint["phaseout_start"]))
            exemp_sep = stepped(float(sep_p["amount"]), float(sep_p["phaseout_start"]))
        df = df.with_columns(
            ct_exemp=pl.when(files_joint()).then(exemp_joint)
            .when(files_head_of_household()).then(exemp_hoh)
            .when((files_separate()) & (effective_year >= 2000)).then(exemp_sep)
            .otherwise(exemp_single)
        )

    # --- Pre-1991 capital gains / dividends / interest tax ---
    # `gain` (`comnew(6)`) is federal taxable gains: net of the federal
    # long-term exclusion through 1986, all gains 1987-1990. The port
    # previously clipped `stcg`/`ltcg` to 0 individually before adding
    # them, throwing away a short-term loss instead of netting it against
    # a long-term gain, which is what `comnew(6)` actually does (a $12,000
    # long-term gain with a $4,000 short-term loss was taxed as if the
    # loss didn't exist) - confirmed against the real oracle, which
    # already nets correctly, so this applies unconditionally rather than
    # being calculation_mode-gated.
    gain = pl.col("pre1987_capgn") if effective_year <= 1986 else federal_capital_gain_in_agi(year, flate)
    if effective_year in (1987, 1988):
        # Same bug as above, in CT's own additional 1987-1988 exclusion:
        # based on raw `ltcg`, ignoring a short-term loss that may have
        # already reduced `gain` below what raw `ltcg` alone implies.
        # Capped at `gain` itself so the exclusion can't exceed the
        # long-term gain actually remaining after netting.
        gain = (gain - float(p["ltcg_ct_only_exclusion_1987_1988"]) * pl.min_horizontal(pl.col("ltcg"), gain).clip(0, None)).clip(0, None)
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
        # Reported taxable income floors a net capital loss at 0 before
        # adding dividends/interest (confirmed against the real oracle);
        # `cgtax` above already handles a negative `gain` correctly on its
        # own, so this floor is a reporting-only detail, not a tax change.
        df = df.with_columns(
            ct_statax=cgtax + divtax, ct_taxinc=gain.clip(0, None) + divint, ct_cgtax=cgtax, ct_divtax=divtax
        )
        table = (
            "divint_rate_table_pre1983" if effective_year <= 1983
            else "divint_rate_table_1984" if effective_year == 1984
            else "divint_rate_table_1985" if effective_year == 1985
            else "divint_rate_table_1986_1988" if effective_year <= 1988
            else "divint_rate_table_1989_1990"
        )
        rate_expr = _flat_rate_step(pl.col("ct_agi"), p[table])
    else:
        df = df.with_columns(ct_taxinc=(pl.col("ct_agi") - pl.col("ct_exemp")).clip(0, None))
        if effective_year == 1991:
            divtax_1991 = _flat_rate_step(pl.col("ct_agi"), p["divint_rate_table_1991"]) * divint
            t91 = p["tax_1991"]
            rate = t91["rate"]
            df = df.with_columns(ct_xtax=pl.col("ct_taxinc") * rate, ct_divtax=divtax_1991)
            # cgtax=max(0,min(.034*agi,(gain-100*(data(7)+data(9)+data(10)))*.0475)),
            # with `data(7)` taxpayers and `data(9)` taxpayers 65 or older.
            exemp_units = taxpayer_count() + aged_count()
            cgtax_1991 = pl.max_horizontal(
                pl.min_horizontal(
                    t91["gains_agi_cap_share"] * pl.col("ct_agi"),
                    (gain - t91["gains_exemption_per_unit"] * exemp_units) * t91["gains_rate"],
                ),
                0.0,
            )
            df = df.with_columns(ct_cgtax=cgtax_1991)
            rate_expr = pl.lit(rate)
        elif effective_year <= 1995:
            rate = float(p["flat_rate_1991_1995"])
            df = df.with_columns(ct_xtax=pl.col("ct_taxinc") * rate, ct_divtax=pl.lit(0.0), ct_cgtax=pl.lit(0.0))
            rate_expr = pl.lit(rate)
        else:
            year_table_map = {
                1996: "brackets_1996", 1997: "brackets_1997", 1998: "brackets_1998",
            }
            if effective_year == 2024:
                prefix = "brackets_2024"
            elif effective_year in year_table_map:
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
            xtax = pl.when(files_head_of_household()).then(
                bracket_tax(pl.col("ct_taxinc"), brackets_hoh)
            ).when(files_joint()).then(
                bracket_tax(pl.col("ct_taxinc"), brackets_joint)
            ).otherwise(bracket_tax(pl.col("ct_taxinc"), brackets_single))
            rate_expr = pl.when(files_head_of_household()).then(
                bracket_rate(pl.col("ct_taxinc"), brackets_hoh)
            ).when(files_joint()).then(
                bracket_rate(pl.col("ct_taxinc"), brackets_joint)
            ).otherwise(bracket_rate(pl.col("ct_taxinc"), brackets_single))

            if effective_year >= 2011:
                is_hoh = files_head_of_household()
                is_joint = files_joint()
                is_sep = files_separate()
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

                rmax_s = p.num("recapture_max_single")
                rmax_h = p.num("recapture_max_hoh")
                rate_s = p.num("recapture_rate_single")
                rate_h = p.num("recapture_rate_hoh")
                ragi_s = float(p["recapture_agi_threshold_single"])
                ragi_h = float(p["recapture_agi_threshold_hoh"])
                # Single and separate returns share the single table and
                # recapture amounts (`mst.eq.1.or.mst.eq.3.or.mst.eq.6`).
                rtax_single = pl.min_horizontal(rate_s * (pl.col("ct_agi") - ragi_s).clip(0, None), rmax_s)
                rtax_hoh = pl.min_horizontal(rate_h * (pl.col("ct_agi") - ragi_h).clip(0, None), rmax_h)
                rtax_joint = pl.min_horizontal(rate_s * (pl.col("ct_agi") - 2 * ragi_s).clip(0, None), 2 * rmax_s)
                rtax = pl.when(is_hoh).then(rtax_hoh).when(is_joint).then(rtax_joint).otherwise(rtax_single)
                if effective_year >= 2022:
                    # Dated statutory recapture: stepwise per increment of AGI
                    # above each tier's start, capped (the low tier only when
                    # in effect).
                    tiers = p["recapture_2022plus"][effective_year]
                    recapture = pl.lit(0.0)
                    for name in ("low", "middle", "high"):
                        tier = tiers[name]
                        if name == "low" and not tier["in_effect"]:
                            continue
                        amount = pl.lit(0.0)
                        for status in ("single", "married_joint", "married_separate", "head_of_household"):
                            start, step_amount, increment, maximum = (float(v) for v in tier[status])
                            steps = ((pl.col("ct_agi") - start).clip(0, None) / increment).ceil()
                            amount = pl.when(pl.col("filing_status") == status).then(
                                pl.min_horizontal(maximum, steps * step_amount)
                            ).otherwise(amount)
                        recapture = recapture + amount
                    rtax = recapture

                if effective_year >= 2022:
                    stax = pl.lit(0.0)
                    for status, (start, step_amount, increment, maximum) in p["add_back_2022plus"][effective_year].items():
                        steps = ((pl.col("ct_agi") - start).clip(0, None) / increment).ceil()
                        stax = pl.when(pl.col("filing_status") == status).then(
                            pl.min_horizontal(float(maximum), steps * step_amount)
                        ).otherwise(stax)

                xtax = xtax + stax + rtax

            df = df.with_columns(ct_xtax=xtax, ct_divtax=pl.lit(0.0), ct_cgtax=pl.lit(0.0))

        # --- Personal Tax Credit ---
        if effective_year <= 1994:
            credp = pl.when(files_head_of_household()).then(
                interpolate_table(pl.col("ct_agi"), p["personal_credit_1991_1994_hoh"])
            ).when(files_joint()).then(
                interpolate_table(pl.col("ct_agi"), p["personal_credit_1991_1994_joint"])
            ).otherwise(interpolate_table(pl.col("ct_agi"), p["personal_credit_1991_1994_single"]))
        else:
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

            # The historical TAXSIM table interpolates between rows. From
            # 2022, Connecticut's worksheet applies the first rate whose AGI
            # threshold has been reached, so a filer exactly at a threshold
            # must receive that row's discrete rate rather than an average
            # with the preceding row.
            if effective_year >= 2022 and behavior.mode.value == "statutory":
                credp_single = tier_values(pl.col("ct_agi"), single_thresholds, rates)[0]
                credp_hoh = tier_values(pl.col("ct_agi"), p["personal_credit_1995plus_hoh_thresholds"], rates)[0]
                credp_joint = tier_values(pl.col("ct_agi") * pl.col("ct_sep"), joint_rows_raw, rates)[0]
            else:
                credp_single = interpolate_table(pl.col("ct_agi"), single_rows)
                credp_hoh = interpolate_table(pl.col("ct_agi"), hoh_rows)
                # joint/married_separate: thresholds divided by `sep` (2 for MFS)
                joint_rows_div1 = list(zip([t for t in joint_rows_raw], rates))
                credp_joint = interpolate_table(pl.col("ct_agi") * pl.col("ct_sep"), joint_rows_div1)
            credp = pl.when(files_head_of_household()).then(credp_hoh).when(
                pl.col("filing_status").is_in(["married_joint", "married_separate"])
            ).then(credp_joint).otherwise(credp_single)

        df = df.with_columns(ct_credp=credp)
        df = df.with_columns(ct_inctax=pl.col("ct_xtax") * (1.0 - pl.col("ct_credp")))
        df = df.with_columns(ct_statax=pl.col("ct_inctax"))
        if effective_year == 1991:
            df = df.with_columns(ct_statax=pl.col("ct_statax") + pl.col("ct_cgtax") + pl.col("ct_divtax"))

    # --- AMT (1993+), only when there is federal AMT (`comnew(70).gt.0`) ---
    if effective_year >= 1993:
        # `xprefs=comnew(69)-subrac`: federal AMT income before the exemption.
        xprefs_base = pl.col("agi") - pl.when(pl.col("itemizes")).then(pl.col("mortgage")).otherwise(0.0)
        is_sep_amt = files_separate()
        sep_addback_cap = float(resolve_year(FEDERAL_AMT_PARAMS["exemption"]["married_separate"], effective_year))
        sep_addback_threshold = float(
            resolve_year(FEDERAL_AMT_PARAMS["separate_return_addback_threshold"], effective_year)
        )
        sep_addback = pl.min_horizontal(
            sep_addback_cap,
            float(resolve_year(FEDERAL_AMT_PARAMS["separate_return_addback_rate"], effective_year))
            * (xprefs_base - sep_addback_threshold).clip(0, None),
        )
        df, (xprefs_raw,) = checkpoint(
            df, ct_xprefs_raw=pl.when(is_sep_amt).then(xprefs_base + sep_addback).otherwise(xprefs_base)
        )
        # `if(mst.eq.3.or.mst.eq.6.and.xprefs.gt.165000.)`: separate returns.
        is_sep = files_separate()
        a = p["amt"]
        df, (xprefs,) = checkpoint(
            df,
            ct_xprefs=pl.when(is_sep & (xprefs_raw > a["separate_addback_start"])).then(
                pl.when(xprefs_raw > a["separate_addback_end"]).then(xprefs_raw + a["separate_addback_max"]).otherwise(
                    xprefs_raw + a["separate_addback_rate"] * (xprefs_raw - a["separate_addback_start"])
                )
            ).otherwise(xprefs_raw),
        )
        # `sorm(mst,a,b)` is `b` for joint and separate returns.
        sorm_b_group = pl.col("filing_status").is_in(["married_joint", "married_separate"])
        sep = pl.col("ct_sep")
        xemp_base = pl.when(sorm_b_group).then(a["exemption_married"] / sep).otherwise(float(a["exemption_other"]))
        ln9_thr = pl.when(sorm_b_group).then(a["exemption_phaseout_married"] / sep).otherwise(float(a["exemption_phaseout_other"]))
        xln9 = (xprefs - ln9_thr).clip(0, None) * a["exemption_phaseout_rate"]
        xemp = (xemp_base - xln9).clip(0, None)
        df, (xln11,) = checkpoint(df, ct_xln11=(xprefs - xemp).clip(0, None))
        ln11_bp = a["rate_breakpoint"] / sep
        offset = (a["breakpoint_offset_1994"] / sep) if effective_year >= 1994 else 0.0
        xln12_hi = (a["tax_at_breakpoint"] / sep) + (xln11 - ln11_bp).clip(0, None) * a["rate_high"] - offset
        xln12_lo = xln11 * a["rate_low"]
        tentative_share = float(resolve_year(a["tentative_tax_share"], effective_year))
        df, (xln12,) = checkpoint(df, ct_xln12=pl.when(xln11 > ln11_bp).then(xln12_hi).otherwise(xln12_lo))

        if effective_year <= 1993:
            amt_final = (xln12 * tentative_share - pl.col("ct_statax")).clip(0, None)
            amcred_final = pl.lit(0.0)
        elif effective_year <= 2012:
            preference_share = float(resolve_year(a["preference_income_share"], effective_year))
            amt_final = (pl.min_horizontal(xln12 * tentative_share, xprefs * preference_share) - pl.col("ct_statax")).clip(0, None)
            # amcred=min(xln12*.19,max(-amt,0)) is always 0 because amt >= 0.
            amcred_final = pl.lit(0.0)
        else:
            # 2013+: `tamt=comnew(88)+comnew(89)`, federal tentative minimum
            # tax before subtracting regular tax, from a federal recompute
            # that matches the main one for these inputs.
            df, tamt = _federal_tentative_minimum_tax(df, effective_year)
            amt1 = tentative_share * tamt
            amt2 = float(resolve_year(a["preference_income_share"], effective_year)) * xprefs_raw
            amtfin = pl.min_horizontal(amt1, amt2)
            df, (amt_final,) = checkpoint(df, ct_amt_final=(pl.col("ct_statax") - amtfin).clip(0, None))
            amcred_final = pl.min_horizontal(xln12 * tentative_share, amt_final)

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
        cap = p.num("property_credit_cap_by_year")
        pcred = pl.min_horizontal(cap, pl.col("proptax"))
        if effective_year >= 1997:
            phse = pl.when(files_single()).then(
                p.num("property_credit_phaseout_single")
            ).when(files_head_of_household()).then(
                p.num("property_credit_phaseout_hoh")
            ).otherwise(p.num("property_credit_phaseout_joint") / pl.col("ct_sep"))
            agix = pl.col("ct_agi").clip(0, None)
            width = float(p["property_credit_phaseout_step_width"])
            if behavior.mode.value == "statutory" and effective_year >= 2022:
                # PolicyEngine and the current Connecticut worksheet use a
                # discrete reduction: each started increment reduces the
                # credit by 15%. The old TAXSIM path retains its historical
                # interpolation for compatibility.
                steps = ((agix - phse).clip(0, None) / width).ceil()
                pct = (float(p["property_credit_phaseout_rate"]) * steps).clip(0, 1)
                pcred = pcred * (1.0 - pct)
            else:
                rows = [(float(width) * i, step) for i, step in enumerate(p["property_credit_phaseout_steps"])]
                rows.append(tuple(p["property_credit_phaseout_final"]))
                pct = interpolate_table(agix - phse, rows)
                pcred = pcred - pct * pcred
        if effective_year >= 2017:
            no_dep_or_elderly = (pl.col("depx") < 1) & (aged_count() < 1)
            pcred = pl.when(no_dep_or_elderly).then(0.0).otherwise(pcred)
        df = df.with_columns(ct_pcred=pcred.clip(0, None))
    else:
        df = df.with_columns(ct_pcred=pl.lit(0.0))
    df = df.with_columns(ct_statax=(pl.col("ct_statax") - pl.col("ct_pcred")).clip(0, None))

    # --- EITC (2011+) ---
    if effective_year >= 2011:
        rate = p.num("eitc_rate_by_year")
        eitc_fed = pl.col("eitc").clip(0, None)
        df = df.with_columns(ct_earncr=rate * eitc_fed)
    else:
        df = df.with_columns(ct_earncr=pl.lit(0.0))

    df = df.with_columns(siitax=(pl.col("ct_statax") - pl.col("ct_earncr")) * flate)
    return with_state_detail(
        df,
        agi=pl.col("ct_agi"),
        exemptions=pl.col("ct_exemp"),
        taxable_income=pl.col("ct_taxinc"),
        property_credit=pl.col("ct_pcred"),
        eic=pl.col("ct_earncr"),
        credits=pl.col("ct_pcred") + pl.col("ct_amcred") + pl.col("ct_earncr"),
        rate=rate_expr,
    )
