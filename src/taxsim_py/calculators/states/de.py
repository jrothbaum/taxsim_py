"""Delaware individual income tax (`detax`, taxsim_2024_09_21.f:3860-4162,
state id 8). See parameters/states/de/income_tax.yaml for the full scope
note (bracket tables, the confirmed-inert pension/elderly/blind/energy-
credit fields).

Two real, non-obvious mechanics found while building this:
1. `xitded` (itemized deduction) is federal's own RAW, pre-Pease itemized
   total (`comnew(30)`) minus JUST the state-tax-liability feedback term
   (`data(50)`, this project's own `state_sales_or_income_tax_ded`) - the
   SAME "subtract the SALT feedback term back out of the pre-Pease raw
   total" technique already used for AZ (>=1991)/California, not the
   simpler "drop SALT/data(50) entirely" shortcut AL/AR/AZ<=1990 use.
   Delaware then applies its OWN separate Pease-style phaseout on top
   (1991-2017, the same 2/3->1/3->0 multiplier schedule already extracted
   for AL/AZ/CA/AR) - so the base here is `salt_capped + mortgage`
   (pre-Pease, matching California's own choice), NOT federal.py's own
   `itemized_deduction` column (which is POST its own Pease reduction -
   using that would double-apply the phaseout).
2. The dependent/personal exemption COUNT (`num=int(comnew(68))`) is a
   pure count, like IL's own `exemps` - divided by `flate` for an
   extrapolated year the same documented quirk IL's own module note
   covers (parameters/national/state_cpi_extrapolation.yaml). Delaware's
   own source additionally `int()`-truncates this value AFTER whatever
   deflation already happened (it reads `comnew`, i.e. the ALREADY-
   deflated array, not the original) - replicated here as `.floor()`
   applied after deflating, not before (a genuine, if minor, extra
   quirk beyond IL's own no-truncation formula).

EITC (2006+) picks whichever of two credits leaves the filer better off
2021+: a smaller REFUNDABLE 4.5% credit, or a larger NONrefundable 20%
credit - refundable is used only when it would exceed what the
nonrefundable option could actually absorb against current state tax
liability (so the taxpayer never loses out to a arbitrary default).

Full range (1977-2021) plus 2022/2023 (CPI-extrapolated, same mechanism as
every other state - see engine/state_extrapolation.py) validated together
via scripts/validate_states.py: **2,252/2,256 exact (99.8%)**. The 4
residuals are all 2023-only, sub-$1, and trace directly to `de_earncr`'s
own dependence on federal `eitc` - the same already-accepted real-vs-
oracle-2023 EITC table override (see feedback_real_params_over_oracle_bugs
/ project_taxsim_py_port memory) already seen in AL/CA/CO/CT/IL.

Real bugs found and fixed while building this (none obvious from a plain
source read - all caught by comparing against direct oracle probes):
- The pre-1988 standard deduction (`stded=min(texp*1000/sep,.1*agi)`) - a
  first pass dropped the `/sep` term entirely, overstating the deduction
  for married_separate by exactly a factor of `sep` (confirmed via a
  married_separate/$15,000-wages/1980 probe: real std deduction is $500,
  not $1,000).
- `if(law.eq.1987) xitded=xitded*1.12` - a real, ONE-YEAR-ONLY 12%
  multiplier on top of the base itemized-deduction formula, easy to miss
  since no other year in this source has anything like it (confirmed via
  probe: proptax=$4,000/otheritem=$2,000/mortgage=$8,000, single, 1987 -
  real itemized deduction is $15,680 = $14,000*1.12).
- The 2013-2017 Pease-phaseout threshold (`phas92=aif13(law)*250000*
  filing(...)`) - a first pass computed `$100,000-base * 2` ($200,000)
  instead of `$100,000-base * 2.5` ($250,000), understating the phaseout
  threshold and overstating everyone's reduction at high income (caught
  by a very-high-income 2013-2017 sweep, all 3 filing statuses tested).
- The 2021-only unemployment-compensation exclusion (`agi=agi-data(82)`)
  - a first pass summed `ui+pui+sui` as the excluded amount, but `pui`/
  `sui` are a SPLIT of `ui` (this project's own per-spouse UI-exclusion
  bookkeeping - see federal.py's own `ui_total=max(ui,pui+sui)`), not
  additional income layered on top of it. Confirmed via a married_joint/
  ui=$8,000/sui=$4,000 probe: federal AGI only reflects $8,000 of UI
  (matching `ui_total`, not $12,000), and DE's own post-exclusion AGI
  matches subtracting that same $8,000.
- `wages` (federal.py's own pwages+swages sum, used by the married_joint
  earner-split relief mechanic) needed direct deflation for an
  extrapolated year - the same "federal.py's own derived, real-year,
  undeflated column" situation as AR's `wages`/AZ's `salt_capped`
  elsewhere in this project; a first pass deflated `pwages`/`swages`
  individually but forgot the already-materialized `wages` column itself,
  silently breaking every married_joint 2022/2023 case.
"""

import polars as pl

from taxsim_py.engine.brackets import bracket_tax
from taxsim_py.engine.schema import PARAMETERS_ROOT, load_yaml, resolve_year
from taxsim_py.engine.state_extrapolation import deflate_for_extrapolation, resolve_state_year

DE_PARAMS = load_yaml(PARAMETERS_ROOT / "states" / "de" / "income_tax.yaml")


def _with_default(df: pl.DataFrame, column: str, default: float = 0.0) -> pl.DataFrame:
    if column in df.columns:
        return df
    return df.with_columns(pl.lit(default).alias(column))


def compute_de_tax(df: pl.DataFrame, year: int, force_itemize: bool | None = None) -> pl.DataFrame:
    effective_year, flate = resolve_state_year(year)
    p = DE_PARAMS
    for col in ("proptax", "otheritem", "mortgage", "depx", "ui", "pui", "sui"):
        df = _with_default(df, col)
    df = _with_default(df, "state_sales_or_income_tax_ded")
    # `ccc`/`eitc` aren't exposed by federal_pre1987.py (years<=1986) -
    # both DE mechanisms that read them (Child Care Credit, EITC) only
    # apply well after 1986 anyway, but default them so the column
    # references below don't crash on an early year.
    df = _with_default(df, "ccc")
    df = _with_default(df, "eitc")

    df = df.with_columns(
        de_sep=pl.when(pl.col("filing_status") == "married_separate").then(2.0).otherwise(1.0),
        # `data(7)` - self/spouse exemption unit count (1, or 2 ONLY for
        # married_joint - head_of_household is NOT bumped here, unlike its
        # own $3,000/$3,000-style personal-exemption dollar amount
        # elsewhere - same "raw data(7), not the local txp" distinction
        # already found for AZ/CA).
        de_texp=pl.when(pl.col("filing_status") == "married_joint").then(2.0).otherwise(1.0),
        # `comnew(68)` (exemps count: self + spouse-if-joint + dependents) -
        # a pure count, divided by flate for extrapolated years like IL's
        # own `exemps` (see module docstring point 2).
        de_exemps_raw=(
            1.0 + pl.col("depx") + pl.when(pl.col("filing_status") == "married_joint").then(1.0).otherwise(0.0)
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
            "pwages", "swages", "wages", "proptax", "otheritem", "mortgage", "ui", "pui", "sui",
            "agi", "salt_capped", "state_sales_or_income_tax_ded", "eitc", "ccc",
            "de_exemps_raw",
        ],
    )
    df = df.with_columns(de_num=pl.col("de_exemps_raw").floor())

    # --- AGI --- federal AGI directly; Social Security benefits (comnew
    # 79) and the state-tax-refund term (data 22) are both confirmed
    # permanently $0 for this schema (no social-security input, no prior-
    # year refund tracking) - and the whole pension-income exclusion
    # mechanism (data 9/20/72) is likewise inert (no pension/elderly
    # inputs this project's schema populates), so none of that needs
    # implementing. `law.eq.2021` - which, per `resolve_state_year`,
    # is ALSO true for every extrapolated 2022/2023 year (forced to
    # 2021) - fully excludes unemployment compensation from AGI, a real,
    # one-time DE COVID-era provision (distinct from, and layered on top
    # of, whatever federal's own UI exclusion already did).
    df = df.with_columns(de_agi=pl.col("agi"))
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
    if force_itemize is False and effective_year == 1999:
        df = df.with_columns(de_xitded=pl.lit(0.0))

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
    df = df.with_columns(de_chcr=pl.col("ccc").clip(0, None) * ccc_rate)
    if effective_year >= 1999:
        cap = float(resolve_year(p["child_care_credit_cap_1999plus"], effective_year))
        df = df.with_columns(de_chcr=pl.min_horizontal(pl.col("de_chcr"), cap))

    # --- Personal Exemption Credit (1996+) --- the $5-per-exemption
    # energy credit (`data(38)`) is confirmed permanently $0 (same field
    # AZ/CA/CT already found unpopulated), so `credit` here is just chcr
    # + the personal-exemption credit.
    if effective_year >= 1996:
        per_unit = float(resolve_year(p["personal_exemption_credit_per_unit"], effective_year))
        df = df.with_columns(de_pecred=pl.col("de_num") * per_unit)
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
    return df
