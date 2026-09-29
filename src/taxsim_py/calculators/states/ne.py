"""Nebraska individual income tax calculator."""

import polars as pl

from taxsim_py.engine.brackets import bracket_rate, bracket_tax, scale_brackets
from taxsim_py.engine.inputs import aged_count, federal_exemption_count, files_joint, files_separate, files_single, taxpayer_count
from taxsim_py.engine.schema import PARAMETERS_ROOT, YearParams, load_yaml, resolve_year
from taxsim_py.engine.state import by_filing_status, checkpoint, with_state_detail
from taxsim_py.behavior import BehaviorProfile, TAXSIM_BEHAVIOR
from taxsim_py.engine.state_extrapolation import deflate_for_extrapolation, resolve_state_year

NE_PARAMS = load_yaml(PARAMETERS_ROOT / "states" / "ne" / "income_tax.yaml")
FEDERAL_INCOME_TAX_PARAMS = load_yaml(PARAMETERS_ROOT / "national" / "income_tax.yaml")


def _p_in(table: dict, year: int) -> float:
    return float(resolve_year(table, year))


def _status_values(section: dict, year: int) -> dict[str, float]:
    return {status: float(resolve_year(values, year)) for status, values in section.items()}


def _additional_tax(dagi: pl.Expr, bounds: list[float], rates: list[float]) -> tuple[pl.Expr, pl.Expr]:
    """High-income additional tax on federal AGI, and its amount at the top bound."""
    s1, s2, s3, s4 = bounds
    r2, r3, r4 = rates
    schedule = [[0.0, 0.0], [s1, r2], [s2, r3], [s3, r4]]
    capped = (s4 - s3) * r4 + (s3 - s2) * r3 + (s2 - s1) * r2
    return bracket_tax(dagi, schedule), pl.lit(capped)


def compute_ne_tax(df: pl.DataFrame, year: int, behavior: BehaviorProfile = TAXSIM_BEHAVIOR) -> pl.DataFrame:
    """Calculate Nebraska income tax for each row."""
    effective_year, flate = resolve_state_year(year)
    y = effective_year
    p = YearParams(NE_PARAMS, effective_year)
    df = deflate_for_extrapolation(df, flate)

    status = pl.col("filing_status")
    is_single = files_single()
    is_joint = files_joint()
    is_sep = files_separate()
    sep = pl.when(is_sep).then(2.0).otherwise(1.0)
    txp = taxpayer_count()
    exemps = txp + pl.col("depx")
    fed_agi = pl.col("agi")
    ne_agi = fed_agi
    if y >= 2015:
        # Taxable Social Security is subtracted in full up to an AGI limit.
        limits = p["social_security_exclusion_agi_limit"]
        limit = pl.when(is_joint).then(_p_in(limits["married_joint"], y)).otherwise(_p_in(limits["single"], y))
        above = p.num("social_security_exclusion_share_above_limit")
        ne_agi = ne_agi - pl.when(fed_agi <= limit).then(pl.col("taxable_social_security")).otherwise(
            above * pl.col("taxable_social_security")
        )
    salt_ded = pl.col("state_sales_or_income_tax_ded")
    itemizes = pl.col("itemizes")

    if y <= 2012:
        base = p.num("itemized_limit_threshold_base") * (p.num("itemized_limit_inflation") if y >= 1992 else 1.0)
        phas92 = base / sep
    elif y <= 2017:
        phas92 = (
            p.num("itemized_limit_threshold_base")
            * p.num("itemized_limit_inflation")
            * by_filing_status(p["itemized_limit_status_multiplier"])
        )
    else:
        phas92 = float(resolve_year(p["itemized_limit_threshold_base"], 1977)) / sep

    if y <= 1986:
        statax = p.num("federal_tax_share") * pl.col("pre1987_pretax").clip(0, None)
        taxbc = pl.col("pre1987_taxbc")
        almtax = pl.col("pre1987_almtax")
        fed_ccc = pl.col("federal_chcr")
        ne_agi = pl.lit(0.0)
        xitded = pl.lit(0.0)
        stded = pl.lit(0.0)
        exemp = pl.lit(0.0)
        taxinc = pl.lit(0.0)
        rt = p.num("federal_tax_share") * pl.col("federal_source_rate") / 100.0
    else:
        taxbc = pl.col("regular_tax")
        almtax = pl.col("amt")
        fed_ccc = pl.col("federal_chcr")

        # --- Deductions ---
        fed_itemized = pl.when(itemizes).then(pl.col("itemized_deduction")).otherwise(0.0)
        fed_std = pl.when(itemizes).then(0.0).otherwise(pl.col("standard_deduction"))
        if y <= 2017:
            xitded = (fed_itemized - salt_ded).abs() * itemizes.cast(pl.Float64)
            stded = fed_std
        else:
            stit = pl.min_horizontal(p.num("salt_cap") / sep, salt_ded)
            xitded = (fed_itemized - stit).abs() * itemizes.cast(pl.Float64)
            std = p["standard_deduction_2018plus"]
            aged_add = p["standard_deduction_aged_addition_2018plus"]
            stded = by_filing_status({
                "single": resolve_year(std["single"], y),
                "married_joint": resolve_year(std["married"], y),
                "married_separate": resolve_year(std["married"], y),
                "head_of_household": resolve_year(std["head_of_household"], y),
            }) / sep + by_filing_status({
                "single": resolve_year(aged_add["single"], y),
                "married_joint": resolve_year(aged_add["married"], y),
                "married_separate": resolve_year(aged_add["married"], y),
                "head_of_household": resolve_year(aged_add["head_of_household"], y),
            }) * aged_count()
        if 1993 <= y <= 2017:
            reduction = p.num("standard_deduction_phaseout_rate") * (fed_agi - phas92).clip(0, None)
            stded = (fed_std - reduction).clip(0, None)
            override = p["standard_deduction_override"]
            if y in override["married"]:
                married_value = (float(override["married"][y]) / sep - reduction).clip(0, None)
                stded = pl.when(is_joint | is_sep).then(married_value).otherwise(stded)
            if y in override["single"]:
                stded = pl.when(is_single).then((float(override["single"][y]) - reduction).clip(0, None)).otherwise(stded)
        if y in (2008, 2009):
            cap = p.num("standard_deduction_property_tax_cap_2008_2009")
            stded = pl.when(pl.col("proptax") > 0).then(
                fed_std - pl.min_horizontal(pl.col("proptax"), cap * txp)
            ).otherwise(stded)
        if y == 1987:
            deduc = (fed_itemized - salt_ded - stded).clip(0, None)
        else:
            deduc = pl.max_horizontal(xitded, stded)
        exemp = federal_exemption_count(y) * p.num("exemption") if y <= 1992 else pl.lit(0.0)
        df, (taxinc,) = checkpoint(df, ne_taxinc=(ne_agi - exemp - deduc).clip(0, None))

        # --- Tax ---
        if y <= 1992:
            tables = {status_name: resolve_year(values, y) for status_name, values in p["brackets_1987_1992"].items()}
            income = taxinc
            if y == 1987:
                income = (taxinc - by_filing_status(p["zero_bracket_1987"])).clip(0, None)
            statax = pl.lit(0.0)
            for status_name, brackets in tables.items():
                statax = pl.when(status == status_name).then(bracket_tax(income, brackets)).otherwise(statax)
            rt = pl.lit(0.0)
            for status_name, brackets in tables.items():
                rt = pl.when(status == status_name).then(bracket_rate(income, brackets)).otherwise(rt)
        else:
            factor = p.num("bracket_inflation") if y >= 2014 else 1.0
            tables = {
                status_name: scale_brackets(resolve_year(values, y), factor)
                for status_name, values in p["brackets_1993plus"].items()
            }
            taxy = pl.when(is_sep).then(2.0 * taxinc).otherwise(taxinc)
            dagi = pl.when(is_sep).then(2.0 * fed_agi).otherwise(fed_agi)
            table_of = {"single": "single", "married_joint": "married_joint",
                        "married_separate": "married_joint", "head_of_household": "head_of_household"}
            statax = pl.lit(0.0)
            rt = pl.lit(0.0)
            for status_name, table_name in table_of.items():
                brackets = tables[table_name]
                statax = pl.when(status == status_name).then(bracket_tax(taxy, brackets) / sep).otherwise(statax)
                rt = pl.when(status == status_name).then(bracket_rate(taxy, brackets)).otherwise(rt)
            df, (statax, rt) = checkpoint(df, ne_table_tax=statax, ne_rt=rt)
            if y <= 2017:
                bounds_year = min(y, 2013)
                bound_factor = p.num("itemized_limit_inflation") if y >= 2013 else 1.0
                rates = p.value("additional_tax_rates")
                surtax = pl.lit(0.0)
                cap_amount = pl.lit(0.0)
                top = pl.lit(0.0)
                for status_name, table_name in table_of.items():
                    bounds = [float(b) * bound_factor for b in p["additional_tax_bounds"][table_name][bounds_year]]
                    amount, capped = _additional_tax(dagi, bounds, rates)
                    surtax = pl.when(status == status_name).then(amount).otherwise(surtax)
                    cap_amount = pl.when(status == status_name).then(capped).otherwise(cap_amount)
                    top = pl.when(status == status_name).then(pl.lit(bounds[3])).otherwise(top)
                limited = pl.when(dagi <= top).then(surtax).otherwise(cap_amount)
                added = pl.when(is_single).then(surtax + limited).otherwise(limited / sep)
                high = dagi > phas92
                big = taxinc >= p.num("additional_tax_min_taxable_income")
                statax = (
                    pl.when(high & big)
                    .then(statax + added)
                    .when(high & (taxinc < p["additional_tax_agi_share"] * (fed_agi - phas92)))
                    .then(rt * taxinc)
                    .otherwise(statax)
                )

    # --- Credits ---
    cred = p.num("personal_credit")
    if y <= 1992:
        stxcr = cred * exemps
    else:
        threshold = by_filing_status(_status_values(p["personal_credit_phaseout_threshold"], y))
        agdiff = (fed_agi - threshold).clip(0, None)
        xcred = (
            cred - p.num("personal_credit_phaseout_amount") * agdiff / (p.num("personal_credit_phaseout_step") / sep)
        ).clip(0, None)
        # The federal exemption count is deflated with the other federal
        # values when a later year is projected.
        stxcr = xcred * federal_exemption_count(y) / flate
    credit = stxcr
    if y >= 1987:
        credit = credit + p.num("elderly_credit_share") * pl.col("federal_elder")
    chcr = p.num("child_care_credit_share") * fed_ccc
    chcref = pl.lit(0.0)
    refundable_zone = pl.lit(False)
    if y >= 1998:
        refundable_zone = fed_agi <= p.num("child_care_refundable_agi_limit")
        share = 1.0 - p.num("child_care_refundable_share_step") * (
            fed_agi - p.num("child_care_refundable_full_share_agi")
        ).clip(0, None)
        chcref = pl.when(refundable_zone).then(share * fed_ccc).otherwise(0.0)
    credit = credit + pl.when(refundable_zone).then(0.0).otherwise(chcr)
    df, (statax,) = checkpoint(df, ne_tax_before_credits=statax)
    statax = statax + p.num("minimum_tax_share") * almtax
    statax = (statax - credit).clip(0, None)
    statax = pl.min_horizontal(taxbc + almtax, statax)
    earncr = p.num("eitc_match_rate") * pl.col("eitc")
    statax = statax - chcref - earncr
    final_chcr = pl.when(chcref > 0).then(chcref).otherwise(chcr)
    final_credit = credit + chcref + earncr

    return with_state_detail(
        df.with_columns(siitax=statax * flate),
        agi=ne_agi,
        exemptions=exemp,
        standard_deduction=stded,
        itemized_deductions=xitded,
        taxable_income=taxinc,
        child_care_credit=final_chcr,
        eic=earncr,
        credits=final_credit,
        rate=rt,
    )
