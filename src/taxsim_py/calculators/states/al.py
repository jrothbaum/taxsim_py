"""Alabama individual income tax calculator."""

import polars as pl

from taxsim_py.engine.brackets import bracket_tax
from taxsim_py.engine.payroll_tax import self_employment_tax
from taxsim_py.engine.schema import PARAMETERS_ROOT, load_yaml, resolve_year
from taxsim_py.engine.state import with_defaults, with_default as _with_default
from taxsim_py.engine.state_extrapolation import deflate_for_extrapolation, resolve_state_year

AL_PARAMS = load_yaml(PARAMETERS_ROOT / "states" / "al" / "income_tax.yaml")
PRE1987_PARAMS = load_yaml(PARAMETERS_ROOT / "national" / "pre1987.yaml")
PAYROLL_PARAMS = load_yaml(PARAMETERS_ROOT / "national" / "payroll_tax.yaml")
_PRE1987_STATUSES = ["single", "married_joint", "married_separate", "head_of_household"]

def compute_al_tax(df: pl.DataFrame, year: int) -> pl.DataFrame:
    effective_year, flate = resolve_state_year(year)
    p = AL_PARAMS
    df = with_defaults(df, ("proptax", "otheritem", "mortgage", "dividends", "ltcg", "intrec", "ui", "pui", "sui", "psemp", "ssemp"))
    df = _with_default(df, "taxable_unemployment")

    df = df.with_columns(
        al_sep=pl.when(pl.col("filing_status") == "married_separate").then(2.0).otherwise(1.0)
    )
    # `data(7)` - self/spouse count ONLY (1 for single/HoH/married_separate,
    # 2 for married_joint), NOT including dependents - only married_joint
    # ever gets data(7)=2 (taxsim_2022_10_21.f:21160-21178, HoH's own input
    # code path leaves data(7) at its default 1). Used for the standard-
    # deduction cap and the 2007+ formula's `stmin` - a genuinely different
    # quantity from the personal-exemption dollar amount below (which DOES
    # give HoH the same $3,000 as married_joint).
    df = df.with_columns(
        al_exemps_count=pl.when(pl.col("filing_status") == "married_joint").then(2.0).otherwise(1.0)
    )

    # `setax` (comnew(175)) - the SAME `c(175)` figure `sstax` produces
    # for `fica`/`tfica` everywhere else (see engine/payroll_tax.py's own
    # `capped_se_tax`/`self_employment_tax` docstrings for the exact
    # cap-check shape and the OASDI/HI rate asymmetry). An earlier version
    # of this code, built against taxsim_2022_10_21.f, found AL's own
    # `setax` used FLAT, hardcoded 12.4%/2.9% rates for every year with HI
    # never wage-base-capped - a real quirk of that OLD source's own
    # `sstax`, confirmed via a live probe (1983, married_joint,
    # $20,000 wages/$40,000 psemp: real setax $3,106.80, only matching a
    # flat-rate formula). The taxsim_2024_09_21.f rewrite's `setax` is
    # just the ordinary, year-specific-rate `c(175)` like every other
    # state that reads it - confirmed via the same technique (1977,
    # single, psemp=$50,000, no wages: real setax $1,303.50, matching
    # 1977's own historical 7.0%/0.9% SE rates, not 12.4%/2.9%).
    #
    # Computed at the REAL `year`'s rates on REAL (undeflated) wages, NOT
    # `effective_year`/already-deflated wages - `setax` (like `untax`
    # below) is produced ONCE by the main federal dispatch's own `sstax`
    # call, at the real year, BEFORE `statax`'s own generic deflate loop
    # divides comnew(175) by flate (taxsim_2022_10_21.f:62-65). 2021's own
    # rates/wage-base differ from a later real year's by more than just the
    # CPI ratio, so "deflate wages then compute at 2021's rates" and
    # "compute at the real year's rates then deflate the result" are NOT
    # interchangeable - an earlier version of this retrofit did the former
    # and was off by $20-$339 on 2022/2023 married_separate/HoH cases,
    # caught by `deflate_for_extrapolation` below now including this
    # (already real-year-computed) result instead.
    wage_base = float(resolve_year(PAYROLL_PARAMS["oasdi_wage_base"], year))
    hi_wage_base = float(resolve_year(PAYROLL_PARAMS["hi_wage_base"], year))
    net_earnings_factor = float(resolve_year(PAYROLL_PARAMS["se_net_earnings_factor"], year))
    se_oasdi_rate = float(resolve_year(PAYROLL_PARAMS["se_oasdi_rate"], year))
    se_hi_rate = float(resolve_year(PAYROLL_PARAMS["se_hi_rate"], year))
    al_setax_p = self_employment_tax(
        pl.col("psemp"), pl.col("pwages"), wage_base, se_oasdi_rate, se_hi_rate, net_earnings_factor, hi_wage_base
    )
    al_setax_s = self_employment_tax(
        pl.col("ssemp"), pl.col("swages"), wage_base, se_oasdi_rate, se_hi_rate, net_earnings_factor, hi_wage_base
    )
    df = df.with_columns(al_setax=al_setax_p + al_setax_s)

    # AL exempts the unemployment compensation included in federal AGI.
    df = df.with_columns(al_taxable_ui=pl.col("taxable_unemployment"))

    # Year>LASTAT (2021): no real AL law exists in the oracle past this
    # point - deflate every dollar-valued raw/federal-computed input by
    # `flate`, run 2021's REAL law (`effective_year`, already forced to
    # 2021 by `resolve_state_year`) on the deflated figures, then reinflate
    # the final tax (see engine/state_extrapolation.py, taxsim_2022_10_21.f:
    # 44-65). A no-op for year<=2021 (`flate==1.0`). Everything below this
    # point reads only already-deflated columns EXCEPT `al_setax` and
    # `addmed`/`niit`/`tax_before_credits` (deliberately excluded - see
    # below).
    #
    # NOT every federal-computed figure gets deflated: the real
    # dispatcher's own generic scaling loop only covers `comnew(1:98)`
    # (taxsim_2022_10_21.f:62-65, `do 300 i=1,98`) - `comnew(154)` (used
    # by the >=2009 `al_fedtax` formula), `comnew(173)`=niit, `comnew(175)`
    # =setax, and `comnew(180)`=addmed are all OUTSIDE that range, so
    # `altax` receives them completely UNSCALED even in an extrapolated
    # year. Confirmed via oracle probe (2022, single, wages=$50,000): the
    # real `al_fedtax`/`al_fica_addback` only reconcile against the
    # REAL-YEAR (undeflated) `tax_before_credits`/`niit`/`addmed`/`setax` -
    # dividing them by `flate` (an earlier version of this retrofit did
    # exactly that, matching the natural "deflate everything federal
    # computed" assumption that holds for every OTHER column) overstated
    # the deduction and understated `siitax` by $1-$300 across 2022/2023.
    # `eitc`/`actc`/`ccc`/`odc`/`agi`/`fica` (comnew 59/93/53/81ish/2/75)
    # ARE all within 1-98, so those stay in the deflate list below.
    df = deflate_for_extrapolation(
        df,
        flate,
        [
            "pwages", "swages", "proptax", "otheritem", "mortgage", "dividends",
            "ltcg", "intrec", "ui", "pui", "sui", "psemp", "ssemp", "childcare",
            "agi", "fica", "odc", "ccc", "eitc", "actc", "fiitax", "al_taxable_ui",
        ],
    )

    # --- AGI ---
    # AL doesn't allow the federal above-the-line half-of-SE-tax AGI
    # deduction (real federally since 1990 - established elsewhere in this
    # project), so it's added BACK unconditionally regardless of year
    # (there's no year gate on this in the source besides the 2011-2012
    # special rate) - taxsim_2022_10_21.f:581-589. A real mechanism missed
    # on the first pass (its own `data(44)` term is correctly inert given
    # this project's schema, but `comnew(175)=setax` is real and populated
    # whenever psemp/ssemp is nonzero - wrongly judged fully inert together
    # with data(44) instead of checked separately). Found via a debug-
    # instrumented oracle build showing AL's own `agi` for a psemp=$50,000
    # case was exactly gross SE income, not federal AGI net of its own
    # half-SE-tax deduction.
    if effective_year in (2011, 2012):
        df = df.with_columns(
            al_setax_addback=pl.when(pl.col("al_setax") <= 14204.0)
            .then(0.5751 * pl.col("al_setax"))
            .otherwise(0.5 * pl.col("al_setax") + 1067.0)
        )
    else:
        df = df.with_columns(al_setax_addback=0.5 * pl.col("al_setax"))
    # Federal dividend-exclusion addback: real 1977-1986 (overlaps law79's
    # own federal range exactly, `divexc(data,comnew,law)` - an
    # unconditional function call in the source), negligible 1987+ (see
    # Illinois's own identical finding - calculators/states/il.py).
    #
    # NOTE there is no equivalent capital-gains addback for actual GAINS -
    # a first pass wrongly added one based on the source's own comment
    # ("Capital Gains are treated similar to Federal Taxes, except that
    # all gains are taxable and all losses are deductible") without
    # checking the actual formula: `if(comnew(6).lt.0) agi=agi+comnew(5)-
    # comnew(6)` only ever fires for a NET LOSS (comnew(6)=capgn<0), which
    # this project's schema never produces (stcg/ltcg both non-negative,
    # same limitation as calculators/federal.py) - for a real gain, AL's
    # own AGI keeps the FEDERAL exclusion intact, it does not add it back.
    # Caught by an oracle probe showing the extra addback overstated AL
    # taxable income by exactly the excluded amount.
    if effective_year <= 1986:
        divexc_expr = pl.lit(None, dtype=pl.Float64)
        for status in _PRE1987_STATUSES:
            fed_divexc = float(resolve_year(PRE1987_PARAMS["dividend_exclusion"][status], effective_year))
            divexc_expr = pl.when(pl.col("filing_status") == status).then(pl.lit(fed_divexc)).otherwise(divexc_expr)
        dividends_plus_fudge = pl.col("dividends") + 0.001
        if effective_year == 1981:
            dividends_plus_fudge = dividends_plus_fudge + pl.col("intrec")
        df = df.with_columns(al_dividend_addback=pl.min_horizontal(dividends_plus_fudge, divexc_expr).clip(0, None))
    else:
        df = df.with_columns(al_dividend_addback=pl.lit(0.0))
    df = df.with_columns(al_capgains_addback=pl.lit(0.0))

    # Federal two-earner deduction addback (real 1982-1986 only - AL
    # doesn't allow it federal itself did for those years).
    if 1982 <= effective_year <= 1986:
        two_earner_rate = float(resolve_year(PRE1987_PARAMS["two_earner_deduction_rate"], effective_year))
        two_earner_cap = float(resolve_year(PRE1987_PARAMS["two_earner_deduction_cap"], effective_year))
        df = df.with_columns(
            al_twoded_addback=pl.when(pl.col("filing_status") == "married_joint")
            .then((two_earner_rate * pl.min_horizontal(pl.col("pwages"), pl.col("swages")).clip(0, None)).clip(0, two_earner_cap))
            .otherwise(0.0)
        )
    else:
        df = df.with_columns(al_twoded_addback=pl.lit(0.0))

    df = df.with_columns(
        al_agi=pl.col("agi")
        + pl.col("al_dividend_addback")
        + pl.col("al_capgains_addback")
        + pl.col("al_twoded_addback")
        + pl.col("al_setax_addback")
        - pl.col("al_taxable_ui")
    )

    # --- Standard vs. itemized deduction ---
    df = df.with_columns(al_ag=pl.col("al_agi").clip(0, None))
    if effective_year <= 2006:
        pct = float(resolve_year(p["standard_deduction_pct"], effective_year))
        cap_per_exemption = float(resolve_year(p["standard_deduction_cap_per_exemption"], effective_year))
        df = df.with_columns(
            al_stded=(pct * pl.col("al_agi")).clip(0, cap_per_exemption * pl.col("al_exemps_count"))
        )
    else:
        income_floor = float(resolve_year(p["standard_deduction_2007_income_floor"], effective_year)) / 1.0
        income_ceiling = float(resolve_year(p["standard_deduction_2007_income_ceiling"], effective_year))
        stmin_per_exemption = float(resolve_year(p["standard_deduction_2007_min_per_exemption"], effective_year))
        max_single = float(resolve_year(p["standard_deduction_2007_max_single"], effective_year))
        max_hoh = float(resolve_year(p["standard_deduction_2007_max_hoh"], effective_year))
        max_joint_or_sep = float(resolve_year(p["standard_deduction_2007_max_joint_or_separate"], effective_year))
        df = df.with_columns(
            al_agimin=income_floor / pl.col("al_sep"),
            al_agimax=income_ceiling / pl.col("al_sep"),
            al_stmin=stmin_per_exemption * pl.col("al_exemps_count"),
        )
        df = df.with_columns(
            al_stmax=pl.when(pl.col("filing_status") == "single")
            .then(max_single)
            .when(pl.col("filing_status") == "head_of_household")
            .then(max_hoh)
            .otherwise(max_joint_or_sep / pl.col("al_sep"))
        )
        df = df.with_columns(al_excess=(pl.col("al_agi") - pl.col("al_agimin")).clip(0, None))
        df = df.with_columns(al_tga=(pl.col("al_stmax") - pl.col("al_stmin")) / (pl.col("al_agimax") - pl.col("al_agimin")))
        df = df.with_columns(
            al_stded=pl.col("al_stmax")
            - pl.min_horizontal(pl.col("al_excess"), pl.col("al_agimax") - pl.col("al_agimin")) * pl.col("al_tga")
        )

    # Itemized deduction: proptax + otheritem + mortgage (the only real
    # itemized categories reachable given this project's input schema -
    # see the YAML scope note), plus (1982+) half of wage FICA, plus half
    # of SE tax netted against itself (algebraically: 0.5*wage_fica +
    # 1.5*additional_medicare_tax - see module docstring's derivation),
    # plus SE tax itself in full.
    # `if(law.ge.1982) xitded=xitded+data(51)+data(54)` - the proptax/
    # otheritem addback is real for `>=1982` INCLUSIVE, not `>1982` (a
    # real off-by-one on the first pass: 1982 itself needs BOTH this
    # branch and the base mortgage-only formula, found via a live oracle
    # probe showing 1982's own itemized deduction case understated by
    # exactly proptax+otheritem).
    df = df.with_columns(al_mortgage=pl.col("mortgage"))
    if effective_year < 1982:
        df = df.with_columns(al_xitded_base=pl.col("al_mortgage"))
    else:
        df = df.with_columns(al_xitded_base=pl.col("al_mortgage") + pl.col("proptax") + pl.col("otheritem"))

    if effective_year >= 1982:
        df = df.with_columns(al_fica_addback=0.5 * (pl.col("fica") - pl.col("al_setax")) + pl.col("addmed"))
        df = df.with_columns(al_xitded=(pl.col("al_xitded_base") + pl.col("al_fica_addback") + pl.col("al_setax")).clip(0, None))
    else:
        df = df.with_columns(al_xitded=pl.col("al_xitded_base"))

    df = df.with_columns(al_deduc_before_fedtax=pl.max_horizontal(pl.col("al_stded"), pl.col("al_xitded")))

    # --- Federal-tax-paid deduction (`fedtax`) - three genuinely different
    # eras, all added on top of whichever of standard/itemized is larger,
    # not part of that choice itself. ---
    if effective_year <= 1999:
        df = df.with_columns(al_fedtax=pl.col("fiitax").clip(0, None))
    elif effective_year <= 2008:
        df = df.with_columns(al_fedtax=(pl.col("tax_before_credits") - pl.col("odc") - pl.col("ccc")).clip(0, None))
    else:
        # `fedtax = max(0, comnew(154)+comnew(173)-comnew(59)-comnew(93))`
        # (taxsim_2024_09_21.f:671), where comnew(154) was verified via
        # live probe (2018 HoH/CTC case) to equal comnew(52)-comnew(58) -
        # i.e. `tax_before_credits` net of nonrefundable credits, the SAME
        # `odc`/`ccc` subtraction as the <=2008 era - so the >=2009
        # formula is just the <=2008 one plus NIIT and the refundable-CTC
        # portion (`actc`) also subtracted (EITC was already subtracted
        # both eras).
        #
        # An earlier version of this code special-cased `effective_year==
        # 2021` as a one-time ARPA lookback (re-running federal at law
        # year 2020 for hypothetical prior-year EITC/CTC/CCC), matching an
        # OLDER oracle vintage (taxsim_2022_10_21.f) - taxsim_2024_09_21.f
        # has that entire mechanism commented out (lines 679-693) in favor
        # of the single unconditional >=2009 formula below. A first pass
        # at removing it also incorrectly dropped the `odc`/`ccc`
        # subtraction entirely, having been misled by 2021's own ARPA-era
        # full CTC refundability (`comnew(58)`==0 for every 2021 probe
        # case, since ARPA moved the whole credit into the refundable
        # `actc` bucket that year) into looking like comnew(154) was just
        # `tax_before_credits` with nothing subtracted - a 2018 HoH/CTC
        # probe (nonrefundable CTC present) caught the $25 (=$500*5%)
        # resulting understatement and confirmed the `-comnew(58)` term is
        # real for every other year. Bug confirmed via oracle probe: real
        # 2021 AL siitax for a single/wages=10000/ui=8000 case ($258.97)
        # only matches this general formula, not the 2020-lookback
        # variant - and since `resolve_state_year` always forces
        # `effective_year=2021` for extrapolated years, this also silently
        # broke every year>2021, which is what surfaced it during the
        # 2022/2023 extrapolation retrofit.
        df = df.with_columns(
            al_fedtax=(
                pl.col("tax_before_credits") - pl.col("odc") - pl.col("ccc") - pl.col("eitc") - pl.col("actc") + pl.col("niit")
            ).clip(0, None)
        )

    df = df.with_columns(al_deduc=pl.col("al_deduc_before_fedtax") + pl.col("al_fedtax"))

    # --- Exemptions ---
    df = df.with_columns(
        al_exemp_base=pl.when(pl.col("filing_status").is_in(["married_joint", "head_of_household"]))
        .then(3000.0)
        .otherwise(1500.0)
    )
    if effective_year <= 2006:
        dep_flat = float(resolve_year(p["dependent_exemption_flat"], effective_year))
        df = df.with_columns(al_dep_exemp=dep_flat * pl.col("depx"))
    else:
        high = float(resolve_year(p["dependent_exemption_2007_high"], effective_year))
        mid = float(resolve_year(p["dependent_exemption_2007_mid"], effective_year))
        low = float(resolve_year(p["dependent_exemption_2007_low"], effective_year))
        bp1 = float(resolve_year(p["dependent_exemption_2007_breakpoint1"], effective_year))
        bp2 = float(resolve_year(p["dependent_exemption_2007_breakpoint2"], effective_year))
        tga = (high - mid) / bp1
        tgb = (mid - low) / (bp2 - bp1)
        excesa = pl.col("al_agi").clip(0, bp1)
        excesb = (pl.col("al_agi") - bp1).clip(0, bp2 - bp1)
        df = df.with_columns(al_dep_exemp=(high - excesa * tga - excesb * tgb) * pl.col("depx"))
    df = df.with_columns(al_exemp=pl.col("al_exemp_base") + pl.col("al_dep_exemp"))

    df = df.with_columns(al_taxinc=(pl.col("al_agi") - pl.col("al_deduc") - pl.col("al_exemp")).clip(0, None))

    # --- Bracket tax: real income-splitting for married_joint (run half
    # taxable income through the table, double the result) - NOT the usual
    # "married_separate = joint/2" shape; married_separate uses its OWN
    # full taxable income directly, same as single/HoH. ---
    if effective_year <= 1981:
        brackets = p["brackets_pre1982"]
        df = df.with_columns(al_regtax=bracket_tax(pl.col("al_taxinc"), brackets))
    else:
        brackets = p["brackets"]
        df = df.with_columns(
            al_taxy=pl.when(pl.col("filing_status") == "married_joint").then(pl.col("al_taxinc") / 2).otherwise(pl.col("al_taxinc"))
        )
        df = df.with_columns(al_stat=bracket_tax(pl.col("al_taxy"), brackets))
        df = df.with_columns(
            al_regtax=pl.when(pl.col("filing_status") == "married_joint").then(pl.col("al_stat") * 2).otherwise(pl.col("al_stat"))
        )

    df = df.with_columns(siitax=pl.col("al_regtax").clip(0, None) * flate)
    return df
