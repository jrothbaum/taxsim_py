"""Illinois individual income tax calculator."""

import polars as pl

from taxsim_py.engine.inputs import aged_count, files_joint, is_dependent_filer, taxpayer_count
from taxsim_py.engine.state import dividend_exclusion_addback, with_state_detail
from taxsim_py.engine.schema import PARAMETERS_ROOT, YearParams, load_yaml
from taxsim_py.behavior import BehaviorProfile, TAXSIM_BEHAVIOR
from taxsim_py.engine.state_extrapolation import resolve_state_year

IL_PARAMS = load_yaml(PARAMETERS_ROOT / "states" / "il" / "income_tax.yaml")


def compute_il_tax(df: pl.DataFrame, year: int, behavior: BehaviorProfile = TAXSIM_BEHAVIOR) -> pl.DataFrame:
    effective_year, flate = resolve_state_year(year, behavior, IL_PARAMS)
    p = YearParams(IL_PARAMS, effective_year)
    rate = p.num("rate")
    exemption_amount = p.num("personal_exemption")

    # The federal exemption count: taxpayers and dependents
    # (none for a dependent filer from 1987), plus the aged before 1987.
    # TAXSIM deflates this count in projected years.
    if effective_year <= 1986:
        exemps = taxpayer_count() + pl.col("depx") + aged_count()
    else:
        exemps = pl.when(is_dependent_filer()).then(0.0).otherwise(taxpayer_count() + pl.col("depx"))
    df = df.with_columns(il_exemps=exemps / flate)

    # Federal AGI plus the federal dividend and capital-gains exclusions
    # (through 1986; the gains exclusion is on the net long-term gain).
    if effective_year <= 1986:
        df = df.with_columns(
            il_dividend_addback=dividend_exclusion_addback(effective_year, behavior),
            il_capgains_addback=pl.col("pre1987_capded"),
        )
    else:
        df = df.with_columns(il_dividend_addback=pl.lit(0.0), il_capgains_addback=pl.lit(0.0))

    # Pensions and Social Security benefits are exempt.
    df = df.with_columns(
        il_agi=(
            pl.col("agi") + pl.col("il_dividend_addback") + pl.col("il_capgains_addback")
            - pl.col("pensions") - pl.col("taxable_social_security")
        ) / flate
    )
    exemption = exemption_amount * pl.col("il_exemps")
    if p.in_effect("aged_exemption"):
        exemption = exemption + p.num("aged_exemption") * aged_count()
    # A dependent filer gets one exemption only if income is at most that amount.
    exemption = pl.when(is_dependent_filer()).then(
        pl.when(pl.col("il_agi") <= exemption_amount).then(exemption_amount).otherwise(0.0)
    ).otherwise(exemption)
    if effective_year >= 2017 and behavior.apply_illinois_exemption_income_limit:
        # No exemption allowance above the base income limit.
        limits = p["exemption_income_limit"]
        limit = pl.when(files_joint()).then(float(limits["joint"])).otherwise(float(limits["other"]))
        exemption = pl.when(pl.col("il_agi") > limit).then(0.0).otherwise(exemption)
    df = df.with_columns(
        il_exemption=exemption,
        il_proptax=pl.col("proptax") / flate,
    )

    # Property tax: a deduction 1983-1990, a nonrefundable credit from 1991.
    if p.in_effect("property_tax_deduction_multiple"):
        df = df.with_columns(il_ptax_deduction=p.num("property_tax_deduction_multiple") * pl.col("il_proptax"))
    else:
        df = df.with_columns(il_ptax_deduction=pl.lit(0.0))
    df = df.with_columns(il_taxinc=(pl.col("il_agi") - pl.col("il_exemption") - pl.col("il_ptax_deduction")).clip(0, None))
    df = df.with_columns(il_tax_before_credits=pl.col("il_taxinc") * rate)

    if p.in_effect("property_tax_credit_rate"):
        credit_rate = p.num("property_tax_credit_rate")
        df = df.with_columns(
            il_income_cap_multiplier=taxpayer_count()
        )
        if p.in_effect("property_tax_credit_income_cap"):
            income_cap = p.num("property_tax_credit_income_cap")
            df = df.with_columns(
                il_property_tax_credit=pl.when(pl.col("agi") / flate > income_cap * pl.col("il_income_cap_multiplier"))
                .then(0.0)
                .otherwise(pl.min_horizontal(pl.col("il_tax_before_credits"), credit_rate * pl.col("il_proptax")))
            )
        else:
            df = df.with_columns(
                il_property_tax_credit=pl.min_horizontal(
                    pl.col("il_tax_before_credits"), credit_rate * pl.col("il_proptax")
                )
            )
    else:
        df = df.with_columns(il_property_tax_credit=pl.lit(0.0))
    df = df.with_columns(
        il_tax_after_property_credit=pl.col("il_tax_before_credits") - pl.col("il_property_tax_credit")
    )

    # Earned income credit (2000+): a share of the federal credit.
    if p.in_effect("eitc_match_rate"):
        eitc_match_rate = p.num("eitc_match_rate")
        federal_eitc = pl.col("eitc")
        if p.in_effect("eitc_childless_minimum_age"):
            # Childless filers qualify from the state minimum age.
            older = pl.max_horizontal(pl.col("page"), pl.col("sage"))
            too_young = (pl.col("num_children") == 0) & (older > 0) & (older < p.num("eitc_childless_minimum_age"))
            federal_eitc = pl.when(too_young).then(0.0).otherwise(pl.col("eitc_before_age_test"))
        df = df.with_columns(il_eitc_raw=eitc_match_rate * (federal_eitc / flate))
        if not p.value("eitc_refundable"):
            ceiling = pl.col("il_tax_after_property_credit")
            if not behavior.subtract_illinois_property_credit_once_in_eitc_limit:
                # TAXSIM subtracts the property tax credit twice in the limit.
                ceiling = ceiling - pl.col("il_property_tax_credit")
            df = df.with_columns(il_eitc_ceiling=ceiling)
            df = df.with_columns(
                il_eitc=pl.max_horizontal(0.0, pl.min_horizontal(pl.col("il_eitc_raw"), pl.col("il_eitc_ceiling")))
            )
        else:
            df = df.with_columns(il_eitc=pl.col("il_eitc_raw"))
    else:
        df = df.with_columns(il_eitc=pl.lit(0.0))
    if p.in_effect("child_tax_credit_rate"):
        # A share of the state EITC for a return with a child under 12
        # (`dep13` is the closest input count).
        il_ctc = pl.when(pl.col("dep13") > 0).then(p.num("child_tax_credit_rate") * pl.col("il_eitc")).otherwise(0.0)
    else:
        il_ctc = pl.lit(0.0)
    df = df.with_columns(il_tax_after_eitc=pl.col("il_tax_after_property_credit") - pl.col("il_eitc") - il_ctc)

    df = df.with_columns(siitax=pl.col("il_tax_after_eitc") * flate)
    return with_state_detail(
        df,
        agi=pl.col("il_agi"),
        exemptions=pl.col("il_exemption"),
        taxable_income=pl.col("il_taxinc"),
        property_credit=pl.col("il_property_tax_credit"),
        eic=pl.col("il_eitc"),
        credits=pl.col("il_property_tax_credit") + pl.col("il_eitc"),
        rate=rate,
    )
