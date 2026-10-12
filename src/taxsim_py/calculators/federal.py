"""Federal individual income tax calculator."""


from __future__ import annotations
import polars as pl

from taxsim_py.behavior import BehaviorProfile, TAXSIM_BEHAVIOR
from taxsim_py.calculators.payroll import payroll_parts, payroll_prerequisites
from taxsim_py.engine.amt import alternative_minimum_tax, separate_return_amt_income
from taxsim_py.engine.brackets import bracket_rate, bracket_rate_by_status, bracket_tax, bracket_tax_by_status
from taxsim_py.engine.capital_gains import preferential_rate_tax
from taxsim_py.engine.credits import child_care_credit_rate
from taxsim_py.engine.eitc import eitc_age_eligible, eitc_filer_eligible, trapezoid_credit
from taxsim_py.engine.inputs import aged_count, files_joint, files_separate, filing_status, is_dependent_filer
from taxsim_py.engine.niit import net_investment_income_tax
from taxsim_py.engine.social_security import taxable_social_security
from taxsim_py.engine.schema import PARAMETERS_ROOT, YearParams, load_yaml, resolve_year, validate_brackets
from taxsim_py.engine.state import (
    FORCE_ITEMIZE,
    by_filing_status,
    dividend_input_adjustment,
    itemize_choice,
    unemployment_total,
    with_default,
    with_defaults,
)

# Parameters use published 2023 EITC law rather than TAXSIM's extrapolated
# 2022 values. See the validation documentation for the known divergence.
FEDERAL_INCOME_TAX_PARAMS = load_yaml(PARAMETERS_ROOT / "national" / "income_tax.yaml")
FEDERAL_EITC_PARAMS = pl.read_csv(PARAMETERS_ROOT / "national" / "eitc.csv")
FEDERAL_EITC_PARAMS_MISC = load_yaml(PARAMETERS_ROOT / "national" / "eitc_misc.yaml")
FEDERAL_CREDITS_PARAMS = load_yaml(PARAMETERS_ROOT / "national" / "credits.yaml")
FEDERAL_ITEMIZED_PARAMS = load_yaml(PARAMETERS_ROOT / "national" / "itemized.yaml")
FEDERAL_AMT_PARAMS = load_yaml(PARAMETERS_ROOT / "national" / "amt.yaml")
ANALYTIC_RATE_PARAMS = load_yaml(PARAMETERS_ROOT / "national" / "analytic_rate.yaml")
FEDERAL_NIIT_PARAMS = load_yaml(PARAMETERS_ROOT / "national" / "niit.yaml")
PAYROLL_TAX_PARAMS = load_yaml(PARAMETERS_ROOT / "national" / "payroll_tax.yaml")
FEDERAL_CAPITAL_GAINS_PARAMS = load_yaml(PARAMETERS_ROOT / "national" / "capital_gains.yaml")
FEDERAL_PERSONAL_EXEMPTION_PARAMS = load_yaml(PARAMETERS_ROOT / "national" / "personal_exemption.yaml")
SOCIAL_SECURITY_PARAMS = load_yaml(PARAMETERS_ROOT / "national" / "social_security.yaml")

FILING_STATUSES = ["single", "married_joint", "married_separate", "head_of_household"]
# Married-separate returns use half of joint thresholds where applicable.
SEPRET_BY_STATUS = {
    "single": 1.0,
    "married_joint": 1.0,
    "head_of_household": 1.0,
    "married_separate": 2.0,
}


def _qbi_deduction(year: int, dividends: pl.Expr, capgn: pl.Expr) -> pl.Expr:
    """Qualified business income deduction (TAXSIM `dedbus`).

    Non-professional business income (net of half its self-employment tax)
    earns the full rate; professional income and S corporation income phase
    out over the taxable-income range. The total is capped at the rate times
    taxable income less net capital gain and dividends.
    """
    p = YearParams(FEDERAL_INCOME_TAX_PARAMS, year)
    rate = p.num("qbi_deduction_rate")
    married = files_joint()

    def by_marriage(key: str) -> pl.Expr:
        amounts = p.value(key)
        value = pl.when(married).then(float(amounts["married_joint"])).otherwise(float(amounts["unmarried"]))
        if "married_separate" in amounts:
            value = pl.when(files_separate()).then(float(amounts["married_separate"])).otherwise(value)
        return value

    start, end = by_marriage("qbi_phase_in_start"), by_marriage("qbi_phase_in_end")
    taxable = pl.col("taxable_income")
    qbi_income = pl.col("pbusinc").clip(0, None) + pl.col("sbusinc").clip(0, None)
    professional = pl.col("pprofinc").clip(0, None) + pl.col("sprofinc").clip(0, None)
    phased_share = 1 - ((taxable - start) / (end - start)).clip(0, 1)
    sstb_income = pl.col("scorp") + pl.when(professional > 0).then(
        (professional - 0.5 * pl.col("setax_sstb")).clip(0, None)
    ).otherwise(0.0)
    sstb = pl.when((professional > 0) | (pl.col("scorp") > 0)).then(rate * sstb_income * phased_share).otherwise(0.0)
    qbi = pl.when(qbi_income > 0).then(rate * (qbi_income - 0.5 * pl.col("setax_qbi")).clip(0, None)).otherwise(0.0)
    cap = rate * (taxable - (capgn.clip(0, None) + dividends)).clip(0, None)
    return pl.min_horizontal(sstb + qbi, cap)


def _law87_analytic_rate(
    year: int,
    brackets_by_status: dict[str, list[list[float]]],
    sepret: pl.Expr,
    behavior: BehaviorProfile = TAXSIM_BEHAVIOR,
) -> pl.Expr:
    """TAXSIM's analytic marginal rate (`comnew(72)`, percent) for 1987-2000.

    States read this rate, not the finite-difference `frate`. It combines the
    bracket rate of TAXSIM's tax routine with the slopes of the phase-ins
    and phase-outs the record is in, as TAXSIM's law87 does.
    """
    a = YearParams(ANALYTIC_RATE_PARAMS, year)
    status = pl.col("filing_status")

    def by_status(build) -> pl.Expr:
        expr = pl.lit(None, dtype=pl.Float64)
        for name, brackets in brackets_by_status.items():
            expr = pl.when(status == name).then(build(brackets)).otherwise(expr)
        return expr

    def rate_at(income: pl.Expr) -> pl.Expr:
        # TAXSIM's lookup: no tax, no rate; a threshold belongs to the lower bracket.
        return by_status(lambda b: pl.when(income > 0).then(bracket_rate(income, b)).otherwise(0.0))

    def threshold(index: int) -> pl.Expr:
        return by_status(lambda b: pl.lit(float(b[index][0]) if index < len(b) else 1.0e29))

    taxinc = pl.col("taxable_income")
    cglong = pl.min_horizontal(pl.col("ltcg"), pl.col("stcg") + pl.col("ltcg")).clip(0, None)
    lowest_rate = by_status(lambda b: pl.lit(float(b[0][1])))
    cap = a["gains_maximum_rate"]
    if year == 1987:
        # Gains are taxed at most 28%: the top of the 28% bracket.
        ttab = threshold(3)
        faster = pl.when(taxinc < ttab).then(taxinc).otherwise(taxinc - cglong)
        rate = rate_at(faster)
        rate = pl.when((cglong > 0) & (rate == 0)).then(cap).otherwise(rate)
        capped = (taxinc >= ttab) & (taxinc - cglong <= ttab)
        regrat = pl.when(capped).then(pl.when(taxinc > ttab).then(cap).otherwise(0.0)).otherwise(rate)
    elif year <= 1990 or (year == 1993 and not behavior.correct_analytic_federal_rate):
        # TAXSIM's 1993 gains adjustment does not reach the returned rate.
        regrat = rate_at(taxinc)
    elif year <= 1992:
        bot28, top28 = threshold(1), threshold(2)
        gains = (pl.col("ltcg") > 0) & (cglong > 0) & (taxinc > top28)
        regrat = (
            pl.when(gains & (taxinc - cglong < bot28)).then(cap)
            .when(gains).then(rate_at(pl.max_horizontal(taxinc - cglong, bot28)))
            .otherwise(rate_at(taxinc))
        )
    elif year <= 1996:
        bot28 = threshold(1)
        taxin3 = pl.max_horizontal(bot28, taxinc - cglong)
        alternative = by_status(lambda b: bracket_tax(taxin3, b)) + cap * (taxinc - taxin3).clip(0, None)
        cheaper = alternative < by_status(lambda b: bracket_tax(taxinc, b))
        below = taxinc - cglong < bot28
        gains_rate = (
            pl.when(below & cheaper).then(cap)
            .when(below).then(lowest_rate)
            .otherwise(rate_at(taxin3))
        )
        cglong = pl.when(pl.col("ltcg") > 0).then(cglong).otherwise(0.0)
        if behavior.correct_analytic_federal_rate:
            # The capped rate applies only when the capped computation is cheaper.
            gains_rate = pl.when(cheaper).then(pl.when(below).then(cap).otherwise(rate_at(taxin3))).otherwise(
                rate_at(taxinc)
            )
        regrat = pl.when(cglong > 0).then(gains_rate).otherwise(rate_at(taxinc))
    else:
        regrat = rate_at((taxinc - pl.col("ltg").clip(0, None)).clip(0, None))
    rgrate = 100.0 * regrat

    dagidw = pl.col("analytic_dagidw")
    itemizes = pl.col("itemizes")
    rded = pl.lit(0.0)
    rexem = pl.lit(0.0)
    if year >= 1991:
        rded = pl.when(pl.col("analytic_pded") & itemizes).then(a["itemized_limitation_rate"] * rgrate).otherwise(0.0)
        pep_slope = a["exemption_phaseout_rate"] * pl.col("analytic_exemptions_base") / (a["exemption_phaseout_step"] / sepret)
        rexem = pl.when(pl.col("analytic_pexem")).then(dagidw * rgrate * pep_slope).otherwise(0.0)
    rsave = pl.col("analytic_rsave") if 1988 <= year <= 1990 else pl.lit(0.0)
    rxmp = pl.col("analytic_rxmp") if 1988 <= year <= 1996 else pl.lit(0.0)

    # EITC slope (`reic`), set before TAXSIM's eligibility tests, so it
    # applies whether or not the credit survives them.
    earned = pl.col("earned_income")
    income = pl.max_horizontal(pl.col("analytic_eitc_income"), earned)
    rtbs, crm, ym, rtlw = pl.col("rate_in"), pl.col("max_credit"), pl.col("phaseout_start"), pl.col("rate_out")
    phase_in_credit = pl.min_horizontal(rtbs * earned, crm)
    phased = pl.min_horizontal(phase_in_credit, (crm - rtlw * (income - ym)).clip(0, None))
    files = (status != "married_separate") & ~is_dependent_filer() & rtbs.is_not_null()
    if behavior.correct_analytic_federal_rate:
        # No slope where the eligibility tests remove the credit.
        files = files & ~((phased > 0) & (pl.col("eitc") <= 0))
    reic = (
        pl.when(files & (income > ym) & (phased > 0)).then(rtlw * dagidw)
        .when(files & (phase_in_credit < crm) & (income < ym)).then(-rtbs * dagidw)
        .otherwise(0.0)
    ) * 100.0
    peic = reic != 0

    first_share = a["social_security_first_tier_share"]
    second_share = a["social_security_second_tier_share"]
    pssa = pl.col("analytic_pssa")
    if year <= 1993:
        rssa = pl.when(pssa).then(first_share * rgrate).otherwise(0.0)
    else:
        pssa1 = pl.col("analytic_pssa1")
        pssa2 = (dagidw > 1.0) & ~pssa1
        rssa = (
            pl.when(pssa & peic & pssa1).then(second_share * rgrate)
            .when(pssa & peic & pssa2).then(first_share * rgrate)
            .when(pssa & ~peic).then(first_share * rgrate)
            .otherwise(0.0)
        )

    rchild = pl.lit(0.0)
    if year <= 2002:
        agi = pl.col("agi")
        phasing = (agi > a["child_care_rate_agi_start"]) & (agi < a["child_care_rate_agi_end"])
        expense = pl.col("analytic_ccc_expense")
        rchild = pl.when(phasing & (expense > 0)).then(dagidw * expense / a["child_care_rate_step"]).otherwise(0.0)

    if behavior.correct_analytic_federal_rate:
        # The credit falls by half the AGI change and rises with the taxable
        # share of benefits: share * (100 - points * dagidw).
        elderly_slope = a["elderly_credit_share"] * (100.0 - a["elderly_credit_points"] * dagidw)
    else:
        # TAXSIM subtracts the Social Security rate (percent) from 50.
        elderly_slope = a["elderly_credit_share"] * dagidw * (a["elderly_credit_points"] - rssa)
    rold = pl.when((pl.col("analytic_elder_excess") > 0) & (pl.col("federal_elder") > 0)).then(
        elderly_slope.clip(0, None)
    ).otherwise(0.0)

    taxbca = pl.col("tax_before_credits")
    chcr = pl.col("ccc_uncapped")
    elder = pl.col("federal_elder")
    precrd = pl.col("analytic_ctc") if year >= 1998 else pl.lit(0.0)
    rcht = pl.lit(0.0)
    if year >= 1998:
        xlin10 = (taxbca - chcr - elder).clip(0, None)
        usable = (precrd > 0) & (precrd < xlin10)
        if behavior.correct_analytic_federal_rate:
            # The phaseout slope, in percent, while the credit phases out.
            rcht = pl.when(usable & pl.col("analytic_ctc_phasing")).then(100.0 * a["child_tax_credit_points"]).otherwise(0.0)
        else:
            # TAXSIM adds the fraction to a rate in percent, phasing out or not.
            rcht = pl.when(usable).then(a["child_tax_credit_points"]).otherwise(0.0)

    rrate = rssa + rchild + rold + rgrate + 100.0 * (rsave + rxmp) + rded + rexem
    rate = rrate + reic + rcht

    # Alternative minimum tax: TAXSIM's AMT rate replaces the regular rate.
    alminc = pl.col("analytic_alminc")
    coeff = pl.when(pl.col("analytic_amt_phasing")).then(a["amt_exemption_phaseout_coefficient"]).otherwise(1.0)
    low = a.num("amt_rate")
    if year <= 1992:
        almrat = pl.lit(low)
    else:
        high = a.num("amt_high_rate")
        base = alminc if year <= 1996 else alminc - pl.col("ltg").clip(0, None)
        almrat = pl.when(base > a["amt_breakpoint"] / sepret).then(high).otherwise(low)
        if year >= 1997:
            almrat = pl.when(alminc < pl.col("ltg")).then(0.0).otherwise(almrat)
    pded_amt = (
        pl.col("analytic_pded") & itemizes if 1989 <= year <= 1993 else pl.lit(False)
    ).cast(pl.Float64)
    ralm = coeff * 100.0 * almrat * dagidw * (1.0 + a["itemized_limitation_rate"] * pded_amt)
    credits = chcr + elder + (pl.col("odc") if year >= 1998 else 0.0)
    ralm = pl.when(taxbca < credits).then(ralm - (rgrate * dagidw - rded - rexem)).otherwise(ralm)
    if year >= 1997:
        ltg = pl.col("ltg")
        brac15 = threshold(1)
        within15 = pl.min_horizontal(brac15, taxinc)
        xl36d = within15 - pl.min_horizontal((taxinc - ltg).clip(0, None), within15)
        xl38 = pl.min_horizontal(alminc, ltg, xl36d)
        xl42 = alminc - xl38
        gains = ltg > alminc + a["amt_gains_margin"]
        ralm = (
            pl.when(gains & (xl42 > 0)).then(a["amt_gains_rate"] * coeff * 100.0 * dagidw)
            .when(gains & (xl38 > 0)).then(a["amt_gains_low_rate"] * coeff * 100.0 * dagidw)
            .otherwise(ralm)
        )
    amt_applies = (pl.col("amt") > 0) & ~is_dependent_filer()
    rate = pl.when(amt_applies).then(ralm + reic + rcht).otherwise(rate)
    ralm_net = pl.when(amt_applies).then(ralm - rrate).otherwise(0.0)

    # Credits reaching the tax leave only the EITC and AMT terms.
    chcr_capped = pl.min_horizontal(chcr, taxbca)
    if year <= 1999:
        exhausted = (chcr_capped + elder + precrd - taxbca > 0) | (
            (precrd > 0) & (precrd + chcr_capped + elder >= taxbca)
        )
        tail = ralm_net
    else:
        exhausted = (credits - taxbca > 0) | ((precrd > 0) & (precrd + chcr_capped + elder >= taxbca))
        tail = pl.lit(0.0)
    reic_exhausted = pl.when(reic > 0).then(reic / dagidw).otherwise(reic)
    return pl.when(exhausted).then(reic_exhausted + tail).otherwise(rate)


def _sepret() -> pl.Expr:
    """2 on a married-separate return, otherwise 1."""
    return by_filing_status(SEPRET_BY_STATUS)


def _by_status_at(table: dict, year: int) -> pl.Expr:
    """A per-filing-status table of year-keyed values, read at `year`."""
    return by_filing_status({status: resolve_year(table[status], year) for status in FILING_STATUSES})


def _gross_se_income() -> pl.Expr:
    """Self-employment, business and professional income."""
    return (
        pl.col("psemp") + pl.col("ssemp")
        + pl.col("pbusinc") + pl.col("pprofinc") + pl.col("sbusinc") + pl.col("sprofinc")
    )


def _dividends() -> pl.Expr:
    """Dividends with TAXSIM's fixed adjustment."""
    return pl.col("dividends") + dividend_input_adjustment()


def _capital_gain(year: int) -> pl.Expr:
    """Net capital gain in AGI (`capgn`); a net loss is limited per return."""
    loss_limit = float(resolve_year(FEDERAL_CAPITAL_GAINS_PARAMS["net_capital_loss_limit"], year))
    return pl.max_horizontal(pl.col("stcg") + pl.col("ltcg"), -loss_limit / _sepret())


def _schedule_e() -> pl.Expr:
    """Schedule E income: other property income and S corporation income."""
    return pl.col("otherprop") + pl.col("scorp")


def _brackets_by_status(year: int) -> dict[str, list[list[float]]]:
    brackets_by_status = {}
    for status in FILING_STATUSES:
        brackets = resolve_year(FEDERAL_INCOME_TAX_PARAMS["brackets"][status], year)
        validate_brackets(brackets, context=f"federal.{status}.{year}")
        brackets_by_status[status] = brackets
    return brackets_by_status


def _models_analytic_rate(year: int) -> bool:
    """Whether TAXSIM's analytic marginal rate (`comnew(72)`) is modeled."""
    return 1987 <= year <= 2000


def compute_federal_income_tax(
    df: pl.DataFrame | pl.LazyFrame,
    year: int,
    behavior: BehaviorProfile = TAXSIM_BEHAVIOR,
) -> pl.DataFrame | pl.LazyFrame:
    """Federal income tax (`fiitax`) and the intermediate columns states read."""
    if year <= 1976:
        # The pre-1977 law has a separate calculation structure.
        from taxsim_py.calculators.federal_law60 import compute_federal_income_tax_law60

        return compute_federal_income_tax_law60(df, year, behavior)
    if year <= 1986:
        # The 1977-1986 law has a separate calculation structure.
        from taxsim_py.calculators.federal_pre1987 import compute_federal_income_tax_pre1987

        return compute_federal_income_tax_pre1987(df, year, behavior)
    df = df.with_columns(
        filing_status=filing_status(),
        wages=pl.col("pwages") + pl.col("swages"),
    )
    df = with_defaults(df, (
        "proptax", "otheritem", "mortgage", "state_sales_or_income_tax_ded", "dep13", "childcare", "intrec",
        "psemp", "ssemp", "dividends", "stcg", "ltcg", "ui", "pui", "sui", "charity_cash", "pensions", "gssi",
        "transfers", "otherprop", "nonprop", "scorp", "pbusinc", "pprofinc", "sbusinc", "sprofinc", "page",
        "sage",
    ))
    df = with_default(df, FORCE_ITEMIZE, None)
    df = _income(df, year, behavior)
    for stage in (_deductions, _regular_tax, _alternative_minimum_tax, _credits, _net_tax):
        df = stage(df, year, behavior)
    return df


def _income(
    df: pl.DataFrame | pl.LazyFrame,
    year: int,
    behavior: BehaviorProfile = TAXSIM_BEHAVIOR,
) -> pl.DataFrame | pl.LazyFrame:
    """AGI: self-employment tax, gains, Social Security and unemployment compensation."""
    # AGI includes gross self-employment and business income and deducts the
    # applicable share of self-employment tax.
    pt_p = YearParams(PAYROLL_TAX_PARAMS, year)
    payroll = payroll_parts(year, behavior)
    gross_se_income = _gross_se_income()

    # The payroll figures are kept for the payroll step.
    prerequisites = payroll_prerequisites(year, behavior)
    df = df.with_columns(**prerequisites).with_columns(**{f"__payroll_{name}": expr for name, expr in payroll.items()}).drop(*prerequisites)
    payroll = {name: pl.col(f"__payroll_{name}") for name in payroll}
    setax_total = payroll["setax"]
    # The payroll tax holiday uses a special SE-tax deduction.
    if pt_p.in_effect("se_deduction_holiday"):
        wage_base = pt_p.num("oasdi_wage_base")
        holiday = pt_p.value("se_deduction_holiday")
        se_deduction_threshold = holiday["threshold_share"] * wage_base
        se_deduction_flat_addon = holiday["threshold_share"] * holiday["addon_rate"] * wage_base
        se_agi_deduction = (
            pl.when(setax_total <= se_deduction_threshold)
            .then(holiday["low_rate"] * setax_total)
            .otherwise(0.5 * setax_total + se_deduction_flat_addon)
        )
    else:
        se_agi_deduction = 0.5 * setax_total

    # EITC earned income always deducts the SE-tax share calculated above.
    df = df.with_columns(
        setax=setax_total,
        setax_qbi=payroll["setax_qbi"],
        setax_sstb=payroll["setax_sstb"],
        earned_income=(pl.col("wages") + gross_se_income - se_agi_deduction).clip(0, None),
    )

    # The AGI deduction for self-employment tax begins in 1990.
    se_agi_adjustment = pl.lit(0.0) if year < 1990 else se_agi_deduction
    df = df.with_columns(se_adjustment=se_agi_adjustment)

    # Interest is ordinary income but not earned income. TAXSIM's dividend
    # amount includes a fixed 0.001 adjustment.
    dividends_with_fudge = _dividends()
    capgn = _capital_gain(year)
    fullcg = pl.col("stcg") + pl.col("ltcg")
    # Net long-term gain after short-term losses, the preferential-rate base.
    net_ltcg = pl.min_horizontal(pl.col("ltcg"), fullcg).clip(0, None)

    # Qualified dividends join the preferential-rate base beginning in 2003.
    if year >= 2003:
        ltg = net_ltcg + dividends_with_fudge
    else:
        ltg = net_ltcg + 0.0

    # Other property income and S corporation income enter as Schedule E
    # income; other non-property income is a (negative) adjustment.
    schedule_e = _schedule_e()
    income_before_ui = (
        pl.col("wages")
        + pl.col("intrec")
        + gross_se_income
        - se_agi_adjustment
        + capgn
        + dividends_with_fudge
        + pl.col("pensions")
        + schedule_e
        + pl.col("nonprop")
    )

    # Prefer split unemployment amounts when they exceed the combined input.
    ui_total = unemployment_total()
    income_p = YearParams(FEDERAL_INCOME_TAX_PARAMS, year)
    ui_exclusion = income_p.value("unemployment_exclusion") if income_p.in_effect("unemployment_exclusion") else {}
    if "per_return" in ui_exclusion:
        # A per-return exclusion with no income limit.
        ui_in_income = (ui_total - ui_exclusion["per_return"]).clip(0, None)
    elif "per_spouse" in ui_exclusion:
        # Unemployment is added after Social Security (below).
        ui_in_income = pl.lit(0.0)
    else:
        ui_in_income = ui_total
    income_before_ss = income_before_ui + ui_in_income

    # Social Security benefits in AGI (`ssagi`).
    ss_p = SOCIAL_SECURITY_PARAMS
    two_tiers = YearParams(ss_p, year).in_effect("second_tier_rate")
    second_tier_rate = YearParams(ss_p, year).num("second_tier_rate") if two_tiers else 0.0
    benefits = pl.col("gssi").clip(0, None)
    # Provisional income adds tax-exempt interest, which TAXSIM reads from the
    # transfers input.
    transfers = pl.lit(0.0) if behavior.exclude_transfers_from_provisional_income else pl.col("transfers")
    provisional = income_before_ss + transfers + 0.5 * benefits
    if "per_spouse" in ui_exclusion:
        provisional = provisional + ui_total
    taxable_ss = taxable_social_security(
        benefits,
        provisional,
        base_amount=by_filing_status(ss_p["base_amount"]),
        first_tier_width=by_filing_status(ss_p["first_tier_width"]),
        first_tier_rate=float(ss_p["first_tier_rate"]),
        second_tier_rate=second_tier_rate,
        two_tiers=two_tiers,
    )
    agi_before_ui = income_before_ss + taxable_ss

    if "per_spouse" in ui_exclusion:
        # A per-spouse exclusion below an AGI limit.
        per_spouse = float(ui_exclusion["per_spouse"])
        excl_spouse = pl.min_horizontal(pl.col("sui"), pl.lit(per_spouse))
        excl_primary = (ui_total - pl.col("sui")).clip(0, per_spouse)
        exclusion = excl_spouse + excl_primary
        taxable_ui = (
            pl.when(agi_before_ui < ui_exclusion["agi_limit"])
            .then((ui_total - exclusion).clip(0, None))
            .otherwise(ui_total)
        )
        agi = agi_before_ui + taxable_ui
    else:
        taxable_ui = ui_in_income
        agi = agi_before_ui

    df = df.with_columns(
        ltg=ltg,
        taxable_unemployment=taxable_ui,
        taxable_social_security=taxable_ss,
        agi=agi,
    )
    # TAXSIM's analytic marginal rate (`comnew(72)`) is modeled for the years
    # states read it; its inputs are kept as `analytic_*` columns.
    if _models_analytic_rate(year):
        ss_base = by_filing_status(ss_p["base_amount"])
        ss_width = by_filing_status(ss_p["first_tier_width"])
        first_share = float(ss_p["first_tier_rate"])
        second_share = second_tier_rate
        xlin9 = (provisional - ss_base).clip(0, None)
        if not two_tiers:
            pssa = (benefits > xlin9) & (xlin9 > 0)
            pssa1 = pl.lit(False)
            pssa2 = pssa
        else:
            xlin11 = (xlin9 - ss_width).clip(0, None)
            xlin13 = first_share * pl.min_horizontal(xlin9, ss_width)
            xlin14 = pl.min_horizontal(xlin13, first_share * benefits)
            pssa = (taxable_ss > 0) & (second_share * benefits > xlin14 + second_share * xlin11)
            pssa1 = pssa & (xlin9 > ss_width)
            pssa2 = pssa & (xlin9 < ss_width) & (xlin13 < first_share * benefits)
        pssa = pssa & (benefits > 0)
        df = df.with_columns(
            analytic_pssa=pssa,
            analytic_pssa1=pssa1 & (benefits > 0),
            # The derivative of AGI with respect to income (`dagidw`).
            analytic_dagidw=1.0 + second_share * (pssa1 & (benefits > 0)).cast(pl.Float64)
            + first_share * (pssa2 & (benefits > 0)).cast(pl.Float64),
        )

    return df


def _deductions(df: pl.DataFrame | pl.LazyFrame, year: int, behavior: BehaviorProfile = TAXSIM_BEHAVIOR) -> pl.DataFrame | pl.LazyFrame:
    """Standard or itemized deduction, exemptions and taxable income."""
    sepret_expr = _sepret()
    gross_se_income = _gross_se_income()
    analytic_rate = _models_analytic_rate(year)

    # Married-separate uses half of the joint standard deduction.
    std_ded_by_status = {
        status: resolve_year(FEDERAL_INCOME_TAX_PARAMS["standard_deduction"][status], year)
        for status in FILING_STATUSES
        if status != "married_separate"
    }
    std_ded_by_status["married_separate"] = std_ded_by_status["married_joint"] / 2.0
    std_ded_expr = by_filing_status(std_ded_by_status)
    income_p = YearParams(FEDERAL_INCOME_TAX_PARAMS, year)
    if income_p.in_effect("standard_deduction_real_property_tax_cap"):
        property_tax_cap = income_p.num("standard_deduction_real_property_tax_cap")
        num_filers = pl.when(files_joint()).then(2.0).otherwise(1.0)
        std_ded_expr = std_ded_expr + pl.min_horizontal(
            pl.col("proptax").clip(0, None), property_tax_cap * num_filers
        )
    # Each taxpayer 65 or older adds the additional standard deduction.
    aged_amount = _by_status_at(FEDERAL_INCOME_TAX_PARAMS["aged_standard_deduction"], year)
    std_ded_expr = std_ded_expr + aged_amount * aged_count()
    # A dependent's standard deduction is limited to the greater of a minimum
    # and earned income plus an addition (TAXSIM applies the limit twice:
    # here with wages and gross business income, and again in taxable
    # income with earned income).
    dependent_p = YearParams(FEDERAL_INCOME_TAX_PARAMS["dependent_standard_deduction"], year)
    dependent_minimum = dependent_p.num("minimum")
    dependent_addition = dependent_p.num("earned_income_addition")
    dependent = is_dependent_filer()
    dependent_earnings = pl.col("wages") + (gross_se_income + pl.col("scorp")).clip(0, None) + dependent_addition
    std_ded_expr = pl.when(dependent).then(
        pl.min_horizontal(std_ded_expr, pl.max_horizontal(pl.lit(dependent_minimum), dependent_earnings))
    ).otherwise(std_ded_expr)
    df = df.with_columns(standard_deduction=std_ded_expr)

    itemized_p = YearParams(FEDERAL_ITEMIZED_PARAMS, year)
    salt_uncapped_expr = pl.col("proptax") + pl.col("otheritem") + pl.col("state_sales_or_income_tax_ded")
    if itemized_p.in_effect("salt_cap"):
        salt_cap_expr = pl.lit(itemized_p.num("salt_cap")) / sepret_expr
        if itemized_p.in_effect("salt_phaseout"):
            # The cap shrinks with income but never below the floor.
            phaseout = itemized_p.value("salt_phaseout")
            salt_cap_expr = pl.max_horizontal(
                pl.lit(float(phaseout["floor"])) / sepret_expr,
                salt_cap_expr - phaseout["rate"] * (pl.col("agi") - phaseout["threshold"] / sepret_expr).clip(0, None),
            )
        salt_capped = salt_uncapped_expr.clip(0, None).clip(None, salt_cap_expr)
    else:
        salt_capped = salt_uncapped_expr.clip(0, None)

    # The cash-contribution AGI cap is suspended for 2020 and 2021.
    agix = pl.col("agi").clip(0, None)
    alim50 = itemized_p.num("cash_charity_agi_limit_share") * agix
    char_cash_itemized = pl.min_horizontal(alim50, pl.col("charity_cash")).clip(0, None)
    itemized_deduction = salt_capped + pl.col("mortgage") + char_cash_itemized
    itemized_before_limit = itemized_deduction
    pease_reduction = pl.lit(0.0)
    pease_binding = pl.lit(False)

    if itemized_p.in_effect("pease_limitation_threshold"):
        pease_p = itemized_p
        pease_threshold_expr = by_filing_status(pease_p.value("pease_limitation_threshold"))
        pease_reduction_rate = pease_p.num("pease_reduction_rate")
        pease_cap_rate = pease_p.num("pease_cap_rate")
        dlim1 = pease_reduction_rate * (pl.col("agi") - pease_threshold_expr).clip(0, None)
        dlim2 = pease_cap_rate * itemized_deduction.clip(0, None)
        pease_reduction = pl.min_horizontal(dlim1, dlim2)
        # The limitation grows with income (the 80% cap does not bind).
        pease_binding = (dlim1 > 0) & (dlim1 < dlim2)
        pease_reduction = pease_reduction * pease_p.num("limitation_phasedown_share")
        itemized_deduction = itemized_deduction - pease_reduction

    # Cash charity deduction for nonitemizers: per return in 2020, per taxpayer in 2021.
    if itemized_p.in_effect("nonitemizer_charity_deduction"):
        charity = itemized_p.num("nonitemizer_charity_deduction")
        num_filers = pl.when(files_joint()).then(2.0).otherwise(1.0)
        limit = pl.lit(charity) / sepret_expr if year == 2020 else charity * num_filers
        cas = pl.min_horizontal(limit, pl.col("charity_cash"))
    else:
        cas = pl.lit(0.0)

    itemize_comparison_floor = std_ded_expr + cas if year == 2021 else std_ded_expr
    # The resolver forces both choices and compares combined federal-state tax.
    itemizes_expr = (
        itemize_choice(itemized_deduction > itemize_comparison_floor)
    )
    df = df.with_columns(
        salt_capped=salt_capped,
        itemized_deduction=itemized_deduction,
        itemized_before_limit=itemized_before_limit,
        deduction_phaseout=pease_reduction,
        itemizes=itemizes_expr,
    )
    if analytic_rate:
        df = df.with_columns(analytic_pded=pease_binding)
    deduction = pl.when(pl.col("itemizes")).then(pl.col("itemized_deduction")).otherwise(std_ded_expr + cas)
    dependent_standard = pl.when(pl.col("itemizes")).then(0.0).otherwise(std_ded_expr)
    dependent_deduction = pl.max_horizontal(
        pl.when(pl.col("itemizes")).then(pl.col("itemized_deduction")).otherwise(0.0),
        pl.min_horizontal(
            dependent_standard,
            pl.max_horizontal(pl.lit(dependent_minimum), pl.col("earned_income") + dependent_addition),
        ),
    )
    deduction = pl.when(dependent).then(dependent_deduction).otherwise(deduction)

    # Personal and dependent exemptions, with the high-income phaseout.
    pe_p = YearParams(FEDERAL_PERSONAL_EXEMPTION_PARAMS, year)
    if pe_p.in_effect("amount"):
        exemption_amount = pe_p.num("amount")
        exemption_count = 1.0 + pl.col("depx") + pl.when(files_joint()).then(1.0).otherwise(0.0)
        amex_base = exemption_amount * exemption_count
        if not pe_p.in_effect("high_income_phaseout_threshold"):
            amex = amex_base
        elif year <= 1996:
            # The reduction is not limited to the exemption.
            pep_threshold_expr = by_filing_status(pe_p.value("high_income_phaseout_threshold"))
            pep_rate = pe_p.num("phaseout_rate")
            pep_bracket_size = pe_p.num("phaseout_bracket_size")
            pep_ratio = pep_rate * (pl.col("agi") - pep_threshold_expr).clip(0, None) / (pep_bracket_size / sepret_expr)
            amex = (amex_base * (1.0 - pep_ratio)).clip(0, None)
        else:
            pep_threshold_expr = by_filing_status(pe_p.value("high_income_phaseout_threshold"))
            pep_rate = pe_p.num("phaseout_rate")
            pep_bracket_size = pe_p.num("phaseout_bracket_size")
            pep_ratio = (
                pep_rate * (pl.col("agi") - pep_threshold_expr).clip(0, None) / (pep_bracket_size / sepret_expr)
            ).clip(0, 1)
            amphs_fraction = itemized_p.num("limitation_phasedown_share")
            amex = amex_base * (1.0 - pep_ratio * amphs_fraction)
    else:
        amex = pl.lit(0.0)
        amex_base = pl.lit(0.0)

    # Dependents claim no exemptions; TAXSIM reports the whole amount as
    # phased out.
    amex = pl.when(dependent).then(0.0).otherwise(amex)
    if analytic_rate:
        # Exemptions phasing out, within TAXSIM's range above the threshold.
        pexem = pl.lit(False)
        if pe_p.in_effect("amount") and pe_p.in_effect("high_income_phaseout_threshold"):
            within = pl.col("agi") - pep_threshold_expr <= ANALYTIC_RATE_PARAMS["exemption_phaseout_range"] / sepret_expr
            pexem = (amex > 0) & (amex_base - amex > 0) & within
        df = df.with_columns(analytic_pexem=pexem, analytic_exemptions_base=amex_base)
    senior = pl.lit(0.0)
    if income_p.in_effect("senior_deduction"):
        sen_p = income_p.value("senior_deduction")
        sen_threshold = pl.when(files_joint()).then(float(sen_p["phaseout_threshold"]["joint"])).otherwise(
            float(sen_p["phaseout_threshold"]["other"])
        )
        per_person = (float(sen_p["amount"]) - float(sen_p["phaseout_rate"]) * (pl.col("agi") - sen_threshold).clip(0, None)).clip(0, None)
        senior = pl.when(files_separate() | dependent).then(0.0).otherwise(per_person * aged_count())
    df = df.with_columns(
        personal_exemptions=amex,
        exemption_phaseout=amex_base - amex,
        senior_deduction=senior,
        taxable_income=(pl.col("agi") - deduction - amex - senior).clip(0, None),
    )
    if income_p.in_effect("qbi_deduction_rate"):
        df = df.with_columns(qbi_deduction=_qbi_deduction(year, _dividends(), _capital_gain(year)))
        df = df.with_columns(taxable_income=(pl.col("taxable_income") - pl.col("qbi_deduction")).clip(0, None))
    else:
        df = df.with_columns(qbi_deduction=pl.lit(0.0))
    return df


def _regular_tax(df: pl.DataFrame | pl.LazyFrame, year: int, behavior: BehaviorProfile = TAXSIM_BEHAVIOR) -> pl.DataFrame | pl.LazyFrame:
    """Bracket tax with capital gains rates and the 1988-1996 surtaxes."""
    # ltg can't exceed taxable income itself (the preferential-rate base is
    # capped by taxable income the same way the source's worksheet does via
    # its min(taxinc, ...) terms).
    brackets_by_status = _brackets_by_status(year)
    sepret_expr = _sepret()
    analytic_rate = _models_analytic_rate(year)
    amex = pl.col("personal_exemptions")
    ltg_capped = pl.min_horizontal(pl.col("ltg"), pl.col("taxable_income"))
    ordinary_income = (pl.col("taxable_income") - ltg_capped).clip(0, None)
    federal_source_rate = 100.0 * bracket_rate_by_status(
        ordinary_income if year >= 1997 else pl.col("taxable_income"),
        brackets_by_status,
    )

    tax_expr = bracket_tax_by_status(ordinary_income, brackets_by_status)

    cg_p = YearParams(FEDERAL_CAPITAL_GAINS_PARAMS, year)
    max_rate = cg_p.num("maximum_rate") if cg_p.in_effect("maximum_rate") else None
    rate_0_ceiling_expr = _by_status_at(cg_p["rate_0_ceiling"], year)
    rate_15_ceiling_expr = _by_status_at(cg_p["rate_15_ceiling"], year)
    plain_ordinary_tax = bracket_tax_by_status(pl.col("taxable_income"), brackets_by_status)

    if year == 1987:
        # The 1987 transition caps gains above the 28% bracket ceiling while
        # preserving ordinary bracket tax below it. See `tax87` in TAXSIM.
        rate_28_ceiling_expr = by_filing_status(cg_p.value("rate_28_ceiling"))
        tax_at_ttab = bracket_tax_by_status(rate_28_ceiling_expr, brackets_by_status)
        tax_case_c = tax_at_ttab + max_rate * (pl.col("taxable_income") - rate_28_ceiling_expr)
        tax_case_a = tax_expr + max_rate * ltg_capped
        total_tax_1987 = (
            pl.when(pl.col("taxable_income") < rate_28_ceiling_expr)
            .then(plain_ordinary_tax)
            .when(ordinary_income <= rate_28_ceiling_expr)
            .then(tax_case_c)
            .otherwise(tax_case_a)
        )
        preferential_tax = total_tax_1987 - tax_expr
    elif 1988 <= year <= 1990:
        # Capital gains are taxed as ordinary income in 1988-1990.
        preferential_tax = plain_ordinary_tax - tax_expr
    elif year in (1991, 1992, 1993):
        # Cap the marginal rate on gains at 28% only above the top of the
        # 28% bracket. The YAML tables contain the inflation-adjusted limits.
        taxin3 = pl.max_horizontal(rate_0_ceiling_expr, pl.col("taxable_income") - ltg_capped)
        excess = pl.col("taxable_income") - taxin3
        alt_ordinary_tax = bracket_tax_by_status(taxin3, brackets_by_status)
        alt_tax = alt_ordinary_tax + max_rate * excess
        gated = (ltg_capped > 0) & (pl.col("taxable_income") > rate_15_ceiling_expr)
        # Below the gate, restore plain bracket tax.
        preferential_tax = pl.when(gated).then(alt_tax - tax_expr).otherwise(plain_ordinary_tax - tax_expr)
    elif year <= 1996:
        # Cap the marginal rate on gains at 28%, with ordinary income floored
        # at the start of the 28% bracket.
        taxin3 = pl.max_horizontal(rate_0_ceiling_expr, pl.col("taxable_income") - ltg_capped)
        excess = (pl.col("taxable_income") - taxin3).clip(0, None)
        alt_ordinary_tax = bracket_tax_by_status(taxin3, brackets_by_status)
        alt_tax = alt_ordinary_tax + max_rate * excess
        preferential_tax = pl.when(ltg_capped > 0).then(alt_tax - tax_expr).otherwise(0.0)
    elif 1997 <= year <= 2000:
        # Apply the capped three-tier worksheet at the historical 10%/20%
        # capital-gains rates.
        preferential_tax = preferential_rate_tax(
            pl.col("taxable_income"),
            ltg_capped,
            rate_0_ceiling_expr,
            rate_15_ceiling_expr,
            rate_15=cg_p.num("rate_15"),
            rate_20=cg_p.num("rate_20"),
            rate_0=cg_p.num("rate_0"),
        )
    elif year <= 2002:
        # TAXSIM leaves the 2001-2002 low-rate room uncapped by available
        # gains; the final minimum below prevents tax above ordinary rates.
        low_room = (rate_0_ceiling_expr - ordinary_income).clip(0, None)
        rate_0 = cg_p.num("rate_0")
        rate_top = cg_p.num("rate_15")
        sch10 = rate_0 * low_room
        above_room = (ltg_capped - low_room).clip(0, None)
        sch20 = rate_top * above_room
        preferential_tax = sch10 + sch20
    elif cg_p.in_effect("transition_rates"):
        # Dividends use the new rates before gains consume the remaining room
        # at the old ones.
        low_room = (pl.min_horizontal(pl.col("taxable_income"), rate_0_ceiling_expr) - ordinary_income).clip(
            0, None
        )
        dividends_with_fudge = _dividends()
        dividends_in_low = pl.min_horizontal(low_room, dividends_with_fudge)
        rates = cg_p.value("transition_rates")
        sch5 = rates["dividends_low"] * dividends_in_low
        gain_in_low = low_room - dividends_in_low
        sch10 = rates["gains_low"] * gain_in_low
        above_room = (ltg_capped - low_room).clip(0, None)
        dividends_remaining = dividends_with_fudge - dividends_in_low
        dividends_in_high = pl.min_horizontal(above_room, dividends_remaining)
        sch15 = rates["dividends_high"] * dividends_in_high
        gain_in_high = above_room - dividends_in_high
        sch20 = rates["gains_high"] * gain_in_high
        preferential_tax = sch5 + sch10 + sch15 + sch20
    else:
        preferential_tax = preferential_rate_tax(
            pl.col("taxable_income"),
            ltg_capped,
            rate_0_ceiling_expr,
            rate_15_ceiling_expr,
            rate_15=cg_p.num("rate_15"),
            rate_20=cg_p.num("rate_20"),
            rate_0=cg_p.num("rate_0"),
        )

    # Schedule D tax cannot exceed ordinary bracket tax on total income.
    regular_tax = pl.min_horizontal(plain_ordinary_tax, tax_expr + preferential_tax)
    # TAXSIM's `regtax` (`comnew(28)`): the schedule tax before the 1988-1996
    # surtaxes; from 1991 on ordinary rates for all income.
    schedule_tax = regular_tax if year <= 1990 else plain_ordinary_tax

    pep_p = YearParams(FEDERAL_PERSONAL_EXEMPTION_PARAMS, year)
    if pep_p.in_effect("exemption_surtax_threshold"):
        # Capped at a share of the reduced personal exemptions.
        exemption_surtax_threshold_expr = by_filing_status(pep_p.value("exemption_surtax_threshold"))
        exemption_surtax = pl.min_horizontal(
            FEDERAL_INCOME_TAX_PARAMS["exemption_surtax_rate"]
            * (pl.col("taxable_income") - exemption_surtax_threshold_expr).clip(0, None),
            FEDERAL_INCOME_TAX_PARAMS["exemption_surtax_cap_share"] * amex,
        )
        regular_tax = regular_tax + exemption_surtax
        if analytic_rate:
            # The surtax adds its rate until it reaches its cap; at the cap a
            # phasing-out exemption lowers it.
            a = ANALYTIC_RATE_PARAMS
            dagidw = pl.col("analytic_dagidw")
            tax3 = a["surtax_rate"] * (pl.col("taxable_income") - exemption_surtax_threshold_expr)
            pep_slope = a["exemption_phaseout_rate"] * pl.col("analytic_exemptions_base") / (a["exemption_phaseout_step"] / sepret_expr)
            rxmp = (
                pl.when(pl.col("taxable_income") <= exemption_surtax_threshold_expr).then(0.0)
                .when(tax3 < a["exemption_surtax_cap_share"] * amex).then(a["surtax_rate"] * dagidw)
                .when(pl.col("analytic_pexem")).then(-a["exemption_surtax_cap_share"] * pep_slope * dagidw)
                .otherwise(0.0)
            )
            df = df.with_columns(analytic_rxmp=rxmp)
    else:
        exemption_surtax = pl.lit(0.0)

    income_tax_p = YearParams(FEDERAL_INCOME_TAX_PARAMS, year)
    if income_tax_p.in_effect("bubble_surtax_threshold"):
        # Claws back the savings of the 15% bracket.
        bubble_threshold_expr = by_filing_status(income_tax_p.value("bubble_surtax_threshold"))
        bubble_cap_expr = by_filing_status(income_tax_p.value("bubble_surtax_cap"))
        bubble_surtax = pl.min_horizontal(
            income_tax_p["bubble_surtax_rate"] * (pl.col("taxable_income") - bubble_threshold_expr).clip(0, None),
            bubble_cap_expr,
        )
        regular_tax = regular_tax + bubble_surtax
        if analytic_rate:
            tax2 = ANALYTIC_RATE_PARAMS["surtax_rate"] * (pl.col("taxable_income") - bubble_threshold_expr)
            rsave = pl.when((pl.col("taxable_income") > bubble_threshold_expr) & (tax2 <= bubble_cap_expr)).then(
                ANALYTIC_RATE_PARAMS["surtax_rate"] * pl.col("analytic_dagidw")
            ).otherwise(0.0)
            df = df.with_columns(analytic_rsave=rsave)

    df = df.with_columns(
        regular_tax=regular_tax,
        schedule_tax=schedule_tax,
        exemption_surtax=exemption_surtax,
        federal_source_rate=federal_source_rate,
        num_children=pl.col("dep18").clip(0, 3),
    )
    return df


def _alternative_minimum_tax(df: pl.DataFrame | pl.LazyFrame, year: int, behavior: BehaviorProfile = TAXSIM_BEHAVIOR) -> pl.DataFrame | pl.LazyFrame:
    """Alternative minimum tax and tax before credits."""
    # AMT income: AGI, minus mortgage interest if itemizing (SALT is never
    # deductible for AMT; mortgage interest is deductible for both).
    sepret_expr = _sepret()
    amt_p = YearParams(FEDERAL_AMT_PARAMS, year)
    cg_p = YearParams(FEDERAL_CAPITAL_GAINS_PARAMS, year)
    amt_exemption_expr = _by_status_at(amt_p["exemption"], year)
    amt_phaseout_threshold_expr = _by_status_at(amt_p["exemption_phaseout_threshold"], year)
    amt_income = pl.col("agi") - pl.when(pl.col("itemizes")).then(pl.col("mortgage")).otherwise(0.0)
    if year in (1991, 1992) and not behavior.exempt_amt_from_itemized_limit_1991_1992:
        # TAXSIM adds the itemized-deduction limitation back for itemizers.
        amt_income = amt_income + pl.when(pl.col("itemizes")).then(pl.col("deduction_phaseout")).otherwise(0.0)
    # 1990+: dependents and young filers get at most earned income plus a
    # fixed amount of exemption.
    young_filer_cap = None
    if year >= 1990:
        young_age = amt_p.num("young_filer_age")
        older_age = pl.max_horizontal(pl.col("page"), pl.col("sage"))
        young = pl.when(older_age > 0).then(older_age < young_age).otherwise(is_dependent_filer())
        if behavior.cap_amt_exemption_for_dependents_only:
            # The cap applies to children subject to the tax on unearned income.
            young = young & is_dependent_filer()
        addition = amt_p.num("young_filer_exemption_addition")
        young_filer_cap = pl.when(young).then(pl.col("earned_income") + addition).otherwise(None)
    amt_income = amt_income.clip(0, None)
    if year >= 2018:
        amt_income = (amt_income - pl.col("qbi_deduction")).clip(0, None)
    # The separate-return addback cap is the separate exemption.
    separate_addback = {}
    if year >= 1990:
        separate_addback = dict(
            separate_return_addback_cap=resolve_year(amt_p["exemption"]["married_separate"], year),
            separate_return_addback_threshold=amt_p.value("separate_return_addback_threshold"),
            separate_return_addback_rate=amt_p.num("separate_return_addback_rate"),
        )
    # Preferential AMT treatment for capital gains begins in 1997.
    gains = {}
    if year >= 1997:
        gains = dict(
            ltg=pl.col("ltg"),
            regular_taxable_income=pl.col("taxable_income"),
            cg_rate_0_ceiling=_by_status_at(cg_p["rate_0_ceiling"], year),
            cg_rate_15_ceiling=_by_status_at(amt_p["cg_rate_15_ceiling"], year),
            cg_rate_15=cg_p.num("rate_15"),
            cg_rate_0=cg_p.num("rate_0"),
            cg_top_rate_addition=amt_p.num("gains_top_rate_addition"),
        )
    amt = alternative_minimum_tax(
        amt_income=amt_income,
        regular_tax=pl.col("regular_tax"),
        exemption=amt_exemption_expr,
        exemption_phaseout_threshold=amt_phaseout_threshold_expr,
        exemption_phaseout_rate=amt_p.num("exemption_phaseout_rate"),
        rate_breakpoint=amt_p.num("rate_breakpoint"),
        rate_below_breakpoint=amt_p.num("rate_below_breakpoint"),
        rate_above_breakpoint=amt_p.num("rate_above_breakpoint"),
        sepret=sepret_expr,
        exemption_cap=young_filer_cap,
        **separate_addback,
        **gains,
    )
    # AMT income before the exemption (`alminy`), as the states read it.
    reported_amt_income = amt_income
    if separate_addback:
        reported_amt_income = separate_return_amt_income(
            amt_income,
            sepret_expr,
            separate_addback["separate_return_addback_cap"],
            separate_addback["separate_return_addback_threshold"],
            separate_addback["separate_return_addback_rate"],
        )
    # Preserve precision for the $0.01 perturbation used by marginal rates.
    df = df.with_columns(amt=amt, amt_income=reported_amt_income)
    if _models_analytic_rate(year):
        # The exemption left (`exclnt`), whether it is phasing out, and AMT
        # income after it (`alminc`).
        phaout = amt_p.num("exemption_phaseout_rate") * (
            pl.col("amt_income") - amt_phaseout_threshold_expr
        ).clip(0, None)
        exclnt = (amt_exemption_expr - phaout).clip(0, None)
        if young_filer_cap is not None:
            exclnt = pl.min_horizontal(exclnt, young_filer_cap.clip(0, None))
        df = df.with_columns(
            analytic_amt_phasing=(exclnt > 0) & (phaout > 0),
            analytic_alminc=(pl.col("amt_income") - exclnt).clip(0, None),
        )
    if year <= 1999 and not behavior.add_amt_before_2000:
        # TAXSIM computes AMT before 2000 but does not add it to liability.
        df = df.with_columns(tax_before_credits=pl.col("regular_tax"))
    else:
        df = df.with_columns(tax_before_credits=pl.col("regular_tax") + pl.col("amt"))
    return df


def _credits(df: pl.DataFrame | pl.LazyFrame, year: int, behavior: BehaviorProfile = TAXSIM_BEHAVIOR) -> pl.DataFrame | pl.LazyFrame:
    """Child care, earned income, elderly, child and other dependent credits."""
    # Apply CCC before CTC/ODC in the nonrefundable-credit stacking order.
    analytic_rate = _models_analytic_rate(year)
    ccc_p = YearParams(FEDERAL_CREDITS_PARAMS["child_care_credit"], year)
    max_qualifying_persons = ccc_p.num("max_qualifying_persons")
    max_expense_per_person = ccc_p.num("max_expense_per_person")
    ccc_rate = child_care_credit_rate(pl.col("agi"), ccc_p.value("rate"))
    num_qualifying_persons = pl.col("dep13").clip(0, max_qualifying_persons)
    qualifying_expense = pl.col("childcare").clip(0, num_qualifying_persons * max_expense_per_person)
    ccc_earned_income_cap = (
        pl.when(pl.col("mstat") == 2)
        .then(pl.min_horizontal(pl.col("pwages"), pl.col("swages")))
        .otherwise(pl.col("wages"))
    )
    ccc_expense = pl.min_horizontal(qualifying_expense, ccc_earned_income_cap).clip(0, None)
    ccc_amount = ccc_rate * ccc_expense
    if ccc_p.value("refundable"):
        ccc = ccc_amount.clip(0, None)
    elif year < 1998 and not behavior.apply_nonrefundable_credits_before_1998:
        # TAXSIM does not apply the computed CCC before 1998.
        ccc = pl.lit(0.0)
    else:
        ccc = pl.min_horizontal(ccc_amount, pl.col("tax_before_credits").clip(0, None))
    # `ccc_uncapped` is the credit before the tax-liability cap (`chcrst`).
    # `federal_chcr` is the credit states read as `comnew(53)`: before 1998,
    # when TAXSIM does not apply it, the credit limited to regular tax.
    federal_chcr = ccc
    if year < 1998 and not behavior.apply_nonrefundable_credits_before_1998:
        federal_chcr = pl.min_horizontal(ccc_amount.clip(0, None), pl.col("regular_tax"))
    df = df.with_columns(ccc=ccc, ccc_uncapped=ccc_amount.clip(0, None), federal_chcr=federal_chcr)
    if analytic_rate:
        df = df.with_columns(analytic_ccc_expense=ccc_expense)

    eitc_params_year = FEDERAL_EITC_PARAMS.filter(pl.col("year") == year).select(
        "filing_status", "num_children", "rate_in", "max_credit", "phaseout_start", "rate_out"
    )
    if isinstance(df, pl.LazyFrame):
        eitc_params_year = eitc_params_year.lazy()
    df = df.join(eitc_params_year, on=["filing_status", "num_children"], how="left")

    # Through 2002 the phaseout income excludes capital and Schedule E losses,
    # and from 1997 part of any business loss.
    eitc_p = YearParams(FEDERAL_EITC_PARAMS_MISC, year)
    phaseout_income = pl.col("agi")
    if year <= 2002:
        phaseout_income = phaseout_income - _capital_gain(year).clip(None, 0) - _schedule_e().clip(None, 0)
        loss_share = eitc_p.num("loss_addback_share") if year >= 1997 else 0.0
        business = _gross_se_income() + pl.col("scorp")
        phaseout_income = phaseout_income - loss_share * business.clip(None, 0)
    eitc_ordinary = (
        pl.when(pl.col("rate_in").is_not_null())
        .then(
            trapezoid_credit(
                pl.col("earned_income"),  # phase-in base: wages + gross SE income - .5*SE tax
                phaseout_income,  # phaseout compares the greater of earned income or AGI
                pl.col("rate_in"),
                pl.col("max_credit"),
                pl.col("phaseout_start"),
                pl.col("rate_out"),
            )
        )
        .otherwise(0.0)
    )
    # Investment income above the annual limit reduces EITC dollar-for-dollar.
    if year < 1996:
        # The disqualified-investment-income test begins in 1996.
        eitc_reduction = pl.lit(0.0)
    else:
        dylim = float(resolve_year(FEDERAL_EITC_PARAMS_MISC["dylim"], year))
        disqy = _capital_gain(year).clip(0, None) + _dividends() + pl.col("intrec") + pl.col("otherprop").clip(0, None)
        eitc_reduction = (disqy - dylim).clip(0, None)
    eitc = (eitc_ordinary - eitc_reduction).clip(0, None)
    childless = pl.col("num_children") == 0
    eitc_before_filer_test = eitc
    eitc = pl.when(eitc_filer_eligible(childless, year)).then(eitc).otherwise(0.0)
    # The credit before the minimum-age test (`comnew(188)`, read by Maine).
    eitc_before_age_test = eitc
    eitc = pl.when(eitc_age_eligible(childless, year)).then(eitc).otherwise(0.0)
    df = df.with_columns(
        eitc=eitc, eitc_before_age_test=eitc_before_age_test, eitc_before_filer_test=eitc_before_filer_test
    )
    if analytic_rate:
        df = df.with_columns(analytic_eitc_income=phaseout_income)

    # Credit for the elderly (`comnew(54)`, which states read), applied from
    # 1998 (TAXSIM does not apply nonrefundable credits for 1987-1997).
    eld_p = FEDERAL_INCOME_TAX_PARAMS["elderly_credit"]
    sepret = _sepret()
    one_aged = (aged_count() == 1) & (sepret != 2)
    # The AGI threshold is set by filing status, not by the number aged.
    single_threshold = (~files_joint() & (sepret != 2)) if behavior.use_joint_elderly_credit_threshold else one_aged
    threshold = pl.when(single_threshold).then(eld_p["agi_threshold"]["one_aged"]).otherwise(
        eld_p["agi_threshold"]["two_aged"] / sepret
    )
    base = pl.when(one_aged).then(eld_p["base"]["one_aged"]).otherwise(eld_p["base"]["two_aged"] / sepret)
    excess_agi = (pl.col("agi") - threshold).clip(0, None)
    nontaxable_benefits = pl.col("gssi") - pl.col("taxable_social_security")
    elderly_credit = pl.when(aged_count() > 0).then(
        eld_p["rate"] * (base - (eld_p["agi_share"] * excess_agi + nontaxable_benefits)).clip(0, None)
    ).otherwise(0.0)
    df = df.with_columns(
        federal_elder=elderly_credit,
        elderly_credit_raw=elderly_credit
        if year >= 1998 or behavior.apply_nonrefundable_credits_before_1998
        else pl.lit(0.0),
    )
    if analytic_rate:
        df = df.with_columns(analytic_elder_excess=pl.when(aged_count() > 0).then(excess_agi).otherwise(0.0))

    ctc_p = YearParams(FEDERAL_CREDITS_PARAMS["child_tax_credit"], year)

    ideps = pl.col("dep17")  # CTC-qualifying children
    young = pl.min_horizontal(pl.col("dep6"), ideps)
    agi = pl.col("agi")

    odc_p = YearParams(FEDERAL_CREDITS_PARAMS["other_dependent_credit"], year)
    if odc_p.in_effect("amount"):
        odc_base = odc_p.num("amount") * (pl.col("depx") - ideps).clip(0, None)
    else:
        odc_base = pl.lit(0.0)
    odc_rate_per_1000 = ctc_p.num("phaseout_rate_per_1000")
    odc_threshold_expr = by_filing_status(ctc_p.value("phaseout_threshold"))

    enhanced = ctc_p.in_effect("enhanced")
    if enhanced:
        e = ctc_p.value("enhanced")
        young_child_amount = float(e["young_child_amount"])
        older_child_amount = float(e["older_child_amount"])
        base_amount_offset = float(e["base_amount_offset"])
        enhanced_cap_expr = by_filing_status(e["enhanced_cap"])
        low_income_threshold_expr = by_filing_status(e["low_income_phaseout_threshold"])
        low_income_rate = float(e["low_income_phaseout_rate"])

        ccrmax = young * young_child_amount + (ideps - young) * older_child_amount
        enhanced_amount = pl.min_horizontal(ccrmax - ideps * base_amount_offset, enhanced_cap_expr)
        low_income_reduction = ((agi - low_income_threshold_expr).clip(0, None)) * low_income_rate
        ctc_before_tcja_phaseout = ccrmax - pl.min_horizontal(enhanced_amount, low_income_reduction)
    else:
        flat_amount = ctc_p.num("amount_per_child") if ctc_p.in_effect("amount_per_child") else 0.0
        ctc_before_tcja_phaseout = flat_amount * ideps

    # TAXSIM computes ODC after 2021 but does not add it to `precrd`.
    include_odc = year <= 2021 or behavior.allow_other_dependent_credit_after_2021
    combined_base = ctc_before_tcja_phaseout + (odc_base if include_odc else pl.lit(0.0))

    # The other dependent credit and child tax credit share the TCJA
    # phaseout; the refundable credit needs the amount after phaseout and
    # before the liability limit.
    tcja_reduction = ((agi - odc_threshold_expr).clip(0, None) / 1000) * odc_rate_per_1000
    combined_after_phaseout = (combined_base - tcja_reduction).clip(0, None)

    if enhanced:
        # Fully refundable.
        nonrefundable_credit = pl.lit(0.0)
        actc = combined_after_phaseout
    elif not ctc_p.in_effect("actc_rate"):
        # TAXSIM's additional credit for three or more children before 2001 is
        # built from a payroll-tax figure it never sets, so it is always 0.
        remaining_after_ccc = (pl.col("tax_before_credits") - pl.col("ccc") - pl.col("elderly_credit_raw")).clip(0, None)
        nonrefundable_credit = pl.min_horizontal(combined_after_phaseout, remaining_after_ccc)
        actc = pl.lit(0.0)
        if behavior.allow_additional_child_credit_before_2001 and ctc_p.in_effect("refundable_minimum_children"):
            # With three or more children, the unused credit is refundable up to
            # the employee share of payroll tax in excess of the EITC.
            employee_payroll_tax = 0.5 * (pl.col("__payroll_wage_tax") + pl.col("__payroll_setax"))
            limit = (employee_payroll_tax - pl.col("eitc")).clip(0, None)
            minimum_children = ctc_p.num("refundable_minimum_children")
            actc = pl.when(ideps >= minimum_children).then(
                pl.min_horizontal(combined_after_phaseout - nonrefundable_credit, limit)
            ).otherwise(0.0)
    else:
        remaining_after_ccc = (pl.col("tax_before_credits") - pl.col("ccc") - pl.col("elderly_credit_raw")).clip(0, None)
        nonrefundable_credit = pl.min_horizontal(combined_after_phaseout, remaining_after_ccc)
        unused_nonrefundable = combined_after_phaseout - nonrefundable_credit

        actc_earned_income_floor = ctc_p.num("actc_earned_income_floor")
        actc_rate = ctc_p.num("actc_rate")
        actc_earned_formula = actc_rate * (pl.col("earned_income").clip(0, None) - actc_earned_income_floor).clip(0, None)
        if ctc_p.in_effect("actc_max_refundable_per_child"):
            actc_max_per_child = ctc_p.num("actc_max_refundable_per_child")
            actc_cap = pl.min_horizontal(unused_nonrefundable, actc_max_per_child * ideps)
        else:
            actc_cap = unused_nonrefundable
        actc = pl.min_horizontal(actc_cap, actc_earned_formula).clip(0, None)

    df = df.with_columns(
        odc=nonrefundable_credit,
        actc=actc,
    )
    if analytic_rate:
        df = df.with_columns(analytic_ctc=combined_after_phaseout, analytic_ctc_phasing=agi > odc_threshold_expr)
        # The analytic rate reads the columns set above.
        df = df.with_columns(
            federal_source_rate=_law87_analytic_rate(year, _brackets_by_status(year), sepret, behavior)
        )
    return df


def _net_tax(df: pl.DataFrame | pl.LazyFrame, year: int, behavior: BehaviorProfile = TAXSIM_BEHAVIOR) -> pl.DataFrame | pl.LazyFrame:
    """Net investment income tax, rebates and refundable credits, and `fiitax`."""
    # NIIT: added on top of regular tax + AMT, outside the pool nonrefundable
    # credits compete for (see engine/niit.py docstring) - not part of
    # tax_before_credits, added directly into the final total instead.
    agi = pl.col("agi")
    niit_p = YearParams(FEDERAL_NIIT_PARAMS, year)
    niit_threshold_expr = _by_status_at(niit_p["threshold"], year)
    niit_expense_cap = niit_p.num("expense_cap") if niit_p.in_effect("expense_cap") else float("inf")
    niit = net_investment_income_tax(
        net_investment_income=(pl.col("intrec") + _dividends() + _capital_gain(year) + pl.col("otherprop")).clip(0, None),
        agi=agi,
        threshold=niit_threshold_expr,
        rate=niit_p.num("rate"),
        state_tax_deduction_claimed=pl.col("state_sales_or_income_tax_ded"),
        expense_cap=niit_expense_cap,
    )
    df = df.with_columns(niit=niit)
    # The elderly credit can reduce tax (including NIIT) to zero but no further.
    elderly_room = pl.col("tax_before_credits") if year == 2021 else (pl.col("tax_before_credits") - pl.col("ccc")).clip(0, None)
    df = df.with_columns(elderly_credit=pl.min_horizontal(pl.col("elderly_credit_raw"), elderly_room + pl.col("niit")))
    # TAXSIM's nonrefundable credit total (`comnew(58)`, which states read).
    # Before 1998 it is tax before credits when the elderly credit exceeds
    # it, otherwise 0, and does not reduce federal tax; in 2021 only the
    # elderly credit.
    #
    # FED-INCOME-001: before 1998, the only nonrefundable credit in this
    # model's scope is the elderly credit, computed but - by design, not the
    # bug here - never actually applied against `fiitax` those years
    # (`elderly_credit_raw` is zeroed for year<1998 above, so the capped
    # `elderly_credit` used against tax is always 0 pre-1998; that part is
    # unchanged). The bug is what gets *reported* as the credit total:
    # TAXSIM's formula reports tax-before-credits (not the credit amount)
    # when the elderly credit happens to exceed it, and 0 otherwise - never
    # the actual computed credit, `federal_elder`. Several states (LA, UT,
    # OR, ND, AL, AZ) read this total into their own federal-tax-deduction
    # or credit calculations, so this only fixes what's reported, not
    # whether the elderly credit reduces `fiitax` pre-1998 (it still
    # doesn't, matching TAXSIM). See statutory_corrections.md.
    if year < 1998 and behavior.apply_nonrefundable_credits_before_1998:
        nonrefundable_credits = pl.col("ccc") + pl.col("elderly_credit")
    elif year < 1998 and behavior.count_pre_1998_nonrefundable_credits_correctly:
        nonrefundable_credits = pl.col("federal_elder")
    elif year < 1998:
        tax = pl.col("tax_before_credits").clip(0, None)
        nonrefundable_credits = pl.when(pl.col("federal_elder") > tax).then(tax).otherwise(0.0)
    elif year == 2021:
        nonrefundable_credits = pl.col("elderly_credit")
    else:
        nonrefundable_credits = pl.col("ccc") + pl.col("elderly_credit") + pl.col("odc")
    df = df.with_columns(nonrefundable_credits=nonrefundable_credits)

    # Recovery rebate credits (economic impact payments).
    credits_p = YearParams(FEDERAL_CREDITS_PARAMS, year)
    rebate = credits_p.value("recovery_rebate") if credits_p.in_effect("recovery_rebate") else {}
    ncare_expr = pl.when(files_joint()).then(2.0).otherwise(1.0)
    if "payments" in rebate:
        # Each payment is reduced separately and floored at 0.
        phcare_expr = by_filing_status(rebate["phaseout_start"])
        reduction = (rebate["phaseout_rate"] * (agi - phcare_expr)).clip(0, None)
        cares = pl.lit(0.0)
        for payment in rebate["payments"]:
            base = payment["per_adult"] * ncare_expr + payment["per_child"] * pl.col("dep13")
            cares = cares + (base - reduction).clip(0, None)
    elif rebate:
        phcare_expr = by_filing_status(rebate["phaseout_start"])
        phmax_expr = by_filing_status(rebate["phaseout_end"])
        payment = rebate["payment"]
        eip3_base = payment["per_adult"] * ncare_expr + payment["per_dependent"] * pl.col("depx")
        eip3 = (
            pl.when(agi >= phmax_expr)
            .then(0.0)
            .when(agi > phcare_expr)
            .then(eip3_base * (phmax_expr - agi) / (phmax_expr - phcare_expr))
            .otherwise(eip3_base)
        )
        cares = eip3.clip(0, None)
    else:
        cares = pl.lit(0.0)
    # Dependent filers get no recovery rebate.
    df = df.with_columns(cares=pl.when(is_dependent_filer()).then(0.0).otherwise(cares))

    # Making Work Pay credit, per taxpayer.
    if credits_p.in_effect("making_work_pay"):
        mwp = credits_p.value("making_work_pay")
        num_filers_expr = pl.when(files_joint()).then(2.0).otherwise(1.0)
        mwp_max_credit = mwp["max_credit"] * num_filers_expr
        mwp_phaseout_start = mwp["phaseout_start"] * num_filers_expr
        mwp_hard_ceiling = mwp["income_ceiling"] * num_filers_expr
        mwp_phase_in = pl.min_horizontal(mwp_max_credit, mwp["rate"] * pl.col("earned_income"))
        mwp_reduction = mwp["phaseout_rate"] * (agi - mwp_phaseout_start).clip(0, None)
        making_work_pay = pl.when(
            (pl.col("earned_income") > 0) & (agi < mwp_hard_ceiling) & ~is_dependent_filer()
        ).then((mwp_phase_in - mwp_reduction).clip(0, None)).otherwise(0.0)
        benefit_offset = pl.min_horizontal(pl.col("gssi"), mwp["benefit_offset"] * num_filers_expr)
        making_work_pay = pl.when(pl.col("gssi") > 0).then(
            (making_work_pay - benefit_offset).clip(0, None)
        ).otherwise(making_work_pay)
    else:
        making_work_pay = pl.lit(0.0)
    df = df.with_columns(making_work_pay=making_work_pay)

    # Telephone excise tax refund.
    if credits_p.in_effect("telephone_excise_credit"):
        exemps = 1.0 + pl.col("depx") + pl.when(files_joint()).then(1.0).otherwise(0.0)
        exemps = pl.when(is_dependent_filer()).then(0.0).otherwise(exemps)
        tel = credits_p.value("telephone_excise_credit")
        telephone_excise_credit = pl.when(exemps > 0).then(
            tel["per_exemption"] * (pl.min_horizontal(exemps, tel["max_exemptions"]) + tel["base_exemptions"])
        ).otherwise(0.0)
    else:
        telephone_excise_credit = pl.lit(0.0)
    df = df.with_columns(telephone_excise_credit=telephone_excise_credit)

    # Not rounded, so the marginal-rate perturbation of $0.01 survives.
    return df.with_columns(
        fiitax=(
            pl.col("tax_before_credits")
            - pl.col("odc")
            - pl.col("actc")
            - pl.col("ccc")
            - pl.col("elderly_credit")
            - pl.col("eitc")
            - pl.col("cares")
            - pl.col("making_work_pay")
            - pl.col("telephone_excise_credit")
            + pl.col("niit")
        )
    )
