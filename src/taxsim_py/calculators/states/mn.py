"""Minnesota individual income tax calculator."""

import polars as pl

from taxsim_py.calculators.payroll import payroll_parts
from taxsim_py.calculators.federal_pre1987 import PRE1987_PARAMS
from taxsim_py.engine.brackets import bracket_rate, bracket_tax
from taxsim_py.engine.inputs import aged_count, federal_exemption_count, is_dependent_filer, taxpayer_count
from taxsim_py.engine.schema import PARAMETERS_ROOT, load_yaml, resolve_year
from taxsim_py.engine.state import (
    with_defaults,
    itemize_choice,
    by_filing_status,
    checkpoint,
    household_income,
    interpolate_table,
    unemployment_total,
    with_default,
    with_state_detail,
)
from taxsim_py.engine.state_extrapolation import deflate_for_extrapolation, resolve_state_year

MN_PARAMS = load_yaml(PARAMETERS_ROOT / "states" / "mn" / "income_tax.yaml")
STATE_ADJUSTMENT_PARAMS = load_yaml(PARAMETERS_ROOT / "national" / "state_adjustments.yaml")
CAPITAL_GAINS_PARAMS = load_yaml(PARAMETERS_ROOT / "national" / "capital_gains.yaml")
FEDERAL_INCOME_TAX_PARAMS = load_yaml(PARAMETERS_ROOT / "national" / "income_tax.yaml")
_STATUSES = ["single", "married_joint", "married_separate", "head_of_household"]


def _p(name: str, year: int) -> float:
    return float(resolve_year(MN_PARAMS[name], year))


def _sub(section: dict, name: str, year: int) -> float:
    return float(resolve_year(section[name], year))


def _adj(name: str, year: int) -> float:
    return float(resolve_year(STATE_ADJUSTMENT_PARAMS[name], year))



def _scaled(brackets: list[list[float]], factor: float) -> list[list[float]]:
    return [[float(start) * factor, float(rate)] for start, rate in brackets]


def _by_filer(single: float, married: float, head_of_household: float) -> pl.Expr:
    """Select a value by Minnesota filer class; married separate uses the married value."""
    return by_filing_status(
        {
            "single": single,
            "married_joint": married,
            "married_separate": married,
            "head_of_household": head_of_household,
        }
    )


def _steps(income: pl.Expr, steps: list[list[float]], otherwise: pl.Expr) -> pl.Expr:
    """Amount from the first `[upper, amount]` step whose upper bound exceeds income."""
    expression = otherwise
    for upper, amount in reversed(steps):
        expression = pl.when(income < upper).then(pl.lit(float(amount))).otherwise(expression)
    return expression


def _pieces(income: pl.Expr, pieces: list[list[float]]) -> pl.Expr:
    """Piecewise-linear schedule of `[start, value at start, slope]` pieces."""
    expression = pl.lit(0.0)
    for start, value, slope in pieces:
        expression = (
            pl.when(income >= start)
            .then(float(value) + float(slope) * (income - start))
            .otherwise(expression)
        )
    return expression


def _schedule_tax(taxinc: pl.Expr, fedtax: pl.Expr, status: pl.Expr, y: int) -> pl.Expr:
    """Minnesota rate-schedule tax (`mnrate`) for a filer class."""
    p = MN_PARAMS
    is_joint = status == "married_joint"
    is_sep = status == "married_separate"
    is_hoh = status == "head_of_household"
    sep = pl.when(is_sep).then(2.0).otherwise(1.0)

    def by_class(tables: dict, income: pl.Expr, factor: float) -> pl.Expr:
        return (
            pl.when(is_joint)
            .then(bracket_tax(income, _scaled(tables["joint"], factor)))
            .when(is_hoh)
            .then(bracket_tax(income, _scaled(tables["head_of_household"], factor)))
            .otherwise(bracket_tax(income, _scaled(tables["single"], factor)))
        )

    def split_joint(tables: dict, factor: float) -> pl.Expr:
        joint = bracket_tax(taxinc * sep, _scaled(tables["joint"], factor)) / sep
        return (
            pl.when(status == "single")
            .then(bracket_tax(taxinc, _scaled(tables["single"], factor)))
            .when(is_hoh)
            .then(bracket_tax(taxinc, _scaled(tables["head_of_household"], factor)))
            .otherwise(joint)
        )

    if y <= 1984:
        rows = [list(row) for row in p["brackets_1977_1984_base"]]
        top = resolve_year(p["brackets_1977_1984_top_rates"], y)
        rows[-2][1], rows[-1][1] = top
        tax = bracket_tax(taxinc, _scaled(rows, _p("brackets_1977_1998_inflation", y)))
    elif y <= 1986:
        aif = _p("brackets_1977_1998_inflation", y)
        without_deduction = taxinc
        with_deduction = (taxinc - fedtax).clip(0, None)
        tax_a = (
            pl.when(is_joint)
            .then(bracket_tax(without_deduction, _scaled(p["brackets_1985_1986_joint_a"], aif)))
            .otherwise(bracket_tax(without_deduction, _scaled(p["brackets_1985_1986_single_a"], aif)))
        )
        tax_b = (
            pl.when(is_joint)
            .then(bracket_tax(with_deduction, _scaled(p["brackets_1985_1986_joint_b"], aif)))
            .otherwise(bracket_tax(with_deduction, _scaled(p["brackets_1985_1986_single_b"], aif)))
        )
        tax = pl.min_horizontal(tax_a, tax_b)
    elif y == 1987:
        tax = by_class(p["brackets_1987"], taxinc, 1.0)
    elif y <= 1990:
        tax = by_class(p["brackets_1988_1990"], taxinc, 1.0)
    elif y <= 1998:
        tax = by_class(p["brackets_1991_1998"], taxinc, _p("brackets_1977_1998_inflation", y))
    elif y == 1999:
        tax = split_joint(p["brackets_1999"], 1.0)
    elif y <= 2012:
        tax = split_joint(p["brackets_2000_2012"], _p("brackets_2000_2012_inflation", y))
    elif y <= 2018:
        tax = split_joint(p["brackets_2013_2018"], _p("brackets_2013plus_inflation", y))
    else:
        tax = split_joint(p["brackets_2019plus"], _p("brackets_2013plus_inflation", y))
    if y <= 1983:
        tax = tax * _p("surtax", y)
    return tax


def _schedule_rate(taxinc: pl.Expr, fedtax: pl.Expr, status: pl.Expr, y: int) -> pl.Expr:
    """Rate left by the final lookup in Minnesota's schedule routine."""
    p = MN_PARAMS
    is_joint = status == "married_joint"
    is_sep = status == "married_separate"
    is_hoh = status == "head_of_household"
    sep = pl.when(is_sep).then(2.0).otherwise(1.0)

    def by_class(tables: dict, income: pl.Expr, factor: float) -> pl.Expr:
        return (
            pl.when(is_joint).then(bracket_rate(income, _scaled(tables["joint"], factor)))
            .when(is_hoh).then(bracket_rate(income, _scaled(tables["head_of_household"], factor)))
            .otherwise(bracket_rate(income, _scaled(tables["single"], factor)))
        )

    def split_joint(tables: dict, factor: float) -> pl.Expr:
        return (
            pl.when(status == "single").then(bracket_rate(taxinc, _scaled(tables["single"], factor)))
            .when(is_hoh).then(bracket_rate(taxinc, _scaled(tables["head_of_household"], factor)))
            .otherwise(bracket_rate(taxinc * sep, _scaled(tables["joint"], factor)))
        )

    if y <= 1984:
        rows = [list(row) for row in p["brackets_1977_1984_base"]]
        rows[-2][1], rows[-1][1] = resolve_year(p["brackets_1977_1984_top_rates"], y)
        return bracket_rate(taxinc, _scaled(rows, _p("brackets_1977_1998_inflation", y)))
    if y <= 1986:
        factor = _p("brackets_1977_1998_inflation", y)
        with_deduction = (taxinc - fedtax).clip(0, None)
        tax_a = pl.when(is_joint).then(
            bracket_tax(taxinc, _scaled(p["brackets_1985_1986_joint_a"], factor))
        ).otherwise(bracket_tax(taxinc, _scaled(p["brackets_1985_1986_single_a"], factor)))
        tax_b = pl.when(is_joint).then(
            bracket_tax(with_deduction, _scaled(p["brackets_1985_1986_joint_b"], factor))
        ).otherwise(bracket_tax(with_deduction, _scaled(p["brackets_1985_1986_single_b"], factor)))
        rate_a = pl.when(is_joint).then(
            bracket_rate(taxinc, _scaled(p["brackets_1985_1986_joint_a"], factor))
        ).otherwise(bracket_rate(taxinc, _scaled(p["brackets_1985_1986_single_a"], factor)))
        rate_b = pl.when(is_joint).then(
            bracket_rate(with_deduction, _scaled(p["brackets_1985_1986_joint_b"], factor))
        ).otherwise(bracket_rate(with_deduction, _scaled(p["brackets_1985_1986_single_b"], factor)))
        return pl.when(tax_a < tax_b).then(rate_a).otherwise(rate_b)
    if y == 1987:
        return by_class(p["brackets_1987"], taxinc, 1.0)
    if y <= 1990:
        return by_class(p["brackets_1988_1990"], taxinc, 1.0)
    if y <= 1998:
        return by_class(p["brackets_1991_1998"], taxinc, _p("brackets_1977_1998_inflation", y))
    if y == 1999:
        return split_joint(p["brackets_1999"], 1.0)
    if y <= 2012:
        return split_joint(p["brackets_2000_2012"], _p("brackets_2000_2012_inflation", y))
    if y <= 2018:
        return split_joint(p["brackets_2013_2018"], _p("brackets_2013plus_inflation", y))
    return split_joint(p["brackets_2019plus"], _p("brackets_2013plus_inflation", y))


def _reported_taxable_income(taxinc: pl.Expr, fedtax: pl.Expr, status: pl.Expr, y: int) -> pl.Expr:
    """Account for `mnrate` mutating taxable income in 1985-1986."""
    if y not in (1985, 1986):
        return taxinc
    is_joint = status == "married_joint"
    factor = _p("brackets_1977_1998_inflation", y)
    with_deduction = (taxinc - fedtax).clip(0, None)
    tax_a = pl.when(is_joint).then(
        bracket_tax(taxinc, _scaled(MN_PARAMS["brackets_1985_1986_joint_a"], factor))
    ).otherwise(bracket_tax(taxinc, _scaled(MN_PARAMS["brackets_1985_1986_single_a"], factor)))
    tax_b = pl.when(is_joint).then(
        bracket_tax(with_deduction, _scaled(MN_PARAMS["brackets_1985_1986_joint_b"], factor))
    ).otherwise(bracket_tax(with_deduction, _scaled(MN_PARAMS["brackets_1985_1986_single_b"], factor)))
    return pl.when(tax_a < tax_b).then(taxinc).otherwise(with_deduction)


def _property_refund_1989plus(hhy: pl.Expr, y: int) -> pl.Expr:
    """Homeowner property tax refund (1989 on)."""
    p = MN_PARAMS
    aif2 = _p("property_refund_inflation", y)
    income = hhy / aif2
    ptxo = pl.col("proptax")
    if y <= 2000:
        c = p["property_refund_1989_2000"]
        maximum = c["maximum"] * aif2
        clawback = (
            pl.when(hhy <= c["clawback_start_2"] * aif2)
            .then((hhy - c["clawback_start_1"] * aif2) * c["clawback_rate_1"])
            .when(hhy <= c["clawback_end"] * aif2)
            .then((hhy - c["clawback_start_2"] * aif2) * c["clawback_rate_2"])
            .otherwise(pl.lit(maximum))
            .clip(None, maximum)
            .clip(0, None)
        )
        refund_max = maximum - clawback
        threshold_pct = interpolate_table(income, p["property_refund_threshold_1989_2000"])
    else:
        if y <= 2002:
            maximum_table, threshold_table = "property_refund_max_2001_2002", "property_refund_threshold_2001_2002"
        elif y <= 2007:
            maximum_table, threshold_table = "property_refund_max_2003_2007", "property_refund_threshold_2003_2010"
        elif y <= 2010:
            maximum_table, threshold_table = "property_refund_max_2008_2010", "property_refund_threshold_2003_2010"
        else:
            maximum_table, threshold_table = "property_refund_max_2011plus", "property_refund_threshold_2011plus"
        refund_max = interpolate_table(income, p[maximum_table]) * aif2
        threshold_pct = interpolate_table(income, p[threshold_table])
    if y <= 2002:
        copay_table = "property_refund_copay_through_2002"
    elif y <= 2010:
        copay_table = "property_refund_copay_2003_2010"
    else:
        copay_table = "property_refund_copay_2011plus"
    copay = interpolate_table(income, p[copay_table])
    threshold = pl.min_horizontal(ptxo, threshold_pct * hhy / 100.0)
    refund = pl.min_horizontal(refund_max, (ptxo - threshold).clip(0, None) * (1.0 - copay)).clip(0, None)
    eligible = (ptxo > 0) & (hhy <= _p("property_refund_owner_income_limit", y) * aif2)
    return pl.when(eligible).then(refund).otherwise(0.0)


def _property_refund_renter(hhy: pl.Expr, y: int) -> pl.Expr:
    """Renter property tax refund (1989 on); zero when not eligible."""
    p = MN_PARAMS
    aifr = _p("property_refund_renter_inflation", y)
    income = hhy / aifr
    ptxr = _p("property_refund_rent_share", y) * pl.col("rentpaid")
    if 2013 <= y <= 2018:
        maximum, threshold = p["property_refund_renter_max_2013_2018"], p["property_refund_renter_threshold_2013_2018"]
    else:
        maximum, threshold = p["property_refund_renter_max"], p["property_refund_renter_threshold"]
    refund_max = interpolate_table(income, maximum) * aifr
    thres = pl.min_horizontal(ptxr, interpolate_table(income, threshold) * hhy / 100.0)
    copay = interpolate_table(income, p["property_refund_renter_copay"])
    refund = pl.min_horizontal(refund_max, (ptxr - thres).clip(0, None) * (1.0 - copay)).clip(0, None)
    return refund


def _property_credit_pre1989(hh: pl.Expr, y: int) -> pl.Expr:
    """Property tax credit before 1989, based on household income."""
    p = MN_PARAMS
    ptx = pl.col("proptax") + float(p["property_credit_rent_share"]) * pl.col("rentpaid")
    aged = aged_count() > 0
    if y >= 1987:
        c = p["property_credit_1987_1988"]
        if y == 1988:
            threshold = interpolate_table(hh, c["threshold_1988"])
            copay = interpolate_table(hh, c["copay_1988"])
        else:
            threshold = (
                pl.when((pl.col("depx") > 0) | aged)
                .then(interpolate_table(hh, c["threshold_1987_with_dependents"]))
                .otherwise(interpolate_table(hh, c["threshold_1987_no_dependents"]))
            )
            copay = interpolate_table(hh, c["copay_1987"])
        excess = (ptx - threshold * hh).clip(0, None)
        maximum = _steps(
            hh,
            c["maximum_steps"],
            pl.when(hh < c["maximum_mid_end"])
            .then(c["maximum_mid_base"] - c["maximum_mid_rate"] * (hh - c["maximum_mid_start"]))
            .otherwise(c["maximum_top_base"] - c["maximum_top_rate"] * (hh - c["maximum_top_start"])),
        )
        credit = pl.min_horizontal(((1.0 - copay) * excess).clip(0, None), maximum)
        return pl.when(hh < c["income_limit"]).then(credit).otherwise(0.0)
    if y >= 1985:
        c = p["property_credit_1985_1986"]
        hh = pl.when(aged).then((hh - float(p["property_credit_1985_1986_aged_income_reduction"])).clip(0, None)).otherwise(hh)
        threshold = interpolate_table(hh, c["threshold"])
        excess = ptx - threshold * hh
        maximum = (
            pl.when(hh < c["maximum_mid_start"])
            .then(pl.lit(float(c["maximum_flat"])))
            .when(hh < c["maximum_mid_end"])
            .then(c["maximum_flat"] - c["maximum_mid_rate"] * (hh - c["maximum_mid_start"]))
            .otherwise(c["maximum_top_base"] - c["maximum_top_rate"] * (hh - c["maximum_mid_end"]))
        )
        credit = pl.min_horizontal(((1.0 - interpolate_table(hh, c["copay"])) * excess).clip(0, None), maximum)
        return pl.when((excess > 0) & (hh < c["income_limit"])).then(credit).otherwise(0.0)
    if y >= 1983:
        c = p["property_credit_1983_1984"]
        threshold = interpolate_table(hh, c["threshold"])
        excess = ptx - threshold * hh
        step1_cap = (
            pl.when(hh < c["first_step_table_limit"])
            .then(interpolate_table(hh, c["first_step"]))
            .otherwise(c["first_step_top_base"] - c["first_step_top_rate"] * (hh - c["first_step_top_start"]))
        )
        step1 = pl.min_horizontal(excess, step1_cap)
        maximum = _steps(
            hh,
            c["maximum_steps"],
            c["maximum_top_base"] - c["maximum_top_rate"] * (hh - c["maximum_top_start"]),
        )
        copay = pl.when(aged).then(interpolate_table(hh, p["property_credit_1983_1984_copay_aged"])).otherwise(
            interpolate_table(hh, c["copay"])
        )
        credit = ((1.0 - copay) * (excess - step1).clip(0, None)).clip(0, None) + step1
        credit = pl.min_horizontal(credit, maximum)
        for lower, upper, share in c["final_limits"]:
            credit = (
                pl.when((hh >= lower) & (hh < upper))
                .then(pl.min_horizontal(credit, ptx - share * hh).clip(0, None))
                .otherwise(credit)
            )
        eligible = (hh < c["income_limit"]) & (ptx > 0) & (excess > 0)
        return pl.when(eligible).then(credit).otherwise(0.0)

    shared = p["property_credit_1977_1982"]
    c = p["property_credit_1979_1982"] if y >= 1979 else p["property_credit_1977_1978"]
    c_aged = p["property_credit_1979_1982_aged"] if y >= 1979 else p["property_credit_1977_1978_aged"]
    threshold = interpolate_table(hh, shared["threshold"])
    excess = ptx - threshold * hh

    def first_step(table: dict) -> pl.Expr:
        return (
            pl.when(hh < shared["first_step_table_limit"])
            .then(interpolate_table(hh, table["first_step"]))
            .otherwise(
                (table["first_step_top_base"] - shared["first_step_top_rate"] * (hh - shared["first_step_top_start"])).clip(
                    0, None
                )
            )
        )

    step1_cap = pl.when(aged).then(first_step(c_aged)).otherwise(first_step(c))
    step1 = pl.min_horizontal(excess, step1_cap)
    if y >= 1979:
        maximum = _steps(
            hh,
            c["maximum_steps"],
            (c["maximum_top_base"] - c["maximum_top_rate"] * (hh - c["maximum_top_start"])).clip(0, None),
        )
    else:
        maximum = (
            pl.when(hh < c["maximum_top_start"])
            .then(pl.lit(float(c["maximum"])))
            .otherwise((c["maximum"] - c["maximum_top_rate"] * (hh - c["maximum_top_start"])).clip(0, None))
        )
    second_step_share = pl.when(aged).then(float(c_aged.get("second_step_share", c["second_step_share"]))).otherwise(
        float(c["second_step_share"])
    )
    credit = (second_step_share * (excess - step1).clip(0, None)).clip(0, None) + step1
    credit = pl.min_horizontal(credit, maximum)
    return pl.when((ptx > 0) & (excess > 0)).then(credit).otherwise(0.0)


def _working_family_credit(
    base_earned: pl.Expr, base_agi: pl.Expr, fed_agi: pl.Expr, is_joint: pl.Expr, y: int
) -> pl.Expr:
    """Working Family Credit (refundable), 1991 on."""
    p = MN_PARAMS
    nkid = pl.col("depx").floor()
    eitc = pl.col("eitc")
    if y < 1991:
        return pl.lit(0.0)
    if y <= 1997:
        share = _p("wfc_share_of_federal_eitc", y)
        return pl.when(eitc > 0).then(share * eitc).otherwise(0.0)

    wmax = pl.max_horizontal(base_earned, base_agi)

    def two_base(credit_fn, agi_test: pl.Expr) -> pl.Expr:
        return pl.when(fed_agi <= agi_test).then(credit_fn(base_earned)).otherwise(
            pl.min_horizontal(credit_fn(base_earned), credit_fn(base_agi))
        )

    if y == 1998:
        c = p["wfc_1998"]
        n0 = c["no_child"]

        def no_child(base: pl.Expr) -> pl.Expr:
            credit = (
                pl.when(base <= n0["phase_in_end"])
                .then(n0["phase_in_rate"] * base)
                .otherwise(pl.lit(0.0))
            )
            credit = pl.when(base <= n0["plateau_end"]).then(pl.lit(float(n0["maximum"]))).otherwise(credit)
            return (
                pl.when(base < n0["phase_out_end"])
                .then((n0["maximum"] - n0["phase_out_rate"] * (base - n0["plateau_end"])).clip(0, None))
                .otherwise(credit)
            )

        work = (
            pl.when(nkid < 1)
            .then(two_base(no_child, pl.lit(float(n0["agi_test"]))))
            .when(nkid == 1)
            .then(two_base(lambda b: _pieces(b, c["one_child"]["pieces"]), pl.lit(float(c["one_child"]["agi_test"]))))
            .otherwise(
                two_base(lambda b: _pieces(b, c["two_or_more"]["pieces"]), pl.lit(float(c["two_or_more"]["agi_test"])))
            )
        )
    elif y <= 2013:
        use_joint = 2002 <= y <= 2011 or y == 2013
        c0 = p["wfc_1999_2013_no_child"]
        phase_out_0 = _sub(c0, "phase_out_start", y)
        if use_joint:
            phase_out_0 = pl.when(is_joint).then(_sub(c0, "phase_out_start_joint", y)).otherwise(phase_out_0)
        else:
            phase_out_0 = pl.lit(phase_out_0)
        rate_0 = _sub(c0, "rate", y)
        max_0 = _sub(c0, "maximum", y)

        def no_child(base: pl.Expr) -> pl.Expr:
            return (
                pl.when(base <= _sub(c0, "phase_in_end", y))
                .then(rate_0 * base)
                .when(base <= phase_out_0)
                .then(pl.min_horizontal(phase_out_0, pl.lit(max_0)))
                .otherwise((max_0 - rate_0 * (base - phase_out_0)).clip(0, None))
            )

        def with_children(c: dict):
            phase_out = _sub(c, "break_4", y)
            if use_joint:
                phase_out = pl.when(is_joint).then(_sub(c, "break_4_joint", y)).otherwise(phase_out)
            else:
                phase_out = pl.lit(phase_out)
            b1, b2, b3 = (_sub(c, f"break_{i}", y) for i in (1, 2, 3))
            r1, r2, r3 = (_sub(c, f"rate_{i}", y) for i in (1, 2, 3))
            m1, m2 = _sub(c, "maximum_1", y), _sub(c, "maximum_2", y)

            def credit(base: pl.Expr) -> pl.Expr:
                return (
                    pl.when(base < b1)
                    .then(r1 * base)
                    .when(base < b2)
                    .then(pl.min_horizontal(phase_out, pl.lit(m1)))
                    .when(base < b3)
                    .then(r2 * (base - b2) + m1)
                    .when(base < phase_out)
                    .then(pl.lit(m2))
                    .otherwise((m2 - r3 * (base - phase_out)).clip(0, None))
                )

            return credit, phase_out

        one_fn, one_out = with_children(p["wfc_1999_2013_one_child"])
        two_fn, two_out = with_children(p["wfc_1999_2013_two_children"])
        work = (
            pl.when(nkid < 1)
            .then(two_base(no_child, phase_out_0))
            .when(nkid == 1)
            .then(two_base(one_fn, one_out))
            .otherwise(two_base(two_fn, two_out))
        )
    else:
        c = p["wfc_2014plus"]

        def schedule(kind: str, phase_out_start: pl.Expr) -> pl.Expr:
            k = c[kind]
            credit = (
                pl.when(base_earned <= _sub(k, "phase_in_end", y))
                .then(_sub(k, "phase_in_rate", y) * base_earned)
                .otherwise(pl.lit(_sub(k, "maximum", y)))
            )
            return (credit - _sub(k, "phase_out_rate", y) * (wmax - phase_out_start).clip(0, None)).clip(0, None)

        def start(kind: str, joint_kind: str) -> pl.Expr:
            return (
                pl.when(is_joint)
                .then(_sub(c[joint_kind], "phase_out_start_joint", y))
                .otherwise(_sub(c[kind], "phase_out_start", y))
            )

        work = (
            pl.when(nkid < 1)
            .then(schedule("no_child", start("no_child", "no_child")))
            .when(nkid == 1)
            .then(schedule("one_child", start("one_child", "one_child")))
        )
        many = schedule("two_children", start("one_child", "two_children"))
        if y >= 2019:
            work = work.when(nkid == 2).then(many).otherwise(
                schedule("three_children", start("one_child", "two_children"))
            )
        else:
            work = work.otherwise(many)

    if y <= 2018:
        work = pl.when(eitc >= _p("wfc_min_federal_eitc", y)).then(work).otherwise(0.0)
    return work


def compute_mn_tax(df: pl.DataFrame, year: int) -> pl.DataFrame:
    """Calculate Minnesota income tax for each row."""
    effective_year, flate = resolve_state_year(year)
    y = effective_year
    p = MN_PARAMS
    df = with_defaults(df, (
        "proptax", "otheritem", "mortgage", "dividends", "intrec", "stcg", "ltcg", "ui", "pui", "sui",
        "psemp", "ssemp", "depx", "dep13", "childcare", "charity_cash", "state_sales_or_income_tax_ded",
        "fiitax", "eitc", "earned_income", "taxable_unemployment", "itemized_deduction",
        "standard_deduction", "personal_exemptions", "ccc_uncapped", "pensions", "gssi",
        "taxable_social_security", "rentpaid", "federal_elder", "pbusinc", "pprofinc", "sbusinc", "sprofinc",
        "scorp",
    ))
    df = with_default(df, "itemizes", False)

    setax = payroll_parts(year)["setax"]  # `comnew(175)`, real-year and undeflated
    hh_income = household_income(
        _adj("household_income_dividend_adjustment", y), _adj("household_income_record_adjustment", y)
    )
    # Business and S corporation income are not deflated in projected years.
    business = pl.col("pbusinc") + pl.col("pprofinc") + pl.col("sbusinc") + pl.col("sprofinc") + pl.col("scorp")
    df = df.with_columns(mn_setax=setax, mn_hh=hh_income, mn_business=business)
    df = deflate_for_extrapolation(
        df, flate,
        [
            "pwages", "swages", "dividends", "intrec", "stcg", "ltcg", "ui", "pui", "sui", "psemp", "ssemp",
            "proptax", "otheritem", "mortgage", "childcare", "charity_cash", "state_sales_or_income_tax_ded",
            "agi", "fiitax", "eitc", "earned_income", "taxable_unemployment", "itemized_deduction",
            "standard_deduction", "personal_exemptions", "mn_hh", "pensions", "gssi",
            "taxable_social_security", "rentpaid", "federal_elder",
        ],
    )

    status = pl.col("filing_status")
    is_joint = status == "married_joint"
    is_sep = status == "married_separate"
    is_hoh = status == "head_of_household"
    sep = pl.when(is_sep).then(2.0).otherwise(1.0)
    txp = taxpayer_count()
    aged = aged_count()
    dependent_filer = is_dependent_filer()
    depx = pl.col("depx")
    hh = pl.col("mn_hh")
    salt_ded = pl.col("state_sales_or_income_tax_ded")
    untax = pl.col("taxable_unemployment")
    ui_total = unemployment_total()
    fullcg = pl.col("stcg") + pl.col("ltcg")

    # Federal return values as Minnesota reads them.
    if y <= 1986:
        fed_deduc = pl.col("proptax") + pl.col("otheritem") + pl.col("mortgage") + salt_ded
        fed_zbr = by_filing_status(
            {s: resolve_year(PRE1987_PARAMS["standard_deduction"][s], y) for s in _STATUSES}
        )
        fed_itemizes = fed_deduc > fed_zbr if y <= 1981 else itemize_choice(fed_deduc > fed_zbr)
    else:
        fed_deduc = pl.col("itemized_deduction")
        fed_itemizes = pl.col("itemizes")
        fed_zbr = pl.when(fed_itemizes).then(0.0).otherwise(pl.col("standard_deduction"))
    agix = pl.col("agi").clip(0, None)
    alim50 = pl.lit(1.0e20) if y in (2020, 2021) else 0.5 * agix
    cash = pl.when(pl.col("agi") < 0).then(0.0).otherwise(pl.min_horizontal(pl.col("charity_cash"), alim50))
    fed_char = pl.min_horizontal(alim50, cash).clip(0, None)
    fed_agi = pl.col("agi")
    if y == 2020:
        fed_agi = fed_agi - pl.when(fed_itemizes).then(0.0).otherwise(
            pl.min_horizontal(_p("charity_nonitemizer_addback", y) / sep, pl.col("charity_cash"))
        )

    # --- Minnesota AGI ---
    agi = fed_agi
    if y == 2009:
        agi = agi - untax + ui_total
    if y == 2020:
        agi = agi + ui_total - untax + pl.when(fed_itemizes).then(0.0).otherwise(
            pl.min_horizontal(_p("charity_nonitemizer_addback", y), pl.col("charity_cash"))
        )
    if 1979 <= y <= 1982:
        agi = agi + _p("capital_gains_addback_rate_1979_1982", y) * fullcg
    if 1979 <= y <= 1986:
        agi = agi - untax
    if 1982 <= y <= 1985:
        agi = agi + pl.col("pre1987_twoded")
    # Pension exclusion, reduced by federal AGI over a threshold.
    if y <= 1986:
        over = (fed_agi - _p("pension_exclusion_income_threshold", y)).clip(0, None)
        penexc = pl.min_horizontal(pl.col("pensions"), (_p("pension_exclusion_max", y) - over).clip(0, None))
        if y >= 1985:
            penexc = pl.when(aged > 0).then(penexc).otherwise(0.0)
        agi = agi - penexc

    df, (agi, fed_agi) = checkpoint(df, mn_agi=agi, mn_fed_agi=fed_agi)

    # --- Deduction for federal income tax (through 1986) ---
    fedtax = pl.lit(0.0)
    if y <= 1986:
        seless = _p("federal_tax_se_share", y)
        semax = _p("federal_tax_se_cap", y)
        fedtax = (pl.col("fiitax") - pl.col("mn_setax") * seless).clip(0, None)
        fedtax = (fedtax - ((1.0 - seless) * pl.col("mn_setax") - semax).clip(0, None)).clip(0, None)
        fedtax = pl.when((fed_agi > 0) & (agi >= 0)).then(
            fedtax * pl.min_horizontal(pl.lit(1.0), agi / fed_agi)
        ).otherwise(fedtax)
        if y <= 1984:
            agi = agi - fedtax

    df, (agi, fedtax) = checkpoint(df, mn_agi_after_fedtax=agi, mn_fedtax=fedtax)

    # --- Exemptions ---
    if y <= 1986:
        exemp = pl.lit(0.0)
    elif y <= 2012:
        exemp = pl.col("personal_exemptions")
    if y >= 2011:
        aif91 = _p("phaseout_inflation", y) if y >= 2013 else 0.0
        mult = by_filing_status(p["phaseout_multiplier"])
        ph13 = _p("phaseout_threshold_base", y) * aif91 * mult / sep if y >= 2013 else pl.lit(0.0)
        phded = _p("phaseout_threshold_base", y) * aif91 / sep if y >= 2013 else pl.lit(0.0)
    if y >= 2013:
        exemption_amount = _p("exemption_amount", y)
        count = federal_exemption_count(y) if y <= 2018 else depx
        exemp = count * exemption_amount
        excess = (fed_agi - ph13).clip(0, None)
        ratio = pl.min_horizontal(
            pl.lit(1.0),
            _p("exemption_phaseout_rate", y) * excess / (_p("exemption_phaseout_step", y) / sep),
        )
        exemp = exemp - ratio * exemp
        exemp = pl.when(dependent_filer).then(0.0).otherwise(exemp)

    # --- Standard deduction ---
    ag = agi.clip(0, None)
    if y <= 1986:
        cap = _p("standard_deduction_cap_pre1987", y) * _p("standard_deduction_inflation_pre1987", y)
        cap_expr = pl.lit(cap) / sep if y >= 1985 else pl.lit(cap)
        stded = pl.min_horizontal(cap_expr, _p("standard_deduction_pct_pre1987", y) * ag)
    elif y <= 2017:
        stded = fed_zbr
        if y >= 2005:
            reduction = _p("standard_deduction_married_reduction", y)
            stded = stded - pl.when(is_joint | is_sep).then(reduction / sep).otherwise(0.0)
        if y in (2008, 2009):
            property_cap = float(
                resolve_year(FEDERAL_INCOME_TAX_PARAMS["standard_deduction_real_property_tax_cap"], y)
            )
            stded = stded - pl.min_horizontal(pl.col("proptax").clip(0, None), property_cap * txp)
    elif y == 2018:
        stded = by_filing_status(p["standard_deduction_2018"]) + by_filing_status(
            p["standard_deduction_2018_aged_addition"]
        ) * aged
    else:
        table = p["standard_deduction_table"]
        aged_add = p["standard_deduction_aged_addition"]
        stded = (
            _by_filer(
                _sub(table, "single", y), _sub(table, "married_joint", y), _sub(table, "head_of_household", y)
            )
            + _by_filer(_sub(aged_add, "single", y), _sub(aged_add, "married", y), _sub(aged_add, "single", y)) * aged
        ) / sep
    if y >= 2018:
        dependent_limit = pl.max_horizontal(
            pl.lit(_p("dependent_standard_deduction_floor", y)),
            pl.col("earned_income") + _p("dependent_standard_deduction_earned_addition", y),
        )
        stded = pl.when(dependent_filer).then(pl.min_horizontal(stded, dependent_limit)).otherwise(stded)

    # --- Itemized deductions ---
    if y <= 1986:
        xitded = pl.when(fed_itemizes).then((fed_deduc - salt_ded).clip(0, None)).otherwise(0.0)
    elif y <= 2012:
        xitded = pl.when(fed_itemizes).then(fed_deduc).otherwise(0.0)
    elif y <= 2017:
        deduc1 = fed_char + pl.col("proptax") + pl.col("otheritem") + pl.col("mortgage")
        dlim1 = _p("itemized_phaseout_rate", y) * (fed_agi - ph13).clip(0, None)
        dlim2 = _p("itemized_phaseout_max_share", y) * deduc1.clip(0, None)
        xitded = (deduc1 - pl.min_horizontal(dlim1, dlim2)).clip(0, None)
    elif y <= 2019:
        salt = pl.col("proptax") + salt_ded + pl.col("otheritem")
        cap = _p("salt_cap", y) / sep
        xitded = fed_deduc - pl.min_horizontal(cap, salt) + salt
        disalt = pl.min_horizontal((xitded - stded).clip(0, None), salt_ded + pl.col("otheritem"))
        dedphs = pl.min_horizontal(
            _p("itemized_phaseout_max_share", y) * xitded,
            _p("itemized_phaseout_rate", y) * (fed_agi - phded),
        )
        alostd = disalt + dedphs
        exces = (xitded - stded).clip(0, None)
        remaining = pl.when(alostd <= exces).then((xitded - dedphs).clip(0, None)).otherwise(
            xitded - (exces - disalt).clip(0, None)
        )
        disagi = pl.when(fed_agi > phded).then(xitded - remaining).otherwise(0.0)
        xitded = xitded - (disalt + disagi)
    else:
        sttax = pl.min_horizontal(_p("salt_cap", y) / sep, pl.col("proptax") + salt_ded + pl.col("otheritem"))
        stt = pl.min_horizontal(salt_ded, sttax - (pl.col("proptax") + pl.col("otheritem")))
        xitded = fed_deduc - stt
        dedphs = pl.min_horizontal(
            _p("itemized_phaseout_max_share", y) * fed_deduc,
            _p("itemized_phaseout_rate", y) * (fed_agi - phded),
        )
        xitded = xitded - pl.when(fed_agi - phded > 0).then(dedphs).otherwise(0.0)

    df, (stded, xitded, exemp) = checkpoint(df, mn_stded=stded, mn_xitded=xitded, mn_exemp=exemp)
    deduc = pl.max_horizontal(stded, xitded)
    taxinc = agi - deduc - exemp

    # --- Addition for state income tax deducted federally ---
    if y <= 2017:
        if y >= 1988:
            table = p["standard_deduction_table"]
            stnd = _by_filer(
                _sub(table, "single", y), _sub(table, "married_joint", y), _sub(table, "head_of_household", y)
            ) / sep
            aged_add = p["standard_deduction_aged_addition"]
            oldd = _by_filer(_sub(aged_add, "single", y), _sub(aged_add, "married", y), _sub(aged_add, "single", y)) * aged
            add = pl.min_horizontal((fed_deduc - stnd - oldd).clip(0, None), salt_ded)
        else:
            add = pl.when(fed_deduc - salt_ded < fed_zbr).then(fed_deduc - fed_zbr).otherwise(salt_ded)
        add = pl.when(fed_itemizes).then(add).otherwise(0.0)
    else:
        add = pl.lit(0.0)

    # Subtraction for the elderly, less nontaxable Social Security.
    subrac = pl.lit(0.0)
    if 1988 <= y <= 2016:
        eld = p["elderly_subtraction"][1994 if y >= 1994 else 1988]
        classes = (
            pl.when(is_joint & (aged >= 2)).then(0).when(is_joint).then(1).when(is_sep).then(3).otherwise(2)
        )

        def pick(values: list) -> pl.Expr:
            expr = pl.lit(float(values[3]))
            for index in (2, 1, 0):
                expr = pl.when(classes == index).then(float(values[index])).otherwise(expr)
            return expr

        nontaxable = pl.col("gssi") - pl.col("taxable_social_security")
        yless = float(p["elderly_subtraction_reduction_rate"]) * (fed_agi - pick(eld["reduction_start"])).clip(0, None)
        eldded = (pick(eld["amount"]) - nontaxable - yless).clip(0, None)
        subrac = pl.when((aged > 0) & (fed_agi <= pick(eld["income_limit"]))).then(eldded).otherwise(0.0)
    if y >= 1999:
        subrac = subrac + pl.when(stded > xitded).then(
            _p("charity_subtraction_rate", y) * (cash - _p("charity_subtraction_floor", y)).clip(0, None)
        ).otherwise(0.0)
    # 2017+ Social Security subtraction.
    if y >= 2017:
        ss_taxable = pl.col("taxable_social_security")
        provisional = fed_agi - ss_taxable + float(p["social_security_subtraction_benefit_share"]) * pl.col("gssi")
        thresholds, maxima = p["social_security_subtraction_threshold"], p["social_security_subtraction_max"]
        single_class = pl.col("filing_status").is_in(["single", "head_of_household"])
        threshold = pl.when(single_class).then(_sub(thresholds, "single", y)).otherwise(_sub(thresholds, "married", y) / sep)
        maximum = pl.when(single_class).then(_sub(maxima, "single", y)).otherwise(_sub(maxima, "married", y) / sep)
        allowed = (
            maximum - float(p["social_security_subtraction_phaseout_rate"]) * (provisional - threshold).clip(0, None)
        ).clip(0, None)
        subrac = subrac + pl.when(ss_taxable > 0).then(pl.min_horizontal(ss_taxable, allowed)).otherwise(0.0)
    taxinc = taxinc - subrac + add

    if 2011 <= y <= 2013:
        ph = _p("m1m_addback_threshold", y)
        table = p["standard_deduction_table"]
        stdmn = _by_filer(
            _sub(table, "single", y), _sub(table, "married_joint", y), _sub(table, "head_of_household", y)
        ) / sep
        xl9 = pl.min_horizontal(
            _p("itemized_phaseout_max_share", y) * fed_deduc.clip(0, None),
            _p("itemized_phaseout_rate", y) * (fed_agi - ph / sep).clip(0, None),
        )
        xl14 = (fed_deduc - stdmn).clip(0, None)
        xl15 = pl.when(add + xl9 <= xl14).then(xl9).otherwise((xl14 - add).clip(0, None))
        taxinc = taxinc + pl.when(fed_itemizes & (fed_agi > ph)).then(xl15).otherwise(0.0)

        phamex = ph * by_filing_status(p["phaseout_multiplier"]) / sep
        over = fed_agi - phamex
        addxmp = pl.when(over <= _p("m1m_exemption_phaseout_range", y) / sep).then(
            _p("exemption_phaseout_rate", y) * over / (_p("exemption_phaseout_step", y) / sep)
            * pl.col("personal_exemptions")
        ).otherwise(pl.col("personal_exemptions"))
        taxinc = taxinc + pl.when(over > 0).then(addxmp).otherwise(0.0)

    df, (taxinc,) = checkpoint(df, mn_taxinc=taxinc.clip(0, None))
    if y <= 1986:
        taxinc = (agi - deduc - exemp).clip(0, None)
    df, (statax,) = checkpoint(df, mn_regular_tax=_schedule_tax(taxinc, fedtax, status, y))

    # --- Alternative minimum tax ---
    if y >= 2001:
        char_pref = (fed_char - _p("amt_charity_floor", y) * fed_agi.clip(0, None)).clip(0, None)
        alminy = fed_agi - char_pref
        excl = _by_filer(*(_sub(p["amt_exclusion"], k, y) for k in ("single", "married", "head_of_household"))) / sep
        phase = _by_filer(*(float(p["amt_phaseout_start"][k]) for k in ("single", "married", "head_of_household"))) / sep
        alminc = (
            alminy - (excl - _p("amt_phaseout_rate", y) * (alminy - phase).clip(0, None)).clip(0, None)
        ).clip(0, None)
        statax = statax + (_p("amt_rate", y) * alminc - statax).clip(0, None)

    df, (statax,) = checkpoint(df, mn_tax_after_amt=statax)

    # --- Nonrefundable credits ---
    credit = pl.lit(0.0)
    if y == 1978:
        # Per taxpayer and dependent, plus smaller amounts for taxpayers 65 or older.
        credit = _p("personal_credit", y) * (txp + depx) + 20.0 * aged + 10.0 * (aged - txp).clip(0, None)
    elif y <= 1986:
        credit = (txp + depx + aged) * _p("personal_credit", y)
    if 1978 <= y <= 1984:
        earnings_limit = float(p["homemaker_credit_earnings_limit"])
        hcred = pl.when(
            dependent_filer
            & (depx >= 1)
            & (pl.col("pwages") <= earnings_limit)
            & (pl.col("swages") <= earnings_limit)
            & ~is_sep
            & (fed_agi <= float(p["homemaker_credit_agi_limit"]) / sep)
        ).then(float(p["homemaker_credit"])).otherwise(0.0)
        credit = credit + hcred
    if y == 1987:
        credit = credit + float(p["elderly_credit_share_1987"]) * pl.col("federal_elder")
    if y <= 1984:
        npop = (txp + depx).clip(None, 6).floor()
        limits = resolve_year(p["low_income_credit_threshold"], y)
        ylow = pl.lit(0.0)
        for index, limit in enumerate(limits, start=1):
            ylow = pl.when(npop == index).then(pl.lit(float(limit))).otherwise(ylow)
        ytest = (_p("low_income_credit_rate", y) * (hh - ylow)).clip(0, None)
        crlow = (statax - credit - ytest).clip(0, None)
        credit = credit + pl.when((fed_agi <= _p("low_income_credit_agi_limit", y)) & ~dependent_filer).then(crlow).otherwise(0.0)
    if y >= 1999:
        d17 = pl.col("psemp") + pl.col("ssemp") + pl.col("pwages").clip(None, 0) + pl.col("swages").clip(None, 0)
        other_income = pl.lit(0.0)
        if y >= 2000:
            # From 2000 business income, pensions and taxable Social Security count too.
            d17 = d17 + pl.col("mn_business")
            other_income = pl.col("pensions") + pl.col("taxable_social_security")
        earn = pl.min_horizontal(pl.col("pwages"), pl.col("swages")) + _p("marriage_credit_self_employment_share", y) * (
            other_income + d17.clip(0, None) - _p("marriage_credit_self_employment_tax_share", y) * pl.col("mn_setax")
        )
        if y <= 2017:
            xl10 = _p("exemption_amount", y) + _p("marriage_credit_standard_deduction_share", y) * _p(
                "federal_joint_standard_deduction", y
            )
        else:
            xl10 = _p("marriage_credit_lower_earner_deduction", y)
        xl11 = (earn - xl10).clip(0, None)
        single = pl.lit("single")
        x1 = _schedule_tax(xl11, fedtax, single, y)
        x2 = _schedule_tax((taxinc - xl11).clip(0, None), fedtax, single, y)
        statm = pl.when(
            (taxinc < _p("marriage_credit_min_taxable_income", y)) | (earn < _p("marriage_credit_min_earnings", y))
        ).then(0.0).otherwise(statax)
        crmar = pl.min_horizontal(pl.lit(_p("marriage_credit_max", y)), (statm - (x1 + x2)).clip(0, None))
        credit = credit + pl.when(is_joint).then(crmar).otherwise(0.0)

    df, (credit,) = checkpoint(df, mn_credit=credit)

    # --- Working Family Credit ---
    base_agi = fed_agi + (ui_total - untax if y == 2020 else 0.0)
    work = _working_family_credit(pl.col("earned_income"), base_agi, fed_agi, is_joint, y)
    work = pl.when(dependent_filer).then(0.0).otherwise(work)

    df, (work,) = checkpoint(df, mn_work=work)

    # --- Child and dependent care credit ---
    deps = depx.clip(None, 2).floor()
    cymax = pl.when(deps >= 2).then(_sub(p["child_care_income_limit"], "two_or_more", y)).otherwise(
        _sub(p["child_care_income_limit"], "one", y)
    )
    cmax = pl.when(deps >= 2).then(_sub(p["child_care_credit_max"], "two_or_more", y)).otherwise(
        _sub(p["child_care_credit_max"], "one", y)
    )
    cded = _p("child_care_phaseout_start", y)
    if y == 2013:
        c13 = p["child_care_2013"]
        ncccr = pl.when(pl.col("dep13") > 0).then(pl.col("dep13").clip(None, 2).floor()).otherwise(deps)
        child = pl.min_horizontal(pl.col("childcare"), c13["expense_cap_per_child"] * ncccr)
        child = pl.when(is_joint).then(
            pl.min_horizontal(child, pl.col("pwages"), pl.col("swages")).clip(0, None)
        ).otherwise(child)
        chr_rate = pl.max_horizontal(
            pl.lit(float(c13["rate_floor"])),
            c13["rate_top"]
            - c13["rate_step_decrement"] * ((agi - c13["rate_start"]) / c13["rate_step"]).clip(0, None),
        )
        cost = chr_rate * child
    elif y <= 1986:
        cost = pl.lit(0.0)
    else:
        cost = _p("child_care_share_of_federal", y) * pl.col("ccc_uncapped")
    phaseout_rate = _p("child_care_phaseout_rate", y)
    if y >= 2017:
        over = (fed_agi - cded).clip(0, None)
        chcr = (cost - phaseout_rate * over).clip(0, None)
        eligible = fed_agi <= cymax
    else:
        over = (hh - cded).clip(0, None)
        cost = pl.min_horizontal(cost, cmax)
        per_dependent = deps if y >= 1983 else pl.lit(1.0)
        chcr = (cost - phaseout_rate * per_dependent * over).clip(0, None)
        eligible = hh <= cymax
    chcr = pl.when((deps > 0) & eligible).then(chcr).otherwise(0.0)

    # --- Property tax refund ---
    if y >= 1989:
        loss_limit = float(resolve_year(CAPITAL_GAINS_PARAMS["net_capital_loss_limit"], y))
        capgn = pl.max_horizontal(fullcg, -loss_limit / flate / sep)
        # Federal AGI plus nontaxable Social Security, with losses added back.
        refund_income = fed_agi + pl.col("gssi") - pl.col("taxable_social_security")
        hhy = pl.when(capgn < 0).then(refund_income - capgn).otherwise(refund_income).clip(0, None)
        if y >= 1994:
            kid = depx.clip(None, 5).floor()

            def by_kid(amounts: list) -> pl.Expr:
                expr = pl.lit(0.0)
                for index, amount in enumerate(amounts):
                    expr = pl.when(kid == index).then(pl.lit(float(amount))).otherwise(expr)
                return expr

            dep_sub = by_kid(resolve_year(p["property_refund_dependent_subtraction"], y))
            if y == 1994:
                aged_sub = by_kid(p["property_refund_aged_subtraction_1994"])
            else:
                aged_sub = dep_sub + _p("exemption_amount", y)
            hhy = (hhy - pl.when(aged > 0).then(aged_sub).otherwise(dep_sub)).clip(0, None)
        df, (hhy,) = checkpoint(df, mn_refund_income=hhy)
        # Renters who qualify get the renter refund in place of the homeowner refund.
        renter = (pl.col("rentpaid") > 0) & (
            hhy <= _p("property_refund_renter_income_limit", y) * _p("property_refund_renter_inflation", y)
        )
        pcred = pl.when(renter).then(_property_refund_renter(hhy, y)).otherwise(_property_refund_1989plus(hhy, y))
    else:
        pcred = _property_credit_pre1989(hh, y)
    pcred = pl.when(dependent_filer).then(0.0).otherwise(pcred)
    df, (chcr, pcred) = checkpoint(df, mn_chcr=chcr, mn_pcred=pcred.clip(0, None))

    # --- 2009 motor fuels credit ---
    crfuel = pl.lit(0.0)
    if y == 2009:
        fuel = p["fuel_credit_2009"]
        limits = fuel["taxable_income_limit"]
        limit = _by_filer(limits["single"], limits["married"], limits["head_of_household"]) / sep
        crfuel = pl.when(taxinc <= limit).then(fuel["amount"] / sep).otherwise(0.0)

    statax = (statax - credit).clip(0, None) - chcr - work - pcred - crfuel
    rate_expr = _schedule_rate(taxinc, fedtax, status, y)
    if y <= 1986:
        first_taxinc = pl.col("mn_taxinc")
        first_tax = _schedule_tax(first_taxinc, fedtax, status, y)
        political_tax = _schedule_tax(taxinc, fedtax, status, y)
        rate_expr = pl.when(first_tax > political_tax).then(
            _schedule_rate(first_taxinc, fedtax, status, y)
        ).otherwise(rate_expr)
    return with_state_detail(
        df.with_columns(siitax=statax * flate),
        agi=agi,
        exemptions=exemp,
        standard_deduction=stded,
        itemized_deductions=xitded,
        taxable_income=_reported_taxable_income(taxinc, fedtax, status, y),
        property_credit=pcred,
        child_care_credit=chcr,
        eic=work,
        credits=credit + chcr + work + pcred + crfuel,
        rate=rate_expr,
    )
