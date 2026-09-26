"""Kansas individual income tax calculator."""

import polars as pl

from taxsim_py.calculators.payroll import payroll_parts
from taxsim_py.engine.brackets import bracket_rate, bracket_tax
from taxsim_py.engine.credits import child_care_credit_rate_pre2021
from taxsim_py.engine.inputs import aged_count, is_dependent_filer, taxpayer_count
from taxsim_py.engine.schema import PARAMETERS_ROOT, load_yaml, resolve_year
from taxsim_py.engine.state import (
    by_filing_status as _by_status,
    household_income,
    interpolate_table as _tablki,
    with_default as _with_default,
    taxsim_socsec,
    with_defaults,
    with_state_detail,
)
from taxsim_py.engine.state_extrapolation import deflate_for_extrapolation, resolve_state_year

STATE_ADJUSTMENT_PARAMS = load_yaml(PARAMETERS_ROOT / "national" / "state_adjustments.yaml")
KS_PARAMS = load_yaml(PARAMETERS_ROOT / "states" / "ks" / "income_tax.yaml")
FEDERAL_CREDITS_PARAMS = load_yaml(PARAMETERS_ROOT / "national" / "credits.yaml")


def compute_ks_tax(df: pl.DataFrame, year: int) -> pl.DataFrame:
    effective_year, flate = resolve_state_year(year)
    p = KS_PARAMS
    df = with_defaults(df, ("federal_chcr", "proptax", "otheritem", "mortgage", "dividends", "intrec", "depx", "psemp", "ssemp"))
    df = _with_default(df, "earned_income")
    df = _with_default(df, "eitc")
    df = _with_default(df, "ccc")
    df = _with_default(df, "itemized_deduction")
    df = _with_default(df, "salt_capped")
    df = _with_default(df, "state_sales_or_income_tax_ded")
    df = _with_default(df, "itemizes", False)
    df = _with_default(df, "fiitax")

    df = df.with_columns(
        ks_sep=pl.when(pl.col("filing_status") == "married_separate").then(2.0).otherwise(1.0),
        ks_txp=taxpayer_count(),
    )
    is_joint = pl.col("filing_status") == "married_joint"
    is_hoh = pl.col("filing_status") == "head_of_household"

    # `setax` (comnew(175)) - computed at the REAL `year`'s rates on REAL
    # (undeflated) wages, same technique Alabama/Iowa already established
    # (sits outside the real dispatcher's own generic deflate loop).
    setax = payroll_parts(year)["setax"]  # `comnew(175)`, real-year and undeflated
    df = df.with_columns(ks_setax=setax)

    df = with_defaults(df, ("taxable_social_security", "earned_income", "pre1987_deduc"))
    df = df.with_columns(
        ks_household_income=household_income(
            float(resolve_year(STATE_ADJUSTMENT_PARAMS["household_income_dividend_adjustment"], effective_year)),
            float(resolve_year(STATE_ADJUSTMENT_PARAMS["household_income_record_adjustment"], effective_year)),
        )
    )
    df = deflate_for_extrapolation(
        df,
        flate,
        [
            "federal_chcr",
            "taxable_social_security", "gssi", "rentpaid", "otherprop", "scorp", "ks_household_income",
            "pre1987_deduc",
            "pwages", "swages", "proptax", "otheritem", "mortgage", "dividends", "intrec",
            "stcg", "ltcg", "ui", "pui", "sui", "agi", "earned_income", "eitc", "ccc",
            "itemized_deduction", "salt_capped", "state_sales_or_income_tax_ded", "fiitax",
        ],
    )

    # --- AGI ---
    df = df.with_columns(ks_agi=pl.col("agi"))
    # Social Security benefits are exempt below a federal AGI limit (2007+).
    if effective_year >= 2007:
        limit = float(resolve_year(p["social_security_agi_limit"], effective_year))
        df = df.with_columns(
            ks_agi=pl.when(pl.col("agi") <= limit).then(pl.col("ks_agi") - pl.col("taxable_social_security")).otherwise(pl.col("ks_agi"))
        )
    if 2013 <= effective_year <= 2016:
        # Self-employment, S corporation and other property income removed.
        se_income = pl.col("psemp") + pl.col("ssemp")
        schedule_e = pl.col("otherprop") + pl.col("scorp")
        df = df.with_columns(ks_agi=pl.col("ks_agi") + 0.5 * pl.col("ks_setax") - se_income - schedule_e)

    fedtax = pl.col("fiitax").clip(0, None)
    if effective_year <= 1982 or (1987 <= effective_year <= 1988):
        fedded = fedtax
    elif effective_year in (1983, 1984):
        cap1 = float(p["fedded_1983_1984_cap_per_filer"][1960]) * pl.col("ks_txp")
        cap2 = float(p["fedded_1983_1984_upper_cap_per_filer"][1960]) * pl.col("ks_txp")
        fedded = pl.when(fedtax <= cap1).then(fedtax).when(fedtax <= cap2).then(cap1).otherwise(0.5 * fedtax)
    elif 1985 <= effective_year <= 1986:
        fedded = fedtax * pl.col("ks_agi").clip(0, None) / pl.col("agi").clip(1.0, None)
    else:
        fedded = pl.lit(0.0)

    # --- Standard deduction ---
    if effective_year <= 1987:
        pct = float(p["standard_deduction_pct_pre1988"][1960])
        single_hoh_floor = float(p["standard_deduction_floor_single_or_hoh_pre1988"][1960])
        single_hoh_cap = float(p["standard_deduction_cap_single_or_hoh_pre1988"][1960])
        married_floor = float(p["standard_deduction_floor_married_pre1988"][1960])
        married_cap = float(p["standard_deduction_cap_married_pre1988"][1960])
        is_single_or_hoh = pl.col("filing_status").is_in(["single", "head_of_household"])
        stded = pl.when(is_single_or_hoh).then(
            (pct * pl.col("ks_agi")).clip(single_hoh_floor, single_hoh_cap)
        ).otherwise((pct * pl.col("ks_agi")).clip(married_floor / pl.col("ks_sep"), married_cap / pl.col("ks_sep")))
    else:
        if effective_year <= 1997:
            table = p["standard_deduction_1988_1997"]
        elif effective_year <= 2012:
            table = p["standard_deduction_1998_2012"]
        elif effective_year <= 2020:
            table = p["standard_deduction_2013_2020"]
        else:
            table = p["standard_deduction_2021plus"]
        stded = _by_status(table)
        if effective_year >= 1998:
            limit = pl.max_horizontal(pl.lit(float(p["dependent_standard_deduction_minimum"])), pl.col("earned_income"))
            stded = pl.when(is_dependent_filer()).then(pl.min_horizontal(stded, limit)).otherwise(stded)
        single_amount, married_amount = resolve_year(p["aged_standard_deduction"], effective_year)
        married_type = pl.col("filing_status").is_in(["married_joint", "married_separate"])
        stded = stded + pl.when(married_type).then(float(married_amount)).otherwise(float(single_amount)) * aged_count()
    df = df.with_columns(ks_stded=stded)

    # --- Itemized deduction ---
    if effective_year <= 1986:
        # Federal itemized deductions (zero when not itemizing federally).
        itemized_deduction_local = pl.col("pre1987_deduc")
        itemizing = pl.col("pre1987_itemizes")
    else:
        salt_plus_mortgage = pl.col("salt_capped") + pl.col("mortgage")
        itemized_deduction_local = pl.col("itemized_deduction")
        itemizing = pl.col("itemizes") & (effective_year <= 2020)
    ag = pl.col("ks_agi").clip(0, None)

    xitded = pl.lit(0.0)
    if effective_year <= 1987:
        # `edm`/`data(44)` confirmed inert. Household FICA/SE-tax paid,
        # capped, real addback (see module docstring point for the
        # `socsec()` reconstruction).
        socsec = taxsim_socsec(effective_year, pl.col("ks_setax"))
        # `socmax`/`selfmx` are real, explicit caps ONLY for 1977-1984 -
        # the source's own DATA statement has `13*1.e20` after that (1985-
        # 1997), i.e. genuinely UNCAPPED, not frozen at 1984's dollar
        # value (an earlier version of this code wrongly reused 1984's
        # figure for 1985+, caught via a live-probe mismatch on a pure-
        # wages 1985 case).
        socmax = float(resolve_year(p["socsec_max_1977_1986"], effective_year)) if effective_year <= 1984 else 1.0e20
        selfmx = float(resolve_year(p["selfemployment_tax_max_1977_1986"], effective_year)) if effective_year <= 1984 else 1.0e20
        soc = socsec.clip(0, socmax * pl.col("ks_txp"))
        addtx = pl.col("ks_setax").clip(0, selfmx * pl.col("ks_txp"))
        base = (itemized_deduction_local - pl.col("state_sales_or_income_tax_ded") + soc + addtx).clip(0, None)
        # `comnew(25)` live-probe-confirmed $0 - the 1979-1986 dividend/
        # interest addback is a no-op.
        xitded = pl.when(itemizing).then(base).otherwise(0.0)
    elif effective_year <= 1990:
        xitded = pl.when(itemizing).then((itemized_deduction_local - pl.col("state_sales_or_income_tax_ded")).clip(0, None)).otherwise(0.0)
    elif effective_year <= 2009:
        aif_val = float(resolve_year(p["itemized_phaseout_aif_1991_2009"], effective_year))
        under_thr = pl.col("agi") <= 100000.0 * aif_val / pl.col("ks_sep")
        base_low = (itemized_deduction_local - pl.col("state_sales_or_income_tax_ded")).clip(0, None)
        fline3 = itemized_deduction_local.clip(0, None)
        fline9 = pl.min_horizontal(0.03 * (pl.col("agi") - 100000.0 * aif_val).clip(0, None), 0.8 * fline3)
        sline1 = pl.when(fline3 > 0).then(fline9 / fline3).otherwise(0.0)
        base_high = pl.when(fline3 > 0).then(
            (itemized_deduction_local - pl.col("state_sales_or_income_tax_ded") * (1 - sline1)).clip(0, None)
        ).otherwise(base_low)
        xitded = pl.when(itemizing).then(pl.when(under_thr).then(base_low).otherwise(base_high)).otherwise(0.0)
    elif effective_year <= 2012:
        xitded = pl.when(itemizing).then((itemized_deduction_local - pl.col("state_sales_or_income_tax_ded")).clip(0, None)).otherwise(0.0)
    elif effective_year <= 2017:
        aifit_val = float(resolve_year(p["itemized_scaling_2013plus"], effective_year))
        xitded = pl.when(itemizing).then(
            aifit_val * (salt_plus_mortgage - pl.col("state_sales_or_income_tax_ded")).clip(0, None)
        ).otherwise(0.0)
    else:
        aifit_val = float(resolve_year(p["itemized_scaling_2013plus"], effective_year))
        txpaid = pl.col("proptax") + pl.col("otheritem")
        xitded = pl.when(itemizing).then(aifit_val * (txpaid + pl.col("mortgage"))).otherwise(0.0)

    if effective_year == 2021:
        # `edical`/`data(47)/(48)/(49)` confirmed inert -> $0. Overrides
        # everything above unconditionally (not gated on itemizing at all).
        xitded = pl.col("proptax") + pl.col("otheritem") + pl.col("mortgage")

    # `force_itemize` has no `kstax` counterpart at all (see the module
    # docstring/the `itemizing` note above) - `data(4)`/`ided` is never
    # read in the source, so no override here either.
    df = df.with_columns(ks_xitded=xitded)
    df = df.with_columns(ks_deduc=pl.max_horizontal(pl.col("ks_stded"), pl.col("ks_xitded")))

    # --- Exemptions ---
    # `comnew(68)` sits at a real-dispatcher array position that gets
    # divided by the CPI-extrapolation `flate` for years past 2021 (same
    # quirk already documented/fixed for Indiana) - the `+1` HoH addition
    # happens in Kansas's OWN subroutine code on the ALREADY-divided
    # value, so it's added AFTER dividing, not before.
    # Federal exemption count (`comnew(68)`): the aged count before 1987;
    # none for a dependent filer from 1987.
    if effective_year <= 1986:
        comnew68 = (pl.col("ks_txp") + pl.col("depx") + aged_count()) / flate
    else:
        comnew68 = pl.when(is_dependent_filer()).then(0.0).otherwise(pl.col("ks_txp") + pl.col("depx")) / flate
    exemps = pl.when(is_hoh).then(comnew68 + 1.0).otherwise(comnew68)
    xmp = float(resolve_year(p["personal_exemption_amount"], effective_year))
    df = df.with_columns(ks_exemp=exemps * xmp)

    df = df.with_columns(ks_taxinc=(pl.col("ks_agi") - pl.col("ks_deduc") - pl.col("ks_exemp") - fedded).clip(0, None))

    # --- Bracket tax --- (see module docstring point 1 for the married-
    # filing-combined split mechanic)
    def _split_allowed(yr: int) -> bool:
        return yr <= 1987 or yr >= 2013

    if effective_year <= 1987:
        table = p["brackets_pre1988"]
        halved = pl.when(is_joint).then(pl.col("ks_taxinc") / 2.0).otherwise(pl.col("ks_taxinc"))
        doubler = pl.when(is_joint).then(2.0).otherwise(1.0)
        statax = bracket_tax(halved, table) * doubler
        rate_expr = bracket_rate(pl.col("ks_taxinc"), table)
    elif effective_year <= 1989:
        statax = pl.when(is_joint).then(bracket_tax(pl.col("ks_taxinc"), p["brackets_1988_1989_joint"])).otherwise(
            bracket_tax(pl.col("ks_taxinc"), p["brackets_1988_1989_single"])
        )
        rate_expr = pl.when(is_joint).then(
            bracket_rate(pl.col("ks_taxinc"), p["brackets_1988_1989_joint"])
        ).otherwise(bracket_rate(pl.col("ks_taxinc"), p["brackets_1988_1989_single"]))
    elif effective_year <= 1991:
        statax = pl.when(is_joint).then(bracket_tax(pl.col("ks_taxinc"), p["brackets_1990_1991_joint"])).otherwise(
            bracket_tax(pl.col("ks_taxinc"), p["brackets_1990_1991_single"])
        )
        rate_expr = pl.when(is_joint).then(
            bracket_rate(pl.col("ks_taxinc"), p["brackets_1990_1991_joint"])
        ).otherwise(bracket_rate(pl.col("ks_taxinc"), p["brackets_1990_1991_single"]))
    elif effective_year <= 2012:
        if effective_year <= 1996:
            single_table = p["brackets_1992_1996_single"]
        elif effective_year == 1997:
            single_table = p["brackets_1997_single"]
        else:
            single_table = p["brackets_1998_2012_single"]
        statax = pl.when(is_joint).then(bracket_tax(pl.col("ks_taxinc"), p["brackets_1992_2012_joint"])).otherwise(
            bracket_tax(pl.col("ks_taxinc"), single_table)
        )
        rate_expr = pl.when(is_joint).then(
            bracket_rate(pl.col("ks_taxinc"), p["brackets_1992_2012_joint"])
        ).otherwise(bracket_rate(pl.col("ks_taxinc"), single_table))
    else:
        if effective_year == 2013:
            table = p["brackets_2013"]
        elif effective_year == 2014:
            table = p["brackets_2014"]
        elif effective_year <= 2016:
            table = p["brackets_2015_2016"]
        elif effective_year == 2017:
            table = p["brackets_2017"]
        else:
            table = p["brackets_2018plus"]
        halved = pl.when(is_joint).then(pl.col("ks_taxinc") / 2.0).otherwise(pl.col("ks_taxinc"))
        doubler = pl.when(is_joint).then(2.0).otherwise(1.0)
        statax = bracket_tax(halved, table) * doubler
        rate_expr = bracket_rate(pl.col("ks_taxinc"), table)

    if _split_allowed(effective_year):
        wages = pl.col("pwages") + pl.col("swages")
        yh = pl.max_horizontal(pl.col("pwages"), pl.col("swages")) + (pl.col("ks_taxinc") - wages) / 2.0
        yw = pl.col("ks_taxinc") - yh
        tax_h = bracket_tax(yh.clip(0, None), table)
        tax_w = bracket_tax(yw.clip(0, None), table)
        statax = pl.when(is_joint).then(pl.min_horizontal(statax, tax_h + tax_w)).otherwise(statax)
        # TAXSIM's shared bracket-rate variable is left by the first
        # (higher-earner) half of the split-return calculation.
        rate_expr = pl.when(is_joint).then(bracket_rate(yh.clip(0, None), table)).otherwise(rate_expr)

    df = df.with_columns(ks_statax=statax)

    if 2015 <= effective_year <= 2017:
        ceiling = float(p["zero_tax_taxinc_ceiling_joint_2015_2017"][1960])
        ceiling_s = float(p["zero_tax_taxinc_ceiling_single_2015_2017"][1960])
        under = pl.when(is_joint).then(pl.col("ks_taxinc") <= ceiling).otherwise(pl.col("ks_taxinc") <= ceiling_s)
        df = df.with_columns(ks_statax=pl.when(under).then(0.0).otherwise(pl.col("ks_statax")))
    elif effective_year >= 2018:
        ceiling = float(p["zero_tax_taxinc_ceiling_joint_2018plus"][1960])
        ceiling_s = float(p["zero_tax_taxinc_ceiling_single_2018plus"][1960])
        under = pl.when(is_joint).then(pl.col("ks_taxinc") <= ceiling).otherwise(pl.col("ks_taxinc") <= ceiling_s)
        df = df.with_columns(ks_statax=pl.when(under).then(0.0).otherwise(pl.col("ks_statax")))

    # --- Credits ---
    # Child/Dependent Care Credit - see module docstring point 3 for why
    # 2013-2018 has no branch (chcr stays $0). `comnew(52)`'s own expense
    # ceiling never bound in probes. `comnew(53)`(=`comnew(176)`) is
    # federal's CCC amount BEFORE its own nonrefundable cap - federal.py's
    # own `ccc` column is a real approximation for this EXCEPT 1988-1997,
    # where federal.py deliberately zeroes `ccc` entirely (a real,
    # separately-documented federal-side quirk: the "stacking" mechanism
    # that actually APPLIES CCC to reduce FEDERAL tax liability doesn't
    # exist in the source before 1998, so federal.py's own `ccc` reports
    # $0 those years even though the RAW credit amount was genuinely
    # computed and nonzero) - reconstructed locally here for 1988-1997
    # using the same pre-2021 rate-schedule primitive federal.py itself
    # uses, UNCAPPED by any federal tax liability (matching `comnew(176)`'s
    # own "before its own cap" definition). Caught via a live-probe
    # mismatch: a 1988/single/$25,000-wages/1-dependent/$2,000-childcare
    # case wrongly gave $0 Kansas credit.
    if 1988 <= effective_year <= 1997:
        ccc_p = FEDERAL_CREDITS_PARAMS["child_care_credit"]
        max_qualifying_persons = float(resolve_year(ccc_p["max_qualifying_persons"], effective_year))
        max_expense_per_person = float(resolve_year(ccc_p["max_expense_per_person_pre2021"], effective_year))
        ccc_rate = child_care_credit_rate_pre2021(
            pl.col("agi"),
            phase_start=float(resolve_year(ccc_p["pre2021_phase_start"], effective_year)),
            top_rate=float(resolve_year(ccc_p["pre2021_rate_top"], effective_year)),
            floor_rate=float(resolve_year(ccc_p["pre2021_rate_floor"], effective_year)),
            step_amount=float(resolve_year(ccc_p["pre2021_step_amount"], effective_year)),
        )
        num_qualifying_persons = pl.col("dep13").clip(0, max_qualifying_persons) if "dep13" in df.collect_schema().names() else pl.lit(0.0)
        qualifying_expense = pl.col("childcare").clip(0, num_qualifying_persons * max_expense_per_person) if "childcare" in df.collect_schema().names() else pl.lit(0.0)
        ccc_earned_income_cap = pl.when(is_joint).then(
            pl.min_horizontal(pl.col("pwages"), pl.col("swages"))
        ).otherwise(pl.col("wages"))
        ccc_expense = pl.min_horizontal(qualifying_expense, ccc_earned_income_cap).clip(0, None)
        chcare = ccc_rate * ccc_expense
    else:
        chcare = pl.col("federal_chcr")
    # Kansas uses `min(comnew(53), max(0, comnew(52)-data(34)))` rather
    # than the uncapped federal credit. `data(34)` is unavailable through
    # TAXSIM's public input schema and therefore zero here.
    chcare = pl.min_horizontal(chcare, pl.col("regular_tax").clip(0, None))
    if effective_year <= 1987:
        chcr = chcare * _tablki(pl.col("ks_agi"), p["child_care_credit_table_pre1988"])
    elif (1988 <= effective_year <= 2012) or effective_year >= 2020:
        chcr = chcare * float(p["child_care_credit_rate_1988_2012_and_2020plus"][1960])
    elif effective_year == 2019:
        chcr = chcare * float(p["child_care_credit_rate_2019"][1960])
    else:
        chcr = pl.lit(0.0)
    df = df.with_columns(ks_chcr=chcr)

    # Solar/energy credit (`data(38)`) confirmed inert for every year.
    encred = pl.lit(0.0)

    # Homestead Property Tax Refund / food credit (`pcred`).
    rent_share = float(resolve_year(p["homestead_rent_share"], effective_year))
    pr1 = pl.col("proptax") + rent_share * pl.col("rentpaid")
    hy = pl.col("ks_household_income")
    pcred = pl.lit(0.0)
    if effective_year in (1977, 1978):
        ceiling = float(p["homestead_1977_income_ceiling" if effective_year == 1977 else "homestead_1978_income_ceiling"][1960])
        claw = (
            pl.when(hy <= 4200.0).then((hy - 3400.0).clip(0, None) * 0.02)
            .when(hy <= 4600.0).then(16.0 + (hy - 4200.0).clip(0, None) * 0.04)
            .otherwise(32.0 + (hy - 4600.0).clip(0, None) * 0.045)
        )
        under = hy <= ceiling
        cap = float(p["homestead_cap_1977_1978"][1960])
        pcred = pl.when(under).then(
            pl.when(hy <= 3400.0).then(pr1).otherwise((pr1 - claw).clip(0, cap))
        ).otherwise(0.0)
    elif 1979 <= effective_year <= 1988:
        ceiling = float(p["homestead_1979_1988_income_ceiling"][1960])
        claw = (
            pl.when(hy <= 3500.0).then((hy - 3400.0).clip(0, None) * 0.01)
            .when(hy <= 4000.0).then(1.0 + (hy - 3500.0).clip(0, None) * 0.02)
            .when(hy <= 4600.0).then(11.0 + (hy - 4000.0).clip(0, None) * 0.03)
            .when(hy <= 8600.0).then(29.0 + (hy - 4600.0).clip(0, None) * 0.04)
            .otherwise(189.0 + (hy - 8600.0).clip(0, None) * 0.05)
        )
        under = hy <= ceiling
        cap = float(p["homestead_cap_1979_1988"][1960])
        pcred = pl.when(under).then(
            pl.when(hy <= 3400.0).then(pr1).otherwise((pr1.clip(0, cap) - claw).clip(0, None))
        ).otherwise(0.0)
    elif 1989 <= effective_year <= 1994:
        ceiling = float(p["homestead_1989_1994_income_ceiling"][1960])
        claw = (
            pl.when(hy <= 4200.0).then((hy - 3400.0).clip(0, None) * 0.02)
            .when(hy <= 4600.0).then(16.0 + (hy - 4200.0).clip(0, None) * 0.04)
            .otherwise(32.0 + (hy - 4600.0).clip(0, None) * 0.045)
        )
        under = hy <= ceiling
        cap = float(p["homestead_cap_1989_1994"][1960])
        pcred = pl.when(under).then(
            pl.when(hy <= 3400.0).then(pr1).otherwise((pr1.clip(0, cap) - claw).clip(0, None))
        ).otherwise(0.0)
    elif effective_year in (1995, 1996):
        ceiling = float(p["homestead_1995_1996_income_ceiling"][1960])
        if effective_year == 1996:
            # TAXSIM's test reads `1996 or (1995 and income <= ceiling)`.
            ceiling = 1.0e20
        claw = (
            pl.when(hy <= 4200.0).then((hy - 3400.0).clip(0, None) * 0.02)
            .when(hy <= 4600.0).then(16.0 + (hy - 4200.0).clip(0, None) * 0.04)
            .otherwise(32.0 + (hy - 4600.0).clip(0, None) * 0.045)
        )
        under = hy <= ceiling
        cap = float(p["homestead_cap_1995_1996"][1960])
        pcred = pl.when(under).then(
            pl.when(hy <= 3400.0).then(pr1).otherwise((pr1.clip(0, cap) - claw).clip(0, None))
        ).otherwise(0.0)
    elif effective_year >= 1997:
        pt_cap = float(resolve_year(p["homestead_pt_cap"], effective_year))
        ptax = pl.min_horizontal(pt_cap, pr1)
        if effective_year <= 2005:
            hhy = hy
        else:
            hhy = pl.col("ks_agi") + pl.col("eitc") + 0.5 * pl.col("gssi")
        pmax = float(resolve_year(p["homestead_hy_ceiling_by_year"], effective_year))
        table = p["homestead_table_1997_2004"] if effective_year <= 2004 else p["homestead_table_2005plus"]
        pcred = pl.when(hhy < pmax).then(ptax * _tablki(hhy, table)).otherwise(0.0)
        if effective_year >= 2008:
            # Property Tax Relief for low income seniors replaces it.
            share = float(resolve_year(p["senior_property_relief_share"], effective_year))
            limit = float(resolve_year(p["senior_property_relief_income_limit"], effective_year))
            senior = (aged_count() > 0) & (pl.col("proptax") > 0) & (hy < limit)
            pcred = pl.when(senior).then(share * pl.col("proptax")).otherwise(pcred)
    df = df.with_columns(ks_pcred=pcred)

    # Food Sales Tax Refund.
    fd = pl.lit(0.0)
    if effective_year <= 1985:
        per_aged = float(p["food_refund_aged_pre1986"])
        fd = pl.when(hy <= p["food_refund_aged_income_limit_pre1986"]).then(
            per_aged * pl.min_horizontal(aged_count(), pl.col("ks_txp") + pl.col("depx"))
        ).otherwise(0.0)
    elif 1986 <= effective_year <= 1997:
        extra = pl.col("ks_txp") + pl.col("depx") - 1.0
        fd = (
            pl.when(hy < 5000.0).then(40.0 + 30.0 * extra)
            .when(hy < 10000.0).then(30.0 + 25.0 * extra)
            .when(hy <= 13000.0).then(20.0 + 15.0 * extra)
            .otherwise(0.0)
        )
    elif effective_year >= 1998:
        eligible = (pl.col("depx") + aged_count()) > 0
        food_amt = float(resolve_year(p["food_sales_tax_credit_amount"], effective_year))
        if effective_year <= 2012:
            agimax = float(resolve_year(p["food_sales_tax_refund_agi_ceiling"], effective_year))
            fd = pl.when(eligible & (pl.col("ks_agi") <= agimax)).then(2.0 * food_amt * exemps).when(
                eligible & (pl.col("ks_agi") <= 2.0 * agimax)
            ).then(food_amt * exemps).otherwise(0.0)
        else:
            agimax = float(resolve_year(p["food_sales_tax_refund_agi_ceiling"], effective_year))
            fd = pl.when(eligible & (pl.col("agi") <= agimax)).then(food_amt * comnew68).otherwise(0.0)
    df = df.with_columns(ks_fd=fd)

    # Earned Income Credit.
    earncr = pl.lit(0.0)
    if effective_year >= 1998:
        rate_eitc = float(resolve_year(p["eitc_rate"], effective_year))
        earncr = rate_eitc * pl.col("eitc")
    df = df.with_columns(ks_earncr=earncr)

    if effective_year <= 2012:
        df = df.with_columns(
            ks_statax=(pl.col("ks_statax") - encred - pl.col("ks_chcr")).clip(0, None)
            - pl.col("ks_fd") - pl.col("ks_earncr") - pl.col("ks_pcred")
        )
    else:
        df = df.with_columns(
            ks_statax=(pl.col("ks_statax") - encred - pl.col("ks_chcr") - pl.col("ks_fd")).clip(0, None)
            - pl.col("ks_earncr") - pl.col("ks_pcred")
        )

    df = df.with_columns(siitax=pl.col("ks_statax") * flate)
    return with_state_detail(
        df,
        agi=pl.col("ks_agi"),
        exemptions=pl.col("ks_exemp"),
        standard_deduction=pl.col("ks_stded"),
        itemized_deductions=pl.col("ks_xitded"),
        taxable_income=pl.col("ks_taxinc"),
        property_credit=pl.col("ks_pcred"),
        child_care_credit=pl.col("ks_chcr"),
        eic=pl.col("ks_earncr"),
        credits=encred + pl.col("ks_chcr") + pl.col("ks_fd") + pl.col("ks_earncr") + pl.col("ks_pcred"),
        rate=rate_expr,
    )
