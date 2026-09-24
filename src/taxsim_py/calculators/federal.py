"""Federal individual income tax calculator."""

from collections.abc import Callable

import polars as pl

from taxsim_py.engine.amt import alternative_minimum_tax
from taxsim_py.engine.brackets import bracket_tax
from taxsim_py.engine.capital_gains import preferential_rate_tax
from taxsim_py.engine.credits import child_care_credit_rate, child_care_credit_rate_pre2021
from taxsim_py.engine.eitc import trapezoid_credit
from taxsim_py.engine.niit import net_investment_income_tax
from taxsim_py.engine.payroll_tax import household_self_employment_tax
from taxsim_py.engine.schema import PARAMETERS_ROOT, load_yaml, resolve_year, validate_brackets
from taxsim_py.engine.state import FORCE_ITEMIZE, itemize_choice

# Parameters use published 2023 EITC law rather than TAXSIM's extrapolated
# 2022 values. See the validation documentation for the known divergence.
FEDERAL_INCOME_TAX_PARAMS = load_yaml(PARAMETERS_ROOT / "national" / "income_tax.yaml")
FEDERAL_EITC_PARAMS = pl.read_csv(PARAMETERS_ROOT / "national" / "eitc.csv")
FEDERAL_EITC_PARAMS_MISC = load_yaml(PARAMETERS_ROOT / "national" / "eitc_misc.yaml")
FEDERAL_CREDITS_PARAMS = load_yaml(PARAMETERS_ROOT / "national" / "credits.yaml")
FEDERAL_ITEMIZED_PARAMS = load_yaml(PARAMETERS_ROOT / "national" / "itemized.yaml")
FEDERAL_AMT_PARAMS = load_yaml(PARAMETERS_ROOT / "national" / "amt.yaml")
FEDERAL_NIIT_PARAMS = load_yaml(PARAMETERS_ROOT / "national" / "niit.yaml")
PAYROLL_TAX_PARAMS = load_yaml(PARAMETERS_ROOT / "national" / "payroll_tax.yaml")
FEDERAL_CAPITAL_GAINS_PARAMS = load_yaml(PARAMETERS_ROOT / "national" / "capital_gains.yaml")
FEDERAL_PERSONAL_EXEMPTION_PARAMS = load_yaml(PARAMETERS_ROOT / "national" / "personal_exemption.yaml")

FILING_STATUSES = ["single", "married_joint", "married_separate", "head_of_household"]
# TAXSIM adds this amount to dividend income before federal calculations.
DIVIDENDS_FUDGE = 0.001
# Married-separate returns use half of joint thresholds where applicable.
SEPRET_BY_STATUS = {
    "single": 1.0,
    "married_joint": 1.0,
    "head_of_household": 1.0,
    "married_separate": 2.0,
}


def _with_default(
    df: pl.DataFrame | pl.LazyFrame, column: str, default: float = 0.0
) -> pl.DataFrame | pl.LazyFrame:
    columns = df.collect_schema().names() if isinstance(df, pl.LazyFrame) else df.columns
    if column in columns:
        return df
    return df.with_columns(pl.lit(default).alias(column))


def _by_status_expr(values_by_status: dict[str, float]) -> pl.Expr:
    """Select a value using the filing status column."""
    expr = pl.lit(None, dtype=pl.Float64)
    for status, value in values_by_status.items():
        expr = pl.when(pl.col("filing_status") == status).then(pl.lit(float(value))).otherwise(expr)
    return expr


def _filing_status_expr() -> pl.Expr:
    """Derive filing status from TAXSIM status and dependent inputs."""
    return (
        pl.when(pl.col("mstat").is_in([1, 3]) & (pl.col("depx") > 0))
        .then(pl.lit("head_of_household"))
        .when(pl.col("mstat").is_in([1, 3]))
        .then(pl.lit("single"))
        .when(pl.col("mstat") == 2)
        .then(pl.lit("married_joint"))
        .when(pl.col("mstat").is_in([6, 66]))
        .then(pl.lit("married_separate"))
    )


def compute_regular_tax(
    df: pl.DataFrame | pl.LazyFrame,
    year: int,
) -> pl.DataFrame | pl.LazyFrame:
    if year <= 1976:
        # The pre-1977 law has a separate calculation structure.
        from taxsim_py.calculators.federal_law60 import compute_regular_tax_law60

        return compute_regular_tax_law60(df, year)
    if year <= 1986:
        # The 1977-1986 law has a separate calculation structure.
        from taxsim_py.calculators.federal_pre1987 import compute_regular_tax_pre1987

        return compute_regular_tax_pre1987(df, year)
    df = df.with_columns(
        filing_status=_filing_status_expr(),
        wages=pl.col("pwages") + pl.col("swages"),
    )
    for col in (
        "proptax",
        "otheritem",
        "mortgage",
        "state_sales_or_income_tax_ded",
        "dep13",
        "childcare",
        "intrec",
        "psemp",
        "ssemp",
        "dividends",
        "stcg",
        "ltcg",
        "ui",
        "pui",
        "sui",
        "charity_cash",
    ):
        df = _with_default(df, col)
    df = _with_default(df, FORCE_ITEMIZE, None)

    # AGI includes gross self-employment income and deducts the applicable
    # share of self-employment tax.
    pt_p = PAYROLL_TAX_PARAMS
    setax_total = household_self_employment_tax(
        pl.col("psemp"),
        pl.col("ssemp"),
        pl.col("pwages"),
        pl.col("swages"),
        net_earnings_factor=float(resolve_year(pt_p["se_net_earnings_factor"], year)),
        wage_base=float(resolve_year(pt_p["oasdi_wage_base"], year)),
        se_oasdi_rate=float(resolve_year(pt_p["se_oasdi_rate"], year)),
        se_hi_rate=float(resolve_year(pt_p["se_hi_rate"], year)),
        hi_wage_base=float(resolve_year(pt_p["hi_wage_base"], year)),
    )
    gross_se_income = pl.col("psemp") + pl.col("ssemp")

    # The 2011-2012 payroll-tax holiday uses a special SE-tax deduction.
    if year in (2011, 2012):
        wage_base = float(resolve_year(pt_p["oasdi_wage_base"], year))
        se_deduction_threshold = 0.133 * wage_base
        se_deduction_flat_addon = 0.133 * 0.0751 * wage_base
        se_agi_deduction = (
            pl.when(setax_total <= se_deduction_threshold)
            .then(0.5751 * setax_total)
            .otherwise(0.5 * setax_total + se_deduction_flat_addon)
        )
    else:
        se_agi_deduction = 0.5 * setax_total

    # EITC earned income always deducts the SE-tax share calculated above.
    df = df.with_columns(
        setax=setax_total,
        earned_income=(pl.col("wages") + gross_se_income - se_agi_deduction).clip(0, None),
    )

    # The AGI deduction for self-employment tax begins in 1990.
    se_agi_adjustment = pl.lit(0.0) if year < 1990 else se_agi_deduction
    df = df.with_columns(se_adjustment=se_agi_adjustment)

    # Interest is ordinary income but not earned income. TAXSIM's dividend
    # amount includes a fixed 0.001 adjustment.
    dividends_with_fudge = pl.col("dividends") + DIVIDENDS_FUDGE
    # Net capital gain in AGI; a net loss is limited per return.
    fullcg = pl.col("stcg") + pl.col("ltcg")
    loss_limit = float(resolve_year(FEDERAL_CAPITAL_GAINS_PARAMS["net_capital_loss_limit"], year))
    capgn = pl.max_horizontal(fullcg, -loss_limit / _by_status_expr(SEPRET_BY_STATUS))
    # Net long-term gain after short-term losses, the preferential-rate base.
    net_ltcg = pl.min_horizontal(pl.col("ltcg"), fullcg).clip(0, None)

    # Qualified dividends join the preferential-rate base beginning in 2003.
    if year >= 2003:
        ltg = net_ltcg + dividends_with_fudge
    else:
        ltg = net_ltcg + 0.0

    agi_before_ui = (
        pl.col("wages")
        + pl.col("intrec")
        + gross_se_income
        - se_agi_adjustment
        + capgn
        + dividends_with_fudge
    )

    # Prefer split unemployment amounts when they exceed the combined input.
    ui_total = pl.max_horizontal(pl.col("ui"), pl.col("pui") + pl.col("sui"))
    UI_EXCLUSION_2020 = 10200.0  # uithrs(2020) - taxsim_2022_10_21.f:24237
    if year == 2020:
        # The $10,200 exclusion applies per spouse below the $150,000 cliff.
        excl_spouse = pl.min_horizontal(pl.col("sui"), pl.lit(UI_EXCLUSION_2020))
        excl_primary = (ui_total - pl.col("sui")).clip(0, UI_EXCLUSION_2020)
        exclusion = excl_spouse + excl_primary
        taxable_ui = (
            pl.when(agi_before_ui < 150000.0)
            .then((ui_total - exclusion).clip(0, None))
            .otherwise(ui_total)
        )
    elif year == 2009:
        # The 2009 exclusion is $2,400 per return with no income cliff.
        taxable_ui = (ui_total - 2400.0).clip(0, None)
    else:
        taxable_ui = ui_total

    df = df.with_columns(
        ltg=ltg,
        taxable_unemployment=taxable_ui,
        agi=agi_before_ui + taxable_ui,
    )

    sepret_expr = _by_status_expr(SEPRET_BY_STATUS)

    # Married-separate uses half of the joint standard deduction.
    std_ded_by_status = {
        status: resolve_year(FEDERAL_INCOME_TAX_PARAMS["standard_deduction"][status], year)
        for status in FILING_STATUSES
        if status != "married_separate"
    }
    std_ded_by_status["married_separate"] = std_ded_by_status["married_joint"] / 2.0
    brackets_by_status = {}
    for status in FILING_STATUSES:
        brackets = resolve_year(FEDERAL_INCOME_TAX_PARAMS["brackets"][status], year)
        validate_brackets(brackets, context=f"federal.{status}.{year}")
        brackets_by_status[status] = brackets

    std_ded_expr = _by_status_expr(std_ded_by_status)
    if year >= 2008:
        property_tax_cap = float(
            resolve_year(FEDERAL_INCOME_TAX_PARAMS["standard_deduction_real_property_tax_cap"], year)
        )
        num_filers = pl.when(pl.col("filing_status") == "married_joint").then(2.0).otherwise(1.0)
        std_ded_expr = std_ded_expr + pl.min_horizontal(
            pl.col("proptax").clip(0, None), property_tax_cap * num_filers
        )
    df = df.with_columns(standard_deduction=std_ded_expr)

    # SALT is capped per return beginning in 2018.
    salt_uncapped_expr = pl.col("proptax") + pl.col("otheritem") + pl.col("state_sales_or_income_tax_ded")
    if year >= 2018:
        salt_cap = float(resolve_year(FEDERAL_ITEMIZED_PARAMS["salt_cap"], year))
        salt_capped = salt_uncapped_expr.clip(0, None).clip(0, salt_cap / sepret_expr)
    else:
        salt_capped = salt_uncapped_expr.clip(0, None)

    # The cash-contribution AGI cap is suspended for 2020 and 2021.
    agix = pl.col("agi").clip(0, None)
    alim50 = pl.lit(1.0e20) if year in (2020, 2021) else 0.5 * agix
    char_cash_itemized = pl.min_horizontal(alim50, pl.col("charity_cash")).clip(0, None)
    itemized_deduction = salt_capped + pl.col("mortgage") + char_cash_itemized

    # Pease applies from 1991, except 2010-2012, and is suspended from 2018.
    if year < 1991:
        pass
    elif 2010 <= year <= 2012:
        pass  # itemized_deduction unreduced - Pease fully off these years
    elif year < 2018:
        pease_p = FEDERAL_ITEMIZED_PARAMS
        pease_threshold_expr = _by_status_expr(
            {status: resolve_year(pease_p["pease_limitation_threshold"][status], year) for status in FILING_STATUSES}
        )
        pease_reduction_rate = float(resolve_year(pease_p["pease_reduction_rate"], year))
        pease_cap_rate = float(resolve_year(pease_p["pease_cap_rate"], year))
        dlim1 = pease_reduction_rate * (pl.col("agi") - pease_threshold_expr).clip(0, None)
        dlim2 = pease_cap_rate * itemized_deduction.clip(0, None)
        pease_reduction = pl.min_horizontal(dlim1, dlim2)
        if year in (2006, 2007):
            # Apply the statutory Pease phase-down.
            pease_reduction = pease_reduction * 2.0 / 3.0
        if year in (2008, 2009):
            pease_reduction = pease_reduction / 3.0
        itemized_deduction = itemized_deduction - pease_reduction

    # Standard filers receive the temporary 2020-2021 cash-charity deduction.
    if year == 2020:
        cas = pl.min_horizontal(pl.lit(300.0) / sepret_expr, pl.col("charity_cash"))
    elif year == 2021:
        num_filers = pl.when(pl.col("filing_status") == "married_joint").then(2.0).otherwise(1.0)
        cas = pl.min_horizontal(300.0 * num_filers, pl.col("charity_cash"))
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
        itemizes=itemizes_expr,
    )
    deduction = pl.when(pl.col("itemizes")).then(pl.col("itemized_deduction")).otherwise(std_ded_expr + cas)

    # Personal/dependent exemptions - suspended entirely 2018-2025 by TCJA
    # (`amex` stays $0 for those years, never computed). Scope: filer +
    # spouse (if married_joint) + depx, phased out above a high-income
    # threshold (PEP) - taxsim_2022_10_21.f:24738-24816.
    if year < 2018:
        pe_p = FEDERAL_PERSONAL_EXEMPTION_PARAMS
        exemption_amount = float(resolve_year(pe_p["amount"], year))
        exemption_count = 1.0 + pl.col("depx") + pl.when(pl.col("filing_status") == "married_joint").then(1.0).otherwise(0.0)
        amex_base = exemption_amount * exemption_count
        if 2010 <= year <= 2012:
            # PEP fully (not just partially) repealed these years - `ratio`
            # forced to 0 unconditionally, not a higher threshold.
            # taxsim_2022_10_21.f:24798-24800.
            amex = amex_base
        elif year < 1991:
            # The Personal Exemption Phaseout didn't exist in law before
            # 1991 either (OBRA1990 created it alongside Pease) - the
            # source's `ratio` stays at its initialized 0 the entire way
            # through for lawyr<1991 (neither the `in(lawyr,1991,1996)` nor
            # the `lawyr.ge.1997` branch below it ever executes), so `amex`
            # is never reduced. No parameter lookup needed - personal_
            # exemption.yaml's high_income_phaseout_threshold has no
            # 1988-1990 entries on purpose.
            amex = amex_base
        elif year <= 1996:
            # A genuinely different, older PEP threshold formula for
            # 1991-1996: the threshold
            # is computed from an inflation-adjusted base
            # (`exmphl = filing(...)*xndx/1.143`, taxsim_2022_10_21.f:
            # 24754-24760) rather than a per-year table - precomputed into
            # personal_exemption.yaml's high_income_phaseout_threshold as
            # plain dollar values (same technique as every other
            # parameter extraction this project uses, not reimplemented
            # here). The ratio itself isn't clipped at 1 in the source
            # here (unlike the modern `.clip(0, 1)` below) - functionally
            # identical anyway, since `amex` still floors at 0 either way.
            pep_threshold_expr = _by_status_expr(
                {status: resolve_year(pe_p["high_income_phaseout_threshold"][status], year) for status in FILING_STATUSES}
            )
            pep_rate = float(resolve_year(pe_p["phaseout_rate"], year))
            pep_bracket_size = float(resolve_year(pe_p["phaseout_bracket_size"], year))
            pep_ratio = pep_rate * (pl.col("agi") - pep_threshold_expr).clip(0, None) / (pep_bracket_size / sepret_expr)
            amex = (amex_base * (1.0 - pep_ratio)).clip(0, None)
        else:
            pep_threshold_expr = _by_status_expr(
                {status: resolve_year(pe_p["high_income_phaseout_threshold"][status], year) for status in FILING_STATUSES}
            )
            pep_rate = float(resolve_year(pe_p["phaseout_rate"], year))
            pep_bracket_size = float(resolve_year(pe_p["phaseout_bracket_size"], year))
            pep_ratio = (
                pep_rate * (pl.col("agi") - pep_threshold_expr).clip(0, None) / (pep_bracket_size / sepret_expr)
            ).clip(0, 1)
            amphs_fraction = 1.0
            if year in (2006, 2007):
                # Same gradual-phase-out timeline as Pease above - 2006 and
                # 2007 apply 2/3 of the exemption reduction the ratio would
                # otherwise imply, 2008-2009 only 1/3.
                # taxsim_2022_10_21.f:24805-24810.
                amphs_fraction = 2.0 / 3.0
            if year in (2008, 2009):
                amphs_fraction = 1.0 / 3.0
            amex = amex_base * (1.0 - pep_ratio * amphs_fraction)
    else:
        amex = pl.lit(0.0)

    df = df.with_columns(
        personal_exemptions=amex,
        taxable_income=(pl.col("agi") - deduction - amex).clip(0, None),
    )

    # ltg can't exceed taxable income itself (the preferential-rate base is
    # capped by taxable income the same way the source's worksheet does via
    # its min(taxinc, ...) terms).
    ltg_capped = pl.min_horizontal(pl.col("ltg"), pl.col("taxable_income"))
    ordinary_income = (pl.col("taxable_income") - ltg_capped).clip(0, None)

    tax_expr = pl.lit(None, dtype=pl.Float64)
    for status, brackets in brackets_by_status.items():
        tax_expr = (
            pl.when(pl.col("filing_status") == status)
            .then(bracket_tax(ordinary_income, brackets))
            .otherwise(tax_expr)
        )

    cg_p = FEDERAL_CAPITAL_GAINS_PARAMS
    rate_0_ceiling_expr = _by_status_expr(
        {status: resolve_year(cg_p["rate_0_ceiling"][status], year) for status in FILING_STATUSES}
    )
    rate_15_ceiling_expr = _by_status_expr(
        {status: resolve_year(cg_p["rate_15_ceiling"][status], year) for status in FILING_STATUSES}
    )
    plain_ordinary_tax = pl.lit(None, dtype=pl.Float64)
    for status, brackets in brackets_by_status.items():
        plain_ordinary_tax = (
            pl.when(pl.col("filing_status") == status)
            .then(bracket_tax(pl.col("taxable_income"), brackets))
            .otherwise(plain_ordinary_tax)
        )

    if year == 1987:
        # The 1987 transition caps gains above the 28% bracket ceiling while
        # preserving ordinary bracket tax below it. See `tax87` in TAXSIM.
        rate_28_ceiling_expr = _by_status_expr(
            {status: resolve_year(cg_p["rate_28_ceiling"][status], year) for status in FILING_STATUSES}
        )
        tax_at_ttab = pl.lit(None, dtype=pl.Float64)
        for status, brackets in brackets_by_status.items():
            tax_at_ttab = (
                pl.when(pl.col("filing_status") == status)
                .then(bracket_tax(rate_28_ceiling_expr, brackets))
                .otherwise(tax_at_ttab)
            )
        tax_case_c = tax_at_ttab + 0.28 * (pl.col("taxable_income") - rate_28_ceiling_expr)
        tax_case_a = tax_expr + 0.28 * ltg_capped
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
        alt_ordinary_tax = pl.lit(None, dtype=pl.Float64)
        for status, brackets in brackets_by_status.items():
            alt_ordinary_tax = (
                pl.when(pl.col("filing_status") == status)
                .then(bracket_tax(taxin3, brackets))
                .otherwise(alt_ordinary_tax)
            )
        alt_tax = alt_ordinary_tax + 0.28 * excess
        gated = (ltg_capped > 0) & (pl.col("taxable_income") > rate_15_ceiling_expr)
        # Below the gate, restore plain bracket tax.
        preferential_tax = pl.when(gated).then(alt_tax - tax_expr).otherwise(plain_ordinary_tax - tax_expr)
    elif year <= 1996:
        # Cap the marginal rate on gains at 28%, with ordinary income floored
        # at the start of the 28% bracket.
        taxin3 = pl.max_horizontal(rate_0_ceiling_expr, pl.col("taxable_income") - ltg_capped)
        excess = (pl.col("taxable_income") - taxin3).clip(0, None)
        alt_ordinary_tax = pl.lit(None, dtype=pl.Float64)
        for status, brackets in brackets_by_status.items():
            alt_ordinary_tax = (
                pl.when(pl.col("filing_status") == status)
                .then(bracket_tax(taxin3, brackets))
                .otherwise(alt_ordinary_tax)
            )
        alt_tax = alt_ordinary_tax + 0.28 * excess
        preferential_tax = pl.when(ltg_capped > 0).then(alt_tax - tax_expr).otherwise(0.0)
    elif 1997 <= year <= 2000:
        # Apply the capped three-tier worksheet at the historical 10%/20%
        # capital-gains rates.
        preferential_tax = preferential_rate_tax(
            pl.col("taxable_income"),
            ltg_capped,
            rate_0_ceiling_expr,
            rate_15_ceiling_expr,
            rate_15=float(resolve_year(cg_p["rate_15"], year)),
            rate_20=float(resolve_year(cg_p["rate_20"], year)),
            rate_0=float(resolve_year(cg_p["rate_0"], year)),
        )
    elif year <= 2002:
        # TAXSIM leaves the 2001-2002 low-rate room uncapped by available
        # gains; the final minimum below prevents tax above ordinary rates.
        low_room = (rate_0_ceiling_expr - ordinary_income).clip(0, None)
        rate_0 = float(resolve_year(cg_p["rate_0"], year))
        rate_top = float(resolve_year(cg_p["rate_15"], year))
        sch10 = rate_0 * low_room
        above_room = (ltg_capped - low_room).clip(0, None)
        sch20 = rate_top * above_room
        preferential_tax = sch10 + sch20
    elif year == 2003:
        # In 2003, dividends use the new 5%/15% rates before gains consume
        # the remaining room at the old 10%/20% rates.
        low_room = (pl.min_horizontal(pl.col("taxable_income"), rate_0_ceiling_expr) - ordinary_income).clip(
            0, None
        )
        dividends_in_low = pl.min_horizontal(low_room, dividends_with_fudge)
        sch5 = 0.05 * dividends_in_low
        gain_in_low = low_room - dividends_in_low
        sch10 = 0.10 * gain_in_low
        above_room = (ltg_capped - low_room).clip(0, None)
        dividends_remaining = dividends_with_fudge - dividends_in_low
        dividends_in_high = pl.min_horizontal(above_room, dividends_remaining)
        sch15 = 0.15 * dividends_in_high
        gain_in_high = above_room - dividends_in_high
        sch20 = 0.20 * gain_in_high
        preferential_tax = sch5 + sch10 + sch15 + sch20
    else:
        preferential_tax = preferential_rate_tax(
            pl.col("taxable_income"),
            ltg_capped,
            rate_0_ceiling_expr,
            rate_15_ceiling_expr,
            rate_15=float(resolve_year(cg_p["rate_15"], year)),
            rate_20=float(resolve_year(cg_p["rate_20"], year)),
            rate_0=float(resolve_year(cg_p["rate_0"], year)),
        )

    # Schedule D tax cannot exceed ordinary bracket tax on total income.
    regular_tax = pl.min_horizontal(plain_ordinary_tax, tax_expr + preferential_tax)

    if 1988 <= year <= 1996:
        # The 1988-1996 exemption surtax is capped at 28% of the reduced
        # personal exemption.
        pep_p = FEDERAL_PERSONAL_EXEMPTION_PARAMS
        exemption_surtax_threshold_expr = _by_status_expr(
            {
                status: resolve_year(pep_p["exemption_surtax_threshold"][status], year)
                for status in FILING_STATUSES
            }
        )
        exemption_surtax = pl.min_horizontal(
            (0.05 * (pl.col("taxable_income") - exemption_surtax_threshold_expr).clip(0, None)),
            0.28 * amex,
        )
        regular_tax = regular_tax + exemption_surtax

    if 1988 <= year <= 1990:
        # The 1988-1990 rate-bubble surtax claws back 15% bracket savings.
        income_tax_p = FEDERAL_INCOME_TAX_PARAMS
        bubble_threshold_expr = _by_status_expr(
            {
                status: resolve_year(income_tax_p["bubble_surtax_threshold"][status], year)
                for status in FILING_STATUSES
            }
        )
        bubble_cap_expr = _by_status_expr(
            {status: resolve_year(income_tax_p["bubble_surtax_cap"][status], year) for status in FILING_STATUSES}
        )
        bubble_surtax = pl.min_horizontal(
            (0.05 * (pl.col("taxable_income") - bubble_threshold_expr).clip(0, None)),
            bubble_cap_expr,
        )
        regular_tax = regular_tax + bubble_surtax

    df = df.with_columns(
        regular_tax=regular_tax,
        num_children=pl.col("dep18").clip(0, 3),
    )

    # AMT income: AGI, minus mortgage interest if itemizing (SALT is never
    # deductible for AMT; mortgage interest is deductible for both).
    amt_p = FEDERAL_AMT_PARAMS
    amt_exemption_expr = _by_status_expr(
        {status: resolve_year(amt_p["exemption"][status], year) for status in FILING_STATUSES}
    )
    amt_phaseout_threshold_expr = _by_status_expr(
        {
            status: resolve_year(amt_p["exemption_phaseout_threshold"][status], year)
            for status in FILING_STATUSES
        }
    )
    amt_income = pl.col("agi") - pl.when(pl.col("itemizes")).then(pl.col("mortgage")).otherwise(0.0)
    # Preferential AMT treatment for capital gains begins in 1997.
    # The MFS addback cap equals the MFS exemption for the same year.
    separate_addback_kwargs = {}
    if year >= 1990:
        separate_addback_kwargs = dict(
            separate_return_addback_cap=resolve_year(amt_p["exemption"]["married_separate"], year),
            separate_return_addback_threshold=resolve_year(amt_p["separate_return_addback_threshold"], year),
        )
    if year <= 1996:
        amt = alternative_minimum_tax(
            amt_income=amt_income,
            regular_tax=pl.col("regular_tax"),
            exemption=amt_exemption_expr,
            exemption_phaseout_threshold=amt_phaseout_threshold_expr,
            exemption_phaseout_rate=float(resolve_year(amt_p["exemption_phaseout_rate"], year)),
            rate_breakpoint=float(resolve_year(amt_p["rate_breakpoint"], year)),
            rate_below_breakpoint=float(resolve_year(amt_p["rate_below_breakpoint"], year)),
            rate_above_breakpoint=float(resolve_year(amt_p["rate_above_breakpoint"], year)),
            sepret=sepret_expr,
            **separate_addback_kwargs,
        )
    else:
        amt = alternative_minimum_tax(
            amt_income=amt_income,
            regular_tax=pl.col("regular_tax"),
            exemption=amt_exemption_expr,
            exemption_phaseout_threshold=amt_phaseout_threshold_expr,
            exemption_phaseout_rate=float(resolve_year(amt_p["exemption_phaseout_rate"], year)),
            rate_breakpoint=float(resolve_year(amt_p["rate_breakpoint"], year)),
            rate_below_breakpoint=float(resolve_year(amt_p["rate_below_breakpoint"], year)),
            rate_above_breakpoint=float(resolve_year(amt_p["rate_above_breakpoint"], year)),
            sepret=sepret_expr,
            **separate_addback_kwargs,
            ltg=pl.col("ltg"),
            regular_taxable_income=pl.col("taxable_income"),
            cg_rate_0_ceiling=rate_0_ceiling_expr,
            cg_rate_15_ceiling=_by_status_expr(
                {status: resolve_year(amt_p["cg_rate_15_ceiling"][status], year) for status in FILING_STATUSES}
            ),
            cg_rate_15=float(resolve_year(cg_p["rate_15"], year)),
            cg_rate_0=float(resolve_year(cg_p["rate_0"], year)),
        )
    # Preserve precision for the $0.01 perturbation used by marginal rates.
    df = df.with_columns(amt=amt)
    if year <= 1999:
        # TAXSIM computes AMT before 2000 but does not add it to liability.
        df = df.with_columns(tax_before_credits=pl.col("regular_tax"))
    else:
        df = df.with_columns(tax_before_credits=pl.col("regular_tax") + pl.col("amt"))

    # Apply CCC before CTC/ODC in the nonrefundable-credit stacking order.
    ccc_p = FEDERAL_CREDITS_PARAMS["child_care_credit"]
    max_qualifying_persons = float(resolve_year(ccc_p["max_qualifying_persons"], year))
    if year == 2021:
        # ARPA enhanced the CCC expense cap and rate for 2021 only.
        max_expense_per_person = float(resolve_year(ccc_p["max_expense_per_person"], year))
        ccc_rate = child_care_credit_rate(
            pl.col("agi"),
            first_phase_start=float(resolve_year(ccc_p["first_phase_start"], year)),
            first_phase_ceiling=float(resolve_year(ccc_p["first_phase_ceiling"], year)),
            first_phase_step_amount=float(resolve_year(ccc_p["first_phase_step_amount"], year)),
            top_rate=float(resolve_year(ccc_p["top_rate"], year)),
            mid_rate=float(resolve_year(ccc_p["mid_rate"], year)),
            second_phase_start=float(resolve_year(ccc_p["second_phase_start"], year)),
            second_phase_ceiling=float(resolve_year(ccc_p["second_phase_ceiling"], year)),
            second_phase_step_amount=float(resolve_year(ccc_p["second_phase_step_amount"], year)),
        )
    else:
        # The standard CCC rate steps down to a 20% floor.
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
    ccc_earned_income_cap = (
        pl.when(pl.col("mstat") == 2)
        .then(pl.min_horizontal(pl.col("pwages"), pl.col("swages")))
        .otherwise(pl.col("wages"))
    )
    ccc_expense = pl.min_horizontal(qualifying_expense, ccc_earned_income_cap).clip(0, None)
    ccc_amount = ccc_rate * ccc_expense
    if year == 2021:
        # ARPA made the CCC fully refundable for 2021.
        ccc = ccc_amount.clip(0, None)
    elif year < 1998:
        # TAXSIM does not apply the computed CCC before 1998.
        ccc = pl.lit(0.0)
    else:
        ccc = pl.min_horizontal(ccc_amount, pl.col("tax_before_credits").clip(0, None))
    # `ccc_uncapped` is the credit before the tax-liability cap (`chcrst`).
    df = df.with_columns(ccc=ccc, ccc_uncapped=ccc_amount.clip(0, None))

    eitc_params_year = FEDERAL_EITC_PARAMS.filter(pl.col("year") == year).select(
        "filing_status", "num_children", "rate_in", "max_credit", "phaseout_start", "rate_out"
    )
    if isinstance(df, pl.LazyFrame):
        eitc_params_year = eitc_params_year.lazy()
    df = df.join(eitc_params_year, on=["filing_status", "num_children"], how="left")

    eitc_ordinary = (
        pl.when(pl.col("rate_in").is_not_null())
        .then(
            trapezoid_credit(
                pl.col("earned_income"),  # phase-in base: wages + gross SE income - .5*SE tax
                pl.col("agi"),  # phaseout compares the greater of earned income or AGI
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
        disqy = capgn.clip(0, None) + dividends_with_fudge + pl.col("intrec")
        eitc_reduction = (disqy - dylim).clip(0, None)
    df = df.with_columns(eitc=(eitc_ordinary - eitc_reduction).clip(0, None))

    ctc_p = FEDERAL_CREDITS_PARAMS["child_tax_credit"]

    ideps = pl.col("dep17")  # CTC-qualifying children
    young = pl.min_horizontal(pl.col("dep6"), ideps)
    agi = pl.col("agi")

    if year >= 2018:
        odc_p = FEDERAL_CREDITS_PARAMS["other_dependent_credit"]
        odc_amount = float(resolve_year(odc_p["amount"], year))
        odc_rate_per_1000 = float(resolve_year(odc_p["phaseout_rate_per_1000"], year))
        odc_threshold_expr = _by_status_expr(
            {status: resolve_year(odc_p["phaseout_threshold"][status], year) for status in FILING_STATUSES}
        )
        odc_base = odc_amount * (pl.col("depx") - ideps).clip(0, None)
    else:
        # Before TCJA, CTC uses the fixed pre-2018 phaseout threshold.
        odc_base = pl.lit(0.0)
        odc_rate_per_1000 = float(resolve_year(ctc_p["pre2018_phaseout_rate_per_1000"], year))
        odc_threshold_expr = _by_status_expr(
            {
                status: resolve_year(ctc_p["pre2018_phaseout_threshold"][status], year)
                for status in FILING_STATUSES
            }
        )

    if year == 2021:
        # ARPA enhanced CTC amounts and added a low-income phaseout in 2021.
        young_child_amount = float(resolve_year(ctc_p["young_child_amount"], year))
        older_child_amount = float(resolve_year(ctc_p["older_child_amount"], year))
        base_amount_offset = float(resolve_year(ctc_p["base_amount_offset"], year))
        enhanced_cap_expr = _by_status_expr(
            {status: resolve_year(ctc_p["enhanced_cap"][status], year) for status in FILING_STATUSES}
        )
        low_income_threshold_expr = _by_status_expr(
            {
                status: resolve_year(ctc_p["low_income_phaseout_threshold"][status], year)
                for status in FILING_STATUSES
            }
        )
        low_income_rate = float(resolve_year(ctc_p["low_income_phaseout_rate"], year))

        ccrmax = young * young_child_amount + (ideps - young) * older_child_amount
        enhanced_amount = pl.min_horizontal(ccrmax - ideps * base_amount_offset, enhanced_cap_expr)
        low_income_reduction = ((agi - low_income_threshold_expr).clip(0, None)) * low_income_rate
        ctc_before_tcja_phaseout = ccrmax - pl.min_horizontal(enhanced_amount, low_income_reduction)
    else:
        # Other years use a flat amount per qualifying child.
        flat_amount = float(resolve_year(ctc_p["flat_amount_pre2021"], year))
        ctc_before_tcja_phaseout = flat_amount * ideps

    # TAXSIM computes ODC after 2021 but does not add it to `precrd`.
    combined_base = ctc_before_tcja_phaseout + (odc_base if year <= 2021 else pl.lit(0.0))

    # ODC and CTC share the same $200k/$400k TCJA-era phaseout threshold/rate.
    # Computed directly here (rather than via engine.credits' shared helper)
    # because ACTC below needs the after-phaseout, before-nonrefundable-cap
    # value too, not just the final capped credit.
    tcja_reduction = ((agi - odc_threshold_expr).clip(0, None) / 1000) * odc_rate_per_1000
    combined_after_phaseout = (combined_base - tcja_reduction).clip(0, None)

    if year == 2021:
        # ARPA makes the full post-phaseout amount refundable in 2021.
        nonrefundable_credit = pl.lit(0.0)
        actc = combined_after_phaseout
    elif year < 2001:
        # ACTC refundability begins in 2001.
        remaining_after_ccc = (pl.col("tax_before_credits") - pl.col("ccc")).clip(0, None)
        nonrefundable_credit = pl.min_horizontal(combined_after_phaseout, remaining_after_ccc)
        actc = pl.lit(0.0)
    else:
        remaining_after_ccc = (pl.col("tax_before_credits") - pl.col("ccc")).clip(0, None)
        nonrefundable_credit = pl.min_horizontal(combined_after_phaseout, remaining_after_ccc)
        unused_nonrefundable = combined_after_phaseout - nonrefundable_credit

        actc_earned_income_floor = float(resolve_year(ctc_p["actc_earned_income_floor"], year))
        actc_rate = float(resolve_year(ctc_p["actc_rate"], year))
        actc_earned_formula = actc_rate * (agi - actc_earned_income_floor).clip(0, None)
        if year >= 2018:
            actc_max_per_child = float(resolve_year(ctc_p["actc_max_refundable_per_child"], year))
            actc_cap = pl.min_horizontal(unused_nonrefundable, actc_max_per_child * ideps)
        else:
            # Before TCJA, ACTC has no separate per-child refundable cap.
            actc_cap = unused_nonrefundable
        actc = pl.min_horizontal(actc_cap, actc_earned_formula).clip(0, None)

    df = df.with_columns(
        odc=nonrefundable_credit,
        actc=actc,
    )

    # NIIT: added on top of regular tax + AMT, outside the pool nonrefundable
    # credits compete for (see engine/niit.py docstring) - not part of
    # tax_before_credits, added directly into the final total instead.
    niit_p = FEDERAL_NIIT_PARAMS
    niit_threshold_expr = _by_status_expr(
        {status: resolve_year(niit_p["threshold"][status], year) for status in FILING_STATUSES}
    )
    # The state-tax-deduction offset against net investment income is
    # uncapped before 2018 too (matching SALT itself) - no `expense_cap`
    # entry exists for year<2018 on purpose (niit.yaml).
    niit_expense_cap = float(resolve_year(niit_p["expense_cap"], year)) if year >= 2018 else float("inf")
    niit = net_investment_income_tax(
        net_investment_income=(pl.col("intrec") + dividends_with_fudge + capgn).clip(0, None),
        agi=agi,
        threshold=niit_threshold_expr,
        rate=float(resolve_year(niit_p["rate"], year)),
        state_tax_deduction_claimed=pl.col("state_sales_or_income_tax_ded"),
        expense_cap=niit_expense_cap,
    )
    df = df.with_columns(niit=niit)

    # 2020-21 Refundable Recovery Rebate Credit (Economic Impact Payments),
    # taxsim_2022_10_21.f:26055-26089. `ncare` = 1, or 2 for a joint return;
    # `phcare`/`phmax` scale head_of_household by 1.5x, matching real IRS
    # guidance (not a taxsim-specific quirk - confirmed by tracing our own
    # depx>0-implies-HoH filers through the source's internal mstat=4
    # renormalization, taxsim_2022_10_21.f:21165-21167). Dependent-taxpayer
    # exclusion (`data(105)`) is never triggered for any mstat we support,
    # so it's not modeled.
    ncare_expr = pl.when(pl.col("filing_status") == "married_joint").then(2.0).otherwise(1.0)
    phcare_expr = _by_status_expr(
        {"single": 75000.0, "married_joint": 150000.0, "head_of_household": 112500.0, "married_separate": 75000.0}
    )
    if year == 2020:
        # $1,200/$600 per adult + $500/$600 per `dep13` (the CCC-eligible,
        # age-adjusted count - the source reuses that exact field, not
        # dep17/depx, for this add-on: taxsim_2022_10_21.f:26066-26072).
        # Reduced 5 cents per dollar of AGI over phcare, each floored at 0
        # independently (eip2 can hit $0 before eip1 if its base is
        # smaller). Confirmed via several isolated probes against
        # taxsim2022.exe.
        eip1_base = 1200.0 * ncare_expr + 500.0 * pl.col("dep13")
        eip2_base = 600.0 * ncare_expr + 600.0 * pl.col("dep13")
        reduction = (0.05 * (agi - phcare_expr)).clip(0, None)
        cares = (eip1_base - reduction).clip(0, None) + (eip2_base - reduction).clip(0, None)
    elif year == 2021:
        # $1,400 per adult + $1,400 per `depx` (ALL dependents this time,
        # not dep13/dep17 - taxsim_2022_10_21.f:26076-26082). Phases out
        # linearly (not 5-cents-per-dollar) over a band from phcare to
        # phmax=80000*(the SAME status multiplier phcare uses: 1/1.5/2 for
        # single-or-MFS/HoH/joint) - NOT "phcare + 5000*ncare_expr": that
        # shortcut coincidentally works for single (75000+5000=80000) and
        # joint (150000+10000=160000) since ncare_expr there is 1/2, but
        # HoH's own multiplier is 1.5, not 1 - phcare=112500 + 5000*1 =
        # 117500 != phmax=120000. Found via a real test mismatch
        # (head_of_household, agi=115000: expected fiitax $6,982, got
        # $7,681 - the wrong phmax put agi=115000 outside the phase-out
        # band entirely, zeroing EIP3 instead of partially phasing it).
        phmax_expr = _by_status_expr(
            {
                "single": 80000.0,
                "married_joint": 160000.0,
                "head_of_household": 120000.0,
                "married_separate": 80000.0,
            }
        )
        eip3_base = 1400.0 * ncare_expr + 1400.0 * pl.col("depx")
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
    df = df.with_columns(cares=cares)

    # 2009-2010 Making Work Pay Credit (ARRA), refundable - $400 single/
    # HoH/MFS, $800 joint (`data(7)`-based, so HoH does NOT get the 1.5x
    # scaling the 2020-21 Recovery Rebate Credit's phcare/phmax get above -
    # confirmed by reading the source's own formula, which uses `data(7)`
    # directly, not the mstat=4 HoH branch used elsewhere). Phases in at
    # 6.2% of earned income up to that cap, phases out 2 cents per dollar
    # of AGI over $75k single/$150k joint, and is hard-zeroed above a
    # $95k/$190k AGI ceiling regardless of the phase-out formula's own
    # crossing point. The offset for a one-time $250 Economic Recovery
    # Payment to Social Security recipients (`data(91)`, gssi) is not
    # modeled - gssi isn't an input this project tracks at all yet, so
    # that term is always $0 here, same as every other place gssi would
    # matter. taxsim_2022_10_21.f:25971-25997.
    if year in (2009, 2010):
        num_filers_expr = pl.when(pl.col("filing_status") == "married_joint").then(2.0).otherwise(1.0)
        mwp_max_credit = 400.0 * num_filers_expr
        mwp_phaseout_start = 75000.0 * num_filers_expr
        mwp_hard_ceiling = 95000.0 * num_filers_expr
        mwp_phase_in = pl.min_horizontal(mwp_max_credit, 0.062 * pl.col("earned_income"))
        mwp_reduction = (0.02 * (agi - mwp_phaseout_start).clip(0, None))
        making_work_pay = pl.when((pl.col("earned_income") > 0) & (agi < mwp_hard_ceiling)).then(
            (mwp_phase_in - mwp_reduction).clip(0, None)
        ).otherwise(0.0)
    else:
        making_work_pay = pl.lit(0.0)
    df = df.with_columns(making_work_pay=making_work_pay)

    # 2006-only refundable Credit for Federal Telephone Excise Tax Paid
    # (`telcr`): a one-time flat refund, $10 per exemption up to 4, plus a
    # flat $20 - i.e. $30 for 1 exemption, $40 for 2, ..., capping at $60
    # for 4+ - regardless of any actual telephone excise tax paid (not an
    # input this project tracks). taxsim_2022_10_21.f:26051-26054.
    if year == 2006:
        exemps = 1.0 + pl.col("depx") + pl.when(pl.col("filing_status") == "married_joint").then(1.0).otherwise(0.0)
        telephone_excise_credit = pl.when(exemps > 0).then(10.0 * (pl.min_horizontal(exemps, 4.0) + 2.0)).otherwise(0.0)
    else:
        telephone_excise_credit = pl.lit(0.0)
    df = df.with_columns(telephone_excise_credit=telephone_excise_credit)

    # Not rounded: kept at full precision like the source, which only rounds
    # at final display. Rounding here would swamp compute_marginal_rate()'s
    # $0.01 perturbation. Round fiitax at the point of comparison/display.
    return df.with_columns(
        fiitax=(
            pl.col("tax_before_credits")
            - pl.col("odc")
            - pl.col("actc")
            - pl.col("ccc")
            - pl.col("eitc")
            - pl.col("cares")
            - pl.col("making_work_pay")
            - pl.col("telephone_excise_credit")
            + pl.col("niit")
        )
    )


def compute_marginal_rate(
    df: pl.DataFrame,
    year: int,
    refresh_dependent_columns: Callable[[pl.DataFrame], pl.DataFrame] | None = None,
) -> pl.DataFrame:
    """Calculate the federal marginal income tax rate."""
    diff = 0.01
    base = compute_regular_tax(df, year).rename({"fiitax": "fiitax_base"})

    plus = df.with_columns(pwages=pl.col("pwages") + diff)
    if refresh_dependent_columns is not None:
        plus = refresh_dependent_columns(plus)
    fiitax_plus = compute_regular_tax(plus, year).select("fiitax").rename({"fiitax": "fiitax_plus"})

    minus = df.with_columns(pwages=pl.col("pwages") - diff)
    if refresh_dependent_columns is not None:
        minus = refresh_dependent_columns(minus)
    fiitax_minus = compute_regular_tax(minus, year).select("fiitax").rename({"fiitax": "fiitax_minus"})

    base = pl.concat([base, fiitax_plus, fiitax_minus], how="horizontal")
    rate_plus = 100 * (pl.col("fiitax_plus") - pl.col("fiitax_base")) / diff
    rate_minus = 100 * (pl.col("fiitax_base") - pl.col("fiitax_minus")) / diff
    frate = pl.when(rate_plus.abs() < 100).then(rate_plus).otherwise(rate_minus)

    base = base.with_columns(frate=frate.round(2))
    return base.drop("fiitax_plus", "fiitax_minus").rename({"fiitax_base": "fiitax"})
