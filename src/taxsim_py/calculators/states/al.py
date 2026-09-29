"""Alabama individual income tax calculator."""

import polars as pl

from taxsim_py.calculators.payroll import payroll_parts
from taxsim_py.engine.brackets import bracket_rate, bracket_tax
from taxsim_py.engine.inputs import files_head_of_household, files_joint, files_single, separate_divisor, taxpayer_count
from taxsim_py.engine.schema import PARAMETERS_ROOT, YearParams, load_yaml, resolve_year
from taxsim_py.engine.state import dividend_exclusion_addback, federal_capital_gain_in_agi, with_state_detail
from taxsim_py.behavior import BehaviorProfile, TAXSIM_BEHAVIOR
from taxsim_py.engine.state_extrapolation import deflate_for_extrapolation, resolve_state_year

AL_PARAMS = load_yaml(PARAMETERS_ROOT / "states" / "al" / "income_tax.yaml")
PRE1987_PARAMS = load_yaml(PARAMETERS_ROOT / "national" / "pre1987.yaml")


def compute_al_tax(df: pl.DataFrame, year: int, behavior: BehaviorProfile = TAXSIM_BEHAVIOR) -> pl.DataFrame:
    effective_year, flate = resolve_state_year(year)
    p = YearParams(AL_PARAMS, effective_year)

    df = df.with_columns(
        al_sep=separate_divisor(),
        # Taxpayers (`data(7)`): 2 only on joint returns. Caps the standard
        # deduction; the exemption below gives head of household the joint amount.
        al_taxpayers=taxpayer_count(),
        # Self-employment tax (`comnew(175)`) is never deflated.
        al_setax=payroll_parts(year)["setax"],
        # Unemployment compensation in federal AGI is exempt.
        al_taxable_ui=pl.col("taxable_unemployment"),
    )
    # Tax before credits, NIIT and additional Medicare tax (`comnew(154)`,
    # `comnew(173)`, `comnew(180)`) stay undeflated in projected years.
    df = deflate_for_extrapolation(df, flate, extra=("al_taxable_ui",))

    # --- AGI ---
    # The federal deduction for half of self-employment tax is added back
    # in every year (taxsim_2022_10_21.f:581-589).
    if effective_year in (2011, 2012):
        holiday = p["se_deduction_holiday"]
        df = df.with_columns(
            al_setax_addback=pl.when(pl.col("al_setax") <= holiday["threshold"])
            .then(holiday["rate"] * pl.col("al_setax"))
            .otherwise(0.5 * pl.col("al_setax") + holiday["flat"])
        )
    else:
        df = df.with_columns(al_setax_addback=0.5 * pl.col("al_setax"))
    # The federal dividend exclusion (through 1986) is added back.
    if effective_year <= 1986:
        df = df.with_columns(al_dividend_addback=dividend_exclusion_addback(effective_year))
    else:
        df = df.with_columns(al_dividend_addback=pl.lit(0.0))

    # "The Capital Gains are treated similar to Federal Taxes, except that
    # all gains are taxable and all losses are deductible in the year
    # in[curred]" (taxsim.f:596-598: `if(comnew(6).lt.0) agi = agi +
    # comnew(5) - comnew(6)`). Federal AGI already includes the
    # $3,000-capped loss (`comnew(6)`); when it's negative, Alabama adds
    # back the difference to the raw, uncapped loss (`comnew(5)`) instead,
    # giving the full loss rather than the federal limit. The port
    # previously never implemented this branch at all, on the mistaken
    # assumption a net loss couldn't occur - confirmed against the real
    # oracle, not just the source: taxsim2024.exe itself already computes
    # the full-loss figure (a single filer with a $42,000 wage, $8,000
    # long-term loss return was off by $250 every year before this fix;
    # not a documented TAXSIM bug to preserve for compatibility, so this
    # applies unconditionally rather than being calculation_mode-gated).
    #
    # `pre1987_pref` looks like the right column at a glance (its own
    # formula is `fullcg - capgn` when `fullcg<0`, exactly this adjustment)
    # but it is a *different* TAXSIM output slot (`comnew(74)`, what other
    # states' own minimum-tax calculators read as "preference income")
    # that is hardcoded to 0 for 1979-1982 for reasons specific to that
    # slot, not because this adjustment is actually 0 those years -
    # recomputed directly here instead from `pre1987_capgn` (`comnew(6)`)
    # and the raw, unexcluded, uncapped input sum (`comnew(5)`).
    capped_capital_gain = pl.col("pre1987_capgn") if effective_year <= 1986 else federal_capital_gain_in_agi(year, flate)
    raw_capital_gain = pl.col("stcg") + pl.col("ltcg")
    capital_loss_excess = pl.when(capped_capital_gain < 0).then(raw_capital_gain - capped_capital_gain).otherwise(0.0)
    df = df.with_columns(al_capital_loss_excess=capital_loss_excess)

    # The federal two-earner deduction (1982-1986) is added back.
    if 1982 <= effective_year <= 1986:
        two_earner_rate = float(resolve_year(PRE1987_PARAMS["two_earner_deduction_rate"], effective_year))
        two_earner_cap = float(resolve_year(PRE1987_PARAMS["two_earner_deduction_cap"], effective_year))
        df = df.with_columns(
            al_twoded_addback=pl.when(files_joint())
            .then((two_earner_rate * pl.min_horizontal(pl.col("pwages"), pl.col("swages")).clip(0, None)).clip(0, two_earner_cap))
            .otherwise(0.0)
        )
    else:
        df = df.with_columns(al_twoded_addback=pl.lit(0.0))

    df = df.with_columns(
        al_agi=pl.col("agi")
        + pl.col("al_dividend_addback")
        + pl.col("al_twoded_addback")
        + pl.col("al_setax_addback")
        + pl.col("al_capital_loss_excess")
        - pl.col("al_taxable_ui")
        # Social Security benefits are exempt from 1984.
        - (pl.col("taxable_social_security") if effective_year >= 1984 else 0.0)
    )

    # --- Standard vs. itemized deduction ---
    if effective_year <= 2006:
        pct = p.num("standard_deduction_pct")
        cap_per_exemption = p.num("standard_deduction_cap_per_exemption")
        df = df.with_columns(
            al_stded=(pct * pl.col("al_agi")).clip(0, cap_per_exemption * pl.col("al_taxpayers"))
        )
    else:
        income_floor = p.num("standard_deduction_2007_income_floor")
        income_ceiling = p.num("standard_deduction_2007_income_ceiling")
        stmin_per_exemption = p.num("standard_deduction_2007_min_per_exemption")
        max_single = p.num("standard_deduction_2007_max_single")
        max_hoh = p.num("standard_deduction_2007_max_hoh")
        max_joint_or_sep = p.num("standard_deduction_2007_max_joint_or_separate")
        df = df.with_columns(
            al_agimin=income_floor / pl.col("al_sep"),
            al_agimax=income_ceiling / pl.col("al_sep"),
            al_stmin=stmin_per_exemption * pl.col("al_taxpayers"),
            al_stmax=pl.when(files_single())
                .then(max_single)
                .when(files_head_of_household())
                .then(max_hoh)
                .otherwise(max_joint_or_sep / pl.col("al_sep")),
        )
        df = df.with_columns(
            al_excess=(pl.col("al_agi") - pl.col("al_agimin")).clip(0, None),
            al_tga=(pl.col("al_stmax") - pl.col("al_stmin")) / (pl.col("al_agimax") - pl.col("al_agimin")),
        )
        df = df.with_columns(
            al_stded=pl.col("al_stmax")
            - pl.min_horizontal(pl.col("al_excess"), pl.col("al_agimax") - pl.col("al_agimin")) * pl.col("al_tga")
        )

    # Itemized deductions: mortgage interest, plus from 1982 property and
    # other taxes, half of wage FICA and self-employment tax.
    if effective_year < 1982:
        df = df.with_columns(al_xitded_base=pl.col("mortgage"))
    else:
        df = df.with_columns(al_xitded_base=pl.col("mortgage") + pl.col("proptax") + pl.col("otheritem"))

    if effective_year >= 1982:
        df = df.with_columns(al_fica_addback=0.5 * (pl.col("fica") - pl.col("al_setax")) + pl.col("addmed"))
        df = df.with_columns(al_xitded=(pl.col("al_xitded_base") + pl.col("al_fica_addback") + pl.col("al_setax")).clip(0, None))
    else:
        df = df.with_columns(al_xitded=pl.col("al_xitded_base"))

    df = df.with_columns(al_deduc_before_fedtax=pl.max_horizontal(pl.col("al_stded"), pl.col("al_xitded")))

    # --- Federal income tax deduction (`fedtax`), added to the larger of
    # the standard and itemized deductions. ---
    if effective_year <= 1999:
        df = df.with_columns(al_fedtax=pl.col("fiitax").clip(0, None))
    elif effective_year <= 2008:
        # Tax after nonrefundable credits (`comnew(154)`, `comnew(52)-comnew(58)`).
        df = df.with_columns(al_fedtax=(pl.col("tax_before_credits") - pl.col("nonrefundable_credits")).clip(0, None))
    else:
        # `max(0, comnew(154)+comnew(173)-comnew(59)-comnew(93))`
        # (taxsim_2024_09_21.f:671): tax after nonrefundable credits, plus
        # NIIT, less the EITC and refundable child credit.
        df = df.with_columns(
            al_fedtax=(
                pl.col("tax_before_credits") - pl.col("nonrefundable_credits") - pl.col("eitc") - pl.col("actc") + pl.col("niit")
            ).clip(0, None)
        )

    df = df.with_columns(al_deduc=pl.col("al_deduc_before_fedtax") + pl.col("al_fedtax"))

    # --- Exemptions ---
    df = df.with_columns(
        al_exemp_base=pl.when(pl.col("filing_status").is_in(["married_joint", "head_of_household"]))
        .then(float(p["personal_exemption"]["joint_or_hoh"]))
        .otherwise(float(p["personal_exemption"]["other"]))
    )
    if effective_year <= 2006:
        dep_flat = p.num("dependent_exemption_flat")
        df = df.with_columns(al_dep_exemp=dep_flat * pl.col("depx"))
    else:
        high = p.num("dependent_exemption_2007_high")
        mid = p.num("dependent_exemption_2007_mid")
        low = p.num("dependent_exemption_2007_low")
        bp1 = p.num("dependent_exemption_2007_breakpoint1")
        bp2 = p.num("dependent_exemption_2007_breakpoint2")
        tga = (high - mid) / bp1
        tgb = (mid - low) / (bp2 - bp1)
        excesa = pl.col("al_agi").clip(0, bp1)
        excesb = (pl.col("al_agi") - bp1).clip(0, bp2 - bp1)
        df = df.with_columns(al_dep_exemp=(high - excesa * tga - excesb * tgb) * pl.col("depx"))
    df = df.with_columns(al_exemp=pl.col("al_exemp_base") + pl.col("al_dep_exemp"))

    df = df.with_columns(al_taxinc=(pl.col("al_agi") - pl.col("al_deduc") - pl.col("al_exemp")).clip(0, None))

    # --- Bracket tax: joint returns split income (tax on half, doubled);
    # other returns use the table directly. ---
    if effective_year <= 1981:
        brackets = p["brackets_pre1982"]
        df = df.with_columns(al_regtax=bracket_tax(pl.col("al_taxinc"), brackets))
        rate = bracket_rate(pl.col("al_taxinc"), brackets)
    else:
        brackets = p["brackets"]
        df = df.with_columns(
            al_taxy=pl.when(files_joint()).then(pl.col("al_taxinc") / 2).otherwise(pl.col("al_taxinc"))
        )
        df = df.with_columns(al_stat=bracket_tax(pl.col("al_taxy"), brackets))
        df = df.with_columns(
            al_regtax=pl.when(files_joint()).then(pl.col("al_stat") * 2).otherwise(pl.col("al_stat"))
        )
        rate = bracket_rate(pl.col("al_taxy"), brackets)

    df = df.with_columns(siitax=pl.col("al_regtax").clip(0, None) * flate)
    return with_state_detail(
        df,
        agi=pl.col("al_agi"),
        exemptions=pl.col("al_exemp"),
        standard_deduction=pl.col("al_stded"),
        itemized_deductions=pl.col("al_xitded"),
        taxable_income=pl.col("al_taxinc"),
        rate=rate,
    )
