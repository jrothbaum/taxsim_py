"""California individual income tax calculator."""

import polars as pl

from taxsim_py.engine.brackets import bracket_tax
from taxsim_py.engine.schema import PARAMETERS_ROOT, load_yaml, resolve_year
from taxsim_py.engine.state import with_defaults, by_filing_status as _by_status, with_default as _with_default, forced_standard
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
    return _by_status(
        {"single": vals[0], "married_joint": vals[1], "head_of_household": vals[2], "married_separate": vals[3]}
    )


def compute_ca_tax(df: pl.DataFrame, year: int) -> pl.DataFrame:
    effective_year, flate = resolve_state_year(year)
    p = CA_PARAMS
    df = with_defaults(df, (
        "proptax", "otheritem", "mortgage", "dividends", "ltcg", "stcg", "intrec",
        "depx", "dep18", "dep6", "childcare", "psemp", "ssemp",
    ))
    df = _with_default(df, "state_sales_or_income_tax_ded")
    df = _with_default(df, "earned_income")

    # Year>LASTAT (2021): deflate every dollar-valued raw/federal-computed
    # input by `flate`, run 2021's REAL law (`effective_year`, forced to
    # 2021 by `resolve_state_year`) on the deflated figures, then reinflate
    # the final tax below (see engine/state_extrapolation.py). A no-op for
    # year<=2021 (`flate==1`). `salt_capped`/`state_sales_or_income_tax_ded`/
    # `earned_income` are federal.py's own derived (real-year, undeflated)
    # columns, same situation as AR's `wages`/AZ's `salt_capped` - deflated
    # directly here rather than relying on their raw inputs being deflated
    # after the fact (which wouldn't reach an already-materialized column).
    df = deflate_for_extrapolation(
        df,
        flate,
        [
            "agi", "salt_capped", "state_sales_or_income_tax_ded", "earned_income",
            "mortgage", "proptax", "otheritem", "dividends", "ltcg", "stcg", "intrec",
            "psemp", "ssemp", "pwages", "swages", "wages", "childcare", "ccc",
        ],
    )

    df = df.with_columns(
        ca_sep=pl.when(pl.col("filing_status") == "married_separate").then(2.0).otherwise(1.0),
        ca_ajnt=pl.when(pl.col("filing_status") == "married_joint").then(2.0).otherwise(1.0),
        ca_hoh=pl.col("filing_status") == "head_of_household",
        # local `txp` - bumped to 2 for head_of_household (`if(mst.eq.4.
        # or.mst.eq.5.or.mst.eq.7)txp=2.`) - used by the pre-1987 std
        # deduction/exemption-credit formulas and the 1987+ std deduction.
        ca_txp=pl.when(pl.col("filing_status").is_in(["married_joint", "head_of_household"])).then(2.0).otherwise(1.0),
        # `data(7)` itself, UNBUMPED - the 1987+ exemption-credit formulas
        # read this directly, not the local `txp` (confirmed via a live
        # oracle probe: HoH, depx=1, 1990 real excrd=$116=50*1.16*(1+1),
        # not $174=50*1.16*(2+1)).
        ca_txp_raw=pl.when(pl.col("filing_status") == "married_joint").then(2.0).otherwise(1.0),
    )

    # --- AGI ---
    aif = float(resolve_year(p["standard_deduction_aif"], effective_year))
    if effective_year <= 1986:
        excl = float(resolve_year(PRE1987_PARAMS["capital_gains_exclusion_rate"], effective_year)) if effective_year <= 1986 else 0.0
        # CA's OWN pre-1987 capital-gain inclusion rate is a flat 65% of
        # LTCG (35% exclusion) - a real, CA-specific rate, NOT the
        # federal pre1987.yaml exclusion rate (confirmed via source:
        # `cg=data(68)+.65*(data(70)+...)`).
        df = df.with_columns(ca_cg=pl.col("stcg") + 0.65 * pl.col("ltcg"))
        # `data(17)=x(46)+x(47)` = raw psemp+ssemp (gross self-employment
        # income, no netting/92.35% adjustment) - a real, live term in
        # `totinc`/`einc`, NOT the dead "Other Property income" field
        # this project mistakenly assumed it was while building Arkansas
        # (that assumption happened to not matter there; it does here).
        df = df.with_columns(
            ca_gross_se=pl.col("psemp").clip(0, None) + pl.col("ssemp").clip(0, None)
        )
        df = df.with_columns(
            ca_totinc=pl.col("wages") + pl.col("intrec") + (pl.col("dividends") + 0.001) + pl.col("ca_cg")
            + pl.col("ca_gross_se")
        )
        df = df.with_columns(ca_agi=pl.col("ca_totinc").clip(0, None))
    else:
        # California conforms to federal AGI directly (`agi=comnew(2)`) -
        # the commented-out cg/cacg/adjcg block in the source confirms
        # `addit` is a real, permanent no-op for 1987+.
        df = df.with_columns(ca_subtra=pl.lit(0.0))
        if effective_year in (2011, 2012):
            # 2011-2012 payroll-tax-holiday FICA adjustment (same
            # hardcoded-rate `setax` quantity found building AL/AZ) - CA
            # doesn't conform to the federal SE-tax-deduction quirk for
            # those two years, so it backs the differential out of AGI.
            df = _with_default(df, "wages")
            rate_in = 0.124
            wage_base = {2011: 106800.0, 2012: 110100.0}[effective_year]
            gross_se = (pl.col("psemp").clip(0, None) + pl.col("ssemp").clip(0, None))
            se_net = 0.9235 * gross_se
            oasdi_room = (wage_base - pl.col("wages")).clip(0, None)
            setax = rate_in * pl.min_horizontal(oasdi_room, se_net) + 0.029 * se_net
            df = df.with_columns(
                # `subtra` STARTS at 0 (SS-benefit terms are all inert
                # here) and this block SUBTRACTS the correction from it,
                # making it NEGATIVE - so `agi=agi-subtra` actually ADDS
                # this amount to AGI (CA doesn't conform to the payroll-
                # tax-holiday-inflated federal SE-tax deduction, so it
                # adds back the differential). `ca_subtra` here keeps the
                # "positive = amount subtracted from AGI" convention, so
                # this branch is negated relative to the raw source sign.
                ca_subtra=-pl.when(setax <= 14204).then((0.5751 - 0.5) * setax).otherwise(1067.0)
            )
        df = df.with_columns(ca_agi=pl.col("agi") - pl.col("ca_subtra"))

    # --- Standard deduction ---
    if effective_year <= 1978:
        df = df.with_columns(ca_stded=1000.0 * pl.col("ca_txp"))
    else:
        df = df.with_columns(ca_stded=1000.0 * aif * pl.col("ca_txp"))

    # --- Itemized deduction ---
    if effective_year <= 1986:
        # `xitded=comnew(24)-data(50)*comnew(24)/comnew(30)` reduces, for
        # this project's schema (no medical/casualty/misc-2% inputs, and
        # no federal-side Pease pre-1987), to the raw proptax+otheritem+
        # mortgage total directly - same simplification already used for
        # AL/IL/AZ<=1990.
        df = df.with_columns(ca_xitded=pl.col("proptax") + pl.col("otheritem") + pl.col("mortgage"))
    else:
        # `xitded=max(0,comnew(30)-data(50)+data(27))` - `comnew(30)`
        # [deducp] includes the state/sales-tax feedback term
        # (`state_sales_or_income_tax_ded`, fed back each iteration by
        # `engine/federal_state.py`) once, via federal's own `salt_capped`
        # - subtract it back out here (matching the exact term the source
        # subtracts) rather than dropping SALT/data(50) entirely like the
        # simpler AL/AR/AZ<=1990 technique does, since that would silently
        # ALSO drop the real 2018+ $10k SALT cap's interaction with this
        # state-tax feedback.
        df = _with_default(df, "state_sales_or_income_tax_ded")
        df = df.with_columns(
            ca_xitded_base=(pl.col("salt_capped") - pl.col("state_sales_or_income_tax_ded") + pl.col("mortgage")).clip(0, None)
        )
        base = float(p["exemption_credit_phaseout_base_1991plus"])
        phaded = pl.when(pl.col("ca_hoh")).then(base * float(p["exemption_credit_phaseout_base_hoh_multiplier"])).when(
            pl.col("filing_status") == "married_joint"
        ).then(base * float(p["exemption_credit_phaseout_base_joint_multiplier"])).otherwise(base)
        if effective_year >= 1992:
            aifded = float(resolve_year(p["itemized_phaseout_aifded"], effective_year))
            phaded = phaded * aifded
        df = df.with_columns(ca_phaded=phaded)
        if effective_year >= 1991:
            df = df.with_columns(
                ca_reduce=pl.when(pl.col("agi") > pl.col("ca_phaded"))
                .then(pl.min_horizontal(0.8 * pl.col("ca_xitded_base"), 0.06 * (pl.col("agi") - pl.col("ca_phaded"))))
                .otherwise(0.0)
            )
        else:
            df = df.with_columns(ca_reduce=pl.lit(0.0))
        df = df.with_columns(ca_xitded=(pl.col("ca_xitded_base") - pl.col("ca_reduce")).clip(0, None))
        if effective_year == 1999:
            df = df.with_columns(ca_xitded=pl.when(forced_standard()).then(0.0).otherwise(pl.col("ca_xitded")))

    if effective_year <= 1986:
        # `deduc=max(stded,xitded); if(stded>xitded) deduc=stded+charni
        # [charni always $0 here - no charity input]; deduc=deduc-
        # xif(law>=1982,stded)` - a real, deliberate cancellation: for
        # law>=1982 the standard deduction's own dollar value is already
        # baked into that era's bracket tables' own zero-rate first
        # segment (e.g. 1982-1986 single's `[0,1580]@0%` - not a
        # coincidence that 1580 = 1000*aif(1982)*1 = that year's stded),
        # so it gets subtracted back out of `deduc` here to avoid double-
        # counting it. Confirmed via oracle probe: single, $50,000 wages,
        # 1984 - a naive `deduc=max(stded,xitded)` overstates the
        # deduction by exactly one `stded` once itemized inputs are $0.
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

    # --- Bracket tax --- (married_joint uses the single table with income
    # halved then the resulting tax doubled - `ajnt`'s own doubling in the
    # source; head_of_household gets its own, separate table instead)

    if effective_year <= 1981:
        aiftab = float(resolve_year(p["bracket_aiftab_pre1987"], effective_year))
        raw_s = [[round(lo * aiftab / 10.0) * 10.0, rate] for lo, rate in p["brackets_pre1982_single"]]
        raw_h = [[round(lo * aiftab / 10.0) * 10.0, rate] for lo, rate in p["brackets_pre1982_hoh"]]
        brackets_s = _raw_to_start_rate(raw_s)
        brackets_h = _raw_to_start_rate(raw_h)
        stat_single = bracket_tax(pl.col("ca_taxinc") / pl.col("ca_ajnt"), brackets_s) * pl.col("ca_ajnt")
        stat_hoh = bracket_tax(pl.col("ca_taxinc"), brackets_h)
        df = df.with_columns(ca_regtax=pl.when(pl.col("ca_hoh")).then(stat_hoh).otherwise(stat_single))
    elif effective_year <= 1986:
        aiftab = float(resolve_year(p["bracket_aiftab_pre1987"], effective_year))
        brackets_s = _raw_to_start_rate(p["brackets_1982_1986_single"], aiftab)
        brackets_h = _raw_to_start_rate(p["brackets_1982_1986_hoh"], aiftab)
        stat_single = bracket_tax(pl.col("ca_taxinc") / pl.col("ca_ajnt"), brackets_s) * pl.col("ca_ajnt")
        stat_hoh = bracket_tax(pl.col("ca_taxinc"), brackets_h)
        df = df.with_columns(ca_regtax=pl.when(pl.col("ca_hoh")).then(stat_hoh).otherwise(stat_single))
    else:
        if effective_year <= 1990:
            aiftab = float(resolve_year(p["bracket_aiftab_1987_2012"], effective_year))
            brackets_s = _raw_to_start_rate(p["brackets_1987_1990_single"], aiftab)
            brackets_h = _raw_to_start_rate(p["brackets_1987_1990_hoh"], aiftab)
        elif effective_year <= 1995:
            aiftab = float(resolve_year(p["bracket_aiftab_1987_2012"], effective_year))
            brackets_s = _raw_to_start_rate(p["brackets_1991_1995_single"], aiftab)
            brackets_h = _raw_to_start_rate(p["brackets_1991_1995_hoh"], aiftab)
        elif effective_year <= 2008:
            aiftab = float(resolve_year(p["bracket_aiftab_1987_2012"], effective_year))
            brackets_s = _raw_to_start_rate(p["brackets_1996_2008_single"], aiftab)
            brackets_h = _raw_to_start_rate(p["brackets_1996_2008_hoh"], aiftab)
        elif effective_year <= 2010:
            # `tab9s`/`tab9h` - the SAME threshold-scaling as tab96s but a
            # real, separate +0.25pp rate schedule (CA's actual 2009-2010
            # recession-era temporary surtax) - see the YAML's own note.
            aiftab = float(resolve_year(p["bracket_aiftab_1987_2012"], effective_year))
            brackets_s = _raw_to_start_rate(p["brackets_2009_2010_single"], aiftab)
            brackets_h = _raw_to_start_rate(p["brackets_2009_2010_hoh"], aiftab)
        elif effective_year <= 2011:
            aiftab = float(resolve_year(p["bracket_aiftab_1987_2012"], effective_year))
            brackets_s = _raw_to_start_rate(p["brackets_1996_2008_single"], aiftab)
            brackets_h = _raw_to_start_rate(p["brackets_1996_2008_hoh"], aiftab)
        else:
            aif12 = float(resolve_year(p["bracket_aif12_2012plus"], effective_year))
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

    df = df.with_columns(ca_statax=pl.col("ca_regtax"))

    # --- Exemption Credit ---
    # `if(mst.eq.4.or.mst.eq.7.or.mst.eq.5)dep=max(0,dep-1)` - ONLY for
    # law<=1986 (head_of_household loses one dependent-credit unit that
    # era specifically; married_joint is NOT included). For law>=1987
    # `dep` is used raw (`data(8)` directly), no reduction at all.
    dep_reduced_pre1987 = pl.when(pl.col("ca_hoh")).then((pl.col("depx") - 1).clip(0, None)).otherwise(pl.col("depx"))
    dep_reduced = dep_reduced_pre1987 if effective_year <= 1986 else pl.col("depx")
    if effective_year <= 1986:
        aif_pre87 = aif
        df = df.with_columns(
            ca_excrd=(pl.lit(round(25.0 * aif_pre87)) * pl.col("ca_txp") + pl.lit(round(8.0 * aif_pre87)) * dep_reduced)
        )
        if effective_year == 1978:
            df = df.with_columns(ca_excrd=100.0 * pl.col("ca_txp") + 8.0 * dep_reduced)
    elif effective_year <= 1997:
        xmpaif = float(resolve_year(p["exemption_credit_xmpaif"], effective_year))
        df = df.with_columns(ca_excrd=50.0 * xmpaif * (pl.col("ca_txp_raw") + dep_reduced))
    else:
        xmpaif = float(resolve_year(p["exemption_credit_xmpaif"], effective_year))
        xmpdep = float(resolve_year(p["exemption_credit_per_dependent_1998plus"], effective_year))
        df = df.with_columns(ca_excrd=50.0 * xmpaif * pl.col("ca_txp_raw") + dep_reduced * xmpdep)

    if effective_year >= 1991:
        base = float(p["exemption_credit_phaseout_base_1991plus"])
        aifded = float(resolve_year(p["itemized_phaseout_aifded"], effective_year)) if effective_year >= 1992 else 1.0
        phaded = pl.when(pl.col("ca_hoh")).then(base * float(p["exemption_credit_phaseout_base_hoh_multiplier"])).when(
            pl.col("filing_status") == "married_joint"
        ).then(base * float(p["exemption_credit_phaseout_base_joint_multiplier"])).otherwise(base)
        phaded = phaded * aifded
        df = df.with_columns(ca_phaded2=phaded)
        aif2 = float(resolve_year(p["exemption_credit_aif2_pre1998"], effective_year)) if effective_year <= 1998 else None
        if effective_year < 1998:
            excess_over = (pl.col("agi") - pl.col("ca_phaded2")).clip(0, None)
            reduction_full = (dep_reduced + pl.col("ca_txp")) * 6.0 * (pl.col("agi") - pl.col("ca_phaded2")) / (2500.0 * aif2 / pl.col("ca_sep"))
            excrd_over_agi = pl.when(excess_over > 25000.0 * aif2 / pl.col("ca_sep")).then(0.0).otherwise(
                (pl.col("ca_excrd") - reduction_full).clip(0, None)
            )
        else:
            num = 1.0 + ((pl.col("agi") - pl.col("ca_phaded2")) / (2500.0 / pl.col("ca_sep"))).floor()
            excrd_over_agi = (pl.col("ca_excrd") - (dep_reduced + pl.col("ca_txp")) * 6.0 * num).clip(0, None)

        df = df.with_columns(
            ca_excrd=pl.when(pl.col("agi") > pl.col("ca_phaded2")).then(excrd_over_agi).otherwise(pl.col("ca_excrd"))
        )

        if 1994 <= effective_year <= 1998:
            aif1 = float(resolve_year(p["exemption_credit_aif1_1994_1998"], effective_year)) if "exemption_credit_aif1_1994_1998" in p else None
            ak = float(resolve_year(p["exemption_credit_ak_1994_1998"], effective_year)) if "exemption_credit_ak_1994_1998" in p else None
            if aif1 is not None and ak is not None:
                exc = pl.when(pl.col("filing_status").is_in(["single", "head_of_household"])).then(
                    30000.0 * aif1
                ).otherwise(20000.0 * aif1 * pl.col("ca_sep"))
                excess_thr = pl.when(pl.col("ca_hoh")).then(150000.0 * aif2).otherwise(100000.0 * aif2 * pl.col("ca_sep"))
                under_std = pl.col("ca_stded") > pl.col("ca_xitded")
                alt_a = pl.col("ca_statax") - ak * (pl.col("agi") - exc).clip(None, 0)
                alt_b = pl.col("ca_statax") - ak * (
                    pl.min_horizontal(0.025 * pl.col("agi").clip(0, None), pl.lit(0.0))
                    + pl.col("otheritem") + pl.col("mortgage") + pl.col("ca_taxinc") - exc
                )
                capped = pl.when(under_std).then(alt_a).otherwise(alt_b)
                gate = (pl.col("ca_agi") > pl.col("ca_phaded2")) & (pl.col("ca_agi") < excess_thr)
                df = df.with_columns(
                    ca_excrd=pl.when(gate).then(pl.min_horizontal(capped, pl.col("ca_excrd"))).otherwise(pl.col("ca_excrd"))
                )

    df = df.with_columns(ca_noncr=pl.col("ca_excrd"))

    # --- Low Income Credit (expired after 1991) ---
    if effective_year <= 1983:
        threshold = float(p["low_income_credit_threshold_multiplier_pre1984"])
        base_amt = round(40.0 * (aif if effective_year >= 1979 else 1.0))
        df = _with_default(df, "childcare")
        hy = pl.col("wages") + pl.col("intrec") + (pl.col("dividends") + 0.001) + pl.col("ca_cg") if effective_year <= 1986 else pl.col("agi")
        df = df.with_columns(
            ca_lowcr=pl.when(hy <= threshold * pl.col("ca_txp"))
            .then((base_amt - (pl.col("ca_agi") - 5000.0) * 0.5).clip(0, None).round())
            .otherwise(0.0)
        )
    elif 1985 <= effective_year <= 1991:
        aiflow = float(resolve_year(p["low_income_credit_aiflow"], effective_year))
        # `div=2` only for mst.eq.1(single)/3(unused by this project)/6
        # (married_separate) - head_of_household is NOT included (a real,
        # easy-to-miss distinction from the div=2 group used elsewhere;
        # confirmed via a live oracle probe showing HoH's own low-income
        # credit only matches when using div=1, same as married_joint).
        div = pl.when(pl.col("filing_status").is_in(["single", "married_separate"])).then(2.0).otherwise(1.0)
        raw_lo = [lo for lo, _ in p["low_income_table_1985_1991"]]
        raw_rate = [rate for _, rate in p["low_income_table_1985_1991"]]
        # `tablki`: LINEAR interpolation between adjacent table points (not
        # a step function) - below the first threshold, flat at rate[0];
        # at/above the last (finite) threshold, flat at rate[-1] (0).
        thresholds = [lo * aiflow / div for lo in raw_lo[:-1]]
        expr = pl.lit(raw_rate[-1])
        for i in range(len(thresholds) - 1, -1, -1):
            t_hi = thresholds[i]
            r_hi = raw_rate[i]
            if i == 0:
                below = pl.lit(r_hi)
            else:
                t_lo = thresholds[i - 1]
                r_lo = raw_rate[i - 1]
                w = (pl.col("ca_agi") - t_lo) / (t_hi - t_lo)
                below = w * r_lo + (1 - w) * r_hi if r_hi > r_lo else w * r_hi + (1 - w) * r_lo
            expr = pl.when(pl.col("ca_agi") < t_hi).then(below).otherwise(expr)
        df = df.with_columns(ca_lowcr=pl.col("ca_statax") * expr)
    else:
        df = df.with_columns(ca_lowcr=pl.lit(0.0))
    df = df.with_columns(ca_noncr=pl.col("ca_noncr") + pl.col("ca_lowcr"))

    # --- Child/Dependent Care Credit ---
    child_fed = pl.col("ccc").clip(0, None) if "ccc" in df.collect_schema().names() else pl.lit(0.0)
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
        df = df.with_columns(ca_chcr=expr * child_fed)

    # --- Credit for the Elderly (inert - data(32)/data(9) never populated) ---
    df = df.with_columns(ca_eld=pl.lit(0.0))

    df = df.with_columns(ca_noncr=pl.col("ca_noncr") + pl.col("ca_eld"))
    df = df.with_columns(ca_statax=(pl.col("ca_statax") - pl.col("ca_noncr")).clip(0, None))

    # --- Minimum Tax on Preference Income (1979-1986) / AMT (1987+) ---
    if 1979 <= effective_year <= 1986:
        df = df.with_columns(ca_amt=pl.lit(0.0))
    elif effective_year >= 1987:
        # `if(xitded.gt.stded) addprf=...+data51+data52+data54; else
        # addprf=stded` - itemizing adds back proptax+otheritem (AMT
        # disallows the state/local tax deduction); NOT itemizing just
        # uses the standard deduction amount directly.
        itemizing = pl.col("ca_xitded") > pl.col("ca_stded")
        addprf = pl.when(itemizing).then(pl.col("proptax") + pl.col("otheritem")).otherwise(pl.col("ca_stded"))
        # `alminy=totprf+taxinc-(max(0,data17)+max(0,data21)+
        # max(0,comnew8)+reduce)` - `reduce` (the regular-tax-side Pease
        # reduction just computed) and `data(17)` (gross SE income - see
        # the AGI note above) both genuinely subtract here; data(21)/
        # comnew(8) are confirmed-inert for this schema.
        reduce_col = pl.col("ca_reduce") if "ca_reduce" in df.collect_schema().names() else pl.lit(0.0)
        gross_se = pl.col("psemp").clip(0, None) + pl.col("ssemp").clip(0, None)
        alminy = (addprf + pl.col("ca_taxinc") - gross_se - reduce_col).clip(0, None)
        if effective_year <= 1997:
            excl_by_status = {
                s: float(resolve_year(FEDERAL_AMT_PARAMS["exemption"][s], effective_year)) for s in _STATUSES
            }
            excl = _by_status(excl_by_status)
        else:
            vals = p["amt_exclusion_by_year"][effective_year]
            excl = _by_status4([float(v) for v in vals])
        if effective_year == 1987:
            phase_vals = p["amt_phase_by_year"][1987]
        else:
            phase_vals = p["amt_phase_by_year"].get(effective_year, p["amt_phase_by_year"][1987])
        phase = _by_status4([float(v) for v in phase_vals])
        phaout = 0.25 * (alminy - phase).clip(0, None)
        exclnt = (excl - phaout).clip(0, None)
        alminc = (alminy - exclnt).clip(0, None)
        amt_rate = float(resolve_year(p["amt_rate_by_year"], effective_year))
        df = df.with_columns(ca_amt=(alminc * amt_rate - pl.col("ca_statax")).clip(0, None))
    else:
        df = df.with_columns(ca_amt=pl.lit(0.0))

    df = df.with_columns(ca_statax=pl.col("ca_statax") + pl.col("ca_amt"))

    # --- Renter's Credit: confirmed permanently inert (see module docstring) ---

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
        dylim = float(resolve_year(p["eitc_disqualified_income_limit"], effective_year))
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
        disqy = pl.col("ca_capgn_disqy") if "ca_capgn_disqy" in df.collect_schema().names() else (
            pl.col("stcg").clip(0, None) + pl.col("ltcg").clip(0, None) + pl.col("intrec")
        )
        earncr = pl.when(disqy >= dylim).then(0.0).otherwise(earncr)
        earncr = pl.when(pl.col("ca_sep") == 2).then(0.0).otherwise(earncr)
        df = df.with_columns(ca_earncr=earncr)
        # Young Child Tax Credit, 2019+ (taxsim_2024_09_21.f:2911-2919):
        # `if(earncr.gt.0.and.data(203).gt.0)` - data(203) is NOT literally
        # "number of young children" despite the credit's name; per the
        # input reader's own "depvars"-style branch (taxsim_2024_09_21.f:
        # 21246-21249, `data(203)=x(10)`, matched to this project's own
        # dep17/dep18-style column reader rather than raw per-dependent
        # ages), `data(203)` is actually `dep18` (the EIC-qualifying-child
        # count) - NOT `data(210)` (a separate x(37) input, matched to
        # this project's own `dep6`, per federal.py's own identical
        # mapping for its Young Child credit). Confirmed via a debug-
        # instrumented oracle build showing a nonzero "young" ($181.13)
        # for a `dep18=1` case this module previously assumed contributed
        # $0 - this was wrongly documented at the top of this file as
        # "confirmed permanently inert" based on `data(210)` alone,
        # without checking the SEPARATE `data(203)` gate. Only matters
        # for `earned>25000` (surfaced this session by the 2022/2023
        # extrapolation retrofit specifically because deflating wages
        # down crossed EIC eligibility thresholds this project's own
        # <=2021 test cases never landed on) - the `earned<=25000` branch
        # (using `dep6`/data(210) directly) is unexercised by the current
        # test suite and unverified beyond matching federal.py's own
        # established `dep6` mapping.
        young_gate = (pl.col("ca_earncr") > 0) & (pl.col("dep18") > 0)
        young_low = 1000.0 * pl.col("dep6")
        young_high = (1000.0 - 0.2 * (earned - 25000.0)).clip(0, None)
        young = pl.when(earned <= 25000).then(young_low).otherwise(young_high)
        df = df.with_columns(ca_young=pl.when(young_gate).then(young).otherwise(0.0))
    else:
        df = df.with_columns(ca_earncr=pl.lit(0.0))
        df = df.with_columns(ca_young=pl.lit(0.0))

    df = df.with_columns(siitax=(pl.col("ca_statax") - pl.col("ca_earncr") - pl.col("ca_young")) * flate)
    return df
