"""District of Columbia individual income tax calculator."""

import polars as pl

from taxsim_py.engine.brackets import bracket_rate, bracket_tax
from taxsim_py.engine.inputs import aged_count, is_dependent_filer, taxpayer_count
from taxsim_py.engine.schema import PARAMETERS_ROOT, load_yaml, resolve_year
from taxsim_py.engine.state import by_filing_status as _by_status, dividend_exclusion_addback, forced_standard, household_income, with_default as _with_default, with_defaults, with_state_detail
from taxsim_py.engine.state_extrapolation import deflate_for_extrapolation, resolve_state_year

DC_PARAMS = load_yaml(PARAMETERS_ROOT / "states" / "dc" / "income_tax.yaml")
STATE_ADJUSTMENT_PARAMS = load_yaml(PARAMETERS_ROOT / "national" / "state_adjustments.yaml")
FEDERAL_INCOME_TAX_PARAMS = load_yaml(PARAMETERS_ROOT / "national" / "income_tax.yaml")
FEDERAL_PERSONAL_EXEMPTION_PARAMS = load_yaml(PARAMETERS_ROOT / "national" / "personal_exemption.yaml")
PRE1987_PARAMS = load_yaml(PARAMETERS_ROOT / "national" / "pre1987.yaml")
_PRE1987_STATUSES = ["single", "married_joint", "married_separate", "head_of_household"]

def compute_dc_tax(df: pl.DataFrame, year: int) -> pl.DataFrame:
    effective_year, flate = resolve_state_year(year)
    p = DC_PARAMS
    df = with_defaults(df, ("proptax", "otheritem", "mortgage", "depx", "dep17", "dividends", "intrec", "ui", "pui", "sui", "childcare"))
    df = _with_default(df, "state_sales_or_income_tax_ded")
    df = _with_default(df, "ccc")
    df = _with_default(df, "ccc_uncapped")
    df = _with_default(df, "eitc")
    df = _with_default(df, "stcg")
    df = _with_default(df, "ltcg")
    df = _with_default(df, "taxable_unemployment")
    # `itemizes`/`itemized_deduction`/`salt_capped` aren't exposed by
    # federal_pre1987.py (years<=1986) - the <=1986 itemized-deduction
    # branch below never reads them (uses the raw proptax+otheritem+
    # mortgage total directly instead), but they need to exist to avoid
    # a column-not-found error before that branch is reached.
    df = _with_default(df, "itemizes", False)
    df = _with_default(df, "itemized_deduction")

    df = df.with_columns(
        dc_sep=pl.when(pl.col("filing_status") == "married_separate").then(2.0).otherwise(1.0),
        # `data(7)` - self/spouse exemption unit count (1, or 2 ONLY for
        # married_joint), used by the exemption formula.
        dc_texp=taxpayer_count(),
    )

    # Year>LASTAT (2021): deflate every dollar-valued raw/federal-computed
    # input by `flate`, run 2021's REAL law (`effective_year`, forced to
    # 2021 by `resolve_state_year`) on the deflated figures, then reinflate
    # the final tax below (see engine/state_extrapolation.py). A no-op for
    # year<=2021 (`flate==1`). `salt_capped`/`state_sales_or_income_tax_ded`
    # /`itemized_deduction`/`earned_income` are federal.py's own derived
    # (real-year, undeflated) columns - deflated directly here, same
    # situation as AR's `wages`/AZ's `salt_capped`/California's
    # `earned_income` elsewhere in this project.
    df = with_defaults(df, ("taxable_social_security", "standard_deduction", "tax_before_credits"))
    df = df.with_columns(
        dc_household_income=household_income(
            float(resolve_year(STATE_ADJUSTMENT_PARAMS["household_income_dividend_adjustment"], effective_year)),
            float(resolve_year(STATE_ADJUSTMENT_PARAMS["household_income_record_adjustment"], effective_year)),
        )
    )
    df = deflate_for_extrapolation(
        df,
        flate,
        [
            "taxable_social_security", "nonprop", "rentpaid", "dc_household_income", "standard_deduction",
            "pwages", "swages", "wages", "proptax", "otheritem", "mortgage", "dividends", "intrec",
            "stcg", "ltcg", "ui", "pui", "sui", "agi", "salt_capped", "state_sales_or_income_tax_ded",
            "itemized_deduction", "earned_income", "eitc", "ccc",
        ],
    )

    # --- AGI ---
    if effective_year <= 1981:
        adjustment = float(resolve_year(STATE_ADJUSTMENT_PARAMS["household_income_dividend_adjustment"], effective_year))
        addit = dividend_exclusion_addback(effective_year, adjustment)
        # Other non-property income is not DC income before 1982.
        df = df.with_columns(
            dc_agi=pl.col("agi") + addit - pl.col("taxable_unemployment") - pl.col("nonprop")
        )
    else:
        df = df.with_columns(dc_agi=pl.col("agi"))
        if 1982 <= effective_year <= 1986:
            two_earner_rate = float(resolve_year(PRE1987_PARAMS["two_earner_deduction_rate"], effective_year))
            two_earner_cap = float(resolve_year(PRE1987_PARAMS["two_earner_deduction_cap"], effective_year))
            twoded = (
                two_earner_rate * pl.min_horizontal(pl.col("pwages"), pl.col("swages")).clip(0, None)
            ).clip(0, two_earner_cap)
            df = df.with_columns(dc_agi=pl.col("dc_agi") + twoded)
        # Social Security benefits are exempt from 1984.
        if effective_year >= 1984:
            df = df.with_columns(dc_agi=pl.col("dc_agi") - pl.col("taxable_social_security"))

    # Unemployment-compensation exclusion - UNCONDITIONAL in the source
    # (no `if(law...)` gate despite the "For 2021+" comment - see module
    # docstring point 1). `ui_total` matches federal.py's own
    # `max(ui, pui+sui)` (pui/sui are a SPLIT of ui, not additional
    # income - the same lesson learned building Delaware).
    ui_total = pl.max_horizontal(pl.col("ui"), pl.col("pui") + pl.col("sui"))
    df = df.with_columns(dc_agi=pl.col("dc_agi") - ui_total)

    # --- Standard deduction ---
    if effective_year <= 1981:
        cap = float(p["standard_deduction_cap_pre1982"][1960])
        df = df.with_columns(dc_stded=(0.1 * pl.col("dc_agi")).clip(0, cap / pl.col("dc_sep")))
    elif effective_year <= 1986:
        flat = float(p["standard_deduction_flat_1982_1986"][1982])
        df = df.with_columns(dc_stded=flat / pl.col("dc_sep"))
    elif effective_year <= 2005:
        flat = float(p["standard_deduction_flat_1987_2005"][1987])
        df = df.with_columns(dc_stded=flat / pl.col("dc_sep"))
    elif effective_year <= 2007:
        flat = float(p["standard_deduction_flat_2006_2007"][2006])
        df = df.with_columns(dc_stded=flat / pl.col("dc_sep"))
    elif effective_year <= 2009:
        flat = float(p["standard_deduction_flat_2008_2009"][2008])
        per_unit = float(p["standard_deduction_proptax_addback_per_exemption_unit_2008_2009"][2008])
        addback = pl.min_horizontal(pl.col("proptax"), pl.col("dc_texp") * per_unit)
        df = df.with_columns(dc_stded=flat / pl.col("dc_sep") + addback)
    elif effective_year <= 2012:
        flat = float(p["standard_deduction_flat_2010_2012"][2010])
        df = df.with_columns(dc_stded=flat / pl.col("dc_sep"))
    elif effective_year == 2013:
        flat = float(p["standard_deduction_flat_2013"][2013])
        df = df.with_columns(dc_stded=flat / pl.col("dc_sep"))
    elif effective_year == 2014:
        flat = float(p["standard_deduction_flat_2014"][2014])
        df = df.with_columns(dc_stded=flat / pl.col("dc_sep"))
    elif effective_year <= 2016:
        joint = float(p["standard_deduction_2015_2016_joint"][2015])
        hoh = float(p["standard_deduction_2015_2016_hoh"][2015])
        single_or_sep = float(p["standard_deduction_2015_2016_single_or_separate"][2015])
        df = df.with_columns(
            dc_stded=pl.when(pl.col("filing_status") == "married_joint")
            .then(joint)
            .when(pl.col("filing_status") == "head_of_household")
            .then(hoh)
            .otherwise(single_or_sep)
        )
    elif effective_year == 2017:
        joint = float(p["standard_deduction_2017_joint"][2017])
        hoh = float(p["standard_deduction_2017_hoh"][2017])
        single_or_sep = float(p["standard_deduction_2017_single_or_separate"][2017])
        df = df.with_columns(
            dc_stded=pl.when(pl.col("filing_status") == "married_joint")
            .then(joint)
            .when(pl.col("filing_status") == "head_of_household")
            .then(hoh)
            .otherwise(single_or_sep)
        )
    else:
        # `zbrack(nfile,law)/sep` - nfile: single=1, married_joint=2,
        # head_of_household=3, married_separate=2 (SAME column as joint,
        # per DC's own `filing(mst,1,2,3,2)` - reused directly from
        # federal's own standard_deduction, divided by `sep` for MFS).
        single_sd = float(resolve_year(FEDERAL_INCOME_TAX_PARAMS["standard_deduction"]["single"], effective_year))
        joint_sd = float(resolve_year(FEDERAL_INCOME_TAX_PARAMS["standard_deduction"]["married_joint"], effective_year))
        hoh_sd = float(resolve_year(FEDERAL_INCOME_TAX_PARAMS["standard_deduction"]["head_of_household"], effective_year))
        df = df.with_columns(
            dc_stded=pl.when(pl.col("filing_status") == "single")
            .then(single_sd)
            .when(pl.col("filing_status") == "head_of_household")
            .then(hoh_sd)
            .otherwise(joint_sd / pl.col("dc_sep"))
        )
    if effective_year >= 2018:
        # The federal amounts: extra for each taxpayer 65 or older, and the
        # dependent filer's limit.
        aged_std = _by_status({
            s: resolve_year(FEDERAL_INCOME_TAX_PARAMS["aged_standard_deduction"][s], effective_year) for s in _PRE1987_STATUSES
        })
        dep_p = FEDERAL_INCOME_TAX_PARAMS["dependent_standard_deduction"]
        earnkd = pl.col("wages") + (pl.col("psemp") + pl.col("ssemp")).clip(0, None) + float(
            resolve_year(dep_p["earned_income_addition"], effective_year)
        )
        std = pl.col("dc_stded") + aged_std * aged_count()
        std = pl.when(is_dependent_filer()).then(
            pl.min_horizontal(std, pl.max_horizontal(pl.lit(float(resolve_year(dep_p["minimum"], effective_year))), earnkd))
        ).otherwise(std)
        df = df.with_columns(dc_stded=std)
    df = df.with_columns(dc_deduc=pl.col("dc_stded"))

    # --- Itemized deduction --- (see module docstring point 2). Neither
    # `salt_capped` nor `itemized_deduction` is exposed by federal_
    # pre1987.py (years<=1986), so both eras that fall in that range
    # reconstruct the needed federal quantities locally rather than
    # reading them as columns - safe because federal had no Pease-style
    # itemized-deduction limitation before 1991, so federal's own PRE-
    # and POST-reduction totals (`comnew(30)`/`comnew(24)`) are identical
    # for any year in this range.
    if effective_year <= 1986:
        df = df.with_columns(
            dc_raw_itemized=pl.col("proptax") + pl.col("otheritem") + pl.col("mortgage")
            + pl.col("state_sales_or_income_tax_ded")
        )
    else:
        df = df.with_columns(dc_raw_itemized=pl.col("salt_capped") + pl.col("mortgage"))
    # `comnew(26).gt.0.and.comnew(30).gt.0` gates DC's OWN itemized
    # section - matching the comment right after it ("Taxpayer MUST
    # itemize deductions if he itemized on his Fed Return"), DC does NOT
    # compare `stded` vs `xitded` itself; it just inherits federal's own
    # itemize-or-standard choice wholesale. `itemizes` isn't exposed as a
    # column for years<=1986 (federal_pre1987.py), but federal's own
    # pre-1987 zero-bracket amount IS available directly from
    # `PRE1987_PARAMS["standard_deduction"]` (the same lookup federal_
    # pre1987.py itself uses internally, just never exposed as an output
    # column) - reconstructed here to replicate the real federal-side
    # comparison. Two earlier, wrong proxies were tried and rejected via
    # oracle probes before landing on this: `dc_raw_itemized>0` (any
    # itemizable amount, however small) over-triggered for low-income
    # filers whose only "itemizable" amount was the tiny self-referential
    # state-tax deduction; `dc_xitded>dc_stded` (DC's own two options
    # compared directly) over-triggered too, since DC's OWN standard
    # deduction is much smaller than federal's - a $30,000-wages/single/
    # 1980 case (self-referential itemized $2,164 > DC's own $1,000
    # standard, but nowhere near federal's much larger ~$2,300+ standard)
    # confirmed the real oracle does NOT itemize there.
    if effective_year <= 1986:
        # Federal itemized deductions (`comnew(24)`), zero when the federal
        # return does not itemize.
        df = _with_default(df, "pre1987_deduc")
        itemizing_gate = pl.col("pre1987_itemizes")
        df = df.with_columns(dc_raw_itemized=pl.col("pre1987_deduc"))
    else:
        itemizing_gate = pl.col("itemizes")

    if effective_year <= 1981:
        # `xitded=comnew(24)-max(comnew(23)-.15*agi,0)-data(60)-data(65)` -
        # `comnew(23)` confirmed $0 via a debug-instrumented oracle probe
        # (a $50,000-wages/no-itemized-inputs 1980 case), collapsing this
        # to `xitded=comnew(24)` directly (no reduction at all) - a real,
        # SELF-REFERENTIAL result: with zero real itemizable expenses,
        # DC's own itemized deduction here is driven ENTIRELY by the
        # federal deductibility of DC's own state tax liability, matching
        # the converged `xitded==comnew(24)==data(50)` the probe showed.
        df = df.with_columns(dc_xitded=pl.col("dc_raw_itemized"))
    elif 1982 <= effective_year <= 2017:
        salt_share = (pl.col("state_sales_or_income_tax_ded") / pl.col("dc_raw_itemized").clip(1e-9, None))
        df = df.with_columns(
            dc_xitded=(pl.col("itemized_deduction") * (1.0 - salt_share)).clip(0, None)
        )
    if 1982 <= effective_year <= 1986:
        # Confirmed via probe (1982, same all-zero-itemized case): here
        # `comnew(24)==comnew(30)` (no Pease pre-1987) makes the general
        # `itemized_deduction*(1-salt_share)` formula above algebraically
        # collapse to `raw_itemized - state_tax = proptax+otheritem+
        # mortgage` - so this era doesn't actually need the (unavailable)
        # `itemized_deduction`/`salt_capped` columns, just the local,
        # state-tax-EXCLUDED raw sum.
        df = df.with_columns(
            dc_xitded=(pl.col("dc_raw_itemized") - pl.col("state_sales_or_income_tax_ded")).clip(0, None)
        )
    if effective_year >= 2018:
        sttax_cap = 10000.0 / pl.col("dc_sep")
        sttax = pl.min_horizontal(
            sttax_cap, pl.col("proptax") + pl.col("state_sales_or_income_tax_ded") + pl.col("otheritem")
        )
        room_after_proptax_otheritem = sttax - (pl.col("proptax") + pl.col("otheritem"))
        df = df.with_columns(
            dc_xitded=(
                pl.col("dc_raw_itemized")
                - pl.min_horizontal(pl.col("state_sales_or_income_tax_ded"), room_after_proptax_otheritem)
            ).clip(0, None)
        )

    # Allowable itemized deductions phaseout for AGI>$200k/sep - applies
    # to EVERY era including <=1981 (a first pass only wired this into
    # the 2018+ branch, then a second pass only extended it to >=1982 -
    # in the source this check sits INSIDE the same outer per-record gate
    # as the whole itemized-deduction section, unconditional on which
    # era's own formula just ran. Caught via two debug-instrumented
    # oracle probes: a $800,000-AGI 2013 single filer showed the real,
    # converged internal `xitded` was exactly $30,000 [=.05*($800,000-
    # $200,000)] higher than the idtl-displayed value (missing for
    # 1982-2017); a $260,000-AGI 1980 single filer (self-referential
    # itemized only, no real itemizable inputs) showed the same $3,000-
    # short pattern for <=1981 specifically once the first fix only
    # covered >=1982). Medical/casualty terms (`comnew(20)`, `data(61)`)
    # confirmed permanently $0 for this schema (same family as AL's own
    # confirmed-inert casualty/misc fields), so this collapses to a flat
    # 5%-of-excess-AGI reduction.
    threshold = float(p["itemized_agi_reduction_threshold"][1960]) / pl.col("dc_sep")
    excess = (pl.col("dc_agi") - threshold).clip(0, None)
    df = df.with_columns(dc_xitded=(pl.col("dc_xitded") - 0.05 * excess).clip(0, None))

    # `xitded` is a single Fortran local, initialized to 0 BEFORE the
    # `if(comnew(26).gt.0.and.comnew(30).gt.0)` gate - when that gate is
    # false (not itemizing federally), the whole block above never runs
    # at all, so `xitded` stays exactly 0, not "computed but unused". A
    # first pass computed the formula unconditionally and only gated
    # whether `dc_deduc` READ it, which left the STALE nonzero value
    # visible to `dcstax`'s own, separately-computed `stded.ge.xitded`
    # comparison (see below) - silently forcing the married-joint earner-
    # split relief to itemize even when federal (and thus DC) didn't,
    # caught via a $30,000-wages/married_joint/1980 case with no
    # itemizable inputs at all. Zeroing `dc_xitded` here whenever
    # `itemizing_gate` is false fixes both the main deduction choice and
    # the split relief's own comparison in one place.
    df = df.with_columns(dc_xitded=pl.when(itemizing_gate).then(pl.col("dc_xitded")).otherwise(0.0))

    if effective_year == 1999:
        df = df.with_columns(dc_xitded=pl.when(forced_standard()).then(0.0).otherwise(pl.col("dc_xitded")))

    df = df.with_columns(
        dc_deduc=pl.when(itemizing_gate).then(pl.col("dc_xitded")).otherwise(pl.col("dc_deduc"))
    )

    # --- Exemption ---
    df = df.with_columns(dc_num=pl.col("dc_texp") + pl.col("depx") + aged_count())
    amnt = float(resolve_year(p["personal_exemption_amount"], effective_year))
    df = df.with_columns(dc_exemp=pl.col("dc_num") * amnt)
    df = df.with_columns(
        dc_exemp=pl.when(pl.col("filing_status") == "head_of_household")
        .then(pl.col("dc_exemp") + amnt)
        .otherwise(pl.col("dc_exemp"))
    )
    df = df.with_columns(dc_exemp=pl.when(is_dependent_filer()).then(0.0).otherwise(pl.col("dc_exemp")))
    if 2015 <= effective_year <= 2017:
        floor = float(p["exemption_phaseout_2015_2017_floor"][2015])
        ceiling = float(p["exemption_phaseout_2015_2017_ceiling"][2015])
        band = float(p["exemption_phaseout_2015_2017_band_per_sep"][2015]) / pl.col("dc_sep")
        ratio = (
            pl.when(pl.col("dc_agi") > ceiling)
            .then(1.0)
            .when(pl.col("dc_agi") > floor)
            .then(pl.min_horizontal(1.0, 0.02 * (pl.col("dc_agi") - floor) / band))
            .otherwise(0.0)
        )
        df = df.with_columns(dc_exemp=pl.col("dc_exemp") * (1.0 - ratio))

    df = df.with_columns(dc_taxinc=(pl.col("dc_agi") - pl.col("dc_deduc") - pl.col("dc_exemp")).clip(0, None))

    # --- Bracket tax ---
    year_table_map = {
        (1977, 1986): "brackets_pre1987",
        (1987, 1987): "brackets_1987",
        (1988, 1999): "brackets_1988_1999",
        (2000, 2000): "brackets_2000",
        (2001, 2004): "brackets_2001_2004",
        (2005, 2005): "brackets_2005",
        (2006, 2006): "brackets_2006",
        (2007, 2011): "brackets_2007_2011",
        (2012, 2014): "brackets_2012_2014",
        (2015, 2015): "brackets_2015",
    }
    key = "brackets_2016plus"
    for (lo, hi), k in year_table_map.items():
        if lo <= effective_year <= hi:
            key = k
            break
    brackets = p[key]
    df = df.with_columns(dc_statax=bracket_tax(pl.col("dc_taxinc"), brackets))
    rate_expr = bracket_rate(pl.col("dc_taxinc"), brackets)

    # --- Married-joint earner split (see module docstring point 4) ---
    is_joint_relief = (pl.col("filing_status") == "married_joint") & (pl.col("dc_agi") > 0)
    df = df.with_columns(
        dc_agih=pl.max_horizontal(pl.col("pwages"), pl.col("swages")) + 0.5 * (pl.col("dc_agi") - pl.col("wages")),
    )
    df = df.with_columns(dc_agiw=pl.col("dc_agi") - pl.col("dc_agih"))
    df = df.with_columns(dc_exemph=pl.col("dc_exemp") * pl.col("dc_agih") / pl.col("dc_agi").clip(1e-9, None))
    df = df.with_columns(dc_exempw=pl.col("dc_exemp") - pl.col("dc_exemph"))
    prefers_std = pl.col("dc_stded") >= pl.col("dc_xitded")
    dedh_base = pl.when(prefers_std).then(pl.col("dc_stded")).otherwise(pl.col("dc_xitded"))
    df = df.with_columns(dc_dedh=dedh_base * pl.col("dc_agih") / pl.col("dc_agi").clip(1e-9, None))
    df = df.with_columns(dc_dedw=dedh_base - pl.col("dc_dedh"))
    df = df.with_columns(
        dc_taxyh=(pl.col("dc_agih") - pl.col("dc_dedh") - pl.col("dc_exemph")).clip(0, None),
        dc_taxyw=(pl.col("dc_agiw") - pl.col("dc_dedw") - pl.col("dc_exempw")).clip(0, None),
    )
    split_brackets = p["brackets_2007_split"] if effective_year == 2007 else brackets
    dc_taxbc = bracket_tax(pl.col("dc_taxyh"), split_brackets) + bracket_tax(pl.col("dc_taxyw"), split_brackets)
    df = df.with_columns(
        dc_statax=pl.when(is_joint_relief).then(pl.min_horizontal(pl.col("dc_statax"), dc_taxbc)).otherwise(pl.col("dc_statax"))
    )

    # --- Credits ---
    # Child/Dependent Care Credit (1982+, see module docstring): a share of the
    # federal credit before its liability limit (`comnew(176)`), read
    # undeflated; TAXSIM leaves that slot at 0 before 1987.
    if effective_year <= 1981:
        df = df.with_columns(dc_chcr=pl.lit(0.0))
    elif effective_year <= 1988:
        rate = float(p["child_care_credit_rate_1982_1988"][1982])
        df = df.with_columns(dc_chcr=rate * pl.col("ccc_uncapped"))
    else:
        rate = float(p["child_care_credit_rate_1989plus"][1989])
        df = df.with_columns(dc_chcr=rate * pl.col("ccc_uncapped"))

    # Credit for D.C. campaign contributions (confirmed inert, comnew(25)
    # not confidently traced - see YAML scope note)
    df = df.with_columns(dc_polcr=pl.lit(0.0))
    df = df.with_columns(dc_credit=pl.col("dc_chcr") + pl.col("dc_polcr"))
    df = df.with_columns(dc_statax=(pl.col("dc_statax") - pl.col("dc_credit")).clip(0, None))

    # Property Tax Credit ("Schedule H"): property tax, or a share of rent
    # if larger, less a share of income; separate schedules for returns
    # with a taxpayer 65 or older. Dependent filers under 65 get none.
    aged = aged_count()
    hy = pl.col("dc_household_income")
    rent_share = float(resolve_year(p["property_credit_rent_share"], effective_year))
    ptax = pl.max_horizontal(rent_share * pl.col("rentpaid"), pl.col("proptax"))
    if effective_year <= 2013:
        cap = float(p["property_credit_cap_pre2014"][1960])
        aged_rate = p["property_credit_aged_base_rate"] + hy / p["property_credit_aged_rate_income"] / 100.0
        aged_pcred = (ptax - hy * aged_rate).clip(0, None)
        raw_pcred = (ptax - hy * (1.5 + hy / 8000.0) / 100.0).clip(0, None)
        young_pcred = pl.when(hy < 3000.0).then(0.95 * raw_pcred).otherwise(0.75 * raw_pcred)
        pcred = pl.when(aged > 0).then(aged_pcred).otherwise(young_pcred)
        pcred = pl.when(hy <= 20000.0).then(pl.min_horizontal(cap, pcred)).otherwise(0.0)
    else:
        table_years = p["property_credit_by_year_2014plus"]
        lookup_year = effective_year if effective_year in table_years else max(y for y in table_years if y <= effective_year)
        prop_cap, pagi_val, prlim_val = (float(v) for v in table_years[lookup_year])
        agix = pl.col("agi").clip(0, None)
        under_prop_cap = agix <= prop_cap
        if effective_year <= 2018:
            over_25k_pcred = pl.when(under_prop_cap).then((ptax - 0.04 * agix).clip(0, None)).otherwise(0.0)
        else:
            over_25k_pcred = (
                pl.when(agix < 52000.0)
                .then((ptax - 0.04 * agix).clip(0, None))
                .when(under_prop_cap)
                .then((ptax - 0.05 * agix).clip(0, None))
                .otherwise(0.0)
            )
        young = pl.when(agix < 25000.0).then((ptax - 0.03 * agix).clip(0, None)).otherwise(over_25k_pcred)
        aged_pcred = pl.when(agix <= pagi_val).then((ptax - 0.03 * agix).clip(0, None)).otherwise(0.0)
        pcred = pl.min_horizontal(prlim_val, pl.when(aged > 0).then(aged_pcred).otherwise(young))
    pcred = pl.when(is_dependent_filer() & (aged < 1)).then(0.0).otherwise(pcred)
    df = df.with_columns(dc_pcred=pcred)

    # Low Income Credit (1987-2017), when federal tax before credits is
    # zero: by filing status and number of aged taxpayers, plus an amount
    # per dependent and aged taxpayer. Dependent filers instead get DC tax
    # on the federal standard deduction less DC's.
    if 1987 <= effective_year <= 2017:
        joint_row = [float(v) for v in resolve_year(p["low_income_credit_joint_by_aged"], effective_year)]
        single_row = [float(v) for v in resolve_year(p["low_income_credit_single_by_aged"], effective_year)]
        hoh_row = [float(v) for v in resolve_year(p["low_income_credit_hoh_by_aged"], effective_year)]
        xtra = float(resolve_year(p["low_income_credit_per_dependent"], effective_year))
        is_joint = pl.col("filing_status") == "married_joint"
        dx2 = pl.when(is_joint).then(aged).otherwise(aged.clip(None, 2)).cast(pl.Int64)

        def pick(row: list[float]) -> pl.Expr:
            return pl.lit(pl.Series(row, dtype=pl.Float64)).gather(dx2.clip(0, len(row) - 1))

        base = (
            pl.when(pl.col("filing_status").is_in(["married_joint", "married_separate"])).then(pick(joint_row) / pl.col("dc_sep"))
            .when(pl.col("filing_status") == "single").then(pick(single_row))
            .otherwise(pick(hoh_row))
        )
        allow = pl.col("depx") + aged
        no_tax = pl.col("tax_before_credits") <= 0
        ycr = pl.when(no_tax & ~is_dependent_filer()).then(base + xtra * allow).otherwise(0.0)
        federal_std = pl.when(pl.col("itemizes")).then(0.0).otherwise(pl.col("standard_deduction"))
        gap = federal_std - pl.col("dc_stded")
        brackets = resolve_year(p["low_income_credit_dependent_brackets"], effective_year)
        dependent_ycr = pl.when(
            no_tax & is_dependent_filer() & (gap > 0) & (pl.col("agi") <= federal_std)
        ).then(bracket_tax(gap.clip(0, None), brackets)).otherwise(0.0)
        ycr = ycr + dependent_ycr
        # The dependent-filer lookup overwrites the reported rate; joint
        # returns end on the higher earner's share.
        dependent_y = pl.when(pl.col("filing_status") == "married_joint").then(
            pl.max_horizontal(pl.col("pwages"), pl.col("swages")) + (gap - pl.col("wages")) / 2.0
        ).otherwise(gap)
        rate_expr = pl.when(no_tax & is_dependent_filer() & (gap > 0)).then(
            bracket_rate(dependent_y, brackets)
        ).otherwise(rate_expr)
    else:
        ycr = pl.lit(0.0)
    df = df.with_columns(dc_ycr=ycr)
    stat1 = (pl.col("dc_statax") - pl.col("dc_ycr")).clip(0, None)

    # --- EITC (2000+) ---
    if effective_year < 2000:
        earncr = pl.lit(0.0)
    elif effective_year <= 2014:
        rate = float(resolve_year(
            {**p["eitc_rate_2000"], **p["eitc_rate_2001_2004"], **p["eitc_rate_2005_2007"], **p["eitc_rate_2008plus"]},
            effective_year,
        ))
        earncr = rate * pl.col("eitc").clip(0, None)
    else:
        rate = float(resolve_year(p["eitc_rate_2008plus"], effective_year))
        earncr_with_kids = rate * pl.col("eitc").clip(0, None)
        cr = float(resolve_year(p["eitc_childless_max_credit_by_year"], effective_year))
        am = float(resolve_year(p["eitc_childless_max_earned_by_year"], effective_year))
        ym = float(resolve_year(p["eitc_childless_phaseout_start_by_year"], effective_year))
        dylim = float(resolve_year(p["eitc_disqualified_income_limit_by_year"], effective_year))
        earned = pl.col("earned_income")
        agimax = pl.max_horizontal(earned, pl.col("agi"))
        phase_rate = 0.153 if effective_year == 2021 else 0.0765
        earncr_childless_raw = pl.when(agimax <= am).then(
            (pl.min_horizontal(cr, phase_rate * earned) - 0.0848 * (agimax - ym).clip(0, None)).clip(None, None)
        ).otherwise(0.0)
        # `disqy` (`comnew(159)`) does NOT include capital gains, unlike
        # this project's own federal EITC disqy or California's own
        # state-EITC disqy (both `stcg+ltcg+dividends+intrec`-style) -
        # confirmed via oracle probe: single/wages=$20,000/ltcg=$15,000/
        # 2021 gets a real, nonzero EIC ($208.21, matching this formula
        # with NO disqualification at all) despite ltcg alone exceeding
        # `dylim` ($10,000 for 2021) under the capital-gains-inclusive
        # formula, which would have wrongly zeroed it.
        disqy = pl.col("dividends").clip(0, None) + pl.col("intrec")
        no_childless_credit = (disqy > dylim) | (pl.col("dc_sep") == 2) | is_dependent_filer()
        if effective_year != 2021:
            no_childless_credit = no_childless_credit | (aged >= taxpayer_count())
        # Childless filers under 25 (or over 65 with a spouse under 25) do
        # not qualify; an unreported age (0) passes.
        older = pl.max_horizontal(pl.col("page"), pl.col("sage"))
        younger = pl.min_horizontal(pl.col("page"), pl.col("sage"))
        too_young = ((older > 0) & (older < 25)) | ((older > 65) & (younger > 0) & (younger < 25))
        no_childless_credit = no_childless_credit | too_young
        earncr_childless = pl.when(no_childless_credit).then(0.0).otherwise(earncr_childless_raw)
        earncr = pl.when(pl.col("depx") > 0).then(earncr_with_kids).otherwise(earncr_childless)
    df = df.with_columns(dc_earncr=earncr)
    stat2 = pl.col("dc_statax") - pl.col("dc_earncr")

    # Taxpayer may not claim both the Low Income Credit and the EITC -
    # whichever leaves a smaller final tax wins.
    df = df.with_columns(
        dc_statax=pl.min_horizontal(stat1, stat2),
        dc_credit=pl.col("dc_credit") + pl.when(stat1 < stat2).then(pl.col("dc_earncr")).otherwise(pl.col("dc_ycr")) + pl.col("dc_pcred"),
    )
    df = df.with_columns(dc_statax=(pl.col("dc_statax") - pl.col("dc_pcred")))

    df = df.with_columns(siitax=pl.col("dc_statax") * flate)
    return with_state_detail(
        df,
        agi=pl.col("dc_agi"),
        exemptions=pl.col("dc_exemp"),
        standard_deduction=pl.col("dc_stded"),
        itemized_deductions=pl.col("dc_xitded"),
        taxable_income=pl.col("dc_taxinc"),
        property_credit=pl.col("dc_pcred"),
        child_care_credit=pl.col("dc_chcr"),
        eic=pl.col("dc_earncr"),
        credits=pl.col("dc_credit"),
        rate=rate_expr,
    )
