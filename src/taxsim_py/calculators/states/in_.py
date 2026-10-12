"""Indiana individual income tax calculator."""


from __future__ import annotations
import polars as pl

from taxsim_py.calculators.federal_pre1987 import PRE1987_PARAMS
from taxsim_py.engine.eitc import trapezoid_credit
from taxsim_py.engine.inputs import aged_count, files_joint, is_dependent_filer, separate_divisor, taxpayer_count
from taxsim_py.engine.schema import PARAMETERS_ROOT, YearParams, load_yaml, resolve_year
from taxsim_py.engine.state import by_filing_status, household_income, interpolate_table, unemployment_total, with_state_detail
from taxsim_py.behavior import BehaviorProfile, TAXSIM_BEHAVIOR
from taxsim_py.engine.state_extrapolation import deflate_for_extrapolation, resolve_state_year

FEDERAL_EITC_PARAMS = pl.read_csv(PARAMETERS_ROOT / "national" / "eitc.csv")
IN_PARAMS = load_yaml(PARAMETERS_ROOT / "states" / "in" / "income_tax.yaml")

def compute_in_tax(df: pl.DataFrame, year: int, behavior: BehaviorProfile = TAXSIM_BEHAVIOR) -> pl.DataFrame:
    effective_year, flate = resolve_state_year(year, behavior, IN_PARAMS)
    p = YearParams(IN_PARAMS, effective_year)
    df = df.with_columns(
        in_household_income=household_income(),
        in_sep=separate_divisor(),
        in_taxpayers=taxpayer_count(),
    )

    df = deflate_for_extrapolation(df, flate, extra=("in_household_income",))

    rate = p.num("rate")

    # --- AGI ---
    # Social Security benefits are exempt.
    df = df.with_columns(in_agi=pl.col("agi").clip(0, None) - pl.col("taxable_social_security"))

    ui_total = unemployment_total()

    if p.in_effect("dividend_exclusion_per_taxpayer"):
        excl_table = PRE1987_PARAMS["dividend_exclusion"]
        fed_excl = by_filing_status({status: resolve_year(amounts, effective_year) for status, amounts in excl_table.items()})
        divexc = pl.min_horizontal(pl.col("dividends") + pl.col("intrec"), fed_excl)
        own_excl_per_filer = p.num("dividend_exclusion_per_taxpayer")
        own_excl = pl.min_horizontal(pl.col("dividends"), own_excl_per_filer * pl.col("in_taxpayers"))
        df = df.with_columns(in_agi=pl.col("in_agi") + divexc - own_excl)

    df = df.with_columns(in_untax=pl.col("taxable_unemployment"))

    if effective_year == 2020:
        df = df.with_columns(in_agi=pl.col("in_agi") + ui_total - pl.col("in_untax"))

    if p.in_effect("ui_addback_per_taxpayer"):
        cap = p.num("ui_addback_per_taxpayer")
        df = df.with_columns(in_agi=pl.col("in_agi") + pl.min_horizontal(ui_total, cap * pl.col("in_taxpayers")))

    # Indiana's own UI exclusion is zero when no UI is present.
    thr_single = float(p["ui_exclusion_threshold"]["single"])
    thr_joint = float(p["ui_exclusion_threshold"]["married_joint"])
    threshold = pl.when(files_joint()).then(thr_joint).otherwise(thr_single)
    if effective_year <= 2008:
        xlin6 = pl.min_horizontal(pl.col("in_untax"), 0.5 * (pl.col("agi") - threshold).clip(0, None))
        unded = pl.col("in_untax") - xlin6
    else:
        xlin7 = 0.5 * (pl.col("agi") + ui_total - pl.col("in_untax") - threshold).clip(0, None)
        unded = (ui_total - xlin7).clip(0, None)
    df = df.with_columns(in_agi=pl.col("in_agi") - unded)

    # --- Deductions ---
    # Renter's deduction (1979+): rent paid up to a cap, halved for
    # married separate filers.
    if p.in_effect("renter_deduction_cap"):
        dedr = pl.min_horizontal(pl.col("rentpaid"), p.num("renter_deduction_cap")) / pl.col("in_sep")
    else:
        dedr = pl.lit(0.0)

    if p.in_effect("homeowner_property_tax_deduction_cap"):
        home_cap = p.num("homeowner_property_tax_deduction_cap")
        dedown = pl.min_horizontal(pl.col("proptax"), home_cap)
        dedown = pl.when(pl.col("in_sep") == 2).then(dedown / pl.col("in_sep")).otherwise(dedown)
    else:
        dedown = pl.lit(0.0)

    if p.in_effect("earned_income_deduction"):
        ded = p.value("earned_income_deduction")
        wages_plus_se = pl.col("wages") + pl.col("psemp") + pl.col("ssemp")
        eligible = (
            (pl.col("depx") > 0)
            & (pl.col("in_agi") < ded["income_limit"])
            & (ded["earnings_share"] * pl.col("in_agi").clip(0, None) < wages_plus_se)
        )
        dedei = pl.when(eligible).then(ded["income_limit"] - pl.col("in_agi").clip(0, None)).otherwise(0.0)
    else:
        dedei = pl.lit(0.0)

    df = df.with_columns(in_deduc=dedr + dedown + dedei)

    # --- Exemptions ---
    # The federal exemption count is deflated in projected years; `depx` is
    # not.
    aged = aged_count()
    if effective_year <= 1986:
        exemps_count = (pl.col("in_taxpayers") + pl.col("depx") + aged) / flate
    else:
        exemps_count = pl.when(is_dependent_filer()).then(0.0).otherwise(pl.col("in_taxpayers") + pl.col("depx")) / flate
    per_taxpayer = p.num("exemption_per_taxpayer")
    if p.in_effect("exemption_per_dependent"):
        exemp = (pl.col("in_taxpayers") + aged) * per_taxpayer + pl.col("depx") * p.num("exemption_per_dependent")
    elif p.in_effect("exemption_income_addition"):
        base = (exemps_count + aged) * per_taxpayer
        is_joint = files_joint()
        addition = p.value("exemption_income_addition")
        floor, maximum = addition["floor"], addition["maximum"]
        xtra1 = (pl.col("in_agi") / 3.0 - floor).clip(0, maximum)
        xtra2 = (pl.col("in_agi") * 2.0 / 3.0 - floor).clip(0, maximum)
        xtra = pl.when(is_joint).then(xtra1 + xtra2).otherwise((pl.col("in_agi") - floor).clip(0, maximum))
        exemp = base + xtra
    elif effective_year <= 1986:
        exemp = exemps_count * per_taxpayer
    else:
        # A dependent filer claims one exemption.
        exemp = (is_dependent_filer().cast(pl.Float64) + exemps_count + aged) * per_taxpayer
        if p.in_effect("aged_low_income_exemption"):
            ale = p.value("aged_low_income_exemption")
            low = pl.col("agi") < ale["agi_limit"]
            exemp = exemp + pl.when(low).then(ale["amount"] * aged).otherwise(0.0)

    if effective_year >= 1997:
        exemp = exemp + p.num("dependent_exemption_addition") * pl.col("depx")

    df = df.with_columns(in_exemp=exemp)

    df = df.with_columns(in_taxinc=(pl.col("in_agi") - pl.col("in_deduc") - pl.col("in_exemp")).clip(0, None))
    df = df.with_columns(in_statax=pl.col("in_taxinc") * rate)
    if p.in_effect("tax_share"):
        df = df.with_columns(in_statax=pl.col("in_statax") * p.num("tax_share"))

    # --- Credits ---
    if p.in_effect("eitc_own"):
        own = p.value("eitc_own")
        rate_cr, cap = float(own["rate"]), float(own["income_cap"])
        # Tested against federal total income.
        total_income = pl.col("agi") + 0.5 * pl.col("payroll_setax")
        eligible = (
            (pl.col("depx") > 0)
            & ((pl.col("earned_income") >= own["earnings_share"] * total_income) | (total_income < 1.0))
            & (total_income < cap)
        )
        earncr = pl.when(eligible).then(rate_cr * (cap - total_income.clip(0, None))).otherwise(0.0)
    elif p.in_effect("eitc_federal_share"):
        rate_cr = p.num("eitc_federal_share")
        fed_eitc = pl.col("eitc")
        earncr = pl.when(fed_eitc >= p.num("eitc_federal_floor")).then(rate_cr * fed_eitc).otherwise(0.0)
        if effective_year >= 2022:
            earncr = rate_cr * fed_eitc
            if effective_year == 2022:
                # 2022 is computed on its own formula: at most two children and no
                # higher phase-out start for joint returns.
                rows = FEDERAL_EITC_PARAMS.filter((pl.col("year") == 2022) & (pl.col("filing_status") == "single"))
                children = pl.col("dep18").clip(0, 2)
                decoupled = pl.lit(0.0)
                for row in rows.to_dicts():
                    amount = trapezoid_credit(
                        pl.col("earned_income"), pl.col("agi"), row["rate_in"], row["max_credit"], row["phaseout_start"], row["rate_out"]
                    )
                    decoupled = pl.when(children == row["num_children"]).then(amount).otherwise(decoupled)
                earncr = pl.when(fed_eitc > 0).then(rate_cr * decoupled).otherwise(0.0)
        elif p.in_effect("eitc_schedule"):
            schedule = p.value("eitc_schedule")
            rates = p["eitc_schedule_rates"]
            ieic_expr = pl.min_horizontal(pl.col("dep18"), 2.0)

            def by_children(values: list[float]) -> pl.Expr:
                return pl.when(ieic_expr == 0).then(pl.lit(float(values[0]))).when(ieic_expr == 1).then(
                    pl.lit(float(values[1]))
                ).otherwise(pl.lit(float(values[2])))

            crm = by_children(schedule["max_credit"])
            ym = by_children(schedule["phaseout_start"])
            rtbs = by_children(rates["phase_in_rate"])
            rtlw = by_children(rates["phaseout_rate"])
            earny = pl.col("earned_income")
            # Uses federal AGI, not Indiana AGI.
            modagi = pl.col("agi").clip(0, None)
            eic11 = pl.min_horizontal(rtbs * earny, crm)
            over = (modagi > ym) | (earny > ym)
            eicpo = rtlw * pl.max_horizontal(modagi, earny, ym) - rtlw * ym
            eic11 = pl.when(over).then(pl.min_horizontal(eic11, (crm - eicpo).clip(0, None))).otherwise(eic11)
            earncr = pl.min_horizontal(earncr, eic11)
    else:
        earncr = pl.lit(0.0)

    # Property tax credit (through 1980) and elderly credit, taxpayers 65 or older.
    fed_agi = pl.col("agi")
    if p.in_effect("property_credit"):
        pc = p.value("property_credit")
        ptax = pl.col("proptax") + pc["rent_share"] * pl.col("rentpaid")
        pcred = pl.when(aged > 0).then(
            (interpolate_table(pl.col("in_household_income"), pc["share_table"]) * ptax).clip(0, pc["cap"])
        ).otherwise(0.0)
        flat = p.value("elderly_credit_flat")
        ecred = pl.when(pl.col("in_household_income") < flat["household_income_limit"]).then(
            float(flat["amount"])
        ).otherwise(0.0)
    else:
        pcred = pl.lit(0.0)
        table = p.value("elderly_credit_table")
        second = p.num("elderly_credit_second_aged")
        # From 2022 the statute's step schedule, not TAXSIM's interpolation.
        base_credit = interpolate_table(fed_agi, table)
        if effective_year >= 2022:
            base_credit = pl.lit(0.0)
            for limit, amount in reversed(table):
                base_credit = pl.when(fed_agi < float(limit)).then(float(amount)).otherwise(base_credit)
        ecred = base_credit + pl.when(
            (aged > 1) & (files_joint())
        ).then(second).otherwise(0.0)
    ecred = pl.when((aged > 0) & (fed_agi < p["elderly_credit_agi_limit"])).then(ecred).otherwise(0.0)
    df = df.with_columns(in_credit=pcred + ecred + earncr)
    df = df.with_columns(in_statax=pl.col("in_statax") - pl.col("in_credit"))

    refund_cr = pl.lit(0.0)
    if p.in_effect("automatic_taxpayer_refund_credit"):
        refund_cr = p.num("automatic_taxpayer_refund_credit") * pl.col("in_taxpayers")
        df = df.with_columns(
            in_refund_cr=pl.when(pl.col("in_statax") > 0).then(refund_cr).otherwise(0.0)
        )
        df = df.with_columns(
            in_statax=pl.col("in_statax") - pl.col("in_refund_cr")
        )
    else:
        df = df.with_columns(in_refund_cr=pl.lit(0.0))

    df = df.with_columns(siitax=pl.col("in_statax") * flate)
    return with_state_detail(
        df,
        agi=pl.col("in_agi"),
        exemptions=pl.col("in_exemp"),
        taxable_income=pl.col("in_taxinc"),
        property_credit=pcred,
        eic=earncr,
        credits=pl.col("in_credit") + pl.col("in_refund_cr"),
        rate=rate,
    )
