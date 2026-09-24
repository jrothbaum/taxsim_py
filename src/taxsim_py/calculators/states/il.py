"""Illinois individual income tax (`iltax`, taxsim_2022_10_21.f:5772-5848,
state id 14). See parameters/states/il/income_tax.yaml for the full scope
note (which real `iltax` mechanisms are unreachable given this project's
federal input schema, the property-tax mechanism's real 1991 formula-shape
change, and the year-extrapolation note for 2022+).

Takes the FEDERAL calculator's own output dataframe (already has `agi`,
`eitc`, `filing_status`, `depx`, `proptax`, `dividends` computed) rather
than recomputing federal quantities from scratch - `iltax` itself only ever
reads federal-computed values (`comnew(...)`) and a handful of raw inputs,
never anything state-specific beyond that.
"""

import polars as pl

from taxsim_py.engine.schema import PARAMETERS_ROOT, load_yaml, resolve_year
from taxsim_py.engine.state_extrapolation import resolve_state_year

IL_PARAMS = load_yaml(PARAMETERS_ROOT / "states" / "il" / "income_tax.yaml")
PRE1987_PARAMS = load_yaml(PARAMETERS_ROOT / "national" / "pre1987.yaml")
_PRE1987_STATUSES = ["single", "married_joint", "married_separate", "head_of_household"]


def compute_il_tax(df: pl.DataFrame, year: int) -> pl.DataFrame:
    effective_year, flate = resolve_state_year(year)
    p = IL_PARAMS
    rate = float(resolve_year(p["rate"], effective_year))
    exemption_amount = float(resolve_year(p["personal_exemption_amount"], effective_year))

    # `exemps` (a pure COUNT, comnew(68)) is divided by `flate` for
    # extrapolated years too, same as every dollar-valued field - a real,
    # replicated-as-found quirk of the source's own generic scaling loop
    # (taxsim_2022_10_21.f:62-65 scales comnew(1-98) uniformly except 4
    # named exceptions, none of which is comnew(68)), not a modeling choice
    # of this project's.
    df = df.with_columns(
        il_exemps=(1.0 + pl.col("depx") + pl.when(pl.col("filing_status") == "married_joint").then(1.0).otherwise(0.0))
        / flate
    )

    # Federal AGI plus the dividend-exclusion and capital-gains-exclusion
    # addbacks (`divexc(...)` and `comnew(7)=capded` in the source) -
    # negligible for law87-era years (1987+: no federal dividend exclusion
    # exists at all, so this is just the already-known $0.001 `divall`
    # fudge in reverse, and `capded` is unconditionally 0 in law87), but
    # REAL and non-negligible for 1977-1986, which overlaps `law79`'s own
    # federal range (state tax years start at 1977, and law79 runs
    # 1977-1986) - IL doesn't get the federal capital-gains EXCLUSION or
    # dividend exclusion at all, so both excluded amounts get added back
    # onto federal AGI. Missed on the first pass (federal AGI alone matched
    # law87-era years exactly, so it looked complete) - caught by widening
    # this project's own test years back to 1977 and comparing against the
    # oracle, not assumed complete.
    if effective_year <= 1986:
        pre1987_status_expr = pl.lit(None, dtype=pl.Float64)
        for status in _PRE1987_STATUSES:
            divexc_fed = float(resolve_year(PRE1987_PARAMS["dividend_exclusion"][status], effective_year))
            pre1987_status_expr = pl.when(pl.col("filing_status") == status).then(pl.lit(divexc_fed)).otherwise(
                pre1987_status_expr
            )
        df = df.with_columns(fed_divexc_amount=pre1987_status_expr)
        dividends_plus_fudge = pl.col("dividends") + 0.001
        if effective_year == 1981:
            # 1981 merges interest into the same federal dividend-exclusion
            # base (a real, one-year-only ERTA 1981 provision already
            # found and documented for federal purposes -
            # calculators/federal_pre1987.py) - the state addback mirrors
            # it exactly, per the state's own `divexc` function's identical
            # `law.eq.1981` special case (taxsim_2022_10_21.f:494-495).
            dividends_plus_fudge = dividends_plus_fudge + pl.col("intrec")
        df = df.with_columns(
            il_dividend_addback=pl.min_horizontal(dividends_plus_fudge, pl.col("fed_divexc_amount")).clip(0, None)
        )
        caprat = float(resolve_year(PRE1987_PARAMS["capital_gains_exclusion_rate"], effective_year))
        df = df.with_columns(il_capgains_addback=(caprat * pl.col("ltcg")).clip(0, None))
    else:
        # 1987+: no federal dividend exclusion exists at all, so
        # `divexc_state = data(12)-comnew(4)` reduces to `(dividends+
        # fudge)-(dividends+fudge) = 0` exactly (both sides carry the same
        # $0.001 `divall` fudge - taxsim_2022_10_21.f:494, `divexc=data(12)
        # -comnew(4)`) - not a residual $0.001, genuinely 0.
        df = df.with_columns(il_dividend_addback=pl.lit(0.0), il_capgains_addback=pl.lit(0.0))

    df = df.with_columns(il_agi=(pl.col("agi") + pl.col("il_dividend_addback") + pl.col("il_capgains_addback")) / flate)
    df = df.with_columns(il_exemption=exemption_amount * pl.col("il_exemps"))
    df = df.with_columns(il_proptax=pl.col("proptax") / flate)

    # Property tax: three genuinely different formula shapes over time, not
    # just parameter changes (see the YAML note) - no mechanism at all
    # before 1983; an AGI-reducing itemized deduction 1983-1990 (doubled
    # 1989-1990); a nonrefundable credit from 1991 on.
    if 1983 <= effective_year <= 1990:
        ptax_multiplier = 2.0 if effective_year >= 1989 else 1.0
        df = df.with_columns(il_ptax_deduction=ptax_multiplier * pl.col("il_proptax"))
    else:
        df = df.with_columns(il_ptax_deduction=pl.lit(0.0))
    df = df.with_columns(il_taxinc=(pl.col("il_agi") - pl.col("il_exemption") - pl.col("il_ptax_deduction")).clip(0, None))
    df = df.with_columns(il_tax_before_credits=pl.col("il_taxinc") * rate)

    if effective_year >= 1991:
        credit_rate = float(resolve_year(p["property_tax_credit_rate"], effective_year))
        df = df.with_columns(
            il_income_cap_multiplier=pl.when(pl.col("filing_status") == "married_joint").then(2.0).otherwise(1.0)
        )
        if effective_year >= 2017:
            income_cap = float(resolve_year(p["property_tax_credit_income_cap"], effective_year))
            df = df.with_columns(
                il_property_tax_credit=pl.when(pl.col("il_agi") > income_cap * pl.col("il_income_cap_multiplier"))
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

    # Illinois EITC: real 2000+ only, a flat percentage match of the
    # FEDERAL eitc amount (deflated like everything else) - non-refundable
    # 2000-2002 (`min(eirt*fed_eitc, statax-pcred)`), refundable from 2003
    # on (taxsim_2022_10_21.f:5841-5845).
    if effective_year >= 2000:
        eitc_match_rate = float(resolve_year(p["eitc_match_rate"], effective_year))
        df = df.with_columns(il_eitc_raw=eitc_match_rate * (pl.col("eitc") / flate))
        if effective_year <= 2002:
            # `earncr = max(0, min(eirt*fed_eitc, statax-pcred))` - but
            # `statax` at this point in the source has ALREADY had `pcred`
            # subtracted once (a few lines above), so `statax-pcred` really
            # means `tax_before_credits - 2*pcred`, not `-1*pcred` - the
            # same double-subtraction quirk already found for law60's
            # `addmin`/`gencr`. Confirmed via a live oracle probe (2001,
            # single, low wages + property tax large enough to make this
            # clamp actually bind).
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
    return df
