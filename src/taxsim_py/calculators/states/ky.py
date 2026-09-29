"""Kentucky individual income tax calculator."""

import polars as pl

from taxsim_py.calculators.payroll import payroll_parts
from taxsim_py.calculators.federal_pre1987 import PRE1987_PARAMS
from taxsim_py.engine.brackets import bracket_rate, bracket_tax
from taxsim_py.engine.inputs import aged_count, files_joint, separate_divisor, taxpayer_count
from taxsim_py.engine.schema import PARAMETERS_ROOT, YearParams, load_yaml, resolve_year
from taxsim_py.engine.state import (
    dividend_input_adjustment,
    forced_standard,
    higher_earner_share,
    interpolate_table,
    unemployment_total,
    with_state_detail,
)
from taxsim_py.behavior import BehaviorProfile, TAXSIM_BEHAVIOR
from taxsim_py.engine.state_extrapolation import deflate_for_extrapolation, resolve_state_year

KY_PARAMS = load_yaml(PARAMETERS_ROOT / "states" / "ky" / "income_tax.yaml")

def compute_ky_tax(df: pl.DataFrame, year: int, behavior: BehaviorProfile = TAXSIM_BEHAVIOR) -> pl.DataFrame:
    effective_year, flate = resolve_state_year(year)
    p = YearParams(KY_PARAMS, effective_year)

    df = df.with_columns(
        ky_sep=separate_divisor(),
        ky_taxpayers=taxpayer_count(),
    )
    is_joint = files_joint()

    setax = payroll_parts(year)["setax"]  # `comnew(175)`, real-year and undeflated
    # The federal child care credit before the liability limit (`comnew(176)`)
    # is likewise read undeflated.
    df = df.with_columns(ky_setax=setax, ky_ccc=pl.col("ccc_uncapped"))

    df = deflate_for_extrapolation(df, flate)

    phas92_base = float(p["itemized_phaseout_base"][1960])
    phas92 = phas92_base / pl.col("ky_sep")
    if 1992 <= effective_year <= 2017:
        aif92 = p.num("itemized_phaseout_aif92_1992_2017")
        phas92 = phas92_base * aif92 / pl.col("ky_sep")

    # `fedtax=max(0,comnew(1)-comnew(175)-max(0,comnew(70)-comnew(28)))`:
    # federal income tax, which can be negative, less self-employment tax
    # and AMT, floored at 0 only at the end.
    ky_fedtax = (pl.col("fiitax") - pl.col("ky_setax") - pl.col("amt").clip(0, None)).clip(0, None)

    # `excli` - 1985's own interest-income exclusion.
    excli_cap = float(p["interest_exclusion_1985_cap_per_filer"][1960])
    excli = p["interest_exclusion_1985_rate"] * pl.col("intrec").clip(0, excli_cap * pl.col("ky_taxpayers"))

    # --- AGI ---
    if effective_year <= 1989:
        agi = pl.col("agi") - ky_fedtax - (excli if effective_year == 1985 else 0.0)
    else:
        # 1990 adds back federal income tax (`comnew(1)`) even when negative.
        subtra = 0.0 if effective_year != 1990 else pl.col("fiitax")
        agi = pl.col("agi") - subtra

    ui_total = unemployment_total()
    if effective_year in (2009, 2020):
        agi = agi + ui_total - pl.col("taxable_unemployment")
    if effective_year >= 1984:
        agi = agi - pl.col("taxable_social_security")
    # The 1987-1989 dividend exclusion (`divexc`) is this same
    # `min(dividends,100*txp)` subtraction, taken once.
    if effective_year <= 1997:
        agi = agi - (pl.col("dividends") + dividend_input_adjustment()).clip(0, 100.0 * pl.col("ky_taxpayers"))
    if 1982 <= effective_year <= 1986:
        rate_2e = float(resolve_year(PRE1987_PARAMS["two_earner_deduction_rate"], effective_year))
        cap_2e = float(resolve_year(PRE1987_PARAMS["two_earner_deduction_cap"], effective_year))
        lesser_wage = pl.min_horizontal(pl.col("pwages"), pl.col("swages")).clip(0, None)
        twoded = pl.when(is_joint).then((rate_2e * lesser_wage).clip(0, cap_2e)).otherwise(0.0)
        agi = agi + twoded
    if effective_year >= 1995:
        share = p.num("retirement_exclusion_share")
        cap = p.num("retirement_exclusion_cap")
        agi = agi - pl.min_horizontal(share * pl.col("pensions"), pl.lit(cap))
    if 1987 <= effective_year <= 1989:
        # 60% LTCG exclusion, gated on a positive net capital gain in AGI.
        capgn = pl.col("stcg") + pl.col("ltcg")
        ltcg_rate = float(p["ltcg_exclusion_rate_1987_1989"][1960])
        agi = pl.when(capgn > 0).then(agi - ltcg_rate * pl.col("ltcg").clip(0, None)).otherwise(agi)

    df = df.with_columns(ky_agi=agi)

    # --- Standard deduction ---
    ded_pf = p.num("standard_deduction_per_filer")
    df = df.with_columns(ky_stded=ded_pf * pl.col("ky_taxpayers"))

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
        if 2006 <= effective_year <= 2007:
            reduce_ = 2.0 * reduce_ / 3.0
        elif 2008 <= effective_year <= 2009:
            reduce_ = reduce_ / 3.0
        elif effective_year == 2010:
            # `law.eq.2010.and.law.le.2012` is true only for 2010.
            reduce_ = pl.lit(0.0)
        xitded = pl.when(over_thr).then(xitded - reduce_).otherwise(xitded)

        if effective_year <= 1989:
            ytest = pl.col("ky_agi") + ky_fedtax
            cap1 = float(p["low_income_child_deduction_cap_1dep"][1960])
            cap2 = float(p["low_income_child_deduction_cap_2deps"][1960])
            cap3 = float(p["low_income_child_deduction_cap_3plus_deps"][1960])
            ceiling = float(p["low_income_child_deduction_income_ceiling"][1960])
            phaseout_start = float(p["low_income_child_deduction_phaseout_start"][1960])
            raw_ccc = pl.col("federal_chcr")  # `comnew(53)`
            child = (
                pl.when((pl.col("depx") > 0) & (pl.col("depx") < 2)).then(pl.min_horizontal(cap1, raw_ccc))
                .when((pl.col("depx") > 1) & (pl.col("depx") < 3)).then(pl.min_horizontal(cap2, raw_ccc))
                .when(pl.col("depx") > 2).then(pl.min_horizontal(cap3, raw_ccc))
                .otherwise(0.0)
            )
            child = pl.when(ytest >= phaseout_start).then((child - (ytest - phaseout_start) / 2.0).clip(0, None)).otherwise(child)
            child = pl.when(ytest <= ceiling).then(child).otherwise(0.0)
            xitded = xitded + child
    else:
        xitded = pl.col("mortgage")

    if effective_year == 1999:
        xitded = pl.when(forced_standard()).then(0.0).otherwise(xitded)

    df = df.with_columns(ky_xitded=xitded)
    df = df.with_columns(ky_deduc=pl.max_horizontal(pl.col("ky_stded"), pl.col("ky_xitded")))
    df = df.with_columns(ky_taxinc=(pl.col("ky_agi") - pl.col("ky_deduc")).clip(0, None))

    # --- Married filing combined --- (own explicit split, real through 2017)
    is_mfc = is_joint & (pl.col("ky_agi") > 0) & (effective_year <= 2017)
    agih = higher_earner_share(pl.col("ky_agi"))
    agiw = pl.col("ky_agi") - agih
    xitdh = pl.when(pl.col("ky_agi") != 0).then(pl.col("ky_xitded") * agih / pl.col("ky_agi")).otherwise(0.0)
    xitdw = pl.col("ky_xitded") - xitdh
    dedh = pl.max_horizontal(pl.col("ky_stded"), xitdh)
    dedw = pl.max_horizontal(pl.col("ky_stded"), xitdw)
    taxinh = pl.when(is_mfc).then((agih - dedh).clip(0, None)).otherwise(pl.lit(0.0))
    taxinw = pl.when(is_mfc).then((agiw - dedw).clip(0, None)).otherwise(pl.lit(0.0))
    df = df.with_columns(ky_taxinh=taxinh, ky_taxinw=taxinw)

    # --- Bracket tax ---
    if effective_year <= 2004:
        table = p["brackets_pre2005"]
        statax = bracket_tax(pl.col("ky_taxinc"), table)
        stath = bracket_tax(pl.col("ky_taxinh"), table)
        statw = bracket_tax(pl.col("ky_taxinw"), table)
        statax = pl.when(is_mfc).then(pl.min_horizontal(statax, stath + statw)).otherwise(statax)
        rate_expr = pl.when(is_mfc).then(bracket_rate(pl.col("ky_taxinw"), table)).otherwise(
            bracket_rate(pl.col("ky_taxinc"), table)
        )
    elif effective_year <= 2017:
        table = p["brackets_2005_2017"]
        statax = bracket_tax(pl.col("ky_taxinc"), table)
        stath = bracket_tax(pl.col("ky_taxinh"), table)
        statw = bracket_tax(pl.col("ky_taxinw"), table)
        statax = pl.when(is_mfc).then(pl.min_horizontal(statax, stath + statw)).otherwise(statax)
        rate_expr = pl.when(is_mfc).then(bracket_rate(pl.col("ky_taxinw"), table)).otherwise(
            bracket_rate(pl.col("ky_taxinc"), table)
        )
    else:
        flat_rate = float(p["flat_rate_2018plus"][1960])
        statax = flat_rate * pl.col("ky_taxinc")
        # The flat formula does not call TAXSIM's bracket lookup, so its
        # shared reported-rate variable remains zero.
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
    else:
        if effective_year < 2004:
            amt = float(p["personal_credit_amount_pre2004"][1960])
            count = pl.col("ky_taxpayers") + pl.col("depx") + 2.0 * aged_count()
            gcred = amt * count
        elif 2014 <= effective_year <= 2017:
            amt = float(p["personal_credit_amount_2014_2017"][1960])
            gcred = amt * (pl.col("ky_taxpayers") + pl.col("depx")) + p["aged_personal_credit_2014"] * aged_count()
        elif effective_year >= 2018:
            gcred = p["aged_personal_credit_2014"] * aged_count()
        else:
            gcred = pl.lit(0.0)
        df = df.with_columns(ky_gcred=gcred)
        df = df.with_columns(ky_statax=(pl.col("ky_statax") - pl.col("ky_gcred")).clip(0, None))

        raw_ccc_now = pl.col("ky_ccc")
        chcr_rate = float(p["low_income_care_credit_rate"][1960])
        chcr = raw_ccc_now * chcr_rate
        df = df.with_columns(ky_chcr=chcr)

        if 1990 <= effective_year <= 2004:
            # The first bracket whose ceiling covers AGI applies.
            rate_lc = pl.lit(0.0)
            for ceiling, rate in reversed(p["low_income_credit_brackets"]):
                rate_lc = pl.when(pl.col("ky_agi") <= ceiling).then(pl.lit(float(rate))).otherwise(rate_lc)
            lowcrd = pl.col("ky_statax") * rate_lc
            df = df.with_columns(ky_lowcrd=lowcrd)
            df = df.with_columns(
                ky_statax=(pl.col("ky_statax") - pl.col("ky_chcr") - pl.col("ky_lowcrd")).clip(0, None)
            )
        elif effective_year >= 2005:
            num = pl.min_horizontal(pl.col("ky_taxpayers") + pl.col("depx") + aged_count(), 4.0)
            modagi = pl.max_horizontal(pl.col("ky_agi"), pl.col("agi"))
            aif1 = p.num("family_size_credit_aif_1person")
            aif2 = p.num("family_size_credit_aif_2person")
            aif3 = p.num("family_size_credit_aif_3person")
            aif4 = p.num("family_size_credit_aif_4person")
            perc = (
                pl.when(num == 1).then(interpolate_table(modagi / aif1, p["family_size_credit_table_1person"]))
                .when(num == 2).then(interpolate_table(modagi / aif2, p["family_size_credit_table_2person"]))
                .when(num == 3).then(interpolate_table(modagi / aif3, p["family_size_credit_table_3person"]))
                .when(num == 4).then(interpolate_table(modagi / aif4, p["family_size_credit_table_4person"]))
                .otherwise(0.0)
            )
            famcr = pl.col("ky_statax") * perc
            df = df.with_columns(ky_famcr=famcr)
            if effective_year in (2019, 2020):
                gapcr = (
                    pl.when((num == 1) & (pl.col("ky_famcr") > 0)).then(
                        interpolate_table(modagi / aif1, p["income_gap_credit_table_1person"])
                    )
                    .when((num == 2) & (pl.col("ky_famcr") > 0)).then(
                        interpolate_table(modagi / aif2, p["income_gap_credit_table_2person"])
                    )
                    .when((num == 3) & (pl.col("ky_famcr") > 0)).then(
                        interpolate_table(modagi / aif3, p["income_gap_credit_table_3person"])
                    )
                    .otherwise(0.0)
                )
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
