"""Idaho individual income tax calculator."""

import polars as pl

from taxsim_py.calculators.federal_pre1987 import PRE1987_PARAMS
from taxsim_py.engine.brackets import bracket_rate, bracket_tax
from taxsim_py.engine.inputs import aged_count, is_dependent_filer, taxpayer_count
from taxsim_py.engine.schema import PARAMETERS_ROOT, load_yaml, resolve_year
from taxsim_py.engine.state import by_filing_status as _by_status, forced_standard, household_income, with_default as _with_default, with_defaults, with_state_detail
from taxsim_py.engine.state_extrapolation import deflate_for_extrapolation, resolve_state_year

ID_PARAMS = load_yaml(PARAMETERS_ROOT / "states" / "id" / "income_tax.yaml")
FEDERAL_INCOME_TAX_PARAMS = load_yaml(PARAMETERS_ROOT / "national" / "income_tax.yaml")
STATE_ADJUSTMENT_PARAMS = load_yaml(PARAMETERS_ROOT / "national" / "state_adjustments.yaml")
_STATUSES = ["single", "married_joint", "married_separate", "head_of_household"]
_SEPRET_BY_STATUS = {"single": 1.0, "married_joint": 1.0, "head_of_household": 1.0, "married_separate": 2.0}

def compute_id_tax(df: pl.DataFrame, year: int) -> pl.DataFrame:
    effective_year, flate = resolve_state_year(year)
    p = ID_PARAMS
    df = with_defaults(df, ("proptax", "otheritem", "mortgage", "depx", "dep17", "dividends", "intrec", "childcare", "ui", "pui", "sui"))
    df = _with_default(df, "state_sales_or_income_tax_ded")
    df = _with_default(df, "itemized_deduction")
    df = _with_default(df, "standard_deduction")
    df = _with_default(df, "earned_income")
    df = _with_default(df, "taxable_unemployment")

    df = df.with_columns(
        id_sep=pl.when(pl.col("filing_status") == "married_separate").then(2.0).otherwise(1.0),
        id_texp=taxpayer_count(),
    )

    # Year>LASTAT (2021): deflate every dollar-valued raw/federal-computed
    # input by `flate`, run 2021's REAL law (`effective_year`, forced to
    # 2021 by `resolve_state_year`) on the deflated figures, then reinflate
    # the final tax below (see engine/state_extrapolation.py). A no-op for
    # year<=2021 (`flate==1`).
    df = with_defaults(df, ("taxable_social_security", "pre1987_amex", "personal_exemptions", "qbi_deduction"))
    df = df.with_columns(
        id_household_income=household_income(
            float(resolve_year(STATE_ADJUSTMENT_PARAMS["household_income_dividend_adjustment"], effective_year)),
            float(resolve_year(STATE_ADJUSTMENT_PARAMS["household_income_record_adjustment"], effective_year)),
        )
    )
    df = deflate_for_extrapolation(
        df,
        flate,
        [
            "taxable_social_security", "pre1987_amex", "personal_exemptions", "id_household_income",
            "pwages", "swages", "proptax", "otheritem", "mortgage", "dividends", "intrec",
            "stcg", "ltcg", "ui", "pui", "sui", "agi", "salt_capped", "state_sales_or_income_tax_ded",
            "itemized_deduction", "standard_deduction", "earned_income", "childcare",
        ],
    )

    # --- AGI ---
    # The entire childcare-deduction/state-tax-refund/alt-energy/xjobs/SS
    # block is gated `if(law.ge.1978)` in the source - 1977 gets NO
    # adjustments at all, straight `agi=comnew(2)` (live-probe-confirmed:
    # a 1977/single/$25,000-wages/$2,000-childcare case's real tax implies
    # NO childcare deduction that year).
    df = df.with_columns(id_agi=pl.col("agi"))
    if effective_year >= 1978:
        if effective_year <= 2002:
            cc_cap = 2400.0 * pl.min_horizontal(pl.col("depx"), 2.0)
        else:
            cc_cap = 3000.0 * pl.min_horizontal(pl.col("depx"), 2.0)
        cc_ded = pl.min_horizontal(pl.col("childcare"), pl.col("earned_income"), cc_cap)
        df = df.with_columns(id_agi=pl.col("id_agi") - cc_ded)
        if effective_year >= 1984:
            df = df.with_columns(id_agi=pl.col("id_agi") - pl.col("taxable_social_security"))
    # `xjobs()` (1979-1986), Social Security in AGI (comnew(79)), and
    # `data(22)`/`data(38)` all confirmed permanently $0 for this schema.

    if effective_year == 2020:
        ui_total = pl.max_horizontal(pl.col("ui"), pl.col("pui") + pl.col("sui"))
        df = df.with_columns(
            id_agi=pl.col("id_agi") + ui_total - pl.col("taxable_unemployment")
        )

    # --- Itemized deduction --- (state-tax-declaration-phaseout formula,
    # same `comnew(24)*(1-data(50)/comnew(30))` scaling DC's own itemized
    # deduction uses). Neither `salt_capped` nor `itemized_deduction` is
    # exposed by federal_pre1987.py (years<=1986) - reconstructed locally
    # the same way DC/Georgia/Hawaii already do (safe: no Pease-style
    # limitation existed before 1991).
    if effective_year <= 1986:
        df = df.with_columns(
            id_raw_itemized=pl.col("proptax") + pl.col("otheritem") + pl.col("mortgage")
            + pl.col("state_sales_or_income_tax_ded")
        )
        id_itemized_deduction = pl.col("id_raw_itemized")
    else:
        df = df.with_columns(id_raw_itemized=pl.col("salt_capped") + pl.col("mortgage"))
        id_itemized_deduction = pl.col("itemized_deduction")
    df = df.with_columns(id_xitded=(pl.col("id_raw_itemized") - pl.col("state_sales_or_income_tax_ded")).clip(0, None))

    if 1991 <= effective_year <= 2017:
        base = float(p["itemized_phaseout_base"][1960])
        if effective_year <= 2012:
            aif92 = float(resolve_year(p["itemized_phaseout_aif92_1992_2012"], effective_year)) if effective_year >= 1992 else 1.0
            phas92 = base * aif92 / pl.col("id_sep")
        else:
            aif13 = float(resolve_year(p["itemized_phaseout_aif13_2013_2017"], effective_year))
            mult = p["itemized_phaseout_2013_2017_multiplier"]
            mult_expr = pl.lit(None, dtype=pl.Float64)
            for status, v in mult.items():
                mult_expr = pl.when(pl.col("filing_status") == status).then(pl.lit(float(v))).otherwise(mult_expr)
            phas92 = aif13 * 2.5 * base * mult_expr
        salt_share = pl.col("state_sales_or_income_tax_ded") / pl.col("id_raw_itemized").clip(1e-9, None)
        over_thr = (pl.col("id_agi") > phas92) & (pl.col("id_raw_itemized") > 0)
        df = df.with_columns(
            id_xitded=pl.when(over_thr)
            .then((id_itemized_deduction * (1.0 - salt_share)).clip(0, None))
            .otherwise(pl.col("id_xitded"))
        )

    # --- Standard deduction ---
    if effective_year == 1977:
        floor_s = float(p["standard_deduction_1977_single_or_hoh_floor"][1960])
        ceiling_s = float(p["standard_deduction_1977_single_or_hoh_ceiling"][1960])
        floor_m = float(p["standard_deduction_1977_married_floor"][1960])
        ceiling_m = float(p["standard_deduction_1977_married_ceiling"][1960])
        is_single_or_hoh = pl.col("filing_status").is_in(["single", "head_of_household"])
        stded_s = (0.16 * pl.col("id_agi")).clip(floor_s, ceiling_s)
        stded_m = (0.16 * pl.col("id_agi")).clip(floor_m, ceiling_m)
        df = df.with_columns(id_stded=pl.when(is_single_or_hoh).then(stded_s).otherwise(stded_m))
    elif effective_year <= 1986:
        # `stded=comnew(3)` - for 1978-1986 (law79, federal_pre1987.py's
        # vintage) this IS federal's own real zero-bracket-amount, live-
        # probe-confirmed via a debug-instrumented oracle run (1978/single/
        # $15,000 wages: comnew(3)=2200, matching PRE1987_PARAMS's own zbr
        # table exactly). The 1982-1986 `comnew(23)` addback is confirmed
        # inert.
        zbr_expr = _by_status(
            {status: resolve_year(PRE1987_PARAMS["standard_deduction"][status], effective_year) for status in _STATUSES}
        )
        df = df.with_columns(id_stded=zbr_expr)
    elif effective_year <= 1992:
        # `stded=comnew(3)` - for 1987-1992 (law87's vintage) `comnew(3)`
        # only holds federal's real standard deduction when federal
        # itself is NOT itemizing; the moment federal itemizes (which, via
        # the circular federal/state SALT-deduction loop in
        # engine/federal_state.py, can happen EVEN WITH ZERO itemizable
        # inputs, purely because Idaho's own growing state tax liability
        # becomes a real deductible SALT amount once it exceeds the flat
        # standard deduction), `comnew(3)` reverts to its initialized $0 -
        # a real, replicated-as-found oracle quirk (federal's std
        # deduction figure is only ever WRITTEN when it's the figure
        # actually used). Algebraically confirmed via real expected-tax
        # reverse-engineering (1987/single: deduc+exemp=$4440=$2540
        # std+$1900 exemption for wages<=$30,000, but exactly $1900
        # (deduc=0) for wages>=$50,000 - the exact income band where
        # Idaho's own state tax first exceeds $2540). The married_
        # separate-only 1988-1989/1991-1992 age/blind-count reduction
        # (`-150*nblage` / `-200*nblage`) is confirmed inert (nblage
        # always 0), but 1990's own version (`-150.*nblage+25.`) has a
        # REAL, unconditional `+25` addback that survives even at
        # nblage=0 - live-probe-confirmed via a constant ~$2.05 (=$25 *
        # marginal rate) residual across every married_separate income
        # level tested for 1990 specifically.
        df = df.with_columns(
            id_stded=pl.when(pl.col("itemizes")).then(0.0).otherwise(pl.col("standard_deduction"))
        )
        if effective_year == 1990:
            df = df.with_columns(
                id_stded=pl.when(pl.col("filing_status") == "married_separate")
                .then(pl.col("id_stded") + 25.0)
                .otherwise(pl.col("id_stded"))
            )
    elif effective_year <= 1998:
        s = float(resolve_year(p["standard_deduction_single_1993plus"], effective_year))
        h = float(resolve_year(p["standard_deduction_hoh_1993plus"], effective_year))
        j = float(resolve_year(p["standard_deduction_married_joint_1993plus"], effective_year))
        df = df.with_columns(
            id_stded=pl.when(pl.col("filing_status") == "single").then(s)
            .when(pl.col("filing_status") == "head_of_household").then(h)
            .otherwise(j / pl.col("id_sep"))
        )
    else:
        # `dedw(law)` - a REAL, distinct table from `dedj`, used (divided
        # by `sep`) for married_separate specifically 1999+ (mst.eq.2
        # alone gets `dedj` directly; the catch-all `else` below it -
        # covering married_separate - uses `dedw`, not `dedj/sep`).
        s = float(resolve_year(p["standard_deduction_single_1993plus"], effective_year))
        h = float(resolve_year(p["standard_deduction_hoh_1993plus"], effective_year))
        j = float(resolve_year(p["standard_deduction_married_joint_1993plus"], effective_year))
        w = float(resolve_year(p["standard_deduction_married_separate_1999plus"], effective_year))
        df = df.with_columns(
            id_stded=pl.when(pl.col("filing_status") == "single").then(s)
            .when(pl.col("filing_status") == "head_of_household").then(h)
            .when(pl.col("filing_status") == "married_joint").then(j)
            .otherwise(w / pl.col("id_sep"))
        )
        if 2008 <= effective_year <= 2009:
            cap_per_exemption = float(p["standard_deduction_proptax_addback_cap_per_exemption_2008_2009"][1960])
            addback = pl.min_horizontal(cap_per_exemption * pl.col("id_texp"), pl.col("proptax"))
            df = df.with_columns(id_stded=pl.col("id_stded") + addback)
    # Age/blind additional standard deduction and the dependent-return
    # standard-deduction cap both confirmed permanently inert.

    df = df.with_columns(id_xitded_detail=pl.col("id_xitded"))
    if effective_year == 1999:
        df = df.with_columns(id_xitded=pl.when(forced_standard()).then(0.0).otherwise(pl.col("id_xitded")))

    # Taxpayers 65 or older, and dependent filers.
    aged = aged_count()
    sep_status = pl.col("filing_status") == "married_separate"
    if 1988 <= effective_year <= 1992:
        reduction = float(resolve_year(p["separate_aged_reduction"], effective_year))
        df = df.with_columns(id_stded=pl.when(sep_status).then(pl.col("id_stded") - reduction * aged).otherwise(pl.col("id_stded")))
    if effective_year == 1987:
        t = p["aged_standard_deduction_1987"]
        aged_std = pl.lit(None, dtype=pl.Float64)
        for status, (first, step) in t.items():
            aged_std = pl.when(pl.col("filing_status") == status).then(float(first) + float(step) * (aged - 1)).otherwise(aged_std)
        df = df.with_columns(id_stded=pl.when(aged >= 1).then(aged_std).otherwise(pl.col("id_stded")))
    elif effective_year >= 1993:
        single_like = pl.col("filing_status").is_in(["single", "head_of_household"])
        aged_p = FEDERAL_INCOME_TAX_PARAMS["aged_standard_deduction"]
        amount = pl.when(single_like).then(float(resolve_year(aged_p["single"], effective_year))).otherwise(
            float(resolve_year(aged_p["married_joint"], effective_year))
        )
        df = df.with_columns(id_stded=pl.col("id_stded") + amount * aged)
    if effective_year >= 1987:
        minimum = float(resolve_year(p["dependent_standard_deduction_minimum"], effective_year))
        limit = pl.max_horizontal(pl.lit(minimum), pl.col("earned_income") + p["dependent_standard_deduction_earned_addition"])
        df = df.with_columns(
            id_stded=pl.when(is_dependent_filer()).then(pl.min_horizontal(pl.col("id_stded"), limit)).otherwise(pl.col("id_stded"))
        )
    df = df.with_columns(id_deduc=pl.max_horizontal(pl.col("id_xitded"), pl.col("id_stded")))

    # --- Exemption --- (see module docstring point 1)
    # The federal exemption amount (`comnew(83)`).
    df = df.with_columns(id_exemp=pl.col("pre1987_amex") if effective_year <= 1986 else pl.col("personal_exemptions"))

    df = df.with_columns(id_taxinc=(pl.col("id_agi") - pl.col("id_deduc") - pl.col("id_exemp")).clip(0, None))
    if effective_year >= 2018:
        df = df.with_columns(id_taxinc=(pl.col("id_taxinc") - pl.col("qbi_deduction")).clip(0, None))
    # 2018+ QBI deduction: genuine scope gap (federal.py never implements
    # it), treated as $0 - see module/YAML scope note.

    # --- Bracket tax --- (see module docstring points 3-4)
    year_table_map = [
        ((1977, 1986), "pre1987", 1.0),
        ((1987, 1999), "1987_1999", 1.0),
        ((2000, 2000), "2000", 1.0),
    ]
    key = None
    for (lo, hi), k, _aif in year_table_map:
        if lo <= effective_year <= hi:
            key = k
            break
    if key is None:
        if 2001 <= effective_year <= 2011:
            key = "2001_2011"
        elif 2012 <= effective_year <= 2017:
            key = "2012_2017"
        elif 2018 <= effective_year <= 2020:
            key = "2018_2020"
        else:
            key = "2021plus"
        aif = float(resolve_year(p["bracket_inflation_factor"], effective_year))
    else:
        aif = 1.0
    brackets = p[f"brackets_{key}"]
    txp = pl.when(pl.col("filing_status").is_in(["married_joint", "head_of_household"])).then(2.0).otherwise(1.0)
    tinc = pl.col("id_taxinc") / txp
    stat = bracket_tax(tinc / aif, brackets) * aif
    df = df.with_columns(id_statax=stat * txp)

    # --- Credits ---
    # Nonrefundable Child Tax Credit (2018+).
    if effective_year >= 2018:
        per_child = float(p["child_tax_credit_per_child_2018plus"][1960])
        df = df.with_columns(id_ctcred=pl.min_horizontal(per_child * pl.col("dep17"), pl.col("id_statax")))
    else:
        df = df.with_columns(id_ctcred=pl.lit(0.0))

    # Grocery Tax Credit.
    depx_plus_texp = pl.col("id_texp") + pl.col("depx")
    if effective_year == 1977:
        flat = float(p["grocery_credit_flat_1977"][1960])
        df = df.with_columns(id_grcred=flat * depx_plus_texp)
    elif effective_year <= 2000:
        flat = float(p["grocery_credit_flat_1978_2000"][1960])
        df = df.with_columns(id_grcred=flat * depx_plus_texp)
    elif effective_year <= 2007:
        flat = float(p["grocery_credit_flat_2001_2007"][1960])
        df = df.with_columns(id_grcred=flat * depx_plus_texp)
    else:
        amt = float(resolve_year(p["grocery_credit_by_year_2008plus"], effective_year))
        grcred = amt * depx_plus_texp
        if effective_year <= 2014:
            low_income_bonus = float(
                p["grocery_credit_low_income_bonus_2013" if effective_year <= 2013 else "grocery_credit_low_income_bonus_2014"][1960]
            ) if False else (
                float(p["grocery_credit_low_income_bonus_2008_2013"][1960]) if effective_year <= 2013
                else float(p["grocery_credit_low_income_bonus_2014"][1960])
            )
            grcred = grcred + pl.when(pl.col("id_taxinc") <= 1000.0).then(low_income_bonus * depx_plus_texp).otherwise(0.0)
        grcred = grcred + float(resolve_year(p["grocery_credit_aged"], effective_year)) * aged_count()
        if 2012 <= effective_year <= 2016:
            grcred = pl.when((pl.col("filing_status") == "married_separate") & (aged_count() < 1)).then(0.0).otherwise(grcred)
        df = df.with_columns(id_grcred=grcred)
    if effective_year <= 2007:
        df = df.with_columns(
            id_grcred=pl.col("id_grcred") + float(resolve_year(p["grocery_credit_aged"], effective_year)) * aged_count()
        )
    df = df.with_columns(id_grcred=pl.when(is_dependent_filer()).then(0.0).otherwise(pl.col("id_grcred")))

    df = df.with_columns(id_credit=pl.col("id_ctcred") + pl.col("id_grcred"))
    df = df.with_columns(id_statax=pl.col("id_statax") - pl.col("id_credit"))

    # --- Permanent Building Fund Tax --- (see module docstring point 5)
    lim_single = float(p["building_fund_limit_single_or_hoh_base"][1960]) + (
        float(p["building_fund_limit_single_or_hoh_addon_1981plus"][1960]) if effective_year >= 1981 else 0.0
    )
    lim_married = float(p["building_fund_limit_married_base"][1960]) + (
        float(p["building_fund_limit_married_addon_1981plus"][1960]) if effective_year >= 1981 else 0.0
    )
    lim_sep = float(p["building_fund_limit_separate_base"][1960]) + (
        float(p["building_fund_limit_separate_addon_1981plus"][1960]) if effective_year >= 1981 else 0.0
    )
    lim = (
        pl.when(pl.col("filing_status").is_in(["single", "head_of_household"])).then(lim_single)
        .when(pl.col("filing_status") == "married_joint").then(lim_married)
        .otherwise(lim_sep)
    )
    lim = lim + pl.when(pl.col("filing_status") != "married_separate").then(p["building_fund_limit_aged"] * aged_count()).otherwise(0.0)
    df = df.with_columns(id_hy=pl.col("id_household_income"))
    if effective_year >= 1978:
        building_fund_tax = float(p["building_fund_tax_amount"][1960])
        df = df.with_columns(
            id_statax=pl.when(pl.col("id_hy") > lim).then(pl.col("id_statax") + building_fund_tax).otherwise(pl.col("id_statax"))
        )

    df = df.with_columns(siitax=pl.col("id_statax") * flate)
    return with_state_detail(
        df,
        agi=pl.col("id_agi"),
        exemptions=pl.col("id_exemp"),
        standard_deduction=pl.col("id_stded"),
        itemized_deductions=pl.col("id_xitded_detail"),
        taxable_income=pl.col("id_taxinc"),
        credits=pl.col("id_credit"),
        rate=bracket_rate(tinc / aif, brackets),
    )
