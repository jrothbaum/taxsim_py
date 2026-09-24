"""Hawaii individual income tax (`hitax`, taxsim_2024_09_21.f:4960-5503,
state id 12). See parameters/states/hi/income_tax.yaml for the full scope
note (confirmed-inert elderly/blind/medical/solar/renters-credit/charity
fields) - the richest, most elaborate state built so far: a real capital-
gains alternative tax, five separate/stacking itemized-deduction
reductions, and half a dozen era-specific credits.

Real, non-obvious mechanics found while building this:
1. `xitded=comnew(30)` - Hawaii uses the FULL raw pre-Pease federal
   itemized total directly (unlike DC/Georgia's own `comnew(24)`-based
   POST-Pease approaches), then applies FIVE of its own, separately-
   stacking reductions on top: a 2011+ SALT-deductibility income
   threshold, a 1991-2010 Pease-style reduction, a SEPARATE 2011+ Pease-
   style reduction with its own, different threshold, a 2011-2015
   absolute-dollar cap, and (1982-1986 only) a "subtract that era's own
   standard deduction back out of both xitded AND stded" cancellation
   mechanic matching California's own pre-1987 pattern.
2. The capital-gains alternative tax (`max(comnew(6),0).gt.0`): taxable
   income EXCLUDING net capital gains is taxed at ordinary rates (floored
   at a bracket-specific minimum so this never produces a WORSE outcome
   than the regular computation), net capital gains are taxed at a flat
   7.25%, and the smaller of (ordinary regular tax) vs (this alternative
   total) wins - a real, general mechanism, not a Hawaii-only oddity (the
   same shape federal's own historical alternative tax on capital gains
   used).
3. The Renter's Credit is confirmed permanently inert - gated on
   `data(160)`=rentpaid>=$1,000, the SAME field California's own Renter's
   Credit already established is never populated by this project's input
   schema.
4. The 2020/2021 up-to-$300/$600 cash-contribution-while-taking-the-
   standard-deduction AGI reduction is confirmed permanently inert too -
   gated on `data(58)`, the same unimplementable `charity_cash` field
   already documented as a gap at the federal level (no input column
   ever drives it).

The 2018-2022 state EITC is nonrefundable (`earncr=min(.2*fed_eitc,
max(0,statax))`), unlike Delaware's own choice-of-refundable-or-not
mechanism or Colorado's fully refundable one.

Harness (1977-2023, extrapolation built in from the start): **2,341/2,350
(99.6%)**. Two real bugs, both in the bracket tables themselves rather
than the surrounding formula logic:
5. Nearly every bracket table (20 of 22) was transcribed missing its own
   FINAL (highest-threshold, top-rate) row - a systematic transcription
   slip, not a one-off, caught only because it understated tax at every
   income level once taxable income crossed the table's second-to-last
   threshold (671 of 2,350 cases failed on the first validation pass).
   Fixed by re-deriving every table's expected row count directly from
   the source's own `dimension` declarations (`stab89(2,8)` etc.) and
   verifying each YAML entry against it, rather than trusting a single
   by-hand transcription pass.
6. `comnew(6)` (net capital gain, used by the alternative tax) is
   federal's own gain INCLUDED IN AGI, not the raw `stcg+ltcg` sum - for
   years<=1986 that differs by the real federal pre-1987 LTCG exclusion
   (50% in 1977, 60% 1978-1986, the SAME `PRE1987_PARAMS[
   "capital_gains_exclusion_rate"]` other states already established),
   confirmed via a debug-instrumented oracle probe (ltcg=$100,000, 1980:
   `comnew(6)`=$40,000=$100,000*(1-0.60), not $100,000) - years>=1987
   need no adjustment (the federal exclusion was repealed, matching the
   already-passing 1987+ capital-gains test cases).

The 2 residuals beyond the already-accepted 2023 real-vs-oracle-EITC
divergence family (see feedback_real_params_over_oracle_bugs /
project_taxsim_py_port memory) are self-employment cases matching this
project's own EARLIEST documented artifact (from the federal build,
before any state was started): SE-income OASDI/HI amounts live in the
oracle's own COMMON block and aren't explicitly zeroed between records,
so a batched run can inherit a stale value from an unrelated prior
record - confirmed by computing the SAME case in isolation (bypassing
`scripts/validate_states.py`'s own batching) and getting an EXACT match
to the "expected" value.
"""

import polars as pl

from taxsim_py.calculators.federal import compute_regular_tax
from taxsim_py.engine.brackets import bracket_tax
from taxsim_py.engine.schema import PARAMETERS_ROOT, load_yaml, resolve_year
from taxsim_py.engine.state_extrapolation import deflate_for_extrapolation, resolve_state_year

HI_PARAMS = load_yaml(PARAMETERS_ROOT / "states" / "hi" / "income_tax.yaml")
PRE1987_PARAMS = load_yaml(PARAMETERS_ROOT / "national" / "pre1987.yaml")

# Raw federal input columns only - re-invoking `compute_regular_tax` (the
# 2020/2021 UI "untax" diff trick) must start from JUST these.
_RAW_INPUT_COLUMNS = [
    "mstat", "depx", "dep17", "dep18", "dep6", "dep13", "pwages", "swages",
    "proptax", "otheritem", "mortgage", "childcare", "intrec", "psemp",
    "ssemp", "dividends", "stcg", "ltcg", "ui", "pui", "sui",
]


def _with_default(df: pl.DataFrame, column: str, default: float = 0.0) -> pl.DataFrame:
    if column in df.columns:
        return df
    return df.with_columns(pl.lit(default).alias(column))


def _tablki(income: pl.Expr, rows: list[list[float]]) -> pl.Expr:
    """`tablki`-style linear interpolation between adjacent (threshold,
    value) points - below the first threshold, flat at rows[0]'s value;
    at/above the last (finite) threshold, flat at the final row's value
    (a real sentinel row, not a droppable one - see the YAML's own note).
    """
    thresholds = [r[0] for r in rows[:-1]]
    values = [r[1] for r in rows]
    expr = pl.lit(values[-1])
    for i in range(len(thresholds) - 1, -1, -1):
        t_hi = thresholds[i]
        v_hi = values[i]
        if i == 0:
            below = pl.lit(v_hi)
        else:
            t_lo = thresholds[i - 1]
            v_lo = values[i - 1]
            w = (income - t_lo) / (t_hi - t_lo)
            below = (w * v_lo + (1 - w) * v_hi) if v_hi > v_lo else (w * v_hi + (1 - w) * v_lo)
        expr = pl.when(income < t_hi).then(below).otherwise(expr)
    return expr


def compute_hi_tax(df: pl.DataFrame, year: int, force_itemize: bool | None = None) -> pl.DataFrame:
    effective_year, flate = resolve_state_year(year)
    p = HI_PARAMS
    for col in ("proptax", "otheritem", "mortgage", "depx", "dividends", "intrec", "childcare", "ui", "pui", "sui"):
        df = _with_default(df, col)
    df = _with_default(df, "state_sales_or_income_tax_ded")
    df = _with_default(df, "itemized_deduction")
    df = _with_default(df, "eitc")
    df = _with_default(df, "stcg")
    df = _with_default(df, "ltcg")

    df = df.with_columns(
        hi_sep=pl.when(pl.col("filing_status") == "married_separate").then(2.0).otherwise(1.0),
        hi_texp=pl.when(pl.col("filing_status") == "married_joint").then(2.0).otherwise(1.0),
    )

    # Year>LASTAT (2021): deflate every dollar-valued raw/federal-computed
    # input by `flate`, run 2021's REAL law (`effective_year`, forced to
    # 2021 by `resolve_state_year`) on the deflated figures, then reinflate
    # the final tax below (see engine/state_extrapolation.py). A no-op for
    # year<=2021 (`flate==1`).
    df = deflate_for_extrapolation(
        df,
        flate,
        [
            "pwages", "swages", "proptax", "otheritem", "mortgage", "dividends", "intrec",
            "stcg", "ltcg", "ui", "pui", "sui", "agi", "salt_capped", "state_sales_or_income_tax_ded",
            "itemized_deduction", "eitc", "childcare",
        ],
    )

    # --- AGI ---
    df = df.with_columns(hi_agi=pl.col("agi"))
    # 2020/2021: full unemployment compensation IS taxable in HI (unlike
    # federal's own CARES/ARPA exclusion) - add back whatever federal
    # excluded, via the same "untax" diff trick Alabama/Georgia already
    # established (federal_pre1987.py never runs for these years, so no
    # pre-1987 variant is needed here).
    if effective_year in (2020, 2021):
        ui_total = pl.max_horizontal(pl.col("ui"), pl.col("pui") + pl.col("sui"))
        if (df.get_column("ui").abs().sum() + df.get_column("pui").abs().sum() + df.get_column("sui").abs().sum()) > 0:
            df_no_ui = df.select(_RAW_INPUT_COLUMNS).with_columns(ui=pl.lit(0.0), pui=pl.lit(0.0), sui=pl.lit(0.0))
            fed_no_ui = compute_regular_tax(df_no_ui, effective_year)
            untax = pl.col("agi") - fed_no_ui.get_column("agi")
        else:
            untax = pl.lit(0.0)
        df = df.with_columns(hi_agi=pl.col("hi_agi") + ui_total - untax)
    # Pensions confirmed permanently $0 for this schema (no pension
    # input) - the `agi = agi - data(72)` subtraction is a no-op.

    # --- Exemptions ---
    xmp = float(resolve_year(p["personal_exemption_amount"], effective_year))
    df = df.with_columns(hi_exemp=(pl.col("hi_texp") + pl.col("depx")) * xmp)
    df = df.with_columns(hi_exema=pl.col("hi_exemp"))
    if 2009 <= effective_year <= 2015:
        base = float(p["personal_exemption_phaseout_base"][2009])
        step = float(p["personal_exemption_phaseout_step"][2009])
        phex = (
            pl.when(pl.col("filing_status") == "single").then(base)
            .when(pl.col("filing_status") == "head_of_household").then(1.25 * base)
            .otherwise(1.5 * base / pl.col("hi_sep"))
        )
        ln6 = (1.0 + (pl.col("hi_agi") - phex) / (step / pl.col("hi_sep"))).floor()
        phased = pl.col("hi_exema") - pl.col("hi_exema") * 0.02 * ln6
        df = df.with_columns(hi_exemp=pl.when(pl.col("hi_agi") > phex).then(phased).otherwise(pl.col("hi_exemp")))
    # Disability exemption (`n10.gt.0`) and "dependent of another return"
    # (`data(105)`) both confirmed permanently inert for this schema (no
    # elderly/blind/dependent-return inputs this project drives).

    # --- Standard deduction ---
    stded_single = float(resolve_year(p["standard_deduction_single"], effective_year))
    stded_joint = float(resolve_year(p["standard_deduction_married_joint"], effective_year))
    stded_sep = float(resolve_year(p["standard_deduction_married_separate"], effective_year))
    stded_hoh = float(resolve_year(p["standard_deduction_hoh"], effective_year))
    df = df.with_columns(
        hi_stded=pl.when(pl.col("filing_status") == "single").then(stded_single)
        .when(pl.col("filing_status") == "married_joint").then(stded_joint)
        .when(pl.col("filing_status") == "married_separate").then(stded_sep)
        .otherwise(stded_hoh)
    )
    if effective_year == 1980:
        df = df.with_columns(hi_stded=pl.min_horizontal((0.1 * pl.col("hi_agi")).clip(0, None), pl.col("hi_stded")))
    # 1983-1986 general-credit-era standard-deduction addback confirmed
    # permanently inert (`data(58)`/`data(59)`, no charity/misc input).

    # --- Itemized deduction --- (see module docstring point 1). Neither
    # `salt_capped` nor `itemized_deduction` is exposed by federal_
    # pre1987.py (years<=1986) - reconstructed locally the same way DC/
    # Georgia already do (safe: no Pease-style limitation existed before
    # 1991, so federal's pre- and post-reduction totals are identical).
    if effective_year <= 1986:
        df = df.with_columns(
            hi_raw_itemized=pl.col("proptax") + pl.col("otheritem") + pl.col("mortgage")
            + pl.col("state_sales_or_income_tax_ded")
        )
        df = df.with_columns(hi_xitded=pl.col("hi_raw_itemized"))
    else:
        df = df.with_columns(hi_raw_itemized=pl.col("salt_capped") + pl.col("mortgage"))
        df = df.with_columns(hi_xitded=pl.col("hi_raw_itemized"))

    if effective_year >= 2011:
        salt_thr = (
            pl.when(pl.col("filing_status") == "head_of_household")
            .then(float(p["salt_allowance_threshold_hoh"][2011]))
            .when((pl.col("filing_status") == "single") | (pl.col("hi_sep") == 2))
            .then(float(p["salt_allowance_threshold_single_or_separate"][2011]))
            .otherwise(float(p["salt_allowance_threshold_joint"][2011]))
        )
        sttax = pl.col("state_sales_or_income_tax_ded")
        df = df.with_columns(
            hi_xitded=pl.when(pl.col("agi") > salt_thr).then((pl.col("hi_xitded") - sttax).clip(0, None)).otherwise(pl.col("hi_xitded"))
        )
    # 2017+ medical-expense itemized adjustment confirmed permanently
    # inert (`comnew(20)`, `data(47)/(48)/(49)` - no medical-expense
    # input this schema drives).

    if 1991 <= effective_year <= 2010:
        threshold = float(p["itemized_phaseout_threshold_1991_2010"][1991]) / pl.col("hi_sep")
        reduce1 = pl.when(pl.col("hi_agi") > threshold).then(
            pl.min_horizontal(0.8 * pl.col("hi_xitded"), 0.03 * (pl.col("hi_agi") - threshold))
        ).otherwise(0.0)
        if effective_year in (2006, 2007):
            reduce1 = reduce1 * (2.0 / 3.0)
        elif effective_year in (2008, 2009):
            reduce1 = reduce1 / 3.0
        elif effective_year == 2010:
            reduce1 = pl.lit(0.0)
        df = df.with_columns(hi_xitded=pl.col("hi_xitded") - reduce1)

    if effective_year >= 2011:
        threshold2 = float(p["itemized_phaseout_threshold_2011plus"][2011]) / pl.col("hi_sep")
        reduce2 = pl.when(pl.col("hi_agi") > threshold2).then(
            pl.min_horizontal(0.8 * pl.col("hi_xitded"), 0.03 * (pl.col("hi_agi") - threshold2))
        ).otherwise(0.0)
        df = df.with_columns(hi_xitded=pl.col("hi_xitded") - reduce2)

    if 2011 <= effective_year <= 2015:
        phas92_hoh = float(p["itemized_cap_2011_2015_threshold_hoh"][2011])
        phas92_other = float(p["itemized_cap_2011_2015_threshold_other_base"][2011]) * pl.col("hi_texp")
        phas92 = pl.when(pl.col("filing_status") == "head_of_household").then(phas92_hoh).otherwise(phas92_other)
        cap_hoh = float(p["itemized_cap_2011_2015_amount_hoh"][2011])
        cap_other = float(p["itemized_cap_2011_2015_amount_other_base"][2011]) * pl.col("hi_texp")
        over_thr = pl.col("agi") > phas92
        df = df.with_columns(
            hi_xitded=pl.when(over_thr & (pl.col("filing_status") == "head_of_household"))
            .then(pl.min_horizontal(cap_hoh, pl.col("hi_xitded")))
            .when(over_thr)
            .then(pl.min_horizontal(cap_other, pl.col("hi_xitded")))
            .otherwise(pl.col("hi_xitded"))
        )

    if 1982 <= effective_year <= 1986:
        std_era = pl.when(pl.col("filing_status") == "single").then(stded_single).when(
            pl.col("filing_status") == "married_joint"
        ).then(stded_joint).when(pl.col("filing_status") == "married_separate").then(stded_sep).otherwise(stded_hoh)
        df = df.with_columns(
            hi_xitded=(pl.col("hi_raw_itemized") - std_era).clip(0, None),
            hi_stded=(pl.col("hi_stded") - std_era).clip(0, None),
        )

    if force_itemize is False and effective_year == 1999:
        df = df.with_columns(hi_xitded=pl.lit(0.0))

    df = df.with_columns(hi_deduc=pl.max_horizontal(pl.col("hi_xitded"), pl.col("hi_stded")))
    # 2020/2021 cash-contribution-while-standard AGI reduction confirmed
    # permanently inert (`data(58)`, no `charity_cash` input).

    df = df.with_columns(hi_taxinc=(pl.col("hi_agi") - pl.col("hi_exemp") - pl.col("hi_deduc")).clip(0, None))

    # --- Bracket tax --- head_of_household gets its OWN table directly;
    # single/married_separate use the shared table on full taxinc;
    # married_joint halves taxinc, runs it through the SAME table, then
    # doubles the result.
    year_table_map = [
        ((1977, 1986), "pre1987"),
        ((1987, 1987), "1987"),
        ((1988, 1988), "1988"),
        ((1989, 1998), "1989_1998"),
        ((1999, 2000), "1999_2000"),
        ((2001, 2001), "2001"),
        ((2002, 2006), "2002_2006"),
        ((2007, 2008), "2007_2008"),
        ((2009, 2015), "2009_2015"),
        ((2016, 2017), "2016_2017"),
    ]
    key = "2018plus"
    for (lo, hi), k in year_table_map:
        if lo <= effective_year <= hi:
            key = k
            break
    is_hoh = pl.col("filing_status") == "head_of_household"
    is_joint = pl.col("filing_status") == "married_joint"

    def _bracket_stat(taxinc_col: str) -> pl.Expr:
        brackets_single = p[f"brackets_single_{key}"]
        brackets_hoh = p[f"brackets_hoh_{key}"]
        taxy = pl.when(is_joint).then(pl.col(taxinc_col) / 2).otherwise(pl.col(taxinc_col))
        stat_single = bracket_tax(taxy, brackets_single)
        stat_single = pl.when(is_joint).then(stat_single * 2).otherwise(stat_single)
        stat_hoh = bracket_tax(pl.col(taxinc_col), brackets_hoh)
        return pl.when(is_hoh).then(stat_hoh).otherwise(stat_single)

    df = df.with_columns(hi_statax=_bracket_stat("hi_taxinc"))

    # --- Capital gains alternative tax --- (see module docstring point 2).
    # `comnew(6)` is federal's own net-capital-gain-INCLUDED-IN-AGI figure,
    # not the raw gross gain - for <=1986 that's net of the real federal
    # pre-1987 LTCG exclusion (50% in 1977, 60% 1978-1986), confirmed via
    # oracle probe (ltcg=$100,000, 1980: `comnew(6)`=$40,000=$100,000*
    # (1-0.60), not $100,000) - for 1987+ the federal exclusion was
    # repealed, so `comnew(6)` is just `stcg+ltcg` directly (matches the
    # already-passing 1987+ capital-gains test cases).
    df = _with_default(df, "ltcg")
    df = _with_default(df, "stcg")
    if effective_year <= 1986:
        excl = float(resolve_year(PRE1987_PARAMS["capital_gains_exclusion_rate"], effective_year))
        capgn = pl.col("stcg") + (1.0 - excl) * pl.col("ltcg")
    else:
        capgn = pl.col("ltcg") + pl.col("stcg")
    has_gain = capgn.clip(0, None) > 0
    brack = pl.when(is_hoh).then(36000.0).otherwise(24000.0 * pl.col("hi_texp"))
    taxyng = (pl.col("hi_taxinc") - capgn.clip(0, None)).clip(0, None)
    taxyng = pl.max_horizontal(brack, taxyng)
    taxycg = (pl.col("hi_taxinc") - taxyng).clip(0, None)
    df = df.with_columns(hi_taxyng=taxyng, hi_taxycg=taxycg)
    statng = _bracket_stat("hi_taxyng")
    statcg = pl.col("hi_taxycg") * 0.0725
    df = df.with_columns(
        hi_statax=pl.when(has_gain).then(pl.min_horizontal(pl.col("hi_statax"), statng + statcg)).otherwise(pl.col("hi_statax"))
    )

    # --- Credits ---
    # Child/Dependent Care Credit.
    cap_per_dep = float(resolve_year(p["child_care_credit_cap_per_dependent"], effective_year))
    max_deps = float(p["child_care_credit_max_dependents"][1960])
    child = pl.min_horizontal(pl.col("childcare"), cap_per_dep * pl.min_horizontal(pl.col("depx"), max_deps))
    child = pl.when(is_joint).then(pl.min_horizontal(child, pl.col("pwages"), pl.col("swages"))).otherwise(child)
    if effective_year <= 1989:
        chr_rate = 0.01 * pl.max_horizontal(10.0, 15.0 - pl.max_horizontal((pl.col("hi_agi") - 21000.0) / 2000.0, 0.0))
    elif effective_year <= 2015:
        chr_rate = 0.01 * pl.max_horizontal(15.0, 25.0 - pl.max_horizontal((pl.col("hi_agi") - 22000.0) / 2000.0, 0.0))
    else:
        chr_rate = 0.01 * pl.max_horizontal(15.0, 25.0 - pl.max_horizontal((pl.col("hi_agi") - 25000.0) / 5000.0, 0.0))
    df = df.with_columns(hi_chcr=chr_rate * child)

    # General Income Tax Credit - not available 1996+ (except the flat
    # $1/exemption revival 2001-2006/2008-2009, and 2007's own formula).
    if effective_year <= 1995:
        amt = float(resolve_year(p["general_credit_amount_by_year_pre1996"], effective_year))
        df = df.with_columns(hi_gencr=amt * (pl.col("hi_texp") + pl.col("depx")))
    elif (2001 <= effective_year <= 2006) or (2008 <= effective_year <= 2009):
        amt = float(p["general_credit_flat_2001_2009"][2001])
        df = df.with_columns(hi_gencr=amt * (pl.col("hi_texp") + pl.col("depx")))
    elif effective_year == 2007:
        under60k = pl.when(pl.col("agi") < 60000.0).then(140.0 - 70.0 * pl.col("agi") / 60000.0).otherwise(0.0)
        under60k_joint = pl.when(pl.col("agi") < 60000.0).then(160.0 - 90.0 * pl.col("agi") / 60000.0).otherwise(0.0)
        under30k = pl.when(pl.col("agi") < 30000.0).then(65.0 - 25.0 * pl.col("agi") / 30000.0).otherwise(0.0)
        df = df.with_columns(
            hi_gencr=pl.when(is_hoh).then(under60k).when(is_joint).then(under60k_joint).otherwise(under30k)
        )
    else:
        df = df.with_columns(hi_gencr=pl.lit(0.0))

    # Renter's Credit confirmed permanently inert (see module docstring).
    df = df.with_columns(hi_rcred=pl.lit(0.0))

    # Excise Tax Credit (repealed after 1994) / Refundable Food-Excise
    # Tax Credit (2008+, replaces it) - both real, `tablki`-based.
    if effective_year <= 1979:
        df = df.with_columns(hi_exc=_tablki(pl.col("hi_agi"), p["excise_credit_table_1977_1979"]) * pl.col("hi_texp"))
    elif effective_year <= 1987:
        df = df.with_columns(hi_exc=_tablki(pl.col("hi_agi"), p["excise_credit_table_1980_1987"]) * pl.col("hi_texp"))
    elif effective_year <= 1994:
        df = df.with_columns(hi_exc=_tablki(pl.col("hi_agi"), p["excise_credit_table_1988_1994"]) * pl.col("hi_texp"))
    elif 2008 <= effective_year <= 2015:
        fedagi = pl.col("agi").clip(0, None)
        df = df.with_columns(
            hi_exc=(pl.col("depx") + pl.col("hi_texp")) * _tablki(fedagi, p["food_excise_credit_table_2008_2015"])
        )
    elif effective_year >= 2016:
        fedagi = pl.col("agi").clip(0, None)
        exc_non_single = (pl.col("depx") + pl.col("hi_texp")) * _tablki(fedagi, p["food_excise_credit_table_2016plus_non_single"])
        exc_single = (pl.col("depx") + pl.col("hi_texp")) * _tablki(fedagi, p["food_excise_credit_table_2016plus_single"])
        df = df.with_columns(hi_exc=pl.when(pl.col("filing_status") == "single").then(exc_single).otherwise(exc_non_single))
    else:
        df = df.with_columns(hi_exc=pl.lit(0.0))

    # Food Tax Credit (not available since 1999).
    if effective_year <= 1998:
        amt = float(resolve_year(p["food_credit_amount_by_year"], effective_year))
        df = df.with_columns(hi_foodcr=amt * (pl.col("hi_texp") + pl.col("depx")))
    else:
        df = df.with_columns(hi_foodcr=pl.lit(0.0))

    # Low-Income (refundable) Credit (1999-2007 only).
    if 1999 <= effective_year <= 2007:
        t1 = float(p["low_income_credit_tier1_amount"][1960])
        t2 = float(p["low_income_credit_tier2_amount"][1960])
        t3 = float(p["low_income_credit_tier3_amount"][1960])
        c1 = float(p["low_income_credit_tier1_ceiling"][1960])
        c2 = float(p["low_income_credit_tier2_ceiling"][1960])
        c3 = float(p["low_income_credit_tier3_ceiling"][1960])
        units = pl.col("hi_texp") + pl.col("depx")
        df = df.with_columns(
            hi_lowcr=pl.when(pl.col("hi_agi") < c1).then(units * t1)
            .when(pl.col("hi_agi") < c2).then(units * t2)
            .when(pl.col("hi_agi") <= c3).then(units * t3)
            .otherwise(0.0)
        )
    else:
        df = df.with_columns(hi_lowcr=pl.lit(0.0))
    # "dependent of another return" zeroing (`data(105)`) confirmed
    # permanently inert - no such input this schema drives.

    df = df.with_columns(
        hi_credit=pl.col("hi_gencr") + pl.col("hi_rcred") + pl.col("hi_exc") + pl.col("hi_foodcr")
        + pl.col("hi_chcr") + pl.col("hi_lowcr")
    )
    df = df.with_columns(hi_statax=pl.col("hi_statax") - pl.col("hi_credit"))

    # --- State EITC (2018-2022, nonrefundable) ---
    if 2018 <= effective_year <= 2022:
        rate = float(p["eitc_rate_2018_2022"][2018])
        raw = rate * pl.col("eitc").clip(0, None)
        df = df.with_columns(hi_earncr=pl.min_horizontal(raw, pl.col("hi_statax").clip(0, None)))
    else:
        df = df.with_columns(hi_earncr=pl.lit(0.0))
    df = df.with_columns(hi_statax=pl.col("hi_statax") - pl.col("hi_earncr"))

    df = df.with_columns(siitax=pl.col("hi_statax") * flate)
    return df
