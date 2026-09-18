"""Federal calculator entry point.

Milestone 1 scope: wages + interest + dividends + capital gains (short and
long-term) + self-employment income (psemp/ssemp; not the granular
pbusinc/pprofinc/sbusinc/sprofinc fields, or QBI) + unemployment
compensation (ui/pui/sui), standard deduction or itemized deductions (SALT
capped at $10k + mortgage interest + cash charitable contributions - not
non-cash/appreciated-property giving), regular bracket tax with the
Qualified Dividends and Capital Gain Tax Worksheet's 0%/15%/20%
preferential rates (engine.capital_gains), AMT (including its own version
of that preferential-rate treatment - see engine/amt.py), EITC (child
counts 0-3, including the disqualified-income reduction), ODC, Child Tax
Credit + refundable Additional CTC (0-2 CTC-qualifying children), the
Child and Dependent Care Credit, NIIT, and the years supported so far
(2019 and 2022 as originally built, plus 2020's CARES Act and 2021's
American Rescue Plan Act provisions - see below).

Multi-year vintage differences implemented, not just parameter swaps:
- CTC/CCC's pre-2021 formulas (flat per-child CTC, single-step-down CCC
  rate) vs. the ARPA-carryover/two-tier-phase-down shapes used 2021+.
- 2021: CTC and CCC both become FULLY refundable - no tax-liability cap,
  and CTC additionally drops the ACTC earned-income floor entirely
  (confirmed empirically, not just from the source's comments - see the
  isolation probes noted at each site below).
- 2020's $10,200-per-spouse unemployment compensation exclusion (a hard
  AGI-without-UI cliff at $150,000, not phased) and the CARES/ARPA
  above-the-line cash-charitable deduction (2020: flat $300/return, a real
  un-doubled-for-joint quirk; 2021: $300/filer, $600 joint, folded into the
  itemize-vs-standard comparison itself).
- The 2020-21 Recovery Rebate Credit (Economic Impact Payments / EIP1-3),
  a genuinely new refundable credit, not present in any other year.

Dividends are treated as fully qualified (the source's own default absent
a separate non-qualified-dividend input) and stcg/ltcg are both assumed
non-negative - capital-loss netting (a loss in one bucket offsetting a
gain in the other, or the $3,000 net-loss limitation) is real
(taxsim_2022_10_21.f:24296-24306) and NOT implemented; enter a loss as 0,
not a negative value, until that's built.

`agi` and `earned_income` are each distinct from `wages` once interest and
self-employment income are in scope: `earned_income` = wages + gross SE
income - half the SE tax (EITC's phase-in base); `agi` = that plus
interest (EITC's phaseout, and every other status-based threshold in this
module - AMT, ODC/CTC, the child care credit's rate schedule - compares
AGI, not earned income). Self-employment tax itself (half of which is
AGI-deductible) is computed via engine.payroll_tax.household_self_
employment_tax, shared with calculators.payroll rather than duplicated -
see that module's docstring for a real, order-dependent bug found in the
source's own SE/OASDI-wage-base-sharing logic that this deliberately does
not replicate.

EITC's disqualified-income rule (taxsim_2022_10_21.f:27751-27765): reduces
the credit dollar-for-dollar above a threshold (`dylim`, confirmed $10,000
for 2022 - exactly $10,000 doesn't trigger it, $10,001 does), computed
from investment-type income (interest + dividends + capital gains, in
this scope). Known sharp edge:
where the ordinary phase-in credit exactly equals the disqualified-income
reduction, `fiitax` is correct on both sides (confirmed: $0 either way),
but the derivative is genuinely ambiguous at that exact knife-edge (0 on
one side, a positive slope on the other) - `compute_marginal_rate`'s
+/-$0.01 retry doesn't fully resolve it, so `frate` can differ from
taxsim2022.exe by the honest ambiguity of a non-differentiable point, not
a tax-liability error.

NIIT (taxsim_2022_10_21.f:25999-26026, local variable `dicare`): 3.8% on
the lesser of net investment income (interest + dividends + all capital
gains, including short-term - a broader base than `ltg`, which excludes
short-term gains since those don't get preferential rates) or AGI over a
threshold, net of the state/local tax attributable to it. Added on top of
regular tax + AMT, outside the pool nonrefundable credits compete for.

Itemized deductions/AMT confirmed against taxsim_2022_10_21.f:24560-24730
(itemize-vs-standard decision, SALT cap) and :25068-25475 (AMT: exemption
with phaseout, flat 26%/28% tentative minimum tax, compared to regular tax
before other credits).

This is a full, real implementation of the AMT formula (exemption
phaseout, both rate brackets, the SALT addback), not a stub. For a
wages-only, standard-deduction filer, AMTI equals AGI exactly (no
preference items in this model); confirmed
empirically against taxsim2022.exe up to $10,000,000 in wages that AMT
never triggers there. Also swept tens of thousands of (wages, proptax,
mortgage) combinations while itemizing, spanning the full
exemption-phaseout range and beyond: max AMT was $0 throughout, matching
taxsim2022.exe exactly. This isn't a gap in the implementation - it's a
real mathematical fact about 2022 law: AMT's top rate (28%) is below
regular tax's top rate (37%), and the $10k SALT cap is far too small
relative to the ~$76k+ exemption to ever close that gap for wage-only
income, with or without itemizing - matching the well-documented collapse
in real AMT incidence for ordinary W-2 filers post-TCJA. Getting a
genuinely nonzero AMT case would need either non-wage preference income
(ISO exercises, private activity bond interest - not in this model) or an
earlier tax year (pre-2018, when SALT had no cap and AMT was commonly
triggered by it).

EIC-qualifying-child count comes from `dep18`, not `dep17` - confirmed by
reading taxsim_2022_10_21.f directly (taxsim_2022_10_21.f:21238-21252):
    data(207) = x(8)   ! CCC-eligible base  = dep13
    data(208) = x(9)   ! CTC-eligible base  = dep17
    data(203) = x(10)  ! EIC-eligible base  = dep18
`check()` (taxsim_2022_10_21.f:21480) then requires CTC-eligible <=
EIC-eligible (data(208) <= data(203)) as an input-consistency check - its
own error message has the two labels swapped ("More EIC elegible than CTC
elegible" for a check that actually fires when CTC exceeds EIC), but the
condition itself is unambiguous. This is why `depx=1,dep17=1` alone errors:
dep18 (the real EIC base) was never set, leaving EIC-eligible=0 < CTC-
eligible=1. Age-specific increments via age1/age2/age3 against
cccage/eicage/ctcage year thresholds are not modeled here (rare case; the
aggregate dep13/dep17/dep18 counts cover the common one).

ODC + Child Tax Credit + Additional (refundable) CTC are implemented
together for 0-2 CTC-qualifying children (dep17 <= 2), confirmed against
taxsim_2022_10_21.f:25619-25668 (nonrefundable base + two-tier phaseout)
and :25918-25940 (refundable ACTC top-up). dep17 >= 3 uses a different
alternate ACTC formula (a floor based on Social Security/Medicare tax
paid minus EIC, taxsim_2022_10_21.f:25926-25927) that is NOT implemented -
a dep17>=3 record's fiitax will be wrong by however that floor differs
from the plain 15%-of-earned-income formula used here. Flagged, not
silently assumed away.

Child and Dependent Care Credit: nonrefundable, confirmed empirically
against taxsim2022.exe (capped exactly at regular tax at the boundary
where regular tax is small) - the source's own "2021 chcr is refundable"
comment does not carry over to 2022, despite the 2021-style expanded
50%/$8,000-per-person rate schedule applying to 2022 too
(taxsim_2022_10_21.f:25486-25530). Since `check()` requires CCC-eligible
<= CTC-eligible (dep13 <= dep17), CCC and CTC are essentially always both
active together whenever CCC applies at all - so their stacking order
matters generally, not as a corner case. Confirmed the order from the
source's nonrefundable-credit stacking block
(taxsim_2022_10_21.f:24802-24822): CCC is capped against the full
tax_before_credits FIRST; CTC/ODC then compete for whatever capacity is
left over, not the full amount independently. Implemented in that order.
Not modeled: the self-employment-income earned-income split
(setax/data(213) etc.) since this scope is wages-only.
"""

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
# See the DIVIDENDS_FUDGE comment in compute_regular_tax.
DIVIDENDS_FUDGE = 0.001
# sepret in the source: 1 for single/joint/HoH, 2 for a married-separate half-return.
SEPRET_BY_STATUS = {
    "single": 1.0,
    "married_joint": 1.0,
    "head_of_household": 1.0,
    "married_separate": 2.0,
}


def _with_default(df: pl.DataFrame, column: str, default: float = 0.0) -> pl.DataFrame:
    if column in df.columns:
        return df
    return df.with_columns(pl.lit(default).alias(column))


def _by_status_expr(values_by_status: dict[str, float]) -> pl.Expr:
    """Build a Polars expression that picks a constant by `filing_status`,
    for the many federal parameters (thresholds, caps) that vary by status
    but not otherwise by row."""
    expr = pl.lit(None, dtype=pl.Float64)
    for status, value in values_by_status.items():
        expr = pl.when(pl.col("filing_status") == status).then(pl.lit(float(value))).otherwise(expr)
    return expr


def _filing_status_expr() -> pl.Expr:
    """mstat=1 or 3 -> single, unless depx>0 -> head_of_household.
    mstat=2 -> married_joint. mstat=6 or 66 -> married_separate.
    (Derived from taxsim_2022_10_21.f:21164-21187 and confirmed against
    taxsim.exe - see the design doc's Rollout Sequence notes.)
    """
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


def compute_regular_tax(df: pl.DataFrame, year: int) -> pl.DataFrame:
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

    # Self-employment tax (needed here for the half-SE-tax AGI deduction,
    # computed independently of calculators.payroll - AGI has to exist
    # before that module runs). Gross SE income (not the .9235-adjusted net
    # earnings) is what counts toward AGI/earned income directly - only the
    # deduction uses the net-earnings-adjusted amount, matching
    # taxsim_2022_10_21.f:24338 (`d17`, effectively psemp+ssemp in this
    # scope) and :24383 (`earny`). Not modeled: itemized business income
    # (pbusinc/pprofinc/sbusinc/sprofinc) or the QBI deduction.
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
    )
    gross_se_income = pl.col("psemp") + pl.col("ssemp")

    # The AGI-deductible share of SE tax is a flat 50% in every year except
    # 2011-2012, when it's a genuinely different formula - not just a
    # different rate applied to the same 50% split. The 2011-2012 payroll
    # tax holiday cut the wage-earner's OWN OASDI share (not the
    # employer's); to keep the self-employed getting the same relative AGI
    # benefit as wage earners despite paying less actual SE tax, the
    # deduction is computed from a *hypothetical* SE tax at the normal,
    # uncut 15.3% rate (`.5751*setax` below a threshold tied to 13.3% of
    # the wage base, else `.5*setax` plus a flat top-up) - NOT from the SE
    # tax actually owed. Confirmed by back-solving a real mismatch: at
    # psemp=$5,000, 2011, the deduction implied by the oracle's own AGI
    # matches `.5751 * (net_earnings * 15.3%)` almost exactly ($406.295
    # predicted vs $406.291 implied), not `.5751 * (net_earnings * 14.3%)`
    # (the actual, cut-rate SE tax) which undershoots by $26.55.
    # taxsim_2022_10_21.f:24383-24404.
    if year in (2011, 2012):
        wage_base = float(resolve_year(pt_p["oasdi_wage_base"], year))
        net_earnings_factor = float(resolve_year(pt_p["se_net_earnings_factor"], year))
        setax_at_normal_rate = household_self_employment_tax(
            pl.col("psemp"),
            pl.col("ssemp"),
            pl.col("pwages"),
            pl.col("swages"),
            net_earnings_factor=net_earnings_factor,
            wage_base=wage_base,
            se_oasdi_rate=0.124,
            se_hi_rate=0.029,
        )
        se_deduction_threshold = 0.133 * wage_base
        se_deduction_flat_addon = 0.133 * 0.0751 * wage_base
        se_agi_deduction = (
            pl.when(setax_at_normal_rate <= se_deduction_threshold)
            .then(0.5751 * setax_at_normal_rate)
            .otherwise(0.5 * setax_at_normal_rate + se_deduction_flat_addon)
        )
    else:
        se_agi_deduction = 0.5 * setax_total

    df = df.with_columns(
        setax=setax_total,
        earned_income=(pl.col("wages") + gross_se_income - se_agi_deduction).clip(0, None),
    )

    # agi != wages once interest/SE/dividend/capital-gains income is
    # present: interest is ordinary income (taxsim_2022_10_21.f test:
    # v19==v28 with intrec alone, no preferential rate), but it's not
    # *earned* income - EITC's phase-in uses earned income, while its
    # phaseout compares AGI.
    #
    # DIVIDENDS_FUDGE: the source unconditionally computes
    # data(12) = <dividends input> + 0.001 (taxsim_2022_10_21.f:21220), even
    # when no dividends are reported, and that data(12) becomes `divall`,
    # which feeds AGI, EITC's disqualified-income test (`disqy`), and (see
    # below) the preferential-rate base `ltg`. Found by instrumenting a
    # debug build after a real 2/844 test mismatch traced to it.
    dividends_with_fudge = pl.col("dividends") + DIVIDENDS_FUDGE
    capgn = pl.col("stcg") + pl.col("ltcg")

    # ltg: qualified dividends + net long-term capital gain, the base that
    # gets 0%/15%/20% preferential rates (taxsim_2022_10_21.f:24302-24321).
    # Dividends are treated as fully qualified (the source's own default
    # absent a separate non-qualified-dividend split). Scope: stcg and ltcg
    # are both assumed non-negative - the source's loss-netting rules for a
    # net capital loss, or a short-term loss offsetting a long-term gain,
    # are real and NOT implemented; enter a loss as 0 for now, not a
    # negative value, or this will be wrong.
    ltg = pl.col("ltcg").clip(0, None) + dividends_with_fudge

    agi_before_ui = (
        pl.col("wages")
        + pl.col("intrec")
        + gross_se_income
        - se_agi_deduction
        + capgn
        + dividends_with_fudge
    )

    # Unemployment compensation: ordinary taxable income in every year
    # (confirmed empirically: fully taxed like wages in 2019/2021/2022 -
    # ui=$20,000 alone produces exactly the ordinary-bracket tax on
    # agi-std_ded). `ui_total` mirrors the psemp/ssemp combining pattern:
    # taxsim_2022_10_21.f:21227 (`data(82) = max(x(21), x(35)+x(36))`)
    # prefers the split pui+sui sum when a combined `ui` isn't given.
    ui_total = pl.max_horizontal(pl.col("ui"), pl.col("pui") + pl.col("sui"))
    UI_EXCLUSION_2020 = 10200.0  # uithrs(2020) - taxsim_2022_10_21.f:24237
    if year == 2020:
        # CARES/ARPA: up to $10,200 excluded PER SPOUSE (not doubled for a
        # joint return where only one spouse has UI - a real, well-known
        # quirk of this provision), gated by a hard AGI-without-UI cliff at
        # $150,000 (flat, not scaled by filing status - also real, not a
        # taxsim-specific artifact). Confirmed empirically: ui=$20,000,
        # single, no wages -> fiitax=-$1,800, matching $9,800 taxable UI
        # (after the $10,200 exclusion) producing $0 regular tax plus the
        # $1,800 EIP1+EIP2 recovery rebate at the resulting lower AGI.
        # taxsim_2022_10_21.f:24531-24539.
        excl_spouse = pl.min_horizontal(pl.col("sui"), pl.lit(UI_EXCLUSION_2020))
        excl_primary = (ui_total - pl.col("sui")).clip(0, UI_EXCLUSION_2020)
        exclusion = excl_spouse + excl_primary
        taxable_ui = (
            pl.when(agi_before_ui < 150000.0)
            .then((ui_total - exclusion).clip(0, None))
            .otherwise(ui_total)
        )
    elif year == 2009:
        # ARRA's original, smaller unemployment exclusion: a flat $2,400
        # PER RETURN (not per spouse like 2020's, and no AGI cliff at all -
        # both real, distinct differences from the 2020 provision, not
        # just a smaller number). taxsim_2022_10_21.f:24336-24337
        # (`uithrs(2009) = 2400`, taxsim_2022_10_21.f:24237).
        taxable_ui = (ui_total - 2400.0).clip(0, None)
    else:
        taxable_ui = ui_total

    df = df.with_columns(
        ltg=ltg,
        agi=agi_before_ui + taxable_ui,
    )

    sepret_expr = _by_status_expr(SEPRET_BY_STATUS)

    std_ded_by_status = {
        status: resolve_year(FEDERAL_INCOME_TAX_PARAMS["standard_deduction"][status], year)
        for status in FILING_STATUSES
    }
    brackets_by_status = {}
    for status in FILING_STATUSES:
        brackets = resolve_year(FEDERAL_INCOME_TAX_PARAMS["brackets"][status], year)
        validate_brackets(brackets, context=f"federal.{status}.{year}")
        brackets_by_status[status] = brackets

    std_ded_expr = _by_status_expr(std_ded_by_status)
    df = df.with_columns(standard_deduction=std_ded_expr)

    # Itemized deductions: SALT (proptax + otheritem + the sales/income tax
    # alternative) + mortgage interest. Capped at $10k per return since
    # TCJA (2018+) - uncapped before that (taxsim_2022_10_21.f:24606-24622).
    salt_uncapped_expr = pl.col("proptax") + pl.col("otheritem") + pl.col("state_sales_or_income_tax_ded")
    if year >= 2018:
        salt_cap = float(resolve_year(FEDERAL_ITEMIZED_PARAMS["salt_cap"], year))
        salt_capped = salt_uncapped_expr.clip(0, None).clip(0, salt_cap / sepret_expr)
    else:
        salt_capped = salt_uncapped_expr.clip(0, None)

    # Charitable cash contributions (taxsim_2022_10_21.f:24548-24571): cash
    # only - the non-cash/appreciated-property portion (asset, its own
    # separate 30%-of-AGI cap) is a real, deliberately un-implemented gap,
    # same scope decision as capital-loss netting. Normally capped at 50%/
    # 60% of AGI (`alim50`), but that cap is suspended entirely for 2020-21
    # (taxsim_2022_10_21.f:24618-24619).
    #
    # Disclosed validation gap: unlike every other field here, `charity_cash`
    # has no corresponding named input column in the source's own public
    # interface at all (`parameter(nx=47)`, taxsim_2022_10_21.f:21082 - its
    # 47-entry `vars` list has no charitable-contribution field, and the
    # only generic override mechanism, opt1/opt2, pokes `extnd()`, not
    # `data(58)`). So this can never be driven to a nonzero value through
    # taxsim2022.exe's file-based CLI, and can't be cross-validated against
    # it the way everything else in this module is - it stays 0 in every
    # oracle-comparison test case. The formula below is transcribed directly
    # from the source, not empirically verified.
    agix = pl.col("agi").clip(0, None)
    alim50 = pl.lit(1.0e20) if year in (2020, 2021) else 0.5 * agix
    char_cash_itemized = pl.min_horizontal(alim50, pl.col("charity_cash")).clip(0, None)
    itemized_deduction = salt_capped + pl.col("mortgage") + char_cash_itemized

    # Pease limitation (pre-TCJA, reinstated by ATRA 2013, suspended
    # 2018-2025): itemized deductions reduced by the lesser of 3% of AGI
    # over a threshold or 80% of the itemized total (source's own dlim2
    # nets out investment/business interest first - data(57)/data(168) -
    # both 0 in this model's scope, so not netted here either). Also fully
    # (not just partially) repealed 2010-2012 - `dlim1`/`dlim2` forced to 0
    # unconditionally, not merely small thresholds - taxsim_2022_10_21.f:
    # 24662-24665. Not just a lower rate: literally no reduction at all.
    if 2010 <= year <= 2012:
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
        if year == 2009:
            # Pease was being phased out gradually before its 2010-2012
            # full repeal - 2009 (like 2008) applies only 1/3 of the
            # reduction the formula above would otherwise give.
            # taxsim_2022_10_21.f:24653-24656.
            pease_reduction = pease_reduction / 3.0
        itemized_deduction = itemized_deduction - pease_reduction

    # Above-the-line cash-charitable deduction for standard-deduction
    # filers - a temporary CARES/ARPA provision, real quirks in both years:
    # 2020 gives a flat $300 per RETURN (not doubled for a joint return,
    # halved for MFS via /sepret - taxsim_2022_10_21.f:24733-24736); 2021
    # fixed that to $300/filer ($600 joint) but folded it into the
    # itemize-vs-standard comparison itself, not just a post-hoc reduction
    # (taxsim_2022_10_21.f:24709-24710).
    if year == 2020:
        cas = pl.min_horizontal(pl.lit(300.0) / sepret_expr, pl.col("charity_cash"))
    elif year == 2021:
        num_filers = pl.when(pl.col("filing_status") == "married_joint").then(2.0).otherwise(1.0)
        cas = pl.min_horizontal(300.0 * num_filers, pl.col("charity_cash"))
    else:
        cas = pl.lit(0.0)

    itemize_comparison_floor = std_ded_expr + cas if year == 2021 else std_ded_expr
    df = df.with_columns(
        salt_capped=salt_capped,
        itemized_deduction=itemized_deduction,
        itemizes=itemized_deduction > itemize_comparison_floor,
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
            if year == 2009:
                # Same gradual-phase-out timeline as Pease above - 2009
                # applies only 1/3 of the exemption reduction the ratio
                # would otherwise imply. taxsim_2022_10_21.f:24808-24814.
                amphs_fraction = 1.0 / 3.0
            amex = amex_base * (1.0 - pep_ratio * amphs_fraction)
    else:
        amex = pl.lit(0.0)

    df = df.with_columns(taxable_income=(pl.col("agi") - deduction - amex).clip(0, None))

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
    preferential_tax = preferential_rate_tax(
        pl.col("taxable_income"),
        ltg_capped,
        rate_0_ceiling_expr,
        rate_15_ceiling_expr,
        rate_15=float(resolve_year(cg_p["rate_15"], year)),
        rate_20=float(resolve_year(cg_p["rate_20"], year)),
    )

    df = df.with_columns(
        regular_tax=tax_expr + preferential_tax,
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
        ltg=pl.col("ltg"),
        regular_taxable_income=pl.col("taxable_income"),
        cg_rate_0_ceiling=rate_0_ceiling_expr,
        cg_rate_15_ceiling=_by_status_expr(
            {status: resolve_year(amt_p["cg_rate_15_ceiling"][status], year) for status in FILING_STATUSES}
        ),
        cg_rate_15=float(resolve_year(cg_p["rate_15"], year)),
    )
    # Not rounded here: matching the source, which stays full double
    # precision throughout and rounds only at final output. Rounding
    # intermediate credits to the cent would swamp the $0.01 perturbation
    # compute_marginal_rate() uses for frate.
    df = df.with_columns(amt=amt)
    df = df.with_columns(tax_before_credits=pl.col("regular_tax") + pl.col("amt"))

    # Child and Dependent Care Credit computed first, since the source's own
    # nonrefundable-credit stacking order (taxsim_2022_10_21.f:24802-24822)
    # caps it against the FULL tax_before_credits, then CTC/ODC get only
    # whatever capacity is left over - not an independent competing cap.
    ccc_p = FEDERAL_CREDITS_PARAMS["child_care_credit"]
    max_qualifying_persons = float(resolve_year(ccc_p["max_qualifying_persons"], year))
    if year >= 2021:
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
        # lawyr 2003-2020: a smaller expense cap and a completely different
        # rate shape - a single continuous step-down to a 20% floor that
        # holds forever, no second phase-down to 0%. See credits.yaml and
        # engine/credits.py's child_care_credit_rate_pre2021 docstring.
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
        # ARPA: the Child and Dependent Care Credit is fully refundable for
        # 2021 only - not capped by tax_before_credits at all (confirmed
        # empirically by isolating it from the CTC's own 2021 refundability:
        # 1 child, single, wages=$5,000, $8,000 childcare expense -> the
        # observed fiitax is exactly $2,500 more negative than
        # tax_before_credits=$0 would allow if still capped there, matching
        # 50%*$5,000 in full). taxsim_2022_10_21.f:25992-25993,26048.
        ccc = ccc_amount.clip(0, None)
    else:
        ccc = pl.min_horizontal(ccc_amount, pl.col("tax_before_credits").clip(0, None))
    df = df.with_columns(ccc=ccc)

    eitc_params_year = FEDERAL_EITC_PARAMS.filter(pl.col("year") == year).select(
        "filing_status", "num_children", "rate_in", "max_credit", "phaseout_start", "rate_out"
    )
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
    # Disqualified (investment) income rule: taxsim_2022_10_21.f:27751-27765.
    # In this scope (interest only - no dividends/capital gains yet), disqy
    # is just intrec. Reduced dollar-for-dollar above dylim, confirmed
    # empirically ($10,000 for 2022, strictly-greater-than: exactly $10,000
    # doesn't trigger it, $10,001 reduces the credit by $1).
    dylim = float(resolve_year(FEDERAL_EITC_PARAMS_MISC["dylim"], year))
    disqy = capgn.clip(0, None) + dividends_with_fudge + pl.col("intrec")
    eitc_reduction = (disqy - dylim).clip(0, None)
    df = df.with_columns(eitc=(eitc_ordinary - eitc_reduction).clip(0, None))

    ctc_p = FEDERAL_CREDITS_PARAMS["child_tax_credit"]
    actc_earned_income_floor = float(resolve_year(ctc_p["actc_earned_income_floor"], year))
    actc_rate = float(resolve_year(ctc_p["actc_rate"], year))

    ideps = pl.col("dep17")  # CTC-qualifying children; 0-2 supported, see module docstring
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
        # Pre-2018: no Credit for Other Dependents at all (TCJA introduced
        # it) - CTC alone phases out above a flat, never-inflation-indexed
        # threshold that was unchanged 2001-2017 (credits.yaml's
        # `pre2018_phaseout_threshold`, from the source's `cphas` array),
        # not the $200k/$400k TCJA-era threshold above.
        odc_base = pl.lit(0.0)
        odc_rate_per_1000 = float(resolve_year(ctc_p["pre2018_phaseout_rate_per_1000"], year))
        odc_threshold_expr = _by_status_expr(
            {
                status: resolve_year(ctc_p["pre2018_phaseout_threshold"][status], year)
                for status in FILING_STATUSES
            }
        )

    if year >= 2021:
        # ARPA carryover mechanism: an "enhanced" amount above the flat
        # $2000-per-child base, phased back down at low incomes.
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
        # lawyr<=2020: a flat per-child amount, no young/old-child split and
        # no low-income phaseout mechanism at all.
        # precrd = chmax(lawyr)*ideps + odcred - taxsim_2022_10_21.f:25619-25637.
        flat_amount = float(resolve_year(ctc_p["flat_amount_pre2021"], year))
        ctc_before_tcja_phaseout = flat_amount * ideps

    combined_base = odc_base + ctc_before_tcja_phaseout

    # ODC and CTC share the same $200k/$400k TCJA-era phaseout threshold/rate.
    # Computed directly here (rather than via engine.credits' shared helper)
    # because ACTC below needs the after-phaseout, before-nonrefundable-cap
    # value too, not just the final capped credit.
    tcja_reduction = ((agi - odc_threshold_expr).clip(0, None) / 1000) * odc_rate_per_1000
    combined_after_phaseout = (combined_base - tcja_reduction).clip(0, None)

    if year == 2021:
        # ARPA: the WHOLE combined (CTC+ODC) amount, after both phaseouts,
        # is refundable outright for 2021 - no tax-liability cap, no
        # earned-income floor, no per-child refundable-amount cap.
        # Confirmed empirically by isolating it from EIP3: married_joint,
        # $0 wages, 2 kids (dep6=0) -> fiitax=-$11,600 exactly, matching the
        # full $6,000 ARPA CTC (0 wages would give $0 ACTC under the normal
        # earned-income-floor formula) plus $5,600 of EIP3.
        # taxsim_2022_10_21.f:25964 (`chcr1 = chcred`).
        nonrefundable_credit = pl.lit(0.0)
        actc = combined_after_phaseout
    else:
        remaining_after_ccc = (pl.col("tax_before_credits") - pl.col("ccc")).clip(0, None)
        nonrefundable_credit = pl.min_horizontal(combined_after_phaseout, remaining_after_ccc)
        unused_nonrefundable = combined_after_phaseout - nonrefundable_credit

        actc_earned_formula = actc_rate * (agi - actc_earned_income_floor).clip(0, None)
        if year >= 2018:
            actc_max_per_child = float(resolve_year(ctc_p["actc_max_refundable_per_child"], year))
            actc_cap = pl.min_horizontal(unused_nonrefundable, actc_max_per_child * ideps)
        else:
            # Pre-2018: no separate per-child refundable cap at all (that's
            # a TCJA-era mechanism) - just the earned-income formula against
            # whatever's left of the nonrefundable amount.
            # taxsim_2022_10_21.f:25924-25927 (the ideps<=2 scope this
            # module covers takes the `chcr1=min(precrd-chcred,tenpct)`
            # branch only - no `refch*ideps` term).
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
            + pl.col("niit")
        )
    )


def compute_marginal_rate(
    df: pl.DataFrame,
    year: int,
    refresh_dependent_columns: Callable[[pl.DataFrame], pl.DataFrame] | None = None,
) -> pl.DataFrame:
    """`frate`: federal marginal tax rate with respect to the taxpayer's own
    earnings (TAXSIM's default `mtr=85`) - a finite difference, not a
    closed-form rate, since credits/phaseouts make the rate a step function.
    Confirmed against taxsim_2022_10_21.f:21326-21385: perturb `pwages` by
    +$0.01 first; if the resulting rate is an implausible >=100% (meaning
    the $0.01 step crossed a cliff - e.g. a phase-in/out boundary that isn't
    smooth at that exact cent), retry with -$0.01 instead. `mtr=86` (spouse)
    and `mtr=11` (non-wage) are not implemented - only the default.

    `refresh_dependent_columns`: some inputs are themselves derived from
    wages outside this module - e.g. the TX sales-tax-deduction alternative
    (parameters/states/tx/sales_tax_deduction.yaml) feeds
    `state_sales_or_income_tax_ded`, which the real Fortran recomputes at
    every perturbed wage level too. Pass a function that re-derives those
    columns for a perturbed `df` (see scripts/validate_federal.py) or the
    finite difference will miss their small wage-dependent shift -
    confirmed as the cause of a real, if tiny (few-hundredths-of-a-point),
    discrepancy for itemizers using that deduction.
    """
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
