"""Arizona individual income tax calculator."""


from __future__ import annotations
import polars as pl

from taxsim_py.engine.brackets import bracket_rate, bracket_tax
from taxsim_py.engine.eitc import trapezoid_credit
from taxsim_py.engine.inputs import aged_count, files_head_of_household, files_joint, files_separate, is_dependent_filer, separate_divisor, taxpayer_count
from taxsim_py.engine.schema import PARAMETERS_ROOT, YearParams, load_yaml, resolve_year
from taxsim_py.engine.state import by_filing_status, dividend_exclusion_addback, household_income, interpolate_table, with_state_detail
from taxsim_py.behavior import BehaviorProfile, TAXSIM_BEHAVIOR
from taxsim_py.engine.state_extrapolation import deflate_for_extrapolation, resolve_state_year

AZ_PARAMS = load_yaml(PARAMETERS_ROOT / "states" / "az" / "income_tax.yaml")
PRE1987_PARAMS = load_yaml(PARAMETERS_ROOT / "national" / "pre1987.yaml")
STATE_ADJUSTMENT_PARAMS = load_yaml(PARAMETERS_ROOT / "national" / "state_adjustments.yaml")

# Joint and head-of-household returns use the "joint_or_hoh" schedules and
# limits; separate returns are treated as single.
_RICH_STATUSES = ["married_joint", "head_of_household"]

def compute_az_tax(df: pl.DataFrame, year: int, behavior: BehaviorProfile = TAXSIM_BEHAVIOR) -> pl.DataFrame:
    effective_year, flate = resolve_state_year(year, behavior, AZ_PARAMS)
    p = YearParams(AZ_PARAMS, effective_year)
    p87 = YearParams(PRE1987_PARAMS, effective_year)
    df = df.with_columns(
        az_household_income=household_income()
    )

    df = deflate_for_extrapolation(df, flate, extra=("az_household_income",))

    df = df.with_columns(
        az_sep=separate_divisor(),
        az_rich=pl.col("filing_status").is_in(_RICH_STATUSES),
    )
    df = df.with_columns(az_txp_raw=taxpayer_count())
    # Heads of household count as two taxpayers for exemptions, the
    # standard deduction and the family income credit.
    df = df.with_columns(
        az_txp=pl.col("az_txp_raw")
        + pl.when(files_head_of_household()).then(1.0).otherwise(0.0)
    )
    # Dependents count for exemptions only while the dependent exemption exists.
    df = df.with_columns(az_nchild=pl.col("depx").cast(pl.Float64) if p.in_effect("dependent_exemption") else pl.lit(0.0))

    # --- AGI ---
    if effective_year <= 1980:
        div_addback = dividend_exclusion_addback(effective_year)
    else:
        div_addback = pl.lit(0.0)
    df = df.with_columns(az_div_addback=div_addback)

    if 1982 <= effective_year <= 1986:
        two_earner_rate = p87.num("two_earner_deduction_rate")
        two_earner_cap = p87.num("two_earner_deduction_cap")
        df = df.with_columns(
            az_twoded_addback=pl.when(files_joint())
            .then((two_earner_rate * pl.min_horizontal(pl.col("pwages"), pl.col("swages")).clip(0, None)).clip(0, two_earner_cap))
            .otherwise(0.0)
        )
    else:
        df = df.with_columns(az_twoded_addback=pl.lit(0.0))

    # Social Security benefits are exempt from 1984.
    exempt_ss = pl.col("taxable_social_security") if effective_year >= 1984 else pl.lit(0.0)
    df = df.with_columns(
        az_agi_1=pl.col("agi") + pl.col("az_div_addback") + pl.col("az_twoded_addback") - exempt_ss
        # 2025 on: Arizona subtracts the federal senior deduction from its income.
        - (pl.col("senior_deduction") if effective_year >= 2025 else 0.0)
    )
    # Through 1989 federal income tax, before the EITC and nonrefundable
    # credits, is subtracted from AGI.
    if effective_year <= 1986:
        rate_in = p87.num("eitc_rate_in")
        max_credit = p87.num("eitc_max_credit")
        phaseout_start = p87.num("eitc_phaseout_start")
        rate_out = p87.num("eitc_rate_out")
        az_earned = (pl.col("wages") + pl.col("psemp").clip(0, None) + pl.col("ssemp").clip(0, None)).clip(0, None)
        az_earncr_raw = trapezoid_credit(az_earned, pl.col("agi"), rate_in, max_credit, phaseout_start, rate_out)
        df = df.with_columns(
            az_earncr=pl.when((files_separate()) | (pl.col("dep18") == 0))
            .then(0.0)
            .otherwise(az_earncr_raw)
        )
    elif effective_year <= 1989:
        df = df.with_columns(az_earncr=pl.col("eitc"))
    else:
        df = df.with_columns(az_earncr=pl.lit(0.0))

    if effective_year <= 1989:
        credit = pl.col("credit") if effective_year <= 1986 else pl.col("nonrefundable_credits")
        df = df.with_columns(az_fedtax=(pl.col("fiitax") + pl.col("az_earncr") + credit).clip(0, None))
        df = df.with_columns(az_agi=(pl.col("az_agi_1") - pl.col("az_fedtax")))
    else:
        df = df.with_columns(az_agi=pl.col("az_agi_1"))
    df = df.with_columns(az_ag=pl.col("az_agi").clip(0, None))

    # --- Exemptions ---
    if effective_year <= 1989:
        pe = p.num("personal_exemption")
        de = p.num("dependent_exemption")
        aif = p.num("indexing_factor")
        exemp = (pl.col("az_txp") * pe + pl.col("az_nchild") * de) * aif
        if p.in_effect("head_of_household_exemption_backout"):
            backout = p.num("head_of_household_exemption_backout")
            exemp = exemp - pl.when(files_head_of_household()).then(pl.col("az_nchild").clip(0, 1) * backout * aif).otherwise(0.0)
        df = df.with_columns(az_exemp=exemp)
        aged_amount = pe * aif
    elif p.in_effect("personal_exemption"):
        pe = p.num("personal_exemption")
        if p.in_effect("personal_exemption_married_with_dependents"):
            use_married = pl.col("filing_status").is_in(["married_joint", "married_separate"]) & (pl.col("az_nchild") > 0)
            pe = pl.when(use_married).then(p.num("personal_exemption_married_with_dependents")).otherwise(pe)
        df = df.with_columns(az_exemp=pl.col("az_txp") * pe + pl.col("az_nchild") * p.num("dependent_exemption"))
        aged_amount = p.num("aged_exemption")
    else:
        df = df.with_columns(az_exemp=pl.lit(0.0))
        aged_amount = p.num("aged_exemption")
    # Each taxpayer 65 or older adds an exemption.
    df = df.with_columns(az_exemp=pl.col("az_exemp") + aged_amount * aged_count())

    # --- Standard deduction ---
    if p.in_effect("standard_deduction_percent"):
        sd = p.value("standard_deduction_percent")
        aif = p.num("indexing_factor")
        # TAXSIM rounds the index on the rate to tenths through 1983 and
        # hundredths in 1984-1985.
        if effective_year <= 1983:
            fctr = round(aif * 10.0) / 10.0
        elif effective_year in (1984, 1985):
            fctr = round(aif * 100.0) / 100.0
        else:
            fctr = aif
        df = df.with_columns(
            az_stded=(float(sd["rate"]) * fctr * pl.col("az_agi")).clip(0, float(sd["cap_per_exemption"]) * pl.col("az_txp") * aif)
        )
    elif p.in_effect("standard_deduction_per_exemption"):
        df = df.with_columns(az_stded=pl.col("az_txp") * p.num("standard_deduction_per_exemption"))
    else:
        multiple = p["standard_deduction_multiple"]
        coef = (
            pl.when(files_joint())
            .then(float(multiple["married_joint"]))
            .when(files_head_of_household())
            .then(float(multiple["head_of_household"]))
            .otherwise(float(multiple["other"]))
        )
        df = df.with_columns(az_stded=coef * p.num("standard_deduction"))

    # --- Itemized deduction ---
    df = df.with_columns(az_raw_itemized=pl.col("proptax") + pl.col("otheritem") + pl.col("mortgage"))
    if p.in_effect("child_care_deduction"):
        ccd = p.value("child_care_deduction")
        df = df.with_columns(
            az_childcare_ded=pl.when(
                pl.col("az_household_income") < ccd["income_limit"] / pl.col("az_sep")
            )
            .then(pl.col("childcare").clip(0, ccd["cap"]))
            .otherwise(0.0)
        )
        # TAXSIM counts other taxes twice.
        df = df.with_columns(
            az_xitded=(pl.col("az_raw_itemized") + pl.col("otheritem") + pl.col("az_childcare_ded")).clip(0, None)
        )
    else:
        # The federal gross itemized total, including the state tax deduction.
        df = df.with_columns(az_xitded_base=pl.col("salt_capped") + pl.col("mortgage"))
        if p.in_effect("itemized_limitation"):
            lim = p.value("itemized_limitation")
            index = float(resolve_year(STATE_ADJUSTMENT_PARAMS["itemized_phaseout_inflation"], effective_year))
            if isinstance(lim["threshold"], dict):
                df = df.with_columns(az_phas=by_filing_status({s: float(v) * index for s, v in lim["threshold"].items()}))
            else:
                df = df.with_columns(az_phas=float(lim["threshold"]) * index / pl.col("az_sep"))
            df = df.with_columns(
                az_reduce_raw=pl.when(pl.col("az_agi") > pl.col("az_phas"))
                .then(
                    pl.min_horizontal(
                        lim["cap_rate"] * pl.col("az_xitded_base"),
                        lim["rate"] * (pl.col("az_agi") - pl.col("az_phas")),
                    )
                )
                .otherwise(0.0)
            )
            share = float(lim["reduction_share"])
            df = df.with_columns(az_xitded=(pl.col("az_xitded_base") - share * pl.col("az_reduce_raw")).clip(0, None))
        else:
            df = df.with_columns(az_xitded=pl.col("az_xitded_base"))

    df = df.with_columns(az_deduc=pl.max_horizontal(pl.col("az_stded"), pl.col("az_xitded")))
    df = df.with_columns(az_taxinc=(pl.col("az_agi") - pl.col("az_deduc") - pl.col("az_exemp")).clip(0, None))

    # --- Tax ---
    brackets = p.value("brackets")
    aif = p.num("bracket_inflation")
    if effective_year <= 1989:
        df = df.with_columns(az_regtax=aif * bracket_tax(pl.col("az_taxinc") / aif, brackets))
        rate_expr = bracket_rate(pl.col("az_taxinc") / aif, brackets)
    elif effective_year >= 2019:
        df = df.with_columns(
            az_taxy=pl.when(pl.col("az_rich")).then(pl.col("az_taxinc") / 2).otherwise(pl.col("az_taxinc"))
        )
        df = df.with_columns(az_stat=aif * bracket_tax(pl.col("az_taxy") / aif, brackets))
        df = df.with_columns(az_regtax=pl.when(pl.col("az_rich")).then(pl.col("az_stat") * 2).otherwise(pl.col("az_stat")))
        rate_expr = bracket_rate(pl.col("az_taxy") / aif, brackets)
    else:
        brackets_single = brackets["single"]
        brackets_rich = brackets["joint_or_hoh"]
        df = df.with_columns(
            az_regtax=aif
            * pl.when(pl.col("az_rich"))
            .then(bracket_tax(pl.col("az_taxinc") / aif, brackets_rich))
            .otherwise(bracket_tax(pl.col("az_taxinc") / aif, brackets_single))
        )
        rate_expr = pl.when(pl.col("az_rich")).then(bracket_rate(pl.col("az_taxinc") / aif, brackets_rich)).otherwise(
            bracket_rate(pl.col("az_taxinc") / aif, brackets_single)
        )

    # --- Credits ---
    if p.in_effect("family_income_credit"):
        fic = p.value("family_income_credit")
        limits = fic["agi_limit"]
        df = df.with_columns(
            az_fagi=pl.when(pl.col("az_rich")).then(float(limits.get("joint_or_hoh", 0))).otherwise(float(limits["single"])),
            az_famlim=pl.when(pl.col("az_rich")).then(float(fic["cap"]["joint_or_hoh"])).otherwise(float(fic["cap"]["single"])),
        )
        if "married_joint" in limits:
            joint_bp = [float(v) for v in limits["married_joint"]]
            hoh_bp = [float(v) for v in limits["head_of_household"]]
            joint_fagi = (
                pl.when(pl.col("depx") <= 1).then(joint_bp[0])
                .when(pl.col("depx") == 2).then(joint_bp[1])
                .when(pl.col("depx") == 3).then(joint_bp[2])
                .otherwise(joint_bp[3])
            )
            hoh_fagi = (
                pl.when(pl.col("depx") <= 1).then(hoh_bp[0])
                .when(pl.col("depx") == 2).then(hoh_bp[1])
                .when(pl.col("depx") == 3).then(hoh_bp[2])
                .when(pl.col("depx") == 4).then(hoh_bp[3])
                .otherwise(hoh_bp[4])
            )
            df = df.with_columns(
                az_fagi=pl.when(files_joint())
                .then(joint_fagi)
                .when(files_head_of_household())
                .then(hoh_fagi)
                .otherwise(pl.col("az_fagi"))
            )
        df = df.with_columns(
            az_famcr=pl.when(pl.col("az_agi") <= pl.col("az_fagi"))
            .then(pl.min_horizontal(pl.col("az_famlim"), (pl.col("depx") + pl.col("az_txp_raw")) * float(fic["per_exemption"])))
            .otherwise(0.0)
        )
    else:
        df = df.with_columns(az_famcr=pl.lit(0.0))

    if p.in_effect("dependent_tax_credit"):
        dtc = p.value("dependent_tax_credit")
        df = df.with_columns(
            az_ctc_raw=float(dtc["per_child"]) * pl.col("dep17") + float(dtc["per_other_dependent"]) * (pl.col("depx") - pl.col("dep17")).clip(0, None),
            az_cphase=float(dtc["phaseout_start"]) * pl.col("az_txp_raw"),
        )
        df = df.with_columns(
            az_ctc=(pl.col("az_ctc_raw") - ((pl.col("az_agi") - pl.col("az_cphase")).clip(0, None) / 1000.0) * float(dtc["reduction_per_1000"])).clip(0, None)
        )
    else:
        df = df.with_columns(az_ctc=pl.lit(0.0))

    df = df.with_columns(az_after_famcr=(pl.col("az_regtax") - pl.col("az_famcr") - pl.col("az_ctc")).clip(0, None))

    if p.in_effect("excise_credit"):
        exc = p.value("excise_credit")
        numb = pl.when(files_head_of_household()).then(2.0).otherwise(pl.col("az_txp_raw"))
        df = df.with_columns(
            az_excise=pl.when((pl.col("agi") <= float(exc["agi_limit"]) * numb) & ~is_dependent_filer())
            .then(pl.min_horizontal(float(exc["amount"]) * (pl.col("az_txp_raw") + pl.col("depx")), float(exc["cap"])))
            .otherwise(0.0)
        )
    else:
        df = df.with_columns(az_excise=pl.lit(0.0))

    # Refundable property tax credit (taxpayers 65 or older with pension or
    # Social Security income) or renter credit, whichever is larger.
    more = (pl.col("filing_status") != "single") | (pl.col("depx") > 0)
    shift = pl.when(more).then(float(p["property_tax_credit_family_addition"])).otherwise(0.0)
    amounts = [float(v) for v in p.value("property_tax_credit_amounts")]
    if p.in_effect("indexing_factor"):
        aif = p.num("indexing_factor")
        amounts = [aif * a for a in amounts]
    rows = [[float(t) + shift, a] for t, a in zip(p["property_tax_credit_thresholds"], amounts)] + [[1.0e20, 0.0]]
    pensions = pl.col("pensions") + pl.col("gssi")
    statute = behavior.limit_arizona_property_credit_per_statute
    property_credit = pl.when((aged_count() > 0) & (pensions > 0)).then(
        # The statute's income test excludes Social Security, which TAXSIM's
        # household income includes.
        interpolate_table(
            pl.col("az_household_income") - (pl.col("gssi") if statute else 0.0),
            rows,
        )
    ).otherwise(0.0)
    if statute:
        # ARS 43-1072(B): at most the property tax paid (renters: rent times
        # the landlord property-tax factor).
        paid = pl.col("proptax") + float(p["property_tax_credit_rent_factor"]) * pl.col("rentpaid")
        property_credit = pl.min_horizontal(property_credit, paid)
    if p.in_effect("renter_credit_rate"):
        renter_credit = (p.num("renter_credit_rate") * pl.col("rentpaid")).clip(0, p.num("renter_credit_cap"))
        if p.in_effect("renter_credit_limit"):
            limit = p.value("renter_credit_limit")
            renter_credit = pl.when(pl.col("az_agi") <= limit["agi_limit"]).then(
                renter_credit.clip(None, limit["max"])
            ).otherwise(0.0)
    else:
        renter_credit = pl.lit(0.0)
    df = df.with_columns(az_credit=pl.max_horizontal(property_credit, renter_credit))

    df = df.with_columns(siitax=(pl.col("az_after_famcr") - pl.col("az_excise") - pl.col("az_credit")) * flate)
    return with_state_detail(
        df,
        agi=pl.col("az_agi"),
        exemptions=pl.col("az_exemp"),
        standard_deduction=pl.col("az_stded"),
        itemized_deductions=pl.col("az_xitded"),
        taxable_income=pl.col("az_taxinc"),
        property_credit=property_credit,
        credits=pl.col("az_credit") + pl.col("az_excise") + pl.col("az_famcr") + pl.col("az_ctc"),
        rate=rate_expr,
    )
