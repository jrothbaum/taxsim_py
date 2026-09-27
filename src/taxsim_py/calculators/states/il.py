"""Illinois individual income tax calculator."""

import polars as pl

from taxsim_py.engine.inputs import aged_count, is_dependent_filer, taxpayer_count
from taxsim_py.engine.state import dividend_exclusion_addback, with_state_detail
from taxsim_py.engine.schema import PARAMETERS_ROOT, YearParams, load_yaml, resolve_year
from taxsim_py.engine.state_extrapolation import resolve_state_year

IL_PARAMS = load_yaml(PARAMETERS_ROOT / "states" / "il" / "income_tax.yaml")
PRE1987_PARAMS = load_yaml(PARAMETERS_ROOT / "national" / "pre1987.yaml")


def compute_il_tax(df: pl.DataFrame, year: int) -> pl.DataFrame:
    effective_year, flate = resolve_state_year(year)
    p = YearParams(IL_PARAMS, effective_year)
    rate = p.num("rate")
    exemption_amount = p.num("personal_exemption_amount")

    # Federal exemption count (`comnew(68)`): taxpayers and dependents
    # (none for a dependent filer from 1987), plus the aged before 1987.
    # TAXSIM deflates this count in projected years.
    if effective_year <= 1986:
        exemps = taxpayer_count() + pl.col("depx") + aged_count()
    else:
        exemps = pl.when(is_dependent_filer()).then(0.0).otherwise(taxpayer_count() + pl.col("depx"))
    df = df.with_columns(il_exemps=exemps / flate)

    # Federal AGI plus the federal dividend and capital-gains exclusions
    # (`divexc(...)`, `comnew(7)=capded`), which exist through 1986.
    if effective_year <= 1986:
        df = df.with_columns(il_dividend_addback=dividend_exclusion_addback(effective_year))
        caprat = float(resolve_year(PRE1987_PARAMS["capital_gains_exclusion_rate"], effective_year))
        df = df.with_columns(il_capgains_addback=(caprat * pl.col("ltcg")).clip(0, None))
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
    if effective_year >= 1990:
        exemption = exemption + p["aged_exemption_1990"] * aged_count()
    # A dependent filer gets one exemption only if income is at most that amount.
    exemption = pl.when(is_dependent_filer()).then(
        pl.when(pl.col("il_agi") <= exemption_amount).then(exemption_amount).otherwise(0.0)
    ).otherwise(exemption)
    df = df.with_columns(
        il_exemption=exemption,
        il_proptax=pl.col("proptax") / flate,
    )

    # Property tax: none before 1983; a deduction 1983-1990 (doubled
    # 1989-1990); a nonrefundable credit from 1991.
    if 1983 <= effective_year <= 1990:
        ptax_multiplier = 2.0 if effective_year >= 1989 else 1.0
        df = df.with_columns(il_ptax_deduction=ptax_multiplier * pl.col("il_proptax"))
    else:
        df = df.with_columns(il_ptax_deduction=pl.lit(0.0))
    df = df.with_columns(il_taxinc=(pl.col("il_agi") - pl.col("il_exemption") - pl.col("il_ptax_deduction")).clip(0, None))
    df = df.with_columns(il_tax_before_credits=pl.col("il_taxinc") * rate)

    if effective_year >= 1991:
        credit_rate = p.num("property_tax_credit_rate")
        df = df.with_columns(
            il_income_cap_multiplier=taxpayer_count()
        )
        if effective_year >= 2017:
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

    # Illinois EITC (2000+): a share of the federal EITC, nonrefundable
    # 2000-2002 and refundable from 2003 (taxsim_2022_10_21.f:5841-5845).
    if effective_year >= 2000:
        eitc_match_rate = p.num("eitc_match_rate")
        df = df.with_columns(il_eitc_raw=eitc_match_rate * (pl.col("eitc") / flate))
        if effective_year <= 2002:
            # `earncr = max(0, min(eirt*fed_eitc, statax-pcred))`, where
            # `statax` already has `pcred` subtracted, so `pcred` counts twice.
            df = df.with_columns(
                il_eitc_ceiling=pl.col("il_tax_after_property_credit") - pl.col("il_property_tax_credit")
            )
            df = df.with_columns(
                il_eitc=pl.max_horizontal(0.0, pl.min_horizontal(pl.col("il_eitc_raw"), pl.col("il_eitc_ceiling")))
            )
        else:
            df = df.with_columns(il_eitc=pl.col("il_eitc_raw"))
    else:
        df = df.with_columns(il_eitc=pl.lit(0.0))
    df = df.with_columns(il_tax_after_eitc=pl.col("il_tax_after_property_credit") - pl.col("il_eitc"))

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
