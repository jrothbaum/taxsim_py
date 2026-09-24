"""New York personal income tax calculator."""

import polars as pl

from taxsim_py.calculators.federal_pre1987 import PRE1987_PARAMS
from taxsim_py.engine.brackets import bracket_rate, bracket_tax
from taxsim_py.engine.eitc import federal_eitc
from taxsim_py.engine.schema import PARAMETERS_ROOT, load_yaml, resolve_year
from taxsim_py.engine.state import (
    with_defaults,
    itemize_choice,
    by_filing_status,
    checkpoint,
    household_income,
    interpolate_table,
    tier_values,
    unemployment_total,
    with_default,
)
from taxsim_py.engine.state_extrapolation import deflate_for_extrapolation, resolve_state_year

NY_PARAMS = load_yaml(PARAMETERS_ROOT / "states" / "ny" / "income_tax.yaml")
STATE_ADJUSTMENT_PARAMS = load_yaml(PARAMETERS_ROOT / "national" / "state_adjustments.yaml")
CAPITAL_GAINS_PARAMS = load_yaml(PARAMETERS_ROOT / "national" / "capital_gains.yaml")
FEDERAL_EITC_PARAMS = pl.read_csv(PARAMETERS_ROOT / "national" / "eitc.csv")
FEDERAL_EITC_MISC = load_yaml(PARAMETERS_ROOT / "national" / "eitc_misc.yaml")
_STATUSES = ["single", "married_joint", "married_separate", "head_of_household"]
# Rate-schedule class by filing status.
_CLASS = {"single": "single", "married_separate": "single", "head_of_household": "head_of_household",
          "married_joint": "married"}


def _p(name: str, year: int) -> float:
    return float(resolve_year(NY_PARAMS[name], year))


def _adj(name: str, year: int) -> float:
    return float(resolve_year(STATE_ADJUSTMENT_PARAMS[name], year))


def _scaled(brackets: list[list[float]], factor: float) -> list[list[float]]:
    return [[float(start) * factor, float(rate)] for start, rate in brackets]


def _by_class(values: dict) -> pl.Expr:
    return by_filing_status({status: values[cls] for status, cls in _CLASS.items()})


def _schedule(status: pl.Expr, taxinc: pl.Expr, y: int) -> tuple[pl.Expr, pl.Expr]:
    """Rate-schedule tax and marginal rate."""
    if y <= 1985:
        brackets = resolve_year(NY_PARAMS["brackets_1977_1985"], y)
        return bracket_tax(taxinc, brackets), bracket_rate(taxinc, brackets)
    if y == 1986:
        brackets = NY_PARAMS["brackets_1986"]
        return bracket_tax(taxinc, brackets), bracket_rate(taxinc, brackets)
    factor = _p("bracket_inflation", y) if 2012 <= y <= 2017 else 1.0
    tax = pl.lit(0.0)
    rate = pl.lit(0.0)
    for status_name, cls in _CLASS.items():
        brackets = _scaled(resolve_year(NY_PARAMS["brackets"][cls], y), factor)
        tax = pl.when(status == status_name).then(bracket_tax(taxinc, brackets)).otherwise(tax)
        rate = pl.when(status == status_name).then(bracket_rate(taxinc, brackets)).otherwise(rate)
    return tax, rate


def _blend(statax: pl.Expr, target: pl.Expr, ratio: pl.Expr, addition: float = 0.0) -> pl.Expr:
    """Worksheet step: tax moves toward `target` by `ratio`, keeping `addition`."""
    return statax + ratio * (target - statax - addition) + addition


def _recapture(statax: pl.Expr, taxinc: pl.Expr, agi: pl.Expr, rt: pl.Expr, status: pl.Expr, y: int) -> pl.Expr:
    """High-income recapture of the benefit of lower brackets."""
    p = NY_PARAMS
    start = float(p["recapture_start"])
    span = float(p["recapture_range"])
    is_single_class = status.is_in(["single", "married_separate"])
    is_hoh = status == "head_of_household"
    is_joint = status == "married_joint"
    if 1989 <= y <= 2002 or 2006 <= y <= 2008:
        ratio = pl.min_horizontal(pl.lit(span), agi - start) / span
        return (
            pl.when((agi > start) & (agi <= start + span))
            .then(statax + (taxinc * rt - statax) * ratio)
            .when(agi > start + span)
            .then(taxinc * rt)
            .otherwise(statax)
        )
    if 2003 <= y <= 2005:
        c = p["recapture_2003"]
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
    if 2009 <= y <= 2011:
        c = p["recapture_2009"]
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
    if y >= 2021:
        return _recapture_real_2021(statax, taxinc, agi, status)
    if y >= 2012:
        a = _p("bracket_inflation", y)
        r = [float(v) for v in resolve_year(p["recapture_2012_rates"], y)]
        add = [float(v) for v in resolve_year(p["recapture_2012_additions"], y)]
        b = p["recapture_2012_bounds"]
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
        ws3 = pl.when(agi >= t2 + phase).then(taxinc * r[2]).otherwise(
            _blend(statax, taxinc * r[2], ratio_from(float(b["married_tier3_phase_start_unscaled"])), add[1])
        )
        r4 = ratio_from(float(b["married_tier3_phase_start_unscaled"]))
        ws4_partial = (
            pl.when(taxinc <= t1).then(_blend(statax, taxinc * r[3], r4, add[0]))
            .when((taxinc > t1) & (taxinc < t2)).then(_blend(statax, taxinc * r[3], r4, add[1]))
            .when(taxinc > t2).then(_blend(statax, taxinc * r[3], r4, add[2]))
            .otherwise(statax)
        )
        ws4 = pl.when(agi >= b["married_top_full_agi"] * a + phase).then(taxinc * r[3]).otherwise(ws4_partial)
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
        ws6 = pl.when(agi >= b["single_tier2_full_agi_unscaled"]).then(taxinc * r[2]).otherwise(
            _blend(statax, taxinc * r[2], ratio_from(base), add[3])
        )
        ws7_partial = pl.when(taxinc <= st).then(_blend(statax, taxinc * r[3], ratio_from(base), add[3])).otherwise(
            _blend(statax, taxinc * r[3], ratio_from(base), add[4])
        )
        ws7 = pl.when(agi >= base + b["single_top_full_offset"]).then(taxinc * r[3]).otherwise(ws7_partial)
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
        ws9 = pl.when(agi >= ht + phase).then(taxinc * r[2]).otherwise(_blend(statax, taxinc * r[2], ratio_from(base), add[5]))
        ws10_partial = pl.when(taxinc <= b["hoh_top_tier_taxinc_unscaled"]).then(
            _blend(statax, taxinc * r[3], ratio_from(base), add[5])
        ).otherwise(_blend(statax, taxinc * r[3], ratio_from(base), add[6]))
        ws10 = pl.when(agi >= htop + phase).then(taxinc * r[3]).otherwise(ws10_partial)
        hoh = (
            pl.when((agi > base) & (agi <= htop) & (taxinc <= ht)).then(ws8)
            .when((agi > ht) & (agi <= htop) & (taxinc > ht)).then(ws9)
            .when(agi > htop).then(ws10)
            .otherwise(statax)
        )
        return pl.when(is_joint).then(joint).when(is_single_class).then(single).otherwise(hoh)
    return statax


def _recapture_real_2021(statax: pl.Expr, taxinc: pl.Expr, agi: pl.Expr, status: pl.Expr) -> pl.Expr:
    """2021 real-law tax computation worksheets (flat rate by taxable-income tier)."""
    c = NY_PARAMS["recapture_real_2021"]
    phase = float(c["phase"])
    decimals = int(c["ratio_decimals"])

    def ratio(start: float) -> pl.Expr:
        return ((agi - start) / phase).round(decimals)

    def worksheet(rate: pl.Expr | float, addition: pl.Expr | float, start: pl.Expr | float) -> pl.Expr:
        flat = taxinc * rate
        partial = statax + addition + ratio(start) * (flat - statax - addition)
        return pl.when(agi >= start + phase).then(flat).otherwise(partial)

    def pick(values: dict[str, pl.Expr]) -> pl.Expr:
        expr = values["married"]
        for cls in ("single", "head_of_household"):
            statuses = [s for s, k in _CLASS.items() if k == cls]
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


def compute_ny_tax(df: pl.DataFrame, year: int) -> pl.DataFrame:
    """Calculate New York income tax for each row."""
    effective_year, flate = resolve_state_year(year)
    y = effective_year
    p = NY_PARAMS
    df = with_defaults(df, (
        "dividends", "intrec", "psemp", "ssemp", "stcg", "ltcg", "ui", "pui", "sui", "proptax", "otheritem",
        "mortgage", "depx", "dep17", "dep18", "childcare", "charity_cash", "state_sales_or_income_tax_ded",
        "itemized_deduction", "standard_deduction", "taxable_unemployment", "earned_income", "eitc", "regular_tax",
        "amt", "ccc", "ccc_uncapped", "odc", "actc", "pre1987_capgn", "pre1987_pref",
    ))
    df = with_default(df, "itemizes", False)
    dividend_adjustment = _adj("household_income_dividend_adjustment", y)
    df = df.with_columns(
        ny_hy=household_income(dividend_adjustment, _adj("household_income_record_adjustment", y)),
        ny_ui=unemployment_total(),
        ny_dividends=pl.col("dividends") + dividend_adjustment,
        # Federal tax before credits (`comnew(137)`) is not deflated when a
        # later year is projected.
        ny_taxbca=pl.col("regular_tax") + pl.col("amt"),
    )
    df = deflate_for_extrapolation(
        df, flate,
        [
            "pwages", "swages", "ny_dividends", "intrec", "psemp", "ssemp", "stcg", "ltcg", "ny_ui", "proptax",
            "otheritem", "mortgage", "childcare", "charity_cash", "state_sales_or_income_tax_ded", "agi",
            "itemized_deduction", "standard_deduction", "taxable_unemployment", "earned_income", "eitc",
            "regular_tax", "amt", "ccc", "odc", "actc", "pre1987_capgn", "pre1987_pref",
        ],
    )

    status = pl.col("filing_status")
    is_single = status == "single"
    is_joint = status == "married_joint"
    is_sep = status == "married_separate"
    is_hoh = status == "head_of_household"
    sep = pl.when(is_sep).then(2.0).otherwise(1.0)
    txp = pl.when(is_joint).then(2.0).otherwise(1.0)
    depx = pl.col("depx")
    exemps = txp + depx
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
    if y == 2020:
        fed_agi = fed_agi - pl.when(fed_itemizes).then(0.0).otherwise(
            pl.min_horizontal(p["charity_nonitemizer_addback_2020"] / sep, pl.col("charity_cash"))
        )

    # --- New York AGI ---
    agi = fed_agi
    if y == 2020:
        agi = agi + pl.col("ny_ui") - pl.col("taxable_unemployment")
    if y == 2020:
        agi = agi + pl.when(fed_itemizes | (pl.col("charity_cash") <= 0)).then(0.0).otherwise(
            pl.min_horizontal(p["charity_nonitemizer_addback_2020"] / sep, pl.col("charity_cash"))
        )
    if y == 2021:
        agi = agi + pl.when(fed_itemizes | (pl.col("charity_cash") <= 0)).then(0.0).otherwise(
            pl.min_horizontal(p["charity_nonitemizer_addback_2021_per_taxpayer"] * txp, pl.col("charity_cash"))
        )
    agieic = agi
    agi = agi + _p("capital_gains_addback", y) * fullcg.clip(0, None)
    # Intermediate results are stored as columns so later formulas refer to
    # them instead of repeating their full expressions.
    df = df.with_columns(ny_agi=agi, ny_agieic=agieic, ny_fed_agi=fed_agi, ny_capgn=capgn)
    agi, agieic, fed_agi, capgn = (pl.col(c) for c in ("ny_agi", "ny_agieic", "ny_fed_agi", "ny_capgn"))

    # --- Standard deduction ---
    stds = float(resolve_year(p["standard_deduction"]["single"], y))
    stdm = float(resolve_year(p["standard_deduction"]["married"], y))
    stdh = float(resolve_year(p["standard_deduction"]["head_of_household"], y))
    if y <= 1984:
        share = _p("standard_deduction_share", y)
        cap = _p("standard_deduction_cap", y)
        stded = pl.when(is_single).then((share * agi).clip(stds, cap)).otherwise((share * agi).clip(stdm, cap) / sep)
    else:
        stded = pl.when(is_single | is_sep).then(pl.lit(stds)).when(is_hoh).then(pl.lit(stdh)).otherwise(pl.lit(stdm))

    # --- Itemized deductions ---
    agix = agi.clip(0, None)
    if y <= 1990:
        xitded = pl.when(fed_itemizes & (deducp > 0)).then((fed_deduc - salt_ded).clip(0, None)).otherwise(0.0)
    elif y <= 2017:
        xitded = pl.when(fed_itemizes & (deducp > 0)).then(
            ((deducp - salt_ded) * fed_deduc / deducp).clip(0, None)
        ).otherwise(0.0)
    else:
        sttax = pl.min_horizontal(_p("salt_cap", y) / sep, pl.col("proptax") + salt_ded + pl.col("otheritem"))
        xitded = deducp + pl.col("proptax") + pl.col("otheritem") - sttax
        limits = p["itemized_limit_threshold"]
        phase = (
            pl.when(is_single).then(float(resolve_year(limits["single"], y)))
            .when(is_hoh).then(float(resolve_year(limits["head_of_household"], y)))
            .otherwise(float(resolve_year(limits["married"], y)) / sep)
        )
        dedphs = pl.min_horizontal(
            _p("itemized_limit_max_share", y) * xitded, _p("itemized_limit_rate", y) * (fed_agi - phase)
        )
        xitded = xitded - pl.when(fed_agi > phase).then(dedphs).otherwise(0.0)
    if 1988 <= y <= 1990:
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
        xitded = (xitded - xitded * xover * _p("itemized_reduction_share", y)).clip(0, None)
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
    xitded = (xitded - adjust).clip(0, None)
    df = df.with_columns(ny_deduc=pl.max_horizontal(stded, xitded))
    deduc = pl.col("ny_deduc")

    # --- Exemptions and taxable income ---
    xmp = _p("exemption_per_person", y)
    exemp = (exemps if y <= 1987 else depx) * xmp
    taxinc = (agi - deduc - exemp).clip(0, None)
    taxy = taxinc

    famded = pl.lit(0.0)
    if y in (1985, 1986):
        fam = p["family_adjustment"][y]
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
    df = df.with_columns(ny_taxinc=taxinc, ny_taxy=taxy, ny_famded=famded)
    taxinc, taxy, famded = pl.col("ny_taxinc"), pl.col("ny_taxy"), pl.col("ny_famded")

    # --- Tax ---
    statax, rt = _schedule(status, taxinc, y)
    df = df.with_columns(ny_table_tax=statax, ny_rt=rt)
    df = df.with_columns(
        ny_recaptured=_recapture(pl.col("ny_table_tax"), taxinc, agi, pl.col("ny_rt"), status, y)
    )
    statax = pl.col("ny_recaptured")
    statax = statax + pl.when(famded > 0).then(bracket_tax(famded, p["family_adjustment_rates"])).otherwise(0.0)
    if 1978 <= y <= 1986:
        business = pl.col("psemp") + pl.col("ssemp") + pl.col("pwages").clip(None, 0) + pl.col("swages").clip(None, 0)
        psinc = (pl.col("pwages").clip(0, None) + pl.col("swages").clip(0, None) + business).clip(0, None)
        percen = pl.when(agi > 0).then(pl.min_horizontal(pl.lit(1.0), psinc / agi)).otherwise(0.0)
        pstinc = (percen * taxy - pl.col("pre1987_pref").clip(0, None)).clip(0, None)
        statax = statax - bracket_tax(pstinc, resolve_year(p["maximum_tax_savings"], y))

    # --- Filing thresholds ---
    if y <= 1985:
        no_tax = (agi <= p["no_tax_per_taxpayer_through_1985"] * txp * sep) | (agi <= exemp)
    elif y == 1986:
        limits = p["no_tax_1986"]
        no_tax = (pl.when(is_single).then(agi <= limits["single"]).otherwise(agi <= limits["other"])) | (agi <= exemp)
    elif y == 1987:
        no_tax = agi <= by_filing_status(p["no_tax_1987"])
    else:
        no_tax = agi <= p["no_tax_1988plus"]
    df = df.with_columns(ny_taxbc=pl.when(no_tax).then(0.0).otherwise(statax))
    statax = taxbc = pl.col("ny_taxbc")

    # --- Credits ---
    hcred = pl.lit(0.0)
    if 1978 <= y <= 1985:
        extra = float(p["household_credit_1982_addition"]) if y >= 1982 else 0.0
        hcred = (interpolate_table(fed_agi, p["household_credit_1978_1985"]) + extra) / sep
    elif y >= 1986:
        # The federal exemption count is deflated with other federal values
        # when a later year is projected.
        extra_people = exemps / flate - 1.0
        hcred = pl.when(is_single).then(interpolate_table(fed_agi, p["household_credit_single"])).otherwise(
            (interpolate_table(fed_agi, p["household_credit_married"])
             + extra_people * interpolate_table(fed_agi, p["household_credit_per_additional_person"])) / sep
        )
    df = df.with_columns(ny_hcred=hcred)
    hcred = pl.col("ny_hcred")
    df = df.with_columns(ny_after_household=(statax - hcred).clip(0, None))
    statax = pl.col("ny_after_household")

    chcrbc = pl.col("ccc_uncapped") if y >= 1987 else pl.lit(0.0)
    if y == 1977:
        statax = statax - p["child_care_share_through_1995"] * chcrbc
    elif y <= 1995:
        statax = (statax - pl.min_horizontal(statax, p["child_care_share_through_1995"] * chcrbc)).clip(0, None)
    else:
        if y == 1996:
            share = interpolate_table(agi, p["child_care_share_1996"])
        elif y <= 1999:
            c = p["child_care_share"][y]
            share = (
                pl.when(agi <= c["full_limit"]).then(pl.lit(float(c["full_share"])))
                .when(agi < c["taper_end"]).then(c["taper_share"] - c["taper_slope"] * (agi - c["taper_start"]))
                .otherwise(pl.lit(float(c["floor_share"])))
            )
        else:
            share = pl.lit(0.0)
            for start, value, slope in p["child_care_share_2000"]["pieces"]:
                share = pl.when(agi >= start).then(value - slope * (agi - start)).otherwise(share)
        statax = statax - chcrbc * share
    df = df.with_columns(ny_after_child_care=statax)
    statax = pl.col("ny_after_child_care")

    # Real property tax credit for homeowners under 65.
    hy = pl.col("ny_hy")
    ptax = _p("property_tax_share", y) * pl.col("proptax")
    pcred = pl.lit(0.0)
    if 1978 <= y <= 1980:
        c = p["property_credit_1978_1980"]
        pmax = interpolate_table(hy, c["maximum"])
        pcred = pl.when(hy <= c["income_limit"]).then(
            (ptax - hy * interpolate_table(hy, c["threshold"])).clip(0, None).clip(None, pmax)
        ).otherwise(0.0)
    elif y == 1981 or 1982 <= y <= 1984:
        c = p["property_credit_1981"] if y == 1981 else p["property_credit_1982_1984"]
        pcred = pl.when(hy <= c["income_limit"]).then(
            (ptax - hy * interpolate_table(hy, c["threshold"])).clip(0, c["maximum"])
        ).otherwise(0.0)
    elif y >= 1985:
        c = p["property_credit_1985plus"]
        pmax = interpolate_table(hy, c["maximum"])
        base = (ptax - hy * interpolate_table(hy, c["threshold"])).clip(0, None)
        base = pl.when(pl.col("proptax") > 0).then(pl.min_horizontal(c["homeowner_share"] * base, pmax)).otherwise(base)
        pcred = pl.when(hy <= c["income_limit"]).then(base).otherwise(0.0)
    df, (pcred,) = checkpoint(df, ny_pcred=pcred)

    # Earned income credit.
    earncr = pl.lit(0.0)
    if 1994 <= y <= 1995:
        earncr = _p("eitc_match_rate", y) * pl.col("eitc")
    elif 1996 <= y <= 2019:
        earncr = (_p("eitc_match_rate", y) * pl.col("eitc") - pl.min_horizontal(hcred, taxbc)).clip(0, None)
    elif y >= 2020:
        nkids = pl.col("dep18").clip(0, 3)
        disqy = capgn.clip(0, None) + pl.col("ny_dividends") + pl.col("intrec")

        def eitc_for(law: int, agi_value: pl.Expr) -> pl.Expr:
            params = FEDERAL_EITC_PARAMS.filter(pl.col("year") == law)
            dylim = float(resolve_year(FEDERAL_EITC_MISC["dylim"], law))
            return federal_eitc(pl.col("earned_income"), agi_value, disqy, status, nkids, params, dylim)

        df, (eitc,) = checkpoint(df, ny_eitc=eitc_for(y, agieic))
        if y == 2021:
            childless_single = is_single & (pl.col("dep17") < 1)
            eitc = pl.when(childless_single).then(
                p["eitc_2021_childless_credit_factor"]
                * eitc_for(2020, agieic * p["eitc_2021_childless_agi_factor"])
            ).otherwise(eitc)
        earncr = (_p("eitc_match_rate", y) * eitc - pl.min_horizontal(hcred, taxbc)).clip(0, None)

    # Empire State child credit.
    eschcr = pl.lit(0.0)
    e = p["empire_child_credit"]
    cphase = pl.when(is_single | is_hoh).then(float(e["income_limit"]["single"])).otherwise(
        float(e["income_limit"]["married"]) / sep
    )
    if 2006 <= y <= 2017:
        eschcr = pl.when(fed_agi <= cphase).then(
            pl.max_horizontal(e["minimum_per_child"] * pl.col("dep17"), e["federal_share"] * (pl.col("actc") + pl.col("odc")))
        ).otherwise(0.0)
    elif y >= 2018:
        fagi = pl.col("agi")
        if y == 2020:
            fagi = fagi + pl.col("ny_ui") - pl.col("taxable_unemployment")
        df, (precrd,) = checkpoint(
            df,
            ny_precrd=(e["per_child"] * pl.col("dep18") - e["phaseout_rate"] * (fagi - cphase).clip(0, None)).clip(0, None),
        )
        taxbca = pl.col("ny_taxbca")
        df, (ctcred,) = checkpoint(df, ny_ctcred=pl.min_horizontal(precrd, (taxbca - pl.col("ccc")).clip(0, None)))
        earned = pl.col("earned_income")
        fift = e["refundable_rate"] * (earned - e["earned_income_floor"]).clip(0, None)
        remaining = precrd - ctcred
        ssmtax = e["payroll_rate"] * pl.min_horizontal(pl.lit(_p("payroll_wage_base", y)), earned.clip(0, None))
        chcr1 = pl.min_horizontal(remaining, fift)
        df, (chcr1,) = checkpoint(df, ny_chcr1=pl.when((depx > e["many_children"] - 1) & (fift < remaining)).then(
            pl.min_horizontal(remaining, pl.max_horizontal(fift, (ssmtax - pl.col("eitc")).clip(0, None)))
        ).otherwise(chcr1))
        base = chcr1 + ctcred
        per_child = e["federal_share"] * base / depx
        credit = pl.when(fed_agi > cphase).then(
            pl.col("dep17") * pl.max_horizontal(pl.lit(float(e["minimum_per_child"])), per_child)
        ).otherwise(pl.col("dep17") * per_child)
        eschcr = pl.when((depx > 0) & (precrd > 0)).then(credit).otherwise(0.0)

    df, (statax,) = checkpoint(df, ny_before_relief=statax - earncr - eschcr - pcred)
    f = p["family_tax_relief"]
    relief = _p_nested(f["amount"], y)
    if relief > 0:
        statax = statax - pl.when(
            (statax >= 0) & (agi >= f["income_min"]) & (agi <= f["income_max"]) & (pl.col("dep17") > 0)
        ).then(relief).otherwise(0.0)

    # Minimum income tax on preference items (none are inputs), less the
    # specific deduction and the tax after credits.
    df, (statax,) = checkpoint(df, ny_after_credits=statax)
    preference_base = (-(float(p["minimum_tax_deduction"]) / sep) - statax).clip(0, None)
    statax = statax + _p("minimum_tax_rate", y) * preference_base

    return df.with_columns(siitax=statax * flate)


def _p_nested(values: dict, year: int) -> float:
    return float(resolve_year(values, year))
