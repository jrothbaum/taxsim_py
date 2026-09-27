"""Arizona individual income tax calculator."""

import polars as pl

from taxsim_py.engine.brackets import bracket_rate, bracket_tax
from taxsim_py.engine.eitc import trapezoid_credit
from taxsim_py.engine.inputs import aged_count, files_head_of_household, files_joint, files_separate, is_dependent_filer, separate_divisor, taxpayer_count
from taxsim_py.engine.schema import PARAMETERS_ROOT, YearParams, load_yaml, resolve_year
from taxsim_py.engine.state import by_filing_status, dividend_exclusion_addback, household_income, interpolate_table, with_state_detail
from taxsim_py.engine.state_extrapolation import deflate_for_extrapolation, resolve_state_year

AZ_PARAMS = load_yaml(PARAMETERS_ROOT / "states" / "az" / "income_tax.yaml")
PRE1987_PARAMS = load_yaml(PARAMETERS_ROOT / "national" / "pre1987.yaml")
STATE_ADJUSTMENT_PARAMS = load_yaml(PARAMETERS_ROOT / "national" / "state_adjustments.yaml")
_PRE1987_STATUSES = ["single", "married_joint", "married_separate", "head_of_household"]

_RICH_STATUSES = ["married_joint", "head_of_household"]  # mst.eq.2/4/7 in the source

def compute_az_tax(df: pl.DataFrame, year: int) -> pl.DataFrame:
    effective_year, flate = resolve_state_year(year)
    p = YearParams(AZ_PARAMS, effective_year)
    df = df.with_columns(
        az_household_income=household_income()
    )

    df = deflate_for_extrapolation(df, flate, extra=("az_household_income",))

    df = df.with_columns(
        az_sep=separate_divisor(),
        az_rich=pl.col("filing_status").is_in(_RICH_STATUSES),
    )
    # Taxpayers (`data(7)`), read by the excise credit and the 2019+
    # dependent credit phaseout.
    df = df.with_columns(az_txp_raw=taxpayer_count())
    # `txp`: taxpayers with head of household counted as 2, used by the
    # exemption, standard deduction and Family Income Credit.
    df = df.with_columns(
        az_txp=pl.col("az_txp_raw")
        + pl.when(files_head_of_household()).then(1.0).otherwise(0.0)
    )
    # `nchild` (`data(8)`=depx), forced to 0 for 2019+ (dependent exemption
    # repeal).
    df = df.with_columns(az_nchild=pl.lit(0.0) if effective_year >= 2019 else pl.col("depx").cast(pl.Float64))

    # --- AGI ---
    if effective_year <= 1980:
        div_addback = dividend_exclusion_addback(effective_year)
    else:
        div_addback = pl.lit(0.0)
    df = df.with_columns(az_div_addback=div_addback)

    if 1982 <= effective_year <= 1986:
        two_earner_rate = float(resolve_year(PRE1987_PARAMS["two_earner_deduction_rate"], effective_year))
        two_earner_cap = float(resolve_year(PRE1987_PARAMS["two_earner_deduction_cap"], effective_year))
        df = df.with_columns(
            az_twoded_addback=pl.when(files_joint())
            .then((two_earner_rate * pl.min_horizontal(pl.col("pwages"), pl.col("swages")).clip(0, None)).clip(0, two_earner_cap))
            .otherwise(0.0)
        )
    else:
        df = df.with_columns(az_twoded_addback=pl.lit(0.0))

    # Social Security benefits are exempt from 1984.
    exempt_ss = pl.col("taxable_social_security") if effective_year >= 1984 else pl.lit(0.0)
    df = df.with_columns(
        az_agi_1=pl.col("agi") + pl.col("az_div_addback") + pl.col("az_twoded_addback") - exempt_ss
    )
    # Through 1989 federal tax is subtracted from AGI:
    # `fedtax=max(0,comnew(1)+comnew(59)+comnew(58))`, federal income tax
    # plus the EITC and nonrefundable credits.
    if effective_year <= 1986:
        rate_in = float(resolve_year(PRE1987_PARAMS["eitc_rate_in"], effective_year))
        max_credit = float(resolve_year(PRE1987_PARAMS["eitc_max_credit"], effective_year))
        phaseout_start = float(resolve_year(PRE1987_PARAMS["eitc_phaseout_start"], effective_year))
        rate_out = float(resolve_year(PRE1987_PARAMS["eitc_rate_out"], effective_year))
        az_earned = (pl.col("wages") + pl.col("psemp").clip(0, None) + pl.col("ssemp").clip(0, None)).clip(0, None)
        az_earncr_raw = trapezoid_credit(az_earned, pl.col("agi"), rate_in, max_credit, phaseout_start, rate_out)
        df = df.with_columns(
            az_earncr=pl.when((files_separate()) | (pl.col("dep18") == 0))
            .then(0.0)
            .otherwise(az_earncr_raw)
        )
    elif effective_year <= 1989:
        df = df.with_columns(az_earncr=pl.col("eitc"))
    else:
        df = df.with_columns(az_earncr=pl.lit(0.0))

    if effective_year <= 1989:
        credit = pl.col("credit") if effective_year <= 1986 else pl.col("nonrefundable_credits")
        df = df.with_columns(az_fedtax=(pl.col("fiitax") + pl.col("az_earncr") + credit).clip(0, None))
        df = df.with_columns(az_agi=(pl.col("az_agi_1") - pl.col("az_fedtax")))
    else:
        df = df.with_columns(az_agi=pl.col("az_agi_1"))
    df = df.with_columns(az_ag=pl.col("az_agi").clip(0, None))

    # --- Personal/dependent exemption (5 formula eras) ---
    if effective_year <= 1989:
        pe = p.num("personal_exemption_amount_pre1990")
        de = p.num("dependent_exemption_amount_pre1990")
        hoh_backout = p.num("hoh_exemption_backout_pre1979")
        aif = p.num("pre1990_inflation")
        df = df.with_columns(
            az_exemp=((pl.col("az_txp") * pe + pl.col("az_nchild") * de) * aif)
            - (
                pl.when((files_head_of_household()) & (effective_year <= 1978))
                .then(pl.col("az_nchild").clip(0, 1) * hoh_backout * aif)
                .otherwise(0.0)
            )
        )
    elif effective_year in (1990, 1991):
        pe = p.num("personal_exemption_amount_1990_1991")
        df = df.with_columns(az_exemp=(pl.col("az_txp") + pl.col("az_nchild")) * pe)
    elif effective_year == 1992:
        pe = p.num("personal_exemption_amount_1992")
        df = df.with_columns(az_exemp=(pl.col("az_txp") + pl.col("az_nchild")) * pe)
    elif 1993 <= effective_year <= 1996:
        pe = p.num("personal_exemption_amount_1993_1996")
        de = p.num("dependent_exemption_amount_1993_1996")
        df = df.with_columns(az_exemp=pl.col("az_txp") * pe + pl.col("az_nchild") * de)
    elif 1997 <= effective_year <= 2018:
        pe_single = p.num("personal_exemption_amount_1997_2018_single")
        pe_joint_kids = p.num("personal_exemption_amount_1997_2018_joint_with_kids")
        de = p.num("dependent_exemption_amount_1997_2018")
        # `if((mst.eq.2.or.sep.eq.2).and.nchild.gt.0) exemp=txp*xmph(law)`:
        # joint and separate returns with children; head of household uses
        # the single amount.
        use_joint_rate = pl.col("filing_status").is_in(["married_joint", "married_separate"]) & (
            pl.col("az_nchild") > 0
        )
        df = df.with_columns(
            az_exemp=pl.col("az_txp") * pl.when(use_joint_rate).then(pe_joint_kids).otherwise(pe_single)
            + pl.col("az_nchild") * de
        )
    else:  # No personal exemption from 2019.
        df = df.with_columns(az_exemp=pl.lit(0.0))
    # Each taxpayer 65 or older adds an exemption.
    if effective_year <= 1989:
        aged_amount = p.num("personal_exemption_amount_pre1990") * float(
            p.value("pre1990_inflation")
        )
    else:
        aged_amount = p.num("aged_exemption")
    df = df.with_columns(az_exemp=pl.col("az_exemp") + aged_amount * aged_count())

    # --- Standard deduction (3 eras) ---
    if effective_year <= 1989:
        pct = p.num("standard_deduction_pct_pre1990")
        cap = p.num("standard_deduction_cap_per_exemption_pre1990")
        aif = p.num("pre1990_inflation")
        if effective_year <= 1983:
            fctr = round(aif * 10.0) / 10.0
        elif effective_year in (1984, 1985):
            fctr = round(aif * 100.0) / 100.0
        else:
            fctr = aif
        df = df.with_columns(
            az_stded=(pct * fctr * pl.col("az_agi")).clip(0, cap * pl.col("az_txp") * aif)
        )
    elif effective_year <= 2018:
        per_exemption = p.num("standard_deduction_per_exemption")
        df = df.with_columns(az_stded=pl.col("az_txp") * per_exemption)
    else:
        flat = p.num("standard_deduction_flat")
        multiple = p["standard_deduction_multiple"]
        coef = (
            pl.when(files_joint())
            .then(float(multiple["married_joint"]))
            .when(files_head_of_household())
            .then(float(multiple["head_of_household"]))
            .otherwise(float(multiple["other"]))
        )
        df = df.with_columns(az_stded=coef * flat)

    # --- Itemized deduction: federal gross itemized deductions less state
    # income tax, plus Arizona's child care deduction (through 1990), with
    # Arizona's own limitation 1991-2017. ---
    df = df.with_columns(az_raw_itemized=pl.col("proptax") + pl.col("otheritem") + pl.col("mortgage"))
    if effective_year <= 1990:
        df = df.with_columns(
            az_childcare_ded=pl.when(
                pl.col("az_household_income") < p["child_care_deduction_pre1991"]["income_limit"] / pl.col("az_sep")
            )
            .then(pl.col("childcare").clip(0, p["child_care_deduction_pre1991"]["cap"]))
            .otherwise(0.0)
        )
        # `xitded = comnew(30)+...-data(50)+...+data(54)`: other taxes
        # (`data(54)`) are counted twice, once inside `comnew(30)`.
        df = df.with_columns(
            az_xitded=(pl.col("az_raw_itemized") + pl.col("otheritem") + pl.col("az_childcare_ded")).clip(0, None)
        )
    else:
        # `xitded=comnew(30)`: the federal gross itemized total, including
        # the state tax deduction.
        df = df.with_columns(az_xitded_base=pl.col("salt_capped") + pl.col("mortgage"))
        if 1991 <= effective_year <= 2017:
            if effective_year <= 2012:
                aif92 = float(
                    resolve_year(
                        STATE_ADJUSTMENT_PARAMS["itemized_phaseout_inflation"],
                        effective_year,
                    )
                )
                threshold = float(p["itemized_phaseout_income_pre2013"][1991]) * aif92
                df = df.with_columns(az_phas=threshold / pl.col("az_sep"))
            else:
                aif13 = float(
                    resolve_year(
                        STATE_ADJUSTMENT_PARAMS["itemized_phaseout_inflation"],
                        effective_year,
                    )
                )
                thresholds = p["itemized_phaseout_income_2013_2017"]
                df = df.with_columns(az_phas=by_filing_status({s: float(thresholds[s]) * aif13 for s in _PRE1987_STATUSES}))
            df = df.with_columns(
                az_reduce_raw=pl.when(pl.col("az_agi") > pl.col("az_phas"))
                .then(
                    pl.min_horizontal(
                        p["itemized_phaseout_cap_rate"] * pl.col("az_xitded_base"),
                        p["itemized_phaseout_rate"] * (pl.col("az_agi") - pl.col("az_phas")),
                    )
                )
                .otherwise(0.0)
            )
            if effective_year in (2006, 2007):
                mult = 2.0 / 3.0
            elif effective_year in (2008, 2009):
                mult = 1.0 / 3.0
            elif 2010 <= effective_year <= 2012:
                mult = 0.0
            else:
                mult = 1.0
            df = df.with_columns(az_xitded=(pl.col("az_xitded_base") - mult * pl.col("az_reduce_raw")).clip(0, None))
        else:
            df = df.with_columns(az_xitded=pl.col("az_xitded_base"))

    df = df.with_columns(az_deduc=pl.max_horizontal(pl.col("az_stded"), pl.col("az_xitded")))
    df = df.with_columns(az_taxinc=(pl.col("az_agi") - pl.col("az_deduc") - pl.col("az_exemp")).clip(0, None))

    # --- Bracket tax: from 2019 joint and head of household returns split
    # income; before that separate tables by filing status. ---
    if effective_year <= 1989:
        brkif = p.num("pre1990_bracket_inflation")
        brackets = p["brackets_pre1990"]
        df = df.with_columns(az_regtax=brkif * bracket_tax(pl.col("az_taxinc") / brkif, brackets))
        rate_expr = bracket_rate(pl.col("az_taxinc") / brkif, brackets)
    elif effective_year >= 2019:
        aif19 = p.num("bracket_inflation_2019plus")
        brackets = p["brackets_2019plus"]
        df = df.with_columns(
            az_taxy=pl.when(pl.col("az_rich")).then(pl.col("az_taxinc") / 2).otherwise(pl.col("az_taxinc"))
        )
        df = df.with_columns(az_stat=aif19 * bracket_tax(pl.col("az_taxy") / aif19, brackets))
        df = df.with_columns(az_regtax=pl.when(pl.col("az_rich")).then(pl.col("az_stat") * 2).otherwise(pl.col("az_stat")))
        rate_expr = bracket_rate(pl.col("az_taxy") / aif19, brackets)
    else:
        era_key = {
            (1990, 1993): "1990_1993",
            (1994, 1994): "1994",
            (1995, 1996): "1995_1996",
            (1997, 1997): "1997",
            (1998, 1998): "1998",
            (1999, 2005): "1999_2005",
            (2006, 2006): "2006",
            (2007, 2018): "2007_2018",
        }
        key = None
        for (lo, hi), k in era_key.items():
            if lo <= effective_year <= hi:
                key = k
                break
        aif15 = p.num("bracket_inflation_2015_2018")
        brackets_single = p[f"brackets_{key}_single"]
        brackets_rich = p[f"brackets_{key}_joint_or_hoh"]
        df = df.with_columns(
            az_regtax=aif15
            * pl.when(pl.col("az_rich"))
            .then(bracket_tax(pl.col("az_taxinc") / aif15, brackets_rich))
            .otherwise(bracket_tax(pl.col("az_taxinc") / aif15, brackets_single))
        )
        rate_expr = pl.when(pl.col("az_rich")).then(bracket_rate(pl.col("az_taxinc") / aif15, brackets_rich)).otherwise(
            bracket_rate(pl.col("az_taxinc") / aif15, brackets_single)
        )

    # --- Credits ---
    # Family Income Credit (1995+, non-refundable).
    if effective_year >= 1995:
        per_exemption = p.num("family_credit_per_exemption")
        thr_single = p.num("family_credit_agi_threshold_single_or_separate")
        thr_rich = p.num("family_credit_agi_threshold_joint_or_hoh")
        cap_single = p.num("family_credit_cap_single_or_separate")
        cap_rich = p.num("family_credit_cap_joint_or_hoh")
        df = df.with_columns(
            az_fagi=pl.when(pl.col("az_rich")).then(thr_rich).otherwise(thr_single),
            az_famlim=pl.when(pl.col("az_rich")).then(cap_rich).otherwise(cap_single),
        )
        if effective_year >= 1998:
            joint_bp = [float(v) for v in p.value("family_credit_agi_threshold_joint_by_dep_count_1998plus")]
            hoh_bp = [float(v) for v in p.value("family_credit_agi_threshold_hoh_by_dep_count_1998plus")]
            joint_fagi = (
                pl.when(pl.col("depx") <= 1).then(joint_bp[0])
                .when(pl.col("depx") == 2).then(joint_bp[1])
                .when(pl.col("depx") == 3).then(joint_bp[2])
                .otherwise(joint_bp[3])
            )
            # A.R.S. 43-1073; TAXSIM stops at four dependents and never
            # reads the five-or-more limit.
            hoh_fagi = (
                pl.when(pl.col("depx") <= 1).then(hoh_bp[0])
                .when(pl.col("depx") == 2).then(hoh_bp[1])
                .when(pl.col("depx") == 3).then(hoh_bp[2])
                .when(pl.col("depx") == 4).then(hoh_bp[3])
                .otherwise(hoh_bp[4])
            )
            df = df.with_columns(
                az_fagi=pl.when(files_joint())
                .then(joint_fagi)
                .when(files_head_of_household())
                .then(hoh_fagi)
                .otherwise(pl.col("az_fagi"))
            )
        df = df.with_columns(
            az_famcr=pl.when(pl.col("az_agi") <= pl.col("az_fagi"))
            .then(pl.min_horizontal(pl.col("az_famlim"), (pl.col("depx") + pl.col("az_txp_raw")) * per_exemption))
            .otherwise(0.0)
        )
    else:
        df = df.with_columns(az_famcr=pl.lit(0.0))

    # 2019+ Dependent Tax Credit (non-refundable).
    if effective_year >= 2019:
        per_ctc = p.num("dependent_tax_credit_per_ctc_child")
        per_other = p.num("dependent_tax_credit_per_other_dependent")
        phaseout_agi_per_exemption = p.num("dependent_tax_credit_phaseout_agi_per_exemption")
        phaseout_rate = p.num("dependent_tax_credit_phaseout_rate_per_1000")
        df = df.with_columns(
            az_ctc_raw=per_ctc * pl.col("dep17") + per_other * (pl.col("depx") - pl.col("dep17")).clip(0, None),
            az_cphase=phaseout_agi_per_exemption * pl.col("az_txp_raw"),
        )
        df = df.with_columns(
            az_ctc=(pl.col("az_ctc_raw") - ((pl.col("az_agi") - pl.col("az_cphase")).clip(0, None) / 1000.0) * phaseout_rate).clip(0, None)
        )
    else:
        df = df.with_columns(az_ctc=pl.lit(0.0))

    df = df.with_columns(az_after_famcr=(pl.col("az_regtax") - pl.col("az_famcr") - pl.col("az_ctc")).clip(0, None))

    # Credit For Increased Excise Taxes (2001+, refundable).
    if effective_year >= 2001:
        thr = p.num("excise_credit_agi_threshold_per_exemption")
        per = p.num("excise_credit_per_exemption_or_dependent")
        cap = p.num("excise_credit_cap")
        numb = pl.when(files_head_of_household()).then(2.0).otherwise(pl.col("az_txp_raw"))
        df = df.with_columns(
            # Tested against federal AGI (`comnew(2)`).
            az_excise=pl.when((pl.col("agi") <= thr * numb) & ~is_dependent_filer())
            .then(pl.min_horizontal(per * (pl.col("az_txp_raw") + pl.col("depx")), cap))
            .otherwise(0.0)
        )
    else:
        df = df.with_columns(az_excise=pl.lit(0.0))

    # Refundable property tax credit (taxpayers 65 or older with pension or
    # Social Security income) or renter credit, whichever is larger.
    more = (pl.col("filing_status") != "single") | (pl.col("depx") > 0)
    shift = pl.when(more).then(float(p["property_tax_credit_family_addition"])).otherwise(0.0)
    if effective_year <= 1989:
        aif = p.num("pre1990_inflation")
        amounts = [aif * float(v) for v in p["property_tax_credit_pre1990"]]
    else:
        amounts = [float(v) for v in p["property_tax_credit_1990"]]
    rows = [[float(t) + shift, a] for t, a in zip(p["property_tax_credit_thresholds"], amounts)] + [[1.0e20, 0.0]]
    pensions = pl.col("pensions") + pl.col("gssi")
    property_credit = pl.when((aged_count() > 0) & (pensions > 0)).then(
        interpolate_table(pl.col("az_household_income"), rows)
    ).otherwise(0.0)
    if effective_year <= 1991:
        rate = p.num("renter_credit_rate")
        cap = p.num("renter_credit_cap")
        renter_credit = (rate * pl.col("rentpaid")).clip(0, cap)
        if effective_year >= 1990:
            renter_credit = pl.when(pl.col("az_agi") <= p["renter_credit_agi_limit_1990"]).then(
                renter_credit.clip(None, p["renter_credit_max_1990"])
            ).otherwise(0.0)
    else:
        renter_credit = pl.lit(0.0)
    df = df.with_columns(az_credit=pl.max_horizontal(property_credit, renter_credit))

    df = df.with_columns(siitax=(pl.col("az_after_famcr") - pl.col("az_excise") - pl.col("az_credit")) * flate)
    return with_state_detail(
        df,
        agi=pl.col("az_agi"),
        exemptions=pl.col("az_exemp"),
        standard_deduction=pl.col("az_stded"),
        itemized_deductions=pl.col("az_xitded"),
        taxable_income=pl.col("az_taxinc"),
        property_credit=property_credit,
        credits=pl.col("az_credit") + pl.col("az_excise") + pl.col("az_famcr") + pl.col("az_ctc"),
        rate=rate_expr,
    )
