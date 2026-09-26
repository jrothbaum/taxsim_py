"""Colorado individual income tax calculator."""

import polars as pl

from taxsim_py.engine.brackets import bracket_rate, bracket_tax
from taxsim_py.engine.inputs import aged_count, taxpayer_count
from taxsim_py.engine.schema import PARAMETERS_ROOT, load_yaml, resolve_year
from taxsim_py.engine.state import dividend_exclusion_addback, household_income, interpolate_table as _table_lookup, with_default as _with_default, with_defaults, with_state_detail
from taxsim_py.engine.state_extrapolation import deflate_for_extrapolation, resolve_state_year

STATE_ADJUSTMENT_PARAMS = load_yaml(PARAMETERS_ROOT / "national" / "state_adjustments.yaml")
CO_PARAMS = load_yaml(PARAMETERS_ROOT / "states" / "co" / "income_tax.yaml")
PRE1987_PARAMS = load_yaml(PARAMETERS_ROOT / "national" / "pre1987.yaml")
FEDERAL_AMT_PARAMS = load_yaml(PARAMETERS_ROOT / "national" / "amt.yaml")

_STATUSES = ["single", "married_joint", "married_separate", "head_of_household"]


def compute_co_tax(df: pl.DataFrame, year: int) -> pl.DataFrame:
    effective_year, flate = resolve_state_year(year)
    p = CO_PARAMS
    df = with_defaults(df, (
        "proptax", "otheritem", "mortgage", "dividends", "ltcg", "stcg", "intrec",
        "depx", "dep18", "childcare", "psemp", "ssemp",
    ))

    # Year>2021 (LASTAT): no real CO law exists in the oracle past this
    # point - it deflates every dollar-valued input by `flate`, runs 2021's
    # REAL law (via `effective_year`, already forced to 2021 by
    # `resolve_state_year`), then reinflates the final tax by the same
    # `flate` (see engine/state_extrapolation.py's own docstring and
    # taxsim_2022_10_21.f:44-65). A no-op for year<=2021 (`flate==1.0`).
    # Dependent/exemption COUNTS (depx, dep18) are NOT in this list -
    # only genuinely dollar-valued fields get scaled, matching the
    # source's own `data(11-99)`/`comnew(1-98)` scaling range (position
    # <11, i.e. counts like depx, is excluded there too).
    df = with_defaults(df, ("credit", "pre1987_taxbc", "pre1987_earncr", "pre1987_deduc", "federal_chcr", "regular_tax"))
    df = df.with_columns(
        co_household_income=household_income(
            float(resolve_year(STATE_ADJUSTMENT_PARAMS["household_income_dividend_adjustment"], effective_year)),
            float(resolve_year(STATE_ADJUSTMENT_PARAMS["household_income_record_adjustment"], effective_year)),
        )
    )
    df = deflate_for_extrapolation(
        df,
        flate,
        [
            "pensions", "gssi", "taxable_social_security", "rentpaid", "co_household_income", "federal_chcr",
            "regular_tax",
            "agi", "pwages", "swages", "proptax", "otheritem", "mortgage",
            "dividends", "ltcg", "stcg", "intrec", "childcare", "psemp", "ssemp",
            "ui", "fiitax", "taxable_income", "itemized_deduction", "standard_deduction",
            "state_sales_or_income_tax_ded", "eitc", "ccc",
        ],
    )

    df = df.with_columns(
        co_sep=pl.when(pl.col("filing_status") == "married_separate").then(2.0).otherwise(1.0),
        co_txp=taxpayer_count(),
        # Federal exemption count (`comnew(68)`), including the aged.
        co_exemps=taxpayer_count() + pl.col("depx") + aged_count(),
    )
    pension_cap = float(resolve_year(p["pension_exclusion"], effective_year))
    df = df.with_columns(
        co_pension_exclusion=pl.when(aged_count() > 0).then(
            pl.min_horizontal(pl.col("pensions") + pl.col("taxable_social_security"), pension_cap * pl.col("co_txp")).clip(0, None)
        ).otherwise(0.0)
    )
    # Pre-1987 property tax and heating credits for taxpayers 65 or older.
    if effective_year <= 1986:
        single_like = pl.col("filing_status").is_in(["single", "head_of_household"])
        def by_group(table: dict) -> pl.Expr:
            return pl.when(single_like).then(float(resolve_year(table["single"], effective_year))).otherwise(
                float(resolve_year(table["other"], effective_year))
            )
        pc, hc = p["property_credit"], p["heating_credit"]
        income = pl.col("co_household_income") * pl.when(pl.col("co_sep") == 2).then(2.0).otherwise(1.0)
        eligible = (aged_count() > 0) & (income <= by_group(pc["income_limit"]))
        propcr = (
            float(resolve_year(pc["maximum"], effective_year))
            - (income - by_group(pc["phaseout_start"])).clip(0, None) * float(resolve_year(pc["phaseout_rate"], effective_year))
        ).clip(0, None)
        propcr = pl.min_horizontal(propcr, pl.col("proptax") + 0.2 * pl.col("rentpaid"))
        if effective_year == 1977:
            propcr = propcr + 0.1 * (pl.col("proptax") + 0.15 * pl.col("rentpaid"))
        if effective_year >= 1979:
            fuelcr = (
                float(hc["maximum"])
                - (income - by_group(hc["phaseout_start"])) * float(resolve_year(hc["phaseout_rate"], effective_year))
            ).clip(0, None)
            fuelcr = pl.min_horizontal(fuelcr, 0.1 * pl.col("rentpaid"))
        else:
            fuelcr = pl.lit(0.0)
        df = df.with_columns(
            co_propcr=pl.when(eligible).then(propcr).otherwise(0.0),
            co_fuelcr=pl.when(eligible).then(fuelcr).otherwise(0.0),
        )
    else:
        df = df.with_columns(co_propcr=pl.lit(0.0), co_fuelcr=pl.lit(0.0))

    if effective_year <= 1986:
        aif = float(resolve_year(p["standard_deduction_aif_pre1987"], effective_year))
        # AGI: federal AGI plus/minus a handful of real CO-only add/
        # subtractions - the 1982-1986 two-earner-deduction addback
        # (`comnew(32)`=twoded - recomputed locally, same technique
        # federal_pre1987.py itself uses internally but doesn't expose)
        # and the pre-1987 dividend exclusion (reused from pre1987.yaml,
        # same technique as AZ/AR) both apply; `xjobs()` and the pension
        # exclusion are both confirmed inert for this schema (see module
        # docstring).
        adjustment = float(resolve_year(STATE_ADJUSTMENT_PARAMS["household_income_dividend_adjustment"], effective_year))
        div_addback = dividend_exclusion_addback(effective_year, adjustment) if effective_year >= 1980 else pl.lit(0.0)
        df = df.with_columns(co_agi_1=pl.col("agi") + div_addback)
        if 1982 <= effective_year <= 1986:
            two_earner_rate = float(resolve_year(PRE1987_PARAMS["two_earner_deduction_rate"], effective_year))
            two_earner_cap = float(resolve_year(PRE1987_PARAMS["two_earner_deduction_cap"], effective_year))
            wife = pl.min_horizontal(pl.col("pwages"), pl.col("swages")).clip(0, None)
            twoded = pl.when(pl.col("filing_status") == "married_joint").then(
                (two_earner_rate * wife).clip(0, two_earner_cap)
            ).otherwise(0.0)
            df = df.with_columns(co_agi_1=pl.col("co_agi_1") + twoded)
        if effective_year >= 1980:
            df = df.with_columns(
                co_agi_1=pl.col("co_agi_1")
                - (pl.col("intrec")).clip(0, 200.0 * pl.col("co_txp"))
                - (pl.col("dividends") + 0.001).clip(0, 200.0 * pl.col("co_txp"))
            )
        # Pensions and taxable Social Security excluded, for returns with a
        # taxpayer 65 or older.
        df = df.with_columns(co_agi=pl.col("co_agi_1") - pl.col("co_pension_exclusion"))
        df = df.with_columns(co_ag=pl.col("co_agi").clip(0, None))

        # `fedded = twn(max(taxbc-credit-earncr,0)*(agi/comnew(2)),0,taxmax)`
        # - approximated as federal tax liability directly (matching the
        # AL/AR precedent of using `fiitax` for an analogous "federal tax
        # paid" deduction where the exact taxbc-credit-earncr reconstruction
        # isn't cheaply available) since AGI/comnew(2) is ~1 whenever this
        # project's own AGI matches federal's (true here - no CO-specific
        # AGI-narrowing inputs in scope).
        # `fedded`: tax before credits less credits and the earned income
        # credit, scaled by Colorado AGI over federal AGI.
        fedded = (pl.col("pre1987_taxbc") - pl.col("credit") - pl.col("pre1987_earncr")).clip(0, None)
        share = pl.when((pl.col("co_agi") > 0) & (pl.col("agi") > 0)).then(pl.col("co_agi") / pl.col("agi")).otherwise(1.0)
        df = df.with_columns(co_fedded=pl.min_horizontal(fedded * share, fedded).clip(0, None))

        # --- Standard deduction ---
        if effective_year <= 1979:
            is_sep = pl.col("filing_status") == "married_separate"
            minex_std = pl.when(is_sep).then(500.0 * aif).otherwise(1000.0 * aif / pl.col("co_sep"))
            stded_std = pl.when(is_sep).then(
                pl.min_horizontal(500.0 * aif, 0.1 * pl.col("co_ag"))
            ).otherwise(pl.min_horizontal(0.1 * pl.col("co_ag"), 1000.0 * aif / pl.col("co_sep")))
            exemps_placeholder = pl.col("co_exemps")  # comnew(68)
            allow_sep = aif * pl.min_horizontal(500.0, 100.0 + exemps_placeholder * 100.0)
            sub = exemps_placeholder * 100.0
            sub = sub + (0.5 * (pl.col("co_ag") - (1000.0 + exemps_placeholder * 750.0)).clip(0, None))
            sub = (800.0 - sub).clip(0, None)
            allow_std = pl.min_horizontal(1000.0 * aif, 200.0 + 100.0 * exemps_placeholder + sub)
            df = df.with_columns(
                co_stded=pl.when(is_sep).then(pl.max_horizontal(stded_std, allow_sep)).otherwise(
                    pl.max_horizontal(stded_std, allow_std)
                )
            )
        else:
            df = df.with_columns(co_stded=1000.0 * aif / pl.col("co_sep"))

        # --- Itemized deduction: raw proptax+otheritem+mortgage (the
        # comnew(24)/comnew(30) ratio simplifies the same way it does for
        # AL/IL/AZ<=1990) + the real, arbitrary-distance gasoline-tax
        # addback (`$49/exemption + $26/dependent`). ---
        # Federal itemized deductions (zero when not itemizing federally)
        # less state income or sales tax, plus a gasoline tax allowance.
        itemizing = pl.col("pre1987_itemizes").cast(pl.Float64)
        df = df.with_columns(
            co_xitded=itemizing * (pl.col("pre1987_deduc") - pl.col("state_sales_or_income_tax_ded"))
            + float(p["gasoline_tax_deduction_per_exemption"]) * pl.col("co_txp")
            + float(p["gasoline_tax_deduction_per_dependent"]) * pl.col("depx")
        )
        df = df.with_columns(co_deduc=pl.max_horizontal(pl.col("co_xitded"), pl.col("co_stded")))

        # --- Exemption ---
        pe = float(p["personal_exemption_amount_1977"]) if effective_year == 1977 else float(p["personal_exemption_amount_1978plus"]) * aif
        df = df.with_columns(co_exemp=pe * pl.col("co_exemps"))

        df = df.with_columns(
            co_taxinc=(pl.col("co_agi") - pl.col("co_deduc") - pl.col("co_exemp") - pl.col("co_fedded")).clip(0, None)
        )

        # --- Bracket tax ---
        if effective_year <= 1978:
            brackets = p["brackets_pre1979"]
        elif effective_year <= 1983:
            brackets = p["brackets_1979_1983"]
        else:
            brackets = p["brackets_1984_1986"]
        brackets_scaled = [[lo * aif, rate] for lo, rate in brackets]
        df = df.with_columns(co_statax=bracket_tax(pl.col("co_taxinc"), brackets_scaled))
        detail = {
            "exemptions": pl.col("co_exemp"),
            "standard_deduction": pl.col("co_stded"),
            "itemized_deductions": pl.col("co_xitded"),
            "rate": bracket_rate(pl.col("co_taxinc"), brackets_scaled),
        }

        surtax = float(resolve_year(p["surtax_pre1987"], effective_year))
        df = df.with_columns(co_statax=pl.col("co_statax") * surtax)

        # --- 2% surcharge on high interest+dividend income ---
        threshold = float(p["surcharge_threshold_pre1979"]) if effective_year <= 1978 else float(p["surcharge_threshold_1979plus"])
        rate = float(p["surcharge_rate_low_years"]) if effective_year <= 1978 else float(p["surcharge_rate_later_years"])
        df = df.with_columns(
            co_statax=pl.col("co_statax")
            + rate * (pl.col("dividends") + pl.col("intrec") - threshold * pl.col("co_txp")).clip(0, None)
        )

        # --- Food credit (1977-1979 only) ---
        if effective_year <= 1979:
            foody = pl.col("co_ag") / (pl.col("co_txp") + pl.col("depx"))
            base_amt = (_table_lookup(foody, p["food_credit_table_pre1980"]) * aif).round()
            # `mst.eq.3.or.mst.eq.6` - married_separate ONLY (mst=3 is
            # unused by this project's own mstat mapping) - NOT
            # head_of_household, despite HoH sharing this flat-rate
            # branch in several OTHER states' analogous formulas.
            is_sep = pl.col("filing_status") == "married_separate"
            foodcr = (pl.col("co_txp") + pl.col("depx")) * base_amt
            if effective_year >= 1978:
                flat_sep = float(p["food_credit_table_pre1980"][-1][1]) * (pl.col("co_txp") + pl.col("depx"))
                foodcr = pl.when(is_sep).then(flat_sep).otherwise(foodcr)
            df = df.with_columns(co_foodcr=foodcr)
        else:
            df = df.with_columns(co_foodcr=pl.lit(0.0))

        df = df.with_columns(co_amt=pl.lit(0.0))
    else:
        # --- 1987+: flat rate on FEDERAL TAXABLE INCOME directly ---
        df = df.with_columns(co_agi=pl.col("agi"))
        df = _with_default(df, "ui")
        taxinc = pl.col("taxable_income")
        if effective_year == 2020:
            # 2020: UI fully taxable for CO even though federal excludes
            # up to $10,200/spouse (`taxinc=taxinc+data(82)-comnew(78)` -
            # add back gross UI, remove federal's own already-taxed
            # portion, netting to adding back exactly the EXCLUDED
            # amount). Recomputed locally from raw ui/sui using the same
            # $10,200/spouse CARES exclusion federal.py itself applies
            # (not exposed as a column there).
            ui_total = pl.col("ui")
            excl_spouse = pl.min_horizontal(pl.col("sui"), 10200.0)
            excl_primary = (ui_total - pl.col("sui")).clip(0, 10200.0)
            excluded = pl.when(pl.col("agi") - excl_spouse - excl_primary < 150000.0).then(
                excl_spouse + excl_primary
            ).otherwise(0.0)
            taxinc = taxinc + excluded
        taxinc = taxinc.clip(0, None)

        # State-income-tax-claimed-as-a-federal-itemized-deduction addback
        # (1992+, only when the federal return itemized) -
        # `min(data(50),comnew(24)-comnew(3))`. Empirically (via oracle
        # probe), `comnew(3)` behaves as $0 for THIS specific formula even
        # though the marriage-penalty formula below needs it treated as
        # the real federal standard deduction - not fully reconciled, but
        # matching the oracle takes priority; see that note for context.
        if effective_year >= 1992 and "itemized_deduction" in df.collect_schema().names() and "itemizes" in df.collect_schema().names():
            addback = pl.when(pl.col("itemizes")).then(
                pl.min_horizontal(pl.col("state_sales_or_income_tax_ded").clip(0, None), pl.col("itemized_deduction"))
            ).otherwise(0.0)
            taxinc = taxinc + addback

        # 2001+ Qualifying Charitable Contributions for non-itemizers:
        # confirmed always $0 here (`comnew(23)`/`char` - charity_cash is
        # federal.py's own disclosed non-drivable gap), so no-op.

        # 2000-2002 Marriage Penalty Subtraction (joint filers only).
        # `comnew(3)`=zbr is NOT a dead pre-1987 relic in `law87` - it's
        # reassigned there to the FEDERAL standard deduction amount
        # (`zbr=zbrack(nfile,lawyr)/sepret+...`, taxsim_2022_10_21.f:
        # 24262) - confirmed via a live oracle probe showing the real
        # subtraction is ~$1,450 (xmar-stded), not the full $8,800 flat
        # `xmar` a first pass assumed by treating comnew(3) as $0.
        # Pension exclusion for taxpayers 65 or older, before the marriage
        # subtraction.
        taxinc = (taxinc - pl.col("co_pension_exclusion")).clip(0, None)
        if 2000 <= effective_year <= 2002 and "itemizes" in df.collect_schema().names():
            xmar = float(p["marriage_penalty_subtraction_2000_2002"][effective_year])
            is_joint = pl.col("filing_status") == "married_joint"
            zbr = pl.col("standard_deduction")
            # Not floored: a standard deduction above `xmar` adds the excess back.
            sub_not_itemized = (taxinc - (xmar - zbr)).clip(0, None)
            itemized_beats_std = pl.col("itemized_deduction") > zbr
            sub_itemized = pl.when(itemized_beats_std).then(
                (taxinc - (xmar - pl.col("itemized_deduction")).clip(0, None)).clip(0, None)
            ).otherwise(taxinc)
            taxinc_marriage = pl.when(~pl.col("itemizes")).then(sub_not_itemized).otherwise(sub_itemized)
            taxinc = pl.when(is_joint).then(taxinc_marriage).otherwise(taxinc)

        if 2001 <= effective_year <= 2002:
            taxinc = pl.when(pl.col("filing_status") == "married_joint").then(
                (taxinc - p["aged_marriage_subtraction_2001"] * aged_count()).clip(0, None)
            ).otherwise(taxinc)
        df = df.with_columns(co_taxinc=taxinc)

        rate = float(resolve_year(p["flat_rate_by_year"], effective_year))
        df = df.with_columns(co_statax=pl.col("co_taxinc") * rate)
        detail = {"rate": rate}

        # --- CO's own mini-AMT: flat rate on the federal AMT base ---
        amt_income = pl.col("agi") - pl.when(pl.col("itemizes")).then(pl.col("mortgage")).otherwise(0.0)
        exemption = pl.lit(None, dtype=pl.Float64)
        threshold = pl.lit(None, dtype=pl.Float64)
        for status in _STATUSES:
            e = float(resolve_year(FEDERAL_AMT_PARAMS["exemption"][status], effective_year))
            t = float(resolve_year(FEDERAL_AMT_PARAMS["exemption_phaseout_threshold"][status], effective_year))
            exemption = pl.when(pl.col("filing_status") == status).then(pl.lit(e)).otherwise(exemption)
            threshold = pl.when(pl.col("filing_status") == status).then(pl.lit(t)).otherwise(threshold)
        phaseout_rate = float(resolve_year(FEDERAL_AMT_PARAMS["exemption_phaseout_rate"], effective_year))
        exemption_after_phaseout = (exemption - phaseout_rate * (amt_income - threshold).clip(0, None)).clip(0, None)
        amt_base = (amt_income - exemption_after_phaseout).clip(0, None)
        amt_rate = float(resolve_year(p["amt_rate_by_year"], effective_year))
        df = df.with_columns(co_altax=amt_rate * amt_base)
        df = df.with_columns(co_statax=pl.col("co_statax") + (pl.col("co_altax") - pl.col("co_statax")).clip(0, None))

        df = df.with_columns(co_foodcr=pl.lit(0.0), co_amt=pl.lit(0.0))

    # --- Sales Tax Refund (1997-2001, 2005, 2015, 2021) ---
    # Modified AGI includes all Social Security benefits.
    coagi = pl.col("agi") - pl.col("taxable_social_security") + pl.col("gssi")
    refund_tables = {
        1997: p["sales_tax_refund_1997"], 1998: p["sales_tax_refund_1998"],
        1999: p["sales_tax_refund_1999"], 2000: p["sales_tax_refund_2000"],
        2001: p["sales_tax_refund_2001"], 2015: p["sales_tax_refund_2015"],
        2021: p["sales_tax_refund_2021"],
    }
    if effective_year in refund_tables:
        df = df.with_columns(co_salesrefund=_table_lookup(coagi, refund_tables[effective_year]) * pl.col("co_txp"))
    elif effective_year == 2005:
        df = df.with_columns(co_salesrefund=float(p["sales_tax_refund_2005"]) * pl.col("co_txp"))
    else:
        df = df.with_columns(co_salesrefund=pl.lit(0.0))

    # --- Child Care Credit ---
    # Federal child care credit (`comnew(53)`) up to tax before credits.
    child_fed = pl.min_horizontal(pl.col("federal_chcr"), pl.col("regular_tax")).clip(0, None)
    if effective_year in (1996, 1997) or effective_year >= 2002:
        rate = _table_lookup(pl.col("agi").clip(0, None), p["child_care_credit_table_1996plus"])
        df = df.with_columns(co_chcr=rate * child_fed)
    elif effective_year == 1998:
        df = df.with_columns(co_chcr=float(p["child_care_credit_rate_1998"]) * child_fed)
    elif effective_year == 1999:
        rate = _table_lookup(pl.col("agi").clip(0, None), p["child_care_credit_table_1999"])
        df = df.with_columns(co_chcr=rate * child_fed)
    elif 2000 <= effective_year <= 2001:
        rate = _table_lookup(pl.col("agi").clip(0, None), p["child_care_credit_table_2000_2001"])
        df = df.with_columns(
            co_chcr=(rate * child_fed - float(p["child_care_credit_dependent_offset_2000_2001"]) * pl.col("depx")).clip(0, None)
        )
    else:
        df = df.with_columns(co_chcr=pl.lit(0.0))

    # --- Earned Income Credit ---
    eitc_fed = pl.col("eitc").clip(0, None) if "eitc" in df.collect_schema().names() else pl.lit(0.0)
    if effective_year == 1999:
        df = df.with_columns(co_earncr=float(p["eitc_rate_1999"]) * eitc_fed)
    elif (2000 <= effective_year <= 2001) or effective_year >= 2015:
        df = df.with_columns(co_earncr=float(p["eitc_rate_2000_2001_2015plus"]) * eitc_fed)
    else:
        df = df.with_columns(co_earncr=pl.lit(0.0))

    # `statax=max(0,statax-credit)-cr-earncr-chcr-child` - the pre-1987
    # NON-refundable credit pool (`credit`=propcr+fuelcr+foodcr+itc+encr,
    # only `foodcr` ever nonzero here) is floored at $0 FIRST; the
    # refundable ones (sales tax refund, EITC, child care credit) are
    # then subtracted WITHOUT a further floor, so the final result can go
    # negative (a real refund below $0 liability).
    df = df.with_columns(
        co_statax=(pl.col("co_statax") - pl.col("co_foodcr") - pl.col("co_propcr") - pl.col("co_fuelcr")).clip(0, None)
    )
    df = df.with_columns(
        siitax=(pl.col("co_statax") - pl.col("co_salesrefund") - pl.col("co_chcr") - pl.col("co_earncr")) * flate
    )
    return with_state_detail(
        df,
        agi=pl.col("co_agi"),
        taxable_income=pl.col("co_taxinc"),
        child_care_credit=pl.col("co_chcr"),
        eic=pl.col("co_earncr"),
        credits=pl.col("co_propcr") + pl.col("co_fuelcr") + pl.col("co_foodcr") + pl.col("co_salesrefund")
        + pl.col("co_earncr") + pl.col("co_chcr"),
        **detail,
    )
