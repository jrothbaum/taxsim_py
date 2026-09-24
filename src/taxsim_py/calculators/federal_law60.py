"""Federal individual income tax calculator for 1960 through 1976."""

import polars as pl

from taxsim_py.engine.brackets import bracket_tax
from taxsim_py.engine.eitc import trapezoid_credit
from taxsim_py.engine.schema import PARAMETERS_ROOT, load_yaml, resolve_year

LAW60_PARAMS = load_yaml(PARAMETERS_ROOT / "national" / "law60.yaml")

FILING_STATUSES = ["single", "married_joint", "married_separate", "head_of_household"]
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


def _halve_thresholds(brackets: list[list[float]]) -> list[list[float]]:
    # married_separate reuses married_joint's own table at sepret=2 (income
    # doubled before lookup, tax halved after in the source's own
    # `faster=taxinc*sepret` / `tax=(...)/sepret` mechanism) - bracket_tax is
    # homogeneous of degree 1, so halving every threshold (keeping the huge
    # terminal sentinel as-is) reproduces that exactly without a separate
    # scaling parameter, same technique already used elsewhere in this
    # project (e.g. the 2017 married_separate bracket fix).
    return [[t / 2 if t < 1.0e28 else t, r] for t, r in brackets]


def _bracket_tax_by_status(income: pl.Expr, brackets_by_status: dict[str, list[list[float]]]) -> pl.Expr:
    expr = pl.lit(None, dtype=pl.Float64)
    for status, brackets in brackets_by_status.items():
        expr = pl.when(pl.col("filing_status") == status).then(bracket_tax(income, brackets)).otherwise(expr)
    return expr


def compute_regular_tax_law60(
    df: pl.DataFrame | pl.LazyFrame, year: int
) -> pl.DataFrame | pl.LazyFrame:
    if year < 1960:
        raise NotImplementedError("law60 only covers 1960-1976 - see parameters/national/law60.yaml")
    p = LAW60_PARAMS
    original_columns = (
        df.collect_schema().names() if isinstance(df, pl.LazyFrame) else list(df.columns)
    )
    df = df.with_columns(
        filing_status=_filing_status_expr(),
        wages=pl.col("pwages") + pl.col("swages"),
    )
    for col in (
        "proptax", "otheritem", "mortgage", "intrec", "psemp", "ssemp",
        "dividends", "stcg", "ltcg", "ui", "pui", "sui", "childcare", "depx",
    ):
        df = _with_default(df, col)

    sepret_expr = _by_status_expr(SEPRET_BY_STATUS)
    single_brackets = resolve_year(p["brackets"]["single"], year)
    joint_brackets = resolve_year(p["brackets"]["married_joint"], year)
    brackets_by_status = {
        "single": single_brackets,
        "married_joint": joint_brackets,
        "head_of_household": resolve_year(p["brackets"]["head_of_household"], year),
        # Phase 2 (year<=1970): married_separate uses SINGLE's own table
        # directly (`nfile1=1`, no scaling at all). Phase 1 (year>=1971):
        # married_separate reuses married_joint's table at sepret=2 (see
        # module docstring point 6).
        "married_separate": single_brackets if year <= 1970 else _halve_thresholds(joint_brackets),
    }

    df = df.with_columns(
        sepret=sepret_expr,
        se_income=pl.col("psemp") + pl.col("ssemp"),
    )
    df = df.with_columns(earned=pl.col("wages") + pl.col("se_income").clip(0, None))

    # Dividend exclusion.
    divexc_expr = _by_status_expr(
        {status: resolve_year(p["dividend_exclusion"][status], year) for status in FILING_STATUSES}
    )
    df = df.with_columns(divexc=divexc_expr)
    df = df.with_columns(divall=(pl.col("dividends") - pl.col("divexc")).clip(0, None))

    # Capital gains: part of the long-term gain is excluded (`capded`),
    # applied to the net gain when a short-term loss offsets it. A net
    # loss is limited per return.
    caprat = float(resolve_year(p["capital_gains_exclusion_rate"], year))
    loss_limit = pl.when(pl.col("filing_status") == "married_separate").then(
        float(resolve_year(p["net_capital_loss_limit_married_separate"], year))
    ).otherwise(float(resolve_year(p["net_capital_loss_limit"], year)))
    df = df.with_columns(fullcg=pl.col("stcg") + pl.col("ltcg"))
    df = df.with_columns(
        capded=pl.when((pl.col("fullcg") > 0) & (pl.col("ltcg") > 0))
        .then(caprat * pl.when(pl.col("stcg") < 0).then(pl.col("fullcg")).otherwise(pl.col("ltcg")))
        .otherwise(0.0)
    )
    df = df.with_columns(
        capgn=pl.when(pl.col("fullcg") > 0)
        .then(pl.col("fullcg") - pl.col("capded"))
        .otherwise(pl.max_horizontal(pl.col("fullcg"), -loss_limit))
    )

    # Total income and AGI - no adjustments of any kind reach this scope
    # (see module docstring point 1); unemployment compensation plays no
    # role here either (point 2).
    df = df.with_columns(
        ti=pl.col("divall") + pl.col("wages") + pl.col("intrec") + pl.col("se_income") + pl.col("capgn")
    )
    df = df.with_columns(agi=pl.col("ti"))

    # Itemized deduction: proptax + otheritem + mortgage, plus (1971-1975
    # only) childcare as an uncapped itemized deduction - see module
    # docstring point 5.
    deduc_base = pl.col("proptax") + pl.col("otheritem") + pl.col("mortgage")
    if year <= 1975:
        df = df.with_columns(deduc=deduc_base + pl.col("childcare"))
    else:
        df = df.with_columns(deduc=deduc_base)

    df = df.with_columns(agix=pl.col("agi").clip(0, None))
    exemption_amount = float(resolve_year(p["personal_exemption_amount"], year))
    df = df.with_columns(
        exemps_count=(
            pl.when(pl.col("filing_status") == "married_joint").then(2.0).otherwise(1.0) + pl.col("depx")
        )
    )
    if year <= 1963:
        # <=1963: a straight percentage of AGI capped at a flat ceiling, NO
        # floor term at all - taxsim_2022_10_21.f:22673-22674.
        pct = float(resolve_year(p["standard_deduction_pct"], year))
        ceiling = float(resolve_year(p["standard_deduction_ceiling_other"], year))
        df = df.with_columns(zbr=pl.min_horizontal(ceiling / pl.col("sepret"), pct * pl.col("agix")))
    elif year <= 1970:
        # 1964-1970: same percentage-capped-at-ceiling term as <=1963, but
        # now floored at the GREATER of (a) that same ceiling/sepret capped
        # differently or (b) a per-exemption floor ($200/sepret +
        # $100*exemps) - taxsim_2022_10_21.f:22670-22672. Both terms are
        # themselves capped at ceiling/sepret before the max() - matching
        # the source's nested `min(ceiling/sepret, ...)` on each side.
        pct = float(resolve_year(p["standard_deduction_pct"], year))
        ceiling = float(resolve_year(p["standard_deduction_ceiling_other"], year))
        per_exemption_base = float(resolve_year(p["standard_deduction_per_exemption_base"], year))
        per_exemption_amount = float(resolve_year(p["standard_deduction_per_exemption_amount"], year))
        df = df.with_columns(
            zbr=pl.max_horizontal(
                pl.min_horizontal(ceiling / pl.col("sepret"), pct * pl.col("agix")),
                pl.min_horizontal(
                    ceiling / pl.col("sepret"),
                    per_exemption_base / pl.col("sepret") + per_exemption_amount * pl.col("exemps_count"),
                ),
            )
        )
        if year == 1970:
            # A genuine, one-year-only "Additional Allowance for Low
            # Incomes" (taxsim_2024_09_21.f:22629-22632; absent from
            # taxsim_2022_10_21.f entirely - a real gap in that release):
            # `zbradd=900-100*exemps`, `zbrpo=max(0,.5*(agi-(1100+625*
            # exemps)))`, `zbr += max(0, zbradd-zbrpo)` - phases out this
            # extra deduction as AGI rises above the exemption-scaled
            # threshold. Found via a live oracle-vs-oracle diff of the
            # whole `law60` subroutine, then confirmed against a very-low-
            # wage 1970 test case (taxsim2024.exe: $0 tax at $1,000-2,000
            # wages single; this project's code, missing this allowance
            # entirely, showed real - if small - tax owed there instead).
            zbradd = 900.0 - 100.0 * pl.col("exemps_count")
            zbrpo = (0.5 * (pl.col("agi") - (1100.0 + 625.0 * pl.col("exemps_count")))).clip(0, None)
            df = df.with_columns(zbr=pl.col("zbr") + (zbradd - zbrpo).clip(0, None))
    elif year <= 1974:
        # 1971-1974: one shared ceiling/floor formula for every status (no
        # joint/other split existed yet - taxsim_2022_10_21.f:22666-22672).
        zbr_pct = float(resolve_year(p["standard_deduction_pct"], year))
        zbr_ceiling_other = float(resolve_year(p["standard_deduction_ceiling_other"], year))
        zbr_floor_other = float(resolve_year(p["standard_deduction_floor_other"], year))
        df = df.with_columns(
            zbr=pl.max_horizontal(
                pl.min_horizontal(zbr_ceiling_other / pl.col("sepret"), zbr_pct * pl.col("agix")),
                zbr_floor_other / pl.col("sepret"),
            )
        )
    else:
        zbr_pct = float(resolve_year(p["standard_deduction_pct"], year))
        zbr_ceiling_other = float(resolve_year(p["standard_deduction_ceiling_other"], year))
        zbr_floor_other = float(resolve_year(p["standard_deduction_floor_other"], year))
        zbr_ceiling_joint = float(resolve_year(p["standard_deduction_ceiling_joint"], year))
        zbr_floor_joint = float(resolve_year(p["standard_deduction_floor_joint"], year))
        # `nfile==2` gates the joint-vs-other split in the source, and
        # `nfile` stays 2 for married_separate too (only `sepret` is set to
        # 2 for that status - taxsim_2022_10_21.f:22498-22507) - so
        # married_separate gets the SAME joint ceiling/floor as
        # married_joint (both divided by sepret, sepret=2 halving it for
        # separate filers), not the "other" formula. A real bug on the
        # first pass here, found via a live oracle probe (1975,
        # married_separate, $5,000 wages: real taxable income $3,300, not
        # this fix's-absence $3,450 - exactly the $150 gap between the
        # "other" floor/2=$800 and the real joint floor/2=$950).
        df = df.with_columns(
            zbr=pl.when(pl.col("filing_status").is_in(["married_joint", "married_separate"]))
            .then(
                pl.max_horizontal(
                    pl.min_horizontal(zbr_ceiling_joint / pl.col("sepret"), zbr_pct * pl.col("agix")),
                    zbr_floor_joint / pl.col("sepret"),
                )
            )
            .otherwise(
                pl.max_horizontal(
                    pl.min_horizontal(zbr_ceiling_other / pl.col("sepret"), zbr_pct * pl.col("agix")),
                    zbr_floor_other / pl.col("sepret"),
                )
            )
        )

    df = df.with_columns(itemizes=pl.col("deduc") > pl.col("zbr"))
    df = df.with_columns(amex=exemption_amount * pl.col("exemps_count"))

    # Taxable income: no "excess" add-back concept exists in this scope
    # (module docstring point 3) - simply agi minus whichever deduction
    # applies, minus the exemption amount, clipped at 0.
    df = df.with_columns(
        taxable_income=(
            pl.col("agi") - pl.when(pl.col("itemizes")).then(pl.col("deduc")).otherwise(pl.col("zbr")) - pl.col("amex")
        ).clip(0, None)
    )

    df = df.with_columns(regtax=_bracket_tax_by_status(pl.col("taxable_income"), brackets_by_status))

    # --- Alternative tax on capital gains (the `$50,000/sepret`-threshold
    # blend is real only 1970-1975 - year<=1969 and year==1976 both fall
    # through to a degenerate case of the same formula, either because no
    # matching branch exists in the source at all (<=1969) or because it's
    # gated on an unpopulated input field (1976's `subd` - see law60.yaml).
    # `cgtx1` itself (the base bracket-tax term) is real for every year. ---
    df = df.with_columns(cg1=(pl.col("taxable_income") - pl.col("capded")).clip(0, None))
    df = df.with_columns(cgtx1=_bracket_tax_by_status(pl.col("cg1"), brackets_by_status))
    if 1970 <= year <= 1975:
        threshold = float(resolve_year(p["alt_capital_gains_threshold"], year))
        flat_component = float(resolve_year(p["alt_capital_gains_flat_component"], year))
        cap_rate = float(resolve_year(p["alt_capital_gains_cap_rate"], year))
        df = df.with_columns(
            gate=pl.min_horizontal(pl.col("ltcg"), pl.col("fullcg")) > threshold / pl.col("sepret")
        )
        df = df.with_columns(
            xl32=_bracket_tax_by_status(pl.max_horizontal(pl.col("taxable_income"), pl.col("capded")), brackets_by_status),
            xl33=_bracket_tax_by_status(
                (pl.col("taxable_income") - pl.col("capded") + threshold / 2.0 / pl.col("sepret")).clip(0, None),
                brackets_by_status,
            ),
        )
        df = df.with_columns(raw_diff=(pl.col("xl32") - pl.col("xl33")).clip(0, None))
        # 1970 caps the blend at 29.5% of the excess over the threshold,
        # 1971 at 32.5%, 1972-1975 leave it uncapped - a single per-year
        # `alt_capital_gains_cap_rate` (a huge sentinel for the uncapped
        # years) reproduces all three without a separate code branch.
        df = df.with_columns(
            cgtx2_gated=pl.min_horizontal(
                pl.col("raw_diff"),
                cap_rate * (pl.min_horizontal(pl.col("ltcg"), pl.col("fullcg")) - threshold / pl.col("sepret")).clip(0, None),
            )
        )
        df = df.with_columns(
            cgtx2=pl.when(pl.col("gate")).then(pl.col("cgtx2_gated")).otherwise(0.5 * pl.col("capded")),
            cgtx3=pl.when(pl.col("gate")).then(flat_component / pl.col("sepret")).otherwise(0.0),
        )
    else:  # year<=1969 (no matching branch) or year==1976 (`subd` unpopulated)
        df = df.with_columns(cgtx2=0.5 * pl.col("capded"), cgtx3=pl.lit(0.0))
    df = df.with_columns(
        acgtax=pl.when(pl.col("fullcg") > 0).then(pl.col("cgtx1") + pl.col("cgtx2") + pl.col("cgtx3")).otherwise(-1.0)
    )

    # --- "Maximum tax on earned income" - real 1971-1976 only (module
    # docstring point 9); doesn't exist at all before 1971, so no parameter
    # lookup is even attempted for year<1971. ---
    if year >= 1971:
        ebot_expr = _by_status_expr(
            {status: resolve_year(p["max_tax_earned_income_floor"][status], year) for status in FILING_STATUSES}
        )
        eacc_expr = _by_status_expr(
            {status: resolve_year(p["max_tax_earned_income_accumulated"][status], year) for status in FILING_STATUSES}
        )
        etop_mult = float(resolve_year(p["max_tax_earned_income_etop_multiplier"], year))
        df = df.with_columns(ebot=ebot_expr, eacc=eacc_expr)
        df = df.with_columns(
            psinc=pl.when(pl.col("agi") > 0).then(pl.col("earned").clip(0, pl.col("agi"))).otherwise(pl.col("earned"))
        )
        df = df.with_columns(
            eratio=pl.when(pl.col("agi") == 0)
            .then(1.0)
            .otherwise((pl.col("psinc") / pl.col("agi")).clip(None, 1.0))
        )
        df = df.with_columns(
            exded=pl.when(pl.col("itemizes") & ((pl.col("deduc") - 0.6 * pl.col("agi")) > 0))
            .then(pl.min_horizontal(pl.col("deduc") - 0.6 * pl.col("agi"), 0.4 * pl.col("agi")))
            .otherwise(0.0)
        )
        df = df.with_columns(preference_ui=pl.max_horizontal(pl.col("ui"), pl.col("pui") + pl.col("sui")))
        df = df.with_columns(pref=(pl.col("capded") + pl.col("preference_ui") + pl.col("exded")).clip(0, None))
        df = df.with_columns(eti=pl.col("taxable_income") * pl.col("eratio") - (pl.col("pref") - 30000.0).clip(0, None))
        df = df.with_columns(etop=pl.col("eti") - pl.col("ebot"))
        df = df.with_columns(partax=_bracket_tax_by_status(pl.col("eti"), brackets_by_status))
        df = df.with_columns(etax_raw=pl.col("regtax") - pl.col("partax") + pl.col("eacc") + etop_mult * pl.col("etop"))
        df = df.with_columns(
            etax_eligible=(pl.col("sepret") == 1.0)
            & (pl.col("agi") >= 0)
            & (pl.col("etop") > 0)
            & (pl.when(pl.col("agi") == 0).then(True).otherwise(pl.col("psinc") > 0))
        )
        df = df.with_columns(etax=pl.when(pl.col("etax_eligible")).then(pl.col("etax_raw")).otherwise(-1.0))
    else:
        df = df.with_columns(etax=pl.lit(-1.0))

    # --- Combine alternatives. When both the maximum tax and the
    # alternative capital gains tax save tax, their savings stack. ---
    df = df.with_columns(
        acgsav=pl.when(pl.col("acgtax") > 0).then((pl.col("regtax") - pl.col("acgtax")).clip(0, None)).otherwise(0.0)
    )
    df = df.with_columns(altax=pl.col("regtax") - pl.col("acgsav"))
    df = df.with_columns(
        altax=pl.when((pl.col("etax") > 0) & (pl.col("etax") < pl.col("regtax")) & (pl.col("acgsav") > 0))
        .then(pl.col("etax") - pl.col("acgsav"))
        .when((pl.col("etax") > 0) & (pl.col("etax") < pl.col("altax")))
        .then(pl.col("etax"))
        .otherwise(pl.col("altax"))
    )

    # --- Vietnam-era surtax (1968-1970 only, module docstring point 7) - a
    # flat multiplier on the already-combined `altax`, applied once, BEFORE
    # credits - not threaded into any sub-computation individually (unlike
    # 1981's Rate Reduction Credit). taxsim_2022_10_21.f:23047-23056. ---
    if year in (1968, 1969, 1970):
        surtax_multiplier = float(resolve_year(p["surtax_multiplier"], year))
        df = df.with_columns(altax=pl.col("altax") * surtax_multiplier)

    # --- Credits: Child Care Credit (1976), $30-per-exemption credit (1975),
    # General Tax Credit (1976) - every other credit this source computes is
    # inert given this project's schema (see module docstring / law60.yaml). ---
    if year == 1976:
        expense_cap = float(resolve_year(p["child_care_credit_expense_cap"], year))
        chr_rate = float(resolve_year(p["child_care_credit_flat_rate"], year))
        df = _with_default(df, "dep13")
        df = df.with_columns(chmax=expense_cap * pl.col("dep13").clip(0, 2))
        df = df.with_columns(child_expense=pl.min_horizontal(pl.col("chmax"), pl.col("wages").clip(0, None), pl.col("childcare")))
        df = df.with_columns(chcr=pl.col("child_expense") * chr_rate)
    else:
        df = df.with_columns(chcr=pl.lit(0.0))

    if year == 1975:
        exemption_credit = float(resolve_year(p["personal_exemption_credit_amount"], year))
        df = df.with_columns(gencr=exemption_credit * pl.col("exemps_count"))
    elif year == 1976:
        per_exemption = float(resolve_year(p["general_tax_credit_per_exemption"], year))
        cap = float(resolve_year(p["general_tax_credit_cap"], year))
        rate = float(resolve_year(p["general_tax_credit_rate"], year))
        df = df.with_columns(
            gencr=pl.max_horizontal(per_exemption * pl.col("exemps_count"), (rate * pl.col("taxable_income")).clip(0, cap))
        )
    else:
        df = df.with_columns(gencr=pl.lit(0.0))

    df = df.with_columns(credit_raw=pl.col("chcr") + pl.col("gencr"))
    df = df.with_columns(credm=(pl.col("chcr") - pl.col("altax")).clip(0, None))
    df = df.with_columns(credit=pl.col("credit_raw").clip(0, pl.col("altax")))
    df = df.with_columns(taxaft=(pl.col("altax") - pl.col("credit")).clip(0, None))

    # --- Add-on minimum tax (genuinely ADDITIVE, not a floor - module
    # docstring point 4). ---
    if year <= 1975:
        offset_flat = float(resolve_year(p["addmin_offset_flat"], year))
        rate = float(resolve_year(p["addmin_rate_pre1976"], year))
        df = df.with_columns(offset=offset_flat / pl.col("sepret") + pl.col("taxaft") - pl.col("credit"))
        # The pre-1976 formula subtracts `gencr` a SECOND time here, on top
        # of `credit` already reducing `taxaft` above - taxsim_2022_10_21.f:
        # 23218 (`addmin = max(0,(data(81)+capgn-offset)*.10-credm-gencr)`).
        # Confirmed via a debug-instrumented oracle probe (1975, single,
        # $400,000 ltcg: real addmin=$4,880.50, the naive `-credm`-only
        # formula gave $4,910.50 - exactly $30 too high, $30 being that
        # year's own `gencr`).
        df = df.with_columns(
            addmin=(((pl.col("capgn") - pl.col("offset")) * rate) - pl.col("credm") - pl.col("gencr")).clip(0, None)
        )
    else:  # 1976
        offset_floor = float(resolve_year(p["addmin_offset_pct_floor"], year))
        rate = float(resolve_year(p["addmin_rate_1976to1978"], year))
        df = df.with_columns(
            offset=pl.max_horizontal(0.5 * (pl.col("taxaft") - pl.col("credit")), offset_floor / pl.col("sepret"))
        )
        df = df.with_columns(addmin=(((pl.col("capgn") - pl.col("offset")) * rate) - pl.col("credm")).clip(0, None))

    # Computed for every year<=1975, but only ever ADDED for 1969-1978
    # (module docstring point 4) - 1960-1968 discard it entirely.
    if year >= 1969:
        df = df.with_columns(tax_after_addmin=pl.col("taxaft") + pl.col("addmin"))
    else:
        df = df.with_columns(tax_after_addmin=pl.col("taxaft"))

    # --- EITC (real 1975-1976 only; eligibility keys off `depx`, not
    # `dep18` the way law79/law87 do - taxsim_2022_10_21.f:23188). ---
    if year >= 1975:
        rate_in = float(resolve_year(p["eitc_rate_in"], year))
        max_credit = float(resolve_year(p["eitc_max_credit"], year))
        phaseout_start = float(resolve_year(p["eitc_phaseout_start"], year))
        rate_out = float(resolve_year(p["eitc_rate_out"], year))
        df = df.with_columns(
            earncr_raw=trapezoid_credit(pl.col("earned"), pl.col("agi"), rate_in, max_credit, phaseout_start, rate_out)
        )
        df = df.with_columns(
            earncr=pl.when((pl.col("filing_status") == "married_separate") | (pl.col("depx") == 0))
            .then(0.0)
            .otherwise(pl.col("earncr_raw"))
        )
    else:
        df = df.with_columns(earncr=pl.lit(0.0))

    df = df.with_columns(fiitax=pl.col("tax_after_addmin") - pl.col("earncr"))
    df = df.with_columns(
        taxable_income=pl.col("taxable_income"),
        taxable_unemployment=pl.lit(0.0),
        earned_income=pl.col("earned"),
        regular_tax=pl.col("regtax"),
    )
    result_columns = [
        "filing_status",
        "wages",
        "agi",
        "taxable_unemployment",
        "taxable_income",
        "earned_income",
        "regular_tax",
        "fiitax",
    ]
    return df.select([*original_columns, *result_columns])
