"""Wisconsin individual income tax calculator."""


from __future__ import annotations
import polars as pl

from taxsim_py.engine.brackets import bracket_rate, bracket_tax, scale_brackets
from taxsim_py.engine.inputs import aged_count, federal_exemption_count, files_head_of_household, files_joint, files_separate, files_single, is_dependent_filer, taxpayer_count
from taxsim_py.engine.schema import PARAMETERS_ROOT, YearParams, load_yaml, resolve_year
from taxsim_py.engine.state import (
    dividend_input_adjustment,
    federal_capital_gain_in_agi,
    by_filing_status,
    checkpoint,
    forced_standard,
    household_income,
    interpolate_table,
    net_earnings_by_spouse,
    pre1987_federal_itemizing,
    tier_values,
    unemployment_total,
    with_state_detail,
)
from taxsim_py.behavior import BehaviorProfile, TAXSIM_BEHAVIOR
from taxsim_py.engine.state_extrapolation import deflate_for_extrapolation, resolve_state_year

WI_PARAMS = load_yaml(PARAMETERS_ROOT / "states" / "wi" / "income_tax.yaml")
CAPITAL_GAINS_PARAMS = load_yaml(PARAMETERS_ROOT / "national" / "capital_gains.yaml")


def _low_income_deduction(rows: list[list[float]], agi: pl.Expr, depx: pl.Expr, depadd: pl.Expr) -> pl.Expr:
    """TAXSIM `witab`: the amount for the first row whose bound is at least AGI.

    Row amounts are by number of dependents; more dependents than the row
    lists take the last amount plus the add-on for each extra dependent.
    """
    width = len(rows[0]) - 1
    amounts = tier_values(
        agi, [float(r[0]) for r in rows], *[[float(r[k]) for r in rows] for k in range(1, width + 1)]
    )
    value = amounts[-1]
    for count in reversed(range(width - 1)):
        value = pl.when(depx == count).then(amounts[count]).otherwise(value)
    return value + (depx - (width - 1)).clip(0, None) * depadd


def _standard_deduction(rows: list[list[float]], agi: pl.Expr, index: float) -> pl.Expr:
    """First row whose AGI bound is at least AGI: max(0, amount - rate * (AGI - start))."""
    uppers = [float(r[0]) if r[0] >= 1.0e19 else float(r[0]) * index for r in rows]
    amount, rate, start = tier_values(
        agi, uppers, [float(r[1]) * index for r in rows], [float(r[2]) for r in rows], [float(r[3]) * index for r in rows]
    )
    return (amount - rate * (agi - start)).clip(0, None)


def compute_wi_tax(df: pl.DataFrame, year: int, behavior: BehaviorProfile = TAXSIM_BEHAVIOR) -> pl.DataFrame:
    """Calculate Wisconsin income tax for each row."""
    effective_year, flate = resolve_state_year(year, behavior, WI_PARAMS)
    y = effective_year
    p = YearParams(WI_PARAMS, effective_year)
    # Household income (`hy`) is read before projected years are deflated.
    df = df.with_columns(wi_household_income_undeflated=household_income(behavior), wi_ui=unemployment_total())
    df = deflate_for_extrapolation(df, flate, extra=("wi_ui",))

    is_joint = files_joint()
    is_sep = files_separate()
    single_like = (files_single()) | (files_head_of_household())
    sep = pl.when(is_sep).then(2.0).otherwise(1.0)
    txp = taxpayer_count()
    aged = aged_count()
    depx = pl.col("depx")
    dependent_filer = is_dependent_filer()
    fed_agi = pl.col("agi")
    ss_taxable = pl.col("taxable_social_security")
    ui = pl.col("wi_ui")
    stcg, ltcg = pl.col("stcg"), pl.col("ltcg")
    fullcg = stcg + ltcg

    # --- Wisconsin AGI ---
    agi = fed_agi
    if y == 2020:
        agi = agi + ui - pl.col("taxable_unemployment")
    if p.in_effect("unemployment_subtraction_base"):
        bases = p.value("unemployment_subtraction_base")
        base = pl.when(is_joint).then(bases["married_joint"]).otherwise(bases["other"])
        half = p["unemployment_share"] * (fed_agi - (base + ss_taxable)).clip(0, None)
        agi = agi - (ui - pl.min_horizontal(ui, half)).clip(0, None)
    # Capital gains and losses.
    if p.in_effect("capital_loss_reduction"):
        c = p.value("capital_loss_reduction")
        loss = pl.when(stcg < 0).then(ltcg).otherwise(fullcg)
        limited = pl.max_horizontal(loss, -c["limit_per_taxpayer"] * txp)
        change = pl.min_horizontal(limited - c["share"] * loss, pl.lit(0.0))
        in_range = (loss > -c["range_per_taxpayer"] * txp) & (loss < 0)
        agi = agi + pl.when((fullcg <= 0) & in_range).then(change).otherwise(0.0)
    elif p.in_effect("capital_loss_limit"):
        loss_limit = float(resolve_year(CAPITAL_GAINS_PARAMS["net_capital_loss_limit"], y))
        # The federal loss limit reaches the state deflated in projected years.
        caplss = pl.max_horizontal(fullcg, -loss_limit / flate / sep).abs()
        limit = p.num("capital_loss_limit")
        if p.in_effect("taxsim_capital_loss_limit") and not behavior.limit_wisconsin_capital_loss_to_statutory_amount:
            limit = p.num("taxsim_capital_loss_limit")
        closswi = pl.min_horizontal(caplss, pl.lit(limit), agi.clip(0, None))
        agi = agi + pl.when(fullcg <= 0).then(caplss - closswi).otherwise(0.0)
    if p.in_effect("capital_gain_exclusion_addback"):
        agi = agi + pl.when(fullcg > 0).then(p.num("capital_gain_exclusion_addback") * pl.col("pre1987_capded")).otherwise(0.0)
    elif y >= 1987:
        excluded = pl.min_horizontal(fullcg, ltcg)
        if behavior.floor_wisconsin_capital_gain_exclusion:
            # TAXSIM lets a long-term loss make the exclusion negative.
            excluded = excluded.clip(0, None)
        agi = agi - pl.when(fullcg > 0).then(p.num("capital_gain_exclusion") * excluded).otherwise(0.0)
    if 1982 <= y <= 1986:
        agi = agi + pl.col("pre1987_twoded")
    if 1986 <= y <= 2007:
        agi = agi - pl.when(pl.col("gssi") > 0).then((ss_taxable - pl.col("gssi") / 2).clip(0, None)).otherwise(0.0)
    elif y >= 2008:
        agi = agi - ss_taxable
    # Unemployment compensation up to half of federal AGI above a base.
    ui_base = by_filing_status(p["unemployment_base"])
    ui_taxable = pl.min_horizontal(ui, p["unemployment_share"] * (fed_agi - ui_base - ss_taxable).clip(0, None))
    if y != 2020:
        agi = agi - pl.when(ui > 0).then(ui - ui_taxable).otherwise(0.0)
    # Child and dependent care expenses (2011 on, with a federal credit).
    if p.in_effect("child_care_subtraction_per_child") and (y <= 2021 or behavior.pay_wisconsin_child_care_credit_per_statute):
        children = pl.when(pl.col("dep13") > 0).then(pl.col("dep13")).otherwise(depx).clip(None, 2)
        child = pl.min_horizontal(pl.col("childcare"), p.num("child_care_subtraction_per_child") * children)
        child = pl.when(is_joint).then(
            pl.min_horizontal(child, pl.col("pwages"), pl.col("swages")).clip(0, None)
        ).otherwise(child)
        agi = agi - pl.when(pl.col("federal_chcr") > 0).then(child).otherwise(0.0)
    # Retirement income exclusion for taxpayers 65 or older (2009 on): up to $5,000 each when federal AGI is under $15,000
    # ($30,000 joint). TAXSIM applies it from 2021, and in 2023-2024 at age 67 with the limit per qualifying taxpayer.
    if 2009 <= y <= 2024:
        pension = pl.col("pensions")
        limit = pl.when(is_joint).then(30000.0).otherwise(15000.0)
        old_head, old_spouse = pl.col("page") >= 65, (pl.col("sage") >= 65) & is_joint
        shares = pl.when(is_joint).then(pension / 2).otherwise(pension)
        exclusion = (
            pl.when(old_head).then(pl.min_horizontal(pl.lit(5000.0), shares)).otherwise(0.0)
            + pl.when(old_spouse).then(pl.min_horizontal(pl.lit(5000.0), shares)).otherwise(0.0)
        )
        in_range = fed_agi < limit
        if not behavior.apply_wisconsin_retirement_exclusion_per_statute:
            if y < 2021:
                in_range = pl.lit(False)
            elif y >= 2023:
                # Ages 67 or older, the limit multiplied by the number of qualifying taxpayers, pensions not split.
                head_67, spouse_67 = pl.col("page") >= 67, pl.col("sage") >= 67
                numold = head_67.cast(pl.Float64) + spouse_67.cast(pl.Float64)
                exclusion = pl.min_horizontal(5000.0 * numold, pl.col("pensions"))
                in_range = (numold > 0) & (fed_agi < 15000.0 * numold)
            else:
                # TAXSIM's 2021-2022 routine reads only the pension input, not the other retirement income.
                pass
        agi = agi - pl.when(in_range).then(exclusion).otherwise(0.0)
    df, (agi,) = checkpoint(df, wi_agi=agi)

    # --- Standard deduction ---
    depadd = interpolate_table(agi, p.value("dependent_addition")) if p.in_effect("dependent_addition") else pl.lit(0.0)
    if p.in_effect("standard_deduction_share"):
        c = p.value("standard_deduction_share")
        stded = (c["rate"] * agi).clip(0, c["cap"]) + depadd
    elif not p.in_effect("standard_deduction"):
        stded = pl.lit(0.0)
    else:
        tables = p.value("standard_deduction")
        index = p.num("standard_deduction_index")
        if behavior.start_wisconsin_standard_deduction_tiers_at_their_range:
            # TAXSIM measures these tiers from an earlier start.
            tables = {key: [list(row) for row in rows] for key, rows in tables.items()}
            if 2000 <= y <= 2015:
                tables["head_of_household"][2][3] = tables["head_of_household"][1][0]
            if 1988 <= y <= 1998 and behavior.start_wisconsin_separate_standard_deduction_tier_at_its_range:
                tables["married_separate"][1][3] = tables["married_separate"][0][0]
        if 2016 <= y and not behavior.start_wisconsin_head_of_household_third_tier_per_statute and len(tables["head_of_household"]) > 3:
            # TAXSIM measures the head of household third tier from where the second ends.
            tables = {key: [list(row) for row in rows] for key, rows in tables.items()}
            tables["head_of_household"][2][3] = tables["head_of_household"][1][0]
            if y >= 2023:
                # From 2023 it is the single schedule, measured from the head of household's own first bound.
                third = tables["head_of_household"][2]
                third[1] = tables["single"][0][1] - third[2] * (third[3] - tables["head_of_household"][0][0])
        if not behavior.use_wisconsin_form_standard_deduction and p.in_effect("taxsim_standard_deduction"):
            tables = dict(p.value("taxsim_standard_deduction"))
            index = p.num("taxsim_standard_deduction_index")
            separate = p.value("taxsim_separate_standard_deduction")
            tables["married_separate"] = [
                [separate["upper"], separate["amount"], 0, 0],
                [1.0e20, separate["amount"], separate["separate_rate"] * index, separate["upper"]],
            ]
        sep_uses_single = y >= 2023 and not behavior.use_wisconsin_separate_standard_deduction_from_2023
        stded = (
            pl.when(files_single()).then(_standard_deduction(tables["single"], agi, index))
            .when(files_head_of_household()).then(_standard_deduction(tables["head_of_household"], agi, index))
            .when(is_joint).then(_standard_deduction(tables["married_joint"], agi, index))
            .otherwise(_standard_deduction(tables["single" if sep_uses_single else "married_separate"], agi, index))
        )
    d = p["dependent_standard_deduction"]
    stded = pl.when(dependent_filer & (y < 2023 or behavior.limit_wisconsin_dependent_standard_deduction_from_2023)).then(
        pl.min_horizontal(stded, pl.max_horizontal(pl.lit(float(d["floor"])), pl.col("earned_income") + d["earned_addition"]))
    ).otherwise(stded)
    if p.in_effect("low_income_deduction"):
        tables = p.value("low_income_deduction")

        def low(key: str) -> pl.Expr:
            return _low_income_deduction(tables[key], agi, depx, depadd)

        lowinc = (
            pl.when(single_like & (aged == 0)).then(low("single_0"))
            .when(single_like).then(low("single_1"))
            .when(aged == 0).then(low("married_0"))
            .when(aged == 1).then(low("married_1"))
            .otherwise(low("married_2"))
        )
        stded = pl.max_horizontal(stded, lowinc)

    # --- Itemized deductions (a credit from 1986) ---
    contr = pl.col("charity_cash")
    inrst = pl.col("mortgage")
    if not behavior.keep_wisconsin_mortgage_interest_cents:
        # TAXSIM keeps mortgage interest as a single-precision whole number.
        inrst = inrst.cast(pl.Int64).cast(pl.Float32).cast(pl.Float64)
    if y <= 1985:
        # The federal gross itemized total (`comnew(30)`); from 1979 less
        # state income and property taxes.
        gross, _, _ = pre1987_federal_itemizing(y)
        xitded = gross
        if y >= 1979:
            xitded = gross - pl.col("state_sales_or_income_tax_ded") - pl.col("proptax")
    else:
        xitded = contr + inrst
        if 1987 <= y <= 2022 and not behavior.exclude_other_itemized_deductions_from_wisconsin_credit:
            xitded = xitded + pl.col("otheritem")
    xitded = xitded.clip(0, None)
    if y == 1999 and not behavior.keep_wisconsin_1999_itemized_credit:
        # TAXSIM zeroes it on the federal standard-deduction run.
        xitded = pl.when(forced_standard()).then(0.0).otherwise(xitded)
    df, (stded, xitded) = checkpoint(df, wi_stded=stded, wi_xitded=xitded)

    if y <= 1985:
        taxinc = (agi - pl.max_horizontal(xitded, stded)).clip(0, None)
    else:
        taxinc = (agi - stded).clip(0, None)
    exemp = pl.lit(0.0)
    if p.in_effect("exemption"):
        people = depx + txp
        if flate != 1.0 and y >= 2023 and not behavior.deflate_wisconsin_exemption_count_per_statute:
            # TAXSIM's exemption is the personal amount times the deflated federal exemption count.
            people = federal_exemption_count(y) / flate
        exemp = pl.when(dependent_filer).then(0.0).otherwise(
            p.num("exemption") * people + p.num("exemption_aged") * aged
        )
    df, (taxinc,) = checkpoint(df, wi_taxinc=(taxinc - exemp).clip(0, None))

    # --- Tax ---
    schedules = p.value("brackets")
    index = p.num("bracket_index")
    if y <= 1985:
        table = scale_brackets(schedules["all"], index)
        # Joint returns: the lesser of income splitting and the spouses'
        # shares, whose lookup leaves the rate at the higher earner's share.
        yh = pl.max_horizontal(pl.col("pwages"), pl.col("swages")) + (taxinc - pl.col("pwages") - pl.col("swages")) / 2.0
        yw = taxinc - yh
        split = 2.0 * bracket_tax(taxinc / 2.0, table)
        spouses = bracket_tax(yh.clip(0, None), table) + bracket_tax(yw.clip(0, None), table)
        statax = pl.when(is_joint).then(pl.min_horizontal(split, spouses)).otherwise(bracket_tax(taxinc, table))
        rate = pl.when(is_joint).then(bracket_rate(yh.clip(0, None), table)).otherwise(bracket_rate(taxinc, table))
    else:
        single_index = p.num("single_bracket_index") if p.in_effect("single_bracket_index") else index
        single = scale_brackets(schedules["single"], single_index)
        # TAXSIM indexes only the single schedule in 2015-2018.
        joint_index = single_index if behavior.index_wisconsin_joint_brackets else index
        joint = scale_brackets(schedules["married_joint"], joint_index)
        separate = schedules["married_separate"]
        if separate is None and behavior.tax_wisconsin_1986_separate_returns:
            # Separate returns use the joint schedule at half the bounds.
            separate = [[b[0] / 2.0, b[1]] for b in schedules["married_joint"]]
        if y == 2022 and not behavior.use_wisconsin_published_2022_separate_brackets_per_statute:
            # TAXSIM taxes a separate return on the joint schedule at twice the income, then halves the tax.
            separate = [[b[0] / 2.0, b[1]] for b in schedules["married_joint"]]
        statax = pl.when(single_like).then(bracket_tax(taxinc, single)).when(is_joint).then(bracket_tax(taxinc, joint))
        rate = pl.when(single_like).then(bracket_rate(taxinc, single)).when(is_joint).then(bracket_rate(taxinc, joint))
        if separate is None:
            # 1986 has no schedule for separate returns.
            statax = statax.otherwise(0.0)
            rate = rate.otherwise(0.0)
        else:
            separate = scale_brackets(separate, joint_index)
            statax = statax.otherwise(bracket_tax(taxinc, separate))
            rate = rate.otherwise(bracket_rate(taxinc, separate))
    if p.in_effect("tax_reduction"):
        r = p.value("tax_reduction")
        statax = statax - pl.min_horizontal(r["rate"] * statax, pl.lit(float(r["cap"])))
    if p.in_effect("surtax"):
        statax = statax * p.num("surtax")
    df, (statax,) = checkpoint(df, wi_tax_before_credits=statax)

    # --- Credits ---
    # TAXSIM reads the household income from its deflated copy (`data(159)`) in a projected year.
    hy = pl.col("wi_household_income_undeflated") / flate
    c = p.value("exemption_credit") if p.in_effect("exemption_credit") else {}
    if "taxpayer" in c:
        exempc = c["taxpayer"] * txp + c["aged"] * aged + c["dependent"] * depx + pl.when(
            files_head_of_household()
        ).then(float(c["head_of_household"])).otherwise(0.0)
    elif c and "income_limit" not in c:
        exempc = c["aged"] * aged + c["dependent"] * depx
    elif c:
        limit = by_filing_status(c["income_limit"])
        reduction = c["phaseout_rate"] * (hy - limit)
        per_return = pl.when(is_joint).then(c["aged"] * aged).otherwise(float(c["aged"]))
        aged_credit = (
            pl.when(hy <= limit).then(per_return)
            .when(hy <= limit + c["width"]).then(per_return - reduction)
            .otherwise(0.0)
        )
        exempc = c["dependent"] * depx + pl.when(aged > 0).then(aged_credit).otherwise(0.0)
    else:
        exempc = pl.lit(0.0)

    xcred = p.num("itemized_credit_rate") * (xitded - stded).clip(0, None) if p.in_effect("itemized_credit_rate") else pl.lit(0.0)

    renter = (pl.col("proptax") < 1) & (pl.col("rentpaid") > 0)
    if p.in_effect("school_property_credit_flat"):
        c = p.value("school_property_credit_flat")
        spcred = pl.when(pl.col("rentpaid") > 0).then(float(c["renter"])).otherwise(
            (c["rate"] * pl.col("proptax")).clip(c["minimum"], c["maximum"])
        )
    elif p.in_effect("school_property_credit_max"):
        cmax = p.num("school_property_credit_max") / sep
        # TAXSIM's renter credit multiplies its renter indicator (1), not rent.
        renter_base = renter.cast(pl.Float64)
        if behavior.base_wisconsin_renter_credit_on_rent:
            renter_base = pl.when(renter).then(pl.col("rentpaid")).otherwise(0.0)
        if not behavior.use_wisconsin_statutory_school_credit:
            # The 2026 TAXSIM build adds the renter and owner credits, caps the sum at the
            # unhalved maximum.
            rent_part = p.num("school_property_credit_renter_rate") * pl.col("rentpaid") / p["school_property_credit_renter_divisor"]
            owner_part = p.num("school_property_credit_owner_rate") * pl.col("proptax") / p["school_property_credit_owner_divisor"]
            spcred = pl.min_horizontal(rent_part + owner_part, p.num("school_property_credit_max"))
        else:
          spcred = pl.when(pl.col("rentpaid") > 0).then(
            pl.min_horizontal(
                p.num("school_property_credit_renter_rate") * renter_base
                / p["school_property_credit_renter_divisor"],
                cmax,
            )
        ).otherwise(
            pl.min_horizontal(
                p.num("school_property_credit_owner_rate") * pl.col("proptax") / p["school_property_credit_owner_divisor"],
                cmax,
            )
        )
    else:
        spcred = pl.lit(0.0)

    chcr = p.num("child_care_credit_share") * pl.col("federal_chcr") if p.in_effect("child_care_credit_share") else pl.lit(0.0)
    if y >= 2022 and p.in_effect("taxsim_child_care_credit_share") and not behavior.pay_wisconsin_child_care_credit_per_statute:
        # TAXSIM pays half the federal credit from 2022 (the statute's credit is not in the table).
        chcr = p.num("taxsim_child_care_credit_share") * pl.col("ccc_uncapped") / flate
    if y >= 2024 and p.in_effect("taxsim_child_care_credit_expense_based") and not behavior.pay_wisconsin_child_care_credit_per_statute:
        # TAXSIM from 2024: a rate falling with AGI (steps rounded up) on the expenses of up to two children under 13,
        # limited to the lower spouse's earnings.
        c24 = p.value("taxsim_child_care_credit_expense_based")
        earn_primary, earn_spouse = net_earnings_by_spouse(behavior)
        earnings = pl.when(files_joint()).then(pl.min_horizontal(earn_primary, earn_spouse)).otherwise(earn_primary)
        children = pl.col("dep13").clip(None, 2).floor()
        child = pl.min_horizontal(earnings.clip(0, None), float(c24["expense_per_child"]) * children, pl.col("childcare"))
        steps = ((agi - float(c24["rate_step_start"])).clip(0, None) / float(c24["rate_step"])).ceil()
        chcr = pl.max_horizontal(pl.lit(float(c24["rate_floor"])), float(c24["rate_top"]) - float(c24["rate_decrement"]) * steps) * child
    if y < 2022 and not behavior.pay_wisconsin_child_care_credit_per_statute:
        # TAXSIM multiplies the share by a federal variable that is never set (always zero).
        chcr = pl.lit(0.0)

    wcred = pl.lit(0.0)
    if p.in_effect("working_families_credit"):
        c = p.value("working_families_credit")
        limit = pl.when(is_joint).then(c["income_limit"]["married_joint"]).otherwise(c["income_limit"]["other"])
        intercept = pl.when(is_joint).then(c["intercept"]["married_joint"]).otherwise(c["intercept"]["other"])
        share = intercept - c["slope"] * agi
        wcred = (
            pl.when(agi <= c["full_income_per_taxpayer"] * txp).then(statax)
            .when(agi <= limit).then(share * (statax - exempc - xcred - spcred).clip(0, None))
            .otherwise(0.0)
        )
        wcred = pl.when(dependent_filer).then(0.0).otherwise(wcred)

    eitc = pl.col("pre1987_earncr") if y <= 1986 else pl.col("eitc")
    earncr = pl.lit(0.0)
    shares = p.value("eitc_share") if p.in_effect("eitc_share") else None
    if p.in_effect("eitc_own_schedule"):
        c = p.value("eitc_own_schedule")
        earned = pl.col("earned_income")
        income = pl.max_horizontal(agi, earned)

        def own(n: str) -> pl.Expr:
            k = c[n]
            credit = pl.min_horizontal(k["rate"] * earned, pl.lit(float(k["maximum"]))).clip(0, None)
            credit = pl.when(income > c["phaseout_start"]).then(
                (credit - k["phaseout_rate"] * (income - c["phaseout_start"])).clip(0, None)
            ).otherwise(credit)
            return pl.when(is_sep | dependent_filer).then(0.0).otherwise(credit)

        earncr = (
            pl.when(depx == 1).then(own("one")).when(depx == 2).then(own("two")).when(depx >= 3).then(own("three"))
            .otherwise(0.0)
        )
    elif isinstance(shares, dict):
        share = pl.lit(0.0)
        # From 2023 the credit follows the qualifying children (TAXSIM's `d(8) - d(209)`), before it the dependents.
        kids = pl.col("num_children") if y >= 2023 else depx
        for n, key in ((1, "one"), (2, "two"), (3, "three")):
            share = pl.when((kids == n) if n < 3 else (kids >= n)).then(float(shares[key])).otherwise(share)
        earncr = share * eitc
    elif shares is not None:
        earncr = float(shares) * eitc

    # --- Married couple credit ---
    twocrd = pl.lit(0.0)
    if p.in_effect("married_couple_credit"):
        c = p.value("married_couple_credit")
        business = 0.5 * (pl.col("psemp") + pl.col("ssemp"))
        earh = (pl.col("pwages") + pl.col("pbusinc") + pl.col("pprofinc") + business).clip(0, None)
        earw = (pl.col("swages") + pl.col("sbusinc") + pl.col("sprofinc") + business).clip(0, None)
        if not behavior.split_wisconsin_self_employment_between_spouses_per_statute:
            # TAXSIM credits each spouse with their own earnings: wages plus self-employment income (92.35% of it from 2023).
            share = 0.9235 if y >= 2023 else 1.0
            earh = (pl.col("pwages") + share * (pl.col("psemp") + pl.col("pbusinc") + pl.col("pprofinc"))).clip(0, None)
            earw = (pl.col("swages") + share * (pl.col("ssemp") + pl.col("sbusinc") + pl.col("sprofinc"))).clip(0, None)
        twocrd = pl.when(is_joint).then(c["rate"] * pl.min_horizontal(pl.min_horizontal(earh, earw), pl.lit(float(c["cap"])))).otherwise(0.0)
    if y in (2021, 2022) and not behavior.apply_federal_investment_income_limit_to_wisconsin_eitc_per_statute:
        # TAXSIM applies its own lower investment income limit to the Wisconsin credit in 2021 and 2022.
        investment = (
            federal_capital_gain_in_agi(y).clip(0, None) + pl.col("dividends") + dividend_input_adjustment() + pl.col("intrec")
            + pl.col("otherprop").clip(0, None)
        )
        earncr = pl.when(investment > (3650.0 if y == 2021 else 3800.0)).then(0.0).otherwise(earncr)
    credit = xcred + exempc + earncr + chcr + spcred + wcred
    if y <= 1988:
        statax = (statax - credit - twocrd).clip(0, None)
    else:
        statax = (statax - exempc - xcred - spcred - wcred - chcr - twocrd).clip(0, None)

    # --- Minimum tax ---
    amt = pl.lit(0.0)
    if p.in_effect("minimum_tax_preferences"):
        c = p.value("minimum_tax_preferences")
        xtra = (contr + inrst - c["agi_share"] * agi.clip(0, None)).clip(0, None)
        amt = pl.when(xitded > c["itemized_threshold"]).then(
            (pl.col("pre1987_capded") + xtra - c["exemption"]).clip(0, None) * c["rate"]
        ).otherwise(0.0)
    elif p.in_effect("minimum_tax_federal_share"):
        amt = (p.num("minimum_tax_federal_share") * pl.col("pre1987_almtax")).clip(0, None)
    elif p.in_effect("amt_exemption_single"):
        alminy = pl.col("amt_income") - ss_taxable
        alminy = alminy - pl.when(ui > 0).then(ui - ui_taxable).otherwise(0.0)
        gains = pl.when(stcg >= 0).then(ltcg.clip(0, None)).otherwise(fullcg.clip(0, None))
        alminy = alminy + p.num("capital_gain_exclusion") * gains
        rate_out = p["amt_phaseout_rate"]
        exempt_single = (p.num("amt_exemption_single") - rate_out * (alminy - p.num("amt_phaseout_single")).clip(0, None)).clip(0, None)
        # TAXSIM gives separate returns the full married exemption.
        married_exemption = p.num("amt_exemption_married") / (sep if behavior.halve_wisconsin_separate_amt_exemption else 1.0)
        exempt_married = (
            married_exemption - rate_out * (alminy - p.num("amt_phaseout_married") / sep).clip(0, None)
        ).clip(0, None)
        alminy = alminy - pl.when(single_like).then(exempt_single).otherwise(exempt_married)
        amt = (p["amt_rate"] * alminy - statax).clip(0, None)
        statax = statax + amt
    df, (statax, amt) = checkpoint(df, wi_after_amt=statax, wi_amt=amt)

    if not behavior.add_wisconsin_minimum_tax_once:
        # TAXSIM adds the minimum tax again where it exceeds the tax now.
        statax = statax + (amt - statax).clip(0, None)

    # --- Homestead credit (refundable) ---
    c = p.value("homestead_credit")
    rentpaid = pl.col("rentpaid")
    homestead_rate = c["rate"]
    if y == 1983 and not behavior.credit_wisconsin_1983_homestead_excess:
        # TAXSIM's 1983 test repeats `le` for `gt`, so larger amounts get nothing.
        homestead_rate = 0.0
    hymod = (hy - c["dependent_deduction"] * depx).clip(0, None)
    income_test = hy if y <= 1988 else hymod
    # TAXSIM's 1989-1990 test uses net income but the reduction gross income.
    reduce_on = hymod if y >= 1991 or (y >= 1989 and behavior.use_wisconsin_net_household_income_1989_1990) else hy
    rent_share = c["rent_share"]
    if y >= 1991 and not behavior.count_wisconsin_rent_at_statutory_share:
        # TAXSIM takes a fifth of rent as property tax from 1991.
        rent_share = float(p.num("taxsim_rent_share"))
    ptax = pl.min_horizontal(pl.col("proptax") + rent_share * rentpaid, pl.lit(float(c["tax_cap"])))
    tablea = pl.when(reduce_on > c["income_floor"]).then((reduce_on - c["income_floor"]) * c["income_rate"]).otherwise(0.0)
    tableb = (ptax - tablea).clip(0, None)
    pcred = (
        pl.when(tableb <= 0).then(0.0)
        .when(tableb <= c["small_limit"]).then(c["small_rate"] * tableb)
        .otherwise(homestead_rate * tableb)
    )
    pcred = pl.when(income_test <= c["income_limit"]).then(pcred).otherwise(0.0)
    if 2020 <= y <= 2022 and not behavior.pay_wisconsin_homestead_credit_from_2023:
        # TAXSIM's notch: nothing to a taxpayer under 19, nor to one under 62 with no wages or business income.
        wage_business = pl.col("pwages") + pl.col("swages") + pl.col("pbusinc") + pl.col("pprofinc") + pl.col("scorp") + pl.col("sbusinc") + pl.col("sprofinc")
        pcred = pl.when((pl.col("page") >= 1) & (pl.col("page") <= 18) | ((wage_business <= 0) & (pl.col("page") < 62))).then(0.0).otherwise(pcred)
    if y >= 2023 and not behavior.pay_wisconsin_homestead_credit_from_2023:
        # TAXSIM's 2023 on routine: to a non-dependent return with a taxpayer 18 or older who is 62 or older or has earned
        # income; property tax (plus a fifth of rent) up to $1,460, less 8.785% of household income over $8,060 (less $500
        # per dependent), at 80%.
        earned = pl.col("earned_income") + pl.col("nonprop")
        if y < 2024:
            earned = earned + pl.col("pbusinc") + pl.col("pprofinc") + pl.col("sbusinc") + pl.col("sprofinc")
        eligible = (~is_dependent_filer()) & (pl.col("page") >= 18) & ((pl.col("page") >= 62) | (pl.col("sage") >= 62) | (earned > 0))
        property_tax = pl.min_horizontal(pl.lit(1460.0), pl.col("proptax") + 0.2 * rentpaid)
        reduction = 0.08785 * (hy - 500.0 * depx - 8060.0).clip(0, None)
        pcred = pl.when(eligible).then((0.8 * (property_tax - reduction)).clip(0, None)).otherwise(0.0)
    statax = statax - pcred
    if y >= 1989:
        statax = statax - earncr
    if p.in_effect("retirement_exclusion"):
        rx = p.value("retirement_exclusion")
        elderly = (
            (pl.col("page") >= int(rx["min_age"])).cast(pl.Int64)
            + (is_joint & (pl.col("sage") >= int(rx["min_age"]))).cast(pl.Int64)
        )
        cap = pl.when((elderly >= 2) & is_joint).then(float(rx["joint"])).otherwise(float(rx["single"]) * elderly)
        line16 = pl.min_horizontal(pl.col("pensions").clip(0, None), cap)
        reduced_agi = (agi - line16).clip(0, None)
        reduced_std = (
            pl.when(files_single()).then(_standard_deduction(tables["single"], reduced_agi, 1.0))
            .when(files_head_of_household()).then(_standard_deduction(tables["head_of_household"], reduced_agi, 1.0))
            .when(is_joint).then(_standard_deduction(tables["married_joint"], reduced_agi, 1.0))
            .otherwise(_standard_deduction(tables["married_separate"], reduced_agi, 1.0))
        )
        alt_income = (reduced_agi - reduced_std - exemp).clip(0, None)
        alt_tax = (
            pl.when(single_like).then(bracket_tax(alt_income, single)).when(is_joint).then(bracket_tax(alt_income, joint))
            .otherwise(bracket_tax(alt_income, separate))
        )
        statax = pl.when(line16 > 0).then(pl.min_horizontal(statax, alt_tax)).otherwise(statax)

    df = df.with_columns(siitax=statax * flate)
    return with_state_detail(
        df,
        agi=agi,
        exemptions=exemp,
        standard_deduction=stded,
        itemized_deductions=xitded,
        taxable_income=taxinc,
        property_credit=pcred,
        child_care_credit=chcr,
        eic=earncr,
        credits=credit + twocrd + pcred,
        rate=rate,
    )
