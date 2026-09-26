"""Delaware individual income tax calculator."""

import polars as pl

from taxsim_py.engine.brackets import bracket_rate, bracket_tax
from taxsim_py.engine.inputs import aged_count, is_dependent_filer, taxpayer_count
from taxsim_py.engine.schema import PARAMETERS_ROOT, load_yaml, resolve_year
from taxsim_py.engine.state import with_defaults, with_default as _with_default, forced_standard, with_state_detail
from taxsim_py.engine.state_extrapolation import deflate_for_extrapolation, resolve_state_year

DE_PARAMS = load_yaml(PARAMETERS_ROOT / "states" / "de" / "income_tax.yaml")


def compute_de_tax(df: pl.DataFrame, year: int) -> pl.DataFrame:
    effective_year, flate = resolve_state_year(year)
    p = DE_PARAMS
    df = with_defaults(df, ("federal_chcr", "proptax", "otheritem", "mortgage", "depx", "ui", "pui", "sui"))
    df = _with_default(df, "state_sales_or_income_tax_ded")
    # `ccc`/`eitc` aren't exposed by federal_pre1987.py (years<=1986) -
    # both DE mechanisms that read them (Child Care Credit, EITC) only
    # apply well after 1986 anyway, but default them so the column
    # references below don't crash on an early year.
    df = _with_default(df, "ccc")
    df = _with_default(df, "eitc")

    df = with_defaults(df, ("taxable_social_security", "earned_income", "intrec", "dividends", "stcg", "ltcg"))
    df = df.with_columns(
        de_sep=pl.when(pl.col("filing_status") == "married_separate").then(2.0).otherwise(1.0),
        # `data(7)` - self/spouse exemption unit count (1, or 2 ONLY for
        # married_joint - head_of_household is NOT bumped here, unlike its
        # own $3,000/$3,000-style personal-exemption dollar amount
        # elsewhere - same "raw data(7), not the local txp" distinction
        # already found for AZ/CA).
        de_texp=taxpayer_count(),
        # `comnew(68)` (exemps count: self + spouse-if-joint + dependents) -
        # a pure count, divided by flate for extrapolated years like IL's
        # own `exemps` (see module docstring point 2).
        de_exemps_raw=(
            taxpayer_count() + pl.col("depx") + aged_count()
            if effective_year <= 1986
            else pl.when(is_dependent_filer()).then(0.0).otherwise(taxpayer_count() + pl.col("depx"))
        ),
    )

    # `salt_capped`/`state_sales_or_income_tax_ded` are federal.py's own
    # derived (real-year, undeflated) columns - deflated directly here,
    # same situation as AR's `wages`/AZ's `salt_capped` elsewhere in this
    # project (deflating their raw inputs afterward wouldn't reach an
    # already-materialized column).
    df = deflate_for_extrapolation(
        df,
        flate,
        [
            "federal_chcr",
            "pwages", "swages", "wages", "proptax", "otheritem", "mortgage", "ui", "pui", "sui",
            "agi", "salt_capped", "state_sales_or_income_tax_ded", "eitc", "ccc",
            "de_exemps_raw", "pensions", "taxable_social_security", "earned_income", "intrec", "dividends",
            "stcg", "ltcg",
        ],
    )
    df = df.with_columns(de_num=pl.col("de_exemps_raw").floor())
    if effective_year == 1989:
        df = df.with_columns(de_num=pl.col("de_num") + aged_count())

    # --- AGI --- federal AGI less taxable Social Security. `law.eq.2021`
    # (also every projected 2022/2023 year, run under 2021 law) fully
    # excludes unemployment compensation, a one-time DE COVID-era provision
    # layered on top of the federal exclusion.
    df = df.with_columns(de_agi=pl.col("agi") - pl.col("taxable_social_security"))
    if effective_year == 2021:
        # `data(82)` is the SAME total-UI quantity federal.py's own
        # `ui_total = max(ui, pui+sui)` computes (confirmed via oracle
        # probe: married_joint, ui=$8,000/pui=$0/sui=$4,000 - federal AGI
        # only reflects $8,000 of UI, not $12,000, and DE's own post-
        # exclusion AGI matches subtracting that same $8,000, not
        # ui+pui+sui summed) - `pui`/`sui` are a SPLIT of `ui`, not
        # additional income on top of it.
        ui_total = pl.max_horizontal(pl.col("ui"), pl.col("pui") + pl.col("sui"))
        df = df.with_columns(de_agi=pl.col("de_agi") - ui_total)

    # Pension exclusion.
    aged = aged_count()
    pensions = pl.col("pensions")
    investment = pl.col("intrec") + pl.col("dividends") + 0.001 + (pl.col("stcg") + pl.col("ltcg")).clip(0, None)
    aged_cap = float(resolve_year(p["pension_exclusion_aged"], effective_year))
    penexc = (
        pl.when(aged < 1).then(pl.min_horizontal(p["pension_exclusion_young_per_taxpayer"] * pl.col("de_texp"), pensions))
        .when(aged == 1).then(pl.min_horizontal(pl.lit(aged_cap), pensions + 0.5 * investment))
        .otherwise(pl.min_horizontal(pl.lit(2 * aged_cap), pensions + investment))
    ).clip(0, None)
    df = df.with_columns(de_agi=pl.col("de_agi") - penexc)
    if effective_year >= 1984:
        low_income = (
            (aged > 0)
            & (pl.col("earned_income") < p["aged_low_income_earned_limit"] * aged)
            & (pl.col("de_agi") <= p["aged_low_income_agi_limit"] * aged)
        )
        df = df.with_columns(
            de_agi=pl.when(low_income).then(pl.col("de_agi") - p["aged_low_income_exclusion"] * aged).otherwise(pl.col("de_agi"))
        )

    # --- Standard deduction ---
    if effective_year <= 1987:
        cap_per_exemption = float(resolve_year(p["standard_deduction_cap_per_exemption_pre1988"], effective_year))
        df = df.with_columns(
            de_stded=pl.min_horizontal(
                cap_per_exemption * pl.col("de_texp") / pl.col("de_sep"), 0.1 * pl.col("de_agi").clip(0, None)
            )
        )
    elif effective_year <= 1998:
        flat_single = float(resolve_year(p["standard_deduction_flat_single_or_hoh_1988_1998"], effective_year))
        flat_joint = float(resolve_year(p["standard_deduction_flat_joint_or_sep_1988_1998"], effective_year))
        df = df.with_columns(
            de_stded=pl.when(pl.col("filing_status").is_in(["single", "head_of_household"]))
            .then(flat_single)
            .otherwise(flat_joint / pl.col("de_sep"))
        )
    elif effective_year == 1999:
        flat_single = float(p["standard_deduction_flat_single_or_hoh_1999"][1999])
        flat_joint = float(p["standard_deduction_flat_joint_or_sep_1999"][1999])
        df = df.with_columns(
            de_stded=pl.when(pl.col("filing_status").is_in(["single", "head_of_household"]))
            .then(flat_single)
            .otherwise(flat_joint / pl.col("de_sep"))
        )
    else:
        per_exemption = float(resolve_year(p["standard_deduction_per_exemption_2000plus"], effective_year))
        df = df.with_columns(de_stded=per_exemption * pl.col("de_texp"))
    if effective_year >= 1987:
        aged_std = float(resolve_year(p["aged_standard_deduction"], effective_year))
        df = df.with_columns(de_stded=pl.col("de_stded") + aged_std * aged)

    # --- Itemized deduction --- (see module docstring point 1). <=1986
    # uses the raw proptax+otheritem+mortgage total directly, same
    # simplification already used for AL/IL/AZ<=1990/CA<=1986 - federal_
    # pre1987.py doesn't expose `salt_capped`/comnew(30) at all, and for
    # this era there's no SALT-feedback term to subtract back out in the
    # first place (the state-tax-liability feedback loop is itself a
    # >=1987-era mechanic - see engine/federal_state.py).
    if effective_year <= 1986:
        df = df.with_columns(de_raw_itemized=pl.col("proptax") + pl.col("otheritem") + pl.col("mortgage"))
    else:
        df = df.with_columns(de_raw_itemized=pl.col("salt_capped") + pl.col("mortgage"))
    if effective_year <= 1986:
        df = df.with_columns(de_xitded_base=pl.col("de_raw_itemized"))
    elif effective_year <= 2017:
        df = df.with_columns(
            de_xitded_base=(pl.col("de_raw_itemized") - pl.col("state_sales_or_income_tax_ded")).clip(0, None)
        )
        # `if(law.eq.1987) xitded=xitded*1.12` - a real, 1987-only
        # multiplier on top of the base formula (confirmed via oracle
        # probe: proptax=$4,000/otheritem=$2,000/mortgage=$8,000, single,
        # 1987 - real itemized deduction is $15,680 = $14,000*1.12, not
        # $14,000).
        if effective_year == 1987:
            df = df.with_columns(de_xitded_base=pl.col("de_xitded_base") * 1.12)
    elif effective_year == 2018:
        # `sttax=min(10000/sep,data51+data50+data54); xitded=comnew(30)-
        # min(data50,sttax-(data51+data54))` - the TCJA $10k cap's own
        # room is filled by proptax/otheritem FIRST, and only the state-
        # tax feedback term's OWN portion of whatever's left gets
        # subtracted back out (not the whole thing, unlike every other
        # year here) - a real, 2018-only transition-year mechanic.
        sttax_cap = 10000.0 / pl.col("de_sep")
        sttax = pl.min_horizontal(
            sttax_cap, pl.col("proptax") + pl.col("state_sales_or_income_tax_ded") + pl.col("otheritem")
        )
        room_after_proptax_otheritem = sttax - (pl.col("proptax") + pl.col("otheritem"))
        df = df.with_columns(
            de_xitded_base=(
                pl.col("de_raw_itemized")
                - pl.min_horizontal(pl.col("state_sales_or_income_tax_ded"), room_after_proptax_otheritem)
            ).clip(0, None)
        )
    else:
        df = df.with_columns(de_xitded_base=pl.col("de_raw_itemized"))

    if 1991 <= effective_year <= 2017:
        base = float(p["itemized_phaseout_base"][1960])
        if effective_year <= 2012:
            aif92 = float(resolve_year(p["itemized_phaseout_aif92_1992_2012"], effective_year)) if effective_year >= 1992 else 1.0
            phas92 = base * aif92 / pl.col("de_sep")
        else:
            aif13 = float(resolve_year(p["itemized_phaseout_aif13_2013_2017"], effective_year))
            mult = p["itemized_phaseout_2013_2017_multiplier"]
            mult_expr = pl.lit(None, dtype=pl.Float64)
            for status, v in mult.items():
                mult_expr = pl.when(pl.col("filing_status") == status).then(pl.lit(float(v))).otherwise(mult_expr)
            phas92 = aif13 * 2.5 * base * mult_expr
        reduce_amt = pl.when(pl.col("de_agi") > phas92).then(
            pl.min_horizontal(0.8 * pl.col("de_xitded_base"), 0.03 * (pl.col("de_agi") - phas92))
        ).otherwise(0.0)
        if effective_year in (2006, 2007):
            reduce_amt = reduce_amt * (2.0 / 3.0)
        elif effective_year in (2008, 2009):
            reduce_amt = reduce_amt / 3.0
        elif 2010 <= effective_year <= 2012:
            reduce_amt = pl.lit(0.0)
        df = df.with_columns(de_xitded=(pl.col("de_xitded_base") - reduce_amt).clip(0, None))
    else:
        df = df.with_columns(de_xitded=pl.col("de_xitded_base"))

    # `if(ided.eq.-2.and.law.eq.1999) xitded=0` - forced-standard zeroes
    # itemized ONLY for 1999, a real, narrow DE-specific special case
    # (matching the exact same `force_itemize is False and effective_year
    # == 1999` mechanism already found for California).
    if effective_year == 1999:
        df = df.with_columns(de_xitded=pl.when(forced_standard()).then(0.0).otherwise(pl.col("de_xitded")))

    df = df.with_columns(de_deduc=pl.max_horizontal(pl.col("de_stded"), pl.col("de_xitded")))

    # --- Exemption --- (dies out after 1995 into a flat credit instead,
    # see EXEMPTIONS/personal-credit sections below)
    if effective_year <= 1987:
        # `exemp=(num*xmp(law,1))+twn(comnew(1),0,300*texp)` - a bonus
        # term ONLY for this era: federal tax liability itself (`fiitax`,
        # comnew(1)), clamped to [0, $300*texp] - a real, DE-specific
        # federal-tax-paid-linked exemption addback, not itself an
        # itemized-deduction-style credit.
        pe = float(resolve_year(p["personal_exemption_amount"], effective_year))
        bonus = pl.col("fiitax").clip(0, None).clip(None, 300.0 * pl.col("de_texp"))
        df = df.with_columns(de_exemp=pl.col("de_num") * pe + bonus)
    elif effective_year <= 1995:
        pe = float(resolve_year(p["personal_exemption_amount"], effective_year))
        df = df.with_columns(de_exemp=pl.col("de_num") * pe)
    else:
        df = df.with_columns(de_exemp=pl.lit(0.0))

    df = df.with_columns(de_taxinc=(pl.col("de_agi") - pl.col("de_deduc") - pl.col("de_exemp")).clip(0, None))

    # --- Married-joint earner split (relief mechanic: run the SAME
    # bracket table on each spouse's own apportioned share, take the min
    # against the combined-income result) - single/HoH/married_separate
    # never get this. ---
    is_joint_relief = (pl.col("filing_status") == "married_joint") & (pl.col("de_agi") > 0)
    df = df.with_columns(
        de_agih=pl.max_horizontal(pl.col("pwages"), pl.col("swages")) + 0.5 * (pl.col("de_agi") - pl.col("wages")),
    )
    df = df.with_columns(de_agiw=pl.col("de_agi") - pl.col("de_agih"))
    df = df.with_columns(
        de_dedh=pl.when(pl.col("de_agi") != 0).then(pl.col("de_deduc") * pl.col("de_agih") / pl.col("de_agi")).otherwise(0.0)
    )
    df = df.with_columns(de_dedw=pl.col("de_deduc") - pl.col("de_dedh"))
    df = df.with_columns(
        de_taxinh=(pl.col("de_agih") - pl.col("de_dedh") - 0.5 * pl.col("de_exemp")).clip(0, None),
        de_taxinw=(pl.col("de_agiw") - pl.col("de_dedw") - 0.5 * pl.col("de_exemp")).clip(0, None),
    )

    # --- Bracket tax ---
    year_table_map = {
        (1977, 1978): "brackets_1977_1978",
        (1979, 1979): "brackets_1979",
        (1980, 1984): "brackets_1980_1984",
        (1985, 1985): "brackets_1985",
        (1986, 1986): "brackets_1986",
        (1987, 1987): "brackets_1987",
        (1988, 1995): "brackets_1988_1995",
        (1996, 1996): "brackets_1996",
        (1997, 1998): "brackets_1997_1998",
        (1999, 1999): "brackets_1999",
        (2000, 2009): "brackets_2000_2009",
        (2010, 2011): "brackets_2010_2011",
        (2012, 2013): "brackets_2012_2013",
    }
    key = "brackets_2014plus"
    for (lo, hi), k in year_table_map.items():
        if lo <= effective_year <= hi:
            key = k
            break
    brackets = p[key]
    stat = bracket_tax(pl.col("de_taxinc"), brackets)
    stat_h = bracket_tax(pl.col("de_taxinh"), brackets)
    stat_w = bracket_tax(pl.col("de_taxinw"), brackets)
    df = df.with_columns(de_statax=pl.when(is_joint_relief).then(pl.min_horizontal(stat, stat_h + stat_w)).otherwise(stat))

    # --- Child/Dependent Care Credit ---
    ccc_rate = float(resolve_year(p["child_care_credit_rate"], effective_year))
    df = df.with_columns(de_chcr=pl.col("federal_chcr").clip(0, None) * ccc_rate)
    if effective_year >= 1999:
        cap = float(resolve_year(p["child_care_credit_cap_1999plus"], effective_year))
        df = df.with_columns(de_chcr=pl.min_horizontal(pl.col("de_chcr"), cap))

    # --- Personal Exemption Credit (1996+) --- the energy credit
    # (`data(38)`) is not a TAXSIM input, so `credit` is the child care
    # credit plus the personal-exemption credit.
    if effective_year >= 1996:
        per_unit = float(resolve_year(p["personal_exemption_credit_per_unit"], effective_year))
        df = df.with_columns(de_pecred=(pl.col("de_num") + aged_count()) * per_unit)
    else:
        df = df.with_columns(de_pecred=pl.lit(0.0))

    df = df.with_columns(de_credit=pl.col("de_chcr") + pl.col("de_pecred"))
    df = df.with_columns(de_statax=(pl.col("de_statax") - pl.col("de_credit")).clip(0, None))

    # --- EITC (2006+) --- see module docstring.
    if 2006 <= effective_year <= 2020:
        rate = float(p["eitc_nonrefundable_rate_2006_2020"][2006])
        df = df.with_columns(de_earncr=rate * pl.col("eitc").clip(0, None))
        df = df.with_columns(de_statax=(pl.col("de_statax") - pl.col("de_earncr")).clip(0, None))
    elif effective_year >= 2021:
        refundable_rate = float(p["eitc_refundable_rate_2021plus"][2021])
        nonrefundable_rate = float(p["eitc_nonrefundable_rate_2021plus"][2021])
        refundable_amt = refundable_rate * pl.col("eitc").clip(0, None)
        nonrefundable_amt = nonrefundable_rate * pl.col("eitc").clip(0, None)
        use_refundable = refundable_amt > pl.col("de_statax")
        df = df.with_columns(de_earncr=pl.when(use_refundable).then(refundable_amt).otherwise(nonrefundable_amt))
        df = df.with_columns(
            de_statax=pl.when(use_refundable)
            .then(pl.col("de_statax") - pl.col("de_earncr"))
            .otherwise((pl.col("de_statax") - pl.col("de_earncr")).clip(0, None))
        )
    else:
        df = df.with_columns(de_earncr=pl.lit(0.0))

    df = df.with_columns(siitax=pl.col("de_statax") * flate)
    return with_state_detail(
        df,
        agi=pl.col("de_agi"),
        exemptions=pl.col("de_exemp"),
        standard_deduction=pl.col("de_stded"),
        itemized_deductions=pl.col("de_xitded"),
        taxable_income=pl.col("de_taxinc"),
        child_care_credit=pl.col("de_chcr"),
        eic=pl.col("de_earncr"),
        credits=pl.col("de_credit") + pl.col("de_earncr"),
        rate=pl.when(is_joint_relief).then(bracket_rate(pl.col("de_taxinw"), brackets)).otherwise(bracket_rate(pl.col("de_taxinc"), brackets)),
    )
