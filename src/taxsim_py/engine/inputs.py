"""TAXSIM input variables and how absent values default."""

import polars as pl

from taxsim_py.engine.schema import PARAMETERS_ROOT, load_yaml

_AGE_PARAMS = load_yaml(PARAMETERS_ROOT / "national" / "dependent_ages.yaml")

# All TAXSIM 35 input variables, in TAXSIM's order.
TAXSIM_INPUTS = (
    "taxsimid", "year", "state", "mstat", "page", "sage", "depx", "dep13", "dep17", "dep18",
    "pwages", "swages", "dividends", "intrec", "stcg", "ltcg", "otherprop", "nonprop", "pensions", "gssi",
    "ui", "transfers", "rentpaid", "proptax", "otheritem", "childcare", "mortgage", "scorp",
    "pbusinc", "pprofinc", "sbusinc", "sprofinc", "idtl", "mtr", "pui", "sui", "dep6", "dep19",
    "opt1", "opt1v", "opt2", "opt2v", "age1", "age2", "age3", "psemp", "ssemp",
)
# Integer-valued inputs that default to 0.
COUNT_INPUTS = ("depx", "dep6", "dep19", "page", "sage", "age1", "age2", "age3")
# Optional semantic extensions. These are not part of TAXSIM's 35 inputs and
# default to zero when a caller does not provide them.
OPTIONAL_CHILD_COUNT_INPUTS = ("children_under_3", "children_under_4", "children_under_7")
# Dependent counts by age group; they default to `depx`, or come from the
# child ages when any age column is present (as TAXSIM decides per file).
DEPENDENT_DEFAULT_INPUTS = ("dep13", "dep17", "dep18")
CHILD_AGE_INPUTS = ("age1", "age2", "age3")
# Dollar inputs that default to 0.
DOLLAR_INPUTS = (
    "pwages", "swages", "dividends", "intrec", "stcg", "ltcg", "otherprop", "nonprop", "pensions", "gssi",
    "ui", "transfers", "rentpaid", "proptax", "otheritem", "childcare", "mortgage", "scorp",
    "pbusinc", "pprofinc", "sbusinc", "sprofinc", "pui", "sui", "psemp", "ssemp",
)


def _age_limit(name: str, year: pl.Expr) -> pl.Expr:
    """The year's age limit from `dependent_ages.yaml`, row by row."""
    table = _AGE_PARAMS[name]
    years = sorted(table)
    expr = pl.lit(float(table[years[0]]))
    for start in years[1:]:
        expr = pl.when(year >= start).then(float(table[start])).otherwise(expr)
    return expr


def child_counts_from_ages(year: pl.Expr) -> dict[str, pl.Expr]:
    """`dep6`, `dep13`, `dep17` and `dep18` counted from `age1`-`age3` (ages above 0 are children)."""
    def count(limit: pl.Expr) -> pl.Expr:
        return pl.sum_horizontal(
            ((pl.col(c) > 0) & (pl.col(c) < limit)).cast(pl.Int64) for c in CHILD_AGE_INPUTS
        )

    return {
        "dep6": count(pl.lit(float(_AGE_PARAMS["young_child_age"]))),
        "dep13": count(_age_limit("child_care_age", year)),
        "dep17": count(_age_limit("child_tax_credit_age", year)),
        "dep18": count(_age_limit("eitc_age", year)),
        "children_under_3": count(pl.lit(3.0)),
        "children_under_4": count(pl.lit(4.0)),
        "children_under_7": count(pl.lit(7.0)),
    }


def with_input_defaults(frame: pl.DataFrame, year: pl.Expr) -> pl.DataFrame:
    """Fill absent or null inputs, cast counts to integers and dollar amounts to floats.

    A row with any child age (`age1`-`age3`) takes its age-group dependent
    counts from the ages, as TAXSIM does for a file with age columns; other
    rows use `dep13`, `dep17` and `dep18`, defaulting to `depx`.
    """
    ages_present = [c for c in CHILD_AGE_INPUTS if c in frame.columns]
    row_uses_ages = pl.any_horizontal(pl.col(c).is_not_null() for c in ages_present) if ages_present else None
    frame = frame.with_columns(
        pl.col("mstat").cast(pl.Int64),
        *[(pl.col(c) if c in frame.columns else pl.lit(0)).cast(pl.Int64).fill_null(0).alias(c) for c in COUNT_INPUTS],
        *[(pl.col(c) if c in frame.columns else pl.lit(0)).cast(pl.Int64).fill_null(0).alias(c) for c in OPTIONAL_CHILD_COUNT_INPUTS],
        *[
            (pl.col(c) if c in frame.columns else pl.lit(0.0)).cast(pl.Float64).fill_null(0.0).alias(c)
            for c in DOLLAR_INPUTS
        ],
        *([row_uses_ages.alias("__uses_child_ages")] if row_uses_ages is not None else []),
    )
    counts = {
        c: (pl.col(c).cast(pl.Int64).fill_null(pl.col("depx")) if c in frame.columns else pl.col("depx"))
        for c in DEPENDENT_DEFAULT_INPUTS
    }
    counts["dep6"] = pl.col("dep6")
    child_counts = {c: pl.col(c) for c in OPTIONAL_CHILD_COUNT_INPUTS}
    if row_uses_ages is None:
        return frame.with_columns(**counts, **child_counts)
    from_ages = child_counts_from_ages(year)
    return frame.with_columns(
        **{c: pl.when(pl.col("__uses_child_ages")).then(from_ages[c]).otherwise(counts[c]) for c in counts},
        **{c: pl.when(pl.col("__uses_child_ages")).then(from_ages[c]).otherwise(child_counts[c]) for c in OPTIONAL_CHILD_COUNT_INPUTS},
    ).drop("__uses_child_ages")


# TAXSIM `mstat` codes accepted: 1 single (head of household with
# dependents), 2 joint, 6 separate, 8 dependent filer; 3, 33 and 66 are the
# older single, single-without-head-of-household and separate codes.
SUPPORTED_MSTAT = (1, 2, 3, 6, 8, 33, 66)


def filing_status() -> pl.Expr:
    """Filing status from `mstat` and `depx`."""
    mstat = pl.col("mstat")
    return (
        pl.when(mstat.is_in([1, 3]) & (pl.col("depx") > 0)).then(pl.lit("head_of_household"))
        .when(mstat.is_in([1, 3, 8, 33])).then(pl.lit("single"))
        .when(mstat == 2).then(pl.lit("married_joint"))
        .when(mstat.is_in([6, 66])).then(pl.lit("married_separate"))
    )


def files_single() -> pl.Expr:
    return pl.col("filing_status") == "single"


def files_joint() -> pl.Expr:
    return pl.col("filing_status") == "married_joint"


def files_separate() -> pl.Expr:
    return pl.col("filing_status") == "married_separate"


def files_head_of_household() -> pl.Expr:
    return pl.col("filing_status") == "head_of_household"


def separate_divisor() -> pl.Expr:
    """2 on a married-separate return, otherwise 1 (TAXSIM `sep`)."""
    return pl.when(files_separate()).then(2.0).otherwise(1.0)


def taxpayer_count() -> pl.Expr:
    """Taxpayers on the return (TAXSIM `data(7)`): 0 for a dependent filer."""
    mstat = pl.col("mstat")
    return pl.when(mstat == 2).then(2.0).when(mstat == 8).then(0.0).otherwise(1.0)


def is_dependent_filer() -> pl.Expr:
    """Whether the return is claimed as someone else's dependent (`data(105)`)."""
    return pl.col("mstat") == 8


def aged_count() -> pl.Expr:
    """Taxpayers 65 or older (TAXSIM `data(9)`)."""
    return (pl.col("page") >= 65).cast(pl.Float64) + (pl.col("sage") >= 65).cast(pl.Float64)


def federal_exemption_count(year: int) -> pl.Expr:
    """Federal exemptions claimed (TAXSIM `comnew(68)`).

    Taxpayers and dependents, plus taxpayers 65 or older before 1987; none
    for a dependent filer from 1987.
    """
    if year <= 1986:
        return taxpayer_count() + pl.col("depx") + aged_count()
    return pl.when(is_dependent_filer()).then(0.0).otherwise(taxpayer_count() + pl.col("depx"))
