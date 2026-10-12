"""Hawaii individual income tax calculator."""


from __future__ import annotations
import polars as pl

from taxsim_py.engine.brackets import bracket_rate, bracket_tax
from taxsim_py.engine.inputs import aged_count, files_head_of_household, files_joint, files_separate, files_single, is_dependent_filer, separate_divisor, taxpayer_count
from taxsim_py.engine.schema import PARAMETERS_ROOT, YearParams, load_yaml
from taxsim_py.engine.state import federal_capital_gain_in_agi, forced_standard, interpolate_table, unemployment_total, with_state_detail
from taxsim_py.behavior import BehaviorProfile, TAXSIM_BEHAVIOR
from taxsim_py.engine.state_extrapolation import deflate_for_extrapolation, resolve_state_year

HI_PARAMS = load_yaml(PARAMETERS_ROOT / "states" / "hi" / "income_tax.yaml")

def compute_hi_tax(df: pl.DataFrame, year: int, behavior: BehaviorProfile = TAXSIM_BEHAVIOR) -> pl.DataFrame:
    effective_year, flate = resolve_state_year(year, behavior, HI_PARAMS)
    p = YearParams(HI_PARAMS, effective_year)

    df = df.with_columns(
        hi_sep=separate_divisor(),
        hi_taxpayers=taxpayer_count(),
    )

    df = deflate_for_extrapolation(df, flate)

    # --- AGI ---
    # Social Security benefits are exempt.
    df = df.with_columns(hi_agi=pl.col("agi") - pl.col("taxable_social_security"))
    # 2020/2021: unemployment compensation federal excluded is taxable.
    if effective_year in (2020, 2021):
        ui_total = unemployment_total()
        df = df.with_columns(
            hi_agi=pl.col("hi_agi") + ui_total - pl.col("taxable_unemployment")
        )
    # HI-003: TAXSIM's pension subtraction reads an input slot that is never
    # set. Hawaii exempts pensions; `pensions` is treated as the exempt kind.
    if behavior.exclude_hawaii_pensions:
        df = df.with_columns(hi_agi=pl.col("hi_agi") - pl.col("pensions").clip(0, None))

    # --- Exemptions ---
    xmp = p.num("personal_exemption")
    df = df.with_columns(hi_exemp=(pl.col("hi_taxpayers") + pl.col("depx") + aged_count()) * xmp)
    df = df.with_columns(hi_exema=pl.col("hi_exemp"))
    if p.in_effect("personal_exemption_phaseout"):
        ph = p.value("personal_exemption_phaseout")
        base, step = float(ph["base"]), float(ph["step"])
        phex = (
            pl.when(files_single()).then(base)
            .when(files_head_of_household()).then(ph["multiplier_hoh"] * base)
            .otherwise(ph["multiplier_married"] * base / pl.col("hi_sep"))
        )
        ln6 = (1.0 + (pl.col("hi_agi") - phex) / (step / pl.col("hi_sep"))).floor()
        phased = pl.col("hi_exema") - pl.col("hi_exema") * ph["share_per_step"] * ln6
        df = df.with_columns(hi_exemp=pl.when(pl.col("hi_agi") > phex).then(phased).otherwise(pl.col("hi_exemp")))
    # Dependent filers claim no exemptions (the disability exemption needs
    # an input TAXSIM does not take).
    df = df.with_columns(hi_exemp=pl.when(is_dependent_filer()).then(0.0).otherwise(pl.col("hi_exemp")))

    # --- Standard deduction ---
    stded_single = p.num("standard_deduction", "single")
    stded_joint = p.num("standard_deduction", "married_joint")
    stded_sep = p.num("standard_deduction", "married_separate")
    stded_hoh = p.num("standard_deduction", "head_of_household")
    df = df.with_columns(
        hi_stded=pl.when(files_single()).then(stded_single)
        .when(files_joint()).then(stded_joint)
        .when(files_separate()).then(stded_sep)
        .otherwise(stded_hoh)
    )
    if p.in_effect("standard_deduction_agi_share"):
        df = df.with_columns(hi_stded=pl.min_horizontal((p.num("standard_deduction_agi_share") * pl.col("hi_agi")).clip(0, None), pl.col("hi_stded")))

    # --- Itemized deduction --- Before 1987 rebuilt from the inputs.
    if effective_year <= 1986:
        df = df.with_columns(
            hi_raw_itemized=pl.col("proptax") + pl.col("otheritem") + pl.col("mortgage")
            + pl.col("state_sales_or_income_tax_ded")
        )
        df = df.with_columns(hi_xitded=pl.col("hi_raw_itemized"))
    else:
        df = df.with_columns(hi_raw_itemized=pl.col("salt_capped") + pl.col("mortgage"))
        df = df.with_columns(hi_xitded=pl.col("hi_raw_itemized"))

    if p.in_effect("state_tax_deduction_agi_limit"):
        limits = p.value("state_tax_deduction_agi_limit")
        salt_thr = (
            pl.when(files_head_of_household())
            .then(float(limits["head_of_household"]))
            .when((files_single()) | (pl.col("hi_sep") == 2))
            .then(float(limits["single_or_separate"]))
            .otherwise(float(limits["married_joint"]))
        )
        sttax = pl.col("state_sales_or_income_tax_ded")
        df = df.with_columns(
            hi_xitded=pl.when(pl.col("agi") > salt_thr).then((pl.col("hi_xitded") - sttax).clip(0, None)).otherwise(pl.col("hi_xitded"))
        )

    if p.in_effect("itemized_limitation"):
        lim = p.value("itemized_limitation")
        threshold = float(lim["threshold"]) / pl.col("hi_sep")
        reduce = pl.when(pl.col("hi_agi") > threshold).then(
            pl.min_horizontal(
                p["itemized_phaseout_cap_rate"] * pl.col("hi_xitded"),
                p["itemized_phaseout_rate"] * (pl.col("hi_agi") - threshold),
            )
        ).otherwise(0.0)
        df = df.with_columns(hi_xitded=pl.col("hi_xitded") - reduce * float(lim["reduction_share"]))

    if p.in_effect("itemized_cap"):
        ic = p.value("itemized_cap")
        phas92_hoh = float(ic["threshold_hoh"])
        phas92_other = float(ic["threshold_per_taxpayer"]) * pl.col("hi_taxpayers")
        phas92 = pl.when(files_head_of_household()).then(phas92_hoh).otherwise(phas92_other)
        cap_hoh = float(ic["amount_hoh"])
        cap_other = float(ic["amount_per_taxpayer"]) * pl.col("hi_taxpayers")
        over_thr = pl.col("agi") > phas92
        df = df.with_columns(
            hi_xitded=pl.when(over_thr & (files_head_of_household()))
            .then(pl.min_horizontal(cap_hoh, pl.col("hi_xitded")))
            .when(over_thr)
            .then(pl.min_horizontal(cap_other, pl.col("hi_xitded")))
            .otherwise(pl.col("hi_xitded"))
        )

    if 1982 <= effective_year <= 1986:
        std_era = pl.when(files_single()).then(stded_single).when(
            files_joint()
        ).then(stded_joint).when(files_separate()).then(stded_sep).otherwise(stded_hoh)
        df = df.with_columns(
            hi_xitded=(pl.col("hi_raw_itemized") - std_era).clip(0, None),
            hi_stded=(pl.col("hi_stded") - std_era).clip(0, None),
        )

    if effective_year == 1999:
        df = df.with_columns(hi_xitded=pl.when(forced_standard()).then(0.0).otherwise(pl.col("hi_xitded")))

    df = df.with_columns(hi_deduc=pl.max_horizontal(pl.col("hi_xitded"), pl.col("hi_stded")))

    df = df.with_columns(hi_taxinc=(pl.col("hi_agi") - pl.col("hi_exemp") - pl.col("hi_deduc")).clip(0, None))

    # --- Tax --- Heads of household have their own schedule; joint returns
    # use the single schedule on half their income, doubled.
    brackets = p.value("brackets")
    is_hoh = files_head_of_household()
    is_joint = files_joint()

    def _bracket_stat(taxinc_col: str) -> pl.Expr:
        brackets_single = brackets["single"]
        brackets_hoh = brackets["head_of_household"]
        taxy = pl.when(is_joint).then(pl.col(taxinc_col) / 2).otherwise(pl.col(taxinc_col))
        stat_single = bracket_tax(taxy, brackets_single)
        stat_single = pl.when(is_joint).then(stat_single * 2).otherwise(stat_single)
        stat_hoh = bracket_tax(pl.col(taxinc_col), brackets_hoh)
        return pl.when(is_hoh).then(stat_hoh).otherwise(stat_single)

    def _bracket_rate(taxinc_col: str) -> pl.Expr:
        brackets_single = brackets["single"]
        brackets_hoh = brackets["head_of_household"]
        taxy = pl.when(is_joint).then(pl.col(taxinc_col) / 2).otherwise(pl.col(taxinc_col))
        return pl.when(is_hoh).then(
            bracket_rate(pl.col(taxinc_col), brackets_hoh)
        ).otherwise(bracket_rate(taxy, brackets_single))

    df = df.with_columns(hi_statax=_bracket_stat("hi_taxinc"))
    rate_expr = _bracket_rate("hi_taxinc")

    # --- Capital gains alternative tax, on capital gains in federal AGI
    # (net of the federal long-term exclusion through 1986) ---
    capgn = pl.col("pre1987_capgn") if effective_year <= 1986 else federal_capital_gain_in_agi(year, flate)
    has_gain = capgn.clip(0, None) > 0
    brack = pl.when(is_hoh).then(float(p["capital_gains_floor_hoh"])).otherwise(
        p["capital_gains_floor_per_taxpayer"] * pl.col("hi_taxpayers")
    )
    taxyng = (pl.col("hi_taxinc") - capgn.clip(0, None)).clip(0, None)
    taxyng = pl.max_horizontal(brack, taxyng)
    taxycg = (pl.col("hi_taxinc") - taxyng).clip(0, None)
    df = df.with_columns(hi_taxyng=taxyng, hi_taxycg=taxycg)
    statng = _bracket_stat("hi_taxyng")
    statcg = pl.col("hi_taxycg") * p["capital_gains_rate"]
    df = df.with_columns(
        hi_statax=pl.when(has_gain).then(pl.min_horizontal(pl.col("hi_statax"), statng + statcg)).otherwise(pl.col("hi_statax"))
    )
    rate_expr = pl.when(has_gain).then(_bracket_rate("hi_taxyng")).otherwise(rate_expr)

    # --- Credits ---
    # Child/Dependent Care Credit.
    cap_per_dep = p.num("child_care_credit_cap_per_dependent")
    max_deps = float(p["child_care_credit_max_dependents"])
    child = pl.min_horizontal(pl.col("childcare"), cap_per_dep * pl.min_horizontal(pl.col("depx"), max_deps))
    child = pl.when(is_joint).then(pl.min_horizontal(child, pl.col("pwages"), pl.col("swages"))).otherwise(child)
    c = p.value("child_care_credit_rate")
    chr_rate = 0.01 * pl.max_horizontal(
        float(c["min_pct"]), c["max_pct"] - pl.max_horizontal((pl.col("hi_agi") - c["start"]) / c["step"], 0.0)
    )
    df = df.with_columns(hi_chcr=chr_rate * child)

    # General income tax credit.
    if p.in_effect("general_credit_phaseout"):
        def general_credit(c: dict) -> pl.Expr:
            return pl.when(pl.col("agi") < c["limit"]).then(c["max"] - c["slope"] * pl.col("agi") / c["limit"]).otherwise(0.0)

        g = p.value("general_credit_phaseout")
        df = df.with_columns(
            hi_gencr=pl.when(is_hoh).then(general_credit(g["head_of_household"]))
            .when(is_joint).then(general_credit(g["married_joint"]))
            .otherwise(general_credit(g["other"]))
        )
    else:
        df = df.with_columns(hi_gencr=p.num("general_credit_per_exemption") * (pl.col("hi_taxpayers") + pl.col("depx")))

    # Renter's Credit.
    aged_factor = aged_count().clip(1, None)
    rent_limit = p.num("renter_credit_agi_limit")
    rcred = pl.when((pl.col("rentpaid") >= p["renter_credit_min_rent"]) & (pl.col("hi_agi") < rent_limit)).then(
        p.num("renter_credit_per_exemption") * (pl.col("hi_taxpayers") + pl.col("depx") + aged_count())
    ).otherwise(0.0)
    df = df.with_columns(hi_rcred=rcred * aged_factor)

    # Excise tax credit through 1994; refundable food/excise tax credit from 2008.
    if p.in_effect("excise_credit"):
        df = df.with_columns(hi_exc=interpolate_table(pl.col("hi_agi"), p.value("excise_credit")) * pl.col("hi_taxpayers") * aged_factor)
    elif p.in_effect("food_excise_credit"):
        table = p.value("food_excise_credit")
        fedagi = pl.col("agi").clip(0, None)
        exc_non_single = (pl.col("depx") + pl.col("hi_taxpayers")) * interpolate_table(fedagi, table["other"])
        exc_single = (pl.col("depx") + pl.col("hi_taxpayers")) * interpolate_table(fedagi, table["single"])
        df = df.with_columns(hi_exc=pl.when(files_single()).then(exc_single).otherwise(exc_non_single))
    else:
        df = df.with_columns(hi_exc=pl.lit(0.0))

    if p.in_effect("food_credit"):
        df = df.with_columns(hi_foodcr=p.num("food_credit") * (pl.col("hi_taxpayers") + pl.col("depx")))
    else:
        df = df.with_columns(hi_foodcr=pl.lit(0.0))

    if p.in_effect("low_income_credit"):
        lic = p.value("low_income_credit")
        (t1, t2, t3), (c1, c2, c3) = ([float(v) for v in lic["amounts"]], [float(v) for v in lic["ceilings"]])
        units = pl.col("hi_taxpayers") + pl.col("depx")
        df = df.with_columns(
            hi_lowcr=pl.when(pl.col("hi_agi") < c1).then(units * t1)
            .when(pl.col("hi_agi") < c2).then(units * t2)
            .when(pl.col("hi_agi") <= c3).then(units * t3)
            .otherwise(0.0)
        )
    else:
        df = df.with_columns(hi_lowcr=pl.lit(0.0))
    # Dependent filers get no excise or food credit.
    df = df.with_columns(
        hi_exc=pl.when(is_dependent_filer()).then(0.0).otherwise(pl.col("hi_exc")),
        hi_foodcr=pl.when(is_dependent_filer()).then(0.0).otherwise(pl.col("hi_foodcr")),
    )
    df = df.with_columns(
        hi_credit=pl.col("hi_gencr") + pl.col("hi_rcred") + pl.col("hi_exc") + pl.col("hi_foodcr")
        + pl.col("hi_chcr") + pl.col("hi_lowcr")
    )
    df = df.with_columns(hi_statax=pl.col("hi_statax") - pl.col("hi_credit"))

    # --- Earned income credit ---
    if p.in_effect("eitc_federal_share"):
        raw = p.num("eitc_federal_share") * pl.col("eitc").clip(0, None)
        if p.value("eitc_refundable"):
            df = df.with_columns(hi_earncr=raw)
        else:
            df = df.with_columns(hi_earncr=pl.min_horizontal(raw, pl.col("hi_statax").clip(0, None)))
    else:
        df = df.with_columns(hi_earncr=pl.lit(0.0))
    df = df.with_columns(hi_statax=pl.col("hi_statax") - pl.col("hi_earncr"))

    df = df.with_columns(siitax=pl.col("hi_statax") * flate)
    return with_state_detail(
        df,
        agi=pl.col("hi_agi"),
        exemptions=pl.col("hi_exemp"),
        standard_deduction=pl.col("hi_stded"),
        itemized_deductions=pl.col("hi_xitded"),
        taxable_income=pl.col("hi_taxinc"),
        child_care_credit=pl.col("hi_chcr"),
        eic=pl.col("hi_earncr"),
        credits=pl.col("hi_credit") + pl.col("hi_earncr"),
        rate=rate_expr,
    )
