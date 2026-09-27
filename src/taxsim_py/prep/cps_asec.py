"""CPS ASEC public-use files as TAXSIM tax units.

Tax units follow the Census tax model on the file: `FILESTAT` 1-3 (joint),
4 (head of household) and 5 (single) mark unit heads. A joint head's spouse
(`A_SPOUSE`, a line number) is coded either a non-filer or, in some years,
joint too, when the lower line number heads the unit. `DEP_STAT` points to
the claiming person, by `PPPOS - 40` in some years and by line number in
others, and a pointer to oneself means not claimed.
Adults who are neither filers, joint spouses nor claimed can form their own
units (married pairs of them file jointly), flagged `census_filer = False`.
Income is summed over the head and spouse; dependents' own income is left out.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

import polars as pl

from taxsim_py.calculators.states import FIPS_TO_TAXSIM

Frame = pl.DataFrame | pl.LazyFrame

# TAXSIM input, and the CPS person variables summed into it (the first
# variable present in each alternative group is used, for older and newer
# file layouts).
_INCOME = {
    "wages": [["wsal_val"]],
    "semp": [["semp_val"], ["frse_val", "frm_val"]],
    "dividends": [["div_val"]],
    "intrec": [["int_val"]],
    "otherprop": [["rnt_val"], ["oi_val"]],
    "nonprop": [["alm_val"]],
    "pensions": [["rtm_val", "pnsn_val"], ["ann_val"], ["srvs_val"], ["dsab_val"]],
    "gssi": [["ss_val"]],
    "ui": [["uc_val"]],
    "transfers": [["ssi_val"], ["paw_val"], ["wc_val"], ["vet_val"], ["csp_val"], ["fin_val"], ["ed_val"]],
    "ltcg": [["cap_gain"]],
    "cap_loss": [["cap_loss"]],
}
# The Census tax model's own results, summed over the unit for comparison.
_CENSUS = {
    "census_agi": "agi",
    "census_fedtax_bc": "fedtax_bc",
    "census_fedtax_ac": "fedtax_ac",
    "census_statetax_bc": "statetax_bc",
    "census_statetax_ac": "statetax_ac",
    "census_eitc": "eit_cred",
    "census_ctc": "ctc_crd",
    "census_actc": "actc_crd",
    "census_fica": "fica",
}


def read_cps_asec(directory: str | Path) -> dict[str, pl.LazyFrame]:
    """The person and household parquet files of a CPS ASEC download directory."""
    directory = Path(directory)
    return {name: pl.scan_parquet(directory / f"{name}.parquet") for name in ("person", "hhld")}


def _lower(frame: Frame) -> pl.DataFrame:
    frame = frame.collect() if isinstance(frame, pl.LazyFrame) else frame
    return frame.rename({c: c.lower() for c in frame.columns})


def _income_expr(groups: list[list[str]], present: set[str]) -> pl.Expr:
    total = pl.lit(0.0)
    for alternatives in groups:
        name = next((c for c in alternatives if c in present), None)
        if name is None and alternatives[0] in ("srvs_val", "dsab_val"):
            # Newer files split survivor and disability income into two sources.
            stem = "sur_val" if alternatives[0] == "srvs_val" else "dis_val"
            parts = [f"{stem}{i}" for i in (1, 2) if f"{stem}{i}" in present]
            total = total + sum((pl.col(p).cast(pl.Float64).fill_null(0.0) for p in parts), pl.lit(0.0))
        elif name is not None:
            total = total + pl.col(name).cast(pl.Float64).fill_null(0.0)
    return total


def _pointer_target(p: pl.DataFrame, dependent_pointer: str) -> pl.Expr:
    """Each person's identifier in the terms `DEP_STAT` uses."""
    readings = {"pppos": pl.col("pppos").cast(pl.Int64) - 40, "line": pl.col("a_lineno").cast(pl.Int64)}
    if dependent_pointer != "auto":
        return readings[dependent_pointer]
    pointers = p.filter(pl.col("dep_stat") > 0).select("ph_seq", pl.col("dep_stat").cast(pl.Int64).alias("target"))

    def resolved(target: pl.Expr) -> int:
        people = p.select("ph_seq", target.alias("target"))
        return pointers.join(people, on=["ph_seq", "target"], how="semi").height

    return max(readings.values(), key=resolved)


def cps_asec_tax_units(
    person: Frame,
    household: Frame,
    *,
    year: int | None = None,
    include_nonfilers: bool = True,
    dependent_pointer: Literal["auto", "pppos", "line"] = "auto",
) -> tuple[pl.DataFrame, pl.DataFrame]:
    """TAXSIM input rows for the tax units of a CPS ASEC person and household file.

    Returns the tax units (TAXSIM inputs with `state` in TAXSIM codes, plus
    `h_seq`, `head_pppos`, `weight`, `census_filer` and the Census tax model's
    results as `census_*`) and a person crosswalk (`ph_seq`, `pppos`,
    `taxsimid`, `role`). `year` defaults to the survey year less one.
    `dependent_pointer` is what `DEP_STAT` refers to; `"auto"` takes the
    reading that resolves more pointers.
    """
    p = _lower(person)
    h = _lower(household)
    present = set(p.columns)

    p = p.with_columns(
        ptr=_pointer_target(p, dependent_pointer),
        filestat=pl.col("filestat").cast(pl.Int64),
        dep_stat=pl.col("dep_stat").cast(pl.Int64),
        a_spouse=pl.col("a_spouse").cast(pl.Int64),
        a_lineno=pl.col("a_lineno").cast(pl.Int64),
        age=pl.col("a_age").cast(pl.Int64),
        **{name: _income_expr(groups, present) for name, groups in _INCOME.items()},
        **{name: pl.col(src).cast(pl.Float64).fill_null(0.0) if src in present else pl.lit(0.0)
           for name, src in _CENSUS.items()},
    )
    line_to_ptr = p.select("ph_seq", pl.col("a_lineno").alias("spouse_line"), pl.col("ptr").alias("spouse_ptr"),
                           pl.col("filestat").alias("spouse_filestat"))
    p = p.join(line_to_ptr, left_on=["ph_seq", "a_spouse"], right_on=["ph_seq", "spouse_line"], how="left")

    # A joint filer's spouse: a non-filer, or also coded joint with the higher line number.
    joint_spouse = (
        pl.col("spouse_filestat").is_between(1, 3)
        & ((pl.col("filestat") == 6) | (pl.col("filestat").is_between(1, 3) & (pl.col("a_lineno") > pl.col("a_spouse"))))
    ).fill_null(False)
    claimed = (pl.col("dep_stat") > 0) & (pl.col("dep_stat") != pl.col("ptr")) & (pl.col("filestat") == 6) & ~joint_spouse
    unit_nonfiler = (pl.col("filestat") == 6) & ~claimed & ~joint_spouse & (pl.col("age") >= 15)
    p = p.with_columns(claimed=claimed, joint_spouse=joint_spouse, unit_nonfiler=unit_nonfiler)
    # Married non-filers who are both unclaimed file together, headed by the lower line number.
    partner = p.select("ph_seq", pl.col("ptr").alias("spouse_ptr"), pl.col("unit_nonfiler").alias("partner_nonfiler"))
    p = p.join(partner, on=["ph_seq", "spouse_ptr"], how="left").with_columns(
        pl.col("partner_nonfiler").fill_null(False)
    )
    nonfiler_couple = pl.col("unit_nonfiler") & pl.col("partner_nonfiler")

    filer_head = pl.col("filestat").is_between(1, 5) & ~pl.col("joint_spouse")
    nonfiler_head = pl.col("unit_nonfiler") & (~nonfiler_couple | (pl.col("a_lineno") < pl.col("a_spouse")))
    nonfiler_spouse = pl.col("unit_nonfiler") & nonfiler_couple & (pl.col("a_lineno") > pl.col("a_spouse"))
    if not include_nonfilers:
        nonfiler_head = pl.lit(False)
        nonfiler_spouse = pl.lit(False)
    p = p.with_columns(
        role=pl.when(filer_head | nonfiler_head).then(pl.lit("head"))
        .when(pl.col("joint_spouse") | nonfiler_spouse).then(pl.lit("spouse"))
        .when(pl.col("claimed")).then(pl.lit("dependent"))
    )
    # Each head's unit; spouses join their spouse's unit, dependents their claimer's.
    p = p.with_columns(
        unit_ptr=pl.when(pl.col("role") == "head").then(pl.col("ptr"))
        .when(pl.col("role") == "spouse").then(pl.col("spouse_ptr"))
    )
    claimer_unit = p.select("ph_seq", pl.col("ptr").alias("claimer_ptr"), pl.col("unit_ptr").alias("claimer_unit"))
    p = p.join(claimer_unit, left_on=["ph_seq", "dep_stat"], right_on=["ph_seq", "claimer_ptr"], how="left").with_columns(
        unit_ptr=pl.when(pl.col("role") == "dependent").then(pl.col("claimer_unit")).otherwise(pl.col("unit_ptr"))
    )
    heads = p.filter(pl.col("role") == "head").sort("ph_seq", "ptr").with_row_index("taxsimid", offset=1)
    p = p.join(
        heads.select("ph_seq", pl.col("ptr").alias("unit_ptr"), "taxsimid"), on=["ph_seq", "unit_ptr"], how="left"
    )

    # Dependents by age group, as TAXSIM's `depx`, `dep6`, `dep13`, `dep17` and `dep18`.
    enrolled = pl.col("a_enrlw") == 1 if "a_enrlw" in present else pl.lit(False)
    disabled = pl.col("pemlr") == 6 if "pemlr" in present else pl.lit(False)
    age = pl.col("age")
    dependents = p.filter(pl.col("role") == "dependent").group_by("taxsimid").agg(
        depx=pl.len(),
        dep6=(age < 6).sum(),
        dep13=(age < 13).sum(),
        dep17=(age < 17).sum(),
        dep18=((age < 19) | ((age < 24) & enrolled) | disabled).sum(),
    )
    filers = p.filter(pl.col("role").is_in(["head", "spouse"]))
    sums = filers.group_by("taxsimid").agg(
        *[pl.col(c).sum() for c in ("dividends", "intrec", "otherprop", "nonprop", "pensions", "gssi", "transfers")],
        *[pl.col(c).sum() for c in _CENSUS],
        ltcg=(pl.col("ltcg") - pl.col("cap_loss")).sum(),
        ui=pl.col("ui").sum(),
    )
    spouse = filers.filter(pl.col("role") == "spouse").select(
        "taxsimid", sage=pl.col("age"), swages=pl.col("wages"), ssemp=pl.col("semp"), sui=pl.col("ui")
    )
    weight = pl.col("marsupwt").cast(pl.Float64) if "marsupwt" in present else pl.lit(None, dtype=pl.Float64)
    units = heads.select(
        "taxsimid",
        pl.col("ph_seq").alias("h_seq"),
        pl.col("pppos").alias("head_pppos"),
        weight.alias("weight"),
        census_filer=pl.col("filestat").is_between(1, 5),
        mstat=pl.when(pl.col("filestat").is_between(1, 3) | (pl.col("unit_nonfiler") & pl.col("partner_nonfiler")))
        .then(2).otherwise(1),
        page=pl.col("age"),
        pwages=pl.col("wages"),
        psemp=pl.col("semp"),
        pui=pl.col("ui"),
    )
    units = (
        units.join(spouse, on="taxsimid", how="left")
        .join(dependents, on="taxsimid", how="left")
        .join(sums, on="taxsimid", how="left")
        .with_columns(pl.col("sage", "depx", "dep6", "dep13", "dep17", "dep18").fill_null(0),
                      pl.col("swages", "ssemp", "sui").fill_null(0.0))
    )

    # Household: state, survey year and property tax (to the reference person's unit).
    h = h.select(
        "h_seq",
        state=pl.col("gestfips").cast(pl.Int64).replace_strict(FIPS_TO_TAXSIM, default=None),
        survey_year=pl.col("h_year").cast(pl.Int64),
        proptax=pl.col("prop_tax").cast(pl.Float64).fill_null(0.0),
    )
    reference = p.filter(pl.col("a_exprrp").is_in([1, 2]) & pl.col("taxsimid").is_not_null()).select(
        pl.col("ph_seq").alias("h_seq"), pl.col("taxsimid").alias("proptax_unit")
    ) if "a_exprrp" in present else pl.DataFrame(schema={"h_seq": pl.Int64, "proptax_unit": pl.UInt32})
    units = units.join(h, on="h_seq", how="left").join(reference.unique("h_seq"), on="h_seq", how="left")
    units = units.with_columns(
        year=pl.lit(year) if year is not None else pl.col("survey_year") - 1,
        proptax=pl.when(pl.col("proptax_unit") == pl.col("taxsimid")).then(pl.col("proptax")).otherwise(0.0),
    ).drop("survey_year", "proptax_unit")

    inputs = [
        "taxsimid", "year", "state", "mstat", "page", "sage", "depx", "dep6", "dep13", "dep17", "dep18",
        "pwages", "swages", "psemp", "ssemp", "dividends", "intrec", "ltcg", "otherprop", "nonprop",
        "pensions", "gssi", "ui", "pui", "sui", "transfers", "proptax",
    ]
    units = units.select(
        pl.col("taxsimid").cast(pl.Int64), *[c for c in inputs if c != "taxsimid"],
        "h_seq", "head_pppos", "weight", "census_filer", *_CENSUS,
    ).sort("taxsimid")
    crosswalk = p.select("ph_seq", "pppos", pl.col("taxsimid").cast(pl.Int64), "role").sort("ph_seq", "pppos")
    return units, crosswalk
