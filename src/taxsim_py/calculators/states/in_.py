"""Indiana individual income tax (`intax`, taxsim_2024_09_21.f:5870-6077,
state id 15). See parameters/states/in/income_tax.yaml for the full scope
note (confirmed-inert elderly-credit/property-credit/solar-credit fields).

Real, non-obvious mechanics found while building this:
1. A flat tax rate (`rate(law)`), not brackets - the simplest rate
   structure of any state built so far.
2. `comnew(65)` (the 1999-2002 EITC-equivalent formula's own AGI-like
   comparison figure) is live-probe-confirmed to be federal AGI itself -
   identical to `comnew(2)` in every probe tried (including with
   dividends present), so this project's own `agi` column is used
   directly, no separate reconstruction needed.
3. Indiana's own Unemployment Compensation exclusion (`unded`) applies
   EVERY year with nonzero UI, not just 2009/2020 - an AGI-threshold-
   based worksheet matching the classic pre-1987 FEDERAL UI-exclusion
   mechanic, but on Indiana's OWN $12,000/$18,000(joint) thresholds and
   keyed off `comnew(78)` - the TAXABLE (not excluded) portion of UI
   already included in federal AGI, real and equal to the full UI amount
   most years but $0 whenever federal fully excludes it (2009/2020) -
   reconstructed via the same UI "diff trick" several other states
   already use, generalized to run for ANY year with nonzero UI (not
   gated to law==2020 like every prior state), since federal.py itself
   already implements both the 2009 ARRA and 2020 CARES/ARPA exclusions.
   The formula's own `modagi` (used by the 2011+ EITC schedule below)
   must read federal's ORIGINAL, unmodified AGI (`comnew(2)`/this
   project's own `agi` column) here too, NOT Indiana's own progressively-
   adjusted state AGI - conflating the two was a real bug caught via a
   live oracle probe (2020/single/$10,000 wages/$8,000 UI: using the
   state-adjusted AGI wrongly capped the credit at $19.79 instead of the
   real $40.09, by pushing `modagi` too far into the phaseout band).
4. The 2009-2010 EITC piggyback recomputes federal EITC with `depx`
   (not `dep18`) capped at 2 before applying Indiana's own 9% rate
   (`data(8)=2; call nlaw(...)`) - but federal.py's own EITC formula uses
   `dep18` (`num_children=dep18.clip(0,3)`), matching this project's own
   already-established `data(203)=dep18` EIC-eligible-base finding from
   the very first federal milestone. Capping `depx` alone therefore
   doesn't change federal.py's own `eitc` column at all (AGI/earned_income/
   dep18 are all untouched) - implemented as a literal, faithful port of
   the source's own recompute (capping depx, leaving dep18 alone) rather
   than "fixed" to cap dep18 instead, since this project replicates the
   oracle's own real mechanics even when they turn out to be inert here.
5. The Permanent-Building-Fund-Tax-style flat add-on other states have
   doesn't exist here, but a near-analogue does for 2012 only: a flat
   $111-per-filer "Automatic Taxpayer Refund Credit", gated on the tax
   being positive BEFORE this credit is applied.
6. `comnew(68)` (exemps count) sits at array position 68 - inside the
   real dispatcher's own generic `comnew(1:98)` CPI-extrapolation deflate
   loop (year>2021 only), which divides by `flate` blindly by array
   POSITION, not by whether the value is actually a dollar amount. A pure
   integer count getting divided by a CPI ratio is a real, replicated-as-
   found quirk (matching the general note already in state_cpi_
   extrapolation.yaml) - caught as a near-uniform ~$1-$4 residual across
   nearly every 2022/2023 test case (scaling with each case's own
   exemption count) until `exemps_count` was explicitly divided by
   `flate` to match. `depx` itself (`data(8)`, array position 8, below
   the loop's own >=11 floor) is never divided.

Harness: 2,533/2,538 (99.8%). The 5 residuals are all 2023-only, small
($0.02-$0.21), and trace to the same already-accepted real-vs-oracle 2023
EITC-table divergence family documented across nearly every state built
this session (Indiana's own credit is a direct percentage of federal
EITC, so it inherits that divergence directly). Full multi-state suite
reconfirmed no regressions elsewhere.
"""

import polars as pl

from taxsim_py.calculators.federal import compute_regular_tax
from taxsim_py.calculators.federal_pre1987 import PRE1987_PARAMS
from taxsim_py.engine.schema import PARAMETERS_ROOT, load_yaml, resolve_year
from taxsim_py.engine.state_extrapolation import deflate_for_extrapolation, resolve_state_year

IN_PARAMS = load_yaml(PARAMETERS_ROOT / "states" / "in" / "income_tax.yaml")

_RAW_INPUT_COLUMNS = [
    "mstat", "depx", "dep17", "dep18", "dep6", "dep13", "pwages", "swages",
    "proptax", "otheritem", "mortgage", "childcare", "intrec", "psemp",
    "ssemp", "dividends", "stcg", "ltcg", "ui", "pui", "sui",
]


def _with_default(df: pl.DataFrame, column: str, default: float = 0.0) -> pl.DataFrame:
    if column in df.columns:
        return df
    return df.with_columns(pl.lit(default).alias(column))


def compute_in_tax(df: pl.DataFrame, year: int, force_itemize: bool | None = None) -> pl.DataFrame:
    effective_year, flate = resolve_state_year(year)
    p = IN_PARAMS
    for col in ("proptax", "dividends", "intrec", "ui", "pui", "sui", "depx", "dep17", "dep18"):
        df = _with_default(df, col)
    df = _with_default(df, "earned_income")
    df = _with_default(df, "eitc")

    df = df.with_columns(
        in_sep=pl.when(pl.col("filing_status") == "married_separate").then(2.0).otherwise(1.0),
        in_num_filers=pl.when(pl.col("filing_status") == "married_joint").then(2.0).otherwise(1.0),
    )

    df = deflate_for_extrapolation(
        df,
        flate,
        [
            "pwages", "swages", "proptax", "otheritem", "mortgage", "dividends", "intrec",
            "stcg", "ltcg", "ui", "pui", "sui", "agi", "earned_income", "eitc",
        ],
    )

    rate = float(resolve_year(p["rate_by_year"], effective_year))

    # --- AGI ---
    df = df.with_columns(in_agi=pl.col("agi").clip(0, None))
    # `comnew(79)` (SS-in-AGI) confirmed inert; `data(22)` confirmed inert.

    ui_total = pl.max_horizontal(pl.col("ui"), pl.col("pui") + pl.col("sui"))
    has_ui = (df.get_column("ui").abs().sum() + df.get_column("pui").abs().sum() + df.get_column("sui").abs().sum()) > 0

    if effective_year == 1981:
        excl_table = PRE1987_PARAMS["dividend_exclusion"]
        fed_excl = pl.lit(None, dtype=pl.Float64)
        for status in ("single", "head_of_household", "married_separate", "married_joint"):
            fed_excl = (
                pl.when(pl.col("filing_status") == status)
                .then(pl.lit(float(resolve_year(excl_table[status], effective_year))))
                .otherwise(fed_excl)
            )
        divexc = pl.min_horizontal(pl.col("dividends") + pl.col("intrec"), fed_excl)
        own_excl_per_filer = float(p["dividend_exclusion_indiana_1981_per_filer"][1960])
        own_excl = pl.min_horizontal(pl.col("dividends"), own_excl_per_filer * pl.col("in_num_filers"))
        df = df.with_columns(in_agi=pl.col("in_agi") + divexc - own_excl)

    # `comnew(78)` ("untax", federal's own UI-exclusion amount) - needed
    # for EVERY year with nonzero UI, not just 2020 (Indiana's own general
    # exclusion formula below reads it every year), since federal.py
    # itself implements both the 2009 ARRA and 2020 CARES/ARPA exclusions.
    # Reconstructed via the same diff-trick several other states already
    # use for 2020 alone, generalized here to run whenever UI is present.
    if has_ui:
        df_no_ui = df.select(_RAW_INPUT_COLUMNS).with_columns(ui=pl.lit(0.0), pui=pl.lit(0.0), sui=pl.lit(0.0))
        fed_no_ui = compute_regular_tax(df_no_ui, effective_year)
        untax = pl.col("agi") - fed_no_ui.get_column("agi")
    else:
        untax = pl.lit(0.0)
    df = df.with_columns(in_untax=untax)

    if effective_year == 2020:
        df = df.with_columns(in_agi=pl.col("in_agi") + ui_total - pl.col("in_untax"))
        # `comnew(26)<1` (not itemizing) $300 cash-charity addback: `data(58)`
        # (charity_cash) has no corresponding input column - confirmed inert.

    if effective_year == 2009 and has_ui:
        cap = float(p["ui_2009_addback_cap_per_filer"][1960])
        df = df.with_columns(in_agi=pl.col("in_agi") + pl.min_horizontal(ui_total, cap * pl.col("in_num_filers")))

    if has_ui:
        # Indiana's OWN UI exclusion (see module docstring point 3) - a
        # no-op except for 2009/2020 where `in_untax` (this project's
        # `comnew(78)` reconstruction) is nonzero.
        thr_single = float(p["ui_exclusion_threshold_single"][1960])
        thr_joint = float(p["ui_exclusion_threshold_married_joint"][1960])
        threshold = pl.when(pl.col("filing_status") == "married_joint").then(thr_joint).otherwise(thr_single)
        if effective_year <= 2008:
            xlin6 = pl.min_horizontal(pl.col("in_untax"), 0.5 * (pl.col("agi") - threshold).clip(0, None))
            unded = pl.col("in_untax") - xlin6
        else:
            xlin7 = 0.5 * (pl.col("agi") + ui_total - pl.col("in_untax") - threshold).clip(0, None)
            unded = (ui_total - xlin7).clip(0, None)
        df = df.with_columns(in_agi=pl.col("in_agi") - unded)

    # --- Deductions ---
    # Renter's deduction (`dedr`, real in the source - real caps kept in
    # the YAML for completeness/documentation) is permanently $0 for this
    # project's schema: `rentpaid` has no corresponding INPUT_COLUMNS
    # entry anywhere in this project (confirmed inert on the same grounds
    # Hawaii's own Renter's Credit already documents), so it's never
    # populated regardless of year.
    dedr = pl.lit(0.0)

    if effective_year >= 1999:
        home_cap = float(p["homeowner_proptax_deduction_cap_1999plus"][1960])
        dedown = pl.min_horizontal(pl.col("proptax"), home_cap)
        dedown = pl.when(pl.col("in_sep") == 2).then(dedown / pl.col("in_sep")).otherwise(dedown)
    else:
        dedown = pl.lit(0.0)

    if effective_year in (1997, 1998):
        wages_plus_se = pl.col("wages") + pl.col("psemp") + pl.col("ssemp")
        eligible = (
            (pl.col("depx") > 0)
            & (pl.col("in_agi") < 12000.0)
            & (0.8 * pl.col("in_agi").clip(0, None) < wages_plus_se)
        )
        dedei = pl.when(eligible).then(12000.0 - pl.col("in_agi").clip(0, None)).otherwise(0.0)
    else:
        dedei = pl.lit(0.0)

    df = df.with_columns(in_deduc=dedr + dedown + dedei)

    # --- Exemptions ---
    # `comnew(68)` (exemps count) sits at array position 68 - inside the
    # dispatcher's own generic comnew(1:98) deflate loop
    # (taxsim_2024_09_21.f:62-65), which divides by `flate` blindly by
    # POSITION, not by whether the value is really a dollar amount. A
    # pure integer count getting divided by a CPI ratio is a genuine,
    # replicated-as-found quirk (already flagged in state_cpi_
    # extrapolation.yaml's own module note) - real and nonzero only for
    # extrapolated years (flate!=1). `depx` itself (`data(8)`, position 8,
    # below the loop's own >=11 floor) is NEVER divided.
    exemps_count = (pl.col("in_num_filers") + pl.col("depx")) / flate
    if effective_year <= 1979:
        exemp = pl.col("in_num_filers") * 1000.0 + pl.col("depx") * 500.0
    elif effective_year <= 1984:
        base = exemps_count * 500.0
        is_joint = pl.col("filing_status") == "married_joint"
        xtra1 = (pl.col("in_agi") / 3.0 - 500.0).clip(0, 500)
        xtra2 = (pl.col("in_agi") * 2.0 / 3.0 - 500.0).clip(0, 500)
        xtra = pl.when(is_joint).then(xtra1 + xtra2).otherwise((pl.col("in_agi") - 500.0).clip(0, 500))
        exemp = base + xtra
    elif effective_year <= 1986:
        exemp = exemps_count * 1000.0
    else:
        exemp = exemps_count * 1000.0
        # `data(105)`/`data(9)`/`data(10)` additive terms and the 1999+
        # elderly-exemption addback are all confirmed inert.

    if effective_year in (1997, 1998):
        exemp = exemp + 500.0 * pl.col("depx")
    elif effective_year >= 1999:
        exemp = exemp + 1500.0 * pl.col("depx")

    df = df.with_columns(in_exemp=exemp)

    df = df.with_columns(in_taxinc=(pl.col("in_agi") - pl.col("in_deduc") - pl.col("in_exemp")).clip(0, None))
    df = df.with_columns(in_statax=pl.col("in_taxinc") * rate)
    if effective_year == 1979:
        df = df.with_columns(in_statax=pl.col("in_statax") * 0.85)

    # --- Credits --- (`pcred`/`ecred` confirmed inert - see module docstring)
    if 1999 <= effective_year <= 2002:
        rate_cr = float(p["eitc_1999_2002_rate"][1960])
        cap = float(p["eitc_1999_2002_income_cap"][1960])
        eligible = (
            (pl.col("depx") > 0)
            & ((pl.col("earned_income") >= 0.8 * pl.col("in_agi")) | (pl.col("in_agi") < 1.0))
            & (pl.col("in_agi") < cap)
        )
        earncr = pl.when(eligible).then(rate_cr * (cap - pl.col("in_agi").clip(0, None))).otherwise(0.0)
    elif 2003 <= effective_year <= 2008:
        rate_cr = float(p["eitc_2003_2008_rate"][1960])
        floor = float(p["eitc_2003_2008_federal_floor"][1960])
        earncr = pl.when(pl.col("eitc") >= floor).then(rate_cr * pl.col("eitc")).otherwise(0.0)
    elif effective_year >= 2009:
        rate_cr = float(p["eitc_2009plus_rate"][1960])
        floor = float(p["eitc_2009plus_federal_floor"][1960])
        fed_eitc = pl.col("eitc")
        if effective_year in (2009, 2010):
            has_depx_over_2 = (df.get_column("depx") > 2).any()
            if has_depx_over_2:
                df_capped = df.select(_RAW_INPUT_COLUMNS).with_columns(
                    depx=pl.min_horizontal(pl.col("depx"), 2.0)
                )
                fed_capped = compute_regular_tax(df_capped, effective_year)
                fed_eitc = pl.when(pl.col("depx") > 2).then(fed_capped.get_column("eitc")).otherwise(pl.col("eitc"))
        earncr = pl.when(fed_eitc >= floor).then(rate_cr * fed_eitc).otherwise(0.0)
        if effective_year >= 2011:
            ieic_expr = pl.min_horizontal(pl.col("dep18"), 2.0)
            crm = pl.when(ieic_expr == 0).then(
                pl.lit(float(resolve_year(p["eitc_crmax_0kids"], effective_year)))
            ).when(ieic_expr == 1).then(
                pl.lit(float(resolve_year(p["eitc_crmax_1kid"], effective_year)))
            ).otherwise(pl.lit(float(resolve_year(p["eitc_crmax_2kids"], effective_year))))
            ym = pl.when(ieic_expr == 0).then(
                pl.lit(float(resolve_year(p["eitc_ymax_0kids"], effective_year)))
            ).when(ieic_expr == 1).then(
                pl.lit(float(resolve_year(p["eitc_ymax_1kid"], effective_year)))
            ).otherwise(pl.lit(float(resolve_year(p["eitc_ymax_2kids"], effective_year))))
            rtbs = pl.when(ieic_expr == 0).then(
                pl.lit(float(p["eitc_rtbase_0kids"][1960]))
            ).when(ieic_expr == 1).then(
                pl.lit(float(p["eitc_rtbase_1kid"][1960]))
            ).otherwise(pl.lit(float(p["eitc_rtbase_2kids"][1960])))
            rtlw = pl.when(ieic_expr == 0).then(
                pl.lit(float(p["eitc_rtless_0kids"][1960]))
            ).when(ieic_expr == 1).then(
                pl.lit(float(p["eitc_rtless_1kid"][1960]))
            ).otherwise(pl.lit(float(p["eitc_rtless_2kids"][1960])))
            earny = pl.col("earned_income")
            # `comnew(6)` (net capital gain in AGI) loss add-back: no-op in
            # this project's scope (stcg/ltcg assumed non-negative). Uses
            # federal's own ORIGINAL `comnew(2)`/`agi`, NOT Indiana's own
            # progressively-adjusted `in_agi` (which by this point already
            # reflects Indiana's own UI-exclusion worksheet reduction) -
            # confirmed via a live oracle probe (2020/single/$10,000 wages/
            # $8,000 UI: using `in_agi`=$13,000 wrongly pushed this deep
            # into the phaseout band, capping the credit at $19.79 instead
            # of the real $40.09).
            modagi = pl.col("agi").clip(0, None)
            eic11 = pl.min_horizontal(rtbs * earny, crm)
            over = (modagi > ym) | (earny > ym)
            eicpo = rtlw * pl.max_horizontal(modagi, earny, ym) - rtlw * ym
            eic11 = pl.when(over).then(pl.min_horizontal(eic11, (crm - eicpo).clip(0, None))).otherwise(eic11)
            earncr = pl.min_horizontal(earncr, eic11)
    else:
        earncr = pl.lit(0.0)

    df = df.with_columns(in_credit=earncr)
    df = df.with_columns(in_statax=pl.col("in_statax") - pl.col("in_credit"))

    if effective_year == 2012:
        refund_cr = float(p["automatic_taxpayer_refund_credit_2012_per_filer"][1960]) * pl.col("in_num_filers")
        df = df.with_columns(
            in_statax=pl.when(pl.col("in_statax") > 0).then(pl.col("in_statax") - refund_cr).otherwise(pl.col("in_statax"))
        )
    # `data(38)` (solar/wind carryover credit) confirmed inert, 1980-1994.

    df = df.with_columns(siitax=pl.col("in_statax") * flate)
    return df
