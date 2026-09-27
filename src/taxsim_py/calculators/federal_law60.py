"""Federal individual income tax calculator for 1960 through 1976."""

import polars as pl

from taxsim_py.engine.brackets import bracket_tax_by_status
from taxsim_py.engine.eitc import trapezoid_credit
from taxsim_py.engine.inputs import aged_count, files_separate, filing_status, is_dependent_filer, taxpayer_count
from taxsim_py.engine.schema import PARAMETERS_ROOT, YearParams, load_yaml, resolve_year
from taxsim_py.engine.state import by_filing_status, unemployment_total, with_default

LAW60_PARAMS = load_yaml(PARAMETERS_ROOT / "national" / "law60.yaml")

FILING_STATUSES = ["single", "married_joint", "married_separate", "head_of_household"]
SEPRET_BY_STATUS = {
    "single": 1.0,
    "married_joint": 1.0,
    "head_of_household": 1.0,
    "married_separate": 2.0,
}


def _halve_thresholds(brackets: list[list[float]]) -> list[list[float]]:
    # Separate returns use the joint table at double income, halved
    # (`faster=taxinc*sepret`), which equals halving every threshold.
    return [[t / 2 if t < 1.0e28 else t, r] for t, r in brackets]


def compute_federal_income_tax_law60(
    df: pl.DataFrame | pl.LazyFrame, year: int
) -> pl.DataFrame | pl.LazyFrame:
    if year < 1960:
        raise NotImplementedError("law60 only covers 1960-1976 - see parameters/national/law60.yaml")
    p = YearParams(LAW60_PARAMS, year)
    original_columns = (
        df.collect_schema().names() if isinstance(df, pl.LazyFrame) else list(df.columns)
    )
    df = df.with_columns(
        filing_status=filing_status(),
        wages=pl.col("pwages") + pl.col("swages"),
    )
    for col in (
        "proptax", "otheritem", "mortgage", "intrec", "psemp", "ssemp",
        "dividends", "stcg", "ltcg", "ui", "pui", "sui", "childcare", "depx",
        "pensions", "otherprop", "nonprop", "page", "sage",
    ):
        df = with_default(df, col)

    sepret_expr = by_filing_status(SEPRET_BY_STATUS)
    single_brackets = resolve_year(p["brackets"]["single"], year)
    joint_brackets = resolve_year(p["brackets"]["married_joint"], year)
    brackets_by_status = {
        "single": single_brackets,
        "married_joint": joint_brackets,
        "head_of_household": resolve_year(p["brackets"]["head_of_household"], year),
        # Separate returns use the single table through 1970 (`nfile1=1`)
        # and the joint table at `sepret=2` from 1971.
        "married_separate": single_brackets if year <= 1970 else _halve_thresholds(joint_brackets),
    }

    df = df.with_columns(
        sepret=sepret_expr,
        se_income=pl.col("psemp") + pl.col("ssemp"),
    )
    df = df.with_columns(earned=pl.col("wages") + pl.col("se_income").clip(0, None))

    # Dividend exclusion.
    divexc_expr = by_filing_status(
        {status: resolve_year(p["dividend_exclusion"][status], year) for status in FILING_STATUSES}
    )
    df = df.with_columns(divexc=divexc_expr)
    df = df.with_columns(divall=(pl.col("dividends") - pl.col("divexc")).clip(0, None))

    # Capital gains: part of the long-term gain is excluded (`capded`),
    # applied to the net gain when a short-term loss offsets it. A net
    # loss is limited per return.
    caprat = p.num("capital_gains_exclusion_rate")
    loss_limit = pl.when(files_separate()).then(
        p.num("net_capital_loss_limit_married_separate")
    ).otherwise(p.num("net_capital_loss_limit"))
    df = df.with_columns(fullcg=pl.col("stcg") + pl.col("ltcg"))
    df = df.with_columns(
        capded=pl.when((pl.col("fullcg") > 0) & (pl.col("ltcg") > 0))
        .then(caprat * pl.when(pl.col("stcg") < 0).then(pl.col("fullcg")).otherwise(pl.col("ltcg")))
        .otherwise(0.0)
    )
    df = df.with_columns(
        capgn=pl.when(pl.col("fullcg") > 0)
        .then(pl.col("fullcg") - pl.col("capded"))
        .otherwise(pl.max_horizontal(pl.col("fullcg"), -loss_limit))
    )

    # Total income includes pensions and other property income; other
    # non-property income is the only adjustment in scope. Unemployment
    # compensation plays no role here.
    df = df.with_columns(
        ti=pl.col("divall") + pl.col("wages") + pl.col("intrec") + pl.col("se_income") + pl.col("capgn")
        + pl.col("pensions") + pl.col("otherprop")
    )
    df = df.with_columns(agi=pl.col("ti") + pl.col("nonprop"))

    # Itemized deduction: property and other taxes and mortgage interest,
    # plus child care as an uncapped itemized deduction in 1971-1975.
    deduc_base = pl.col("proptax") + pl.col("otheritem") + pl.col("mortgage")
    if year <= 1975:
        df = df.with_columns(deduc=deduc_base + pl.col("childcare"))
    else:
        df = df.with_columns(deduc=deduc_base)

    df = df.with_columns(agix=pl.col("agi").clip(0, None))
    exemption_amount = p.num("personal_exemption_amount")
    # Taxpayers (none for a dependent filer), dependents and one more for
    # each taxpayer 65 or older.
    df = df.with_columns(exemps_count=taxpayer_count() + pl.col("depx") + aged_count())
    if year <= 1963:
        # <=1963: a straight percentage of AGI capped at a flat ceiling, NO
        # floor term at all - taxsim_2022_10_21.f:22673-22674.
        pct = p.num("standard_deduction_pct")
        ceiling = p.num("standard_deduction_ceiling_other")
        df = df.with_columns(zbr=pl.min_horizontal(ceiling / pl.col("sepret"), pct * pl.col("agix")))
    elif year <= 1970:
        # 1964-1970: the percentage deduction up to the ceiling, at least
        # $200/sepret + $100 per exemption, both capped at ceiling/sepret
        # (taxsim_2022_10_21.f:22670-22672).
        pct = p.num("standard_deduction_pct")
        ceiling = p.num("standard_deduction_ceiling_other")
        per_exemption_base = p.num("standard_deduction_per_exemption_base")
        per_exemption_amount = p.num("standard_deduction_per_exemption_amount")
        df = df.with_columns(
            zbr=pl.max_horizontal(
                pl.min_horizontal(ceiling / pl.col("sepret"), pct * pl.col("agix")),
                pl.min_horizontal(
                    ceiling / pl.col("sepret"),
                    per_exemption_base / pl.col("sepret") + per_exemption_amount * pl.col("exemps_count"),
                ),
            )
        )
        if year == 1970:
            # 1970 Additional Allowance for Low Incomes
            # (taxsim_2024_09_21.f:22629-22632): `zbradd=900-100*exemps`,
            # `zbrpo=max(0,.5*(agi-(1100+625*exemps)))`,
            # `zbr += max(0, zbradd-zbrpo)`.
            lia = p["low_income_allowance_1970"]
            zbradd = lia["base"] - lia["per_exemption"] * pl.col("exemps_count")
            zbrpo = (
                lia["phaseout_rate"] * (pl.col("agi") - (lia["income_base"] + lia["income_per_exemption"] * pl.col("exemps_count")))
            ).clip(0, None)
            df = df.with_columns(zbr=pl.col("zbr") + (zbradd - zbrpo).clip(0, None))
    elif year <= 1974:
        # 1971-1974: one shared ceiling/floor formula for every status (no
        # joint/other split existed yet - taxsim_2022_10_21.f:22666-22672).
        zbr_pct = p.num("standard_deduction_pct")
        zbr_ceiling_other = p.num("standard_deduction_ceiling_other")
        zbr_floor_other = p.num("standard_deduction_floor_other")
        df = df.with_columns(
            zbr=pl.max_horizontal(
                pl.min_horizontal(zbr_ceiling_other / pl.col("sepret"), zbr_pct * pl.col("agix")),
                zbr_floor_other / pl.col("sepret"),
            )
        )
    else:
        zbr_pct = p.num("standard_deduction_pct")
        zbr_ceiling_other = p.num("standard_deduction_ceiling_other")
        zbr_floor_other = p.num("standard_deduction_floor_other")
        zbr_ceiling_joint = p.num("standard_deduction_ceiling_joint")
        zbr_floor_joint = p.num("standard_deduction_floor_joint")
        # `nfile` stays 2 for separate returns (only `sepret` is 2), so
        # they get the joint ceiling and floor halved.
        df = df.with_columns(
            zbr=pl.when(pl.col("filing_status").is_in(["married_joint", "married_separate"]))
            .then(
                pl.max_horizontal(
                    pl.min_horizontal(zbr_ceiling_joint / pl.col("sepret"), zbr_pct * pl.col("agix")),
                    zbr_floor_joint / pl.col("sepret"),
                )
            )
            .otherwise(
                pl.max_horizontal(
                    pl.min_horizontal(zbr_ceiling_other / pl.col("sepret"), zbr_pct * pl.col("agix")),
                    zbr_floor_other / pl.col("sepret"),
                )
            )
        )

    df = df.with_columns(
        itemizes=pl.col("deduc") > pl.col("zbr"),
        amex=exemption_amount * pl.col("exemps_count"),
    )

    # Taxable income: AGI less the deduction and exemptions, at least 0.
    df = df.with_columns(
        taxable_income=(
            pl.col("agi") - pl.when(pl.col("itemizes")).then(pl.col("deduc")).otherwise(pl.col("zbr")) - pl.col("amex")
        ).clip(0, None)
    )

    df = df.with_columns(regtax=bracket_tax_by_status(pl.col("taxable_income"), brackets_by_status))

    # --- Alternative tax on capital gains: the `$50,000/sepret` blend
    # applies 1970-1975; `cgtx1`, the bracket tax, applies every year. ---
    df = df.with_columns(cg1=(pl.col("taxable_income") - pl.col("capded")).clip(0, None))
    df = df.with_columns(cgtx1=bracket_tax_by_status(pl.col("cg1"), brackets_by_status))
    if 1970 <= year <= 1975:
        threshold = p.num("alt_capital_gains_threshold")
        flat_component = p.num("alt_capital_gains_flat_component")
        cap_rate = p.num("alt_capital_gains_cap_rate")
        df = df.with_columns(
            gate=pl.min_horizontal(pl.col("ltcg"), pl.col("fullcg")) > threshold / pl.col("sepret"),
            xl32=bracket_tax_by_status(pl.max_horizontal(pl.col("taxable_income"), pl.col("capded")), brackets_by_status),
            xl33=bracket_tax_by_status(
                (pl.col("taxable_income") - pl.col("capded") + threshold / 2.0 / pl.col("sepret")).clip(0, None),
                brackets_by_status,
            ),
        )
        df = df.with_columns(raw_diff=(pl.col("xl32") - pl.col("xl33")).clip(0, None))
        # The blend is capped at 29.5% of the excess in 1970 and 32.5% in
        # 1971, and uncapped 1972-1975 (a very large cap rate).
        df = df.with_columns(
            cgtx2_gated=pl.min_horizontal(
                pl.col("raw_diff"),
                cap_rate * (pl.min_horizontal(pl.col("ltcg"), pl.col("fullcg")) - threshold / pl.col("sepret")).clip(0, None),
            )
        )
        df = df.with_columns(
            cgtx2=pl.when(pl.col("gate")).then(pl.col("cgtx2_gated")).otherwise(0.5 * pl.col("capded")),
            cgtx3=pl.when(pl.col("gate")).then(flat_component / pl.col("sepret")).otherwise(0.0),
        )
    else:  # year<=1969 (no matching branch) or year==1976 (`subd` unpopulated)
        df = df.with_columns(cgtx2=0.5 * pl.col("capded"), cgtx3=pl.lit(0.0))
    df = df.with_columns(
        acgtax=pl.when(pl.col("fullcg") > 0).then(pl.col("cgtx1") + pl.col("cgtx2") + pl.col("cgtx3")).otherwise(-1.0)
    )

    # --- Maximum tax on earned income (1971-1976) ---
    if year >= 1971:
        ebot_expr = by_filing_status(
            {status: resolve_year(p["max_tax_earned_income_floor"][status], year) for status in FILING_STATUSES}
        )
        eacc_expr = by_filing_status(
            {status: resolve_year(p["max_tax_earned_income_accumulated"][status], year) for status in FILING_STATUSES}
        )
        etop_mult = p.num("max_tax_earned_income_etop_multiplier")
        df = df.with_columns(
            ebot=ebot_expr,
            eacc=eacc_expr,
            psinc=pl.when(pl.col("agi") > 0).then((pl.col("earned") + pl.col("pensions")).clip(0, pl.col("agi"))).otherwise((pl.col("earned") + pl.col("pensions"))),
        )
        df = df.with_columns(
            eratio=pl.when(pl.col("agi") == 0)
            .then(1.0)
            .otherwise((pl.col("psinc") / pl.col("agi")).clip(None, 1.0))
        )
        excess = p["excess_itemized_preference"]
        df = df.with_columns(
            exded=pl.when(pl.col("itemizes") & ((pl.col("deduc") - excess["agi_floor_share"] * pl.col("agi")) > 0))
                .then(
                    pl.min_horizontal(
                        pl.col("deduc") - excess["agi_floor_share"] * pl.col("agi"), excess["cap_share"] * pl.col("agi")
                    )
                )
                .otherwise(0.0),
            preference_ui=unemployment_total(),
        )
        df = df.with_columns(pref=(pl.col("capded") + pl.col("preference_ui") + pl.col("exded")).clip(0, None))
        df = df.with_columns(eti=pl.col("taxable_income") * pl.col("eratio") - (pl.col("pref") - p["preference_exemption"]).clip(0, None))
        df = df.with_columns(
            etop=pl.col("eti") - pl.col("ebot"),
            partax=bracket_tax_by_status(pl.col("eti"), brackets_by_status),
        )
        df = df.with_columns(
            etax_raw=pl.col("regtax") - pl.col("partax") + pl.col("eacc") + etop_mult * pl.col("etop"),
            etax_eligible=(pl.col("sepret") == 1.0)
                & (pl.col("agi") >= 0)
                & (pl.col("etop") > 0)
                & (pl.when(pl.col("agi") == 0).then(True).otherwise(pl.col("psinc") > 0)),
        )
        df = df.with_columns(etax=pl.when(pl.col("etax_eligible")).then(pl.col("etax_raw")).otherwise(-1.0))
    else:
        df = df.with_columns(etax=pl.lit(-1.0))

    # --- Combine alternatives. When both the maximum tax and the
    # alternative capital gains tax save tax, their savings stack. ---
    df = df.with_columns(
        acgsav=pl.when(pl.col("acgtax") > 0).then((pl.col("regtax") - pl.col("acgtax")).clip(0, None)).otherwise(0.0)
    )
    df = df.with_columns(altax=pl.col("regtax") - pl.col("acgsav"))
    df = df.with_columns(
        altax=pl.when((pl.col("etax") > 0) & (pl.col("etax") < pl.col("regtax")) & (pl.col("acgsav") > 0))
        .then(pl.col("etax") - pl.col("acgsav"))
        .when((pl.col("etax") > 0) & (pl.col("etax") < pl.col("altax")))
        .then(pl.col("etax"))
        .otherwise(pl.col("altax"))
    )

    # --- Vietnam-era surtax (1968-1970): a multiplier on `altax` before
    # credits (taxsim_2022_10_21.f:23047-23056). ---
    if year in (1968, 1969, 1970):
        surtax_multiplier = p.num("surtax_multiplier")
        df = df.with_columns(altax=pl.col("altax") * surtax_multiplier)

    # --- Credits: Child Care Credit (1976), $30-per-exemption credit
    # (1975), General Tax Credit (1976). ---
    if year == 1976:
        expense_cap = p.num("child_care_credit_expense_cap")
        chr_rate = p.num("child_care_credit_flat_rate")
        df = with_default(df, "dep13")
        df = df.with_columns(chmax=expense_cap * pl.col("dep13").clip(0, 2))
        df = df.with_columns(child_expense=pl.min_horizontal(pl.col("chmax"), pl.col("wages").clip(0, None), pl.col("childcare")))
        df = df.with_columns(chcr=pl.col("child_expense") * chr_rate)
    else:
        df = df.with_columns(chcr=pl.lit(0.0))

    if year == 1975:
        exemption_credit = p.num("personal_exemption_credit_amount")
        df = df.with_columns(gencr=exemption_credit * pl.col("exemps_count"))
    elif year == 1976:
        per_exemption = p.num("general_tax_credit_per_exemption")
        cap = p.num("general_tax_credit_cap")
        rate = p.num("general_tax_credit_rate")
        df = df.with_columns(
            gencr=pl.max_horizontal(per_exemption * pl.col("exemps_count"), (rate * pl.col("taxable_income")).clip(0, cap))
        )
    else:
        df = df.with_columns(gencr=pl.lit(0.0))

    df = df.with_columns(
        credit_raw=pl.col("chcr") + pl.col("gencr"),
        credm=(pl.col("chcr") - pl.col("altax")).clip(0, None),
    )
    df = df.with_columns(credit=pl.col("credit_raw").clip(0, pl.col("altax")))
    df = df.with_columns(taxaft=(pl.col("altax") - pl.col("credit")).clip(0, None))

    # --- Add-on minimum tax, added to tax rather than a floor. ---
    if year <= 1975:
        offset_flat = p.num("addmin_offset_flat")
        rate = p.num("addmin_rate_pre1976")
        df = df.with_columns(offset=offset_flat / pl.col("sepret") + pl.col("taxaft") - pl.col("credit"))
        # Before 1976 `gencr` is subtracted again although `credit` already
        # reduced `taxaft` (taxsim_2022_10_21.f:23218,
        # `addmin = max(0,(data(81)+capgn-offset)*.10-credm-gencr)`).
        df = df.with_columns(
            addmin=(((pl.col("capgn") - pl.col("offset")) * rate) - pl.col("credm") - pl.col("gencr")).clip(0, None)
        )
    else:  # 1976
        offset_floor = p.num("addmin_offset_pct_floor")
        rate = p.num("addmin_rate_1976to1978")
        df = df.with_columns(
            offset=pl.max_horizontal(0.5 * (pl.col("taxaft") - pl.col("credit")), offset_floor / pl.col("sepret"))
        )
        df = df.with_columns(addmin=(((pl.col("capgn") - pl.col("offset")) * rate) - pl.col("credm")).clip(0, None))

    # The minimum tax is added from 1969 only.
    if year >= 1969:
        df = df.with_columns(tax_after_addmin=pl.col("taxaft") + pl.col("addmin"))
    else:
        df = df.with_columns(tax_after_addmin=pl.col("taxaft"))

    # --- EITC (real 1975-1976 only; eligibility keys off `depx`, not
    # `dep18` the way law79/law87 do - taxsim_2022_10_21.f:23188). ---
    if year >= 1975:
        rate_in = p.num("eitc_rate_in")
        max_credit = p.num("eitc_max_credit")
        phaseout_start = p.num("eitc_phaseout_start")
        rate_out = p.num("eitc_rate_out")
        df = df.with_columns(
            earncr_raw=trapezoid_credit(pl.col("earned"), pl.col("agi"), rate_in, max_credit, phaseout_start, rate_out)
        )
        df = df.with_columns(
            earncr=pl.when((files_separate()) | (pl.col("depx") == 0) | is_dependent_filer())
            .then(0.0)
            .otherwise(pl.col("earncr_raw"))
        )
    else:
        df = df.with_columns(earncr=pl.lit(0.0))

    df = df.with_columns(fiitax=pl.col("tax_after_addmin") - pl.col("earncr"))
    df = df.with_columns(
        taxable_income=pl.col("taxable_income"),
        taxable_unemployment=pl.lit(0.0),
        earned_income=pl.col("earned"),
        regular_tax=pl.col("regtax"),
        # Detail-output figures (TAXSIM's `comnew` slots for this law).
        credit=pl.col("credit"),
        federal_chcr=pl.col("chcr"),
        pre1987_chcr=pl.col("chcr"),
        pre1987_earncr=pl.col("earncr"),
        pre1987_taxbc=pl.col("altax"),
        pre1987_almtax=pl.lit(0.0),
        pre1987_zbr=pl.col("zbr"),
        pre1987_amex=pl.col("amex"),
        pre1987_deduc=pl.when(pl.col("itemizes")).then(pl.col("deduc")).otherwise(0.0),
        pre1987_gencr=pl.col("gencr"),
        pre1987_alminy=pl.lit(0.0),
        pre1987_taxinc=pl.col("taxable_income"),
    )
    result_columns = [
        "filing_status",
        "wages",
        "agi",
        "taxable_unemployment",
        "taxable_income",
        "earned_income",
        "regular_tax",
        "fiitax",
        "credit",
        "federal_chcr",
        "pre1987_chcr",
        "pre1987_earncr",
        "pre1987_taxbc",
        "pre1987_almtax",
        "pre1987_zbr",
        "pre1987_amex",
        "pre1987_deduc",
        "pre1987_gencr",
        "pre1987_alminy",
        "pre1987_taxinc",
    ]
    return df.select([*original_columns, *result_columns])
