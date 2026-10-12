"""Colorado individual income tax calculator."""


from __future__ import annotations
import polars as pl

from taxsim_py.engine.brackets import bracket_rate, bracket_tax
from taxsim_py.engine.inputs import aged_count, files_joint, files_separate, separate_divisor, taxpayer_count
from taxsim_py.engine.schema import PARAMETERS_ROOT, YearParams, load_yaml, resolve_year
from taxsim_py.engine.state import by_filing_status, dividend_input_adjustment, dividend_exclusion_addback, household_income, interpolate_table, with_state_detail
from taxsim_py.behavior import BehaviorProfile, TAXSIM_BEHAVIOR
from taxsim_py.engine.state_extrapolation import deflate_for_extrapolation, resolve_state_year

CO_PARAMS = load_yaml(PARAMETERS_ROOT / "states" / "co" / "income_tax.yaml")
FEDERAL_INCOME_TAX_PARAMS = load_yaml(PARAMETERS_ROOT / "national" / "income_tax.yaml")
PRE1987_PARAMS = load_yaml(PARAMETERS_ROOT / "national" / "pre1987.yaml")
FEDERAL_AMT_PARAMS = load_yaml(PARAMETERS_ROOT / "national" / "amt.yaml")

FEDERAL_CREDITS_PARAMS = load_yaml(PARAMETERS_ROOT / "national" / "credits.yaml")
_STATUSES = ["single", "married_joint", "married_separate", "head_of_household"]


def compute_co_tax(df: pl.DataFrame, year: int, behavior: BehaviorProfile = TAXSIM_BEHAVIOR) -> pl.DataFrame:
    effective_year, flate = resolve_state_year(year, behavior, CO_PARAMS)
    p = YearParams(CO_PARAMS, effective_year)

    df = df.with_columns(
        co_household_income=household_income()
    )
    df = deflate_for_extrapolation(df, flate, extra=("co_household_income",))

    df = df.with_columns(
        co_sep=separate_divisor(),
        co_taxpayers=taxpayer_count(),
        # The federal exemption count, including the aged.
        co_exemps=taxpayer_count() + pl.col("depx") + aged_count(),
    )
    if p.in_effect("pension_subtraction"):
        ps = p.value("pension_subtraction")
        cap_older, cap_younger = float(ps["cap_older"]), float(ps["cap_younger"])
        agi_limits = ps["social_security_full_agi_limit"]
        agi_limit = pl.when(files_joint()).then(float(agi_limits["joint"])).otherwise(float(agi_limits["other"]))
        # Each taxpayer takes an equal share of the pension and Social Security
        # inputs, as for the other states that cap pensions per person; a
        # dependent filer is one person.
        people = pl.col("co_taxpayers").clip(1, None)
        pension_each = pl.col("pensions") / people
        social_security_each = pl.col("taxable_social_security") / people

        def subtraction(age: pl.Expr, present: pl.Expr) -> pl.Expr:
            older = present & (age >= float(ps["age_older"]))
            middle = present & ~older & (age >= float(ps["age_younger"]))
            social_security = (
                pl.when(older)
                .then(social_security_each)
                .when(middle)
                .then(
                    pl.when((social_security_each > cap_younger) & (pl.col("agi") <= agi_limit))
                    .then(social_security_each)
                    .otherwise(pl.min_horizontal(social_security_each, cap_younger))
                )
                .otherwise(0.0)
            )
            cap = pl.when(older).then(cap_older).otherwise(cap_younger)
            pension = pl.when(older | middle).then(pl.min_horizontal((cap - social_security).clip(0, None), pension_each)).otherwise(0.0)
            return pension.clip(0, None) + social_security

        df = df.with_columns(
            co_pension_exclusion=subtraction(pl.col("page"), pl.lit(True))
            + subtraction(pl.col("sage"), pl.col("co_taxpayers") > 1)
        )
    else:
        pension_cap = p.num("pension_exclusion")
        df = df.with_columns(
            co_pension_exclusion=pl.when(aged_count() > 0).then(
                pl.min_horizontal(pl.col("pensions") + pl.col("taxable_social_security"), pension_cap * pl.col("co_taxpayers")).clip(0, None)
            ).otherwise(0.0)
        )
    # Pre-1987 property tax and heating credits for taxpayers 65 or older.
    if effective_year <= 1986:
        single_like = pl.col("filing_status").is_in(["single", "head_of_household"])
        def by_group(credit: str, field: str) -> pl.Expr:
            return pl.when(single_like).then(p.num(credit, field, "single")).otherwise(p.num(credit, field, "other"))
        income = pl.col("co_household_income") * pl.when(pl.col("co_sep") == 2).then(2.0).otherwise(1.0)
        eligible = (aged_count() > 0) & (income <= by_group("property_credit", "income_limit"))
        propcr = (
            p.num("property_credit", "maximum")
            - (income - by_group("property_credit", "phaseout_start")).clip(0, None) * p.num("property_credit", "phaseout_rate")
        ).clip(0, None)
        propcr = pl.min_horizontal(propcr, pl.col("proptax") + p["property_credit_rent_share"] * pl.col("rentpaid"))
        if p.in_effect("property_credit_addition"):
            addition = p.value("property_credit_addition")
            propcr = propcr + addition["rate"] * (pl.col("proptax") + addition["rent_share"] * pl.col("rentpaid"))
        if effective_year >= 1979:
            fuelcr = (
                p.num("heating_credit", "maximum")
                - (income - by_group("heating_credit", "phaseout_start")) * p.num("heating_credit", "phaseout_rate")
            ).clip(0, None)
            fuelcr = pl.min_horizontal(fuelcr, p["heating_credit_rent_share"] * pl.col("rentpaid"))
        else:
            fuelcr = pl.lit(0.0)
        df = df.with_columns(
            co_propcr=pl.when(eligible).then(propcr).otherwise(0.0),
            co_fuelcr=pl.when(eligible).then(fuelcr).otherwise(0.0),
        )
    else:
        df = df.with_columns(co_propcr=pl.lit(0.0), co_fuelcr=pl.lit(0.0))

    if effective_year <= 1986:
        aif = p.num("indexing_factor")
        # Federal AGI plus the 1982-1986 two-earner deduction and the
        # federal dividend exclusion.
        div_addback = dividend_exclusion_addback(effective_year) if effective_year >= 1980 else pl.lit(0.0)
        df = df.with_columns(co_agi_1=pl.col("agi") + div_addback)
        if 1982 <= effective_year <= 1986:
            p87 = YearParams(PRE1987_PARAMS, effective_year)
            two_earner_rate = p87.num("two_earner_deduction_rate")
            two_earner_cap = p87.num("two_earner_deduction_cap")
            wife = pl.min_horizontal(pl.col("pwages"), pl.col("swages")).clip(0, None)
            twoded = pl.when(files_joint()).then(
                (two_earner_rate * wife).clip(0, two_earner_cap)
            ).otherwise(0.0)
            df = df.with_columns(co_agi_1=pl.col("co_agi_1") + twoded)
        if p.in_effect("interest_exclusion_per_taxpayer"):
            df = df.with_columns(
                co_agi_1=pl.col("co_agi_1")
                - (pl.col("intrec")).clip(0, p.num("interest_exclusion_per_taxpayer") * pl.col("co_taxpayers"))
                - (pl.col("dividends") + dividend_input_adjustment()).clip(
                    0, p.num("dividend_exclusion_per_taxpayer") * pl.col("co_taxpayers")
                )
            )
        # Pensions and taxable Social Security excluded, for returns with a
        # taxpayer 65 or older.
        df = df.with_columns(co_agi=pl.col("co_agi_1") - pl.col("co_pension_exclusion"))
        df = df.with_columns(co_ag=pl.col("co_agi").clip(0, None))

        # Federal tax after credits is deducted, scaled by Colorado AGI over
        # federal AGI.
        fedded = (pl.col("pre1987_taxbc") - pl.col("credit") - pl.col("pre1987_earncr")).clip(0, None)
        share = pl.when((pl.col("co_agi") > 0) & (pl.col("agi") > 0)).then(pl.col("co_agi") / pl.col("agi")).otherwise(1.0)
        df = df.with_columns(co_fedded=pl.min_horizontal(fedded * share, fedded).clip(0, None))

        # --- Standard deduction ---
        if p.in_effect("standard_deduction_percent"):
            is_sep = files_separate()
            sd = p.value("standard_deduction_percent")
            stded_std = pl.when(is_sep).then(
                pl.min_horizontal(sd["cap_separate"] * aif, sd["rate"] * pl.col("co_ag"))
            ).otherwise(pl.min_horizontal(sd["rate"] * pl.col("co_ag"), sd["cap"] * aif / pl.col("co_sep")))
            exemps_placeholder = pl.col("co_exemps")
            per_exemption = sd["allowance_per_exemption"]
            allow_sep = aif * pl.min_horizontal(
                float(sd["allowance_separate_cap"]), sd["allowance_separate_base"] + exemps_placeholder * per_exemption
            )
            sub = exemps_placeholder * per_exemption
            sub = sub + (
                sd["bonus_phaseout_rate"]
                * (pl.col("co_ag") - (sd["bonus_income_floor"] + exemps_placeholder * sd["bonus_income_per_exemption"]))
            ).clip(0, None)
            sub = (sd["allowance_bonus"] - sub).clip(0, None)
            allow_std = pl.min_horizontal(sd["allowance_cap"] * aif, sd["allowance_base"] + per_exemption * exemps_placeholder + sub)
            df = df.with_columns(
                co_stded=pl.when(is_sep).then(pl.max_horizontal(stded_std, allow_sep)).otherwise(
                    pl.max_horizontal(stded_std, allow_std)
                )
            )
        else:
            df = df.with_columns(co_stded=p.num("standard_deduction") * aif / pl.col("co_sep"))

        # Federal itemized deductions (zero when not itemizing federally)
        # less state income or sales tax, plus a gasoline tax allowance.
        itemizing = pl.col("pre1987_itemizes").cast(pl.Float64)
        gas = p.value("gasoline_tax_deduction")
        df = df.with_columns(
            co_xitded=itemizing * (pl.col("pre1987_deduc") - pl.col("state_sales_or_income_tax_ded"))
            + float(gas["per_taxpayer"]) * pl.col("co_taxpayers")
            + float(gas["per_dependent"]) * pl.col("depx")
        )
        df = df.with_columns(co_deduc=pl.max_horizontal(pl.col("co_xitded"), pl.col("co_stded")))

        # --- Exemption ---
        pex = p.value("personal_exemption")
        pe = float(pex["amount"]) * aif if pex["indexed"] else float(pex["amount"])
        df = df.with_columns(co_exemp=pe * pl.col("co_exemps"))

        df = df.with_columns(
            co_taxinc=(pl.col("co_agi") - pl.col("co_deduc") - pl.col("co_exemp") - pl.col("co_fedded")).clip(0, None)
        )

        # --- Bracket tax ---
        brackets = p.value("brackets")
        brackets_scaled = [[lo * aif, rate] for lo, rate in brackets]
        df = df.with_columns(co_statax=bracket_tax(pl.col("co_taxinc"), brackets_scaled))
        detail = {
            "exemptions": pl.col("co_exemp"),
            "standard_deduction": pl.col("co_stded"),
            "itemized_deductions": pl.col("co_xitded"),
            "rate": bracket_rate(pl.col("co_taxinc"), brackets_scaled),
        }

        df = df.with_columns(co_statax=pl.col("co_statax") * p.num("tax_multiplier"))

        surcharge = p.value("investment_income_surcharge")
        threshold = float(surcharge["threshold_per_taxpayer"])
        rate = float(surcharge["rate"])
        df = df.with_columns(
            co_statax=pl.col("co_statax")
            + rate * (pl.col("dividends") + pl.col("intrec") - threshold * pl.col("co_taxpayers")).clip(0, None)
        )

        if p.in_effect("food_credit"):
            table = p.value("food_credit")
            foody = pl.col("co_ag") / (pl.col("co_taxpayers") + pl.col("depx"))
            base_amt = (interpolate_table(foody, table) * aif).round()
            is_sep = files_separate()
            foodcr = (pl.col("co_taxpayers") + pl.col("depx")) * base_amt
            if effective_year >= 1978:
                flat_sep = float(table[-1][1]) * (pl.col("co_taxpayers") + pl.col("depx"))
                foodcr = pl.when(is_sep).then(flat_sep).otherwise(foodcr)
            df = df.with_columns(co_foodcr=foodcr)
        else:
            df = df.with_columns(co_foodcr=pl.lit(0.0))

        df = df.with_columns(co_amt=pl.lit(0.0))
    else:
        # --- 1987+: flat rate on federal taxable income ---
        df = df.with_columns(co_agi=pl.col("agi"))
        taxinc = pl.col("taxable_income")
        if effective_year == 2020:
            # 2020: unemployment compensation excluded federally is taxable.
            ui_total = pl.col("ui")
            ui_2020 = YearParams(FEDERAL_INCOME_TAX_PARAMS, 2020).value("unemployment_exclusion")
            per_spouse = float(ui_2020["per_spouse"])
            excl_spouse = pl.min_horizontal(pl.col("sui"), per_spouse)
            excl_primary = (ui_total - pl.col("sui")).clip(0, per_spouse)
            excluded = pl.when(pl.col("agi") - excl_spouse - excl_primary < ui_2020["agi_limit"]).then(
                excl_spouse + excl_primary
            ).otherwise(0.0)
            taxinc = taxinc + excluded
        taxinc = taxinc.clip(0, None)

        # 1992+: the state income tax deducted federally is added back when
        # itemizing.
        if effective_year >= 1992:
            addback = pl.when(pl.col("itemizes")).then(
                pl.min_horizontal(pl.col("state_sales_or_income_tax_ded").clip(0, None), pl.col("itemized_deduction"))
            ).otherwise(0.0)
            taxinc = taxinc + addback

        if p.in_effect("high_income_addback"):
            hi = p.value("high_income_addback")
            threshold = float(hi["agi_threshold"])
            joint = files_joint()
            limit = pl.when(joint).then(float(hi["limit_joint"])).otherwise(float(hi["limit_other"]))
            claimed = pl.when(pl.col("itemizes")).then(pl.col("itemized_deduction")).otherwise(
                0.0 if bool(hi["itemized_only"]) else pl.col("standard_deduction")
            )
            taxinc = taxinc + pl.when(pl.col("agi") > threshold).then((claimed - limit).clip(0, None)).otherwise(0.0)
            if "qbi_deduction" in df.collect_schema().names():
                qbi_limit = pl.when(joint).then(float(hi["qbi_agi_limit_joint"])).otherwise(float(hi["qbi_agi_limit_other"]))
                taxinc = taxinc + pl.when(pl.col("agi") > qbi_limit).then(pl.col("qbi_deduction")).otherwise(0.0)


        # The pension exclusion comes before the 2000-2002 marriage penalty
        # subtraction for joint returns, which is less the federal standard
        # deduction.
        taxinc = (taxinc - pl.col("co_pension_exclusion")).clip(0, None)
        if p.in_effect("marriage_penalty_subtraction"):
            xmar = p.num("marriage_penalty_subtraction")
            is_joint = files_joint()
            zbr = pl.col("standard_deduction")
            # Not floored: a standard deduction above `xmar` adds the excess back.
            sub_not_itemized = (taxinc - (xmar - zbr)).clip(0, None)
            itemized_beats_std = pl.col("itemized_deduction") > zbr
            sub_itemized = pl.when(itemized_beats_std).then(
                (taxinc - (xmar - pl.col("itemized_deduction")).clip(0, None)).clip(0, None)
            ).otherwise(taxinc)
            taxinc_marriage = pl.when(~pl.col("itemizes")).then(sub_not_itemized).otherwise(sub_itemized)
            taxinc = pl.when(is_joint).then(taxinc_marriage).otherwise(taxinc)

        if p.in_effect("aged_marriage_subtraction"):
            taxinc = pl.when(files_joint()).then(
                (taxinc - p.num("aged_marriage_subtraction") * aged_count()).clip(0, None)
            ).otherwise(taxinc)
        df = df.with_columns(co_taxinc=taxinc)

        rate = p.num("flat_rate")
        df = df.with_columns(co_statax=pl.col("co_taxinc") * rate)
        detail = {"rate": rate}

        # --- Minimum tax: a flat rate on the federal AMT base ---
        amt_income = pl.col("agi") - pl.when(pl.col("itemizes")).then(pl.col("mortgage")).otherwise(0.0)
        exemption = by_filing_status(
            {s: resolve_year(FEDERAL_AMT_PARAMS["exemption"][s], effective_year) for s in _STATUSES}
        )
        threshold = by_filing_status(
            {s: resolve_year(FEDERAL_AMT_PARAMS["exemption_phaseout_threshold"][s], effective_year) for s in _STATUSES}
        )
        phaseout_rate = float(resolve_year(FEDERAL_AMT_PARAMS["exemption_phaseout_rate"], effective_year))
        exemption_after_phaseout = (exemption - phaseout_rate * (amt_income - threshold).clip(0, None)).clip(0, None)
        amt_base = (amt_income - exemption_after_phaseout).clip(0, None)
        amt_rate = p.num("amt_rate")
        df = df.with_columns(co_altax=amt_rate * amt_base)
        df = df.with_columns(
            co_statax=pl.col("co_statax") + (pl.col("co_altax") - pl.col("co_statax")).clip(0, None),
            co_foodcr=pl.lit(0.0),
            co_amt=pl.lit(0.0),
        )

    # --- Sales tax refund, on modified AGI including all Social Security ---
    coagi = pl.col("agi") - pl.col("taxable_social_security") + pl.col("gssi")
    refund = p.value("sales_tax_refund")
    if not isinstance(refund, list):
        df = df.with_columns(co_salesrefund=float(refund) * pl.col("co_taxpayers"))
    elif effective_year >= 2022:
        df = df.with_columns(co_salesrefund=bracket_rate(coagi, refund) * pl.col("co_taxpayers"))
    else:
        df = df.with_columns(co_salesrefund=interpolate_table(coagi, refund) * pl.col("co_taxpayers"))

    # --- Child care credit: a share of the federal credit up to tax ---
    child_fed = pl.min_horizontal(pl.col("federal_chcr"), pl.col("regular_tax")).clip(0, None)
    share = p.value("child_care_credit_federal_share")
    if isinstance(share, list):
        rate = interpolate_table(pl.col("agi").clip(0, None), share)
    else:
        rate = float(share)
    if p.in_effect("child_care_credit_dependent_offset"):
        df = df.with_columns(
            co_chcr=(rate * child_fed - p.num("child_care_credit_dependent_offset") * pl.col("depx")).clip(0, None)
        )
    elif isinstance(share, list) or share:
        df = df.with_columns(co_chcr=rate * child_fed)
    else:
        df = df.with_columns(co_chcr=pl.lit(0.0))

    # --- Earned income credit: a share of the federal credit ---
    eitc_share = p.num("eitc_federal_share")
    if p.in_effect("eitc_minimum_age"):
        # Childless filers qualify from the state minimum age, so start from
        # the federal credit before its age test.
        older = pl.max_horizontal(pl.col("page"), pl.col("sage"))
        too_young = (pl.col("num_children") == 0) & (older > 0) & (older < p.num("eitc_minimum_age"))
        eitc_co = pl.when(too_young).then(0.0).otherwise(pl.col("eitc_before_age_test").clip(0, None))
        df = df.with_columns(co_earncr=eitc_share * eitc_co)
    elif eitc_share:
        df = df.with_columns(co_earncr=eitc_share * pl.col("eitc").clip(0, None))
    else:
        df = df.with_columns(co_earncr=pl.lit(0.0))

    # Refundable child tax credit for children under six.
    if p.in_effect("child_tax_credit_amount"):
        ctc_rate = pl.lit(0.0)
        ctc_scales = p.value("child_tax_credit_amount")
        for status in _STATUSES:
            ctc_rate = pl.when(pl.col("filing_status") == status).then(
                bracket_rate(pl.col("co_agi"), ctc_scales[status])
            ).otherwise(ctc_rate)
        if effective_year <= 2023:
            # DR 0104CN worksheet: the federal credit recomputed for children
            # under six only (nonrefundable part limited by tax, refundable
            # part by the ACTC cap and earnings), times the income-based share.
            fed = YearParams(FEDERAL_CREDITS_PARAMS["child_tax_credit"], effective_year)
            maximum = fed.num("amount_per_child") * pl.col("dep6")
            available_tax = (pl.col("tax_before_credits") - pl.col("ccc") - pl.col("elderly_credit_raw")).clip(0, None)
            nonrefundable = pl.min_horizontal(maximum, available_tax)
            refundable = pl.min_horizontal(
                fed.num("actc_max_refundable_per_child") * pl.col("dep6"),
                maximum - nonrefundable,
                fed.num("actc_rate") * (pl.col("earned_income").clip(0, None) - fed.num("actc_earned_income_floor")).clip(0, None),
            ).clip(0, None)
            co_ctc = (ctc_rate / fed.num("amount_per_child")) * (nonrefundable + refundable)
        else:
            co_ctc = ctc_rate * pl.col("dep6")
        if p.in_effect("family_affordability_credit"):
            fac = p.value("family_affordability_credit")
            threshold = pl.lit(0.0)
            for status in _STATUSES:
                threshold = pl.when(pl.col("filing_status") == status).then(
                    float(fac["reduction_threshold"][status])
                ).otherwise(threshold)
            increments = ((pl.col("agi") - threshold).clip(0, None) / float(fac["reduction_increment"])).ceil()
            remaining = 1.0 - (increments * float(fac["reduction_rate"])).clip(None, 1.0)
            children = pl.col("dep6") + float(fac["older_child_share"]) * (pl.col("dep17") - pl.col("dep6")).clip(0, None)
            co_ctc = co_ctc + float(fac["amount"]) * children * remaining
    else:
        co_ctc = pl.lit(0.0)

    # The pre-1987 nonrefundable credits are limited to tax; the sales tax
    # refund, child care, earned income and child tax credits are refundable.
    df = df.with_columns(
        co_statax=(pl.col("co_statax") - pl.col("co_foodcr") - pl.col("co_propcr") - pl.col("co_fuelcr")).clip(0, None)
    )
    df = df.with_columns(
        siitax=(pl.col("co_statax") - pl.col("co_salesrefund") - pl.col("co_chcr") - pl.col("co_earncr") - co_ctc) * flate
    )
    return with_state_detail(
        df,
        agi=pl.col("co_agi"),
        taxable_income=pl.col("co_taxinc"),
        child_care_credit=pl.col("co_chcr"),
        eic=pl.col("co_earncr"),
        credits=pl.col("co_propcr") + pl.col("co_fuelcr") + pl.col("co_foodcr") + pl.col("co_salesrefund")
        + pl.col("co_earncr") + pl.col("co_chcr") + co_ctc,
        **detail,
    )
