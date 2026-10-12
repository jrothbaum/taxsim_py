"""Alabama individual income tax calculator."""

import polars as pl

from taxsim_py.engine.brackets import bracket_rate, bracket_tax
from taxsim_py.engine.inputs import aged_count, files_head_of_household, files_joint, files_single, separate_divisor, taxpayer_count
from taxsim_py.engine.schema import PARAMETERS_ROOT, YearParams, load_yaml
from taxsim_py.engine.state import by_filing_status, dividend_exclusion_addback, federal_capital_gain_in_agi, with_state_detail
from taxsim_py.behavior import BehaviorProfile, TAXSIM_BEHAVIOR
from taxsim_py.engine.state_extrapolation import deflate_for_extrapolation, resolve_state_year

AL_PARAMS = load_yaml(PARAMETERS_ROOT / "states" / "al" / "income_tax.yaml")
PRE1987_PARAMS = load_yaml(PARAMETERS_ROOT / "national" / "pre1987.yaml")


def compute_al_tax(df: pl.DataFrame, year: int, behavior: BehaviorProfile = TAXSIM_BEHAVIOR) -> pl.DataFrame:
    effective_year, flate = resolve_state_year(year, behavior, AL_PARAMS)
    p = YearParams(AL_PARAMS, effective_year)

    df = df.with_columns(
        al_sep=separate_divisor(),
        al_taxpayers=taxpayer_count(),
        # Self-employment tax is not deflated in projected years.
        al_setax=pl.col("payroll_setax"),
        # Unemployment compensation in federal AGI is exempt.
        al_taxable_ui=pl.col("taxable_unemployment"),
    )
    # Tax before credits, NIIT and additional Medicare tax stay undeflated in
    # projected years.
    df = deflate_for_extrapolation(df, flate, extra=("al_taxable_ui",))

    # --- AGI ---
    # The federal deduction for half of self-employment tax is added back.
    if p.in_effect("se_deduction_holiday"):
        holiday = p.value("se_deduction_holiday")
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

    # All capital gains are taxable and all losses deductible: when federal
    # AGI holds a capped net loss, the uncapped loss replaces it.
    capped_capital_gain = pl.col("pre1987_capgn") if effective_year <= 1986 else federal_capital_gain_in_agi(year, flate)
    raw_capital_gain = pl.col("stcg") + pl.col("ltcg")
    capital_loss_excess = pl.when(capped_capital_gain < 0).then(raw_capital_gain - capped_capital_gain).otherwise(0.0)
    df = df.with_columns(al_capital_loss_excess=capital_loss_excess)

    # The federal two-earner deduction (1982-1986) is added back.
    if 1982 <= effective_year <= 1986:
        p87 = YearParams(PRE1987_PARAMS, effective_year)
        two_earner_rate = p87.num("two_earner_deduction_rate")
        two_earner_cap = p87.num("two_earner_deduction_cap")
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
    if p.in_effect("retirement_exemption_cap"):
        # Retirement income exemption: up to the cap for each taxpayer 65 or older.
        cap = p.num("retirement_exemption_cap")
        df = df.with_columns(al_agi=pl.col("al_agi") - pl.min_horizontal(pl.col("pensions"), cap * aged_count()))

    # --- Standard vs. itemized deduction ---
    if p.in_effect("standard_deduction_percent"):
        sd = p.value("standard_deduction_percent")
        df = df.with_columns(
            al_stded=(float(sd["rate"]) * pl.col("al_agi")).clip(0, float(sd["cap_per_taxpayer"]) * pl.col("al_taxpayers"))
        )
    elif p.in_effect("standard_deduction_steps"):
        steps = p.value("standard_deduction_steps")
        def field(name: str) -> pl.Expr:
            return by_filing_status({status: terms[name] for status, terms in steps.items()})
        max_deduction, min_deduction = field("maximum"), field("minimum")
        df = df.with_columns(
            al_stded=(max_deduction - field("reduction") * ((pl.col("al_agi") - field("threshold")).clip(0, None) / field("increment")).floor()).clip(min_deduction, max_deduction)
        )
    else:
        sd = p.value("standard_deduction_phasedown")
        df = df.with_columns(
            al_agimin=float(sd["income_floor"]) / pl.col("al_sep"),
            al_agimax=float(sd["income_ceiling"]) / pl.col("al_sep"),
            al_stmin=float(sd["min_per_taxpayer"]) * pl.col("al_taxpayers"),
            al_stmax=pl.when(files_single())
                .then(float(sd["max"]["single"]))
                .when(files_head_of_household())
                .then(float(sd["max"]["head_of_household"]))
                .otherwise(float(sd["max"]["married"]) / pl.col("al_sep")),
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
        if behavior.count_additional_medicare_once_in_alabama_fica_deduction:
            # Additional Medicare tax is employee-only and already in `fica` once.
            df = df.with_columns(
                al_fica_addback=0.5 * (pl.col("fica") - pl.col("al_setax") - pl.col("addmed")) + pl.col("addmed")
            )
        else:
            df = df.with_columns(al_fica_addback=0.5 * (pl.col("fica") - pl.col("al_setax")) + pl.col("addmed"))
        df = df.with_columns(al_xitded=(pl.col("al_xitded_base") + pl.col("al_fica_addback") + pl.col("al_setax")).clip(0, None))
    else:
        df = df.with_columns(al_xitded=pl.col("al_xitded_base"))

    df = df.with_columns(al_deduc_before_fedtax=pl.max_horizontal(pl.col("al_stded"), pl.col("al_xitded")))

    # --- Federal income tax deduction, added to the larger of the standard
    # and itemized deductions. ---
    if effective_year <= 1999:
        df = df.with_columns(al_fedtax=pl.col("fiitax").clip(0, None))
    elif effective_year <= 2008:
        # Tax after nonrefundable credits.
        df = df.with_columns(al_fedtax=(pl.col("tax_before_credits") - pl.col("nonrefundable_credits")).clip(0, None))
    else:
        # Tax after nonrefundable credits, plus NIIT, less the EITC and
        # refundable child credit.
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
    if p.in_effect("dependent_exemption_flat"):
        df = df.with_columns(al_dep_exemp=p.num("dependent_exemption_flat") * pl.col("depx"))
    else:
        dx = p.value("dependent_exemption_by_agi")
        high, mid, low = float(dx["high"]), float(dx["mid"]), float(dx["low"])
        bp1, bp2 = (float(b) for b in dx["breakpoints"])
        if effective_year >= 2022:
            step = pl.when(pl.col("al_agi") >= bp2).then(low).when(pl.col("al_agi") >= bp1).then(mid).otherwise(high)
            df = df.with_columns(al_dep_exemp=step * pl.col("depx"))
        else:
            tga = (high - mid) / bp1
            tgb = (mid - low) / (bp2 - bp1)
            excesa = pl.col("al_agi").clip(0, bp1)
            excesb = (pl.col("al_agi") - bp1).clip(0, bp2 - bp1)
            df = df.with_columns(al_dep_exemp=(high - excesa * tga - excesb * tgb) * pl.col("depx"))
    df = df.with_columns(al_exemp=pl.col("al_exemp_base") + pl.col("al_dep_exemp"))

    df = df.with_columns(al_taxinc=(pl.col("al_agi") - pl.col("al_deduc") - pl.col("al_exemp")).clip(0, None))

    # --- Bracket tax: joint returns split income (tax on half, doubled);
    # other returns use the table directly. ---
    brackets = p.value("brackets")
    if effective_year <= 1981:
        df = df.with_columns(al_regtax=bracket_tax(pl.col("al_taxinc"), brackets))
        rate = bracket_rate(pl.col("al_taxinc"), brackets)
    else:
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
