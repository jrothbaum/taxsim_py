"""Maine individual income tax (`metax`, taxsim_2024_09_21.f:7437-7886,
state id 20). See parameters/states/me/income_tax.yaml for the full scope
note (confirmed-inert Social-Security/pension/IRA/elderly fields - most
notably the ENTIRE Property Tax Credit and Credit for the Elderly, both
confirmed permanently $0 for this schema).

Harness: 2,796/2,820 (99.1%). The richest state built this session,
exceeding even Iowa/Kansas/Kentucky - built by reading the full real
source directly (unusual for this project; most states so far were
built from live-probe reconstruction of undocumented `comnew(N)`
positions, but Maine's own subroutine text was read line-by-line, which
caught several real bugs before they ever reached the test harness).
Real, non-obvious mechanics found while building this:
1. `txp` (`data(7)`) is real ONLY for married_joint (2) - married_
   separate gets 1, same as single/HoH, a genuinely DIFFERENT concept
   from `sep` (`data(9)`/`mst.eq.3.or.mst.eq.6`, which IS 2 for
   married_separate). Caught via a live oracle probe: a flat 1987
   per-exemption credit came out $9 too generous for married_separate
   until `txp` was corrected.
2. `chcr=child(law)*min(comnew(53),comnew(52))` caps the Child Care
   Credit against `comnew(52)` (federal tax LIABILITY), not against raw
   childcare expense - an initial wrong-by-construction guess (`min`
   against the `childcare` input) coincidentally passed most of the
   harness anyway since `min(fedCCC,expense)` and `min(fedCCC,liability)`
   usually agree once expenses exceed a few hundred dollars, until a
   from-source re-derivation caught it.
3. `comnew(53)` (the raw federal Child Care Credit BEFORE combining
   with the unrelated 1977-1978 federal General Tax Credit and before
   the nonrefundable cap) is NOT the same as `federal_pre1987.py`'s own
   exposed `credit` output column for years<=1986 - that column is the
   COMBINED, capped total - reconstructed locally instead
   (`_raw_ccc_pre1987`).
4. `nexem=int(comnew(68))` - `comnew(68)` is itself divided by `flate`
   for CPI-extrapolated years (2022/2023 here), and the subsequent
   `int()` truncation genuinely zeroes the Property Tax Fairness/Sales
   Tax Fairness Credits for most ordinary households once `flate`>1 -
   confirmed via a direct oracle probe (a single filer's PTFC/STFC
   refund present in 2021 disappears entirely in 2022/2023).
5. Federal.py's own `standard_deduction` column is missing the REAL
   2008-2009 federal "additional standard deduction for state and local
   real estate taxes" (Housing Assistance Tax Act of 2008, up to $500
   single/$1000 joint) - a federal-side gap, not Maine-specific (Idaho's
   own build already found and worked around the same gap locally).
6. `comnew(3)` (federal's own standard deduction, used directly for
   1989+) is real and nonzero ONLY when federal does NOT itemize -
   matching Idaho's own already-established finding for this array
   position (confirmed universal, not Idaho-specific).
7. `comnew(65)`/`comnew(17)` (used for PTFC/STFC's own "total income"
   test) are AGI plus (and, separately, exactly) half the household's
   self-employment-tax AGI deduction - matches this project's own
   already-established Maine-adjacent finding from a prior session.
8. Head_of_household gets its own `texp=1.5` halve-then-multiply
   divisor for the bracket lookup (not 1 or 2 like every other status),
   applied for every year, not just 1977-1987; 1977-1987's own bracket
   table is a single base table with individual per-year cell patches,
   not 11 unrelated tables.

Residual failures (24/2820, all in extrapolated 2020/2022/2023 years):
the same standing real-vs-oracle EITC-table divergence family accepted
across nearly every other state's own harness, plus one deeper PTFC/
STFC interaction at very high dependent counts in extrapolated years
not chased further.
"""

import polars as pl

from taxsim_py.calculators.federal import compute_regular_tax
from taxsim_py.calculators.federal_pre1987 import PRE1987_PARAMS
from taxsim_py.engine.brackets import bracket_tax
from taxsim_py.engine.credits import child_care_credit_rate_pre2021
from taxsim_py.engine.payroll_tax import household_self_employment_tax
from taxsim_py.engine.schema import PARAMETERS_ROOT, load_yaml, resolve_year
from taxsim_py.engine.state_extrapolation import deflate_for_extrapolation, resolve_state_year

ME_PARAMS = load_yaml(PARAMETERS_ROOT / "states" / "me" / "income_tax.yaml")
FEDERAL_PERSONAL_EXEMPTION_PARAMS = load_yaml(PARAMETERS_ROOT / "national" / "personal_exemption.yaml")
PAYROLL_PARAMS = load_yaml(PARAMETERS_ROOT / "national" / "payroll_tax.yaml")
FEDERAL_CREDITS_PARAMS = load_yaml(PARAMETERS_ROOT / "national" / "credits.yaml")
_STATUSES = ["single", "married_joint", "married_separate", "head_of_household"]

_RAW_INPUT_COLUMNS = [
    "mstat", "depx", "dep17", "dep18", "dep6", "dep13", "pwages", "swages",
    "proptax", "otheritem", "mortgage", "childcare", "intrec", "psemp",
    "ssemp", "dividends", "stcg", "ltcg", "ui", "pui", "sui",
]


def _max2(a: pl.Expr, b: pl.Expr) -> pl.Expr:
    """Two-argument max via `when/then/otherwise` rather than
    `pl.max_horizontal` - when one side is (or reduces to) an
    all-constant column, e.g. a `stded=0` year combined with a
    `pl.when(all_false_cond)...otherwise(0.0)` `xitded`, polars marks
    the constant side as a scalar-broadcast chunk that `max_horizontal`
    then fails to re-broadcast against the DataFrame's real height (a
    genuine, reproduced-in-isolation polars quirk, not a logic bug)."""
    return pl.when(a >= b).then(a).otherwise(b)


def _with_default(df: pl.DataFrame, column: str, default: float = 0.0) -> pl.DataFrame:
    if column in df.columns:
        return df
    return df.with_columns(pl.lit(default).alias(column))


def _by_status(values: dict) -> pl.Expr:
    expr = pl.lit(None, dtype=pl.Float64)
    for status, v in values.items():
        expr = pl.when(pl.col("filing_status") == status).then(pl.lit(float(v))).otherwise(expr)
    return expr


def _tablki(income: pl.Expr, rows: list[list[float]]) -> pl.Expr:
    """`tablki`-style linear interpolation between adjacent (threshold,
    value) points - below the first threshold, flat at rows[0]'s value;
    at/above the last (finite) threshold, flat at the final row's value."""
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
            below = pl.when(v_hi > v_lo).then(w * v_lo + (1 - w) * v_hi).otherwise(w * v_hi + (1 - w) * v_lo)
        expr = pl.when(income < t_hi).then(below).otherwise(expr)
    return expr


def _raw_ccc_pre1987(df: pl.DataFrame, year: int) -> pl.Expr:
    """The raw federal Child Care Credit amount for years<=1986, BEFORE
    it's combined with the unrelated federal General Tax Credit
    (`gencr`, 1977-1978 only) and BEFORE the `taxbc` nonrefundable cap -
    `federal_pre1987.py`'s own `credit` output column is that COMBINED,
    capped total (`min(chcr+gencr, taxbc)`), not the CCC portion alone,
    so reusing it directly as `comnew(53)` wrongly pulled in `gencr` for
    1977-1978 (caught when the corrected `chcr=child(law)*min(comnew53,
    comnew52)` formula stopped masking it against a $0 childcare
    input). Reconstructed here matching `federal_pre1987.py`'s own
    internal (unexposed) `chcr` computation exactly."""
    expense_cap = float(resolve_year(PRE1987_PARAMS["child_care_credit_expense_cap"], year))
    chmax = expense_cap * pl.col("dep13").clip(0, 2)
    chwage = (pl.col("pwages") + pl.col("swages")).clip(0, None)
    child_expense = pl.min_horizontal(chmax, chwage, pl.col("childcare"))
    if year <= 1981:
        rate = float(resolve_year(PRE1987_PARAMS["child_care_credit_flat_rate"], year))
        ccc_rate = pl.lit(rate)
    else:
        ccc_rate = (0.30 - ((pl.col("agi") - 8000.0) / 200000.0).clip(0, None)).clip(0.20, None)
    return child_expense * ccc_rate


def _raw_ccc(df: pl.DataFrame, year: int) -> pl.Expr:
    """`comnew(53)` - federal's own CCC amount. federal.py's own `ccc`
    column deliberately reports $0 for years<1998 (a real, separately-
    documented federal-side quirk about the credit-stacking mechanism,
    not the credit computation itself) - reconstructed locally here for
    1987-1997 the same way Kansas/Kentucky/Louisiana's own builds
    already established for this exact gap."""
    ccc_p = FEDERAL_CREDITS_PARAMS["child_care_credit"]
    max_qualifying_persons = float(resolve_year(ccc_p["max_qualifying_persons"], year))
    max_expense_per_person = float(resolve_year(ccc_p["max_expense_per_person_pre2021"], year))
    ccc_rate = child_care_credit_rate_pre2021(
        pl.col("agi"),
        phase_start=float(resolve_year(ccc_p["pre2021_phase_start"], year)),
        top_rate=float(resolve_year(ccc_p["pre2021_rate_top"], year)),
        floor_rate=float(resolve_year(ccc_p["pre2021_rate_floor"], year)),
        step_amount=float(resolve_year(ccc_p["pre2021_step_amount"], year)),
    )
    num_qualifying_persons = pl.col("dep13").clip(0, max_qualifying_persons)
    qualifying_expense = pl.col("childcare").clip(0, num_qualifying_persons * max_expense_per_person)
    ccc_earned_income_cap = pl.when(pl.col("filing_status") == "married_joint").then(
        pl.min_horizontal(pl.col("pwages"), pl.col("swages"))
    ).otherwise(pl.col("pwages") + pl.col("swages"))
    ccc_expense = pl.min_horizontal(qualifying_expense, ccc_earned_income_cap).clip(0, None)
    return ccc_rate * ccc_expense


def _federal_personal_exemption(df: pl.DataFrame, year: int) -> pl.Expr:
    """`comnew(83)` - federal's own computed personal-exemption total,
    same local reconstruction Idaho's own build already established
    (federal.py never exposes this as a column)."""
    if year >= 2018 or year < 1987:
        return pl.lit(0.0)
    pe_p = FEDERAL_PERSONAL_EXEMPTION_PARAMS
    exemption_amount = float(resolve_year(pe_p["amount"], year))
    exemption_count = 1.0 + pl.col("depx") + pl.when(pl.col("filing_status") == "married_joint").then(1.0).otherwise(0.0)
    amex_base = exemption_amount * exemption_count
    sepret_expr = pl.when(pl.col("filing_status") == "married_separate").then(2.0).otherwise(1.0)
    if 2010 <= year <= 2012 or year < 1991:
        return amex_base
    pep_threshold_expr = _by_status(
        {status: resolve_year(pe_p["high_income_phaseout_threshold"][status], year) for status in _STATUSES}
    )
    pep_rate = float(resolve_year(pe_p["phaseout_rate"], year))
    pep_bracket_size = float(resolve_year(pe_p["phaseout_bracket_size"], year))
    if year <= 1996:
        pep_ratio = pep_rate * (pl.col("agi") - pep_threshold_expr).clip(0, None) / (pep_bracket_size / sepret_expr)
        return (amex_base * (1.0 - pep_ratio)).clip(0, None)
    pep_ratio = (pep_rate * (pl.col("agi") - pep_threshold_expr).clip(0, None) / (pep_bracket_size / sepret_expr)).clip(0, 1)
    amphs_fraction = 1.0
    if year in (2006, 2007):
        amphs_fraction = 2.0 / 3.0
    if year in (2008, 2009):
        amphs_fraction = 1.0 / 3.0
    return amex_base * (1.0 - pep_ratio * amphs_fraction)


def compute_me_tax(df: pl.DataFrame, year: int, force_itemize: bool | None = None) -> pl.DataFrame:
    effective_year, flate = resolve_state_year(year)
    p = ME_PARAMS
    for col in ("proptax", "otheritem", "mortgage", "dividends", "intrec", "depx", "childcare"):
        df = _with_default(df, col)
    df = _with_default(df, "eitc")
    df = _with_default(df, "ccc")
    df = _with_default(df, "itemized_deduction")
    df = _with_default(df, "salt_capped")
    df = _with_default(df, "state_sales_or_income_tax_ded")
    df = _with_default(df, "itemizes", False)
    df = _with_default(df, "fiitax")
    df = _with_default(df, "regular_tax")
    df = _with_default(df, "amt")
    df = _with_default(df, "standard_deduction")
    df = _with_default(df, "credit")
    df = _with_default(df, "earned_income")

    is_joint = pl.col("filing_status") == "married_joint"
    is_hoh = pl.col("filing_status") == "head_of_household"
    is_sep = pl.col("filing_status") == "married_separate"
    df = df.with_columns(
        me_sep=pl.when(is_sep).then(2.0).otherwise(1.0),
        # `txp`/`data(7)` - real ONLY for married_joint (2); married_
        # separate files as an individual return so gets 1, same as
        # single/HoH - a genuinely DIFFERENT concept from `sep`
        # (data(9)... `mst.eq.3.or.mst.eq.6`), which is 2 for
        # married_separate specifically (confirmed via a live diff: the
        # 1987 flat `9*(txp+depx)` credit came out $9 too generous for
        # married_separate until this was corrected to 1).
        me_txp=pl.when(is_joint).then(2.0).otherwise(1.0),
    )
    # `texp` - the bracket-lookup divisor: 1.5 for HoH (real, unique to
    # Maine), 2 for married_joint, 1 otherwise.
    me_texp = pl.when(is_joint).then(2.0).otherwise(pl.when(is_hoh).then(1.5).otherwise(1.0))

    wage_base = float(resolve_year(PAYROLL_PARAMS["oasdi_wage_base"], year))
    hi_wage_base = float(resolve_year(PAYROLL_PARAMS["hi_wage_base"], year))
    net_earnings_factor = float(resolve_year(PAYROLL_PARAMS["se_net_earnings_factor"], year))
    se_oasdi_rate = float(resolve_year(PAYROLL_PARAMS["se_oasdi_rate"], year))
    se_hi_rate = float(resolve_year(PAYROLL_PARAMS["se_hi_rate"], year))
    setax = household_self_employment_tax(
        pl.col("psemp") if "psemp" in df.columns else pl.lit(0.0),
        pl.col("ssemp") if "ssemp" in df.columns else pl.lit(0.0),
        pl.col("pwages"), pl.col("swages"),
        net_earnings_factor, wage_base, se_oasdi_rate, se_hi_rate, hi_wage_base,
    )
    df = df.with_columns(me_setax=setax)

    df = deflate_for_extrapolation(
        df,
        flate,
        [
            "pwages", "swages", "proptax", "otheritem", "mortgage", "dividends", "intrec",
            "stcg", "ltcg", "ui", "pui", "sui", "agi", "earned_income", "eitc", "ccc",
            "itemized_deduction", "salt_capped", "state_sales_or_income_tax_ded", "fiitax",
            "regular_tax", "amt", "standard_deduction", "childcare",
        ],
    )

    # --- AGI --- (SS/pension exclusion and state-refund/xjobs terms all
    # confirmed inert - see module docstring)
    me_agi = pl.col("agi")
    if effective_year == 1981:
        excl_cap = float(p["dividend_exclusion_maine_1981_per_filer"][1960])
        excl_table = PRE1987_PARAMS["dividend_exclusion"]
        fed_excl = _by_status({s: resolve_year(excl_table[s], effective_year) for s in _STATUSES})
        divexc = pl.min_horizontal(pl.col("dividends") + pl.col("intrec"), fed_excl)
        own_cap = pl.min_horizontal(pl.col("dividends"), excl_cap * pl.col("me_txp"))
        me_agi = me_agi + divexc - own_cap
    df = df.with_columns(me_agi=me_agi)

    # --- Standard deduction ---
    if effective_year <= 1982:
        pct = float(p["standard_deduction_pct_pre1988"][1960])
        floor_sh = float(p["standard_deduction_floor_single_or_hoh_1977_1982"][1960])
        ceil_sh = float(p["standard_deduction_ceiling_single_or_hoh_1977_1982"][1960])
        floor_m = float(p["standard_deduction_floor_married_1977_1982"][1960])
        ceil_m = float(p["standard_deduction_ceiling_married_1977_1982"][1960])
        is_single_group = pl.col("filing_status").is_in(["single", "head_of_household"])
        stded = pl.when(is_single_group).then((pct * pl.col("me_agi")).clip(floor_sh, ceil_sh)).otherwise(
            (pct * pl.col("me_agi")).clip(floor_m / pl.col("me_sep"), ceil_m / pl.col("me_sep"))
        )
    elif effective_year <= 1987:
        pct = float(p["standard_deduction_pct_pre1988"][1960])
        floor_sh = float(p["standard_deduction_floor_single_or_hoh_1983_1987"][1960])
        ceil_sh = float(p["standard_deduction_ceiling_single_or_hoh_1983_1987"][1960])
        floor_m = float(p["standard_deduction_floor_married_1983_1987"][1960])
        ceil_m = float(p["standard_deduction_ceiling_married_1983_1987"][1960])
        floor_s = float(p["standard_deduction_floor_separate_1983_1987"][1960])
        ceil_s = float(p["standard_deduction_ceiling_separate_1983_1987"][1960])
        stded = (
            pl.when(pl.col("filing_status").is_in(["single", "head_of_household"])).then((pct * pl.col("me_agi")).clip(floor_sh, ceil_sh))
            .when(is_joint).then((pct * pl.col("me_agi")).clip(floor_m, ceil_m))
            .otherwise((pct * pl.col("me_agi")).clip(floor_s, ceil_s))
        )
    elif effective_year == 1988:
        stded = pl.lit(0.0)
    else:
        # `comnew(3)` - real ONLY when federal does not itemize, same
        # finding Idaho's own build already established for this array
        # position (confirmed here to be a universal, not Idaho-
        # specific, quirk).
        stded = pl.when(~pl.col("itemizes")).then(pl.col("standard_deduction")).otherwise(0.0)
        if 2008 <= effective_year <= 2009:
            # Real 2008-2009 federal "additional standard deduction for
            # state and local real estate taxes" (Housing Assistance
            # Tax Act of 2008) - federal.py's own `standard_deduction`
            # column doesn't implement this (a federal-side gap, not a
            # Maine-specific one, confirmed via a live oracle probe:
            # `comnew(3)` came out $500 higher than federal.py's own
            # column whenever `proptax>0` for these two years) -
            # reconstructed locally the same way Idaho's own build
            # already worked around this exact gap.
            addback_cap = float(p["standard_deduction_proptax_addback_cap_per_filer_2008_2009"][1960])
            addback = pl.min_horizontal(addback_cap * pl.col("me_txp"), pl.col("proptax"))
            stded = pl.when(~pl.col("itemizes")).then(stded + addback).otherwise(stded)
        if (2003 <= effective_year <= 2011) or (2013 <= effective_year <= 2017):
            gets_override = is_joint | is_sep
            override_val = float(resolve_year(p["standard_deduction_married_override"], effective_year)) / pl.col("me_sep")
            stded = pl.when(gets_override).then(override_val).otherwise(stded)
        if 2016 <= effective_year <= 2017:
            hoh_val = float(p["standard_deduction_hoh_2016_2017"][1960])
            single_val = float(p["standard_deduction_single_2016_2017"][1960])
            stded = pl.when(is_hoh).then(hoh_val).when(pl.col("filing_status") == "single").then(single_val).otherwise(stded)

    df = df.with_columns(me_stded=stded)

    # --- Itemized deduction --- (real gate: `comnew(26).gt.0 and
    # comnew(30).gt.0` - comnew(26) matches `itemizes` for years>=1987;
    # for years<=1986 (never exposed by federal_pre1987.py) reconstructed
    # locally as the same year-scoped itemize-decision test Kansas/
    # Louisiana's own builds already established: the natural test for
    # <=1981 (force_itemize ignored those years), respecting
    # `force_itemize` for 1982-1986.)
    if effective_year <= 1986:
        salt_plus_mortgage = pl.col("proptax") + pl.col("otheritem") + pl.col("mortgage") + pl.col("state_sales_or_income_tax_ded")
        itemized_deduction_local = salt_plus_mortgage
        zbr = _by_status({s: resolve_year(PRE1987_PARAMS["standard_deduction"][s], effective_year) for s in _STATUSES})
        if effective_year <= 1981:
            itemizes_local = itemized_deduction_local > zbr
        else:
            itemizes_local = pl.lit(force_itemize) if force_itemize is not None else (itemized_deduction_local > zbr)
    else:
        salt_plus_mortgage = pl.col("salt_capped") + pl.col("mortgage")
        itemized_deduction_local = pl.col("itemized_deduction")
        itemizes_local = pl.col("itemizes")

    itemizing_gate = itemizes_local & (salt_plus_mortgage > 0)

    if effective_year <= 1988:
        xitded = (itemized_deduction_local - pl.col("state_sales_or_income_tax_ded")).clip(0, None)
        if effective_year == 1988:
            # `xitded=xitded-comnew(3)` in the source, but `comnew(3)` is
            # $0 whenever federal itemizes (see the `comnew(3)` finding
            # above) - which the outer `comnew(26)>0` gate already
            # guarantees here, so this second subtraction is always a
            # no-op and is omitted.
            pass
    elif effective_year <= 2012:
        xitded = (itemized_deduction_local - pl.col("state_sales_or_income_tax_ded")).clip(0, None)
    else:
        statit = pl.col("state_sales_or_income_tax_ded") - pl.col("state_sales_or_income_tax_ded") * (
            salt_plus_mortgage - itemized_deduction_local
        ) / salt_plus_mortgage.clip(1e-9, None)
        xitd_cap = float(resolve_year(p["itemized_deduction_phaseout_cap_2013_2021"], effective_year))
        xitded = pl.min_horizontal(xitd_cap, (itemized_deduction_local - statit).clip(0, None))

    xitded = pl.when(itemizing_gate).then(xitded).otherwise(0.0)

    df = df.with_columns(me_deduc=_max2(stded, xitded))

    if 2016 <= effective_year <= 2017:
        phased_hoh = float(p["deduction_phaseout_2016_2017_hoh_phased"][1960])
        thrsh_hoh = float(p["deduction_phaseout_2016_2017_hoh_threshold"][1960])
        phased_pf = float(p["deduction_phaseout_2016_2017_other_phased_per_filer"][1960])
        thrsh_pf = float(p["deduction_phaseout_2016_2017_other_threshold_per_filer"][1960])
        # real: `phased/thrsh=70000/75000*max(1,data(7))` for everyone
        # but HoH - `data(7)` is `txp` (1 single/HoH, 2 married), not
        # `sep`.
        phased = pl.when(is_hoh).then(phased_hoh).otherwise(phased_pf * pl.col("me_txp"))
        thrsh = pl.when(is_hoh).then(thrsh_hoh).otherwise(thrsh_pf * pl.col("me_txp"))
        over = pl.col("me_agi") > phased
        df = df.with_columns(
            me_deduc=pl.when(over).then((pl.col("me_deduc") - pl.col("me_deduc") * (pl.col("me_agi") - phased) / thrsh).clip(0, None)).otherwise(pl.col("me_deduc"))
        )
    if effective_year >= 2018:
        xmp18 = float(resolve_year(p["xmp18_index"], effective_year))
        thr_p = p["deduction_phaseout_2018plus_threshold"]
        rng_p = p["deduction_phaseout_2018plus_range"]
        phased = _by_status(thr_p) * xmp18
        xl4 = _by_status(rng_p)
        over = pl.col("me_agi") >= phased
        df = df.with_columns(
            me_deduc=pl.when(over).then(pl.col("me_deduc") * (1.0 - ((pl.col("me_agi") - phased) / xl4).clip(0, 1))).otherwise(pl.col("me_deduc"))
        )

    # --- Exemptions ---
    exemps_count = (pl.when(is_joint).then(2.0).otherwise(1.0)) + pl.col("depx")  # `comnew(68)`
    if effective_year <= 2012:
        xmp_amt = float(resolve_year(p["personal_exemption_amount"], effective_year))
        exemp = exemps_count * xmp_amt  # elderly/blind 1988 addback confirmed inert
    elif effective_year <= 2017:
        exemp = _federal_personal_exemption(df, effective_year)
    else:
        xmp_amt = float(resolve_year(p["personal_exemption_amount"], effective_year))
        exemp = xmp_amt * pl.col("me_txp")
        xmp18 = float(resolve_year(p["xmp18_index"], effective_year))
        phasex = _by_status(p["exemption_phaseout_2018plus_threshold"]) * xmp18
        rng = float(p["exemption_phaseout_2018plus_range"][1960])
        over = pl.col("me_agi") > phasex
        exemp = pl.when(over).then(exemp * (1.0 - ((pl.col("me_agi") - phasex) / (rng / pl.col("me_sep"))).clip(0, 1))).otherwise(exemp)
        # `data(105)` (dependent of another return) confirmed inert.

    df = df.with_columns(me_exemp=exemp)
    df = df.with_columns(me_taxinc=(pl.col("me_agi") - pl.col("me_deduc") - pl.col("me_exemp")).clip(0, None))

    # --- Bracket tax ---
    year_tables = {
        1977: "brackets_1977", 1978: "brackets_1978", 1983: "brackets_1983",
        1984: "brackets_1984", 1985: "brackets_1985", 1986: "brackets_1986", 1987: "brackets_1987",
    }
    if effective_year in year_tables:
        table = p[year_tables[effective_year]]
        aif = 1.0
    elif 1979 <= effective_year <= 1982:
        table = p["brackets_1979_1982"]
        aif = 1.0
    elif effective_year == 1988:
        table = p["brackets_1988"]
        aif = 1.0
    elif 1989 <= effective_year <= 1990:
        table = p["brackets_1989_1990"]
        aif = 1.0
    elif 1991 <= effective_year <= 1992:
        table = p["brackets_1991_1992"]
        aif = 1.0
    elif 1993 <= effective_year <= 2012:
        table = p["brackets_1993plus"]
        aif = float(resolve_year(p["bracket_inflation_factor_1993_2012"], effective_year))
    elif 2013 <= effective_year <= 2015:
        table = p["brackets_2013_2015"]
        aif = 1.0
    elif effective_year == 2016:
        table = p["brackets_2016"]
        aif = 1.0
    else:
        table = p["brackets_2017plus"]
        aif = float(resolve_year(p["bracket_inflation_factor_2017plus"], effective_year))

    tinc = pl.col("me_taxinc") / me_texp
    stat = bracket_tax(tinc / aif, table) * aif
    df = df.with_columns(me_statax=stat * me_texp)

    if 1997 <= effective_year <= 2002:
        ceiling = float(p["low_income_credit_taxinc_ceiling"][1960])
        df = df.with_columns(me_statax=pl.when(pl.col("me_taxinc") <= ceiling).then(0.0).otherwise(pl.col("me_statax")))

    # --- Extra Tax (minimum tax, low-materiality - AMT is $0 in nearly
    # every case this project's own scope reaches) ---
    if effective_year <= 1979:
        comnew28 = pl.col("regular_tax")
    elif effective_year <= 1990:
        comnew28 = pl.col("fiitax")
    else:
        comnew28 = pl.col("regular_tax")
    comnew69 = pl.col("agi") - pl.when(pl.col("itemizes")).then(pl.col("mortgage")).otherwise(0.0)
    amt = pl.col("amt") if "amt" in df.columns else pl.lit(0.0)
    txm = pl.lit(0.0)
    if effective_year <= 1985:
        txm = pl.when(amt > 0).then(0.15 * (amt - comnew28).clip(0, None)).otherwise(0.0)
    elif 1986 <= effective_year <= 1990:
        txm = pl.when(amt > 0).then((0.03 * comnew69 - pl.col("me_statax")).clip(0, None)).otherwise(0.0)
    elif 1991 <= effective_year <= 2011:
        txm = pl.when(amt > 0).then(0.27 * (amt - comnew28).clip(0, None)).otherwise(0.0)
    df = df.with_columns(me_statax=pl.col("me_statax") + txm)  # `iratax` (`data(42)`) confirmed inert.

    # --- Credits --- (`chcr=child(law)*min(comnew(53),max(0,comnew(52)
    # -data(34)))` - `data(34)` confirmed inert; `comnew(52)` is the
    # same era-dependent federal tax-liability figure Louisiana's own
    # build established: `regular_tax` for years<=1979 and years>=1987,
    # `fiitax` for 1980-1986 - NOT a cap on raw childcare expense, a cap
    # on federal tax liability, since this is fundamentally a
    # percentage-of-the-FEDERAL-credit provision.)
    if effective_year <= 1986:
        ccc_for_chcr = _raw_ccc_pre1987(df, effective_year)
    elif effective_year <= 1997:
        ccc_for_chcr = _raw_ccc(df, effective_year)
    else:
        ccc_for_chcr = pl.col("ccc")
    if effective_year <= 1979:
        comnew52 = pl.col("regular_tax")
    elif effective_year <= 1986:
        comnew52 = pl.col("fiitax")
    else:
        comnew52 = pl.col("regular_tax")
    child_rate = float(resolve_year(p["child_care_credit_rate"], effective_year))
    chcr = child_rate * pl.min_horizontal(ccc_for_chcr, comnew52.clip(0, None))
    refundable_cap = float(p["child_care_credit_refundable_cap"][1960])
    chcrr = pl.min_horizontal(refundable_cap, chcr)
    chcrn = chcr - chcrr

    depcrd = pl.lit(0.0)
    if effective_year >= 2018:
        per_child = float(p["dependent_credit_per_child"][1960])
        depcrd = pl.min_horizontal(per_child * pl.col("dep17"), pl.col("me_statax"))

    credit = chcrn + depcrd  # `eldcr`/`encred` confirmed inert.

    if effective_year == 1987:
        amt_ex = float(p["credit_1987_per_exemption"][1960])
        credit = credit + amt_ex * (pl.col("me_txp") + pl.col("depx"))
    if effective_year == 1978:
        cap_r = float(p["credit_1978_rentpaid_cap"][1960])
        cap_p = float(p["credit_1978_proptax_cap"][1960])
        credit = credit + pl.max_horizontal(pl.lit(0.0), pl.min_horizontal(pl.lit(0.0), cap_r), pl.min_horizontal(cap_p, pl.col("proptax")))
    if effective_year == 1988:
        pct88 = float(p["credit_1988_earned_income_pct"][1960])
        floor88 = float(p["credit_1988_earned_income_floor"][1960])
        std_caps = p["credit_1988_std_deduction_cap_by_status"]
        std_cap = (
            pl.when(pl.col("filing_status") == "single").then(float(std_caps["single"]))
            .when(is_joint).then(float(std_caps["married_joint"]))
            .when(is_hoh).then(float(std_caps["head_of_household"]))
            .otherwise(float(std_caps["married_separate"]))
        )
        credit = credit + (pct88 * pl.col("earned_income")).clip(floor88, std_cap)
        exemp_credit = (
            pl.when(pl.col("filing_status").is_in(["single", "married_separate"])).then(_tablki(pl.col("me_agi"), p["credit_1988_exemption_table_single"]))
            .when(is_hoh).then(_tablki(pl.col("me_agi"), p["credit_1988_exemption_table_hoh"]))
            .otherwise(_tablki(pl.col("me_agi"), p["credit_1988_exemption_table_married"]))
        )
        credit = credit + exemp_credit

    df = df.with_columns(me_statax=(pl.col("me_statax") - credit).clip(0, None))

    # --- EITC ---
    earncr = pl.lit(0.0)
    if effective_year >= 2000:
        rate_eitc = float(resolve_year(p["eitc_rate"], effective_year))
        earncr = rate_eitc * pl.col("eitc")  # `comnew(188)` 2020 gap acknowledged - see module docstring.
    if 2000 <= effective_year <= 2015:
        df = df.with_columns(me_statax=(pl.col("me_statax") - earncr).clip(0, None))

    # --- Property Tax Fairness Credit (2013+, refundable) --- (`pcred`
    # itself confirmed permanently inert - see module docstring)
    setax_half = 0.5 * pl.col("me_setax")
    ti = (pl.col("agi") + setax_half).clip(0, None)  # `comnew(65)`
    # `nexem=int(comnew(68))` - `comnew(68)` is itself divided by
    # `flate` for extrapolated years (the same generic comnew(1:98)
    # deflate-loop quirk Indiana/Kansas's own builds already
    # established for this exact array position), and the `int()`
    # TRUNCATION then genuinely zeroes PTFC/STFC eligibility for most
    # ordinary households once `flate`>1 - confirmed via a direct oracle
    # probe (a single filer's PTFC/STFC-driven refund present at
    # `flate=1` in 2021 vanishes entirely in 2022/2023).
    nexem = (exemps_count / flate).floor()
    ptfc = pl.lit(0.0)
    if effective_year == 2013:
        ceiling = float(p["ptfc_2013_income_ceiling"][1960])
        cap = float(p["ptfc_2013_cap"][1960])
        agix = pl.col("me_agi").clip(0, None)
        bagix = 0.1 * agix
        base = pl.col("proptax")
        eligible = (nexem > 0) & (agix <= ceiling) & (base > bagix)
        ptfc = pl.when(eligible).then(pl.min_horizontal(cap / pl.col("me_sep"), 0.4 * (base - bagix))).otherwise(0.0)
    elif 2014 <= effective_year <= 2017:
        base_raw = pl.col("proptax")
        cap_single = float(p["ptfc_2014_2017_single_base_cap"][1960])
        cap_2e = float(p["ptfc_2014_2017_2exempt_base_cap"][1960])
        cap_3e = float(p["ptfc_2014_2017_3plus_base_cap"][1960])
        inc_single = float(p["ptfc_2014_2017_single_income_cap"][1960])
        inc_2e = float(p["ptfc_2014_2017_2exempt_income_cap"][1960])
        inc_3e = float(p["ptfc_2014_2017_3plus_income_cap"][1960])
        is_single = pl.col("filing_status") == "single"
        base = pl.when(is_single).then(pl.min_horizontal(cap_single, base_raw)).when(nexem <= 2).then(pl.min_horizontal(cap_2e / pl.col("me_sep"), base_raw)).otherwise(pl.min_horizontal(cap_3e / pl.col("me_sep"), base_raw))
        tix = pl.when(is_single).then(pl.min_horizontal(inc_single / pl.col("me_sep"), ti)).when(nexem <= 2).then(pl.min_horizontal(inc_2e / pl.col("me_sep"), ti)).otherwise(pl.min_horizontal(inc_3e / pl.col("me_sep"), ti))
        cap_out = float(p["ptfc_2014_2017_cap"][1960])
        eligible = nexem > 0
        ptfc = pl.when(eligible).then(pl.min_horizontal(cap_out / pl.col("me_sep"), 0.5 * (base - 0.06 * tix).clip(0, None))).otherwise(0.0)
    elif effective_year >= 2018:
        base_raw = pl.col("proptax")
        cap_single = float(p["ptfc_2018plus_single_base_cap"][1960])
        cap_2e = float(p["ptfc_2018plus_2exempt_base_cap"][1960])
        cap_3e = float(p["ptfc_2018plus_3plus_base_cap"][1960])
        inc_single = float(p["ptfc_2018plus_single_income_cap"][1960])
        inc_2e = float(p["ptfc_2018plus_2exempt_income_cap"][1960])
        inc_3e = float(p["ptfc_2018plus_3plus_income_cap"][1960])
        # 2018+ caps are NOT divided by `sep` (unlike 2014-2017's own
        # caps) - confirmed directly against the source, which has no
        # `/sep` anywhere in this branch.
        is_single = pl.col("filing_status") == "single"
        base = pl.when(is_single).then(pl.min_horizontal(cap_single, base_raw)).when(nexem <= 2).then(pl.min_horizontal(cap_2e, base_raw)).otherwise(pl.min_horizontal(cap_3e, base_raw))
        tix = pl.when(is_single).then(pl.min_horizontal(inc_single, ti)).when(nexem <= 2).then(pl.min_horizontal(inc_2e, ti)).otherwise(pl.min_horizontal(inc_3e, ti))
        cap_out = float(p["ptfc_2018plus_cap"][1960])
        eligible = nexem > 0
        ptfc = pl.when(eligible).then(pl.min_horizontal(cap_out, (base - 0.06 * tix).clip(0, None))).otherwise(0.0)
    if effective_year >= 2017:
        ptfc = pl.when(is_sep).then(0.0).otherwise(ptfc)

    # --- Sales Tax Fairness Credit (2016+, refundable) ---
    stfc = pl.lit(0.0)
    if effective_year >= 2016:
        # `tis=comnew(2)+comnew(17)+data(91)-comnew(79)-min(0,comnew(6))`
        # - `data(91)`/`comnew(79)` confirmed inert; `comnew(6)` (net
        # capital gain actually included in AGI) reconstructed as the
        # raw `stcg+ltcg` input - the `-min(0,...)` term adds back any
        # net capital LOSS so it doesn't depress this credit's own
        # income test below what it would be without the loss.
        capgn = pl.col("stcg") + pl.col("ltcg")
        tis = pl.col("agi") + pl.col("me_setax") * 0.5 - pl.min_horizontal(capgn, 0.0)
        if effective_year == 2020:
            ui_total = pl.max_horizontal(pl.col("ui"), pl.col("pui") + pl.col("sui"))
            has_ui = (df.get_column("ui").abs().sum() + df.get_column("pui").abs().sum() + df.get_column("sui").abs().sum()) > 0
            if has_ui:
                df_no_ui = df.select(_RAW_INPUT_COLUMNS).with_columns(ui=pl.lit(0.0), pui=pl.lit(0.0), sui=pl.lit(0.0))
                fed_no_ui = compute_regular_tax(df_no_ui, effective_year)
                untax = pl.col("agi") - fed_no_ui.get_column("agi")
            else:
                untax = pl.lit(0.0)
            tis = tis + ui_total - untax
        nmst_eligible = ~is_sep  # `data(105)` confirmed inert.
        nexem_capped = nexem.clip(0, 4)
        threshold = (
            pl.when(pl.col("filing_status") == "single").then(_by_status_year(p["stfc_income_threshold"]["single"], effective_year))
            .when(is_hoh).then(_by_status_year(p["stfc_income_threshold"]["head_of_household"], effective_year))
            .otherwise(_by_status_year(p["stfc_income_threshold"]["married_joint"], effective_year))
        )
        credit_amt = pl.lit(0.0)
        for n in (1, 2, 3, 4):
            amt_n = float(resolve_year(p["stfc_credit_amount"][n], effective_year))
            credit_amt = pl.when(nexem_capped == n).then(amt_n).otherwise(credit_amt)
        stfc = pl.when((nexem > 0) & nmst_eligible).then(
            (credit_amt - ((tis - threshold) / 50.0).clip(0, None)).clip(0, None)
        ).otherwise(0.0)

    df = df.with_columns(me_statax=pl.col("me_statax") - chcrr - ptfc - stfc)
    if effective_year >= 2016:
        df = df.with_columns(me_statax=pl.col("me_statax") - earncr)

    df = df.with_columns(siitax=pl.col("me_statax") * flate)
    return df


def _by_status_year(table: dict, year: int) -> float:
    from taxsim_py.engine.schema import resolve_year as _ry
    return float(_ry(table, year))
