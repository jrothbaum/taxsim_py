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
    # The separate-return AMT income addback (2013 on, see amt.yaml).
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
    effective_year, flate = resolve_state_year(year, behavior, CT_PARAMS)
    p = YearParams(CT_PARAMS, effective_year)

    df = df.with_columns(
        ct_household_income=household_income(behavior)
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
        if p.in_effect("pension_deduction_rates"):
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
        ssbmax = pl.when(couple).then(p.num("social_security_agi_limit", "joint")).otherwise(
            p.num("social_security_agi_limit", "single")
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
        elif effective_year >= 2022:
            # A share of the lesser of gross benefits and combined income
            # above the federal base amount (IRC section 86).
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
    if p.in_effect("exemption_per_person"):
        df = df.with_columns(ct_exemp=p.num("exemption_per_person") * (taxpayer_count() + aged_count()))
    else:
        def exemption(status: str) -> pl.Expr:
            terms = p.value("exemption", status)
            amount, start = float(terms["amount"]), float(terms["phaseout_start"])
            if effective_year >= 2022:
                # $1,000 less for each $1,000 (or part) of AGI over the start.
                steps = ((pl.col("ct_agi") - start).clip(0, None) / 1000.0).ceil()
                return (amount - 1000.0 * steps).clip(0, amount)
            return (amount - (pl.col("ct_agi") - start)).clip(0, amount)

        exemp = pl.when(files_joint()).then(exemption("married_joint")).when(files_head_of_household()).then(exemption("head_of_household"))
        if effective_year >= 2000:
            exemp = exemp.when(files_separate()).then(exemption("married_separate"))
        df = df.with_columns(ct_exemp=exemp.otherwise(exemption("single")))

    # --- Capital gains, dividends and interest (before 1991) ---
    # Federal taxable gains: net of the federal long-term exclusion through
    # 1986, all gains 1987-1990.
    gain = pl.col("pre1987_capgn") if effective_year <= 1986 else federal_capital_gain_in_agi(year, flate)
    if p.in_effect("capital_gains_exclusion"):
        # Limited to the long-term gain remaining after netting.
        gain = (gain - p.num("capital_gains_exclusion") * pl.min_horizontal(pl.col("ltcg"), gain).clip(0, None)).clip(0, None)
    divint = pl.col("dividends") + pl.col("intrec")

    if p.in_effect("capital_gains_rate"):
        cgtax = (p.num("capital_gains_rate") * (gain - pl.col("ct_exemp"))).clip(0, None)
        rate_expr = _flat_rate_step(pl.col("ct_agi"), p.value("investment_income_rates"))
        # Interest is taxed from 1983.
        divtax = rate_expr * (pl.col("dividends") if effective_year < 1983 else divint)
        # Reported taxable income floors a net capital loss at 0.
        df = df.with_columns(
            ct_statax=cgtax + divtax, ct_taxinc=gain.clip(0, None) + divint, ct_cgtax=cgtax, ct_divtax=divtax
        )
    else:
        df = df.with_columns(ct_taxinc=(pl.col("ct_agi") - pl.col("ct_exemp")).clip(0, None))
        if p.in_effect("flat_rate"):
            rate = p.num("flat_rate")
            df = df.with_columns(ct_xtax=pl.col("ct_taxinc") * rate, ct_divtax=pl.lit(0.0), ct_cgtax=pl.lit(0.0))
            rate_expr = pl.lit(rate)
            if p.in_effect("capital_gains_tax"):
                df = df.with_columns(ct_divtax=_flat_rate_step(pl.col("ct_agi"), p.value("investment_income_rates")) * divint)
                cg = p.value("capital_gains_tax")
                exemp_units = taxpayer_count() + aged_count()
                df = df.with_columns(ct_cgtax=pl.max_horizontal(
                    pl.min_horizontal(
                        cg["agi_cap_share"] * pl.col("ct_agi"),
                        (gain - cg["exemption_per_unit"] * exemp_units) * cg["rate"],
                    ),
                    0.0,
                ))
        else:
            brackets = p.value("brackets")
            brackets_single = brackets["single"]
            brackets_hoh = brackets["head_of_household"]
            brackets_joint = brackets["joint"]
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

            def stepped(table: dict) -> pl.Expr:
                """Per status [start, amount per increment, increment, maximum], per increment or part."""
                amount = pl.lit(0.0)
                for status in _STATUSES:
                    start, step_amount, increment, maximum = (float(v) for v in table[status])
                    steps = ((pl.col("ct_agi") - start).clip(0, None) / increment).ceil()
                    amount = pl.when(pl.col("filing_status") == status).then(
                        pl.min_horizontal(maximum, steps * step_amount)
                    ).otherwise(amount)
                return amount

            if p.in_effect("add_back_steps"):
                stax = stepped(p.value("add_back_steps"))
                recapture = pl.lit(0.0)
                for name, tier in p.value("recapture_steps").items():
                    if tier.get("in_effect", True):
                        recapture = recapture + stepped(tier)
                xtax = xtax + stax + recapture
            elif p.in_effect("add_back"):
                ab = p.value("add_back")

                def stax_for(ph):
                    return pl.min_horizontal(
                        float(ph["cap"]),
                        float(ph["per_1000"]) * (pl.col("ct_agi") - float(ph["threshold"])).clip(0, None) / float(ph["denom"]),
                    )

                stax = (
                    pl.when(files_head_of_household()).then(stax_for(ab["head_of_household"]))
                    .when(files_joint()).then(stax_for(ab["married_joint"]))
                    .when(files_separate()).then(stax_for(ab["married_separate"]))
                    .otherwise(stax_for(ab["single"]))
                )
                rc = p.value("recapture")
                single, hoh = rc["single"], rc["head_of_household"]
                rmax_s, rate_s, ragi_s = float(single["max"]), float(single["rate"]), float(single["agi_threshold"])
                rmax_h, rate_h, ragi_h = float(hoh["max"]), float(hoh["rate"]), float(hoh["agi_threshold"])
                rtax_single = pl.min_horizontal(rate_s * (pl.col("ct_agi") - ragi_s).clip(0, None), rmax_s)
                rtax_hoh = pl.min_horizontal(rate_h * (pl.col("ct_agi") - ragi_h).clip(0, None), rmax_h)
                rtax_joint = pl.min_horizontal(rate_s * (pl.col("ct_agi") - 2 * ragi_s).clip(0, None), 2 * rmax_s)
                rtax = pl.when(files_head_of_household()).then(rtax_hoh).when(files_joint()).then(rtax_joint).otherwise(rtax_single)
                xtax = xtax + stax + rtax

            df = df.with_columns(ct_xtax=xtax, ct_divtax=pl.lit(0.0), ct_cgtax=pl.lit(0.0))

        # --- Personal tax credit ---
        rates = p.value("personal_credit_rates")
        single_thresholds = p.value("personal_credit_thresholds_single")
        hoh_thresholds = p.value("personal_credit_thresholds_head_of_household")
        joint_thresholds = p.value("personal_credit_thresholds_joint")
        if effective_year <= 1994:
            credp = pl.when(files_head_of_household()).then(
                interpolate_table(pl.col("ct_agi"), list(zip(hoh_thresholds, rates)))
            ).when(files_joint()).then(
                interpolate_table(pl.col("ct_agi"), list(zip(joint_thresholds, rates)))
            ).otherwise(interpolate_table(pl.col("ct_agi"), list(zip(single_thresholds, rates))))
        else:
            if effective_year >= 2022:
                # The rate of the first threshold reached, not interpolated.
                credp_single = tier_values(pl.col("ct_agi"), single_thresholds, rates)[0]
                credp_hoh = tier_values(pl.col("ct_agi"), hoh_thresholds, rates)[0]
                credp_joint = tier_values(pl.col("ct_agi") * pl.col("ct_sep"), joint_thresholds, rates)[0]
            else:
                credp_single = interpolate_table(pl.col("ct_agi"), list(zip(single_thresholds, rates)))
                credp_hoh = interpolate_table(pl.col("ct_agi"), list(zip(hoh_thresholds, rates)))
                credp_joint = interpolate_table(pl.col("ct_agi") * pl.col("ct_sep"), list(zip(joint_thresholds, rates)))
            credp = pl.when(files_head_of_household()).then(credp_hoh).when(
                pl.col("filing_status").is_in(["married_joint", "married_separate"])
            ).then(credp_joint).otherwise(credp_single)

        df = df.with_columns(ct_credp=credp)
        df = df.with_columns(ct_inctax=pl.col("ct_xtax") * (1.0 - pl.col("ct_credp")))
        df = df.with_columns(ct_statax=pl.col("ct_inctax"))
        if p.in_effect("capital_gains_tax"):
            df = df.with_columns(ct_statax=pl.col("ct_statax") + pl.col("ct_cgtax") + pl.col("ct_divtax"))

    # --- Minimum tax (1993+), only when there is federal AMT ---
    if effective_year >= 1993:
        # Federal AMT income before the exemption.
        xprefs_base = pl.col("agi") - pl.when(pl.col("itemizes")).then(pl.col("mortgage")).otherwise(0.0)
        is_sep = files_separate()
        fed_amt = YearParams(FEDERAL_AMT_PARAMS, effective_year)
        sep_addback_cap = fed_amt.num("exemption", "married_separate")
        sep_addback_threshold = fed_amt.num("separate_return_addback_threshold")
        sep_addback = pl.min_horizontal(
            sep_addback_cap,
            fed_amt.num("separate_return_addback_rate") * (xprefs_base - sep_addback_threshold).clip(0, None),
        )
        df, (xprefs_raw,) = checkpoint(
            df, ct_xprefs_raw=pl.when(is_sep).then(xprefs_base + sep_addback).otherwise(xprefs_base)
        )
        a = p["amt"]
        df, (xprefs,) = checkpoint(
            df,
            ct_xprefs=pl.when(is_sep & (xprefs_raw > a["separate_addback_start"])).then(
                pl.when(xprefs_raw > a["separate_addback_end"]).then(xprefs_raw + a["separate_addback_max"]).otherwise(
                    xprefs_raw + a["separate_addback_rate"] * (xprefs_raw - a["separate_addback_start"])
                )
            ).otherwise(xprefs_raw),
        )
        # Joint and separate returns use the married amounts.
        married = pl.col("filing_status").is_in(["married_joint", "married_separate"])
        sep = pl.col("ct_sep")
        xemp_base = pl.when(married).then(a["exemption_married"] / sep).otherwise(float(a["exemption_other"]))
        ln9_thr = pl.when(married).then(a["exemption_phaseout_married"] / sep).otherwise(float(a["exemption_phaseout_other"]))
        xln9 = (xprefs - ln9_thr).clip(0, None) * a["exemption_phaseout_rate"]
        xemp = (xemp_base - xln9).clip(0, None)
        df, (xln11,) = checkpoint(df, ct_xln11=(xprefs - xemp).clip(0, None))
        ln11_bp = a["rate_breakpoint"] / sep
        offset = resolve_year(a["breakpoint_offset"], effective_year) / sep
        xln12_hi = (a["tax_at_breakpoint"] / sep) + (xln11 - ln11_bp).clip(0, None) * a["rate_high"] - offset
        xln12_lo = xln11 * a["rate_low"]
        tentative_share = p.num("amt", "tentative_tax_share")
        df, (xln12,) = checkpoint(df, ct_xln12=pl.when(xln11 > ln11_bp).then(xln12_hi).otherwise(xln12_lo))

        if effective_year <= 1993:
            amt_final = (xln12 * tentative_share - pl.col("ct_statax")).clip(0, None)
            amcred_final = pl.lit(0.0)
        elif effective_year <= 2012:
            preference_share = p.num("amt", "preference_income_share")
            amt_final = (pl.min_horizontal(xln12 * tentative_share, xprefs * preference_share) - pl.col("ct_statax")).clip(0, None)
            # TAXSIM's minimum tax credit is always 0 here because amt >= 0.
            amcred_final = pl.lit(0.0)
        else:
            # 2013+: federal tentative minimum tax from a federal recompute.
            df, tamt = _federal_tentative_minimum_tax(df, effective_year)
            amt1 = tentative_share * tamt
            amt2 = p.num("amt", "preference_income_share") * xprefs_raw
            amtfin = pl.min_horizontal(amt1, amt2)
            df, (amt_final,) = checkpoint(df, ct_amt_final=(pl.col("ct_statax") - amtfin).clip(0, None))
            amcred_final = pl.min_horizontal(xln12 * tentative_share, amt_final)

        gate = (xln11 > 0) & (pl.col("amt") > 0)
        df = df.with_columns(
            ct_amcred=pl.when(gate).then(amcred_final).otherwise(0.0),
            ct_amt=pl.when(gate).then(amt_final).otherwise(0.0),
        )
        df = df.with_columns(ct_statax=(pl.col("ct_statax") - pl.col("ct_amcred")).clip(0, None))
        df = df.with_columns(ct_statax=(pl.col("ct_statax") + pl.col("ct_amt")).clip(0, None))
    else:
        df = df.with_columns(ct_amcred=pl.lit(0.0))

    # --- Property tax credit (1996+) ---
    if p.in_effect("property_credit_cap"):
        pcred = pl.min_horizontal(p.num("property_credit_cap"), pl.col("proptax"))
        if effective_year >= 1997:
            phse = pl.when(files_single()).then(
                p.num("property_credit_phaseout_start", "single")
            ).when(files_head_of_household()).then(
                p.num("property_credit_phaseout_start", "head_of_household")
            ).otherwise(p.num("property_credit_phaseout_start", "joint") / pl.col("ct_sep"))
            agix = pl.col("ct_agi").clip(0, None)
            width = float(p["property_credit_phaseout_step_width"])
            if effective_year >= 2022:
                # Each started increment reduces the credit.
                steps = ((agix - phse).clip(0, None) / width).ceil()
                pct = (float(p["property_credit_phaseout_rate"]) * steps).clip(0, 1)
                pcred = pcred * (1.0 - pct)
            else:
                rows = [(float(width) * i, step) for i, step in enumerate(p["property_credit_phaseout_steps"])]
                rows.append(tuple(p["property_credit_phaseout_final"]))
                pct = interpolate_table(agix - phse, rows)
                pcred = pcred - pct * pcred
        if p.value("property_credit_requires_dependent_or_elderly"):
            no_dep_or_elderly = (pl.col("depx") < 1) & (aged_count() < 1)
            pcred = pl.when(no_dep_or_elderly).then(0.0).otherwise(pcred)
        df = df.with_columns(ct_pcred=pcred.clip(0, None))
    else:
        df = df.with_columns(ct_pcred=pl.lit(0.0))
    df = df.with_columns(ct_statax=(pl.col("ct_statax") - pl.col("ct_pcred")).clip(0, None))

    # --- Earned income credit (2011+) ---
    if p.in_effect("eitc_federal_share"):
        eitc_fed = pl.col("eitc").clip(0, None)
        earncr = p.num("eitc_federal_share") * eitc_fed
        bonus = p.num("eitc_child_bonus")
        if bonus:
            earncr = earncr + pl.when((eitc_fed > 0) & (pl.col("dep18") > 0)).then(bonus).otherwise(0.0)
        df = df.with_columns(ct_earncr=earncr)
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
