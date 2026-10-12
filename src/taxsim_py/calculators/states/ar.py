"""Arkansas individual income tax calculator."""


from __future__ import annotations
import polars as pl

from taxsim_py.engine.inputs import aged_count, files_head_of_household, files_joint, files_separate, files_single, separate_divisor, taxpayer_count
from taxsim_py.engine.brackets import bracket_rate, bracket_tax
from taxsim_py.engine.schema import PARAMETERS_ROOT, YearParams, load_yaml, resolve_year
from taxsim_py.engine.state import by_filing_status, dividend_input_adjustment, federal_capital_gain_in_agi, forced_itemized, forced_standard, higher_earner_share, with_state_detail
from taxsim_py.behavior import BehaviorProfile, TAXSIM_BEHAVIOR
from taxsim_py.engine.state_extrapolation import deflate_for_extrapolation, resolve_state_year

AR_PARAMS = load_yaml(PARAMETERS_ROOT / "states" / "ar" / "income_tax.yaml")
STATE_ADJUSTMENT_PARAMS = load_yaml(PARAMETERS_ROOT / "national" / "state_adjustments.yaml")
AR_LOW_INCOME_TABLE = pl.read_csv(PARAMETERS_ROOT / "states" / "ar" / "low_income_formula_table.csv")
AR_LOW_INCOME_STEPS = pl.read_csv(PARAMETERS_ROOT / "states" / "ar" / "low_income_step_table.csv")


def _upper_bound_brackets(raw_pairs: list[list[float]]) -> list[list[float]]:
    """Convert [upper bound, rate %] rows to [start, rate] brackets."""
    out = [[0.0, raw_pairs[0][1] / 100.0]]
    for i in range(1, len(raw_pairs)):
        out.append([raw_pairs[i - 1][0], raw_pairs[i][1] / 100.0])
    return out


def _rate_lookup_brackets(rows: list[list[float]]) -> pl.Expr:
    """Build Arkansas's rate and subtraction lookup."""

    def build(income: pl.Expr) -> pl.Expr:
        expr = pl.lit(rows[-1][1] / 100.0) * income - pl.lit(rows[-1][2])
        for threshold, rate, subtraction in reversed(rows[:-1]):
            expr = pl.when(income < threshold).then((rate / 100.0) * income - subtraction).otherwise(expr)
        return expr

    return build


def _low_income_override(df: pl.DataFrame, effective_year: int) -> pl.Expr | None:
    table = AR_LOW_INCOME_TABLE.filter(pl.col("year") == effective_year)
    if table.height == 0:
        return None
    ge_lower = effective_year >= 2017
    applies = pl.lit(False)
    value = pl.lit(0.0)
    for status in ("single", "married_joint", "head_of_household"):
        rows = table.filter(pl.col("status") == status).sort("tier_order")
        if rows.height == 0:
            continue
        for dep_tier in sorted(set(rows["dep_tier"].to_list())):
            tier_rows = rows.filter(pl.col("dep_tier") == dep_tier).sort("tier_order")
            if dep_tier == 0:
                dep_cond = pl.lit(True)
            elif dep_tier == 1:
                dep_cond = pl.col("depx") <= 1
            else:
                dep_cond = pl.col("depx") >= 2
            status_cond = (pl.col("filing_status") == status) & dep_cond
            group_upper = float(tier_rows["upper_bound"][-1])
            group_lower0 = float(tier_rows["lower_bound"][0])
            lower_test = (pl.col("ar_agi") >= group_lower0) if ge_lower else (pl.col("ar_agi") > group_lower0)
            in_range = status_cond & lower_test & (pl.col("ar_agi") <= group_upper)
            applies = applies | (status_cond & (pl.col("ar_agi") <= group_upper))
            zero_upper = float(tier_rows["lower_bound"][0])
            tier_val = pl.when(pl.col("ar_agi") <= zero_upper).then(0.0).otherwise(pl.lit(None, dtype=pl.Float64))
            policy = tier_rows.select(
                "formula_offset", "base", "rate", "upper_bound", "upper_exclusive"
            ).to_dict(as_series=False)
            for offset, base, rate, upper, excl in zip(
                policy["formula_offset"],
                policy["base"],
                policy["rate"],
                policy["upper_bound"],
                policy["upper_exclusive"],
            ):
                cond = (pl.col("ar_agi") < upper) if excl else (pl.col("ar_agi") <= upper)
                tier_val = pl.when(cond & tier_val.is_null()).then(base + rate * (pl.col("ar_agi") - offset)).otherwise(tier_val)
            value = pl.when(in_range).then(tier_val.fill_null(0.0)).otherwise(value)
    return pl.when(applies).then(value).otherwise(None)


def compute_ar_tax(df: pl.DataFrame, year: int, behavior: BehaviorProfile = TAXSIM_BEHAVIOR) -> pl.DataFrame:
    effective_year, flate = resolve_state_year(year, behavior, AR_PARAMS)
    p = YearParams(AR_PARAMS, effective_year)

    # Business income is not deflated in projected years; Schedule E income is.
    df = df.with_columns(
        ar_business=pl.col("pbusinc") + pl.col("pprofinc") + pl.col("sbusinc") + pl.col("sprofinc") + pl.col("scorp"),
        # Federal Schedule E income.
        ar_schede=pl.col("otherprop") + (pl.col("scorp") if effective_year >= 1987 else 0.0),
    )
    df = deflate_for_extrapolation(df, flate, extra=("ar_schede",))

    df = df.with_columns(
        ar_taxpayers=taxpayer_count(),
        ar_sep=separate_divisor(),
        ar_low_income_eligible=pl.col("filing_status") != "married_separate",
    )

    # --- Capital gains ---
    # The federal gain in AGI; through 1990 a net loss enters in full. From
    # 1999 only part of a net long-term gain is taxed.
    fullcg = pl.col("stcg") + pl.col("ltcg")
    federal_capgn = federal_capital_gain_in_agi(effective_year, flate)
    if effective_year <= 1990:
        capgn = pl.when(federal_capgn < 0).then(fullcg).otherwise(federal_capgn)
    elif effective_year <= 1998:
        capgn = federal_capgn
    else:
        rate = 1.0 - p.num("capital_gains_exclusion")
        partial = (
            pl.when(pl.col("stcg") < 0)
            .then(rate * fullcg)
            .otherwise(rate * pl.col("ltcg") + pl.col("stcg"))
        )
        capgn = pl.when(federal_capgn > 0).then(partial).otherwise(federal_capgn)
        if p.in_effect("capital_gains_cap"):
            capgn = capgn.clip(None, p.num("capital_gains_cap"))
    df = df.with_columns(ar_capgn=capgn)

    # --- AGI ---
    # Arkansas builds income from its parts: wages, dividends, interest,
    # self-employment income, pensions, gains, the federal Schedule E figure
    # (other property income, and from 1987 S corporation income) and
    # business income including S corporation income again.
    schedule_e = pl.col("ar_schede")
    business = pl.col("ar_business")
    df = df.with_columns(
        ar_agi=pl.col("wages") + (pl.col("dividends") + dividend_input_adjustment()) + pl.col("intrec") + pl.col("ar_capgn")
        + pl.col("psemp") + pl.col("ssemp") + pl.col("pensions") + schedule_e + business
    )
    # Pension exclusion.
    if effective_year >= 1983:
        amount = p.num("pension_exclusion_amount")
        basis = p.value("pension_exclusion_basis")
        count = {"return": pl.lit(1.0), "taxpayer": pl.col("ar_taxpayers"), "aged": aged_count()}[basis]
        df = df.with_columns(ar_agi=pl.col("ar_agi") - pl.min_horizontal(pl.col("pensions"), amount * count))
    if 2018 <= effective_year <= 2019:
        df = df.with_columns(ar_agi=pl.col("ar_agi") + pl.col("ui"))
    df = df.with_columns(ar_ag=pl.col("ar_agi").clip(0, None))

    # --- Standard deduction ---
    if p.in_effect("standard_deduction_percent"):
        sd = p.value("standard_deduction_percent")
        df = df.with_columns(
            ar_stded=(float(sd["rate"]) * pl.col("ar_agi")).clip(0, float(sd["cap_per_taxpayer"]) * pl.col("ar_taxpayers") / pl.col("ar_sep"))
        )
    else:
        df = df.with_columns(ar_stded=p.num("standard_deduction") * pl.col("ar_taxpayers"))

    # --- Itemized deduction: the itemized inputs, with Arkansas's own
    # limitation 1991-2017. ---
    df = df.with_columns(ar_xitded_base=pl.col("proptax") + pl.col("otheritem") + pl.col("mortgage"))
    if 1991 <= effective_year <= 2017:
        if p.in_effect("itemized_limitation"):
            lim = p.value("itemized_limitation")
            index = float(resolve_year(STATE_ADJUSTMENT_PARAMS["itemized_phaseout_inflation"], effective_year))
            if isinstance(lim["threshold"], dict):
                df = df.with_columns(ar_phas=by_filing_status({s: float(v) * index for s, v in lim["threshold"].items()}))
            else:
                df = df.with_columns(ar_phas=float(lim["threshold"]) * index / pl.col("ar_sep"))
            df = df.with_columns(
                ar_reduce=pl.when(pl.col("ar_agi") > pl.col("ar_phas"))
                .then(pl.min_horizontal(lim["cap_rate"] * pl.col("ar_xitded_base"), float(lim["rate"]) * (pl.col("ar_agi") - pl.col("ar_phas"))))
                .otherwise(0.0)
            )
        else:
            df = df.with_columns(ar_reduce=pl.lit(0.0))
        df = df.with_columns(ar_xitded=(pl.col("ar_xitded_base") - pl.col("ar_reduce")).clip(0, None))
    else:
        df = df.with_columns(ar_xitded=pl.col("ar_xitded_base"))

    # A forced standard deduction zeroes itemized deductions.
    df = df.with_columns(ar_xitded=pl.when(forced_standard()).then(0.0).otherwise(pl.col("ar_xitded")))

    df = df.with_columns(ar_deduc=pl.max_horizontal(pl.col("ar_stded"), pl.col("ar_xitded")))
    df = df.with_columns(ar_taxinc=(pl.col("ar_agi") - pl.col("ar_deduc")).clip(0, None))

    # --- Joint returns: each earner's share of AGI and deductions. ---
    df = df.with_columns(
        ar_agi_higher_earner=higher_earner_share(pl.col("ar_agi")),
    )
    df = df.with_columns(
        ar_agi_lower_earner=pl.col("ar_agi") - pl.col("ar_agi_higher_earner"),
        ar_xitdh=pl.when(pl.col("ar_agi") != 0).then(pl.col("ar_xitded") * pl.col("ar_agi_higher_earner") / pl.col("ar_agi")).otherwise(0.0),
    )
    df = df.with_columns(ar_xitdw=pl.col("ar_xitded") - pl.col("ar_xitdh"))
    df = df.with_columns(
        ar_dedh=pl.max_horizontal(0.5 * pl.col("ar_stded"), pl.col("ar_xitdh")),
        ar_dedw=pl.max_horizontal(0.5 * pl.col("ar_stded"), pl.col("ar_xitdw")),
    )
    df = df.with_columns(
        ar_taxinh=(pl.col("ar_agi_higher_earner") - pl.col("ar_dedh")).clip(0, None),
        ar_taxinw=(pl.col("ar_agi_lower_earner") - pl.col("ar_dedw")).clip(0, None),
    )
    is_joint_split = (files_joint()) & (pl.col("ar_agi") > 0)

    # --- Tax ---
    if p.in_effect("standard_deduction_tables"):
        # Standard-deduction returns use the AGI tables; itemizers (and a
        # forced itemized deduction) use the taxable income schedule.
        prefers_itemized_or_forced = forced_itemized() | (pl.col("ar_stded") < pl.col("ar_xitded"))
        tables = p.value("standard_deduction_tables")
        brackets_std1 = _upper_bound_brackets(tables["other"])
        brackets_std2 = _upper_bound_brackets(tables["separate"])
        brackets_item = p.value("brackets")
        stat_std = pl.when(files_separate()).then(
            bracket_tax(pl.col("ar_agi"), brackets_std2)
        ).otherwise(bracket_tax(pl.col("ar_agi"), brackets_std1))
        # Joint returns pay the lesser of tax on half of AGI doubled and the
        # sum of tax on each earner's share.
        method_a = 2.0 * bracket_tax(pl.col("ar_agi") / 2.0, brackets_std1)
        agih_wage = higher_earner_share(pl.col("ar_agi"))
        agiw_wage = pl.col("ar_agi") - agih_wage
        method_b = bracket_tax(agih_wage, brackets_std1) + bracket_tax(agiw_wage, brackets_std1)
        stat_std = pl.when(files_joint()).then(
            pl.min_horizontal(method_a, method_b)
        ).otherwise(stat_std)

        stat_item = bracket_tax(pl.col("ar_taxinc"), brackets_item)
        stat_item_h = bracket_tax(pl.col("ar_taxinh"), brackets_item)
        stat_item_w = bracket_tax(pl.col("ar_taxinw"), brackets_item)
        stat_item = pl.when(is_joint_split).then(pl.min_horizontal(stat_item, stat_item_h + stat_item_w)).otherwise(stat_item)

        df = df.with_columns(ar_statax=pl.when(prefers_itemized_or_forced).then(stat_item).otherwise(stat_std))
        # The last bracket lookup: the husband's share of the standard
        # table on joint returns (the full table halves income), else the
        # wife's share on itemized joint returns.
        rate_std = (
            pl.when(files_joint()).then(bracket_rate(agih_wage, brackets_std1))
            .when(files_separate()).then(bracket_rate(pl.col("ar_agi"), brackets_std2))
            .otherwise(bracket_rate(pl.col("ar_agi"), brackets_std1))
        )
        rate_item = pl.when(is_joint_split).then(bracket_rate(pl.col("ar_taxinw"), brackets_item)).otherwise(
            bracket_rate(pl.col("ar_taxinc"), brackets_item)
        )
        rate_expr = pl.when(prefers_itemized_or_forced).then(rate_item).otherwise(rate_std)
    elif p.in_effect("brackets"):
        brackets = p.value("brackets")
        stat = bracket_tax(pl.col("ar_taxinc"), brackets)
        stat_h = bracket_tax(pl.col("ar_taxinh"), brackets)
        stat_w = bracket_tax(pl.col("ar_taxinw"), brackets)
        df = df.with_columns(ar_statax=pl.when(is_joint_split).then(pl.min_horizontal(stat, stat_h + stat_w)).otherwise(stat))
        rate_expr = pl.when(is_joint_split).then(bracket_rate(pl.col("ar_taxinw"), brackets)).otherwise(
            bracket_rate(pl.col("ar_taxinc"), brackets)
        )
    else:
        rows = [[float(t), float(r), float(s)] for t, r, s in p.value("rate_table")]
        lookup = _rate_lookup_brackets(rows)
        stat = lookup(pl.col("ar_taxinc"))
        stat_h = lookup(pl.col("ar_taxinh"))
        stat_w = lookup(pl.col("ar_taxinw"))
        df = df.with_columns(ar_statax=pl.when(is_joint_split).then(pl.min_horizontal(stat, stat_h + stat_w)).otherwise(stat))
        rate_expr = pl.lit(0.0)

    if p.in_effect("surcharge"):
        df = df.with_columns(ar_statax=pl.col("ar_statax") * (1.0 + p.num("surcharge")))

    if p.in_effect("working_taxpayer_credit"):
        wtc = p.value("working_taxpayer_credit")
        rate, cap, floor = float(wtc["rate"]), float(wtc["cap"]), float(wtc["floor"])
        # All self-employment income goes to the higher-wage spouse.
        self_employment = pl.col("psemp") + pl.col("ssemp")
        winc85 = pl.max_horizontal(pl.col("pwages"), pl.col("swages")) + self_employment
        winc86 = pl.min_horizontal(pl.col("pwages"), pl.col("swages"))
        work85 = pl.when(winc85 > floor).then(pl.min_horizontal(cap, rate * winc85)).otherwise(0.0)
        work86 = pl.when((winc86 > floor) & (pl.col("pwages") > 0) & (pl.col("swages") > 0)).then(
            pl.min_horizontal(cap, rate * winc86)
        ).otherwise(0.0)
        winc_single = pl.col("wages") + self_employment
        work_single = pl.when(winc_single > floor).then(pl.min_horizontal(cap, rate * winc_single)).otherwise(0.0)
        two_earner = (pl.col("pwages") > 0) & (pl.col("swages") > 0)
        work = pl.when(two_earner).then(work85 + work86).otherwise(work_single)
        df = df.with_columns(ar_work=work)
        df = df.with_columns(ar_statax=(pl.col("ar_statax") - pl.col("ar_work")).clip(0, None))
    else:
        df = df.with_columns(ar_work=pl.lit(0.0))

    # --- Low-income tax tables (not for separate returns) ---
    if p.in_effect("low_income_schedule"):
        depx = pl.col("depx")
        schedules = p.value("low_income_schedule")

        def override(c: dict) -> pl.Expr:
            agi = pl.col("ar_agi")
            return pl.when(agi < c["start"]).then(0.0).when(agi <= c["end"]).then(
                ((agi - c["offset"]) / schedules["step"]).floor() * c["multiplier"]
            ).otherwise(None)

        single_ov = override(schedules["single"])
        married_nodep_ov = override(schedules["married_no_dependents"])
        richer_dep01_ov = override(schedules["married_or_hoh_up_to_one_dependent"])
        richer_dep2_ov = override(schedules["married_or_hoh_two_dependents"])
        ov = pl.when((files_single()) & single_ov.is_not_null()).then(single_ov)
        ov = ov.when(
            (files_joint()) & (depx < 1) & married_nodep_ov.is_not_null()
        ).then(married_nodep_ov)
        richer = pl.col("filing_status").is_in(["married_joint", "head_of_household"])
        ov = ov.when(richer & (depx <= 1) & richer_dep01_ov.is_not_null()).then(richer_dep01_ov)
        ov = ov.when(richer & (depx >= 2) & richer_dep2_ov.is_not_null()).then(richer_dep2_ov)
        df = df.with_columns(ar_statax=pl.when(ov.is_not_null()).then(ov).otherwise(pl.col("ar_statax")))
    elif effective_year >= 2022:
        # Dated statutory tables: step lookups on net taxable income; the tax
        # is the lesser of the table and the main schedule.
        table = AR_LOW_INCOME_STEPS.filter(pl.col("year") == effective_year)
        lookup = pl.lit(None, dtype=pl.Float64)
        for (status, dep_tier), rows in table.group_by(["status", "dep_tier"], maintain_order=True):
            starts = rows["threshold"].to_list()
            amounts = [1e18 if a is None else a for a in rows["amount"].to_list()]
            dep_cond = pl.lit(True) if dep_tier == 0 else (pl.col("depx") <= 1 if dep_tier == 1 else pl.col("depx") >= 2)
            group = (pl.col("filing_status") == status) & dep_cond
            value = bracket_rate(pl.col("ar_agi"), [[a, b] for a, b in zip(starts, amounts)])
            lookup = pl.when(group).then(value).otherwise(lookup)
        df = df.with_columns(
            ar_statax=pl.when(pl.col("ar_low_income_eligible") & lookup.is_not_null())
            .then(pl.min_horizontal(pl.col("ar_statax"), lookup))
            .otherwise(pl.col("ar_statax"))
        )
    else:
        override_expr = _low_income_override(df, effective_year)
        if override_expr is not None:
            df = df.with_columns(
                ar_override=pl.when(pl.col("ar_low_income_eligible")).then(override_expr).otherwise(None)
            )
            df = df.with_columns(
                ar_statax=pl.when(pl.col("ar_override").is_not_null()).then(pl.col("ar_override")).otherwise(pl.col("ar_statax"))
            )

    if p.in_effect("capital_gains_credit"):
        cg = p.value("capital_gains_credit")
        df = df.with_columns(
            ar_statax=pl.col("ar_statax")
            - cg["rate"]
            * pl.when(pl.col("ar_taxinc") > cg["threshold"])
            .then(pl.min_horizontal(pl.col("ar_taxinc") - cg["threshold"], float(cg["maximum"]), pl.col("ar_capgn").clip(0, None)))
            .otherwise(0.0)
        )

    # --- Credits ---
    if p.in_effect("dependent_credit"):
        gcred = p.num("personal_credit") * (pl.col("ar_taxpayers") + aged_count()) + p.num("dependent_credit") * pl.col("depx")
    else:
        pcr = p.num("personal_credit")
        gcred = pcr * (pl.col("ar_taxpayers") + aged_count() + pl.col("depx"))
        gcred = gcred + pl.when(files_head_of_household()).then(pcr).otherwise(0.0)
        # Taxpayers 65 or older without pension income get one more credit.
        gcred = gcred + pl.when((pl.col("pensions") == 0) & (aged_count() > 0)).then(pcr).otherwise(0.0)
    df = df.with_columns(ar_gcred=gcred)

    extra = []
    if p.in_effect("inflationary_relief_credit"):
        relief = p.value("inflationary_relief_credit")
        def field(name: str) -> pl.Expr:
            return by_filing_status({status: terms[name] for status, terms in relief.items()})
        extra.append((field("maximum") - field("reduction") * ((pl.col("ar_agi") - field("start")).clip(0, None) / field("increment")).floor()).clip(0, None))
    if p.in_effect("qualified_individual_credit"):
        qic = p.value("qualified_individual_credit")
        people = pl.col("ar_taxpayers")
        extra.append((
            float(qic["amount"]) * people
            - float(qic["reduction"])
            * people
            * ((pl.col("ar_taxinc") - float(qic["start"])).clip(0, None) / float(qic["increment"])).ceil()
        ).clip(0, None))
    df = df.with_columns(ar_statutory_extra_credit=sum(extra) if extra else pl.lit(0.0))

    child_rate = p.num("child_care_credit_federal_share")
    child = pl.col("federal_chcr").clip(0, None)
    df = df.with_columns(ar_chcr=(child_rate * child).clip(0, None))
    if p.in_effect("child_care_credit_cap_per_dependent"):
        df = df.with_columns(ar_chcr=pl.min_horizontal(pl.col("ar_chcr"), p.num("child_care_credit_cap_per_dependent") * pl.col("depx").clip(0, 2)))

    df = df.with_columns(ar_credit=pl.col("ar_chcr") + pl.col("ar_gcred") + pl.col("ar_statutory_extra_credit"))
    df = df.with_columns(siitax=(pl.col("ar_statax") - pl.col("ar_credit")).clip(0, None) * flate)
    return with_state_detail(
        df,
        agi=pl.col("ar_agi"),
        standard_deduction=pl.col("ar_stded"),
        itemized_deductions=pl.col("ar_xitded"),
        taxable_income=pl.col("ar_taxinc"),
        child_care_credit=pl.col("ar_chcr"),
        credits=pl.col("ar_credit") + pl.col("ar_work"),
        rate=rate_expr,
    )
