"""Utah individual income tax calculator."""

import polars as pl

from taxsim_py.engine.brackets import bracket_rate, bracket_tax
from taxsim_py.engine.inputs import aged_count, federal_exemption_count, files_head_of_household, files_joint, files_separate, files_single, taxpayer_count
from taxsim_py.engine.schema import PARAMETERS_ROOT, YearParams, load_yaml
from taxsim_py.engine.state import by_filing_status, checkpoint, pre1987_federal_itemizing, with_state_detail
from taxsim_py.behavior import BehaviorProfile, TAXSIM_BEHAVIOR
from taxsim_py.engine.state_extrapolation import deflate_for_extrapolation, resolve_state_year

UT_PARAMS = load_yaml(PARAMETERS_ROOT / "states" / "ut" / "income_tax.yaml")


def compute_ut_tax(df: pl.DataFrame, year: int, behavior: BehaviorProfile = TAXSIM_BEHAVIOR) -> pl.DataFrame:
    """Calculate Utah income tax for each row."""
    state_year = "ut" if behavior.mode.value == "statutory" else None
    effective_year, flate = resolve_state_year(year, state_year)
    y = effective_year
    p = YearParams(UT_PARAMS, effective_year)
    df = deflate_for_extrapolation(df, flate)

    is_joint = files_joint()
    is_hoh = files_head_of_household()
    married_table = is_joint | is_hoh
    sep = pl.when(files_separate()).then(2.0).otherwise(1.0)
    txp = taxpayer_count()
    aged = aged_count()
    agi = pl.col("agi")
    single_phase = files_single()
    phases = p["retirement_deduction_phaseout_start"]
    phase = pl.when(single_phase).then(float(phases["single"])).otherwise(float(phases["other"]) / sep)
    salt = pl.col("state_sales_or_income_tax_ded")

    # --- Exemptions ---
    if y <= 1986:
        exemp = p["exemption_per_person_1986"] * federal_exemption_count(y)
    elif y == 1987:
        # TAXSIM sets no exemption in 1987 (logged).
        exemp = pl.lit(0.0)
    elif y <= 2017:
        exemp = p["federal_exemption_share"] * pl.col("personal_exemptions")
    else:
        exemp = p.num("dependent_exemption") * pl.col("depx")

    if y <= 2007:
        # --- Deductions ---
        if y <= 1986:
            gross, fed_itemizes, _ = pre1987_federal_itemizing(y)
            itemized = gross
            c = p["standard_deduction_1986"]
            stded = (c["rate"] * agi).clip(c["low"] / sep, c["high"] / sep)
            if y > 1980:
                stded = pl.when(fed_itemizes).then(0.0).otherwise(stded)
            taxbc = pl.col("pre1987_taxbc")
            almtax = pl.col("pre1987_almtax")
            fed_credit = pl.col("credit")
        else:
            fed_itemizes = pl.col("itemizes")
            itemized = pl.col("itemized_deduction")
            stded = pl.when(fed_itemizes).then(0.0).otherwise(pl.col("standard_deduction"))
            taxbc = pl.col("regular_tax")
            almtax = pl.col("amt")
            fed_credit = pl.col("nonrefundable_credits")
        xitded = (pl.when(fed_itemizes).then(itemized).otherwise(0.0) - salt).clip(0, None)
        deduc = pl.max_horizontal(xitded, stded)
        fedtax = (almtax + taxbc - fed_credit).clip(0, None) * p.num("federal_tax_share")

        # --- State income tax deduction lost to the federal-style limit (1993 on) ---
        tx = pl.lit(0.0)
        if y >= 1993:
            fed_agi = agi
            phas92 = p["itemized_limit_threshold"] * p.num("itemized_limit_index") / sep
            xtot = salt + pl.col("proptax") + pl.col("mortgage") + pl.col("charity_cash")
            over = fed_agi - phas92
            xconst = pl.min_horizontal(p["itemized_limit_share"] * xtot, p["itemized_limit_rate"] * over)
            lost = xconst / xtot * salt
            tx = pl.when((xitded > stded) & (xtot > 0) & (over > 0)).then(lost).otherwise(0.0)
        # --- Retirement income deduction ---
        under65 = p.num("retirement_deduction_under65")
        aged_amount = p.num("retirement_deduction_aged")
        investment = pl.col("dividends") + pl.col("intrec")
        retinc = pl.col("pensions") + pl.col("taxable_social_security") + investment * aged.clip(None, 1)
        if y <= 1987:
            cap = (
                pl.when(aged == 0).then(txp * under65)
                .when(aged == 1).then(aged_amount + (txp - 1) * under65)
                .otherwise(txp * aged_amount)
            )
            retded = retinc.clip(0, cap)
        else:
            reduction = float(p["retirement_deduction_phaseout_rate"]) * (agi - phase).clip(0, None)
            retded = (
                pl.when(aged == 0).then(
                    pl.when(retinc > 0).then(txp * pl.min_horizontal(retinc, pl.lit(under65)) - reduction).otherwise(0.0)
                )
                .otherwise(aged.clip(None, 2) * aged_amount - reduction)
            ).clip(0, None)
        taxinc = (agi - retded - fedtax - exemp - deduc + tx).clip(0, None)
        df, (taxinc,) = checkpoint(df, ut_taxinc=taxinc)

        schedule = p.value("brackets")
        statax = pl.when(married_table).then(bracket_tax(taxinc, schedule["married"])).otherwise(
            bracket_tax(taxinc, schedule["single"])
        )
        rate = pl.when(married_table).then(bracket_rate(taxinc, schedule["married"])).otherwise(
            bracket_rate(taxinc, schedule["single"])
        )
        credits = pl.lit(0.0)
        if y == 2007:
            statax = pl.min_horizontal(statax, p["single_rate_2007"] * agi.clip(0, None))
        if y == 1988:
            c = p["small_tax_credit_1988"]
            credits = pl.when(statax <= c["tax_limit"]).then((c["rate"] * statax).clip(0, c["cap"])).otherwise(0.0)
            statax = (statax - credits).clip(0, None)
        detail = {"standard_deduction": stded, "itemized_deductions": xitded}
    else:
        taxinc = agi.clip(0, None)
        statax = p.num("flat_rate") * taxinc
        rate = pl.lit(p.num("flat_rate"))
        detail = {}
        fed_itemizes = pl.col("itemizes")
        if y <= 2017:
            itemized = (pl.col("itemized_deduction") - salt).clip(0, None)
        else:
            other = pl.col("proptax") + pl.col("otheritem")
            sttax = pl.min_horizontal(p["salt_cap_2018"] / sep, other + salt)
            stt = pl.min_horizontal(salt, sttax - other)
            itemized = pl.col("itemized_before_limit") - stt
        deduc = pl.when(fed_itemizes).then(itemized).otherwise(pl.col("standard_deduction"))
        if y in (2022, 2023, 2024):
            actual_thresholds = p["taxpayer_credit_phaseout_threshold_actual"][y]
            threshold = by_filing_status(actual_thresholds)
            credit_base = deduc + p.num("taxpayer_credit_personal_exemption") * pl.col("depx")
            credit = (
                p["taxpayer_credit_rate"] * credit_base
                - p["taxpayer_credit_phaseout_rate"] * (taxinc - threshold).clip(0, None)
            ).clip(0, None)
        else:
            t = p["taxpayer_credit_threshold"]
            index = p.num("taxpayer_credit_index")
            threshold = pl.when(is_hoh).then(t["head_of_household"] * index).otherwise(t["per_taxpayer"] * index * txp)
            credit = (
                p["taxpayer_credit_rate"] * (exemp + deduc)
                - p["taxpayer_credit_phaseout_rate"] * (taxinc - threshold).clip(0, None)
            ).clip(0, None)
        # Retirement and Social Security credits are alternatives in current
        # Utah law. PolicyEngine applies the larger potential credit, rather
        # than adding both as the old TAXSIM-era implementation did.
        ss = pl.col("taxable_social_security")
        if y in (2022, 2023, 2024):
            retirement_age = p["retirement_credit_age"][y]
            retirement_taxpayers = (
                (pl.col("page") >= retirement_age).cast(pl.Float64)
                + (pl.col("sage") >= retirement_age).cast(pl.Float64)
            )
            retirement_threshold = by_filing_status(p["retirement_credit_phaseout_start_actual"][y])
            retirement_credit = (
                p["retirement_credit_max_actual"][y] * retirement_taxpayers
                - 0.025 * (agi - retirement_threshold).clip(0, None)
            ).clip(0, None)
            ss_threshold = by_filing_status(p["social_security_credit_phaseout_start"][y])
            ss_credit = (p["social_security_credit_rate"] * ss - 0.025 * (agi - ss_threshold).clip(0, None)).clip(0, None)
            retcrd = pl.max_horizontal(retirement_credit, ss_credit)
        else:
            retcrd = pl.when(aged > 0).then(
                (float(p["retirement_credit_per_aged_taxpayer"]) * aged
                 - float(p["retirement_credit_phaseout_rate"]) * (agi - phase).clip(0, None)).clip(0, None)
            ).otherwise(0.0)
            retcrd = retcrd + pl.when(ss > 0).then(
                pl.min_horizontal(
                    float(p["social_security_credit_per_taxpayer"]) * (txp - aged),
                    float(p["social_security_credit_rate"]) * ss,
                )
            ).otherwise(0.0)

        if y in (2022, 2023, 2024):
            # Survey adapters may provide this semantic extension directly;
            # it is deliberately outside TAXSIM's 35 inputs.
            ctc_children = pl.col("children_under_4").cast(pl.Float64)
            ctc_threshold = by_filing_status(p["child_tax_credit_phaseout_start"][y])
            # The credit starts with tax year 2024 (UC 59-10-1047).
            ctc = (
                p["child_tax_credit_amount"][y] * ctc_children
                - p["child_tax_credit_reduction_rate"][y] * (taxinc - ctc_threshold).clip(0, None)
            ).clip(0, None)
            if y < 2024:
                ctc = pl.lit(0.0)
            earned_income_credit = pl.min_horizontal(
                p["earned_income_credit_rate"][y] * pl.col("eitc"),
                (pl.col("pwages") + pl.col("swages")).clip(0, None),
            )
        else:
            ctc = pl.lit(0.0)
            earned_income_credit = pl.lit(0.0)

        statax = (statax - credit - retcrd - ctc - earned_income_credit).clip(0, None)
        credits = credit + retcrd + ctc + earned_income_credit

    df = df.with_columns(siitax=statax * flate)
    return with_state_detail(
        df, agi=agi, exemptions=exemp, taxable_income=taxinc, credits=credits, rate=rate, **detail
    )
