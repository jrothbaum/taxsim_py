"""Social Security + Medicare payroll tax on wages and self-employment
income, per spouse. TAXSIM reports the combined employer+employee economic
burden (`fica`), the taxpayer's own marginal payroll-tax rate (`ficar`),
and the primary taxpayer's own liability alone (`tfica`, "ssmed" in the
source) - see taxsim_2022_10_21.f:22166-22457.

All functions take one spouse's wages/SE income at a time; callers sum
across spouses for household totals (`fica`) or combine as documented for
household-level items (Additional Medicare Tax, marginal rate).

Self-employment income shares the *same* OASDI wage base as wages, with
wages counted first: `remaining_room_oasdi_tax` implements this as a single
continuous clip rather than the source's three-way branch
(taxsim_2022_10_21.f:22308-22320) - deliberately, not just for style. That
branch leaves `oasb1`/`hib1` (the SE-income OASDI/HI amounts) unassigned
in the "wages alone already exceed the cap" case, and since they live in a
COMMON block that persists across records, `tfica` for such a record can
silently pick up a stale value left over from whatever unrelated prior
record happened to run before it in the same batch - confirmed empirically
(taxsim_2022_10_21.f, wages=$200k + $20k self-employment income, single):
tfica=$12,715.86 run in isolation, $13,583.86 (off by exactly the prior
record's stale oasb1) when run right after another self-employment-income
record in the same batch. This implementation always computes the
correct, order-independent $12,715.86-style answer - it does not replicate
that bug.
"""

import polars as pl


def oasdi_tax(wages: pl.Expr, wage_base: float, rate_combined: float) -> pl.Expr:
    return wages.clip(0, wage_base) * rate_combined


def hi_tax(wages: pl.Expr, rate_combined: float, wage_base: float = 1.0e15) -> pl.Expr:
    """Uncapped on the HI (Medicare) side for every year this project has
    otherwise modeled - `wage_base` only matters for 1993, the one year
    with a real, separate HI wage base cap (`himax(1993)=$135,000`, before
    it was eliminated entirely starting 1994 - `himax` becomes `9e10`,
    i.e. uncapped, taxsim_2022_10_21.f:22183-22192). The default here
    (a large sentinel) reproduces "uncapped" with no special-casing."""
    return wages.clip(0, wage_base) * rate_combined


def remaining_room_oasdi_tax(
    income: pl.Expr, wage_base_already_used: pl.Expr, wage_base: float, rate_combined: float
) -> pl.Expr:
    """OASDI tax on `income` (self-employment net earnings) given that
    `wage_base_already_used` (wages) has first claim on the wage base.
    Generic enough to reuse for HI's own 1993-only wage base cap too (the
    source's `hib1 = hbrate*(hmax-wage1)` follows the identical "wages
    claim the cap first" shape, taxsim_2022_10_21.f:22323-22328) - callers
    pass HI's rate/wage_base instead of OASDI's when doing so.

    Superseded by `capped_se_tax` below for the current (taxsim_2024_09_21.f)
    `sstax` rewrite - kept only as a reference/fallback for the pre-2024
    source's simpler "clean room" shape, which `capped_se_tax` also
    reproduces exactly (see its own docstring)."""
    remaining_room = (wage_base - wage_base_already_used).clip(0, None)
    return income.clip(0, None).clip(0, remaining_room) * rate_combined


def capped_se_tax(
    wages: pl.Expr,
    se_gross: pl.Expr,
    wage_base: float,
    se_rate_combined: float,
    net_earnings_factor: float,
    rate_includes_netting: bool = False,
) -> pl.Expr:
    """Self-employment OASDI or HI tax for one spouse, replicating
    `sstax`'s own 2024+ cap-check exactly (taxsim_2024_09_21.f:22238-22380,
    the `do i=1,4 ... exit` loop) - NOT a simple "wages claim the wage base
    first, SE gets the netted remainder" allocation.

    The source accumulates `e = e + d(j(i,k))` using RAW (un-netted)
    income across income types, then checks `e*h(law,i)` (the CURRENT
    type's own netting factor applied to the WHOLE accumulated sum,
    including the wage portion) against the wage base - a quirk, not a
    bug fix, confirmed by back-solving a real oracle mismatch (single,
    $82,000 wages + $100,000 self-employment, 2008: this formula gives
    the oracle's real $18,482.00 `fica`; a clean "netted SE against
    remaining room after wages" calculation gives $17,704.15 instead,
    $777.85 low).

    OASDI and HI are NOT symmetric here, despite superficially identical
    loop shapes - a second, narrower bug found only once the HI wage base
    cap (himax, a real finite value 1988-1993 only) actually got exercised
    (the 2008 case above never triggers it - himax is effectively infinite
    by then): OASDI's own `erate` (used both for the uncapped tax AND the
    ref/overage subtraction) is `strate-shrate`, with `h(law,i)` applied
    SEPARATELY, once, only inside the cap *check* itself. HI's own `erate`
    is `h(law,i)*shrate` - the netting factor is baked INTO the rate used
    for both the uncapped tax and the ref subtraction, so it effectively
    appears TWICE in the capped case. `rate_includes_netting=True`
    (pass for HI, not OASDI) selects the correspondingly different closed
    form - found via a live oracle probe (single, $200,000 self-employment
    income only, 1990, back when HI's own cap was still $51,300 same as
    OASDI's: treating both symmetrically gives $7,848.90 `fica`; the real
    oracle gives $8,144.85, matching only once HI's own extra netting
    factor is accounted for).

    Reduces to the exact pre-2024 "clean room" shape whenever
    `net_earnings_factor==1.0` (true for every year before 1990, when the
    source's own `h(law,2)` was 1.0 too, collapsing both closed forms to
    the same thing) - so this single formula is correct for every year,
    not just 2024+."""
    n = net_earnings_factor
    se_gross_c = se_gross.clip(0, None)
    wages_alone_capped = wages > wage_base
    se_net = se_gross_c * n
    combined_over_cap = (wages + se_gross_c) * n > wage_base
    se_tax_when_under = se_net * se_rate_combined
    if rate_includes_netting:
        se_tax_when_over = (n * se_rate_combined * (se_gross_c * (1 - n) + wage_base - n * wages)).clip(0, None)
    else:
        se_tax_when_over = (se_rate_combined * (wage_base - n * wages)).clip(0, None)
    return (
        pl.when(wages_alone_capped)
        .then(0.0)
        .when(combined_over_cap)
        .then(se_tax_when_over)
        .otherwise(se_tax_when_under)
    )


def additional_medicare_tax(household_earnings: pl.Expr, threshold: pl.Expr, rate: float) -> pl.Expr:
    return (household_earnings - threshold).clip(0, None) * rate


def marginal_oasdi_rate(wages: pl.Expr, wage_base: float, rate_combined: float) -> pl.Expr:
    """The rate on the LAST dollar earned (a backward difference,
    tax(w)-tax(w-1)), not a forward one - confirmed via a live oracle
    probe against taxsim2024.exe at the exact wage-base boundary (2008,
    $102,000 wage base: fica(101999->102000) is a full 15.3% dollar,
    fica(102000->102001) is a bare 2.9% HI-only dollar - the reported
    `ficar` AT wages=$102,000 is 15.3%, matching the backward step, not
    the forward one). `<=` (not `<`) is what makes this land at the exact
    boundary - `taxsim2022.exe` reported this same boundary as 2.9%
    instead (a real, confirmed reporting-convention difference between
    the two oracle vintages, not just the sstax rewrite).

    Only correct when there's no self-employment income to interact with
    - see `marginal_wage_rate_with_se` below for the combined case."""
    return pl.when(wages <= wage_base).then(rate_combined).otherwise(0.0)


def marginal_wage_rate_with_se(
    wages: pl.Expr,
    se_gross: pl.Expr,
    wage_base: float,
    rate_combined: float,
    net_earnings_factor: float,
) -> pl.Expr:
    """The reported marginal wage rate (`ficar`'s own `rateoa`/`ratehi`,
    taxsim_2024_09_21.f:22238-22380) is a binary switch, NOT a true
    numeric derivative - it's `rate_combined` unless EITHER wages alone
    already exceed `wage_base`, OR (once self-employment income joins in)
    `(wages+se_gross)*net_earnings_factor` does - in which case it drops
    to exactly 0, not some blended in-between value reflecting how the
    self-employment slice's own capped-tax formula actually varies with
    wages (which it does - see `capped_se_tax`'s docstring - just not in
    what gets reported here). Confirmed via a live oracle probe (1991,
    single, $100,000 self-employment income, wages stepped $33,398-
    $33,402: `fica` moves by ~$0.03-0.04/dollar - implying a real ~3.85%
    combined marginal effect - while the reported `ficar` stays flatly at
    2.9%, i.e. HI-only, the whole way through - confirming `ficar` doesn't
    track the true derivative). The same combined check governs whether
    `rateoa`/`ratehi` were zeroed BEFORE this dollar was earned (a
    backward-difference convention, matching `marginal_oasdi_rate`'s own
    `<=` - not `<` - boundary here too)."""
    wages_alone_capped = wages > wage_base
    combined_over_cap = (wages + se_gross.clip(0, None)) * net_earnings_factor > wage_base
    return pl.when(wages_alone_capped | combined_over_cap).then(0.0).otherwise(rate_combined)


def self_employment_tax(
    se_gross: pl.Expr,
    wages: pl.Expr,
    wage_base: float,
    oasdi_rate: float,
    hi_rate: float,
    net_earnings_factor: float,
    hi_wage_base: float = 1.0e15,
) -> pl.Expr:
    """Total Schedule-SE-style self-employment tax for one spouse (via
    `capped_se_tax` - see its docstring for the exact 2024+ cap-check
    shape, which also reproduces the pre-2024/pre-1990 "clean room" shape
    when `net_earnings_factor==1.0`) - the amount half of which is
    deductible for AGI, and which the taxpayer (not an employer) bears in
    full. `se_gross` is RAW self-employment income, not pre-netted - the
    92.35% adjustment is applied internally (the cap-check itself needs
    the raw figure, not just the netted one)."""
    oasdi = capped_se_tax(wages, se_gross, wage_base, oasdi_rate, net_earnings_factor)
    hi = capped_se_tax(wages, se_gross, hi_wage_base, hi_rate, net_earnings_factor, rate_includes_netting=True)
    return oasdi + hi


def household_self_employment_tax(
    gross_se_income_primary: pl.Expr,
    gross_se_income_secondary: pl.Expr,
    wages_primary: pl.Expr,
    wages_secondary: pl.Expr,
    net_earnings_factor: float,
    wage_base: float,
    se_oasdi_rate: float,
    se_hi_rate: float,
    hi_wage_base: float = 1.0e15,
) -> pl.Expr:
    """Household total self-employment tax - the figure federal.py needs for
    the half-SE-tax AGI deduction (computed independently of payroll.py's
    own per-spouse fica/tfica breakdown, since AGI is computed before
    payroll tax runs in the pipeline)."""
    setax_1 = self_employment_tax(
        gross_se_income_primary, wages_primary, wage_base, se_oasdi_rate, se_hi_rate, net_earnings_factor, hi_wage_base
    )
    setax_2 = self_employment_tax(
        gross_se_income_secondary, wages_secondary, wage_base, se_oasdi_rate, se_hi_rate, net_earnings_factor, hi_wage_base
    )
    return setax_1 + setax_2
