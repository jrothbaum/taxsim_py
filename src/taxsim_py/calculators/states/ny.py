"""New York personal income tax calculator."""


from __future__ import annotations
import polars as pl

from taxsim_py.calculators.federal_pre1987 import PRE1987_PARAMS
from taxsim_py.engine.brackets import bracket_rate, bracket_tax, scale_brackets
from taxsim_py.calculators.federal import FEDERAL_CREDITS_PARAMS
from taxsim_py.engine.credits import child_care_credit_rate
from taxsim_py.engine.eitc import eitc_age_eligible, eitc_filer_eligible, federal_eitc
from taxsim_py.engine.inputs import aged_count, federal_exemption_count, files_head_of_household, files_joint, files_separate, files_single, is_dependent_filer, taxpayer_count
from taxsim_py.engine.schema import PARAMETERS_ROOT, YearParams, load_yaml, resolve_year
from taxsim_py.engine.state import (
    phaseout_steps,
    dividend_input_adjustment,
    itemize_choice,
    by_filing_status,
    checkpoint,
    household_income,
    interpolate_table,
    net_earnings_by_spouse,
    tier_values,
    unemployment_total,
    with_state_detail,
)
from taxsim_py.behavior import BehaviorProfile, TAXSIM_BEHAVIOR
from taxsim_py.engine.state_extrapolation import deflate_for_extrapolation, resolve_state_year

NY_PARAMS = load_yaml(PARAMETERS_ROOT / "states" / "ny" / "income_tax.yaml")
CAPITAL_GAINS_PARAMS = load_yaml(PARAMETERS_ROOT / "national" / "capital_gains.yaml")
FEDERAL_EITC_PARAMS = pl.read_csv(PARAMETERS_ROOT / "national" / "eitc.csv")
FEDERAL_EITC_MISC = load_yaml(PARAMETERS_ROOT / "national" / "eitc_misc.yaml")
_STATUSES = ["single", "married_joint", "married_separate", "head_of_household"]
# Rate-schedule class by filing status.
_CLASS = {"single": "single", "married_separate": "single", "head_of_household": "head_of_household",
          "married_joint": "married"}


def _by_class(values: dict) -> pl.Expr:
    return by_filing_status({status: values[cls] for status, cls in _CLASS.items()})


def _schedule(status: pl.Expr, taxinc: pl.Expr, p: YearParams) -> tuple[pl.Expr, pl.Expr]:
    """Rate-schedule tax and marginal rate."""
    tables = p.value("brackets")
    if isinstance(tables, list):
        return bracket_tax(taxinc, tables), bracket_rate(taxinc, tables)
    factor = p.num("bracket_inflation")
    tax = pl.lit(0.0)
    rate = pl.lit(0.0)
    for status_name, cls in _CLASS.items():
        brackets = scale_brackets(tables[cls], factor)
        tax = pl.when(status == status_name).then(bracket_tax(taxinc, brackets)).otherwise(tax)
        rate = pl.when(status == status_name).then(bracket_rate(taxinc, brackets)).otherwise(rate)
    return tax, rate


def _blend(statax: pl.Expr, target: pl.Expr, ratio: pl.Expr, addition: float = 0.0) -> pl.Expr:
    """Worksheet step: tax moves toward `target` by `ratio`, keeping `addition`."""
    return statax + ratio * (target - statax - addition) + addition


def _supplemental(statax: pl.Expr, taxinc: pl.Expr, agi: pl.Expr, status: pl.Expr, p: YearParams) -> pl.Expr:
    """Tax plus the supplemental tax that recaptures the benefit of lower brackets."""
    d = p.value("supplemental_tax")
    supplemental = pl.lit(0.0)
    for status_name, key in _CLASS.items():
        brackets = p.value("brackets", key)
        starts = [float(b[0]) for b in brackets]
        previous = pl.lit(pl.Series(starts, dtype=pl.Float64)).gather(
            (pl.lit(pl.Series(starts, dtype=pl.Float64)).search_sorted(taxinc, side="right").cast(pl.Int64) - 1).clip(0, None)
        )
        applicable = (agi - pl.max_horizontal(previous, pl.lit(float(d["min_agi"])))).clip(0, None)
        fraction = (applicable / float(d["phase_in_length"])).clip(None, 1.0)
        base = bracket_rate(taxinc, d[key]["base"])
        incremental = bracket_rate(taxinc, d[key]["incremental"])
        supplemental = pl.when(status == status_name).then(base + fraction * incremental).otherwise(supplemental)
    high = taxinc * float(d["top_rate"]) - statax
    return statax + pl.when(agi > float(d["top_agi"])).then(high).otherwise(supplemental)


def _taxsim_new_york_tax(taxinc: pl.Expr, agi: pl.Expr, status: pl.Expr, p: YearParams) -> pl.Expr:
    """The TAXSIM build's 2022+ tax: bracket tax plus a phased-in recapture (`nyminus`)."""
    t = p.value("taxsim_new_york_tax")
    result = pl.lit(0.0)
    for status_name, key in _CLASS.items():
        c = t[key]
        uppers = [float(b[0]) for b in c["brackets"]]
        rate_pct, subtract = tier_values(taxinc, uppers, [float(b[1]) for b in c["brackets"]], [float(b[2]) for b in c["brackets"]], strict=True)
        flat = taxinc * rate_pct / 100.0
        progressive = flat - subtract
        rows = c["worksheets"]
        sheet_uppers = [float(r[0]) for r in rows]
        added, own_benefit = tier_values(taxinc, sheet_uppers, [float(r[1]) for r in rows], [float(r[2]) for r in rows], strict=True)
        (previous,) = tier_values(taxinc, sheet_uppers, [107650.0] + sheet_uppers[:-1], strict=True)
        benefit = pl.when(own_benefit == 0).then(flat - progressive).otherwise(own_benefit)
        share = pl.min_horizontal(pl.lit(50000.0), (agi - previous).clip(0, None)) / 50000.0
        tax = pl.when(agi > 25.0e6).then(flat).otherwise(progressive + added + benefit * share)
        result = pl.when(status == status_name).then(tax).otherwise(result)
    return result


def _recapture(
    statax: pl.Expr,
    taxinc: pl.Expr,
    agi: pl.Expr,
    rt: pl.Expr,
    status: pl.Expr,
    p: YearParams,
    behavior: BehaviorProfile = TAXSIM_BEHAVIOR,
) -> pl.Expr:
    """High-income recapture of the benefit of lower brackets."""
    is_single_class = status.is_in(["single", "married_separate"])
    is_hoh = status == "head_of_household"
    is_joint = status == "married_joint"
    if p.in_effect("taxsim_new_york_tax") and not behavior.compute_new_york_supplemental_tax_per_statute:
        return _taxsim_new_york_tax(taxinc, agi, status, p)
    if p.in_effect("supplemental_tax"):
        return _supplemental(statax, taxinc, agi, status, p)
    if p.in_effect("recapture_tiers"):
        return _recapture_tiers(statax, taxinc, agi, status, p, behavior)
    if p.in_effect("recapture_bounds"):
        return _recapture_worksheets(statax, taxinc, agi, status, p, behavior)
    if not p.in_effect("recapture_start"):
        return statax
    start = p.num("recapture_start")
    span = p.num("recapture_range")
    if p.in_effect("recapture_top_bracket"):
        c = p.value("recapture_top_bracket")
        top = _by_class(c["bracket_top"])
        benefit = _by_class(c["bracket_benefit"])
        ratio = (agi - start) / span
        within = (is_single_class & (taxinc <= top)) | (is_hoh & (taxinc <= top)) | is_joint
        above = ~is_joint & (taxinc > top)
        low = statax + pl.when(within).then((taxinc * c["flat_rate"] - statax) * ratio).otherwise(0.0)
        low = low + pl.when(above).then(benefit * ratio).otherwise(0.0)
        high_base = pl.when(taxinc <= top).then(taxinc * c["flat_rate"]).otherwise(statax)
        ratio2 = pl.min_horizontal(pl.lit(span), agi - (start + span)) / span
        high = pl.when(taxinc > top).then(
            benefit + high_base + (taxinc * c["top_rate"] - high_base - benefit) * ratio2
        ).otherwise(high_base)
        return (
            pl.when((agi > start) & (agi <= start + span))
            .then(low)
            .when((agi > start + span) & (agi <= c["top_agi"]))
            .then(high)
            .when(agi > c["top_agi"])
            .then(taxinc * c["top_flat_rate"])
            .otherwise(statax)
        )
    if p.in_effect("recapture_millionaire"):
        c = p.value("recapture_millionaire")
        top = _by_class(c["bracket_top"])
        benefit = _by_class(c["benefit"])
        upper = _by_class(c["upper_benefit"])
        second = float(c["second_start"])
        middle_agi = float(c["middle_agi"])
        ratio1 = pl.min_horizontal(pl.lit(span), agi - start) / span
        ratio2 = pl.min_horizontal(pl.lit(span), agi - second) / span
        ratio3 = pl.min_horizontal(pl.lit(span), agi - middle_agi) / span
        # Above the bracket top, single and head-of-household filers phase
        # in from the bracket top rather than the middle AGI bound.
        ratio3_upper = pl.when(is_joint).then(ratio3).otherwise(
            pl.min_horizontal(pl.lit(span), agi - top) / span
        )
        middle = pl.when(taxinc <= top).then(taxinc * c["flat_rate"]).otherwise(
            _blend(statax, taxinc * c["middle_rate"], ratio2, benefit)
        )
        upper_band = pl.when(taxinc <= top).then(
            _blend(statax, taxinc * c["top_rate"], ratio3, benefit)
        ).otherwise(_blend(statax, taxinc * c["top_rate"], ratio3_upper, upper))
        return (
            pl.when((agi > start) & (agi <= start + span))
            .then(statax + ratio1 * (taxinc * c["flat_rate"] - statax))
            .when((agi > start + span) & (agi <= middle_agi))
            .then(middle)
            .when((agi > middle_agi) & (agi <= c["top_agi"]))
            .then(upper_band)
            .when(agi > c["top_agi"])
            .then(taxinc * c["top_rate"])
            .otherwise(statax)
        )
    ratio = pl.min_horizontal(pl.lit(span), agi - start) / span
    return (
        pl.when((agi > start) & (agi <= start + span))
        .then(statax + (taxinc * rt - statax) * ratio)
        .when(agi > start + span)
        .then(taxinc * rt)
        .otherwise(statax)
    )


def _recapture_worksheets(
    statax: pl.Expr,
    taxinc: pl.Expr,
    agi: pl.Expr,
    status: pl.Expr,
    p: YearParams,
    behavior: BehaviorProfile = TAXSIM_BEHAVIOR,
) -> pl.Expr:
    """Tax computation worksheets: flat rates by tier with lower-bracket benefits added back.

    Each worksheet phases in over `phase` from its own AGI bound; TAXSIM
    uses unscaled or other worksheets' bounds in several of them.
    """
    own = behavior.use_own_bounds_in_new_york_worksheets
    is_single_class = status.is_in(["single", "married_separate"])
    is_joint = status == "married_joint"
    a = p.num("recapture_inflation")
    r = [float(v) for v in p.value("recapture_rates")]
    add = [float(v) for v in p.value("recapture_additions")]
    b = p.value("recapture_bounds")
    base = b["base_agi"] * a
    phase = float(b["phase"])

    def ratio_from(start_agi: float | pl.Expr) -> pl.Expr:
        return pl.min_horizontal(pl.lit(phase), agi - start_agi) / phase

    # Married filing jointly: worksheets 1-4.
    t1 = b["married_tier1_taxinc"] * a
    t2 = b["married_tier2_taxinc"] * a
    top = b["married_top_agi"] * a
    ws1 = pl.when(agi >= base + phase).then(taxinc * r[0]).otherwise(statax + ratio_from(base) * (taxinc * r[0] - statax))
    ws2 = pl.when(agi >= t1 + phase).then(taxinc * r[1]).otherwise(_blend(statax, taxinc * r[1], ratio_from(t1), add[0]))
    unscaled = float(b["married_tier3_phase_start_unscaled"])
    ws3 = pl.when(agi >= t2 + phase).then(taxinc * r[2]).otherwise(
        _blend(statax, taxinc * r[2], ratio_from(t2 if own else unscaled), add[1])
    )
    r4 = ratio_from(top if own else unscaled)
    ws4_partial = (
        pl.when(taxinc <= t1).then(_blend(statax, taxinc * r[3], r4, add[0]))
        .when((taxinc > t1) & (taxinc < t2)).then(_blend(statax, taxinc * r[3], r4, add[1]))
        .when(taxinc > t2).then(_blend(statax, taxinc * r[3], r4, add[2]))
        .otherwise(statax)
    )
    ws4_full = top + phase if own else b["married_top_full_agi"] * a + phase
    ws4 = pl.when(agi >= ws4_full).then(taxinc * r[3]).otherwise(ws4_partial)
    joint = (
        pl.when((agi > base) & (agi <= top) & (taxinc <= t1)).then(ws1)
        .when((agi > t1) & (agi <= top) & (taxinc > t1) & (taxinc <= t2)).then(ws2)
        .when((agi > t2) & (agi <= top) & (taxinc > t2)).then(ws3)
        .when(agi > top).then(ws4)
        .otherwise(statax)
    )
    # Single and married separate: worksheets 5-7.
    st = b["single_tier_taxinc"] * a
    stop = b["single_top_agi"] * a
    ws5 = pl.when(agi >= base + phase).then(taxinc * r[1]).otherwise(statax + ratio_from(base) * (taxinc * r[1] - statax))
    ws6_full = st + phase if own else b["single_tier2_full_agi_unscaled"]
    ws6 = pl.when(agi >= ws6_full).then(taxinc * r[2]).otherwise(
        _blend(statax, taxinc * r[2], ratio_from(st if own else base), add[3])
    )
    r7 = ratio_from(stop if own else base)
    ws7_partial = pl.when(taxinc <= st).then(_blend(statax, taxinc * r[3], r7, add[3])).otherwise(
        _blend(statax, taxinc * r[3], r7, add[4])
    )
    ws7_full = stop + phase if own else base + b["single_top_full_offset"]
    ws7 = pl.when(agi >= ws7_full).then(taxinc * r[3]).otherwise(ws7_partial)
    single = (
        pl.when((agi > base) & (agi <= stop) & (taxinc <= st)).then(ws5)
        .when((agi > st) & (agi <= stop) & (taxinc > st)).then(ws6)
        .when(agi > stop).then(ws7)
        .otherwise(statax)
    )
    # Head of household: worksheets 8-10.
    ht = b["hoh_tier_taxinc"] * a
    htop = b["hoh_top_agi"] * a
    ws8 = pl.when(agi >= base + phase).then(taxinc * r[1]).otherwise(statax + ratio_from(base) * (taxinc * r[1] - statax))
    ws9 = pl.when(agi >= ht + phase).then(taxinc * r[2]).otherwise(
        _blend(statax, taxinc * r[2], ratio_from(ht if own else base), add[5])
    )
    r10 = ratio_from(htop if own else base)
    ws10_tier = ht if own else b["hoh_top_tier_taxinc_unscaled"]
    ws10_partial = pl.when(taxinc <= ws10_tier).then(_blend(statax, taxinc * r[3], r10, add[5])).otherwise(
        _blend(statax, taxinc * r[3], r10, add[6])
    )
    ws10 = pl.when(agi >= htop + phase).then(taxinc * r[3]).otherwise(ws10_partial)
    hoh = (
        pl.when((agi > base) & (agi <= htop) & (taxinc <= ht)).then(ws8)
        .when((agi > ht) & (agi <= htop) & (taxinc > ht)).then(ws9)
        .when(agi > htop).then(ws10)
        .otherwise(statax)
    )
    return pl.when(is_joint).then(joint).when(is_single_class).then(single).otherwise(hoh)


def _recapture_tiers(
    statax: pl.Expr, taxinc: pl.Expr, agi: pl.Expr, status: pl.Expr, p: YearParams, behavior: BehaviorProfile = TAXSIM_BEHAVIOR
) -> pl.Expr:
    """Tax computation worksheets with a flat rate by taxable-income tier."""
    c = p.value("recapture_tiers")
    phase = float(c["phase"])
    decimals = int(c["ratio_decimals"])

    def ratio(start: float) -> pl.Expr:
        share = (agi - start) / phase
        # TAXSIM leaves the worksheet ratio unrounded.
        return share.round(decimals) if behavior.round_new_york_recapture_ratio_per_statute else share

    def worksheet(rate: pl.Expr | float, addition: pl.Expr | float, start: pl.Expr | float) -> pl.Expr:
        flat = taxinc * rate
        partial = statax + addition + ratio(start) * (flat - statax - addition)
        return pl.when(agi >= start + phase).then(flat).otherwise(partial)

    classes = dict(_CLASS)
    if p.in_effect("taxsim_swap_worksheet_classes") and not behavior.pick_new_york_worksheet_by_filing_status:
        # TAXSIM's 2021 code tests its own filing index (single 1, head of household 2, joint 3) against
        # the codes 2 (joint) and 4 (head of household): joint returns use the single worksheets and
        # head of household returns the joint ones.
        classes["married_joint"] = "single"
        classes["head_of_household"] = "married"

    def pick(values: dict[str, pl.Expr]) -> pl.Expr:
        expr = values["married"]
        for cls in ("single", "head_of_household"):
            statuses = [s for s, k in classes.items() if k == cls]
            expr = pl.when(status.is_in(statuses)).then(values[cls]).otherwise(expr)
        return expr

    rate, addition, start, top_addition = ({}, {}, {}, {})
    for cls in ("single", "head_of_household", "married"):
        tiers = c[cls]["tiers"]
        rate[cls], addition[cls], start[cls] = tier_values(
            taxinc, [t[0] for t in tiers], [t[1] for t in tiers], [t[2] for t in tiers], [t[3] for t in tiers]
        )
        tops = c[cls]["top_additions"]
        (top_addition[cls],) = tier_values(taxinc, [t[0] for t in tops], [t[1] for t in tops])
    tiered = worksheet(pick(rate), pick(addition), pick(start))
    top = worksheet(float(c["top_rate"]), pick(top_addition), float(c["top_agi"]))
    return pl.when(agi <= c["base_agi"]).then(statax).when(agi > c["top_agi"]).then(top).otherwise(tiered)


def compute_ny_tax(df: pl.DataFrame, year: int, behavior: BehaviorProfile = TAXSIM_BEHAVIOR) -> pl.DataFrame:
    """Calculate New York income tax for each row."""
    effective_year, flate = resolve_state_year(year, behavior, NY_PARAMS)
    y = effective_year
    p = YearParams(NY_PARAMS, effective_year)
    dividend_adjustment = dividend_input_adjustment()
    df = df.with_columns(
        ny_household_income_undeflated=household_income(behavior),
        ny_ui=unemployment_total(),
        ny_dividends=pl.col("dividends") + dividend_adjustment,
        # Federal tax before credits (`comnew(137)`) is not deflated when a
        # later year is projected.
        ny_taxbca=pl.col("regular_tax") + pl.col("amt"),
    )
    df = deflate_for_extrapolation(df, flate, extra=("ny_dividends", "ny_ui"))

    status = pl.col("filing_status")
    is_single = files_single()
    is_joint = files_joint()
    is_sep = files_separate()
    is_hoh = files_head_of_household()
    sep = pl.when(is_sep).then(2.0).otherwise(1.0)
    txp = taxpayer_count()
    aged = aged_count()
    dependent_filer = is_dependent_filer()
    depx = pl.col("depx")
    exemps = federal_exemption_count(y)
    salt_ded = pl.col("state_sales_or_income_tax_ded")
    fullcg = pl.col("stcg") + pl.col("ltcg")

    # --- Federal values ---
    if y <= 1986:
        deducp = pl.col("proptax") + pl.col("otheritem") + pl.col("mortgage") + salt_ded
        fed_zbr = by_filing_status({s: resolve_year(PRE1987_PARAMS["standard_deduction"][s], y) for s in _STATUSES})
        fed_itemizes = deducp > fed_zbr if y <= 1981 else itemize_choice(deducp > fed_zbr)
        fed_deduc = pl.when(fed_itemizes).then(deducp).otherwise(0.0)
        capgn = pl.col("pre1987_capgn")
    else:
        fed_itemizes = pl.col("itemizes")
        agix = pl.col("agi").clip(0, None)
        alim50 = pl.lit(1.0e20) if y in (2020, 2021) else 0.5 * agix
        fed_char = pl.when(pl.col("agi") < 0).then(0.0).otherwise(
            pl.min_horizontal(pl.col("charity_cash"), alim50)
        ).clip(0, None)
        if y <= 2017:
            deducp = (pl.col("proptax") + pl.col("otheritem") + salt_ded).clip(0, None) + pl.col("mortgage") + fed_char
        else:
            deducp = pl.col("itemized_deduction")
        fed_deduc = pl.when(fed_itemizes).then(pl.col("itemized_deduction")).otherwise(0.0)
        loss_limit = float(resolve_year(CAPITAL_GAINS_PARAMS["net_capital_loss_limit"], y))
        capgn = pl.max_horizontal(fullcg, -loss_limit / flate / sep)
    fed_agi = pl.col("agi")
    charity_addback = pl.lit(0.0)
    if p.in_effect("charity_nonitemizer_addback"):
        amount = p.num("charity_nonitemizer_addback")
        limit = amount / sep if y == 2020 else amount * txp
        charity_addback = pl.when(fed_itemizes | (pl.col("charity_cash") <= 0)).then(0.0).otherwise(
            pl.min_horizontal(limit, pl.col("charity_cash"))
        )
    if y == 2020:
        # The 2020 federal deduction is above the line.
        fed_agi = fed_agi - pl.when(fed_itemizes).then(0.0).otherwise(
            pl.min_horizontal(p.num("charity_nonitemizer_addback") / sep, pl.col("charity_cash"))
        )

    # --- New York AGI ---
    agi = fed_agi + charity_addback
    if y == 2020:
        agi = agi + pl.col("ny_ui") - pl.col("taxable_unemployment")
    agieic = agi
    # Pensions of taxpayers 59½ or older are exempt up to a cap each; Social
    # Security is exempt from 1984.
    pension_aged = (pl.col("page") >= 60).cast(pl.Float64) + (pl.col("sage") >= 60).cast(pl.Float64)
    agi = agi - pl.min_horizontal(pl.col("pensions"), float(p["pension_exclusion_per_aged_taxpayer"]) * pension_aged)
    agi = agi + p.num("capital_gains_addback") * fullcg.clip(0, None)
    if y >= 1984:
        ss_exclusion = pl.col("taxable_social_security")
        if y >= 2022 and not behavior.subtract_new_york_social_security_at_any_age:
            # TAXSIM zeroes the whole exclusion unless a spouse is 60 or older.
            ss_exclusion = pl.when(pension_aged > 0).then(ss_exclusion).otherwise(0.0)
        agi = agi - ss_exclusion
    # Intermediate results are stored as columns so later formulas refer to
    # them instead of repeating their full expressions.
    df = df.with_columns(ny_agi=agi, ny_agieic=agieic, ny_fed_agi=fed_agi, ny_capgn=capgn)
    agi, agieic, fed_agi, capgn = (pl.col(c) for c in ("ny_agi", "ny_agieic", "ny_fed_agi", "ny_capgn"))

    # --- Standard deduction ---
    stds = p.num("standard_deduction", "single")
    stdm = p.num("standard_deduction", "married")
    stdh = p.num("standard_deduction", "head_of_household")
    if y <= 1986 and behavior.use_single_standard_deduction_for_new_york_head_of_household_before_1987:
        # No head-of-household status before 1987; TAXSIM's table is 0.
        stdh = stds
    if p.in_effect("standard_deduction_share"):
        share = p.num("standard_deduction_share")
        cap = p.num("standard_deduction_cap")
        stded = pl.when(is_single).then((share * agi).clip(stds, cap)).otherwise((share * agi).clip(stdm, cap) / sep)
    else:
        stded = pl.when(is_single | is_sep).then(pl.lit(stds)).when(is_hoh).then(pl.lit(stdh)).otherwise(pl.lit(stdm))
    if p.in_effect("standard_deduction_dependent"):
        if y >= 2022 and not behavior.use_new_york_dependent_standard_deduction_alone:
            # TAXSIM adds the dependent amount to the regular standard deduction.
            stded = pl.when(dependent_filer).then(stded + p.num("standard_deduction_dependent")).otherwise(stded)
        else:
            stded = pl.when(dependent_filer).then(p.num("standard_deduction_dependent")).otherwise(stded)

    # --- Itemized deductions ---
    agix = agi.clip(0, None)
    if y <= 1990:
        xitded = pl.when(fed_itemizes & (deducp > 0)).then((fed_deduc - salt_ded).clip(0, None)).otherwise(0.0)
    elif y <= 2017:
        xitded = pl.when(fed_itemizes & (deducp > 0)).then(
            ((deducp - salt_ded) * fed_deduc / deducp).clip(0, None)
        ).otherwise(0.0)
    else:
        if p.in_effect("salt_cap") or (p.in_effect("taxsim_salt_cap") and not behavior.cap_new_york_state_tax_deduction_per_statute):
            # TAXSIM keeps the ten-thousand-dollar cap after the federal cap rises.
            cap_key = "salt_cap" if p.in_effect("salt_cap") else "taxsim_salt_cap"
            sttax = pl.min_horizontal(p.num(cap_key) / sep, pl.col("proptax") + salt_ded)
        else:
            sttax = pl.col("salt_capped") - pl.col("otheritem")
        xitded = deducp + pl.col("proptax") - sttax
        phase = (
            pl.when(is_single).then(p.num("itemized_limit_threshold", "single"))
            .when(is_hoh).then(p.num("itemized_limit_threshold", "head_of_household"))
            .when(is_sep & pl.lit(y >= 2022 and not behavior.halve_new_york_married_itemized_limit_for_separate_per_statute)).then(
                p.num("itemized_limit_threshold", "single") / sep
            )
            .otherwise(p.num("itemized_limit_threshold", "married") / sep)
        )
        dedphs = pl.min_horizontal(
            p.num("itemized_limit_max_share") * xitded, p.num("itemized_limit_rate") * (fed_agi - phase)
        )
        xitded = xitded - pl.when(fed_agi > phase).then(dedphs).otherwise(0.0)
    if p.in_effect("itemized_reduction_share"):
        rng = float(p["itemized_reduction_range"])
        upper_start = float(p["itemized_reduction_upper_start"])
        upper_end = float(p["itemized_reduction_upper_end"])
        threshold = by_filing_status(p["itemized_reduction_threshold"])
        xover = (
            pl.when((agi > p["itemized_reduction_start"]) & (agi <= upper_start))
            .then((agi - threshold).clip(0, rng) / rng)
            .when((agi > upper_start) & (agi <= upper_end))
            .then((agi - upper_start) / rng)
            .when(agi > upper_end)
            .then(pl.lit(float(p["itemized_reduction_top_factor"])))
            .otherwise(0.0)
        )
        xitded = (xitded - xitded * xover * p.num("itemized_reduction_share")).clip(0, None)
    rng = float(p["itemized_reduction_range"])
    upper_start = float(p["itemized_reduction_upper_start"])
    upper_end = float(p["itemized_reduction_upper_end"])
    phasit = by_filing_status(p["itemized_adjustment_threshold"])
    rate = float(p["itemized_adjustment_rate"])
    adjust = (
        pl.when((agi > p["itemized_reduction_start"]) & (agi <= upper_start))
        .then(rate * xitded * (pl.min_horizontal(pl.lit(rng), agi - phasit) / rng).clip(0, None))
        .when((agi > upper_start) & (agi <= upper_end))
        .then(rate * xitded * (agi - upper_start) / rng)
        .when(agi > upper_end)
        .then(float(p["itemized_adjustment_top_rate"]) * xitded)
        .otherwise(0.0)
    )
    # Only the reduction branches floor the deduction at zero.
    xitded = pl.when(agi > p["itemized_reduction_start"]).then((xitded - adjust).clip(0, None)).otherwise(xitded)
    if behavior.floor_new_york_itemized_deductions:
        # TAXSIM floors them only inside the high-income reductions.
        xitded = xitded.clip(0, None)
    df = df.with_columns(ny_deduc=pl.max_horizontal(stded, xitded), ny_stded=stded, ny_xitded=xitded)
    deduc = pl.col("ny_deduc")

    # --- Exemptions and taxable income ---
    xmp = p.num("exemption_per_person")
    exemp = (exemps if y <= 1987 else depx) * xmp
    if y > 1987 and behavior.mode.value != "statutory":
        # TAXSIM takes the (deflated, in a projected year) federal exemption count less the taxpayers, so a
        # projected year gives a small negative exemption.
        exemp = (exemps / flate - taxpayer_count()) * xmp
    taxinc = (agi - deduc - exemp).clip(0, None)
    taxy = taxinc
    df = df.with_columns(ny_exemp=exemp)

    famded = pl.lit(0.0)
    if p.in_effect("family_adjustment"):
        fam = p.value("family_adjustment")
        taxy = pl.when(is_sep).then(2.0 * taxinc).otherwise(taxinc)
        low = (p["family_adjustment_low_share"] * taxy - interpolate_table(agi, fam["low_table"])).clip(0, None)
        high = interpolate_table(agi, fam["high_table"])
        famded = (
            pl.when(taxy <= fam["low_income_limit"]).then(low)
            .when(taxy <= fam["high_income_limit"]).then(high)
            .otherwise(0.0)
        )
        famded = pl.when(is_joint | is_sep).then(famded).otherwise(0.0)
        taxinc = (taxinc - famded).clip(0, None)
    # 1979: taxpayers 65 or older deduct part of their property tax or rent.
    reald = pl.lit(0.0)
    if p.in_effect("real_property_deduction"):
        r = p.value("real_property_deduction")
        rentpaid = pl.col("rentpaid")
        renttx = pl.when(rentpaid <= r["rent_cap"]).then(r["rent_share"] * rentpaid).otherwise(0.0)
        home = pl.when(pl.col("proptax") <= r["property_tax_cap"]).then(pl.col("proptax")).otherwise(0.0)
        ptax79 = pl.max_horizontal(renttx, home)
        hh79 = pl.col("ny_household_income_undeflated")
        for lower, upper, share, amount in r["tiers"]:
            reald = pl.when((hh79 >= lower) & (hh79 < upper) & (ptax79 > share * hh79)).then(float(amount)).otherwise(reald)
        reald = pl.when(aged > 0).then(reald).otherwise(0.0)
        taxinc = (taxinc - reald).clip(0, None)
    df = df.with_columns(ny_taxinc=taxinc, ny_taxy=taxy, ny_famded=famded, ny_reald=reald)
    taxinc, taxy, famded = pl.col("ny_taxinc"), pl.col("ny_taxy"), pl.col("ny_famded")

    # --- Tax ---
    statax, rt = _schedule(status, taxinc, p)
    df = df.with_columns(ny_table_tax=statax, ny_rt=rt)
    df = df.with_columns(
        ny_recaptured=_recapture(pl.col("ny_table_tax"), taxinc, agi, pl.col("ny_rt"), status, p, behavior)
    )
    statax = pl.col("ny_recaptured")
    statax = statax + pl.when(famded > 0).then(bracket_tax(famded, p["family_adjustment_rates"])).otherwise(0.0)
    if p.in_effect("maximum_tax_savings"):
        business = pl.col("psemp") + pl.col("ssemp") + pl.col("pwages").clip(None, 0) + pl.col("swages").clip(None, 0)
        psinc = (
            pl.col("pwages").clip(0, None) + pl.col("swages").clip(0, None) + business + pl.col("pensions")
            + float(p["maximum_tax_property_income_share"]) * pl.col("otherprop")
        ).clip(0, None)
        percen = pl.when(agi > 0).then(pl.min_horizontal(pl.lit(1.0), psinc / agi)).otherwise(0.0)
        pstinc = (percen * taxy - pl.col("pre1987_pref").clip(0, None)).clip(0, None)
        savings = p.value("maximum_tax_savings")
        if y == 1986 and not behavior.complete_new_york_1986_maximum_tax_table:
            # TAXSIM reads one row too few of the 1986 table.
            savings = savings[:-1]
        statax = statax - bracket_tax(pstinc, savings)
    if p.in_effect("unearned_income_tax") and behavior.charge_new_york_unearned_income_tax_1987_1988:
        # TAXSIM's year test can never be true.
        u = p.value("unearned_income_tax")
        unearned = (
            pl.col("dividends") + pl.col("intrec") + (pl.col("stcg") + pl.col("ltcg")).clip(0, None)
        ).clip(0, None)
        share = ((agi - float(u["phase_in_start"]) / sep) / float(u["phase_in_range"])).clip(0, 1)
        statax = statax + float(u["rate"]) * share * unearned

    # --- Filing thresholds ---
    limits = p.value("no_tax_threshold")
    if y <= 1985:
        no_tax = (agi <= limits["per_taxpayer"] * txp * sep) | (agi <= exemp)
    elif y == 1986:
        no_tax = (pl.when(is_single).then(agi <= limits["single"]).otherwise(agi <= limits["other"])) | (agi <= exemp)
    else:
        threshold = by_filing_status(limits) if isinstance(limits, dict) else pl.lit(float(limits))
        no_tax = agi <= pl.when(dependent_filer).then(p.num("no_tax_dependent")).otherwise(threshold)
    df = df.with_columns(ny_taxbc=pl.when(no_tax).then(0.0).otherwise(statax))
    statax = taxbc = pl.col("ny_taxbc")

    # --- Credits ---
    hcred = pl.lit(0.0)
    household = p.value("household_credit") if p.in_effect("household_credit") else None
    if isinstance(household, list):
        extra = p.num("household_credit_addition") if p.in_effect("household_credit_addition") else 0.0
        hcred = (interpolate_table(fed_agi, household) + extra) / sep
    elif household is not None:
        # The federal exemption count is deflated with other federal values
        # when a later year is projected.
        extra_people = exemps / flate - 1.0
        extra_credit = interpolate_table(fed_agi, household["per_additional_person"])
        married_credit = interpolate_table(fed_agi, household["married"])
        if p.in_effect("taxsim_household_credit_per_additional_person") and not behavior.interpolate_new_york_household_credit_per_person:
            steps = p.value("taxsim_household_credit_per_additional_person")
            extra_credit = pl.lit(0.0)
            for upper, amount in reversed(steps):
                extra_credit = pl.when(fed_agi <= upper).then(float(amount)).otherwise(extra_credit)
            # No credit at all above the last step.
            if y >= 2022:
                married_credit = pl.when(fed_agi > float(steps[-1][0])).then(0.0).otherwise(married_credit)
        hcred = pl.when(is_single).then(interpolate_table(fed_agi, household["single"])).otherwise(
            (married_credit + extra_people * extra_credit) / sep
        )
    if 1990 <= y <= 2016 and not behavior.use_new_york_household_credit_table_1990_2016:
        # TAXSIM's closed form: $15 for a single filer, $30 per exemption otherwise, less
        # a dollar for each $500 of federal AGI over $10,000 (single) or $15,000.
        single_credit = (15.0 - (fed_agi - 10_000.0).clip(0, None) / 500.0).clip(0, None)
        other_credit = (30.0 - (fed_agi - 15_000.0).clip(0, None) / 500.0).clip(0, None) * exemps / flate / sep
        hcred = pl.when(is_single).then(single_credit).otherwise(other_credit)
    hcred = pl.when(dependent_filer).then(0.0).otherwise(hcred)
    if y >= 2022 and not behavior.pay_new_york_household_credit_to_separate_returns:
        # TAXSIM's household credit branches skip a married-separate return, which gets none.
        hcred = pl.when(status == "married_separate").then(0.0).otherwise(hcred)
    df = df.with_columns(ny_hcred=hcred)
    hcred = pl.col("ny_hcred")
    df = df.with_columns(ny_after_household=(statax - hcred).clip(0, None))
    statax = pl.col("ny_after_household")

    if y >= 1987:
        chcrbc = pl.col("ccc_uncapped")
    elif behavior.read_federal_child_care_credit_before_1987:
        chcrbc = pl.col("federal_chcr")
    else:
        # TAXSIM reads a federal figure its early routine never fills.
        chcrbc = pl.lit(0.0)
    c = p.value("child_care_share")
    if 2000 <= y <= 2021 and not behavior.use_new_york_statutory_child_care_credit:
        federal_rate = pl.when(pl.col("ccc_expense") > 0).then(pl.col("ccc_uncapped") / pl.col("ccc_expense")).otherwise(0.0)
        if y <= 2005:
            # TAXSIM multiplies the federal credit by the federal rate a second time.
            if "pieces" in c:
                share = pl.lit(0.0)
                for start, value, slope in c["pieces"]:
                    share = pl.when(agi >= start).then(value - slope * (agi - start)).otherwise(share)
                chcr = chcrbc * share * federal_rate
            else:
                chcr = pl.lit(0.0)
        else:
            earn_primary, earn_spouse = net_earnings_by_spouse(behavior)
            excess = (agi - 25_000.0).clip(0, 40_000.0)
            frac = (1.1 - (2.25e-5 if y <= 2017 else 5e-6) * excess).clip(0, 0.9)
            if y >= 2018:
                frac = pl.when(agi > 150_000).then(0.2).when(agi >= 65_000).then(0.6).otherwise(frac)
            rate_used = federal_rate
            if y == 2021:
                rate_used = 0.01 * pl.max_horizontal(pl.lit(20.0), 35.0 - ((agi - 15_000.0) / 2000.0).clip(0, None))
            dependents = pl.col("dep13").clip(None, 2 if y <= 2017 else 6)
            maximum = pl.lit(0.0)
            for count, amount in enumerate((0, 3000, 6000, 75000, 8500, 9000, 9500)):
                maximum = pl.when(dependents == count).then(float(amount)).otherwise(maximum)
            expenses = pl.min_horizontal(earn_primary, pl.col("childcare"))
            expenses = pl.when(txp > 1).then(pl.min_horizontal(expenses, earn_spouse)).otherwise(expenses)
            chcr = pl.min_horizontal(maximum, expenses) * frac * rate_used
        statax = statax - chcr
    elif p.in_effect("taxsim_child_care_credit") and not behavior.use_new_york_statutory_child_care_credit:
        t = p.value("taxsim_child_care_credit")
        rows = t["table"]
        percentage = pl.lit(float(t["top_percentage"]))
        for index in range(len(rows) - 1, -1, -1):
            upper, high_percentage, low_percentage = (float(v) for v in rows[index])
            previous = float(rows[index - 1][0]) if index else None
            if previous is None:
                piece = pl.lit(high_percentage)
            else:
                piece = high_percentage - (agi - previous) * (high_percentage - low_percentage) / (upper - previous)
            percentage = pl.when(agi < upper).then(piece).otherwise(percentage)
        limits = t["expense_limits"]
        children = pl.col("dep13")
        limit = pl.lit(0.0)
        for count, amount in enumerate(limits, start=1):
            limit = pl.when(children >= count).then(float(amount)).otherwise(limit)
        earned = pl.when(is_joint).then(pl.min_horizontal(pl.col("pwages"), pl.col("swages"))).otherwise(pl.col("wages"))
        expense = pl.min_horizontal(limit, earned, pl.col("childcare")).clip(0, None)
        rate = child_care_credit_rate(pl.col("agi"), YearParams(FEDERAL_CREDITS_PARAMS["child_care_credit"], y).value("rate"), round_up=y >= 2024)
        chcr = rate * expense * percentage
        statax = statax - chcr
    elif not isinstance(c, dict):
        chcr = pl.min_horizontal(statax, float(c) * chcrbc)
        statax = (statax - chcr).clip(0, None)
    else:
        if "by_agi" in c:
            share = interpolate_table(agi, c["by_agi"])
        elif "pieces" not in c:
            share = (
                pl.when(agi <= c["full_limit"]).then(pl.lit(float(c["full_share"])))
                .when(agi < c["taper_end"]).then(c["taper_share"] - c["taper_slope"] * (agi - c["taper_start"]))
                .otherwise(pl.lit(float(c["floor_share"])))
            )
        else:
            share = pl.lit(0.0)
            for start, value, slope in c["pieces"]:
                share = pl.when(agi >= start).then(value - slope * (agi - start)).otherwise(share)
        chcr = chcrbc * share
        statax = statax - chcr
    df = df.with_columns(ny_after_child_care=statax, ny_chcr=chcr)
    statax = pl.col("ny_after_child_care")

    # Real property tax credit (refundable).
    hy = pl.col("ny_household_income_undeflated")
    rentpaid = pl.col("rentpaid")
    ptax = p.num("property_tax_share") * pl.col("proptax") + float(p["property_credit_rent_share"]) * rentpaid
    is_aged = aged > 0
    pcred = pl.lit(0.0)
    c = p.value("property_credit") if p.in_effect("property_credit") else {}
    if "homeowner_share" in c:
        pmax = pl.when(is_aged).then(interpolate_table(hy, c["aged_maximum"])).otherwise(interpolate_table(hy, c["maximum"]))
        base = (ptax - hy * interpolate_table(hy, c["threshold"])).clip(0, None)
        base = pl.when(pl.col("proptax") > 0).then(pl.min_horizontal(c["homeowner_share"] * base, pmax)).otherwise(base)
        base = pl.when(rentpaid > 0).then(pl.min_horizontal(c["renter_share"] * base, pmax)).otherwise(base)
        pcred = pl.when(hy <= c["income_limit"]).then(base).otherwise(0.0)
    elif "aged_maximum_step" in c:
        pmax = pl.when(is_aged).then(
            pl.when(hy <= c["aged_maximum_step"]).then(float(c["aged_maximum_low_income"])).otherwise(float(c["aged_maximum_high_income"]))
        ).otherwise(float(c["maximum"]))
        pcred = pl.when(hy <= c["income_limit"]).then(
            (ptax - hy * interpolate_table(hy, c["threshold"])).clip(0, None).clip(None, pmax)
        ).otherwise(0.0)
    elif c:
        maximum = c["maximum"]
        pmax_young = interpolate_table(hy, maximum) if isinstance(maximum, list) else pl.lit(float(maximum))
        pmax = pl.when(is_aged).then(interpolate_table(hy, c["aged_maximum"])).otherwise(pmax_young)
        share = pl.when(is_aged).then(interpolate_table(hy, c["aged_threshold"])).otherwise(interpolate_table(hy, c["threshold"]))
        pcred = pl.when(hy <= c["income_limit"]).then((ptax - hy * share).clip(0, None).clip(None, pmax)).otherwise(0.0)
        if p.in_effect("real_property_deduction"):
            pcred = pl.when(is_aged & (pl.col("ny_reald") > 0)).then(0.0).otherwise(pcred)
    if y >= 2025 and p.in_effect("taxsim_property_credit") and not behavior.compute_new_york_property_credit_per_statute:
        # TAXSIM's 2025 on credit: rent counts at a quarter, the rate and maximum come from federal AGI tables,
        # and it has no rent limit.
        t = p.value("taxsim_property_credit")
        fagi = pl.col("agi")
        rate = interpolate_table(fagi, t["rate"])
        maximum = pl.when(is_aged).then(interpolate_table(fagi, t["aged_maximum"])).otherwise(interpolate_table(fagi, t["maximum"]))
        credit = (pl.min_horizontal(maximum, pl.col("proptax") + float(t["rent_share"]) * rentpaid - rate * fagi)).clip(0, None)
        pcred = pl.when(fagi < float(t["income_limit"])).then(credit).otherwise(0.0)
    elif behavior.limit_new_york_property_credit_rent_per_statute:
        pcred = pl.when(float(p["property_credit_rent_share"]) * rentpaid > p["property_credit_rent_limit"]).then(0.0).otherwise(pcred)
    elif p.in_effect("taxsim_property_credit_monthly_rent_limit"):
        # TAXSIM's rent test applies from 2010 and reads the limit as monthly rent.
        pcred = pl.when(rentpaid / 12.0 > p.num("taxsim_property_credit_monthly_rent_limit")).then(0.0).otherwise(pcred)
    df, (pcred,) = checkpoint(df, ny_pcred=pcred)

    # Earned income credit.
    earncr = pl.lit(0.0)
    if 1994 <= y <= 1995:
        earncr = p.num("eitc_match_rate") * pl.col("eitc")
    elif 1996 <= y <= 2019:
        earncr = (p.num("eitc_match_rate") * pl.col("eitc") - pl.min_horizontal(hcred, taxbc)).clip(0, None)
    elif y >= 2020:
        nkids = pl.col("dep18").clip(0, 3)
        disqy = capgn.clip(0, None) + pl.col("ny_dividends") + pl.col("intrec") + pl.col("otherprop").clip(0, None)

        def eitc_for(law: int, agi_value: pl.Expr, eligibility_year: int | None = None) -> pl.Expr:
            params = FEDERAL_EITC_PARAMS.filter(pl.col("year") == law)
            dylim = float(resolve_year(FEDERAL_EITC_MISC["dylim"], law))
            credit_status = status
            if y == 2021 and not behavior.deny_new_york_2021_earned_income_credit_to_separate_returns:
                # TAXSIM's federal credit routine pays separate returns in 2021, on the single schedule.
                credit_status = pl.when(status == "married_separate").then(pl.lit("single")).otherwise(status)
            credit = federal_eitc(pl.col("earned_income"), agi_value, disqy, credit_status, nkids, params, dylim)
            childless = nkids == 0
            age_law = law if eligibility_year is None else eligibility_year
            eligible = eitc_filer_eligible(childless, age_law) & eitc_age_eligible(childless, age_law)
            return pl.when(eligible).then(credit).otherwise(0.0)

        if y >= 2024 and not behavior.compute_new_york_earned_income_credit_base_per_statute:
            # TAXSIM's 2024 on routine reads the federal credit as the federal pass computed it (deflated).
            df, (eitc,) = checkpoint(df, ny_eitc=pl.col("eitc"))
        else:
            df, (eitc,) = checkpoint(df, ny_eitc=eitc_for(y, agieic))
        if p.in_effect("eitc_childless_prior_schedule"):
            # The prior year's schedule, keeping the state's upper age
            # exclusion for taxpayers 65 or older.
            prior = p.value("eitc_childless_prior_schedule")
            aged_single_childless = is_single & (pl.col("dep17") < 1) & (aged >= taxpayer_count())
            childless_single = is_single & (pl.col("dep17") < 1) & (aged < taxpayer_count())
            eitc = pl.when(childless_single).then(
                prior["credit_factor"] * eitc_for(y - 1, agieic * prior["agi_factor"], eligibility_year=y)
            ).when(aged_single_childless).then(0.0).otherwise(eitc)
        earncr = (p.num("eitc_match_rate") * eitc - pl.min_horizontal(hcred, taxbc)).clip(0, None)

    # Empire State child credit.
    eschcr = pl.lit(0.0)
    e = p.value("empire_child_credit") if p.in_effect("empire_child_credit") else {}
    t = p.value("taxsim_empire_child_credit") if p.in_effect("taxsim_empire_child_credit") else None
    if t is not None and not behavior.pay_new_york_empire_child_credit_per_statute:
        e = {}
        # TAXSIM's `numlt(17)` returns the count of dependents under 19.
        dep17 = pl.col("dep18")
        base = pl.lit(0.0)
        gate = pl.lit(True)
        if "threshold" in t:
            thr = by_filing_status({k: float(v) for k, v in t["threshold"].items()})
            gate = fed_agi < thr
        if "per_child" in t:
            reduction = t["phaseout_rate"] * (1000.0 * phaseout_steps((fed_agi - thr).clip(0, None) / 1000.0, behavior))
            available = (t["per_child"] * dep17 - reduction).clip(0, None)
            base = pl.min_horizontal(available, t["refundable_rate"] * (pl.col("earned_income") - t["earned_income_floor"]))
        if t["share"]:
            eschcr = pl.when(gate).then(t["share"] * pl.max_horizontal(float(t["minimum_per_child"]) * dep17, base)).otherwise(0.0)
    if "income_limit" in e:
        cphase = pl.when(is_single | is_hoh).then(float(e["income_limit"]["single"])).otherwise(
            float(e["income_limit"]["married"]) / sep
        )
    if "under_4_amount" in e:
        # Flat amounts by child age, phased out on the total.
        young = pl.col("children_under_4").clip(None, pl.col("dep17"))
        base = float(e["under_4_amount"]) * young + float(e["amount"]) * (pl.col("dep17") - young)
        thr = by_filing_status({k: float(v) for k, v in e["threshold"].items()})
        reduction = float(e["phaseout_per_1000"]) * ((pl.col("agi") - thr).clip(0, None) / 1000.0).floor()
        eschcr = (base - reduction).clip(0, None)
    elif "income_limit" in e and "per_child" not in e:
        credit_children = pl.col("dep17")
        if not behavior.pay_new_york_empire_child_credit_per_statute:
            # TAXSIM counts dependents under 18 less those under 6 (`numlt(17) - numlt(5)`).
            credit_children = (pl.col("dep18") - pl.col("dep6")).clip(0, None)
        federal_credit = pl.col("actc") + pl.col("odc")
        if not behavior.pay_new_york_empire_child_credit_per_statute:
            # TAXSIM adds the refundable child credit (`comnew(93)`) and the child care credit
            # before its liability limit (`comnew(176)`), not the child tax credit.
            federal_credit = pl.col("actc") + pl.col("ccc_uncapped")
        eschcr = pl.when(fed_agi <= cphase).then(
            pl.max_horizontal(e["minimum_per_child"] * credit_children, e["federal_share"] * federal_credit)
        ).otherwise(0.0)
    elif "per_child" in e:
        fagi = pl.col("agi")
        dep17 = pl.col("dep17")
        dep18 = pl.col("dep18")
        kids = depx
        if not behavior.pay_new_york_empire_child_credit_per_statute and y <= 2021:
            # TAXSIM counts dependents under 18 less those under 6.
            numctc = (pl.col("dep18") - pl.col("dep6")).clip(0, None)
            dep17 = numctc
            dep18 = numctc
        if behavior.exclude_children_under_4_from_ny_empire_credit and p.value("empire_child_credit_excludes_under_4"):
            young = pl.col("children_under_4")
            dep17 = dep17 - young
            dep18 = dep18 - young
            kids = depx - young
        if y == 2020:
            fagi = fagi + pl.col("ny_ui") - pl.col("taxable_unemployment")
        excess = (fagi - cphase).clip(0, None)
        if not behavior.round_new_york_empire_child_credit_phaseout_per_statute:
            # TAXSIM adds $500 to the excess over the threshold (its stand-in for rounding up to $1,000).
            excess = pl.when(fagi > cphase).then(excess + 500.0).otherwise(0.0)
        df, (precrd,) = checkpoint(
            df,
            ny_precrd=(e["per_child"] * dep18 - e["phaseout_rate"] * excess).clip(0, None),
        )
        taxbca = pl.col("ny_taxbca")
        df, (ctcred,) = checkpoint(df, ny_ctcred=pl.min_horizontal(precrd, (taxbca - pl.col("ccc") - pl.col("federal_elder")).clip(0, None)))
        earned = pl.col("earned_income")
        fift = e["refundable_rate"] * (earned - e["earned_income_floor"]).clip(0, None)
        remaining = precrd - ctcred
        ssmtax = e["payroll_rate"] * pl.min_horizontal(pl.lit(p.num("payroll_wage_base")), earned.clip(0, None))
        chcr1 = pl.min_horizontal(remaining, fift)
        df, (chcr1,) = checkpoint(df, ny_chcr1=pl.when((depx > e["many_children"] - 1) & (fift < remaining)).then(
            pl.min_horizontal(remaining, pl.max_horizontal(fift, (ssmtax - pl.col("eitc")).clip(0, None)))
        ).otherwise(chcr1))
        base = chcr1 + ctcred
        per_child = e["federal_share"] * base / kids.clip(1, None)
        # The $100-per-child minimum applies at every income in the dated
        # statutory years; TAXSIM applies it only above the phase-out start.
        floor_applies = (fed_agi > cphase) | (y >= 2022)
        if not behavior.pay_new_york_empire_child_credit_per_statute and y <= 2021:
            # The 2026 build pays the minimum only up to the phase-out start.
            floor_applies = fagi <= cphase
        credit = pl.when(floor_applies).then(
            dep17 * pl.max_horizontal(pl.lit(float(e["minimum_per_child"])), per_child)
        ).otherwise(dep17 * per_child)
        eschcr = pl.when((kids > 0) & (precrd > 0) & (dep17 > 0)).then(credit).otherwise(0.0)

    df, (statax,) = checkpoint(df, ny_before_relief=statax - earncr - eschcr - pcred)
    famref = pl.lit(0.0)
    if p.in_effect("family_tax_relief"):
        f = p.value("family_tax_relief")
        relief = float(f["amount"])
        famref = pl.when(
            (statax >= 0) & (agi >= f["income_min"]) & (agi <= f["income_max"]) & (pl.col("dep17") > 0)
        ).then(relief).otherwise(0.0)
        statax = statax - famref

    # Minimum income tax on preference items (none are inputs), less the
    # specific deduction and the tax after credits.
    df, (statax,) = checkpoint(df, ny_after_credits=statax)
    if not behavior.charge_new_york_minimum_tax_on_preferences_only:
        # TAXSIM computes it after refundable credits, so a tax below the
        # negative specific deduction creates a minimum tax.
        preference_base = (-(float(p["minimum_tax_deduction"]) / sep) - statax).clip(0, None)
        statax = statax + p.num("minimum_tax_rate") * preference_base

    df = df.with_columns(siitax=statax * flate)
    return with_state_detail(
        df,
        agi=agi,
        exemptions=pl.col("ny_exemp"),
        standard_deduction=pl.col("ny_stded"),
        itemized_deductions=pl.col("ny_xitded"),
        taxable_income=taxinc,
        property_credit=pcred,
        child_care_credit=pl.col("ny_chcr"),
        eic=earncr,
        credits=hcred + pcred + earncr + pl.col("ny_chcr") + eschcr + famref,
        rate=pl.col("ny_rt"),
    )
