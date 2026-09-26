"""Utah individual income tax calculator."""

import polars as pl

from taxsim_py.engine.brackets import bracket_rate, bracket_tax
from taxsim_py.engine.inputs import aged_count, federal_exemption_count, taxpayer_count
from taxsim_py.engine.schema import PARAMETERS_ROOT, load_yaml, resolve_year
from taxsim_py.engine.state import checkpoint, pre1987_federal_itemizing, with_default, with_defaults, with_state_detail
from taxsim_py.engine.state_extrapolation import deflate_for_extrapolation, resolve_state_year

UT_PARAMS = load_yaml(PARAMETERS_ROOT / "states" / "ut" / "income_tax.yaml")


def _p(name: str, year: int) -> float:
    return float(resolve_year(UT_PARAMS[name], year))


def compute_ut_tax(df: pl.DataFrame, year: int) -> pl.DataFrame:
    """Calculate Utah income tax for each row."""
    effective_year, flate = resolve_state_year(year)
    y = effective_year
    p = UT_PARAMS
    df = with_defaults(df, (
        "proptax", "otheritem", "mortgage", "depx", "charity_cash", "state_sales_or_income_tax_ded",
        "standard_deduction", "itemized_deduction", "itemized_before_limit", "personal_exemptions",
        "regular_tax", "amt", "ccc", "odc", "credit", "pre1987_taxbc", "pre1987_almtax", "pensions",
        "taxable_social_security", "dividends", "intrec",
    ))
    df = with_default(df, "itemizes", False)
    df = deflate_for_extrapolation(
        df, flate,
        [
            "proptax", "otheritem", "mortgage", "charity_cash", "state_sales_or_income_tax_ded", "agi",
            "standard_deduction", "itemized_deduction", "itemized_before_limit", "personal_exemptions",
            "regular_tax", "amt", "ccc", "odc", "pensions", "taxable_social_security", "dividends", "intrec",
        ],
    )

    status = pl.col("filing_status")
    is_joint = status == "married_joint"
    is_hoh = status == "head_of_household"
    married_table = is_joint | is_hoh
    sep = pl.when(status == "married_separate").then(2.0).otherwise(1.0)
    txp = taxpayer_count()
    aged = aged_count()
    agi = pl.col("agi")
    single_phase = status == "single"
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
        exemp = _p("dependent_exemption", y) * pl.col("depx")

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
            # The federal credit total is only filled from 1998.
            fed_credit = pl.lit(0.0) if y <= 1997 else pl.col("ccc") + pl.col("odc")
        xitded = (pl.when(fed_itemizes).then(itemized).otherwise(0.0) - salt).clip(0, None)
        deduc = pl.max_horizontal(xitded, stded)
        fedtax = (almtax + taxbc - fed_credit).clip(0, None) * _p("federal_tax_share", y)

        # --- State income tax deduction lost to the federal-style limit (1993 on) ---
        tx = pl.lit(0.0)
        if y >= 1993:
            fed_agi = agi
            phas92 = p["itemized_limit_threshold"] * _p("itemized_limit_index", y) / sep
            xtot = salt + pl.col("proptax") + pl.col("mortgage") + pl.col("charity_cash")
            over = fed_agi - phas92
            xconst = pl.min_horizontal(p["itemized_limit_share"] * xtot, p["itemized_limit_rate"] * over)
            lost = xconst / xtot * salt
            tx = pl.when((xitded > stded) & (xtot > 0) & (over > 0)).then(lost).otherwise(0.0)
        # --- Retirement income deduction ---
        under65 = _p("retirement_deduction_under65", y)
        aged_amount = _p("retirement_deduction_aged", y)
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

        schedule = resolve_year(p["brackets"], y)
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
        statax = _p("flat_rate", y) * taxinc
        rate = pl.lit(_p("flat_rate", y))
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
        t = p["taxpayer_credit_threshold"]
        index = _p("taxpayer_credit_index", y)
        threshold = pl.when(is_hoh).then(t["head_of_household"] * index).otherwise(t["per_taxpayer"] * index * txp)
        credit = (
            p["taxpayer_credit_rate"] * (exemp + deduc)
            - p["taxpayer_credit_phaseout_rate"] * (taxinc - threshold).clip(0, None)
        ).clip(0, None)
        # Retirement credit and Social Security credit.
        retcrd = pl.when(aged > 0).then(
            (float(p["retirement_credit_per_aged_taxpayer"]) * aged
             - float(p["retirement_credit_phaseout_rate"]) * (agi - phase).clip(0, None)).clip(0, None)
        ).otherwise(0.0)
        ss = pl.col("taxable_social_security")
        retcrd = retcrd + pl.when(ss > 0).then(
            pl.min_horizontal(
                float(p["social_security_credit_per_taxpayer"]) * (txp - aged),
                float(p["social_security_credit_rate"]) * ss,
            )
        ).otherwise(0.0)
        statax = (statax - credit - retcrd).clip(0, None)
        credits = credit + retcrd

    df = df.with_columns(siitax=statax * flate)
    return with_state_detail(
        df, agi=agi, exemptions=exemp, taxable_income=taxinc, credits=credits, rate=rate, **detail
    )
