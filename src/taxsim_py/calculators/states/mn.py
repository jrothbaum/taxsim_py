"""Minnesota individual income tax calculator."""


from __future__ import annotations
import polars as pl

from taxsim_py.calculators.federal_pre1987 import PRE1987_PARAMS
from taxsim_py.engine.brackets import bracket_rate, bracket_tax
from taxsim_py.engine.inputs import aged_count, federal_exemption_count, files_head_of_household, files_joint, files_separate, is_dependent_filer, taxpayer_count
from taxsim_py.engine.schema import PARAMETERS_ROOT, YearParams, load_yaml, resolve_year
from taxsim_py.engine.state import (
    federal_zero_bracket,
    net_earnings_by_spouse,
    phaseout_steps,
    itemize_choice,
    by_filing_status,
    checkpoint,
    household_income,
    interpolate_table,
    unemployment_total,
    with_state_detail,
)
from taxsim_py.behavior import BehaviorProfile, TAXSIM_BEHAVIOR
from taxsim_py.engine.state_extrapolation import deflate_for_extrapolation, resolve_state_year

MN_PARAMS = load_yaml(PARAMETERS_ROOT / "states" / "mn" / "income_tax.yaml")
CAPITAL_GAINS_PARAMS = load_yaml(PARAMETERS_ROOT / "national" / "capital_gains.yaml")
FEDERAL_INCOME_TAX_PARAMS = load_yaml(PARAMETERS_ROOT / "national" / "income_tax.yaml")
_STATUSES = ["single", "married_joint", "married_separate", "head_of_household"]


def _sub(section: dict, name: str, year: int) -> float:
    return float(resolve_year(section[name], year))


def _scaled(brackets: list[list[float]], factor: float) -> list[list[float]]:
    return [[float(start) * factor, float(rate)] for start, rate in brackets]


def _by_filer(single: float, married: float, head_of_household: float, separate_is_married: bool = True) -> pl.Expr:
    """Select a value by Minnesota filer class; married separate uses the married value (TAXSIM's `nfile` makes it single)."""
    return by_filing_status(
        {
            "single": single,
            "married_joint": married,
            "married_separate": married if separate_is_married else single,
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


def _brackets(p: YearParams, published: bool) -> tuple[dict | list, float]:
    """The rate tables and inflation factor: TAXSIM scales its 2021 table from 2022 on."""
    if not published and p.in_effect("taxsim_bracket_inflation"):
        return p.value("taxsim_brackets"), p.num("taxsim_bracket_inflation")
    return p.value("brackets"), p.num("bracket_inflation")


def _schedule_tax(
    taxinc: pl.Expr, fedtax: pl.Expr, status: pl.Expr, y: int, halve_separate: bool = True, published: bool = True
) -> pl.Expr:
    """Minnesota rate-schedule tax (`mnrate`) for a filer class."""
    p = YearParams(MN_PARAMS, y)
    is_joint = status == "married_joint"
    is_sep = status == "married_separate"
    is_hoh = status == "head_of_household"
    # The 2026 build hands the rate routine the joint code with a divisor of 1 for separate returns: the
    # joint table, neither doubled nor halved.
    sep = pl.when(is_sep & pl.lit(halve_separate)).then(2.0).otherwise(1.0)

    def by_class(tables: dict, income: pl.Expr, factor: float) -> pl.Expr:
        return (
            pl.when(is_joint | (is_sep & ~pl.lit(halve_separate)))
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

    tables, factor = _brackets(p, published)
    if y <= 1984:
        tax = bracket_tax(taxinc, _scaled(tables, factor))
    elif y <= 1986:
        with_deduction = (taxinc - fedtax).clip(0, None)
        joint_like = is_joint | (is_sep & ~pl.lit(halve_separate))
        tax_a = (
            pl.when(joint_like)
            .then(bracket_tax(taxinc, _scaled(tables["joint_a"], factor)))
            .otherwise(bracket_tax(taxinc, _scaled(tables["single_a"], factor)))
        )
        tax_b = (
            pl.when(joint_like)
            .then(bracket_tax(with_deduction, _scaled(tables["joint_b"], factor)))
            .otherwise(bracket_tax(with_deduction, _scaled(tables["single_b"], factor)))
        )
        tax = pl.min_horizontal(tax_a, tax_b)
    elif y <= 1998:
        tax = by_class(tables, taxinc, factor)
    else:
        tax = split_joint(tables, factor)
    if p.in_effect("surtax"):
        tax = tax * p.num("surtax")
    return tax


def _schedule_rate(
    taxinc: pl.Expr, fedtax: pl.Expr, status: pl.Expr, y: int, halve_separate: bool = True, published: bool = True
) -> pl.Expr:
    """Rate left by the final lookup in Minnesota's schedule routine."""
    p = YearParams(MN_PARAMS, y)
    is_joint = status == "married_joint"
    is_sep = status == "married_separate"
    is_hoh = status == "head_of_household"
    sep = pl.when(is_sep & pl.lit(halve_separate)).then(2.0).otherwise(1.0)

    def by_class(tables: dict, income: pl.Expr, factor: float) -> pl.Expr:
        return (
            pl.when(is_joint | (is_sep & ~pl.lit(halve_separate))).then(bracket_rate(income, _scaled(tables["joint"], factor)))
            .when(is_hoh).then(bracket_rate(income, _scaled(tables["head_of_household"], factor)))
            .otherwise(bracket_rate(income, _scaled(tables["single"], factor)))
        )

    def split_joint(tables: dict, factor: float) -> pl.Expr:
        return (
            pl.when(status == "single").then(bracket_rate(taxinc, _scaled(tables["single"], factor)))
            .when(is_hoh).then(bracket_rate(taxinc, _scaled(tables["head_of_household"], factor)))
            .otherwise(bracket_rate(taxinc * sep, _scaled(tables["joint"], factor)))
        )

    tables, factor = _brackets(p, published)
    if y <= 1984:
        return bracket_rate(taxinc, _scaled(tables, factor))
    if y <= 1986:
        with_deduction = (taxinc - fedtax).clip(0, None)
        tax_a = pl.when(is_joint).then(
            bracket_tax(taxinc, _scaled(tables["joint_a"], factor))
        ).otherwise(bracket_tax(taxinc, _scaled(tables["single_a"], factor)))
        tax_b = pl.when(is_joint).then(
            bracket_tax(with_deduction, _scaled(tables["joint_b"], factor))
        ).otherwise(bracket_tax(with_deduction, _scaled(tables["single_b"], factor)))
        rate_a = pl.when(is_joint).then(
            bracket_rate(taxinc, _scaled(tables["joint_a"], factor))
        ).otherwise(bracket_rate(taxinc, _scaled(tables["single_a"], factor)))
        rate_b = pl.when(is_joint).then(
            bracket_rate(with_deduction, _scaled(tables["joint_b"], factor))
        ).otherwise(bracket_rate(with_deduction, _scaled(tables["single_b"], factor)))
        return pl.when(tax_a < tax_b).then(rate_a).otherwise(rate_b)
    if y <= 1998:
        return by_class(tables, taxinc, factor)
    return split_joint(tables, factor)


def _reported_taxable_income(taxinc: pl.Expr, fedtax: pl.Expr, status: pl.Expr, y: int) -> pl.Expr:
    """Account for `mnrate` mutating taxable income in 1985-1986."""
    p = YearParams(MN_PARAMS, y)
    if y not in (1985, 1986):
        return taxinc
    is_joint = status == "married_joint"
    factor = p.num("bracket_inflation")
    tables = p.value("brackets")
    with_deduction = (taxinc - fedtax).clip(0, None)
    tax_a = pl.when(is_joint).then(
        bracket_tax(taxinc, _scaled(tables["joint_a"], factor))
    ).otherwise(bracket_tax(taxinc, _scaled(tables["single_a"], factor)))
    tax_b = pl.when(is_joint).then(
        bracket_tax(with_deduction, _scaled(tables["joint_b"], factor))
    ).otherwise(bracket_tax(with_deduction, _scaled(tables["single_b"], factor)))
    return pl.when(tax_a < tax_b).then(taxinc).otherwise(with_deduction)


def _property_refund_1989plus(hhy: pl.Expr, y: int) -> pl.Expr:
    """Homeowner property tax refund (1989 on)."""
    p = YearParams(MN_PARAMS, y)
    aif2 = p.num("property_refund_inflation")
    income = hhy / aif2
    ptxo = pl.col("proptax")
    if p.in_effect("property_refund_clawback"):
        c = p.value("property_refund_clawback")
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
    else:
        refund_max = interpolate_table(income, p.value("property_refund_max")) * aif2
    threshold_pct = interpolate_table(income, p.value("property_refund_threshold"))
    copay = interpolate_table(income, p.value("property_refund_copay"))
    threshold = pl.min_horizontal(ptxo, threshold_pct * hhy / 100.0)
    refund = pl.min_horizontal(refund_max, (ptxo - threshold).clip(0, None) * (1.0 - copay)).clip(0, None)
    eligible = (ptxo > 0) & (hhy <= p.num("property_refund_owner_income_limit") * aif2)
    return pl.when(eligible).then(refund).otherwise(0.0)


def _property_refund_renter(hhy: pl.Expr, y: int) -> pl.Expr:
    """Renter property tax refund (1989 on); zero when not eligible."""
    p = YearParams(MN_PARAMS, y)
    aifr = p.num("property_refund_renter_inflation")
    income = hhy / aifr
    ptxr = p.num("property_refund_rent_share") * pl.col("rentpaid")
    maximum, threshold = p.value("property_refund_renter_max"), p.value("property_refund_renter_threshold")
    refund_max = interpolate_table(income, maximum) * aifr
    thres = pl.min_horizontal(ptxr, interpolate_table(income, threshold) * hhy / 100.0)
    copay = interpolate_table(income, p["property_refund_renter_copay"])
    refund = pl.min_horizontal(refund_max, (ptxr - thres).clip(0, None) * (1.0 - copay)).clip(0, None)
    return refund


def _taxsim_property_refund_2024(p: YearParams, fed_agi: pl.Expr, depx: pl.Expr, aged: pl.Expr) -> pl.Expr:
    """TAXSIM's 2024 on property tax refund: the larger of the homeowner and renter worksheet results."""

    def column(table: list[list[float]], index: int) -> list[list[float]]:
        return [[row[0], row[index]] for row in table]

    homestead = p.value("taxsim_homestead_table")
    income = (
        fed_agi
        - depx.clip(None, 5) * p.num("taxsim_homestead_dependent_subtraction")
        - aged * p.num("taxsim_homestead_aged_subtraction")
    ).clip(0, None)
    index = p.num("taxsim_homestead_inflation_index") / float(resolve_year(MN_PARAMS["taxsim_homestead_inflation_index"], 2024))
    owner_threshold = interpolate_table(income / index, column(homestead, 1)) * income
    owner = pl.min_horizontal(
        (pl.col("proptax") - owner_threshold).clip(0, None) * interpolate_table(income, column(homestead, 2)),
        interpolate_table(income, column(homestead, 3)),
    )
    owner = pl.when(pl.col("proptax") > 0).then(owner).otherwise(0.0)

    renter_table = p.value("taxsim_renter_table")
    dependents = p.value("taxsim_renter_dependent_subtraction")
    dependent_amount = pl.lit(0.0)
    for count, amount in enumerate(dependents):
        dependent_amount = pl.when(depx.clip(None, 5).floor() == count).then(float(amount)).otherwise(dependent_amount)
    renter_income = fed_agi.clip(0, None) - dependent_amount - pl.when(aged > 0).then(p.num("taxsim_renter_aged_subtraction")).otherwise(0.0)
    renter_rent = p.num("taxsim_renter_rent_share") * pl.col("rentpaid")
    renter = pl.min_horizontal(
        (renter_rent - renter_income * interpolate_table(renter_income, column(renter_table, 1))).clip(0, None)
        * interpolate_table(renter_income, column(renter_table, 2)),
        interpolate_table(renter_income, column(renter_table, 3)),
    )
    renter = pl.when(pl.col("rentpaid") > 0).then(renter).otherwise(0.0)
    return pl.max_horizontal(owner, renter)


def _property_credit_pre1989(hh: pl.Expr, y: int) -> pl.Expr:
    """Property tax credit before 1989, based on household income."""
    p = YearParams(MN_PARAMS, y)
    c = p.value("property_tax_credit")
    ptx = pl.col("proptax") + float(p["property_credit_rent_share"]) * pl.col("rentpaid")
    aged = aged_count() > 0
    if y >= 1987:
        if "threshold" in c:
            threshold = interpolate_table(hh, c["threshold"])
        else:
            threshold = (
                pl.when((pl.col("depx") > 0) | aged)
                .then(interpolate_table(hh, c["threshold_with_dependents"]))
                .otherwise(interpolate_table(hh, c["threshold_no_dependents"]))
            )
        copay = interpolate_table(hh, c["copay"])
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
        hh = pl.when(aged).then((hh - float(c["aged_income_reduction"])).clip(0, None)).otherwise(hh)
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
        copay = pl.when(aged).then(interpolate_table(hh, c["copay_aged"])).otherwise(
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

    shared = c
    c_aged = c["aged"]
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


def _phaseout_2022plus(y: int) -> dict:
    return YearParams(MN_PARAMS, y).value("phaseout_schedule")


def _status_value(values: dict, status: pl.Expr) -> pl.Expr:
    expr = pl.lit(float(values["single"]))
    for name in ("married_joint", "married_separate", "head_of_household"):
        expr = pl.when(status == name).then(float(values[name])).otherwise(expr)
    return expr


def _deduction_after_reduction_2022plus(
    amount: pl.Expr, agi: pl.Expr, status: pl.Expr, y: int, halve_separate: bool = True
) -> pl.Expr:
    """Standard or itemized deduction after Minnesota's AGI reduction."""
    d = _phaseout_2022plus(y)
    low = _status_value(d["low"], status)
    high = _status_value(d["high"], status)
    if not halve_separate:
        # TAXSIM applies the single thresholds to separate returns too.
        low = pl.when(status == "married_separate").then(float(d["low"]["single"])).otherwise(low)
        high = pl.when(status == "married_separate").then(float(d["high"]["single"])).otherwise(high)
    alternate = float(d["alternate_rate"]) * amount
    if d["alternate_applies"]:
        main = float(d["low_rate"]) * pl.min_horizontal((agi - low).clip(0, None), high - low) + float(
            d["high_rate"]
        ) * (agi - high).clip(0, None)
        reduction = pl.when(agi > float(d["alternate_income"])).then(alternate).otherwise(
            pl.min_horizontal(alternate, main)
        )
    else:
        reduction = pl.min_horizontal(alternate, float(d["low_rate"]) * (agi - low).clip(0, None))
    return (amount - reduction).clip(0, None)


def _childless_age_eligible(is_joint: pl.Expr, unknown_ages_eligible: bool) -> pl.Expr:
    """A taxpayer 19 to 64 (TAXSIM: a missing primary age is not, a missing spouse age on a joint return is 40)."""
    spouse_age = pl.when(is_joint & (pl.col("sage") == 0)).then(40.0).otherwise(pl.col("sage").cast(pl.Float64))
    ages = ((pl.col("page") >= 19) & (pl.col("page") <= 64)) | ((spouse_age >= 19) & (spouse_age <= 64))
    if unknown_ages_eligible:
        ages = ages | ((pl.col("page") == 0) & (pl.col("sage") == 0))
    return ages


def _statutory_working_family_credit(
    y: int, earned: pl.Expr, agi: pl.Expr, is_joint: pl.Expr, unknown_ages_eligible: bool = True
) -> pl.Expr:
    """Dated statutory credits. 2022: the pre-2023 working family credit by
    EITC child count. 2023+: the combined child and working families credit
    ($1,750 per child under 18 plus a 4% phase-in), reduced 12% above a threshold."""
    c = MN_PARAMS["child_and_working_family_credit"]
    ages = _childless_age_eligible(is_joint, unknown_ages_eligible)
    eligible = ((pl.col("depx") > 0) | ages) & (pl.col("filing_status") != "married_separate")
    income = pl.max_horizontal(earned, agi)
    if y == 2022:
        count = pl.col("dep18").clip(0, 3).cast(pl.Int64)

        def by_count(values: list[float]) -> pl.Expr:
            expr = pl.lit(float(values[-1]))
            for i in range(len(values) - 2, -1, -1):
                expr = pl.when(count == i).then(float(values[i])).otherwise(expr)
            return expr

        pre = c["pre_cwfc"]
        credit = pl.min_horizontal(earned.clip(0, None), by_count(pre["earnings_maximum"])) * by_count(pre["phase_in_rate"])
        threshold = pl.when(is_joint).then(by_count(pre["phase_out_threshold_joint"])).otherwise(by_count(pre["phase_out_threshold_other"]))
        reduction = (income - threshold).clip(0, None) * by_count(pre["phase_out_rate"])
        return pl.when(eligible).then((credit - reduction).clip(0, None)).otherwise(0.0)
    d = resolve_year(c["by_year"], y)
    if not unknown_ages_eligible:
        # TAXSIM (`m1cwfc`): no age or separate-return test, but nothing for a childless return with a taxpayer 65 or older;
        # children are those under 18.
        taxsim_base = float(d.get("child_amount", c["child_amount"])) * pl.col("dep18") + float(c["phase_in_rate"]) * earned.clip(0, float(d["phase_in_end"]))
        taxsim_d = resolve_year(c["taxsim_by_year"], y) if y >= 2024 and "taxsim_by_year" in c else d
        taxsim_threshold = pl.when(is_joint).then(float(taxsim_d["phase_out_joint"])).otherwise(float(taxsim_d["phase_out_other"]))
        taxsim_credit = (taxsim_base - float(c["phase_out_rate"]) * (income - taxsim_threshold).clip(0, None)).clip(0, None)
        if y >= 2024:
            # The 2024 on routine pays a childless return only with a reported taxpayer age 19 to 64.
            return pl.when((pl.col("depx") > 0) | _childless_age_eligible(is_joint, False)).then(taxsim_credit).otherwise(0.0)
        return pl.when((pl.col("depx") > 0) | (aged_count() == 0)).then(taxsim_credit).otherwise(0.0)
    base = float(d.get("child_amount", c["child_amount"])) * pl.col("dep17") + float(c["phase_in_rate"]) * earned.clip(0, float(d["phase_in_end"]))
    threshold = pl.when(is_joint).then(float(d["phase_out_joint"])).otherwise(float(d["phase_out_other"]))
    reduction = (income - threshold).clip(0, None) * float(c["phase_out_rate"])
    return pl.when(eligible).then((base - reduction).clip(0, None)).otherwise(0.0)


def _working_family_credit(
    base_earned: pl.Expr,
    base_agi: pl.Expr,
    fed_agi: pl.Expr,
    is_joint: pl.Expr,
    y: int,
    behavior: BehaviorProfile = TAXSIM_BEHAVIOR,
) -> pl.Expr:
    """Working Family Credit (refundable), 1991 on."""
    p = YearParams(MN_PARAMS, y)
    nkid = pl.col("depx").floor()
    eitc = pl.col("eitc")
    if y >= 2023 or (y == 2022 and behavior.use_minnesota_2022_working_family_credit_per_statute):
        credit = _statutory_working_family_credit(
            y, pl.col("earned_income"), pl.col("agi"), is_joint, behavior.count_unknown_ages_as_eligible_for_minnesota_childless_credit
        )
        # The credit needs the federal investment income test (Schedule M1CWFC): over the limit it is lost.
        return pl.when(pl.col("eitc_investment_disqualified") > 0).then(0.0).otherwise(credit)
    if y < 1991:
        return pl.lit(0.0)
    if p.in_effect("working_family_credit_federal_share"):
        share = p.num("working_family_credit_federal_share")
        return pl.when(eitc > 0).then(share * eitc).otherwise(0.0)

    wmax = pl.max_horizontal(base_earned, base_agi)

    def two_base(credit_fn, agi_test: pl.Expr) -> pl.Expr:
        return pl.when(fed_agi <= agi_test).then(credit_fn(base_earned)).otherwise(
            pl.min_horizontal(credit_fn(base_earned), credit_fn(base_agi))
        )

    if p.in_effect("working_family_credit_pieces"):
        c = p.value("working_family_credit_pieces")
        n0 = c["no_child"]

        def no_child(base: pl.Expr) -> pl.Expr:
            phase_out = (n0["maximum"] - n0["phase_out_rate"] * (base - n0["plateau_end"])).clip(0, None)
            if behavior.order_minnesota_1998_working_family_credit:
                return (
                    pl.when(base <= n0["phase_in_end"]).then(n0["phase_in_rate"] * base)
                    .when(base <= n0["plateau_end"]).then(pl.lit(float(n0["maximum"])))
                    .when(base < n0["phase_out_end"]).then(phase_out)
                    .otherwise(0.0)
                )
            # TAXSIM's three tests overwrite each other.
            credit = (
                pl.when(base <= n0["phase_in_end"])
                .then(n0["phase_in_rate"] * base)
                .otherwise(pl.lit(0.0))
            )
            credit = pl.when(base <= n0["plateau_end"]).then(pl.lit(float(n0["maximum"]))).otherwise(credit)
            return pl.when(base < n0["phase_out_end"]).then(phase_out).otherwise(credit)

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
        # TAXSIM skips the joint thresholds in 2012.
        use_joint = 2002 <= y <= 2013 if behavior.use_minnesota_2012_joint_working_family_thresholds else (
            2002 <= y <= 2011 or y == 2013
        )
        c0 = p["working_family_credit_no_child"]
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

        one_fn, one_out = with_children(p["working_family_credit_one_child"])
        two_fn, two_out = with_children(p["working_family_credit_two_children"])
        work = (
            pl.when(nkid < 1)
            .then(two_base(no_child, phase_out_0))
            .when(nkid == 1)
            .then(two_base(one_fn, one_out))
            .otherwise(two_base(two_fn, two_out))
        )
    else:
        c = p["working_family_credit_schedule"]

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
        if y >= 2019 or behavior.use_minnesota_working_family_tables_by_children:
            many = schedule("two_children", start("two_children", "two_children"))
            most = schedule("three_children", start("three_children", "three_children")) if y >= 2019 else None
        else:
            # TAXSIM starts the 2014-2018 phaseout at the one-child (and two-child joint) amounts.
            many = schedule("two_children", start("one_child", "two_children"))
            most = schedule("three_children", start("one_child", "two_children")) if y >= 2019 else None
        if y >= 2019:
            work = work.when(nkid == 2).then(many).otherwise(most)
            if not behavior.count_unknown_ages_as_eligible_for_minnesota_childless_credit:
                work = pl.when((nkid < 1) & ~_childless_age_eligible(is_joint, False)).then(0.0).otherwise(work)
        else:
            work = work.otherwise(many)

    if p.in_effect("working_family_credit_min_federal_eitc"):
        work = pl.when(eitc >= p.num("working_family_credit_min_federal_eitc")).then(work).otherwise(0.0)
    elif y >= 2019:
        # With the federal minimum gone, TAXSIM still loses the credit to the federal investment income test.
        work = pl.when(pl.col("eitc_investment_disqualified") > 0).then(0.0).otherwise(work)
    return work


def compute_mn_tax(df: pl.DataFrame, year: int, behavior: BehaviorProfile = TAXSIM_BEHAVIOR) -> pl.DataFrame:
    """Calculate Minnesota income tax for each row."""
    effective_year, flate = resolve_state_year(year, behavior, MN_PARAMS)
    y = effective_year
    p = YearParams(MN_PARAMS, effective_year)

    # Self-employment tax is not deflated in projected years.
    setax = pl.col("payroll_setax")
    hh_income = household_income(behavior)
    # Business and S corporation income are not deflated in projected years.
    business = pl.col("pbusinc") + pl.col("pprofinc") + pl.col("sbusinc") + pl.col("sprofinc") + pl.col("scorp")
    df = df.with_columns(mn_setax=setax, mn_household_income=hh_income, mn_business=business)
    df = deflate_for_extrapolation(df, flate, extra=("mn_household_income",))

    status = pl.col("filing_status")
    is_joint = files_joint()
    is_sep = files_separate()
    # The 2026 build reads the divisor from a status code it has already capped at 2, so a separate return
    # is never halved, and its filer class (`nfile`) is single.
    sep = pl.when(is_sep & pl.lit(behavior.double_minnesota_separate_returns)).then(2.0).otherwise(1.0)

    def by_filer(single: float, married: float, head_of_household: float) -> pl.Expr:
        return _by_filer(single, married, head_of_household, behavior.double_minnesota_separate_returns)

    txp = taxpayer_count()
    aged = aged_count()
    dependent_filer = is_dependent_filer()
    depx = pl.col("depx")
    hh = pl.col("mn_household_income")
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
        fed_zbr = federal_zero_bracket()
    agix = pl.col("agi").clip(0, None)
    alim50 = pl.lit(1.0e20) if y in (2020, 2021) else 0.5 * agix
    cash = pl.when(pl.col("agi") < 0).then(0.0).otherwise(pl.min_horizontal(pl.col("charity_cash"), alim50))
    fed_char = pl.min_horizontal(alim50, cash).clip(0, None)
    fed_agi = pl.col("agi")
    if y == 2020:
        fed_agi = fed_agi - pl.when(fed_itemizes).then(0.0).otherwise(
            pl.min_horizontal(p.num("charity_nonitemizer_addback") / sep, pl.col("charity_cash"))
        )

    # --- Minnesota AGI ---
    agi = fed_agi
    if y == 2009:
        agi = agi - untax + ui_total
    if y == 2020:
        agi = agi + ui_total - untax + pl.when(fed_itemizes).then(0.0).otherwise(
            pl.min_horizontal(p.num("charity_nonitemizer_addback"), pl.col("charity_cash"))
        )
    if p.in_effect("capital_gains_addback_rate"):
        agi = agi + p.num("capital_gains_addback_rate") * fullcg
    if 1979 <= y <= 1986:
        agi = agi - untax
    if 1982 <= y <= 1985:
        agi = agi + pl.col("pre1987_twoded")
    # Pension exclusion, reduced by federal AGI over a threshold.
    if y <= 1986:
        over = (fed_agi - p.num("pension_exclusion_income_threshold")).clip(0, None)
        penexc = pl.min_horizontal(pl.col("pensions"), (p.num("pension_exclusion_max") - over).clip(0, None))
        if y >= 1985:
            penexc = pl.when(aged > 0).then(penexc).otherwise(0.0)
        if behavior.apply_minnesota_pension_exclusion_through_1986_per_statute:
            # TAXSIM computes it but only subtracts it inside its 1988 on age-63 block, so never through 1986.
            agi = agi - penexc

    df, (agi, fed_agi) = checkpoint(df, mn_agi=agi, mn_fed_agi=fed_agi)

    # --- Deduction for federal income tax (through 1986) ---
    fedtax = pl.lit(0.0)
    if y <= 1986:
        seless = p.num("federal_tax_se_share")
        semax = p.num("federal_tax_se_cap")
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
        aif91 = p.num("phaseout_inflation") if y >= 2013 else 0.0
        mult = by_filing_status(p["phaseout_multiplier"])
        ph13 = p.num("phaseout_threshold_base") * aif91 * mult / sep if y >= 2013 else pl.lit(0.0)
        phded = p.num("phaseout_threshold_base") * aif91 / sep if y >= 2013 else pl.lit(0.0)
        if 2018 <= y <= 2019 and not behavior.start_minnesota_itemized_phaseout_without_status_multiplier_per_statute:
            # TAXSIM uses the exemption phase-out start (with its status multiplier) for the itemized deduction too.
            phded = ph13
        if y >= 2021 and not behavior.phase_out_minnesota_exemptions_per_statute:
            # TAXSIM (2021 on): start by capped status code (joint, head of household, other) scaled by its own factor.
            starts = p.value("taxsim_exemption_phaseout_start")
            ph13 = pl.when(is_joint | is_sep).then(float(starts["married"])).when(
                files_head_of_household()
            ).then(float(starts["head_of_household"])).otherwise(float(starts["single"])) * p.num("taxsim_exemption_phaseout_inflation")
            if p.in_effect("taxsim_exemption_phaseout_table"):
                # From 2024 (`mntax24`) the start comes from a table typed in the routine (2025 single has a typo).
                table = p.value("taxsim_exemption_phaseout_table")
                ph13 = pl.when(is_joint | is_sep).then(float(table["married"])).when(
                    files_head_of_household()
                ).then(float(table["head_of_household"])).otherwise(float(table["single"]))
    if y >= 2013:
        exemption_amount = p.num("exemption_amount")
        count = federal_exemption_count(y) if y <= 2018 else depx
        if 2013 <= y <= 2018 and not behavior.allow_minnesota_personal_exemption_2013_to_2018:
            # The build reads the exemption amount from a federal table it leaves empty for these years.
            count = count * 0.0
        exemp = count * exemption_amount
        excess = (fed_agi - ph13).clip(0, None)
        ratio = pl.min_horizontal(
            pl.lit(1.0),
            p.num("exemption_phaseout_rate") * excess / (p.num("exemption_phaseout_step") / sep),
        )
        exemp = exemp - ratio * exemp
        if y >= 2022 and behavior.phase_out_minnesota_exemptions_per_statute:
            d22 = _phaseout_2022plus(y)
            steps = phaseout_steps(
                (fed_agi - _status_value(d22["exemption_start"], pl.col("filing_status"))).clip(0, None)
                / _status_value(d22["exemption_step"], pl.col("filing_status")), behavior)
            exemp = (count * exemption_amount * (1 - float(d22["step_fraction"]) * steps)).clip(0, None)
        exemp = pl.when(dependent_filer).then(0.0).otherwise(exemp)

    # --- Standard deduction ---
    ag = agi.clip(0, None)
    if y <= 1986:
        cap = p.num("standard_deduction_cap") * p.num("standard_deduction_cap_inflation")
        cap_expr = pl.lit(cap) / sep if y >= 1985 else pl.lit(cap)
        stded = pl.min_horizontal(cap_expr, p.num("standard_deduction_percent") * ag)
    elif y <= 2017:
        stded = fed_zbr
        if p.in_effect("standard_deduction_married_reduction"):
            reduction = p.num("standard_deduction_married_reduction")
            stded = stded - pl.when(is_joint | is_sep).then(reduction / sep).otherwise(0.0)
        if y in (2008, 2009):
            property_cap = YearParams(FEDERAL_INCOME_TAX_PARAMS, y).num("standard_deduction_real_property_tax_cap")
            stded = stded - pl.min_horizontal(pl.col("proptax").clip(0, None), property_cap * txp)
    elif p.in_effect("standard_deduction_by_status"):
        by_status = dict(p.value("standard_deduction_by_status"))
        aged_by_status = dict(p.value("standard_deduction_aged_addition_by_status"))
        if not behavior.double_minnesota_separate_returns:
            # TAXSIM's capped status code makes a separate return a joint one here.
            by_status["married_separate"] = by_status["married_joint"]
            aged_by_status["married_separate"] = aged_by_status["married_joint"]
        stded = by_filing_status(by_status) + by_filing_status(aged_by_status) * aged
    else:
        table = p["standard_deduction_table"]
        aged_add = p["standard_deduction_aged_addition"]
        stded = (
            by_filer(
                _sub(table, "single", y), _sub(table, "married_joint", y), _sub(table, "head_of_household", y)
            )
            + by_filer(_sub(aged_add, "single", y), _sub(aged_add, "married", y), _sub(aged_add, "single", y)) * aged
        ) / sep
    taxsim_reduction = not behavior.reduce_minnesota_deductions_per_statute and p.in_effect("taxsim_deduction_reduction_start")
    if taxsim_reduction:
        # TAXSIM (2019-2022): 3% of federal AGI above its own start, at most 80% of the standard deduction.
        reduction_start = p.num("taxsim_deduction_reduction_start") / sep
        if y >= 2019:
            stded = (stded - pl.min_horizontal(0.03 * (fed_agi - reduction_start).clip(0, None), 0.8 * stded)).clip(0, None)
    elif y >= 2022:
        stded = _deduction_after_reduction_2022plus(
            stded, fed_agi, pl.col("filing_status"), y,
            halve_separate=y < 2023 or behavior.halve_minnesota_deduction_reduction_for_separate_returns_per_statute,
        )
    if y >= 2018:
        dependent_floor = p.num("dependent_standard_deduction_floor")
        dependent_addition = p.num("dependent_standard_deduction_earned_addition")
        if p.in_effect("taxsim_dependent_standard_deduction") and not behavior.use_published_minnesota_dependent_standard_deduction_per_statute:
            taxsim_dependent = p.value("taxsim_dependent_standard_deduction")
            dependent_floor, dependent_addition = float(taxsim_dependent["floor"]), float(taxsim_dependent["earned_addition"])
        dependent_limit = pl.max_horizontal(pl.lit(dependent_floor), pl.col("earned_income") + dependent_addition)
        stded = pl.when(dependent_filer).then(pl.min_horizontal(stded, dependent_limit)).otherwise(stded)

    # --- Itemized deductions ---
    if y <= 1986:
        xitded = pl.when(fed_itemizes).then((fed_deduc - salt_ded).clip(0, None)).otherwise(0.0)
    elif y <= 2012:
        xitded = pl.when(fed_itemizes).then(fed_deduc).otherwise(0.0)
    elif y <= 2017:
        deduc1 = fed_char + pl.col("proptax") + pl.col("otheritem") + pl.col("mortgage")
        dlim1 = p.num("itemized_phaseout_rate") * (fed_agi - ph13).clip(0, None)
        dlim2 = p.num("itemized_phaseout_max_share") * deduc1.clip(0, None)
        xitded = (deduc1 - pl.min_horizontal(dlim1, dlim2)).clip(0, None)
    elif y <= 2019:
        # TAXSIM leaves other itemized deductions out of the state and local tax add-back.
        other_taxes = pl.col("otheritem") if behavior.treat_minnesota_other_itemized_as_state_tax_2018_2019 else pl.lit(0.0)
        salt = pl.col("proptax") + salt_ded + other_taxes
        cap = p.num("salt_cap") / sep
        xitded = fed_deduc - pl.min_horizontal(cap, salt) + salt
        disalt = pl.min_horizontal((xitded - stded).clip(0, None), salt_ded + other_taxes)
        dedphs = pl.min_horizontal(
            p.num("itemized_phaseout_max_share") * xitded,
            p.num("itemized_phaseout_rate") * (fed_agi - phded),
        )
        alostd = disalt + dedphs
        exces = (xitded - stded).clip(0, None)
        remaining = pl.when(alostd <= exces).then((xitded - dedphs).clip(0, None)).otherwise(
            xitded - (exces - disalt).clip(0, None)
        )
        disagi = pl.when(fed_agi > phded).then(xitded - remaining).otherwise(0.0)
        xitded = xitded - (disalt + disagi)
    else:
        other_taxes = pl.col("otheritem") if behavior.treat_minnesota_other_itemized_as_state_tax_2018_2019 else pl.lit(0.0)
        sttax = pl.min_horizontal(p.num("salt_cap") / sep, pl.col("proptax") + salt_ded + other_taxes)
        stt = pl.min_horizontal(salt_ded, (sttax - (pl.col("proptax") + other_taxes)).clip(0, None))
        xitded = fed_deduc - stt
        dedphs = pl.min_horizontal(
            p.num("itemized_phaseout_max_share") * fed_deduc,
            p.num("itemized_phaseout_rate") * (fed_agi - phded),
        )
        if not taxsim_reduction:
            xitded = xitded - pl.when(fed_agi - phded > 0).then(dedphs).otherwise(0.0)
        if y >= 2022 and not taxsim_reduction:
            # Statutory reduction replaces the historical phase-out above.
            xitded = _deduction_after_reduction_2022plus(fed_deduc - stt, fed_agi, pl.col("filing_status"), y)

    if taxsim_reduction:
        # TAXSIM's itemized deduction limitation (2018-2022): 3% of federal AGI above its start, at most 80% of the
        # federal itemized deductions.
        limitation = pl.min_horizontal(0.8 * fed_deduc.clip(0, None), 0.03 * (fed_agi - p.num("taxsim_deduction_reduction_start")).clip(0, None))
        xitded = (xitded - limitation.clip(0, None)).clip(0, None)
    if y >= 2024 and not behavior.limit_minnesota_itemized_deduction_from_2024_per_statute:
        # TAXSIM's 2024 on routine: the federal total less property and other taxes over the federal SALT limit (the
        # state tax estimate stays in), cut by 3% and 10% of AGI over two tiers, or 80% above a top level.
        t24 = p.value("taxsim_itemized_deduction")
        over_limit = (pl.col("proptax") - float(t24["salt_limit"])).clip(0, None)
        base = pl.col("itemized_before_limit") - over_limit
        first = (fed_agi - float(t24["start"])).clip(0, float(t24["range"]))
        second = (fed_agi - float(t24["start"]) - float(t24["range"])).clip(0, None)
        reduction = pl.min_horizontal(0.03 * first + 0.1 * second, 0.8 * base)
        reduction = pl.when(fed_agi > float(t24["top"])).then(0.8 * base).otherwise(reduction)
        # Minnesota lets a filer itemize whatever the federal choice (Form M1 instructions), and so does TAXSIM.
        xitded = (base - reduction).clip(0, None)
    df, (stded, xitded, exemp) = checkpoint(df, mn_stded=stded, mn_xitded=xitded, mn_exemp=exemp)
    deduc = pl.max_horizontal(stded, xitded)
    taxinc = agi - deduc - exemp

    # --- Addition for state income tax deducted federally ---
    if y <= 2017:
        if y >= 1988:
            table = p["standard_deduction_table"]
            stnd = by_filer(
                _sub(table, "single", y), _sub(table, "married_joint", y), _sub(table, "head_of_household", y)
            ) / sep
            aged_add = p["standard_deduction_aged_addition"]
            oldd = by_filer(_sub(aged_add, "single", y), _sub(aged_add, "married", y), _sub(aged_add, "single", y)) * aged
            add = pl.min_horizontal((fed_deduc - stnd - oldd).clip(0, None), salt_ded)
        else:
            add = pl.when(fed_deduc - salt_ded < fed_zbr).then(fed_deduc - fed_zbr).otherwise(salt_ded)
        add = pl.when(fed_itemizes).then(add).otherwise(0.0)
    else:
        add = pl.lit(0.0)

    # Subtraction for the elderly, less nontaxable Social Security.
    subrac = pl.lit(0.0)
    if p.in_effect("elderly_subtraction"):
        eld = p.value("elderly_subtraction")
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
            p.num("charity_subtraction_rate") * (cash - p.num("charity_subtraction_floor")).clip(0, None)
        ).otherwise(0.0)
    if not behavior.subtract_minnesota_elderly_and_charity_from_income:
        # The 2026 build computes the charitable subtraction and, through 2023, the elderly one, and never applies them. From
        # 2024 (`mntax24`) it applies the elderly subtraction from its own formula: a maximum (9,600 for a single return, 12,000
        # otherwise, including a head of household and a separate return) less nontaxable Social Security and half
        # of federal AGI over 14,500 (18,000 for a joint return with two aged filers).
        subrac = pl.lit(0.0)
        if y >= 2024:
            # The routine caps its status code at 2 before it halves for separate returns, so those are not halved.
            start = pl.when(is_joint & (aged >= 2)).then(18000.0).otherwise(14500.0)
            maximum = pl.when(pl.col("filing_status") == "single").then(9600.0).otherwise(12000.0)
            nontaxable = pl.col("gssi") - pl.col("taxable_social_security")
            taxsim_elderly = (maximum - nontaxable - 0.5 * (fed_agi - start).clip(0, None)).clip(0, None)
            subrac = pl.when(aged > 0).then(taxsim_elderly).otherwise(0.0)
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
        subtraction = pl.min_horizontal(ss_taxable, allowed)
        if y >= 2023:
            full_threshold = pl.when(single_class).then(_sub(p["social_security_full_subtraction_threshold"], "single", y)).otherwise(
                _sub(p["social_security_full_subtraction_threshold"], "married", y)
            )
            steps = phaseout_steps(((fed_agi - full_threshold) / (4000.0 / sep)).clip(0, None), behavior)
            full = (ss_taxable * (1.0 - 0.1 * steps)).clip(0, None)
            subtraction = pl.max_horizontal(subtraction, full)
        has_benefit = ss_taxable > 0
        if y >= 2024 and not behavior.subtract_minnesota_social_security_without_age_from_2024:
            # TAXSIM's 2024 on routine takes it only with a taxpayer aged 62 or more (it also tests pensions in dollars).
            has_benefit = has_benefit & ((pl.col("page") >= 62) | (pl.col("pensions") >= 62) | (aged > 0))
        subrac = subrac + pl.when(has_benefit).then(subtraction).otherwise(0.0)
    taxinc = taxinc - subrac + add

    if 2011 <= y <= 2013:
        ph = p.num("m1m_addback_threshold")
        table = p["standard_deduction_table"]
        stdmn = by_filer(
            _sub(table, "single", y), _sub(table, "married_joint", y), _sub(table, "head_of_household", y)
        ) / sep
        xl9 = pl.min_horizontal(
            p.num("itemized_phaseout_max_share") * fed_deduc.clip(0, None),
            p.num("itemized_phaseout_rate") * (fed_agi - ph / sep).clip(0, None),
        )
        xl14 = (fed_deduc - stdmn).clip(0, None)
        xl15 = pl.when(add + xl9 <= xl14).then(xl9).otherwise((xl14 - add).clip(0, None))
        taxinc = taxinc + pl.when(fed_itemizes & (fed_agi > ph)).then(xl15).otherwise(0.0)

        phamex = ph * by_filing_status(p["phaseout_multiplier"]) / sep
        over = fed_agi - phamex
        addxmp = pl.when(over <= p.num("m1m_exemption_phaseout_range") / sep).then(
            p.num("exemption_phaseout_rate") * over / (p.num("exemption_phaseout_step") / sep)
            * pl.col("personal_exemptions")
        ).otherwise(pl.col("personal_exemptions"))
        taxinc = taxinc + pl.when(over > 0).then(addxmp).otherwise(0.0)

    df, (taxinc,) = checkpoint(df, mn_taxinc=taxinc.clip(0, None))
    if y <= 1986:
        taxinc = (agi - deduc - exemp).clip(0, None)
    df, (statax,) = checkpoint(df, mn_regular_tax=_schedule_tax(
        taxinc, fedtax, status, y, behavior.double_minnesota_separate_returns, behavior.use_minnesota_published_brackets_from_2022
    ))

    # --- Alternative minimum tax ---
    if y >= 2001 and (y < 2024 or behavior.charge_minnesota_alternative_minimum_tax_from_2024):
        char_pref = (fed_char - p.num("amt_charity_floor") * fed_agi.clip(0, None)).clip(0, None)
        alminy = fed_agi - char_pref
        excl = by_filer(*(_sub(p["amt_exclusion"], k, y) for k in ("single", "married", "head_of_household"))) / sep
        phase = by_filer(*(float(p["amt_phaseout_start"][k]) for k in ("single", "married", "head_of_household"))) / sep
        alminc = (
            alminy - (excl - p.num("amt_phaseout_rate") * (alminy - phase).clip(0, None)).clip(0, None)
        ).clip(0, None)
        statax = statax + (p.num("amt_rate") * alminc - statax).clip(0, None)

    df, (statax,) = checkpoint(df, mn_tax_after_amt=statax)

    # --- Nonrefundable credits ---
    credit = pl.lit(0.0)
    if p.in_effect("personal_credit_aged"):
        pca = p.value("personal_credit_aged")
        credit = p.num("personal_credit") * (txp + depx) + pca["per_aged"] * aged + pca["extra_aged"] * (aged - txp).clip(0, None)
    elif y <= 1986:
        credit = (txp + depx + aged) * p.num("personal_credit")
    if p.in_effect("homemaker_credit"):
        hc = p.value("homemaker_credit")
        earnings_limit = float(hc["earnings_limit"])
        # TAXSIM pays it only to dependent filers (a reversed test).
        filer_test = ~dependent_filer if behavior.pay_minnesota_homemaker_credit_to_nondependents else dependent_filer
        hcred = pl.when(
            filer_test
            & (depx >= 1)
            & (pl.col("pwages") <= earnings_limit)
            & (pl.col("swages") <= earnings_limit)
            & ~is_sep
            & (fed_agi <= float(hc["agi_limit"]) / sep)
        ).then(float(hc["amount"])).otherwise(0.0)
        credit = credit + hcred
    if p.in_effect("elderly_credit_share"):
        credit = credit + p.num("elderly_credit_share") * pl.col("federal_elder")
    if p.in_effect("low_income_credit_rate"):
        npop = (txp + depx).clip(None, 6).floor()
        limits = p.value("low_income_credit_threshold")
        ylow = pl.lit(0.0)
        for index, limit in enumerate(limits, start=1):
            ylow = pl.when(npop == index).then(pl.lit(float(limit))).otherwise(ylow)
        ytest = (p.num("low_income_credit_rate") * (hh - ylow)).clip(0, None)
        crlow = (statax - credit - ytest).clip(0, None)
        credit = credit + pl.when((fed_agi <= p.num("low_income_credit_agi_limit")) & ~dependent_filer).then(crlow).otherwise(0.0)
    if y >= 1999:
        d17 = pl.col("psemp") + pl.col("ssemp") + pl.col("pwages").clip(None, 0) + pl.col("swages").clip(None, 0)
        other_income = pl.lit(0.0)
        if y >= 2000:
            # From 2000 business income, pensions and taxable Social Security count too.
            d17 = d17 + pl.col("mn_business")
            other_income = pl.col("pensions") + pl.col("taxable_social_security")
        earn = pl.min_horizontal(pl.col("pwages"), pl.col("swages")) + p.num("marriage_credit_self_employment_share") * (
            other_income + d17.clip(0, None) - p.num("marriage_credit_self_employment_tax_share") * pl.col("mn_setax")
        )
        if not behavior.compute_minnesota_marriage_credit_earnings_per_statute:
            # TAXSIM (`crmarr1`): each spouse's wages, half of pensions and taxable benefits, and 92.35% of
            # business income, which loses a count of older dependents (primary) or of children under 6 (spouse).
            share = p.num("taxsim_marriage_credit_business_share")
            half_pensions = 0.5 * (pl.col("pensions") + pl.col("taxable_social_security"))
            older_dependents = (pl.col("depx") - pl.col("dep18")).clip(0, None)
            business_primary = pl.col("psemp") + pl.col("pbusinc") + pl.col("pprofinc") - older_dependents
            business_spouse = pl.col("ssemp") + pl.col("sbusinc") + pl.col("sprofinc") - pl.col("dep6")
            earn = pl.min_horizontal(
                pl.col("pwages") + half_pensions + share * business_primary,
                pl.col("swages") + half_pensions + share * business_spouse,
            )
        if y <= 2017:
            # TAXSIM reads the federal exemption and zero bracket arrays before they are filled: both 0.
            xl10 = p.num("exemption_amount") + p.num("marriage_credit_standard_deduction_share") * p.num(
                "federal_joint_standard_deduction"
            )
            if not behavior.compute_minnesota_marriage_credit_earnings_per_statute:
                xl10 = pl.lit(0.0)
        else:
            xl10 = p.num("marriage_credit_lower_earner_deduction")
            if y >= 2019 and not behavior.compute_minnesota_marriage_credit_earnings_per_statute:
                xl10 = stded / 2
        xl11 = (earn - xl10).clip(0, None)
        single = pl.lit("single")
        x1 = _schedule_tax(xl11, fedtax, single, y, True, behavior.use_minnesota_published_brackets_from_2022)
        x2 = _schedule_tax((taxinc - xl11).clip(0, None), fedtax, single, y, True, behavior.use_minnesota_published_brackets_from_2022)
        credit_max, min_earnings, min_taxinc = p.num("marriage_credit_max"), p.num("marriage_credit_min_earnings"), p.num("marriage_credit_min_taxable_income")
        if not behavior.compute_minnesota_marriage_credit_earnings_per_statute:
            # TAXSIM's typed 2024-25 amounts are stale or off by one against the published ones (Schedule M1MA).
            credit_max = p.num("taxsim_marriage_credit_max") if p.in_effect("taxsim_marriage_credit_max") else credit_max
            min_earnings = p.num("taxsim_marriage_credit_min_earnings") if p.in_effect("taxsim_marriage_credit_min_earnings") else min_earnings
            min_taxinc = p.num("taxsim_marriage_credit_min_taxable_income") if p.in_effect("taxsim_marriage_credit_min_taxable_income") else min_taxinc
        statm = pl.when((taxinc < min_taxinc) | (earn < min_earnings)).then(0.0).otherwise(statax)
        crmar = pl.min_horizontal(pl.lit(credit_max), (statm - (x1 + x2)).clip(0, None))
        credit = credit + pl.when(is_joint).then(crmar).otherwise(0.0)

    df, (credit,) = checkpoint(df, mn_credit=credit)

    # --- Working Family Credit ---
    base_agi = fed_agi + (ui_total - untax if y == 2020 and behavior.add_back_minnesota_unemployment_exclusion_to_working_family_income_per_statute else 0.0)
    work = _working_family_credit(pl.col("earned_income"), base_agi, fed_agi, is_joint, y, behavior)
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
    cded = p.num("child_care_phaseout_start")
    if p.in_effect("child_care_expense_credit"):
        c13 = p.value("child_care_expense_credit")
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
    elif y <= 2016 and not behavior.read_minnesota_child_care_from_federal_credit:
        # The 2026 build takes its share of the federal credit's qualifying expenses (the input itself before 1987).
        expense = pl.col("childcare").clip(0, None) if y <= 1986 else pl.col("ccc_expense")
        cost = p.num("child_care_share_of_federal") * expense
    elif y <= 1986 and not behavior.read_federal_child_care_credit_before_1987:
        # TAXSIM reads a federal figure its early routine never fills.
        cost = pl.lit(0.0)
    elif y <= 1986:
        cost = p.num("child_care_share_of_federal") * pl.col("federal_chcr")
    else:
        cost = p.num("child_care_share_of_federal") * pl.col("ccc_uncapped") / flate
    phaseout_rate = p.num("child_care_phaseout_rate")
    if y >= 2017:
        over = (fed_agi - cded).clip(0, None)
        if behavior.limit_minnesota_child_care_credit_by_maximum_after_2017:
            # Minn. Stat. 290.067 subd. 2b: above the threshold, the lesser of
            # the credit and the maximum less the phaseout.
            chcr = pl.when(over <= 0).then(cost).otherwise(
                pl.min_horizontal(cost, (cmax - phaseout_rate * over).clip(0, None))
            )
        else:
            # TAXSIM pays the whole credit within the range above the threshold, then the lesser of it and the
            # maximum less the phaseout.
            range_ = p.num("taxsim_child_care_full_credit_range")
            chcr = pl.when(over <= range_).then(cost).otherwise(
                pl.min_horizontal(cost, (cmax - phaseout_rate * over).clip(0, None))
            )
        eligible = fed_agi <= cymax
        if y >= 2021 and not behavior.compute_minnesota_child_care_credit_from_2021_per_statute:
            # TAXSIM (2021 on): a credit on the expenses of the children under 13 (at most $3,000 each for two), at
            # 35% falling a point for each $2,000 of AGI above $15,000 to 20%, limited above the threshold.
            c21 = p.value("taxsim_child_care_credit_expense_based")
            earn_primary, earn_spouse = net_earnings_by_spouse(behavior)
            under13 = pl.when(pl.col("dep13") > 0).then(pl.col("dep13")).otherwise(pl.col("depx"))
            cost21 = pl.min_horizontal(pl.col("childcare"), under13.clip(None, 2) * float(c21["expense_per_child"]))
            exps = pl.when(is_joint).then(pl.min_horizontal(cost21, earn_primary, earn_spouse)).otherwise(
                pl.min_horizontal(cost21, earn_primary)
            )
            steps = phaseout_steps(((fed_agi - float(c21["rate_step_start"])) / float(c21["rate_step"])).clip(0, None), behavior)
            frac = pl.max_horizontal(pl.lit(float(c21["rate_floor"])), float(c21["rate_top"]) - float(c21["rate_decrement"]) * steps)
            chcr = frac * exps
            limited = (float(c21["limited_credit_per_child"]) * under13.clip(None, 2) - phaseout_rate * over).clip(0, None)
            chcr = pl.when(fed_agi > cded).then(pl.min_horizontal(chcr, limited)).otherwise(chcr)
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

            dep_sub = by_kid(p.value("property_refund_dependent_subtraction"))
            if p.in_effect("property_refund_aged_subtraction"):
                aged_sub = by_kid(p.value("property_refund_aged_subtraction"))
            else:
                aged_sub = dep_sub + p.num("exemption_amount")
            hhy = (hhy - pl.when(aged > 0).then(aged_sub).otherwise(dep_sub)).clip(0, None)
        df, (hhy,) = checkpoint(df, mn_refund_income=hhy)
        renter = (pl.col("rentpaid") > 0) & (
            hhy <= p.num("property_refund_renter_income_limit") * p.num("property_refund_renter_inflation")
        )
        renter_refund = pl.when(renter).then(_property_refund_renter(hhy, y)).otherwise(0.0)
        if behavior.allow_minnesota_renter_and_homeowner_refunds:
            # A household that rented and owned claims both (Form M1PR).
            pcred = renter_refund + _property_refund_1989plus(hhy, y)
        else:
            # TAXSIM gives qualifying renters the renter refund in place of
            # the homeowner refund.
            pcred = pl.when(renter).then(renter_refund).otherwise(_property_refund_1989plus(hhy, y))
    else:
        pcred = _property_credit_pre1989(hh, y)
    if effective_year >= 2024 and not behavior.compute_minnesota_property_tax_refund_per_statute:
        pcred = _taxsim_property_refund_2024(p, fed_agi, depx, aged)
    if effective_year <= 2023 and not behavior.pay_minnesota_property_tax_refund_through_2023:
        # The 2026 build dropped the property tax refund through 2023 ("it isn't on the M1 form").
        pcred = pl.lit(0.0)
    if not (effective_year >= 2024 and not behavior.compute_minnesota_property_tax_refund_per_statute):
        pcred = pl.when(dependent_filer).then(0.0).otherwise(pcred)
    df, (chcr, pcred) = checkpoint(df, mn_chcr=chcr, mn_pcred=pcred.clip(0, None))

    # --- 2009 motor fuels credit ---
    crfuel = pl.lit(0.0)
    if p.in_effect("motor_fuels_credit"):
        fuel = p.value("motor_fuels_credit")
        limits = fuel["taxable_income_limit"]
        fuel_sep = sep if behavior.halve_minnesota_motor_fuels_credit_for_separate_returns_per_statute else 1.0
        limit = _by_filer(limits["single"], limits["married"], limits["head_of_household"]) / fuel_sep
        crfuel = pl.when(taxinc <= limit).then(fuel["amount"] / fuel_sep).otherwise(0.0)

    statax = (statax - credit).clip(0, None) - chcr - work - pcred - crfuel
    rate_expr = _schedule_rate(
        taxinc, fedtax, status, y, behavior.double_minnesota_separate_returns, behavior.use_minnesota_published_brackets_from_2022
    )
    if y <= 1986:
        first_taxinc = pl.col("mn_taxinc")
        first_tax = _schedule_tax(first_taxinc, fedtax, status, y, True, behavior.use_minnesota_published_brackets_from_2022)
        political_tax = _schedule_tax(taxinc, fedtax, status, y, True, behavior.use_minnesota_published_brackets_from_2022)
        rate_expr = pl.when(first_tax > political_tax).then(
            _schedule_rate(
                first_taxinc, fedtax, status, y, behavior.double_minnesota_separate_returns, behavior.use_minnesota_published_brackets_from_2022
            )
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
