"""Federal income tax, 1977-1986 (`law79`, taxsim_2022_10_21.f:23286-24111) -
a genuinely separate top-level calculator from `law87`
(calculators/federal.py), not a plug-in vintage within it. Dispatched
`if(lawyr.ge.1977.and.lawyr.le.1986) call law79` (taxsim_2022_10_21.f:22152),
at the SAME level `law87` itself is dispatched from.

See parameters/national/pre1987.yaml for the full scope note on which real
law79 mechanisms are unreachable through this project's input schema (and so
are correctly always-zero/inert here, not silently-skipped gaps): medical
deduction, charitable contributions, casualty losses, the pre-1979 add-on
minimum tax, income averaging, political/elderly/foreign-tax/investment/
jobs/energy credits, Keogh/IRA deductions.

Formula structure (matching the source's own control flow closely, for
auditability):
1. AGI: dividend exclusion (divall) + capital-gains EXCLUSION (capded,
   60%/50% of net LTCG excluded from income entirely - not a preferential
   rate) + wages + interest + gross SE income - two-earner deduction, then
   the unemployment-compensation exclusion layered on top.
2. Standard-or-itemized deduction decision (zbr vs proptax+otheritem+
   mortgage), personal exemption, taxable income - and for 1979-1986 the
   bracket table already has the standard deduction baked into its own
   first threshold (confirmed: 1979's own $2,300 single first bracket
   equals that year's zbr exactly) while 1977-1978's table does not (an
   explicit zbr subtraction is needed for those two years only).
3. Two alternative computations that can beat the plain bracket tax:
   `acgtax` (a capital-gains-specific alternative, real only 1977-1978 and
   1981) and `etax` (the "maximum tax on earned income", real only
   1977-1981) - `taxbc = min(regular, acgtax if positive, etax if
   positive)`.
4. Child and Dependent Care Credit (only nonrefundable credit reachable in
   this scope) caps against taxbc; `taxaft = taxbc - credit`.
5. Minimum tax (three distinct formulas by sub-period) adds on top of
   taxaft as a floor: `tax = max(taxaft, minimum_tax_amount)`.
6. EITC (`earncr`, own early rate schedule) is subtracted LAST, after the
   minimum tax - can make the final liability negative (refundable).

The 1981 Rate Reduction Credit (a one-time, mid-year 1.25% cut) is threaded
into the regular tax, the capital-gains alternative tax, and the
maximum-tax-on-earned-income computations individually, matching the
source line by line rather than applied as one blanket final multiplier.

Implementation note: every intermediate quantity is materialized into its
own real column via a separate `df.with_columns(...)` call (not chained as
nested Python expression variables into one giant final expression) -
Polars' query planner is pathologically slow on deeply-nested expression
trees built that way (confirmed: one un-staged version of this function
took ~19s for 200 rows; staged, the same computation is a small fraction
of a second). Downstream stages reference `pl.col("...")`, never the
Python expression variable from an earlier stage.
"""

import polars as pl

from taxsim_py.engine.brackets import bracket_tax
from taxsim_py.engine.eitc import trapezoid_credit
from taxsim_py.engine.schema import PARAMETERS_ROOT, load_yaml, resolve_year

PRE1987_PARAMS = load_yaml(PARAMETERS_ROOT / "national" / "pre1987.yaml")

FILING_STATUSES = ["single", "married_joint", "married_separate", "head_of_household"]
DIVIDENDS_FUDGE = 0.001
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
    expr = pl.lit(None, dtype=pl.Float64)
    for status, value in values_by_status.items():
        expr = pl.when(pl.col("filing_status") == status).then(pl.lit(float(value))).otherwise(expr)
    return expr


def _filing_status_expr() -> pl.Expr:
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


def _bracket_tax_by_status(income: pl.Expr, brackets_by_status: dict[str, list[list[float]]]) -> pl.Expr:
    expr = pl.lit(None, dtype=pl.Float64)
    for status, brackets in brackets_by_status.items():
        expr = pl.when(pl.col("filing_status") == status).then(bracket_tax(income, brackets)).otherwise(expr)
    return expr


def compute_regular_tax_pre1987(df: pl.DataFrame, year: int, force_itemize: bool | None = None) -> pl.DataFrame:
    p = PRE1987_PARAMS
    # Which intermediate working columns exist varies by year (e.g. the
    # 1977-1978 alternative-capital-gains-tax columns don't exist for
    # 1982+) - keep the original input columns plus a fixed result set at
    # the end so different years' outputs can be `pl.concat`'d together
    # (scripts/validate_federal.py does this across the whole YEARS list).
    original_columns = list(df.columns)
    df = df.with_columns(
        filing_status=_filing_status_expr(),
        wages=pl.col("pwages") + pl.col("swages"),
    )
    for col in (
        "proptax", "otheritem", "mortgage", "intrec", "psemp", "ssemp", "dividends", "stcg", "ltcg", "ui",
        "state_sales_or_income_tax_ded",
    ):
        df = _with_default(df, col)

    sepret_expr = _by_status_expr(SEPRET_BY_STATUS)
    brackets_by_status = {status: resolve_year(p["brackets"][status], year) for status in FILING_STATUSES}

    df = df.with_columns(
        sepret=sepret_expr,
        gross_se_income=pl.col("psemp").clip(0, None) + pl.col("ssemp").clip(0, None),
    )
    df = df.with_columns(earned=(pl.col("wages") + pl.col("gross_se_income")).clip(0, None))

    # Dividend exclusion (divall). 1981 merges interest into the same
    # exclusion base (a real, one-year-only ERTA 1981 provision) and zeroes
    # `ints` afterward; every other year excludes dividends alone and keeps
    # interest separate. 1979's exclusion is a genuine, replicated-as-found
    # $0 quirk (`divexc = data(13)`, an input field the source never
    # populates) - see pre1987.yaml's own note.
    divexc_expr = _by_status_expr(
        {status: resolve_year(p["dividend_exclusion"][status], year) for status in FILING_STATUSES}
    )
    df = df.with_columns(dividends_with_fudge=pl.col("dividends") + DIVIDENDS_FUDGE, divexc=divexc_expr)
    if year == 1981:
        df = df.with_columns(
            divall=(pl.col("dividends_with_fudge") + pl.col("intrec") - pl.col("divexc")).clip(0, None),
            ints=pl.lit(0.0),
        )
    else:
        df = df.with_columns(
            divall=(pl.col("dividends_with_fudge") - pl.col("divexc")).clip(0, None),
            ints=pl.col("intrec"),
        )

    # Capital gains: an EXCLUSION (not a preferential rate) - `capded` is
    # excluded from income entirely, `capgn` is what's left to add to AGI.
    # Scope: stcg/ltcg both assumed non-negative (same limitation as
    # calculators/federal.py), so the source's net-capital-LOSS branch
    # (taxsim_2022_10_21.f:23380-23387) never triggers here.
    caprat = float(resolve_year(p["capital_gains_exclusion_rate"], year))
    df = df.with_columns(fullcg=pl.col("stcg") + pl.col("ltcg"))
    df = df.with_columns(capded=(caprat * pl.min_horizontal(pl.col("ltcg"), pl.col("fullcg"))).clip(0, None))
    df = df.with_columns(capgn=pl.col("fullcg") - pl.col("capded"))

    # Two-earner deduction (1982+ only) - 10% of the lesser-earning
    # spouse's own wages, capped; 1982 itself used a smaller transitional
    # 5%/$1,500 version.
    if year >= 1982:
        two_earner_rate = float(resolve_year(p["two_earner_deduction_rate"], year))
        two_earner_cap = float(resolve_year(p["two_earner_deduction_cap"], year))
        df = df.with_columns(wife=pl.min_horizontal(pl.col("pwages"), pl.col("swages")).clip(0, None))
        df = df.with_columns(
            twoded=pl.when(pl.col("filing_status") == "married_joint")
            .then((two_earner_rate * pl.col("wife")).clip(0, two_earner_cap))
            .otherwise(0.0)
        )
    else:
        df = df.with_columns(twoded=pl.lit(0.0))

    df = df.with_columns(
        ti=pl.col("divall") + pl.col("wages") + pl.col("ints") + pl.col("gross_se_income"),
    )
    df = df.with_columns(agi_pre_ui=pl.col("ti") - pl.col("twoded") + pl.col("capgn"))

    # Unemployment compensation: fully excluded pre-1979; from 1979 on,
    # half the excess over a status-specific threshold becomes taxable
    # (capped at the UI amount itself). taxsim_2022_10_21.f:23437-23443.
    if year <= 1978:
        df = df.with_columns(untax=pl.lit(0.0))
    else:
        uxemp_expr = _by_status_expr(
            {status: resolve_year(p["unemployment_exclusion_threshold"][status], year) for status in FILING_STATUSES}
        )
        df = df.with_columns(uxemp=uxemp_expr)
        df = df.with_columns(
            untax=pl.min_horizontal(
                (0.5 * (pl.col("ui") + pl.col("agi_pre_ui") - pl.col("uxemp")).clip(0, None)),
                pl.col("ui"),
            )
        )
    df = df.with_columns(agi=pl.col("agi_pre_ui") + pl.col("untax"))

    # Itemized deduction: proptax + otheritem + mortgage + the state/sales
    # tax deduction (`data(50)`, real - law79's own `deduc` formula reads
    # it directly, taxsim_2022_10_21.f:210 - a real gap found only while
    # building Alabama's state calculator: harmless for every year this
    # project validated federal-only against TX, since TX's own sales-tax
    # deduction is genuinely $0 before 2004 - see engine/federal_state.py)
    # - medical, charitable contributions, and casualty losses are all
    # correctly $0 here - see the module docstring / pre1987.yaml's scope
    # note, none of their own `data()` fields have a corresponding named
    # input.
    zbr_expr = _by_status_expr({status: resolve_year(p["standard_deduction"][status], year) for status in FILING_STATUSES})
    df = df.with_columns(
        deduc=pl.col("proptax") + pl.col("otheritem") + pl.col("mortgage") + pl.col("state_sales_or_income_tax_ded"),
        zbr=zbr_expr,
    )
    # `force_itemize` (`data(4)`) only actually gates the real source's
    # own itemize-vs-standard choice for lawyr>=1982 (`(deduc+char>zbr+
    # charni and data(4)>=0) or data(4)==-1` - taxsim_2022_10_21.f:23532).
    # For lawyr<=1981 the commented-out `data(4)` checks right above that
    # block are dead code (`c if(data(4).eq.-1) zbr=0.` / `c if(data(4).
    # eq.-2) deduc=0.`) - the real 1977-1981 choice is ALWAYS the plain
    # `deduc>zbr` dollar comparison, never forced. Confirmed via a live
    # oracle probe (Arkansas, single, $12,500 wages, 1977 vs 1982: forcing
    # itemize with $0 itemized deductions changes 1982's federal tax but
    # NOT 1977's).
    if year <= 1981:
        itemizes_expr = pl.col("deduc") > pl.col("zbr")
    else:
        itemizes_expr = pl.lit(force_itemize) if force_itemize is not None else (pl.col("deduc") > pl.col("zbr"))
    df = df.with_columns(itemizes=itemizes_expr)
    df = df.with_columns(excess=pl.when(pl.col("itemizes")).then(pl.col("deduc") - pl.col("zbr")).otherwise(0.0))

    exemption_amount = float(resolve_year(p["personal_exemption_amount"], year))
    df = df.with_columns(
        exemps_count=(
            pl.when(pl.col("filing_status") == "married_joint").then(2.0).otherwise(1.0) + pl.col("depx")
        )
    )
    df = df.with_columns(amex=exemption_amount * pl.col("exemps_count"))
    df = df.with_columns(taxy=pl.col("agi") - pl.col("excess") - pl.col("amex"))
    if year <= 1978:
        df = df.with_columns(taxable_income_for_bracket=(pl.col("taxy") - pl.col("zbr")).clip(0, None))
    else:
        df = df.with_columns(taxable_income_for_bracket=pl.col("taxy").clip(0, None))
    # `taxinc` (pre-1979's own further-zbr-subtracted figure) and `taxy`
    # both stay unclamped below zero going into the alternative-tax/
    # preference-income formulas (matching the source, which only clips
    # at the very end via `if(taxinc.lt.0.)taxinc=0.` right before the
    # bracket lookup) - kept separately here since acgtax/etax reuse the
    # unclamped `taxy` (and, pre-1979, `taxinc`) directly.
    df = df.with_columns(taxinc_unclamped=pl.col("taxy") - pl.col("zbr"))

    df = df.with_columns(regtax_raw=_bracket_tax_by_status(pl.col("taxable_income_for_bracket"), brackets_by_status))
    if year == 1981:
        rrc = float(resolve_year(p["rate_reduction_credit_multiplier"], year))
        df = df.with_columns(regtax=rrc * pl.col("regtax_raw"))
    else:
        df = df.with_columns(regtax=pl.col("regtax_raw"))

    # --- Alternative computation of tax on capital gains (`acgtax`) ---
    # Real only 1977-1978 (a blended 25%/50% formula gated at a $50,000/
    # sepret breakpoint) and 1981 (a flat effective-20%-cap one-year
    # formula) - every other year has no such mechanism (`acgtax` stays
    # inert, capital gains flow through the exclusion alone).
    if year in (1977, 1978):
        threshold = float(resolve_year(p["alt_capital_gains_tax_threshold_pre1979"], year))
        df = df.with_columns(cg1=(pl.col("taxinc_unclamped") - pl.col("capded")).clip(0, None))
        df = df.with_columns(cgtx1=_bracket_tax_by_status(pl.col("cg1"), brackets_by_status))
        df = df.with_columns(subd=(threshold / pl.col("sepret")).clip(0, None))
        df = df.with_columns(
            gated=pl.col("capded") * 2 > (threshold / pl.col("sepret")),
        )
        df = df.with_columns(further_gated=pl.col("gated") & (pl.col("subd") < pl.col("capded") * 2))
        df = df.with_columns(
            cgtx2=pl.when(pl.col("further_gated")).then(0.25 * pl.col("subd")).otherwise(0.5 * pl.col("capded"))
        )
        df = df.with_columns(cg3=pl.max_horizontal(pl.col("taxinc_unclamped"), pl.col("capded")))
        df = df.with_columns(cgtx31=_bracket_tax_by_status(pl.col("cg3"), brackets_by_status))
        df = df.with_columns(cg1_plus_half_subd=pl.col("cg1") + pl.col("subd") * 0.5)
        df = df.with_columns(cgtx32=_bracket_tax_by_status(pl.col("cg1_plus_half_subd"), brackets_by_status))
        df = df.with_columns(
            cgtx3=pl.when(pl.col("further_gated")).then(pl.col("cgtx31") - pl.col("cgtx32")).otherwise(0.0)
        )
        df = df.with_columns(
            acgtax=pl.when(pl.col("fullcg") > 0)
            .then(pl.col("cgtx1") + pl.col("cgtx2") + pl.col("cgtx3"))
            .otherwise(-1.0)
        )
    elif year == 1981:
        rrc = float(resolve_year(p["rate_reduction_credit_multiplier"], year))
        df = df.with_columns(cglong=pl.min_horizontal(pl.col("ltcg"), pl.col("fullcg")).clip(0, None))
        df = df.with_columns(cg1=(pl.col("taxy") - 0.4 * pl.col("cglong")).clip(0, None))
        df = df.with_columns(cgtx1_raw=_bracket_tax_by_status(pl.col("cg1"), brackets_by_status))
        df = df.with_columns(
            acgtax=pl.when(pl.col("cglong") > 0)
            .then(rrc * pl.col("cgtx1_raw") + 0.2 * pl.col("cglong"))
            .otherwise(-1.0)
        )
    else:
        df = df.with_columns(acgtax=pl.lit(-1.0))

    # --- "Maximum tax on earned income" (`etax`), 1977-1981 only ---
    if year <= 1981:
        ebot_expr = _by_status_expr(
            {status: resolve_year(p["max_tax_earned_income_floor"][status], year) for status in FILING_STATUSES}
        )
        eacc_expr = _by_status_expr(
            {status: resolve_year(p["max_tax_earned_income_accumulated"][status], year) for status in FILING_STATUSES}
        )
        df = df.with_columns(ebot=ebot_expr, eacc=eacc_expr)
        df = df.with_columns(
            psinc=pl.when(pl.col("agi") != 0).then(pl.col("earned").clip(0, pl.col("agi"))).otherwise(pl.col("earned"))
        )
        df = df.with_columns(
            eratio=pl.when(pl.col("agi") == 0).then(1.0).otherwise((pl.col("psinc") / pl.col("agi")).clip(None, 1.0))
        )
        # Preference income (`pref`) feeding `eti`: for 1977-1978, itemizers
        # add back capital gains' excluded share, UI, and wages, plus an
        # excess-itemized-deductions preference of their own (a genuinely
        # different exded formula from the one 1979-1986 use for the
        # minimum tax below); for 1979-1981, `pref` is always 0 (it reads
        # an input field - `data(164)` - this project's schema never
        # populates) despite the source computing an unused `exded` right
        # alongside it. taxsim_2022_10_21.f:23683-23701.
        if year <= 1978:
            df = df.with_columns(
                exded_pref=pl.min_horizontal((pl.col("deduc") - 0.6 * pl.col("agi")).clip(0, None), 0.4 * pl.col("agi"))
            )
            df = df.with_columns(
                pref=pl.when(pl.col("itemizes"))
                .then((pl.col("capded") + pl.col("ui") + pl.col("wages") + pl.col("exded_pref")).clip(0, None))
                .otherwise(0.0)
            )
            df = df.with_columns(eti=pl.col("taxinc_unclamped") * pl.col("eratio") - pl.col("pref"))
        else:
            df = df.with_columns(eti=pl.col("taxy") * pl.col("eratio"))
        df = df.with_columns(etop=pl.col("eti") - pl.col("ebot"))
        df = df.with_columns(partax=_bracket_tax_by_status(pl.col("eti"), brackets_by_status))
        if year == 1981:
            rrc = float(resolve_year(p["rate_reduction_credit_multiplier"], year))
            df = df.with_columns(
                etax_raw=rrc * (pl.col("regtax_raw") - pl.col("partax") + pl.col("eacc")) + 0.5 * pl.col("etop")
            )
        else:
            df = df.with_columns(
                etax_raw=pl.col("regtax") - pl.col("partax") + pl.col("eacc") + 0.5 * pl.col("etop")
            )
        df = df.with_columns(
            etax_eligible=(pl.col("sepret") == 1.0) & (pl.col("agi") > 0) & (pl.col("psinc") > 0) & (pl.col("etop") > 0)
        )
        df = df.with_columns(etax=pl.when(pl.col("etax_eligible")).then(pl.col("etax_raw")).otherwise(-1.0))
    else:
        df = df.with_columns(etax=pl.lit(-1.0))

    # --- Combine alternatives: taxbc = min(regular, acgtax, etax) ---
    df = df.with_columns(
        taxbc=pl.when(pl.col("acgtax") > 0).then(pl.min_horizontal(pl.col("acgtax"), pl.col("regtax"))).otherwise(pl.col("regtax"))
    )
    df = df.with_columns(
        taxbc=pl.when((pl.col("etax") > 0) & (pl.col("etax") < pl.col("taxbc"))).then(pl.col("etax")).otherwise(pl.col("taxbc"))
    )

    # --- Child and Dependent Care Credit (only nonrefundable credit in scope) ---
    expense_cap = float(resolve_year(p["child_care_credit_expense_cap"], year))
    df = df.with_columns(
        chmax=expense_cap * pl.col("dep13").clip(0, 2),
        chwage=pl.col("wages").clip(0, None),
    )
    df = df.with_columns(child_expense=pl.min_horizontal(pl.col("chmax"), pl.col("chwage"), pl.col("childcare")))
    if year <= 1981:
        chr_rate = float(resolve_year(p["child_care_credit_flat_rate"], year))
        df = df.with_columns(ccc_rate=pl.lit(chr_rate))
    else:
        df = df.with_columns(
            ccc_rate=(0.30 - ((pl.col("agi") - 8000.0) / 200000.0).clip(0, None)).clip(0.20, None)
        )
    df = df.with_columns(chcr=pl.col("child_expense") * pl.col("ccc_rate"))

    # General Tax Credit (Tax Reduction Act of 1975, expired after 1978) -
    # `gencr=max(35*exemps,min(180,.02*taxinc))` for 1976-1978 (1975 itself
    # is out of this function's scope - year<=1976 routes to
    # federal_law60.py instead). Missing entirely from taxsim_2022_10_21.f
    # (a genuine gap in that release, added in taxsim_2024_09_21.f:23936-
    # 23940) - found via a live 2-oracle comparison (single, $4,000 wages,
    # 1977: old oracle $153.00 fiitax, new oracle $118.00 - a flat $35
    # gap matching the `35*exemps` floor for this low-taxinc case exactly).
    if year in (1977, 1978):
        gencr = pl.max_horizontal(
            35.0 * pl.col("exemps_count"), pl.min_horizontal(180.0, 0.02 * pl.col("taxable_income_for_bracket"))
        )
    else:
        gencr = pl.lit(0.0)
    df = df.with_columns(gencr=gencr)
    df = df.with_columns(credit=pl.min_horizontal(pl.col("chcr") + pl.col("gencr"), pl.col("taxbc")).clip(0, None))
    df = df.with_columns(taxaft=pl.col("taxbc") - pl.col("credit"))

    # --- Minimum tax: three distinct formulas layered as a floor over taxaft ---
    if year <= 1978:
        df = df.with_columns(mintax_raw=pl.lit(-1.0))  # inert - no mechanism reaches `tax` at all
    elif year <= 1981:
        t1 = float(resolve_year(p["minimum_tax_tier1_threshold"], year))
        t2 = float(resolve_year(p["minimum_tax_tier2_threshold"], year))
        t3 = float(resolve_year(p["minimum_tax_tier3_threshold"], year))
        r1 = float(resolve_year(p["minimum_tax_tier1_rate"], year))
        r2 = float(resolve_year(p["minimum_tax_tier2_rate"], year))
        r3 = float(resolve_year(p["minimum_tax_tier3_rate"], year))
        # `alminy = agi - amex - excess - zbr + capded` (taxsim_2022_10_21.f:
        # 23977-23978) - NOT `taxable_income_for_bracket + capded`. For
        # year>=1979, `taxable_income_for_bracket` (=`taxy`) does NOT have
        # zbr subtracted a second time (zbr's already baked into the
        # bracket table itself), but `alminy` DOES still subtract it - so
        # `agi-amex-excess-zbr` = `taxy-zbr` = `taxinc_unclamped` exactly
        # (the same quantity 1977-1978's own bracket lookup uses). A real
        # bug on the first pass: using `taxable_income_for_bracket`
        # directly overstated alminy by exactly one zbr, wrongly triggering
        # the minimum tax on cases where the real tax never reaches it -
        # confirmed via a live oracle probe (1979, single, $400,000 ltcg:
        # real alminy=$396,700, giving a 3-tier sum of $86,175, LESS than
        # the $91,187 regular tax already owed - the floor never binds; the
        # buggy $399,000 alminy gave $101,700, wrongly overriding it).
        df = df.with_columns(alminy=pl.col("taxinc_unclamped") + pl.col("capded"))
        df = df.with_columns(
            mintax_raw=(
                r1 * (pl.col("alminy") - t1 / pl.col("sepret")).clip(0, None)
                + r2 * (pl.col("alminy") - t2 / pl.col("sepret")).clip(0, None)
                + r3 * (pl.col("alminy") - t3 / pl.col("sepret")).clip(0, None)
            )
        )
    elif year == 1982:
        t1 = float(resolve_year(p["minimum_tax_tier1_threshold"], year))
        t2 = float(resolve_year(p["minimum_tax_tier2_threshold"], year))
        r1 = float(resolve_year(p["minimum_tax_tier1_rate"], year))
        r2 = float(resolve_year(p["minimum_tax_tier2_rate"], year))
        df = df.with_columns(
            exded_1979plus=pl.when(pl.col("itemizes"))
            .then((pl.col("deduc") - 0.6 * pl.col("agi").clip(0, None)).clip(0, None))
            .otherwise(0.0)
        )
        df = df.with_columns(
            alminy=pl.col("agi") - pl.col("excess") - pl.col("zbr") - pl.col("amex") + pl.col("capded") + pl.col("exded_1979plus")
        )
        df = df.with_columns(
            mintax_raw=r1 * (pl.col("alminy") - t1 / pl.col("sepret")).clip(0, None)
            + r2 * (pl.col("alminy") - t2 / pl.col("sepret")).clip(0, None)
        )
    else:  # 1983-1986
        flat_rate = float(resolve_year(p["minimum_tax_flat_rate"], year))
        offset_single_hoh = float(resolve_year(p["minimum_tax_offset_single_hoh"], year))
        offset_joint_sep = float(resolve_year(p["minimum_tax_offset_joint_sep"], year))
        df = df.with_columns(
            offset=pl.when(pl.col("filing_status").is_in(["single", "head_of_household"]))
            .then(offset_single_hoh)
            .otherwise(offset_joint_sep / pl.col("sepret"))
        )
        df = df.with_columns(prfded=pl.when(pl.col("itemizes")).then(pl.col("deduc")).otherwise(0.0))
        df = df.with_columns(alminy=(pl.col("agi") - pl.col("prfded") + pl.col("capded")).clip(0, None))
        df = df.with_columns(mintax_raw=flat_rate * (pl.col("alminy") - pl.col("offset")).clip(0, None))

    df = df.with_columns(
        tax_after_mintax=pl.when(pl.col("mintax_raw") > 0)
        .then(pl.max_horizontal(pl.col("taxaft"), pl.col("mintax_raw")))
        .otherwise(pl.col("taxaft"))
    )

    # --- EITC (own early rate schedule - a single flat table, not varying
    # by status or number of children, only by eligibility) ---
    rate_in = float(resolve_year(p["eitc_rate_in"], year))
    max_credit = float(resolve_year(p["eitc_max_credit"], year))
    phaseout_start = float(resolve_year(p["eitc_phaseout_start"], year))
    rate_out = float(resolve_year(p["eitc_rate_out"], year))
    df = df.with_columns(
        earncr_raw=trapezoid_credit(pl.col("earned"), pl.col("agi"), rate_in, max_credit, phaseout_start, rate_out)
    )
    df = df.with_columns(
        earncr=pl.when((pl.col("filing_status") == "married_separate") | (pl.col("dep18") == 0))
        .then(0.0)
        .otherwise(pl.col("earncr_raw"))
    )

    df = df.with_columns(fiitax=pl.col("tax_after_mintax") - pl.col("earncr"))
    df = df.with_columns(
        taxable_income=pl.col("taxable_income_for_bracket"),
        earned_income=pl.col("earned"),
        regular_tax=pl.col("regtax"),
    )
    result_columns = [
        "filing_status",
        "wages",
        "agi",
        "taxable_income",
        "earned_income",
        "regular_tax",
        "fiitax",
        "credit",
    ]
    return df.select([*original_columns, *result_columns])
