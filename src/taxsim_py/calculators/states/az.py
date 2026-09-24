"""Arizona individual income tax calculator."""

import polars as pl

from taxsim_py.engine.brackets import bracket_tax
from taxsim_py.engine.eitc import trapezoid_credit
from taxsim_py.engine.schema import PARAMETERS_ROOT, load_yaml, resolve_year
from taxsim_py.engine.state import with_defaults, with_default as _with_default
from taxsim_py.engine.state_extrapolation import deflate_for_extrapolation, resolve_state_year

AZ_PARAMS = load_yaml(PARAMETERS_ROOT / "states" / "az" / "income_tax.yaml")
PRE1987_PARAMS = load_yaml(PARAMETERS_ROOT / "national" / "pre1987.yaml")
STATE_ADJUSTMENT_PARAMS = load_yaml(PARAMETERS_ROOT / "national" / "state_adjustments.yaml")
_PRE1987_STATUSES = ["single", "married_joint", "married_separate", "head_of_household"]

_RICH_STATUSES = ["married_joint", "head_of_household"]  # mst.eq.2/4/7 in the source

def compute_az_tax(df: pl.DataFrame, year: int) -> pl.DataFrame:
    effective_year, flate = resolve_state_year(year)
    p = AZ_PARAMS
    df = with_defaults(df, ("proptax", "otheritem", "mortgage", "dividends", "ltcg", "intrec", "depx", "dep17"))

    # Year>LASTAT (2021): deflate every dollar-valued raw/federal-computed
    # input by `flate`, run 2021's REAL law (`effective_year`, forced to
    # 2021 by `resolve_state_year`) on the deflated figures, then reinflate
    # the final tax below (see engine/state_extrapolation.py). A no-op for
    # year<=2021 (`flate==1`). Since `effective_year` is always forced to
    # exactly 2021 for an extrapolated year, only the >=2019 branches below
    # are ever reached - so the columns that actually matter here are
    # `agi` (federal AGI, comnew(2)) and `salt_capped`/`mortgage` (feed
    # `az_xitded_base`); the rest are included defensively for consistency
    # with the other state retrofits even though this module's own
    # >=2019-only branches don't read them. `salt_capped` is federal.py's
    # own derived (real-year, undeflated) column, same situation as AR's
    # `wages` - deflating `proptax`/`otheritem` after the fact wouldn't
    # reach it, so it's deflated directly here instead.
    df = deflate_for_extrapolation(
        df,
        flate,
        [
            "agi", "salt_capped", "mortgage", "pwages", "swages", "dividends",
            "proptax", "otheritem", "ltcg", "intrec",
        ],
    )

    df = df.with_columns(
        az_sep=pl.when(pl.col("filing_status") == "married_separate").then(2.0).otherwise(1.0),
        az_rich=pl.col("filing_status").is_in(_RICH_STATUSES),
    )
    # `data(7)` itself (self/spouse exemption unit count, 1 or 2 for
    # joint) - used directly (unbumped) by the excise credit and the
    # 2019+ Dependent Tax Credit phaseout, per the source's own literal
    # `data(7)` reads there (confirmed via a live oracle probe showing
    # `head_of_household`'s `txp` bump must NOT apply to those two).
    df = df.with_columns(
        az_txp_raw=pl.when(pl.col("filing_status") == "married_joint").then(2.0).otherwise(1.0)
    )
    # local `txp` (bumped +1 for head_of_household specifically) - used by
    # the exemption/standard-deduction formulas and the Family Income
    # Credit.
    df = df.with_columns(
        az_txp=pl.when(pl.col("filing_status") == "married_joint")
        .then(2.0)
        .otherwise(1.0)
        + pl.when(pl.col("filing_status") == "head_of_household").then(1.0).otherwise(0.0)
    )
    # `nchild` (`data(8)`=depx), forced to 0 for 2019+ (dependent exemption
    # repeal).
    df = df.with_columns(az_nchild=pl.lit(0.0) if effective_year >= 2019 else pl.col("depx").cast(pl.Float64))

    # --- AGI ---
    if effective_year <= 1980:
        divexc_expr = pl.lit(None, dtype=pl.Float64)
        for status in _PRE1987_STATUSES:
            fed_divexc = float(resolve_year(PRE1987_PARAMS["dividend_exclusion"][status], effective_year))
            divexc_expr = pl.when(pl.col("filing_status") == status).then(pl.lit(fed_divexc)).otherwise(divexc_expr)
        dividends_plus_fudge = pl.col("dividends") + 0.001
        div_addback = pl.min_horizontal(dividends_plus_fudge, divexc_expr).clip(0, None)
    else:
        div_addback = pl.lit(0.0)
    df = df.with_columns(az_div_addback=div_addback)

    if 1982 <= effective_year <= 1986:
        two_earner_rate = float(resolve_year(PRE1987_PARAMS["two_earner_deduction_rate"], effective_year))
        two_earner_cap = float(resolve_year(PRE1987_PARAMS["two_earner_deduction_cap"], effective_year))
        df = df.with_columns(
            az_twoded_addback=pl.when(pl.col("filing_status") == "married_joint")
            .then((two_earner_rate * pl.min_horizontal(pl.col("pwages"), pl.col("swages")).clip(0, None)).clip(0, two_earner_cap))
            .otherwise(0.0)
        )
    else:
        df = df.with_columns(az_twoded_addback=pl.lit(0.0))

    df = df.with_columns(
        az_agi_1=pl.col("agi") + pl.col("az_div_addback") + pl.col("az_twoded_addback")
    )
    # federal tax subtracted from AGI directly for <=1989 (NOT an itemized
    # addition like Alabama) - `fedtax=max(0,comnew(1)+comnew(59)+comnew(58))`
    # where comnew(1)=fiitax (net of all credits), comnew(59)=earncr (EITC),
    # comnew(58)=credit (the general nonrefundable-credit pool - child/
    # dependent care credit, and, for 1977-1978 only, the General Tax
    # Credit found while re-validating against taxsim_2024_09_21.f - see
    # federal_pre1987.py's own `gencr`). Previously approximated as $0
    # (this project's schema never populates childcare-credit-eligible
    # inputs for AZ's own test patterns), which stayed correct until
    # `gencr` gave `credit` a real, nonzero value for plain wages-only
    # 1977-1978 filers too - now added back for real via the `credit`
    # column federal_pre1987.py exposes.
    df = _with_default(df, "credit")
    if effective_year <= 1986:
        rate_in = float(resolve_year(PRE1987_PARAMS["eitc_rate_in"], effective_year))
        max_credit = float(resolve_year(PRE1987_PARAMS["eitc_max_credit"], effective_year))
        phaseout_start = float(resolve_year(PRE1987_PARAMS["eitc_phaseout_start"], effective_year))
        rate_out = float(resolve_year(PRE1987_PARAMS["eitc_rate_out"], effective_year))
        az_earned = (pl.col("wages") + pl.col("psemp").clip(0, None) + pl.col("ssemp").clip(0, None)).clip(0, None)
        az_earncr_raw = trapezoid_credit(az_earned, pl.col("agi"), rate_in, max_credit, phaseout_start, rate_out)
        df = df.with_columns(
            az_earncr=pl.when((pl.col("filing_status") == "married_separate") | (pl.col("dep18") == 0))
            .then(0.0)
            .otherwise(az_earncr_raw)
        )
    elif effective_year <= 1989:
        df = df.with_columns(az_earncr=pl.col("eitc"))
    else:
        df = df.with_columns(az_earncr=pl.lit(0.0))

    if effective_year <= 1989:
        df = df.with_columns(az_fedtax=(pl.col("fiitax") + pl.col("az_earncr") + pl.col("credit")).clip(0, None))
        df = df.with_columns(az_agi=(pl.col("az_agi_1") - pl.col("az_fedtax")))
    else:
        df = df.with_columns(az_agi=pl.col("az_agi_1"))
    df = df.with_columns(az_ag=pl.col("az_agi").clip(0, None))

    # --- Personal/dependent exemption (5 formula eras) ---
    if effective_year <= 1989:
        pe = float(resolve_year(p["personal_exemption_amount_pre1990"], effective_year))
        de = float(resolve_year(p["dependent_exemption_amount_pre1990"], effective_year))
        hoh_backout = float(resolve_year(p["hoh_exemption_backout_pre1979"], effective_year))
        aif = float(resolve_year(p["pre1990_inflation"], effective_year))
        df = df.with_columns(
            az_exemp=((pl.col("az_txp") * pe + pl.col("az_nchild") * de) * aif)
            - (
                pl.when((pl.col("filing_status") == "head_of_household") & (effective_year <= 1978))
                .then(pl.col("az_nchild").clip(0, 1) * hoh_backout * aif)
                .otherwise(0.0)
            )
        )
    elif effective_year in (1990, 1991):
        pe = float(resolve_year(p["personal_exemption_amount_1990_1991"], effective_year))
        df = df.with_columns(az_exemp=(pl.col("az_txp") + pl.col("az_nchild")) * pe)
    elif effective_year == 1992:
        pe = float(resolve_year(p["personal_exemption_amount_1992"], effective_year))
        df = df.with_columns(az_exemp=(pl.col("az_txp") + pl.col("az_nchild")) * pe)
    elif 1993 <= effective_year <= 1996:
        pe = float(resolve_year(p["personal_exemption_amount_1993_1996"], effective_year))
        de = float(resolve_year(p["dependent_exemption_amount_1993_1996"], effective_year))
        df = df.with_columns(az_exemp=pl.col("az_txp") * pe + pl.col("az_nchild") * de)
    elif 1997 <= effective_year <= 2018:
        pe_single = float(resolve_year(p["personal_exemption_amount_1997_2018_single"], effective_year))
        pe_joint_kids = float(resolve_year(p["personal_exemption_amount_1997_2018_joint_with_kids"], effective_year))
        de = float(resolve_year(p["dependent_exemption_amount_1997_2018"], effective_year))
        # `if((mst.eq.2.or.sep.eq.2).and.nchild.gt.0) exemp=txp*xmph(law)` -
        # mst.eq.2 is married_joint, sep.eq.2 is mst.eq.3.or.mst.eq.6 (and
        # mst never reaches 3 in this project's own mst-code mapping, so
        # in practice this is married_joint OR married_separate - NOT
        # head_of_household, which is a separate mst code (4/7) and always
        # keeps the single/xmps rate here despite getting its own +1 `txp`
        # bump elsewhere). Confirmed via a live oracle probe.
        use_joint_rate = pl.col("filing_status").is_in(["married_joint", "married_separate"]) & (
            pl.col("az_nchild") > 0
        )
        df = df.with_columns(
            az_exemp=pl.col("az_txp") * pl.when(use_joint_rate).then(pe_joint_kids).otherwise(pe_single)
            + pl.col("az_nchild") * de
        )
    else:  # 2019+ - confirmed $0 (see YAML note)
        df = df.with_columns(az_exemp=pl.lit(0.0))

    # --- Standard deduction (3 eras) ---
    if effective_year <= 1989:
        pct = float(resolve_year(p["standard_deduction_pct_pre1990"], effective_year))
        cap = float(resolve_year(p["standard_deduction_cap_per_exemption_pre1990"], effective_year))
        aif = float(resolve_year(p["pre1990_inflation"], effective_year))
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
        per_exemption = float(resolve_year(p["standard_deduction_per_exemption"], effective_year))
        df = df.with_columns(az_stded=pl.col("az_txp") * per_exemption)
    else:
        flat = float(resolve_year(p["standard_deduction_flat"], effective_year))
        coef = (
            pl.when(pl.col("filing_status") == "married_joint")
            .then(2.0)
            .when(pl.col("filing_status") == "head_of_household")
            .then(1.5)
            .otherwise(1.0)
        )
        df = df.with_columns(az_stded=coef * flat)

    # --- Itemized deduction: federal's own raw itemized total (before
    # federal's OWN Pease reduction), minus the SALT/state-tax component
    # (AZ doesn't allow deducting state tax on the state return), plus AZ's
    # own childcare deduction (pre-1991 only, gated on a low-income test),
    # with AZ's own, separate Pease-style phaseout for 1991-2017. ---
    df = df.with_columns(az_raw_itemized=pl.col("proptax") + pl.col("otheritem") + pl.col("mortgage"))
    if effective_year <= 1990:
        df = _with_default(df, "childcare")
        df = _with_default(df, "wages")
        df = df.with_columns(
            az_hy=pl.col("pwages") + pl.col("swages") + pl.col("dividends")
        )
        df = df.with_columns(
            az_childcare_ded=pl.when(pl.col("az_hy") < 6000.0 / pl.col("az_sep"))
            .then(pl.col("childcare").clip(0, 1200))
            .otherwise(0.0)
        )
        # `xitded = comnew(30)+health-data(50)-comnew(20)+...+data(54)+...`
        # - comnew(30) [the raw federal itemized total] already includes
        # `otheritem` (data 54) once, and this era's own formula adds it a
        # SECOND time on top - a real double-count, confirmed via a live
        # oracle probe (proptax=4000/otheritem=2000/mortgage=8000, 1977 ->
        # xitded=16000=4000+8000+2*2000), not a bug to "fix away".
        df = df.with_columns(
            az_xitded=(pl.col("az_raw_itemized") + pl.col("otheritem") + pl.col("az_childcare_ded")).clip(0, None)
        )
    else:
        # `xitded=comnew(30)` directly here (no `-data(50)` subtraction,
        # unlike the <=1990 branch) - AZ conforms to the FULL federal
        # itemized total INCLUDING state/local tax paid, pre-Pease (a real,
        # self-referential mechanic: AZ's own tax liability, fed back as
        # `state_sales_or_income_tax_ded` by the 3-iteration federal/state
        # loop, becomes deductible against itself here). Reuses federal.py's
        # own `salt_capped` (correctly TCJA-$10k-capped for 2018+) rather
        # than re-summing proptax+otheritem+SALT unclipped.
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
                thr_expr = pl.lit(None, dtype=pl.Float64)
                for status in _PRE1987_STATUSES:
                    v = float(p["itemized_phaseout_income_2013_2017"][status]) * aif13
                    thr_expr = pl.when(pl.col("filing_status") == status).then(pl.lit(v)).otherwise(thr_expr)
                df = df.with_columns(az_phas=thr_expr)
            df = df.with_columns(
                az_reduce_raw=pl.when(pl.col("az_agi") > pl.col("az_phas"))
                .then(
                    pl.min_horizontal(
                        0.8 * pl.col("az_xitded_base"), 0.03 * (pl.col("az_agi") - pl.col("az_phas"))
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

    # --- Bracket tax: married_joint/HoH income-split for 2019+ only;
    # otherwise a real, separate table for single-or-separate vs
    # joint-or-HoH (and, 1990+, HoH gets its own table distinct from joint
    # too). ---
    if effective_year <= 1989:
        brkif = float(resolve_year(p["pre1990_bracket_inflation"], effective_year))
        brackets = p["brackets_pre1990"]
        df = df.with_columns(az_regtax=brkif * bracket_tax(pl.col("az_taxinc") / brkif, brackets))
    elif effective_year >= 2019:
        aif19 = float(resolve_year(p["bracket_inflation_2019plus"], effective_year))
        brackets = p["brackets_2019plus"]
        df = df.with_columns(
            az_taxy=pl.when(pl.col("az_rich")).then(pl.col("az_taxinc") / 2).otherwise(pl.col("az_taxinc"))
        )
        df = df.with_columns(az_stat=aif19 * bracket_tax(pl.col("az_taxy") / aif19, brackets))
        df = df.with_columns(az_regtax=pl.when(pl.col("az_rich")).then(pl.col("az_stat") * 2).otherwise(pl.col("az_stat")))
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
        aif15 = float(resolve_year(p["bracket_inflation_2015_2018"], effective_year))
        brackets_single = p[f"brackets_{key}_single"]
        brackets_rich = p[f"brackets_{key}_joint_or_hoh"]
        df = df.with_columns(
            az_regtax=aif15
            * pl.when(pl.col("az_rich"))
            .then(bracket_tax(pl.col("az_taxinc") / aif15, brackets_rich))
            .otherwise(bracket_tax(pl.col("az_taxinc") / aif15, brackets_single))
        )

    # --- Credits ---
    # Family Income Credit (1995+, non-refundable).
    if effective_year >= 1995:
        per_exemption = float(resolve_year(p["family_credit_per_exemption"], effective_year))
        thr_single = float(resolve_year(p["family_credit_agi_threshold_single_or_separate"], effective_year))
        thr_rich = float(resolve_year(p["family_credit_agi_threshold_joint_or_hoh"], effective_year))
        cap_single = float(resolve_year(p["family_credit_cap_single_or_separate"], effective_year))
        cap_rich = float(resolve_year(p["family_credit_cap_joint_or_hoh"], effective_year))
        df = df.with_columns(
            az_fagi=pl.when(pl.col("az_rich")).then(thr_rich).otherwise(thr_single),
            az_famlim=pl.when(pl.col("az_rich")).then(cap_rich).otherwise(cap_single),
        )
        if effective_year >= 1998:
            joint_bp = [float(v) for v in resolve_year(p["family_credit_agi_threshold_joint_by_dep_count_1998plus"], effective_year)]
            hoh_bp = [float(v) for v in resolve_year(p["family_credit_agi_threshold_hoh_by_dep_count_1998plus"], effective_year)]
            joint_fagi = (
                pl.when(pl.col("depx") <= 1).then(joint_bp[0])
                .when(pl.col("depx") == 2).then(joint_bp[1])
                .when(pl.col("depx") == 3).then(joint_bp[2])
                .otherwise(joint_bp[3])
            )
            hoh_fagi = (
                pl.when(pl.col("depx") <= 0).then(hoh_bp[0])
                .when(pl.col("depx") == 1).then(hoh_bp[1])
                .when(pl.col("depx") == 2).then(hoh_bp[2])
                .when(pl.col("depx") == 3).then(hoh_bp[3])
                .otherwise(hoh_bp[4])
            )
            df = df.with_columns(
                az_fagi=pl.when(pl.col("filing_status") == "married_joint")
                .then(joint_fagi)
                .when(pl.col("filing_status") == "head_of_household")
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
        per_ctc = float(resolve_year(p["dependent_tax_credit_per_ctc_child"], effective_year))
        per_other = float(resolve_year(p["dependent_tax_credit_per_other_dependent"], effective_year))
        phaseout_agi_per_exemption = float(resolve_year(p["dependent_tax_credit_phaseout_agi_per_exemption"], effective_year))
        phaseout_rate = float(resolve_year(p["dependent_tax_credit_phaseout_rate_per_1000"], effective_year))
        df = df.with_columns(
            az_ctc_raw=per_ctc * pl.col("dep17") + per_other * (pl.col("depx") - pl.col("dep17")).clip(0, None)
        )
        df = df.with_columns(az_cphase=phaseout_agi_per_exemption * pl.col("az_txp_raw"))
        df = df.with_columns(
            az_ctc=(pl.col("az_ctc_raw") - ((pl.col("az_agi") - pl.col("az_cphase")).clip(0, None) / 1000.0) * phaseout_rate).clip(0, None)
        )
    else:
        df = df.with_columns(az_ctc=pl.lit(0.0))

    df = df.with_columns(az_after_famcr=(pl.col("az_regtax") - pl.col("az_famcr") - pl.col("az_ctc")).clip(0, None))

    # Credit For Increased Excise Taxes (2001+, refundable).
    if effective_year >= 2001:
        thr = float(resolve_year(p["excise_credit_agi_threshold_per_exemption"], effective_year))
        per = float(resolve_year(p["excise_credit_per_exemption_or_dependent"], effective_year))
        cap = float(resolve_year(p["excise_credit_cap"], effective_year))
        numb = pl.when(pl.col("filing_status") == "head_of_household").then(2.0).otherwise(pl.col("az_txp_raw"))
        df = df.with_columns(
            az_excise=pl.when(pl.col("az_agi") <= thr * numb)
            .then(pl.min_horizontal(per * (pl.col("az_txp_raw") + pl.col("depx")), cap))
            .otherwise(0.0)
        )
    else:
        df = df.with_columns(az_excise=pl.lit(0.0))

    df = df.with_columns(siitax=(pl.col("az_after_famcr") - pl.col("az_excise")).clip(None, None) * flate)
    return df
