"""Kentucky individual income tax calculator."""

import polars as pl

from taxsim_py.calculators.federal_pre1987 import PRE1987_PARAMS
from taxsim_py.engine.brackets import bracket_rate, bracket_tax
from taxsim_py.engine.inputs import aged_count, files_joint, separate_divisor, taxpayer_count
from taxsim_py.engine.schema import PARAMETERS_ROOT, YearParams, load_yaml
from taxsim_py.engine.state import (
    allocate_pension,
    dividend_input_adjustment,
    forced_standard,
    higher_earner_share,
    split_agi,
    interpolate_table,
    unemployment_total,
    with_state_detail,
)
from taxsim_py.behavior import BehaviorProfile, TAXSIM_BEHAVIOR
from taxsim_py.engine.state_extrapolation import deflate_for_extrapolation, resolve_state_year

KY_PARAMS = load_yaml(PARAMETERS_ROOT / "states" / "ky" / "income_tax.yaml")

def compute_ky_tax(df: pl.DataFrame, year: int, behavior: BehaviorProfile = TAXSIM_BEHAVIOR) -> pl.DataFrame:
    effective_year, flate = resolve_state_year(year, behavior, KY_PARAMS)
    p = YearParams(KY_PARAMS, effective_year)

    df = df.with_columns(
        ky_sep=separate_divisor(),
        ky_taxpayers=taxpayer_count(),
    )
    is_joint = files_joint()

    # Self-employment tax and the federal child care credit are not
    # deflated in projected years.
    df = df.with_columns(ky_setax=pl.col("payroll_setax"), ky_ccc=pl.col("federal_chcr"))

    df = deflate_for_extrapolation(df, flate)

    phas92_base = float(p["itemized_limitation_threshold"])
    phas92 = phas92_base / pl.col("ky_sep")
    if 1992 <= effective_year <= 2017:
        phas92 = phas92_base * p.num("itemized_limitation_index") / pl.col("ky_sep")

    # Federal income tax, which can be negative, less self-employment tax and
    # AMT, floored at 0 only at the end.
    ky_fedtax = (pl.col("fiitax") - pl.col("ky_setax") - pl.col("amt").clip(0, None)).clip(0, None)

    # --- AGI ---
    if effective_year <= 1989:
        agi = pl.col("agi") - ky_fedtax
        if p.in_effect("interest_exclusion"):
            ie = p.value("interest_exclusion")
            agi = agi - ie["rate"] * pl.col("intrec").clip(0, float(ie["cap_per_taxpayer"]) * pl.col("ky_taxpayers"))
    else:
        # 1990 adds back federal income tax (`comnew(1)`) even when negative.
        subtra = 0.0 if effective_year != 1990 else pl.col("fiitax")
        agi = pl.col("agi") - subtra

    ui_total = unemployment_total()
    if effective_year in (2009, 2020):
        agi = agi + ui_total - pl.col("taxable_unemployment")
    if effective_year >= 1984:
        agi = agi - pl.col("taxable_social_security")
    if p.in_effect("dividend_exclusion_per_taxpayer"):
        agi = agi - (pl.col("dividends") + dividend_input_adjustment()).clip(0, p.num("dividend_exclusion_per_taxpayer") * pl.col("ky_taxpayers"))
    if 1982 <= effective_year <= 1986:
        p87 = YearParams(PRE1987_PARAMS, effective_year)
        rate_2e = p87.num("two_earner_deduction_rate")
        cap_2e = p87.num("two_earner_deduction_cap")
        lesser_wage = pl.min_horizontal(pl.col("pwages"), pl.col("swages")).clip(0, None)
        twoded = pl.when(is_joint).then((rate_2e * lesser_wage).clip(0, cap_2e)).otherwise(0.0)
        agi = agi + twoded
    retirement_exclusion = pl.lit(0.0)
    if effective_year >= 1995 and not behavior.apply_kentucky_pension_exclusion_schedule:
        # TAXSIM excludes up to $31,110 of each spouse's share, in every year.
        pent, pens = allocate_pension(pl.col("pensions"), 0.0 if effective_year >= 2022 else 25.0)
        retirement_exclusion = pl.min_horizontal(pent, 31110.0) + pl.min_horizontal(pens, 31110.0)
        agi = agi - retirement_exclusion
    elif p.in_effect("retirement_exclusion_share"):
        share = p.num("retirement_exclusion_share")
        cap = p.num("retirement_exclusion_cap")
        # The statutory cap is per taxpayer, with joint pensions split evenly.
        cap_total = cap * pl.col("ky_taxpayers") if behavior.apply_kentucky_pension_cap_per_taxpayer else pl.lit(cap)
        agi = agi - pl.min_horizontal(share * pl.col("pensions"), cap_total)
    if p.in_effect("capital_gains_exclusion"):
        capgn = pl.col("stcg") + pl.col("ltcg")
        ltcg_rate = p.num("capital_gains_exclusion")
        agi = pl.when(capgn > 0).then(agi - ltcg_rate * pl.col("ltcg").clip(0, None)).otherwise(agi)

    df = df.with_columns(ky_agi=agi)

    # --- Standard deduction ---
    ded_pf = p.num("standard_deduction")
    # One standard deduction per return: Kentucky's amount does not vary by filing status, and TAXSIM's reading was not
    # shown to be wrong, so there is a single behavior in both modes.
    df = df.with_columns(ky_stded=pl.lit(ded_pf))

    # --- Itemized deduction ---
    if effective_year <= 1986:
        salt_plus_mortgage = pl.col("proptax") + pl.col("otheritem") + pl.col("mortgage") + pl.col("state_sales_or_income_tax_ded")
    else:
        salt_plus_mortgage = pl.col("salt_capped") + pl.col("mortgage")

    if effective_year <= 2017:
        xitded = (salt_plus_mortgage - pl.col("state_sales_or_income_tax_ded")).clip(0, None)
        over_thr = (pl.col("ky_agi") > phas92) & (1991 <= effective_year <= 2017)
        reduce_ = pl.min_horizontal(
            p["itemized_phaseout_cap_rate"] * xitded, p["itemized_phaseout_rate"] * (pl.col("ky_agi") - phas92).clip(0, None)
        )
        if effective_year >= 1991:
            share = p.num("itemized_limitation_reduction_share")
            if effective_year in (2011, 2012) and not behavior.suspend_kentucky_itemized_limitation_through_2012:
                # TAXSIM's suspension test is true only in 2010.
                share = 1.0
            reduce_ = reduce_ * share
        xitded = pl.when(over_thr).then(xitded - reduce_).otherwise(xitded)

        if p.in_effect("child_care_deduction"):
            ccd = p.value("child_care_deduction")
            ytest = pl.col("ky_agi") + ky_fedtax
            cap1, cap2, cap3 = (float(c) for c in ccd["caps"])
            ceiling = float(ccd["income_ceiling"])
            phaseout_start = float(ccd["phaseout_start"])
            raw_ccc = pl.col("federal_chcr")
            child = (
                pl.when((pl.col("depx") > 0) & (pl.col("depx") < 2)).then(pl.min_horizontal(cap1, raw_ccc))
                .when((pl.col("depx") > 1) & (pl.col("depx") < 3)).then(pl.min_horizontal(cap2, raw_ccc))
                .when(pl.col("depx") > 2).then(pl.min_horizontal(cap3, raw_ccc))
                .otherwise(0.0)
            )
            child = pl.when(ytest >= phaseout_start).then((child - (ytest - phaseout_start) / 2.0).clip(0, None)).otherwise(child)
            child = pl.when(ytest <= ceiling).then(child).otherwise(0.0)
            xitded = xitded + child
    elif behavior.limit_kentucky_itemized_deductions_to_mortgage_interest:
        xitded = pl.col("mortgage")
    else:
        # TAXSIM also allows the other itemized deductions.
        xitded = pl.col("mortgage") + pl.col("otheritem")

    if effective_year == 1999:
        xitded = pl.when(forced_standard()).then(0.0).otherwise(xitded)

    df = df.with_columns(ky_xitded=xitded)
    df = df.with_columns(ky_deduc=pl.max_horizontal(pl.col("ky_stded"), pl.col("ky_xitded")))
    df = df.with_columns(ky_taxinc=(pl.col("ky_agi") - pl.col("ky_deduc")).clip(0, None))

    # --- Married filing combined: each spouse on their own share, with the
    # full standard deduction each (through 2017 and from 2022) ---
    mfc_years = effective_year <= 2017 or effective_year >= 2022 or not behavior.file_kentucky_combined_returns_per_statute
    is_mfc = is_joint & (pl.col("ky_agi") > 0) & mfc_years
    if behavior.file_kentucky_combined_returns_per_statute:
        agih = higher_earner_share(pl.col("ky_agi"))
        agiw = pl.col("ky_agi") - agih
        xitdh = pl.when(pl.col("ky_agi") != 0).then(pl.col("ky_xitded") * agih / pl.col("ky_agi")).otherwise(0.0)
        xitdw = pl.col("ky_xitded") - xitdh
        dedh = pl.max_horizontal(pl.col("ky_stded"), xitdh)
        dedw = pl.max_horizontal(pl.col("ky_stded"), xitdw)
        taxinh = pl.when(is_mfc).then((agih - dedh).clip(0, None)).otherwise(pl.lit(0.0))
        taxinw = pl.when(is_mfc).then((agiw - dedw).clip(0, None)).otherwise(pl.lit(0.0))
    else:
        # TAXSIM splits income by ownership, giving each spouse a standard
        # deduction or half the itemized deductions.
        # Pensions excluded per spouse come off after the split.
        shared_deduction = pl.max_horizontal(pl.lit(ded_pf), 0.5 * pl.col("ky_xitded"))
        pent, pens = allocate_pension(pl.col("pensions"), 0.0 if effective_year >= 2022 else 25.0)
        if effective_year >= 1995:
            exclude_h = pl.min_horizontal(pent, 31110.0)
            exclude_w = pl.min_horizontal(pens, 31110.0)
        else:
            exclude_h = exclude_w = pl.lit(0.0)
        split_base = pl.col("ky_agi") + (retirement_exclusion if effective_year >= 2022 else 0.0)
        agih, agiw = split_agi(split_base)
        taxinh = pl.when(is_mfc).then((agih - exclude_h * (effective_year >= 2022) - shared_deduction).clip(0, None)).otherwise(pl.lit(0.0))
        taxinw = pl.when(is_mfc).then((agiw - exclude_w * (effective_year >= 2022) - shared_deduction).clip(0, None)).otherwise(pl.lit(0.0))
    df = df.with_columns(ky_taxinh=taxinh, ky_taxinw=taxinw)

    # --- Tax ---
    if p.in_effect("brackets"):
        table = p.value("brackets")
        statax = bracket_tax(pl.col("ky_taxinc"), table)
        stath = bracket_tax(pl.col("ky_taxinh"), table)
        statw = bracket_tax(pl.col("ky_taxinw"), table)
        statax = pl.when(is_mfc).then(pl.min_horizontal(statax, stath + statw)).otherwise(statax)
        rate_expr = pl.when(is_mfc).then(bracket_rate(pl.col("ky_taxinw"), table)).otherwise(
            bracket_rate(pl.col("ky_taxinc"), table)
        )
    else:
        flat_rate = p.num("flat_rate")
        statax = flat_rate * pl.col("ky_taxinc")
        if effective_year >= 2022 or not behavior.file_kentucky_combined_returns_per_statute:
            statax = pl.when(is_mfc).then(flat_rate * pl.min_horizontal(pl.col("ky_taxinc"), pl.col("ky_taxinh") + pl.col("ky_taxinw"))).otherwise(statax)
        # TAXSIM's reported rate stays zero without a bracket lookup.
        rate_expr = pl.lit(0.0)
    df = df.with_columns(ky_statax=statax)

    # --- Credits ---
    df = df.with_columns(
        ky_gcred=pl.lit(0.0),
        ky_chcr=pl.lit(0.0),
        ky_lowcrd=pl.lit(0.0),
        ky_famcr=pl.lit(0.0),
        ky_gapcr=pl.lit(0.0),
    )
    if effective_year <= 1989:
        df = df.with_columns(ky_statax=pl.col("ky_statax").clip(0, None))
    elif effective_year >= 2022 and not behavior.step_kentucky_family_size_credit_by_poverty_share:
        # TAXSIM: the aged credit comes off before the unfloored tax is
        # multiplied by the interpolated family size credit share.
        rate_22 = p.num("flat_rate")
        aged_primary = (pl.col("page") >= 65).cast(pl.Float64)
        aged_spouse = (pl.col("sage") >= 65).cast(pl.Float64)
        statax_22 = rate_22 * pl.col("ky_taxinc") - 40.0 * aged_count()
        combined = (
            (rate_22 * pl.col("ky_taxinh") - 40.0 * aged_primary).clip(0, None)
            + (rate_22 * pl.col("ky_taxinw") - 40.0 * aged_spouse).clip(0, None)
        )
        statax_22 = pl.when(is_mfc & (combined < statax_22)).then(combined).otherwise(statax_22)
        num_22 = pl.min_horizontal(pl.max_horizontal(pl.col("depx") + pl.col("ky_taxpayers"), 1.0), 4.0)
        tables = p.value("taxsim_family_size_credit_table")
        share_22 = pl.lit(0.0)
        for n, name in ((4, "four"), (3, "three"), (2, "two"), (1, "one")):
            rows = tables[name]
            share_22 = pl.when(num_22 == n).then(
                pl.when(pl.col("ky_agi") > float(rows[9][0])).then(0.0).otherwise(interpolate_table(pl.col("agi"), rows))
            ).otherwise(share_22)
        chcr_22 = pl.col("ky_ccc") * p.num("child_care_credit_federal_share")
        famcr_22 = share_22 * statax_22
        df = df.with_columns(ky_chcr=chcr_22, ky_famcr=famcr_22)
        df = df.with_columns(ky_statax=(statax_22 - chcr_22 - famcr_22).clip(0, None))
    else:
        pc = p.value("personal_credit")
        gcred = float(pc["per_person"]) * (pl.col("ky_taxpayers") + pl.col("depx")) + float(pc["per_aged"]) * aged_count()
        df = df.with_columns(ky_gcred=gcred)
        df = df.with_columns(ky_statax=(pl.col("ky_statax") - pl.col("ky_gcred")).clip(0, None))

        raw_ccc_now = pl.col("ky_ccc")
        chcr_rate = p.num("child_care_credit_federal_share")
        chcr = raw_ccc_now * chcr_rate
        df = df.with_columns(ky_chcr=chcr)

        if p.in_effect("low_income_credit"):
            # The first bracket whose ceiling covers AGI applies.
            rate_lc = pl.lit(0.0)
            for ceiling, rate in reversed(p.value("low_income_credit")):
                rate_lc = pl.when(pl.col("ky_agi") <= ceiling).then(pl.lit(float(rate))).otherwise(rate_lc)
            lowcrd = pl.col("ky_statax") * rate_lc
            df = df.with_columns(ky_lowcrd=lowcrd)
            df = df.with_columns(
                ky_statax=(pl.col("ky_statax") - pl.col("ky_chcr") - pl.col("ky_lowcrd")).clip(0, None)
            )
        elif effective_year >= 2005:
            num = pl.min_horizontal(pl.max_horizontal(pl.col("ky_taxpayers") + pl.col("depx"), 1.0), 4.0)
            modagi = pl.max_horizontal(pl.col("ky_agi"), pl.col("agi") + pl.col("transfers"))
            tables = p["family_size_credit_table"]
            sizes = ("one", "two", "three", "four")
            if p.in_effect("family_size_credit_poverty_guideline"):
                # Income as a multiple of the federal poverty guideline
                # (family size capped at four), by step.
                fpg = p.value("family_size_credit_poverty_guideline")
                poverty = float(fpg["first_person"]) + float(fpg["additional_person"]) * (num - 1.0)
                share = modagi / poverty
                perc = pl.lit(0.0)
                for limit, rate in reversed(p["family_size_credit_poverty_share"]):
                    perc = pl.when(share <= float(limit)).then(float(rate)).otherwise(perc)
            else:
                index = [float(v) for v in p.value("family_size_credit_index")]
                perc = pl.lit(0.0)
                for n in (4, 3, 2, 1):
                    perc = pl.when(num == n).then(interpolate_table(modagi / index[n - 1], tables[sizes[n - 1]])).otherwise(perc)
            famcr = pl.col("ky_statax") * perc
            df = df.with_columns(ky_famcr=famcr)
            if p.in_effect("income_gap_credit"):
                gap_tables = p.value("income_gap_credit")
                gapcr = pl.lit(0.0)
                for n in (3, 2, 1):
                    gapcr = pl.when((num == n) & (pl.col("ky_famcr") > 0)).then(
                        interpolate_table(modagi / index[n - 1], gap_tables[sizes[n - 1]])
                    ).otherwise(gapcr)
                df = df.with_columns(ky_gapcr=gapcr)
            # `ky_gapcr` is reported but is deliberately not subtracted.
            df = df.with_columns(
                ky_statax=(pl.col("ky_statax") - pl.col("ky_famcr") - pl.col("ky_chcr")).clip(0, None)
            )

    df = df.with_columns(siitax=pl.col("ky_statax") * flate)
    return with_state_detail(
        df,
        agi=pl.col("ky_agi"),
        standard_deduction=pl.col("ky_stded"),
        itemized_deductions=pl.col("ky_xitded"),
        taxable_income=pl.col("ky_taxinc"),
        child_care_credit=pl.col("ky_chcr"),
        credits=pl.col("ky_gcred") + pl.col("ky_chcr") + pl.col("ky_lowcrd") + pl.col("ky_famcr") + pl.col("ky_gapcr"),
        rate=rate_expr,
    )
