"""South Carolina individual income tax calculator."""


from __future__ import annotations
import polars as pl

from taxsim_py.engine.brackets import bracket_rate, bracket_tax, scale_brackets
from taxsim_py.engine.inputs import aged_count, federal_exemption_count, files_head_of_household, files_joint, files_separate, files_single, taxpayer_count
from taxsim_py.engine.schema import PARAMETERS_ROOT, YearParams, load_yaml, resolve_year
from taxsim_py.engine.state import (
    dividend_input_adjustment,
    by_filing_status,
    checkpoint,
    dividend_exclusion_addback,
    pre1987_federal_itemizing,
    unemployment_total,
    with_state_detail,
)
from taxsim_py.behavior import BehaviorProfile, TAXSIM_BEHAVIOR
from taxsim_py.engine.state_extrapolation import deflate_for_extrapolation, resolve_state_year

SC_PARAMS = load_yaml(PARAMETERS_ROOT / "states" / "sc" / "income_tax.yaml")
FEDERAL_EXEMPTION_PARAMS = load_yaml(PARAMETERS_ROOT / "national" / "personal_exemption.yaml")


def compute_sc_tax(df: pl.DataFrame, year: int, behavior: BehaviorProfile = TAXSIM_BEHAVIOR) -> pl.DataFrame:
    """Calculate South Carolina income tax for each row."""
    state_year = "sc" if behavior.mode.value == "statutory" else None
    effective_year, flate = resolve_state_year(year, state_year)
    y = effective_year
    p = YearParams(SC_PARAMS, effective_year)
    dividend_adjustment = dividend_input_adjustment()
    df = df.with_columns(sc_ui=unemployment_total())
    # Self-employment tax (`comnew(175)`) is not deflated in projected years.
    df = deflate_for_extrapolation(df, flate, extra=("sc_ui",))

    is_single = files_single()
    is_joint = files_joint()
    is_sep = files_separate()
    is_hoh = files_head_of_household()
    sep = pl.when(is_sep).then(2.0).otherwise(1.0)
    txp = taxpayer_count()
    aged = aged_count()
    depx = pl.col("depx")
    fed_agi = pl.col("agi")
    fed_itemizes = pl.col("itemizes")

    # --- AGI ---
    if y <= 1984:
        agi = fed_agi + dividend_exclusion_addback(y) + (
            pl.col("sc_ui") - pl.col("taxable_unemployment")
        ).clip(0, None)
        cg = p["long_term_gain_share_1984"] * pl.col("ltcg") + pl.col("stcg")
        agi = agi - pl.col("pre1987_capgn").clip(0, None) + cg.clip(0, None)
        if y >= 1982:
            agi = agi - (pl.col("dividends") + dividend_adjustment + pl.col("intrec")).clip(
                0, p["interest_dividend_exclusion_1982"] * txp
            )
    elif y <= 1986:
        # Federal taxable income (`comnew(29)`) is net of the zero bracket
        # before 1987; the federal tax-table income includes it.
        _, fed_itemizes, zbr = pre1987_federal_itemizing(y)
        agi = (pl.col("taxable_income") - zbr).clip(0, None)
    else:
        agi = pl.col("taxable_income")
    if y in (2009, 2020):
        # Unemployment compensation is taxed in full.
        agi = agi - pl.col("taxable_unemployment") + pl.col("sc_ui")
    if y == 2020:
        agi = agi + pl.when(fed_itemizes).then(0.0).otherwise(pl.col("charity_cash").clip(None, p["charity_nonitemizer_2020"] / sep))
    elif y == 2021:
        agi = agi + pl.when(fed_itemizes).then(0.0).otherwise(pl.col("charity_cash").clip(None, p["charity_nonitemizer_2020"] * txp))
    # Retirement income deduction.
    pensions = pl.col("pensions")
    if y >= 1983:
        young_cap = p.num("retirement_deduction_per_taxpayer")
        retded = pensions.clip(0, young_cap * txp)
        if y >= 1993:
            aged_cap = float(p["retirement_deduction_aged_per_taxpayer"])
            aged_ded = pl.when(is_joint & (aged == 1)).then(pensions.clip(0, aged_cap + young_cap)).otherwise(
                pensions.clip(0, aged_cap * txp)
            )
            retded = pl.when(aged > 0).then(aged_ded).otherwise(retded)
        if y >= 1999:
            retded = pl.when(aged > 0).then(float(p["aged_deduction_per_taxpayer_1999"]) * aged).otherwise(retded)
        agi = agi - retded
    if y >= 1984:
        agi = agi - pl.col("taxable_social_security")
    df, (agi,) = checkpoint(df, sc_agi=agi)

    if y <= 1984:
        index = p.num("index")
        ag = agi.clip(0, None)
        stded = pl.min_horizontal(p["standard_deduction_rate_1984"] * ag * index, index * p["standard_deduction_cap_1984"] * txp)
        gross, _, _ = pre1987_federal_itemizing(y)
        xitded = (gross - pl.col("state_sales_or_income_tax_ded")).clip(0, None)
        # TAXSIM re-runs federal law 1980 for this deduction, but the result
        # never reaches the state's copy of the federal values, so the
        # current year's federal tax is deducted (logged).
        fedtax = pl.col("fiitax")
        xitded = (xitded + fedtax.clip(0, p["federal_tax_deduction_cap"] * txp)).clip(0, None)
        if y <= 1981:
            xitded = xitded + p.num("gasoline_deduction") * txp
        contr = pl.col("charity_cash")
        clim = p.num("charity_limit_share") * ag
        xitded = pl.when(contr > clim).then((xitded - (contr - clim)).clip(0, None)).otherwise(xitded)
        chcr = pl.min_horizontal(
            pl.col("childcare"),
            depx.clip(None, p["child_care_deduction_max_dependents"]) * p["child_care_deduction_per_dependent"]
            + p["child_care_deduction_base"],
        )
        chcr = (chcr - p["child_care_deduction_phaseout_rate"] * (agi - p["child_care_deduction_phaseout_agi"]).clip(0, None)).clip(0, None)
        deduc = pl.max_horizontal(stded, xitded + chcr)
        exemps = federal_exemption_count(y)
        exemp = exemps * index * p["exemption_1984"] + pl.when(is_hoh).then(index * p["exemption_1984"]).otherwise(0.0)
        df, (taxinc,) = checkpoint(df, sc_taxinc=(agi - deduc - exemp).clip(0, None))
        statax = bracket_tax(taxinc, scale_brackets(p["brackets"][1977], index))
        values = {
            "exemptions": exemp,
            "standard_deduction": stded,
            "itemized_deductions": xitded + chcr,
            "taxable_income": taxinc,
            "child_care_credit": chcr,
            "rate": bracket_rate(taxinc, scale_brackets(p["brackets"][1977], index)),
        }
        if y == 1984:
            foodcr = (depx + txp) * p["food_credit_1984"]
            statax = statax - foodcr
            values["credits"] = foodcr
        else:
            limits = p["aged_no_tax_limit_1983"]
            limit = pl.when(depx > 0).then(float(limits["with_dependents"])).otherwise(float(limits["alone"]))
            # TAXSIM returns before the worksheet for these returns.
            early = (aged > 0) & (agi <= limit)
            statax = pl.when(early).then(0.0).otherwise(statax)
            values = {k: pl.when(early).then(0.0).otherwise(v) for k, v in values.items()}
        df = df.with_columns(siitax=statax * flate)
        return with_state_detail(df, agi=agi, **values)

    # --- State income tax added back for federal itemizers ---
    salt = pl.col("state_sales_or_income_tax_ded")
    itemized = pl.col("itemized_deduction")
    deducp = pl.col("itemized_before_limit")
    if y <= 2017:
        tx = salt
        if y >= 1991:
            if y <= 2012:
                phas92 = p["itemized_limit_threshold"] * p.num("itemized_limit_index") / sep
            else:
                phas92 = (
                    p.num("itemized_limit_index_2013") * p["itemized_limit_threshold_2013"]
                    * by_filing_status(p["itemized_limit_status_factor_2013"])
                )
            dedphs = deducp - itemized
            tx = pl.when((fed_agi > phas92) & (deducp > 0)).then(salt - dedphs * salt / deducp).otherwise(salt)
            # The federal zero bracket is 0 for itemizers.
            tx = pl.min_horizontal(tx, itemized)
    else:
        room = (p["salt_cap_2018"] / sep - pl.col("proptax") - pl.col("otheritem")).clip(0, None)
        if y >= 2025 and behavior.mode.value == "statutory":
            # The federal cap is no longer $10,000; use what the federal return actually deducted.
            room = (pl.col("salt_capped") - pl.col("proptax") - pl.col("otheritem")).clip(0, None)
        tx = pl.min_horizontal(salt, pl.min_horizontal(itemized.clip(0, None), room))
    tx = pl.when(fed_itemizes).then(tx).otherwise(0.0)

    # --- Long-term capital gain deduction ---
    dedgan = pl.lit(0.0)
    if y >= 1990:
        dedgan = p.num("capital_gain_deduction") * pl.col("ltcg").clip(0, None)

    add = pl.lit(0.0)
    if y == 2003:
        zbr = pl.col("standard_deduction")
        added = pl.min_horizontal(zbr, (fed_agi - pl.col("personal_exemptions")).clip(0, None))
        added = (added - p["married_standard_deduction_2003"]).clip(0, None) / sep
        add = pl.when(~fed_itemizes & (is_joint | is_sep)).then(added).otherwise(0.0)

    # --- Federal itemized and exemption phase-out adjustment (itemizers) ---
    adjust = pl.lit(0.0)
    if 1987 <= y <= 2017:
        exemps = federal_exemption_count(y) / flate
        unreduced = deducp + exemps * float(resolve_year(FEDERAL_EXEMPTION_PARAMS["amount"], y))
        reduced = itemized + pl.col("personal_exemptions")
        adjust = pl.when(fed_itemizes).then((unreduced - reduced).clip(0, None)).otherwise(0.0)

    if y >= 2025 and behavior.mode.value == "statutory":
        increase = by_filing_status({k: float(v) for k, v in resolve_year(p["obbba_standard_deduction_increase"], y).items()})
        add = add + pl.when(fed_itemizes).then(0.0).otherwise(increase) + pl.col("senior_deduction")
    exemp = pl.lit(0.0)
    if y >= 2018:
        exemp = p.num("dependent_exemption") * (depx + pl.col("dep6"))
    taxinc = (agi + tx - dedgan + add - exemp - adjust).clip(0, None)
    df, (taxinc,) = checkpoint(df, sc_taxinc=taxinc)

    # --- Tax ---
    brackets = p["brackets"]
    if y <= 1986:
        amounts = p["exemption_1985"][y]
        single_income = (taxinc - amounts["single"]).clip(0, None)
        married_income = (taxinc - amounts["married"] / sep).clip(0, None)
        statax = pl.when(is_single).then(bracket_tax(single_income, brackets["1985_single"])).otherwise(
            bracket_tax(married_income, brackets["1985_married"])
        )
        rate = pl.when(is_single).then(bracket_rate(single_income, brackets["1985_single"])).otherwise(
            bracket_rate(married_income, brackets["1985_married"])
        )
        detail_taxinc = pl.when(is_single).then(single_income).otherwise(married_income)
    else:
        if y <= 1989:
            table = scale_brackets(brackets[1987], p.num("index"))
        elif y == 1990:
            table = brackets[1990]
        elif y <= 2000:
            table = scale_brackets(brackets[1991], p.num("index"))
        elif y == 2001:
            table = brackets[2001]
        else:
            table = scale_brackets(brackets[2002] if y <= 2008 else brackets[2009], p.num("index_2002"))
            if y >= 2022:
                table = brackets[y]  # statutory dollars
        statax = bracket_tax(taxinc, table)
        rate = bracket_rate(taxinc, table)
        detail_taxinc = taxinc
    df, (statax,) = checkpoint(df, sc_tax_before_credits=statax)

    # --- Credits (nonrefundable) ---
    nccp = depx.clip(None, 2).floor()
    expenses = pl.min_horizontal(p.num("child_care_expense_per_child") * nccp, pl.col("childcare"))
    chcr = p["child_care_rate"] * expenses
    if y >= 2002:
        chcr = pl.min_horizontal(p.num("child_care_cap_per_child") * nccp, chcr)
    chcr = pl.when(is_sep).then(0.0).otherwise(chcr)

    twocrd = pl.lit(0.0)
    if y >= 1987:
        busnes = (pl.col("psemp") + pl.col("ssemp")).clip(0, None) - 0.5 * pl.col("setax")
        husb = pl.col("pwages").clip(0, None) + 0.5 * busnes
        wife = pl.col("swages").clip(0, None) + 0.5 * busnes
        if y >= 2018:
            # TAXSIM gives the husband both business incomes and the wife
            # both professional incomes.
            husb = husb + pl.col("pbusinc") + pl.col("sbusinc")
            wife = wife + pl.col("pprofinc") + pl.col("sprofinc")
        earnls = pl.min_horizontal(husb, wife).clip(0, p.num("two_earner_earnings_max"))
        twocrd = pl.when(is_joint).then(p["two_earner_credit_rate"] * earnls).otherwise(0.0)

    earncr = p.num("eitc_rate") * pl.col("eitc") if y >= 2018 else pl.lit(0.0)
    statax = (statax - chcr - twocrd - earncr).clip(0, None)

    df = df.with_columns(siitax=statax * flate)
    return with_state_detail(
        df,
        agi=agi,
        exemptions=exemp,
        taxable_income=detail_taxinc,
        child_care_credit=chcr,
        eic=earncr,
        credits=chcr + twocrd + earncr,
        rate=rate,
    )
