"""California individual income tax calculator."""


from __future__ import annotations
import math

import polars as pl

from taxsim_py.engine.brackets import bracket_rate, bracket_tax
from taxsim_py.engine.inputs import aged_count, files_head_of_household, files_joint, files_separate, files_single, is_dependent_filer, separate_divisor, taxpayer_count
from taxsim_py.engine.schema import PARAMETERS_ROOT, YearParams, load_yaml, resolve_year
from taxsim_py.engine.state import by_filing_status, dividend_input_adjustment, forced_standard, household_income, interpolate_table, with_state_detail
from taxsim_py.behavior import BehaviorProfile, TAXSIM_BEHAVIOR
from taxsim_py.engine.state_extrapolation import deflate_for_extrapolation, resolve_state_year

CA_PARAMS = load_yaml(PARAMETERS_ROOT / "states" / "ca" / "income_tax.yaml")
FEDERAL_AMT_PARAMS = load_yaml(PARAMETERS_ROOT / "national" / "amt.yaml")

_STATUSES = ["single", "married_joint", "married_separate", "head_of_household"]


def _raw_to_start_rate(raw_pairs: list[list[float]], scale_threshold: float = 1.0) -> list[list[float]]:
    """Convert upper-bound brackets to start-rate brackets."""
    out = [[0.0, raw_pairs[0][1]]]
    for i in range(1, len(raw_pairs)):
        prev_upper = raw_pairs[i - 1][0]
        lo = prev_upper * scale_threshold if prev_upper < 1.0e19 else prev_upper
        out.append([lo, raw_pairs[i][1]])
    return out


def _agi_rows(rows: list[list[float]], agi: pl.Expr) -> pl.Expr:
    """The share in the first [AGI upper bound, share] row covering `agi`, else 0."""
    expr = pl.lit(0.0)
    for upper, share in reversed(rows):
        expr = pl.when(agi <= upper).then(pl.lit(share)).otherwise(expr)
    return expr


def compute_ca_tax(df: pl.DataFrame, year: int, behavior: BehaviorProfile = TAXSIM_BEHAVIOR) -> pl.DataFrame:
    effective_year, flate = resolve_state_year(year, behavior, CA_PARAMS)
    p = YearParams(CA_PARAMS, effective_year)

    # Self-employment tax and household income are not deflated in
    # projected years.
    df = df.with_columns(
        ca_setax=pl.col("payroll_setax"),
        ca_household_income=household_income(behavior),
    )
    df = deflate_for_extrapolation(df, flate, extra=("ca_household_income",))

    df = df.with_columns(
        ca_sep=separate_divisor(),
        ca_ajnt=pl.when(files_joint()).then(2.0).otherwise(1.0),
        ca_hoh=files_head_of_household(),
        # Heads of household count as two taxpayers for the standard
        # deduction and the pre-1987 exemption credit.
        ca_txp=pl.when(files_head_of_household()).then(2.0).otherwise(taxpayer_count()),
        ca_txp_raw=taxpayer_count(),
    )

    # --- AGI ---
    aif = p.num("indexing_factor")
    if effective_year <= 1986:
        df = df.with_columns(ca_cg=pl.col("stcg") + p.num("capital_gains_inclusion") * pl.col("ltcg"))
        # TAXSIM also limits the loss to income before gains, which is not
        # replicated (see docs/pending_issues.md).
        capital_loss_cap = p.num("capital_loss_limit") / pl.col("ca_sep")
        df = df.with_columns(
            ca_cg=pl.when(pl.col("ca_cg") < 0)
            .then(-pl.min_horizontal(pl.col("ca_cg").abs(), capital_loss_cap))
            .otherwise(pl.col("ca_cg"))
        )
        df = df.with_columns(ca_gross_se=pl.col("psemp") + pl.col("ssemp"))
        df = df.with_columns(
            ca_totinc=pl.col("wages") + pl.col("intrec") + (pl.col("dividends") + dividend_input_adjustment()) + pl.col("ca_cg")
            + pl.col("ca_gross_se") + pl.col("pensions") + pl.col("otherprop") + pl.col("nonprop")
        )
        if p.in_effect("aged_exclusion"):
            ex = p.value("aged_exclusion")
            aged = aged_count()
            limit = ex["income_limit"] * aged / pl.col("ca_sep")
            oldex = (ex["amount"] * aged - 0.5 * (pl.col("ca_totinc") - limit).clip(0, None)).clip(0, None)
            df = df.with_columns(ca_totinc=pl.col("ca_totinc") - oldex)
        # CA-001: from 1979 TAXSIM subtracts taxable unemployment
        # compensation, which total income never included.
        ui_adjustment = (
            0.0
            if behavior.include_unemployment_in_california_total_income
            else (pl.col("taxable_unemployment") if effective_year >= 1979 else 0.0)
        )
        df = df.with_columns(ca_agi=(pl.col("ca_totinc") - ui_adjustment).clip(0, None))
    else:
        # Federal AGI less taxable unemployment compensation and Social Security.
        df = df.with_columns(ca_subtra=pl.col("taxable_unemployment") + pl.col("taxable_social_security"))
        if p.in_effect("se_deduction_holiday_addback"):
            setax = pl.col("ca_setax")
            holiday = p.value("se_deduction_holiday_addback")
            df = df.with_columns(
                # A negative subtraction: the adjustment adds to AGI.
                ca_subtra=pl.col("ca_subtra") - pl.when(setax <= holiday["threshold"]).then(
                    (holiday["rate"] - 0.5) * setax
                ).otherwise(float(holiday["flat"]))
            )
        df = df.with_columns(ca_agi=pl.col("agi") - pl.col("ca_subtra"))

    # --- Standard deduction ---
    per_taxpayer = float(p["standard_deduction_per_taxpayer"])
    if effective_year <= 1978:
        df = df.with_columns(ca_stded=per_taxpayer * pl.col("ca_txp"))
    else:
        df = df.with_columns(ca_stded=per_taxpayer * aif * pl.col("ca_txp"))

    # --- Itemized deduction ---
    if effective_year <= 1986:
        # Federal itemized deductions (zero when not itemizing federally)
        # less the state income or sales tax deduction.
        itemizing = pl.col("pre1987_itemizes").cast(pl.Float64)
        df = df.with_columns(
            ca_xitded=itemizing * (pl.col("pre1987_deduc") - pl.col("state_sales_or_income_tax_ded"))
        )
    else:
        # Federal gross itemized deductions less the state income or sales
        # tax deduction.
        xitded_base = (pl.col("salt_capped") - pl.col("state_sales_or_income_tax_ded") + pl.col("mortgage")).clip(0, None)
        if behavior.ignore_federal_salt_cap_in_california and effective_year >= 2018:
            # California never adopted the federal SALT cap: real estate tax is
            # deductible in full and its own income tax not at all.
            xitded_base = (pl.col("proptax") + pl.col("otheritem") + pl.col("mortgage")).clip(0, None)
        df = df.with_columns(ca_xitded_base=xitded_base)
        if p.in_effect("itemized_limitation"):
            lim = p.value("itemized_limitation")
            threshold = by_filing_status({s: float(v) * p.num("phaseout_index") for s, v in p["phaseout_threshold"].items()})
            df = df.with_columns(
                ca_reduce=pl.when(pl.col("agi") > threshold)
                .then(
                    pl.min_horizontal(
                        lim["cap_rate"] * pl.col("ca_xitded_base"),
                        lim["rate"] * (pl.col("agi") - threshold),
                    )
                )
                .otherwise(0.0)
            )
        else:
            df = df.with_columns(ca_reduce=pl.lit(0.0))
        df = df.with_columns(ca_xitded=(pl.col("ca_xitded_base") - pl.col("ca_reduce")).clip(0, None))
        if effective_year == 1999:
            df = df.with_columns(ca_xitded=pl.when(forced_standard()).then(0.0).otherwise(pl.col("ca_xitded")))

    if 1982 <= effective_year <= 1986:
        # The tax tables' zero-rate first bracket is the standard deduction,
        # so only itemized deductions above it are subtracted.
        df = df.with_columns(
            ca_deduc=pl.when(pl.col("ca_stded") > pl.col("ca_xitded"))
            .then(0.0)
            .otherwise(pl.col("ca_xitded") - pl.col("ca_stded"))
        )
    else:
        df = df.with_columns(ca_deduc=pl.max_horizontal(pl.col("ca_stded"), pl.col("ca_xitded")))
    df = df.with_columns(ca_taxinc=(pl.col("ca_agi") - pl.col("ca_deduc")).clip(0, None))

    # --- Tax: joint returns use the single schedule on half their income,
    # doubled; heads of household have their own schedule. ---
    brackets = p.value("brackets")
    scale = p.num("bracket_inflation")
    if effective_year <= 1981:
        # Bounds are rounded to $10, halves away from zero.
        raw_s = [[math.floor(lo * scale / 10.0 + 0.5) * 10.0, rate] for lo, rate in brackets["single"]]
        raw_h = [[math.floor(lo * scale / 10.0 + 0.5) * 10.0, rate] for lo, rate in brackets["head_of_household"]]
        brackets_s = _raw_to_start_rate(raw_s)
        brackets_h = _raw_to_start_rate(raw_h)
    else:
        brackets_s = _raw_to_start_rate(brackets["single"], scale)
        brackets_h = _raw_to_start_rate(brackets["head_of_household"], scale)
    stat_single = bracket_tax(pl.col("ca_taxinc") / pl.col("ca_ajnt"), brackets_s) * pl.col("ca_ajnt")
    stat_hoh = bracket_tax(pl.col("ca_taxinc"), brackets_h)
    df = df.with_columns(ca_regtax=pl.when(pl.col("ca_hoh")).then(stat_hoh).otherwise(stat_single))
    rate_expr = pl.when(pl.col("ca_hoh")).then(bracket_rate(pl.col("ca_taxinc"), brackets_h)).otherwise(
        bracket_rate(pl.col("ca_taxinc") / pl.col("ca_ajnt"), brackets_s)
    )

    df = df.with_columns(ca_statax=pl.col("ca_regtax"))

    # --- Exemption credits ---
    # Through 1986 a head of household claims one fewer dependent credit.
    dep_reduced_pre1987 = pl.when(pl.col("ca_hoh")).then((pl.col("depx") - 1).clip(0, None)).otherwise(pl.col("depx"))
    dep_reduced = dep_reduced_pre1987 if effective_year <= 1986 else pl.col("depx")
    if p.in_effect("exemption_credit_amounts"):
        credit = p.value("exemption_credit_amounts")
        if credit["indexed"]:
            per_taxpayer = math.floor(credit["per_taxpayer"] * aif + 0.5)
            per_dependent = math.floor(credit["per_dependent"] * aif + 0.5)
        else:
            per_taxpayer, per_dependent = float(credit["per_taxpayer"]), float(credit["per_dependent"])
        df = df.with_columns(ca_excrd=pl.lit(per_taxpayer) * pl.col("ca_txp") + pl.lit(per_dependent) * dep_reduced)
    elif p.in_effect("exemption_credit_personal"):
        phase_start = by_filing_status(p.value("exemption_credit_phaseout_start"))
        phase_step = float(p["exemption_credit_phaseout"]["step"]) / pl.col("ca_sep")
        # Each credit falls by `per_credit` for each step (or part) of AGI
        # over the threshold, and not below zero.
        per_person = p.num("exemption_credit_personal")
        per_dependent = p.num("exemption_credit_per_dependent")
        steps = ((pl.col("agi") - phase_start).clip(0, None) / phase_step).ceil()
        reduction = steps * float(p["exemption_credit_phaseout"]["per_credit"])
        df = df.with_columns(
            ca_phaded2=phase_start,
            ca_excrd=(pl.col("ca_txp_raw") + aged_count()) * (per_person - reduction).clip(0, None)
            + dep_reduced * (per_dependent - reduction).clip(0, None),
        )
    elif not p.in_effect("exemption_credit_per_dependent"):
        df = df.with_columns(ca_excrd=p["exemption_credit_base"] * p.num("exemption_credit_index") * (pl.col("ca_txp_raw") + aged_count() + dep_reduced))
    else:
        df = df.with_columns(ca_excrd=p["exemption_credit_base"] * p.num("exemption_credit_index") * (pl.col("ca_txp_raw") + aged_count()) + dep_reduced * p.num("exemption_credit_per_dependent"))

    if effective_year >= 1991 and not p.in_effect("exemption_credit_personal"):
        index = p.num("phaseout_index") if effective_year >= 1992 else 1.0
        phaded = by_filing_status({s: float(v) * index for s, v in p["phaseout_threshold"].items()})
        df = df.with_columns(ca_phaded2=phaded)
        upper_index = p.num("exemption_credit_upper_index") if effective_year <= 1998 else None
        per_credit = float(p["exemption_credit_phaseout"]["per_credit"])
        step = float(p["exemption_credit_phaseout"]["step"])
        if p.in_effect("exemption_credit_phaseout_upper"):
            excess_over = (pl.col("agi") - pl.col("ca_phaded2")).clip(0, None)
            reduction_full = (dep_reduced + pl.col("ca_txp") + aged_count()) * per_credit * (
                pl.col("agi") - pl.col("ca_phaded2")
            ) / step / pl.col("ca_sep")
            upper = p.num("exemption_credit_phaseout_upper")
            excrd_over_agi = pl.when(excess_over > upper * upper_index / pl.col("ca_sep")).then(0.0).otherwise(
                (pl.col("ca_excrd") - reduction_full).clip(0, None)
            )
        else:
            num = 1.0 + ((pl.col("agi") - pl.col("ca_phaded2")) / (step / pl.col("ca_sep"))).floor()
            excrd_over_agi = (pl.col("ca_excrd") - (dep_reduced + pl.col("ca_txp") + aged_count()) * per_credit * num).clip(0, None)

        df = df.with_columns(
            ca_excrd=pl.when(pl.col("agi") > pl.col("ca_phaded2")).then(excrd_over_agi).otherwise(pl.col("ca_excrd"))
        )

        if p.in_effect("exemption_credit_limit"):
            lim = p.value("exemption_credit_limit")
            floor_index = float(lim["floor_index"])
            ak = float(lim["rate"])
            exc = pl.when(pl.col("filing_status").is_in(["single", "head_of_household"])).then(
                lim["floor_single_or_hoh"] * floor_index
            ).otherwise(lim["floor_other"] * floor_index * pl.col("ca_sep"))
            excess_thr = pl.when(pl.col("ca_hoh")).then(lim["upper_hoh"] * upper_index).otherwise(
                lim["upper_other"] * upper_index * pl.col("ca_sep")
            )
            under_std = pl.col("ca_stded") > pl.col("ca_xitded")
            alt_a = pl.col("ca_statax") - ak * (pl.col("agi") - exc).clip(None, 0)
            alt_b = pl.col("ca_statax") - ak * (
                pl.min_horizontal(lim["agi_share"] * pl.col("agi").clip(0, None), pl.lit(0.0))
                + pl.col("otheritem") + pl.col("mortgage") + pl.col("ca_taxinc") - exc
            )
            capped = pl.when(under_std).then(alt_a).otherwise(alt_b)
            gate = (pl.col("ca_agi") > pl.col("ca_phaded2")) & (pl.col("ca_agi") < excess_thr)
            df = df.with_columns(
                ca_excrd=pl.when(gate).then(pl.min_horizontal(capped, pl.col("ca_excrd"))).otherwise(pl.col("ca_excrd"))
            )

    # Dependent filers get no exemption credit.
    df = df.with_columns(ca_excrd=pl.when(is_dependent_filer()).then(0.0).otherwise(pl.col("ca_excrd")))
    df = df.with_columns(ca_noncr=pl.col("ca_excrd"))

    # --- Low-income credit ---
    if p.in_effect("low_income_credit_flat"):
        low = p.value("low_income_credit_flat")
        base_amt = low["base"] * aif
        hy = pl.col("ca_household_income")
        df = df.with_columns(
            ca_lowcr=pl.when(hy <= float(low["household_income_limit"]) * pl.col("ca_txp"))
            .then((base_amt - (pl.col("ca_agi") - low["agi_floor"]) * low["reduction_rate"]).clip(0, None).round(mode="half_away_from_zero"))
            .otherwise(0.0)
        )
    elif p.in_effect("low_income_credit_share"):
        index = p.num("low_income_credit_index")
        div = pl.when(pl.col("filing_status").is_in(["single", "married_separate"])).then(2.0).otherwise(1.0)
        rows = [[lo * index / div, rate] for lo, rate in p.value("low_income_credit_share")]
        df = df.with_columns(ca_lowcr=pl.col("ca_statax") * interpolate_table(pl.col("ca_agi"), rows))
    else:
        df = df.with_columns(ca_lowcr=pl.lit(0.0))
    df = df.with_columns(ca_lowcr=pl.when(is_dependent_filer()).then(0.0).otherwise(pl.col("ca_lowcr")))
    df = df.with_columns(ca_noncr=pl.col("ca_noncr") + pl.col("ca_lowcr"))

    # --- Child and dependent care credit ---
    if p.in_effect("child_care_credit_expenses"):
        cc = p.value("child_care_credit_expenses")
        chmax = pl.min_horizontal(float(cc["cap_per_child"]) * pl.col("depx"), float(cc["cap_total"]))
        chwage = pl.max_horizontal(pl.col("wages"), 0.0)
        df = df.with_columns(ca_chcr=float(cc["rate"]) * pl.min_horizontal(chmax, chwage, pl.col("childcare")))
    else:
        share = p.value("child_care_credit_federal_share")
        # From 2000 the federal credit before its liability limit, undeflated.
        federal = pl.col("ccc_uncapped") if effective_year >= 2000 else pl.col("federal_chcr")
        if isinstance(share, dict):
            rate = pl.when(pl.col("ca_agi") >= share["threshold"]).then(float(share["above"])).otherwise(float(share["below"]))
        elif isinstance(share, list):
            rate = _agi_rows(share, pl.col("agi"))
        else:
            rate = pl.lit(float(share))
        df = df.with_columns(ca_chcr=rate * federal)

    df = df.with_columns(ca_statax=(pl.col("ca_statax") - pl.col("ca_noncr")).clip(0, None))

    # --- Alternative minimum tax (1987+) ---
    if effective_year >= 1987:
        # Itemizers add back property and other taxes; others add back the
        # standard deduction.
        itemizing = pl.col("ca_xitded") > pl.col("ca_stded")
        addprf = pl.when(itemizing).then(pl.col("proptax") + pl.col("otheritem")).otherwise(pl.col("ca_stded"))
        # CA-002: TAXSIM subtracts positive self-employment and Schedule E
        # income from the base.
        reduce_col = pl.col("ca_reduce")
        if behavior.include_business_and_rental_income_in_california_minimum_tax:
            alminy = (addprf + pl.col("ca_taxinc") - reduce_col).clip(0, None)
        else:
            gross_se = pl.col("psemp").clip(0, None) + pl.col("ssemp").clip(0, None)
            schedule_e = (pl.col("otherprop") + pl.col("scorp")).clip(0, None)
            alminy = (addprf + pl.col("ca_taxinc") - gross_se - schedule_e - reduce_col).clip(0, None)
        if p.in_effect("amt_exemption"):
            excl = by_filing_status(p.value("amt_exemption"))
        else:
            excl = by_filing_status({s: float(resolve_year(FEDERAL_AMT_PARAMS["exemption"][s], effective_year)) for s in _STATUSES})
        phase = by_filing_status(p.value("amt_phaseout_start"))
        phaout = p["amt_exemption_phaseout_rate"] * (alminy - phase).clip(0, None)
        exclnt = (excl - phaout).clip(0, None)
        alminc = (alminy - exclnt).clip(0, None)
        df = df.with_columns(ca_amt=(alminc * p.num("amt_rate") - pl.col("ca_statax")).clip(0, None))
    else:
        df = df.with_columns(ca_amt=pl.lit(0.0))

    df = df.with_columns(ca_statax=pl.col("ca_statax") + pl.col("ca_amt"))

    # --- Renter's credit, for renters paying no property tax ---
    renter = (pl.col("proptax") < 1) & (pl.col("rentpaid") > 0)
    single = files_single()
    sep = files_separate()
    if p.in_effect("renter_credit"):
        rc = p.value("renter_credit")
        if "per_taxpayer" in rc:
            index = p.num("renter_credit_index")
            units = pl.when(single | sep).then(1.0).otherwise(2.0)
            rcred = pl.when(pl.col("ca_agi") <= rc["agi_limit_per_taxpayer"] * units * index).then(
                rc["per_taxpayer"] * units
            ).otherwise(0.0)
        elif "agi_limit" in rc:
            index = p.num("renter_credit_index")
            use_single = single | sep
            limit = pl.when(use_single).then(rc["agi_limit"]["single"]).otherwise(rc["agi_limit"]["other"]) * index
            band = pl.when(use_single).then(rc["half_band"]["single"]).otherwise(rc["half_band"]["other"]) * index
            amount = pl.when(use_single).then(float(rc["single"])).otherwise(float(rc["other"]))
            rcred = (
                pl.when(pl.col("ca_agi") <= limit).then(amount)
                .when(pl.col("ca_agi") <= limit + band).then(0.5 * amount)
                .otherwise(0.0)
            )
        elif effective_year <= 1978:
            rcred = pl.lit(float(rc["single"]))
        else:
            rcred = pl.when(single).then(float(rc["single"])).otherwise(float(rc["other"]) / pl.col("ca_sep"))
    else:
        rcred = pl.lit(0.0)
    rcred = pl.when(renter).then(rcred).otherwise(0.0)
    df = df.with_columns(ca_rcred=rcred)
    df = df.with_columns(ca_statax=(pl.col("ca_statax") - rcred).clip(0, None))

    if p.value("child_care_credit_refundable"):
        df = df.with_columns(ca_statax=pl.col("ca_statax") - pl.col("ca_chcr"))
    else:
        df = df.with_columns(ca_statax=(pl.col("ca_statax") - pl.col("ca_chcr")).clip(0, None))

    if p.in_effect("mental_health_tax"):
        mh = p.value("mental_health_tax")
        df = df.with_columns(ca_statax=pl.col("ca_statax") + float(mh["rate"]) * (pl.col("ca_taxinc") - float(mh["threshold"])).clip(0, None))

    # --- California EITC and Young Child Tax Credit ---
    if effective_year >= 2015:
        ieic = pl.col("dep18").clip(0, 3).cast(pl.Int64)
        earned = pl.col("wages") if effective_year <= 2016 else pl.col("earned_income")

        def by_children(values: list[float], default: float | None = None) -> pl.Expr:
            out = pl.lit(float(values[-1]) if default is None else default)
            for i in range(len(values) - 1 if default is None else len(values)):
                out = pl.when(ieic == i).then(float(values[i])).otherwise(out)
            return out

        if p.in_effect("caleitc"):
            ce = p.value("caleitc")
            cr = by_children(ce["max_credit"], 0.0)
            am = by_children(ce["max_earned"], 0.0)
            posagi = pl.col("agi").clip(0, None)
            base1 = pl.when(earned < 0.5 * am).then(earned).otherwise((am - earned).clip(0, None))
            earncr = pl.when(earned > 0).then(base1 * cr / (0.5 * am)).otherwise(0.0)
            base_agi = (am - posagi).clip(0, None)
            earncr = pl.when(posagi >= 0.5 * am).then(pl.min_horizontal(earncr, base_agi * cr / (0.5 * am))).otherwise(earncr)
            if "second_phaseout_start" in ce:
                ym1 = by_children(ce["second_phaseout_start"], 0.0)
                em1 = by_children(ce["credit_at_second_phaseout"], 0.0)
                limit = by_children(ce["income_limit"], 0.0)
                tgbeta = em1 / (limit - ym1)
                base2 = (limit - pl.max_horizontal(posagi, earned)).clip(0, None)
                earncr2 = pl.when(earned > 0).then(base2 * tgbeta).otherwise(pl.lit(0.0))
                earncr = pl.when((earned > ym1) | (posagi > ym1)).then(earncr2).otherwise(earncr)
        else:
            # The credit is the lesser of the schedule at earned income and
            # at the higher of earned income and AGI.
            ce = p.value("caleitc_schedule")
            rate_in = by_children(ce["phase_in_rate"]) * float(ce["adjustment_factor"])
            amount = by_children(ce["earned_income_amount"])
            final_start = by_children(ce["final_start"])
            final_end = float(ce["final_end"])
            maximum = amount * rate_in
            first_range = (maximum - final_start) / rate_in
            second_start = amount + first_range

            def caleitc_at(income):
                after_first = income.clip(None, amount) * rate_in - (income - amount).clip(0, None).clip(None, first_range) * rate_in
                second_share = ((income - second_start) / (final_end - second_start)).clip(None, 1.0)
                return pl.when(income > second_start).then(after_first * (1 - second_share)).otherwise(after_first)

            earned_now = pl.col("earned_income")
            credit = pl.min_horizontal(caleitc_at(earned_now), caleitc_at(pl.max_horizontal(earned_now, pl.col("agi"))))
            earncr = pl.when(pl.col("agi") < final_end).then(credit.clip(0, None)).otherwise(0.0)
        if behavior.apply_state_childless_eitc_minimum_age and p.in_effect("eitc_childless_minimum_age"):
            older = pl.max_horizontal(pl.col("page"), pl.col("sage"))
            too_young = (pl.col("num_children") == 0) & (older > 0) & (older < p.num("eitc_childless_minimum_age"))
            earncr = pl.when(too_young).then(0.0).otherwise(earncr)
        disqy = (
            (pl.col("stcg") + pl.col("ltcg")).clip(0, None) + pl.col("dividends") + dividend_input_adjustment() + pl.col("intrec")
            + pl.col("otherprop").clip(0, None)
        )
        earncr = pl.when(disqy >= p.num("eitc_disqualified_income_limit")).then(0.0).otherwise(earncr)
        earncr = pl.when((pl.col("ca_sep") == 2) | is_dependent_filer()).then(0.0).otherwise(earncr)
        df = df.with_columns(ca_earncr=earncr)
        # Young Child Tax Credit: requires a state EITC and an
        # EITC-qualifying child; the earnings test uses children under 6.
        if p.in_effect("young_child_credit"):
            yc = p.value("young_child_credit")
            young_gate = (pl.col("ca_earncr") > 0) & (pl.col("dep18") > 0)
            if effective_year >= 2022:
                # A filer with little or no earned income and no large
                # gross-income loss also qualifies (R&TC 17052.1).
                small_losses = (pl.col("agi") < float(p.value("caleitc_schedule")["final_end"])) & (
                    pl.col("earned_income") <= float(yc["loss_threshold"])
                )
                young_gate = young_gate | ((pl.col("dep18") > 0) & small_losses)
            if behavior.require_young_child_for_california_yctc:
                young_gate = young_gate & (pl.col("dep6") > 0)
            yc_amount = yc["amount"]
            yc_threshold = yc["earnings_threshold"]
            yc_rate = yc["phaseout_rate"]
            young_count = pl.col("dep6") if behavior.pay_california_yctc_per_young_child else pl.col("dep6").clip(0, 1)
            young_low = yc_amount * young_count
            young_high = (yc_amount - yc_rate * (earned - yc_threshold)).clip(0, None)
            young = pl.when(earned <= yc_threshold).then(young_low).otherwise(young_high)
            df = df.with_columns(ca_young=pl.when(young_gate).then(young).otherwise(0.0))
        else:
            df = df.with_columns(ca_young=pl.lit(0.0))
    else:
        df = df.with_columns(
            ca_earncr=pl.lit(0.0),
            ca_young=pl.lit(0.0),
        )

    df = df.with_columns(siitax=(pl.col("ca_statax") - pl.col("ca_earncr") - pl.col("ca_young")) * flate)
    return with_state_detail(
        df,
        agi=pl.col("ca_agi"),
        standard_deduction=pl.col("ca_stded"),
        itemized_deductions=pl.col("ca_xitded"),
        taxable_income=pl.col("ca_taxinc"),
        child_care_credit=pl.col("ca_chcr"),
        eic=pl.col("ca_earncr"),
        credits=pl.col("ca_noncr") + pl.col("ca_rcred") + pl.col("ca_chcr") + pl.col("ca_earncr") + pl.col("ca_young"),
        rate=rate_expr,
    )
