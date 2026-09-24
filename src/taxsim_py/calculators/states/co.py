"""Colorado individual income tax (`cotax`, taxsim_2022_10_21.f:2930-3260,
state id 6). See parameters/states/co/income_tax.yaml for the full scope
note - much smaller than Arkansas/California: for 1987+ Colorado taxes
FEDERAL TAXABLE INCOME directly at a flat rate (the simplest federal-
conformity mechanism of any state built so far), plus its own mini-AMT
(a flat rate on the federal AMT base, added on top whenever it exceeds
CO's own regular tax).

Full real-law range (1977-2021) validated via scripts/validate_states.py:
2,057/2,070 exact (99.4%). Two real bugs found via live oracle probes:
`comnew(3)` (`zbr`) is NOT a dead pre-1987 relic in `law87` - it's
reassigned there to the FEDERAL standard deduction amount, which the
2000-2002 Marriage Penalty Subtraction formula genuinely needs (a first
pass assuming it was $0 overstated that subtraction by the filer's full
standard deduction); and the 1982-1986 two-earner-deduction addback
(`comnew(32)`), initially skipped as an acknowledged gap, turned out to
matter even though it's capped/reversed elsewhere - fixed by recomputing
it locally (same technique `federal_pre1987.py` uses internally without
exposing it). Oddly, the SEPARATE 1992+ state-tax-itemized-deduction
addback (`comnew(24)-comnew(3)`) empirically needs `comnew(3)` treated as
$0 despite reading the identical variable - not fully reconciled, but
confirmed correct by direct comparison against the oracle rather than
assumed from one formula to the other.

Remaining 13 failures, not chased further: ~6 sub-$2 residuals on
dividend-income cases (unisolated, likely a small rounding artifact in
the pre-1987 AGI reconstruction) and 7 cases (married_separate, $260,000
wages, 2006-2012) showing the same self-referential SALT-feedback-loop
sensitivity already documented for California/Arizona - CO's own state-
tax-paid addback interacts with the 3-iteration federal/state fixed point
in a way that's more sensitive for married_separate specifically at this
income level.
"""

import polars as pl

from taxsim_py.engine.brackets import bracket_tax
from taxsim_py.engine.schema import PARAMETERS_ROOT, load_yaml, resolve_year
from taxsim_py.engine.state_extrapolation import deflate_for_extrapolation, resolve_state_year

CO_PARAMS = load_yaml(PARAMETERS_ROOT / "states" / "co" / "income_tax.yaml")
PRE1987_PARAMS = load_yaml(PARAMETERS_ROOT / "national" / "pre1987.yaml")
FEDERAL_AMT_PARAMS = load_yaml(PARAMETERS_ROOT / "national" / "amt.yaml")

_STATUSES = ["single", "married_joint", "married_separate", "head_of_household"]


def _with_default(df: pl.DataFrame, column: str, default: float = 0.0) -> pl.DataFrame:
    if column in df.columns:
        return df
    return df.with_columns(pl.lit(default).alias(column))


def _table_lookup(income: pl.Expr, rows: list[list[float]]) -> pl.Expr:
    """`tablki`-style: linear interpolation between adjacent (threshold,
    value) points; flat at the first value below the first threshold and
    at the last value at/above the last (finite) threshold."""
    thresholds = [r[0] for r in rows[:-1]]
    values = [r[1] for r in rows]
    expr = pl.lit(values[-1])
    for i in range(len(thresholds) - 1, -1, -1):
        t_hi = thresholds[i]
        v_hi = values[i]
        if i == 0:
            below = pl.lit(v_hi)
        else:
            t_lo = thresholds[i - 1]
            v_lo = values[i - 1]
            w = (income - t_lo) / (t_hi - t_lo)
            below = w * v_lo + (1 - w) * v_hi if v_hi > v_lo else w * v_hi + (1 - w) * v_lo
        expr = pl.when(income < t_hi).then(below).otherwise(expr)
    return expr


def compute_co_tax(df: pl.DataFrame, year: int, force_itemize: bool | None = None) -> pl.DataFrame:
    effective_year, flate = resolve_state_year(year)
    p = CO_PARAMS
    for col in (
        "proptax", "otheritem", "mortgage", "dividends", "ltcg", "stcg", "intrec",
        "depx", "dep18", "childcare", "psemp", "ssemp",
    ):
        df = _with_default(df, col)

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
    df = deflate_for_extrapolation(
        df,
        flate,
        [
            "agi", "pwages", "swages", "proptax", "otheritem", "mortgage",
            "dividends", "ltcg", "stcg", "intrec", "childcare", "psemp", "ssemp",
            "ui", "fiitax", "taxable_income", "itemized_deduction", "standard_deduction",
            "state_sales_or_income_tax_ded", "eitc", "ccc",
        ],
    )

    df = df.with_columns(
        co_sep=pl.when(pl.col("filing_status") == "married_separate").then(2.0).otherwise(1.0),
        co_txp=pl.when(pl.col("filing_status") == "married_joint").then(2.0).otherwise(1.0),
    )

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
        divexc_expr = pl.lit(None, dtype=pl.Float64)
        for status in _STATUSES:
            v = float(resolve_year(PRE1987_PARAMS["dividend_exclusion"][status], effective_year))
            divexc_expr = pl.when(pl.col("filing_status") == status).then(pl.lit(v)).otherwise(divexc_expr)
        dividends_fudge = pl.col("dividends") + 0.001
        div_addback = pl.when(effective_year >= 1980).then(
            pl.min_horizontal(dividends_fudge, divexc_expr).clip(0, None)
        ).otherwise(0.0)
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
        df = df.with_columns(co_agi=pl.col("co_agi_1"))
        df = df.with_columns(co_ag=pl.col("co_agi").clip(0, None))

        # `fedded = twn(max(taxbc-credit-earncr,0)*(agi/comnew(2)),0,taxmax)`
        # - approximated as federal tax liability directly (matching the
        # AL/AR precedent of using `fiitax` for an analogous "federal tax
        # paid" deduction where the exact taxbc-credit-earncr reconstruction
        # isn't cheaply available) since AGI/comnew(2) is ~1 whenever this
        # project's own AGI matches federal's (true here - no CO-specific
        # AGI-narrowing inputs in scope).
        df = df.with_columns(co_fedded=pl.col("fiitax").clip(0, None))

        # --- Standard deduction ---
        if effective_year <= 1979:
            is_sep = pl.col("filing_status") == "married_separate"
            minex_std = pl.when(is_sep).then(500.0 * aif).otherwise(1000.0 * aif / pl.col("co_sep"))
            stded_std = pl.when(is_sep).then(
                pl.min_horizontal(500.0 * aif, 0.1 * pl.col("co_ag"))
            ).otherwise(pl.min_horizontal(0.1 * pl.col("co_ag"), 1000.0 * aif / pl.col("co_sep")))
            exemps_placeholder = pl.col("co_txp") + pl.col("depx")  # comnew(68)~=exemps count
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
        df = df.with_columns(
            co_xitded=pl.col("proptax") + pl.col("otheritem") + pl.col("mortgage")
            + float(p["gasoline_tax_deduction_per_exemption"]) * pl.col("co_txp")
            + float(p["gasoline_tax_deduction_per_dependent"]) * pl.col("depx")
        )
        df = df.with_columns(co_deduc=pl.max_horizontal(pl.col("co_xitded"), pl.col("co_stded")))

        # --- Exemption ---
        pe = float(p["personal_exemption_amount_1977"]) if effective_year == 1977 else float(p["personal_exemption_amount_1978plus"]) * aif
        df = df.with_columns(co_exemp=pe * (pl.col("co_txp") + pl.col("depx")))

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
        if effective_year >= 1992 and "itemized_deduction" in df.columns and "itemizes" in df.columns:
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
        if 2000 <= effective_year <= 2002 and "itemizes" in df.columns:
            xmar = float(p["marriage_penalty_subtraction_2000_2002"][effective_year])
            is_joint = pl.col("filing_status") == "married_joint"
            zbr = pl.col("standard_deduction")
            sub_not_itemized = (taxinc - (xmar - zbr).clip(0, None)).clip(0, None)
            itemized_beats_std = pl.col("itemized_deduction") > zbr
            sub_itemized = pl.when(itemized_beats_std).then(
                (taxinc - (xmar - pl.col("itemized_deduction")).clip(0, None)).clip(0, None)
            ).otherwise(taxinc)
            taxinc_marriage = pl.when(~pl.col("itemizes")).then(sub_not_itemized).otherwise(sub_itemized)
            taxinc = pl.when(is_joint).then(taxinc_marriage).otherwise(taxinc)

        df = df.with_columns(co_taxinc=taxinc)

        rate = float(resolve_year(p["flat_rate_by_year"], effective_year))
        df = df.with_columns(co_statax=pl.col("co_taxinc") * rate)

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
    coagi = pl.col("agi")  # comnew(79)/data(91) [SS-related] both $0 here
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
    child_fed = pl.col("ccc").clip(0, None) if "ccc" in df.columns else pl.lit(0.0)
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
    eitc_fed = pl.col("eitc").clip(0, None) if "eitc" in df.columns else pl.lit(0.0)
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
    df = df.with_columns(co_statax=(pl.col("co_statax") - pl.col("co_foodcr")).clip(0, None))
    df = df.with_columns(
        siitax=(pl.col("co_statax") - pl.col("co_salesrefund") - pl.col("co_chcr") - pl.col("co_earncr")) * flate
    )
    return df
