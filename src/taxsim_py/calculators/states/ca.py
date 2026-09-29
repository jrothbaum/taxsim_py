"""California individual income tax calculator."""

import math

import polars as pl

from taxsim_py.calculators.payroll import payroll_parts
from taxsim_py.engine.brackets import bracket_rate, bracket_tax
from taxsim_py.engine.inputs import aged_count, files_head_of_household, files_joint, files_separate, files_single, is_dependent_filer, separate_divisor, taxpayer_count
from taxsim_py.engine.schema import PARAMETERS_ROOT, YearParams, load_yaml, resolve_year
from taxsim_py.engine.state import by_filing_status, dividend_input_adjustment, forced_standard, household_income, interpolate_table, with_state_detail
from taxsim_py.behavior import BehaviorProfile, TAXSIM_BEHAVIOR
from taxsim_py.engine.state_extrapolation import deflate_for_extrapolation, resolve_state_year

CA_PARAMS = load_yaml(PARAMETERS_ROOT / "states" / "ca" / "income_tax.yaml")
PRE1987_PARAMS = load_yaml(PARAMETERS_ROOT / "national" / "pre1987.yaml")
FEDERAL_AMT_PARAMS = load_yaml(PARAMETERS_ROOT / "national" / "amt.yaml")

_STATUSES = ["single", "married_joint", "married_separate", "head_of_household"]


def _raw_to_start_rate(raw_pairs: list[list[float]], scale_threshold: float = 1.0) -> list[list[float]]:
    """Convert upper-bound brackets to start-rate brackets."""
    out = [[0.0, raw_pairs[0][1]]]
    for i in range(1, len(raw_pairs)):
        prev_upper = raw_pairs[i - 1][0]
        lo = prev_upper * scale_threshold if prev_upper < 1.0e19 else prev_upper
        out.append([lo, raw_pairs[i][1]])
    return out


def _by_status4(vals: list[float]) -> pl.Expr:
    """vals ordered [single, married_joint, head_of_household, married_separate]
    matching the source's own `filing(mst,single,joint,hoh,sep)` helper."""
    return by_filing_status(
        {"single": vals[0], "married_joint": vals[1], "head_of_household": vals[2], "married_separate": vals[3]}
    )


def compute_ca_tax(df: pl.DataFrame, year: int, behavior: BehaviorProfile = TAXSIM_BEHAVIOR) -> pl.DataFrame:
    effective_year, flate = resolve_state_year(year)
    p = YearParams(CA_PARAMS, effective_year)

    # Self-employment tax (`comnew(175)`) and household income (`data(159)`)
    # before projected-year deflation.
    df = df.with_columns(
        ca_setax=payroll_parts(year)["setax"],
        ca_household_income=household_income(),
    )
    df = deflate_for_extrapolation(df, flate, extra=("ca_household_income",))

    df = df.with_columns(
        ca_sep=separate_divisor(),
        ca_ajnt=pl.when(files_joint()).then(2.0).otherwise(1.0),
        ca_hoh=files_head_of_household(),
        # `txp`: taxpayers, with head of household counted as 2. Used by the
        # standard deduction and the pre-1987 exemption credit.
        ca_txp=pl.when(files_head_of_household()).then(2.0).otherwise(taxpayer_count()),
        # `data(7)` itself, read by the 1987+ exemption credit.
        ca_txp_raw=taxpayer_count(),
    )

    # --- AGI ---
    aif = p.num("standard_deduction_aif")
    if effective_year <= 1986:
        excl = float(resolve_year(PRE1987_PARAMS["capital_gains_exclusion_rate"], effective_year)) if effective_year <= 1986 else 0.0
        # California's own pre-1987 inclusion of 65% of long-term gains
        # (`cg=data(68)+.65*(data(70)+...)`).
        df = df.with_columns(ca_cg=pl.col("stcg") + p["capital_gains_inclusion_pre1987"] * pl.col("ltcg"))
        # Real California law (and TAXSIM's own `catax`, taxsim.f around
        # line 2463: `if(cg.lt.0.) cg=-1.*min(abs(cg),taxy,1000.0d0/sep)`)
        # caps a net capital LOSS at $1,000 per return (halved for
        # married-separate returns) - a different, much tighter limit than
        # federal's $3,000. The port previously allowed the full
        # 65%-included loss with no cap at all (a single filer with a
        # $42,000 wage and an $8,000 long-term loss was off by $462) -
        # confirmed against the real oracle, which already applies this
        # cap, so this applies unconditionally rather than being
        # calculation_mode-gated. Real law also floors the cap further at
        # a computed pre-capital-gain income measure (`taxy`) for very
        # low-income filers - that floor is NOT replicated here (a known,
        # narrower simplification than the $1,000 cap itself; see
        # docs/pending_issues.md), so this is exact except at very low
        # incomes.
        capital_loss_cap = 1000.0 / pl.col("ca_sep")
        df = df.with_columns(
            ca_cg=pl.when(pl.col("ca_cg") < 0)
            .then(-pl.min_horizontal(pl.col("ca_cg").abs(), capital_loss_cap))
            .otherwise(pl.col("ca_cg"))
        )
        # Gross self-employment income (`data(17)`).
        df = df.with_columns(ca_gross_se=pl.col("psemp") + pl.col("ssemp"))
        df = df.with_columns(
            ca_totinc=pl.col("wages") + pl.col("intrec") + (pl.col("dividends") + dividend_input_adjustment()) + pl.col("ca_cg")
            + pl.col("ca_gross_se") + pl.col("pensions") + pl.col("otherprop") + pl.col("nonprop")
        )
        if effective_year >= 1985:
            # 1985-1986: an exclusion for taxpayers 65 or older.
            aged = aged_count()
            limit = p["aged_exclusion_income_limit_1985"] * aged / pl.col("ca_sep")
            oldex = (p["aged_exclusion_1985"] * aged - 0.5 * (pl.col("ca_totinc") - limit).clip(0, None)).clip(0, None)
            df = df.with_columns(ca_totinc=pl.col("ca_totinc") - oldex)
        # CA-001: from 1979 the adjustments subtract taxable unemployment
        # compensation (`comnew(78)`), which total income above never
        # included in the first place - so it's removed twice, not once. A
        # filer with $10,000 of wages and $4,000 of taxable unemployment
        # gets a $6,000 CA AGI instead of $10,000. Corrected by not
        # subtracting it at all (it was never meant to be in the base
        # either way), rather than adding it to `ca_totinc` above, since
        # the 1985-1986 aged exclusion is computed from that same
        # `ca_totinc` and changing its composition could shift that
        # unrelated provision too. See statutory_corrections.md.
        ui_adjustment = (
            0.0
            if behavior.include_unemployment_in_california_total_income
            else (pl.col("taxable_unemployment") if effective_year >= 1979 else 0.0)
        )
        df = df.with_columns(ca_agi=(pl.col("ca_totinc") - ui_adjustment).clip(0, None))
    else:
        # Federal AGI (`agi=comnew(2)`) less exempt unemployment
        # compensation and Social Security (`comnew(78)`, `comnew(79)`).
        df = df.with_columns(ca_subtra=pl.col("taxable_unemployment") + pl.col("taxable_social_security"))
        if effective_year in (2011, 2012):
            # 2011-2012: the federal self-employment tax deduction's
            # payroll-tax-holiday adjustment is added back.
            setax = pl.col("ca_setax")
            holiday = p["se_deduction_holiday_addback"]
            df = df.with_columns(
                # A negative subtraction: the adjustment adds to AGI.
                ca_subtra=pl.col("ca_subtra") - pl.when(setax <= holiday["threshold"]).then(
                    (holiday["rate"] - 0.5) * setax
                ).otherwise(float(holiday["flat"]))
            )
        df = df.with_columns(ca_agi=pl.col("agi") - pl.col("ca_subtra"))

    # --- Standard deduction ---
    if effective_year <= 1978:
        df = df.with_columns(ca_stded=1000.0 * pl.col("ca_txp"))
    else:
        df = df.with_columns(ca_stded=1000.0 * aif * pl.col("ca_txp"))

    # --- Itemized deduction ---
    if effective_year <= 1986:
        # `xitded=comnew(24)-data(50)*comnew(24)/comnew(30)`: the federal
        # itemized deductions (zero when not itemizing federally) less the
        # state income or sales tax deduction.
        itemizing = pl.col("pre1987_itemizes").cast(pl.Float64)
        df = df.with_columns(
            ca_xitded=itemizing * (pl.col("pre1987_deduc") - pl.col("state_sales_or_income_tax_ded"))
        )
    else:
        # `xitded=max(0,comnew(30)-data(50)+data(27))`: federal gross
        # itemized deductions less the state income or sales tax deduction.
        df = df.with_columns(
            ca_xitded_base=(pl.col("salt_capped") - pl.col("state_sales_or_income_tax_ded") + pl.col("mortgage")).clip(0, None)
        )
        base = float(p["exemption_credit_phaseout_base_1991plus"])
        phaded = pl.when(pl.col("ca_hoh")).then(base * float(p["exemption_credit_phaseout_base_hoh_multiplier"])).when(
            files_joint()
        ).then(base * float(p["exemption_credit_phaseout_base_joint_multiplier"])).otherwise(base)
        if effective_year >= 1992:
            aifded = p.num("itemized_phaseout_aifded")
            phaded = phaded * aifded
        df = df.with_columns(ca_phaded=phaded)
        if effective_year >= 1991:
            df = df.with_columns(
                ca_reduce=pl.when(pl.col("agi") > pl.col("ca_phaded"))
                .then(
                    pl.min_horizontal(
                        p["itemized_phaseout_cap_rate"] * pl.col("ca_xitded_base"),
                        p["itemized_phaseout_rate"] * (pl.col("agi") - pl.col("ca_phaded")),
                    )
                )
                .otherwise(0.0)
            )
        else:
            df = df.with_columns(ca_reduce=pl.lit(0.0))
        df = df.with_columns(ca_xitded=(pl.col("ca_xitded_base") - pl.col("ca_reduce")).clip(0, None))
        if effective_year == 1999:
            df = df.with_columns(ca_xitded=pl.when(forced_standard()).then(0.0).otherwise(pl.col("ca_xitded")))

    if effective_year <= 1986:
        # From 1982 the tax tables' zero-rate first bracket is the standard
        # deduction, so it is taken back out: `deduc=max(stded,xitded)-stded`.
        if effective_year <= 1981:
            df = df.with_columns(ca_deduc=pl.max_horizontal(pl.col("ca_stded"), pl.col("ca_xitded")))
        else:
            df = df.with_columns(
                ca_deduc=pl.when(pl.col("ca_stded") > pl.col("ca_xitded"))
                .then(0.0)
                .otherwise(pl.col("ca_xitded") - pl.col("ca_stded"))
            )
    else:
        df = df.with_columns(ca_deduc=pl.max_horizontal(pl.col("ca_stded"), pl.col("ca_xitded")))
    df = df.with_columns(ca_taxinc=(pl.col("ca_agi") - pl.col("ca_deduc")).clip(0, None))

    # --- Bracket tax --- Joint returns use the single table on half their
    # income, doubled; head of household has its own table.

    if effective_year <= 1981:
        aiftab = p.num("bracket_aiftab_pre1987")
        # `nint` rounds halves away from zero.
        raw_s = [[math.floor(lo * aiftab / 10.0 + 0.5) * 10.0, rate] for lo, rate in p["brackets_pre1982_single"]]
        raw_h = [[math.floor(lo * aiftab / 10.0 + 0.5) * 10.0, rate] for lo, rate in p["brackets_pre1982_hoh"]]
        brackets_s = _raw_to_start_rate(raw_s)
        brackets_h = _raw_to_start_rate(raw_h)
        stat_single = bracket_tax(pl.col("ca_taxinc") / pl.col("ca_ajnt"), brackets_s) * pl.col("ca_ajnt")
        stat_hoh = bracket_tax(pl.col("ca_taxinc"), brackets_h)
        df = df.with_columns(ca_regtax=pl.when(pl.col("ca_hoh")).then(stat_hoh).otherwise(stat_single))
        rate_expr = pl.when(pl.col("ca_hoh")).then(bracket_rate(pl.col("ca_taxinc"), brackets_h)).otherwise(
            bracket_rate(pl.col("ca_taxinc") / pl.col("ca_ajnt"), brackets_s)
        )
    elif effective_year <= 1986:
        aiftab = p.num("bracket_aiftab_pre1987")
        brackets_s = _raw_to_start_rate(p["brackets_1982_1986_single"], aiftab)
        brackets_h = _raw_to_start_rate(p["brackets_1982_1986_hoh"], aiftab)
        stat_single = bracket_tax(pl.col("ca_taxinc") / pl.col("ca_ajnt"), brackets_s) * pl.col("ca_ajnt")
        stat_hoh = bracket_tax(pl.col("ca_taxinc"), brackets_h)
        df = df.with_columns(ca_regtax=pl.when(pl.col("ca_hoh")).then(stat_hoh).otherwise(stat_single))
        rate_expr = pl.when(pl.col("ca_hoh")).then(bracket_rate(pl.col("ca_taxinc"), brackets_h)).otherwise(
            bracket_rate(pl.col("ca_taxinc") / pl.col("ca_ajnt"), brackets_s)
        )
    else:
        if effective_year <= 1990:
            aiftab = p.num("bracket_aiftab_1987_2012")
            brackets_s = _raw_to_start_rate(p["brackets_1987_1990_single"], aiftab)
            brackets_h = _raw_to_start_rate(p["brackets_1987_1990_hoh"], aiftab)
        elif effective_year <= 1995:
            aiftab = p.num("bracket_aiftab_1987_2012")
            brackets_s = _raw_to_start_rate(p["brackets_1991_1995_single"], aiftab)
            brackets_h = _raw_to_start_rate(p["brackets_1991_1995_hoh"], aiftab)
        elif effective_year <= 2008:
            aiftab = p.num("bracket_aiftab_1987_2012")
            brackets_s = _raw_to_start_rate(p["brackets_1996_2008_single"], aiftab)
            brackets_h = _raw_to_start_rate(p["brackets_1996_2008_hoh"], aiftab)
        elif effective_year <= 2010:
            # 2009-2010 (`tab9s`/`tab9h`): the 1996 schedule plus 0.25 points.
            aiftab = p.num("bracket_aiftab_1987_2012")
            brackets_s = _raw_to_start_rate(p["brackets_2009_2010_single"], aiftab)
            brackets_h = _raw_to_start_rate(p["brackets_2009_2010_hoh"], aiftab)
        elif effective_year <= 2011:
            aiftab = p.num("bracket_aiftab_1987_2012")
            brackets_s = _raw_to_start_rate(p["brackets_1996_2008_single"], aiftab)
            brackets_h = _raw_to_start_rate(p["brackets_1996_2008_hoh"], aiftab)
        else:
            aif12 = p.num("bracket_aif12_2012plus")
            rates = p["brackets_2012plus_rates"]
            ts = p["brackets_2012plus_single_thresholds"]
            th = p["brackets_2012plus_hoh_thresholds"]
            raw_s = list(zip(ts + [1.0e20], rates))
            raw_h = list(zip(th + [1.0e20], rates))
            brackets_s = _raw_to_start_rate(raw_s, aif12)
            brackets_h = _raw_to_start_rate(raw_h, aif12)
        stat_single = bracket_tax(pl.col("ca_taxinc") / pl.col("ca_ajnt"), brackets_s) * pl.col("ca_ajnt")
        stat_hoh = bracket_tax(pl.col("ca_taxinc"), brackets_h)
        df = df.with_columns(ca_regtax=pl.when(pl.col("ca_hoh")).then(stat_hoh).otherwise(stat_single))
        rate_expr = pl.when(pl.col("ca_hoh")).then(bracket_rate(pl.col("ca_taxinc"), brackets_h)).otherwise(
            bracket_rate(pl.col("ca_taxinc") / pl.col("ca_ajnt"), brackets_s)
        )

    df = df.with_columns(ca_statax=pl.col("ca_regtax"))

    # --- Exemption Credit ---
    # Through 1986 head of household claims one fewer dependent credit.
    dep_reduced_pre1987 = pl.when(pl.col("ca_hoh")).then((pl.col("depx") - 1).clip(0, None)).otherwise(pl.col("depx"))
    dep_reduced = dep_reduced_pre1987 if effective_year <= 1986 else pl.col("depx")
    if effective_year <= 1986:
        aif_pre87 = aif
        credit = p["exemption_credit_pre1987"]
        df = df.with_columns(
            ca_excrd=(
                pl.lit(math.floor(credit["per_taxpayer"] * aif_pre87 + 0.5)) * pl.col("ca_txp")
                + pl.lit(math.floor(credit["per_dependent"] * aif_pre87 + 0.5)) * dep_reduced
            )
        )
        if effective_year == 1978:
            credit_1978 = p["exemption_credit_1978"]
            df = df.with_columns(
                ca_excrd=float(credit_1978["per_taxpayer"]) * pl.col("ca_txp") + float(credit_1978["per_dependent"]) * dep_reduced
            )
    elif effective_year <= 1997:
        xmpaif = p.num("exemption_credit_xmpaif")
        df = df.with_columns(ca_excrd=p["exemption_credit_base_1987plus"] * xmpaif * (pl.col("ca_txp_raw") + aged_count() + dep_reduced))
    else:
        xmpaif = p.num("exemption_credit_xmpaif")
        xmpdep = p.num("exemption_credit_per_dependent_1998plus")
        df = df.with_columns(ca_excrd=p["exemption_credit_base_1987plus"] * xmpaif * (pl.col("ca_txp_raw") + aged_count()) + dep_reduced * xmpdep)

    if effective_year >= 1991:
        base = float(p["exemption_credit_phaseout_base_1991plus"])
        aifded = p.num("itemized_phaseout_aifded") if effective_year >= 1992 else 1.0
        phaded = pl.when(pl.col("ca_hoh")).then(base * float(p["exemption_credit_phaseout_base_hoh_multiplier"])).when(
            files_joint()
        ).then(base * float(p["exemption_credit_phaseout_base_joint_multiplier"])).otherwise(base)
        phaded = phaded * aifded
        df = df.with_columns(ca_phaded2=phaded)
        aif2 = p.num("exemption_credit_aif2_pre1998") if effective_year <= 1998 else None
        per_credit = float(p["exemption_credit_phaseout_per_credit"])
        step = float(p["exemption_credit_phaseout_step"])
        if effective_year < 1998:
            excess_over = (pl.col("agi") - pl.col("ca_phaded2")).clip(0, None)
            reduction_full = (dep_reduced + pl.col("ca_txp") + aged_count()) * per_credit * (
                pl.col("agi") - pl.col("ca_phaded2")
            ) / step / pl.col("ca_sep")
            upper = p["exemption_credit_phaseout_upper_pre1998"]
            excrd_over_agi = pl.when(excess_over > upper * aif2 / pl.col("ca_sep")).then(0.0).otherwise(
                (pl.col("ca_excrd") - reduction_full).clip(0, None)
            )
        else:
            num = 1.0 + ((pl.col("agi") - pl.col("ca_phaded2")) / (step / pl.col("ca_sep"))).floor()
            excrd_over_agi = (pl.col("ca_excrd") - (dep_reduced + pl.col("ca_txp") + aged_count()) * per_credit * num).clip(0, None)

        df = df.with_columns(
            ca_excrd=pl.when(pl.col("agi") > pl.col("ca_phaded2")).then(excrd_over_agi).otherwise(pl.col("ca_excrd"))
        )

        if 1994 <= effective_year <= 1998:
            aif1 = p.num("exemption_credit_aif1_1994_1998") if "exemption_credit_aif1_1994_1998" in p else None
            ak = p.num("exemption_credit_ak_1994_1998") if "exemption_credit_ak_1994_1998" in p else None
            if aif1 is not None and ak is not None:
                lim = p["exemption_credit_limit_1994_1998"]
                exc = pl.when(pl.col("filing_status").is_in(["single", "head_of_household"])).then(
                    lim["floor_single_or_hoh"] * aif1
                ).otherwise(lim["floor_other"] * aif1 * pl.col("ca_sep"))
                excess_thr = pl.when(pl.col("ca_hoh")).then(lim["upper_hoh"] * aif2).otherwise(
                    lim["upper_other"] * aif2 * pl.col("ca_sep")
                )
                under_std = pl.col("ca_stded") > pl.col("ca_xitded")
                alt_a = pl.col("ca_statax") - ak * (pl.col("agi") - exc).clip(None, 0)
                alt_b = pl.col("ca_statax") - ak * (
                    pl.min_horizontal(lim["agi_share"] * pl.col("agi").clip(0, None), pl.lit(0.0))
                    + pl.col("otheritem") + pl.col("mortgage") + pl.col("ca_taxinc") - exc
                )
                capped = pl.when(under_std).then(alt_a).otherwise(alt_b)
                gate = (pl.col("ca_agi") > pl.col("ca_phaded2")) & (pl.col("ca_agi") < excess_thr)
                df = df.with_columns(
                    ca_excrd=pl.when(gate).then(pl.min_horizontal(capped, pl.col("ca_excrd"))).otherwise(pl.col("ca_excrd"))
                )

    # Dependent filers get no exemption credit.
    df = df.with_columns(ca_excrd=pl.when(is_dependent_filer()).then(0.0).otherwise(pl.col("ca_excrd")))
    df = df.with_columns(ca_noncr=pl.col("ca_excrd"))

    # --- Low Income Credit (expired after 1991) ---
    if effective_year <= 1983:
        threshold = float(p["low_income_credit_threshold_multiplier_pre1984"])
        # `40*xif(law.ge.1979, aif)`: the base is $0 before 1979, so the
        # credit is only half of how far income falls short of $5,000.
        low = p["low_income_credit_pre1984"]
        base_amt = low["base"] * aif if effective_year >= 1979 else 0.0
        hy = pl.col("ca_household_income")
        df = df.with_columns(
            ca_lowcr=pl.when(hy <= threshold * pl.col("ca_txp"))
            .then((base_amt - (pl.col("ca_agi") - low["agi_floor"]) * low["reduction_rate"]).clip(0, None).round(mode="half_away_from_zero"))
            .otherwise(0.0)
        )
    elif 1985 <= effective_year <= 1991:
        aiflow = p.num("low_income_credit_aiflow")
        # `div=2` for single and separate returns only.
        div = pl.when(pl.col("filing_status").is_in(["single", "married_separate"])).then(2.0).otherwise(1.0)
        rows = [[lo * aiflow / div, rate] for lo, rate in p["low_income_table_1985_1991"]]
        expr = interpolate_table(pl.col("ca_agi"), rows)
        df = df.with_columns(ca_lowcr=pl.col("ca_statax") * expr)
    else:
        df = df.with_columns(ca_lowcr=pl.lit(0.0))
    df = df.with_columns(ca_lowcr=pl.when(is_dependent_filer()).then(0.0).otherwise(pl.col("ca_lowcr")))
    df = df.with_columns(ca_noncr=pl.col("ca_noncr") + pl.col("ca_lowcr"))

    # --- Child/Dependent Care Credit ---
    # Federal credit as TAXSIM reports it to states (`comnew(53)`).
    child_fed = pl.col("federal_chcr")
    if effective_year <= 1984:
        cap_per = float(p["child_care_credit_cap_per_child_pre1985"])
        cap_tot = float(p["child_care_credit_cap_total_pre1985"])
        rate = float(p["child_care_credit_rate_pre1985"])
        chmax = pl.min_horizontal(cap_per * pl.col("depx"), cap_tot)
        chwage = pl.max_horizontal(pl.col("wages"), 0.0)
        df = df.with_columns(ca_chcr=rate * pl.min_horizontal(chmax, chwage, pl.col("childcare")))
    elif effective_year <= 1986:
        low = float(p["child_care_credit_rate_1985_1986_low"])
        high = float(p["child_care_credit_rate_1985_1986_high"])
        thr = float(p["child_care_credit_1985_1986_agi_threshold"])
        df = df.with_columns(ca_chcr=pl.when(pl.col("ca_agi") >= thr).then(high * child_fed).otherwise(low * child_fed))
    elif effective_year <= 1990:
        df = df.with_columns(ca_chcr=float(p["child_care_credit_rate_1987_1990"]) * child_fed)
    elif effective_year <= 1992:
        rows = p["child_care_credit_1991_1992_brackets"]
        expr = pl.lit(rows[-1][1])
        for upper, rate in reversed(rows[:-1]):
            expr = pl.when(pl.col("agi") <= upper).then(pl.lit(rate)).otherwise(expr)
        df = df.with_columns(ca_chcr=expr * child_fed)
    elif effective_year <= 1999:
        df = df.with_columns(ca_chcr=pl.lit(0.0))
    else:
        rows = p["child_care_credit_2000_2002_brackets"] if effective_year <= 2002 else p["child_care_credit_2003plus_brackets"]
        expr = pl.lit(0.0)
        for upper, rate in reversed(rows):
            expr = pl.when(pl.col("agi") <= upper).then(pl.lit(rate)).otherwise(expr)
        # Federal credit before its liability limit (`comnew(176)`), read undeflated.
        chcrbc = pl.col("ccc_uncapped")
        df = df.with_columns(ca_chcr=expr * chcrbc)

    # --- Credit for the Elderly: limited by `data(32)`, which TAXSIM never sets ---
    df = df.with_columns(ca_eld=pl.lit(0.0))

    df = df.with_columns(ca_noncr=pl.col("ca_noncr") + pl.col("ca_eld"))
    df = df.with_columns(ca_statax=(pl.col("ca_statax") - pl.col("ca_noncr")).clip(0, None))

    # --- Minimum Tax on Preference Income (1979-1986) / AMT (1987+) ---
    if 1979 <= effective_year <= 1986:
        df = df.with_columns(ca_amt=pl.lit(0.0))
    elif effective_year >= 1987:
        # Itemizers add back property and other taxes; others add back the
        # standard deduction.
        itemizing = pl.col("ca_xitded") > pl.col("ca_stded")
        addprf = pl.when(itemizing).then(pl.col("proptax") + pl.col("otheritem")).otherwise(pl.col("ca_stded"))
        # `alminy=totprf+taxinc-(max(0,data17)+max(0,data21)+max(0,comnew8)+reduce)`;
        # `data(21)` is never set and `comnew(8)` is Schedule E income.
        #
        # CA-002: real law (Schedule P) keeps self-employment and Schedule E
        # rental/S-corp income in the minimum-tax base; TAXSIM's own
        # `alminy` formula excludes both (positive amounts only). A couple
        # with $83,000 of pensions, $1,000 of rent and $99,997 of property
        # tax sees each dollar of rent lower their CA tax by 7 cents under
        # TAXSIM's formula. Corrected by not subtracting these two terms.
        # See statutory_corrections.md.
        reduce_col = pl.col("ca_reduce")
        if behavior.include_business_and_rental_income_in_california_minimum_tax:
            alminy = (addprf + pl.col("ca_taxinc") - reduce_col).clip(0, None)
        else:
            gross_se = pl.col("psemp").clip(0, None) + pl.col("ssemp").clip(0, None)
            schedule_e = (pl.col("otherprop") + pl.col("scorp")).clip(0, None)
            alminy = (addprf + pl.col("ca_taxinc") - gross_se - schedule_e - reduce_col).clip(0, None)
        if effective_year <= 1997:
            excl_by_status = {
                s: float(resolve_year(FEDERAL_AMT_PARAMS["exemption"][s], effective_year)) for s in _STATUSES
            }
            excl = by_filing_status(excl_by_status)
        else:
            vals = p["amt_exclusion_by_year"][effective_year]
            excl = _by_status4([float(v) for v in vals])
        if effective_year == 1987:
            phase_vals = p["amt_phase_by_year"][1987]
        else:
            phase_vals = p["amt_phase_by_year"].get(effective_year, p["amt_phase_by_year"][1987])
        phase = _by_status4([float(v) for v in phase_vals])
        phaout = p["amt_exclusion_phaseout_rate"] * (alminy - phase).clip(0, None)
        exclnt = (excl - phaout).clip(0, None)
        alminc = (alminy - exclnt).clip(0, None)
        amt_rate = p.num("amt_rate_by_year")
        df = df.with_columns(ca_amt=(alminc * amt_rate - pl.col("ca_statax")).clip(0, None))
    else:
        df = df.with_columns(ca_amt=pl.lit(0.0))

    df = df.with_columns(ca_statax=pl.col("ca_statax") + pl.col("ca_amt"))

    # --- Renter's credit, for renters paying no property tax ---
    renter = (pl.col("proptax") < 1) & (pl.col("rentpaid") > 0)
    single = files_single()
    sep = files_separate()
    if effective_year <= 1992:
        single_amount = p.num("renter_credit_single")
        other_amount = p.num("renter_credit_other")
    if effective_year <= 1978:
        rcred = pl.lit(single_amount)
    elif effective_year <= 1990:
        rcred = pl.when(single).then(single_amount).otherwise(other_amount / pl.col("ca_sep"))
    elif effective_year <= 1992:
        index = p.num("renter_credit_1991_inflation")
        use_single = single | sep
        limit = pl.when(use_single).then(p["renter_credit_1991_agi_limit"]["single"]).otherwise(
            p["renter_credit_1991_agi_limit"]["other"]
        ) * index
        band = pl.when(use_single).then(p["renter_credit_1991_half_band"]["single"]).otherwise(
            p["renter_credit_1991_half_band"]["other"]
        ) * index
        amount = pl.when(use_single).then(single_amount).otherwise(other_amount)
        rcred = (
            pl.when(pl.col("ca_agi") <= limit).then(amount)
            .when(pl.col("ca_agi") <= limit + band).then(0.5 * amount)
            .otherwise(0.0)
        )
    elif effective_year >= 1998:
        index = p.num("renter_credit_1998_inflation")
        units = pl.when(single | sep).then(1.0).otherwise(2.0)
        rcred = pl.when(pl.col("ca_agi") <= p["renter_credit_1998_agi_limit"] * units * index).then(
            p["renter_credit_1998_amount"] * units
        ).otherwise(0.0)
    else:
        rcred = pl.lit(0.0)
    rcred = pl.when(renter).then(rcred).otherwise(0.0)
    df = df.with_columns(ca_rcred=rcred)
    df = df.with_columns(ca_statax=(pl.col("ca_statax") - rcred).clip(0, None))

    # --- Child care credit subtraction (2011+ clamped at 0) ---
    if effective_year <= 2010:
        df = df.with_columns(ca_statax=pl.col("ca_statax") - pl.col("ca_chcr"))
    else:
        df = df.with_columns(ca_statax=(pl.col("ca_statax") - pl.col("ca_chcr")).clip(0, None))

    # --- Mental Health Services Tax (2005+) ---
    if effective_year >= 2005:
        rate = float(p["mental_health_surtax_rate"])
        threshold = float(p["mental_health_surtax_threshold"])
        df = df.with_columns(ca_statax=pl.col("ca_statax") + rate * (pl.col("ca_taxinc") - threshold).clip(0, None))

    # --- California EITC + Young Child Tax Credit ---
    if effective_year >= 2015:
        dylim = p.num("eitc_disqualified_income_limit")
        ieic = pl.col("dep18").clip(0, 3).cast(pl.Int64)
        earned = pl.col("wages") if effective_year <= 2016 else pl.col("earned_income")
        crmax_vals = p["eitc_max_credit_by_children"][effective_year]
        amax_vals = p["eitc_max_earned_by_children"][effective_year]
        cr = pl.lit(0.0)
        am = pl.lit(0.0)
        for i, (c, a) in enumerate(zip(crmax_vals, amax_vals)):
            cr = pl.when(ieic == i).then(float(c)).otherwise(cr)
            am = pl.when(ieic == i).then(float(a)).otherwise(am)
        posagi = pl.col("agi").clip(0, None)
        base1 = pl.when(earned < 0.5 * am).then(earned).otherwise((am - earned).clip(0, None))
        earncr = pl.when(earned > 0).then(base1 * cr / (0.5 * am)).otherwise(0.0)
        base_agi = (am - posagi).clip(0, None)
        earncr = pl.when(posagi >= 0.5 * am).then(pl.min_horizontal(earncr, base_agi * cr / (0.5 * am))).otherwise(earncr)
        if effective_year >= 2017:
            ym_vals = p["eitc_2017plus_ym"][effective_year]
            em_vals = p["eitc_2017plus_em"][effective_year]
            amax17_vals = p["eitc_2017plus_amax"][effective_year]
            ym1 = pl.lit(0.0)
            em1 = pl.lit(0.0)
            amax17_c = pl.lit(0.0)
            for i, (y, e, a17) in enumerate(zip(ym_vals, em_vals, amax17_vals)):
                ym1 = pl.when(ieic == i).then(float(y)).otherwise(ym1)
                em1 = pl.when(ieic == i).then(float(e)).otherwise(em1)
                amax17_c = pl.when(ieic == i).then(float(a17)).otherwise(amax17_c)
            tgbeta = em1 / (amax17_c - ym1)
            base2 = (amax17_c - pl.max_horizontal(posagi, earned)).clip(0, None)
            earncr2 = pl.when(earned > 0).then(base2 * tgbeta).otherwise(pl.lit(0.0))
            earncr = pl.when((earned > ym1) | (posagi > ym1)).then(earncr2).otherwise(earncr)
        disqy = (
            (pl.col("stcg") + pl.col("ltcg")).clip(0, None) + pl.col("dividends") + dividend_input_adjustment() + pl.col("intrec")
            + pl.col("otherprop").clip(0, None)
        )
        earncr = pl.when(disqy >= dylim).then(0.0).otherwise(earncr)
        earncr = pl.when((pl.col("ca_sep") == 2) | is_dependent_filer()).then(0.0).otherwise(earncr)
        df = df.with_columns(ca_earncr=earncr)
        # Young Child Tax Credit, 2019+ (taxsim_2024_09_21.f:2911-2919):
        # requires a state EITC and an EITC-qualifying child (`data(203)`,
        # `dep18`); the earnings test uses children under 6 (`data(210)`, `dep6`).
        if effective_year >= 2019:
            young_gate = (pl.col("ca_earncr") > 0) & (pl.col("dep18") > 0)
            yc = p["young_child_credit"]
            young_low = yc["amount"] * pl.col("dep6")
            young_high = (yc["amount"] - yc["phaseout_rate"] * (earned - yc["earnings_threshold"])).clip(0, None)
            young = pl.when(earned <= yc["earnings_threshold"]).then(young_low).otherwise(young_high)
            df = df.with_columns(ca_young=pl.when(young_gate).then(young).otherwise(0.0))
        else:
            df = df.with_columns(ca_young=pl.lit(0.0))
    else:
        df = df.with_columns(
            ca_earncr=pl.lit(0.0),
            ca_young=pl.lit(0.0),
        )

    df = df.with_columns(siitax=(pl.col("ca_statax") - pl.col("ca_earncr") - pl.col("ca_young")) * flate)
    return with_state_detail(
        df,
        agi=pl.col("ca_agi"),
        standard_deduction=pl.col("ca_stded"),
        itemized_deductions=pl.col("ca_xitded"),
        taxable_income=pl.col("ca_taxinc"),
        child_care_credit=pl.col("ca_chcr"),
        eic=pl.col("ca_earncr"),
        credits=pl.col("ca_noncr") + pl.col("ca_rcred") + pl.col("ca_chcr") + pl.col("ca_earncr") + pl.col("ca_young"),
        rate=rate_expr,
    )
