"""Run the compiled TAXSIM oracle on a Polars frame of cases."""

from __future__ import annotations

import os
import subprocess
from collections.abc import Callable
import sys
from pathlib import Path

import polars as pl

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from taxsim_py.engine.inputs import CHILD_AGE_INPUTS, DEPENDENT_DEFAULT_INPUTS, TAXSIM_INPUTS  # noqa: E402

# Keep the frozen local executable as the compatibility baseline when present.
# Release and CI jobs can point at PolicyEngine's bundled or an archived NBER
# build without copying that binary into this repository. Build identity must
# be recorded because TAXSIM is being sunset and outputs differ by snapshot.
TAXSIM_EXE = Path(os.environ.get("TAXSIM_EXE", ROOT / "taxsim2024.exe")).expanduser().resolve()
# Inputs sent unless a case sets them: controls and child ages change how
# TAXSIM reads the whole file, so they are only sent when used.
_OPTIONAL = {"idtl", "mtr", "opt1", "opt1v", "opt2", "opt2v", *CHILD_AGE_INPUTS}
_DEPENDENT_AGE_COUNT_INPUTS = {*DEPENDENT_DEFAULT_INPUTS, "dep6", "dep19"}


# TAXSIM adds a leftover $1 to household income for every record after the
# first in a run; a throwaway first record puts every real case in that state.
_WARMUP_ID = 999_999_999
_WARMUP_VALUES = {"taxsimid": _WARMUP_ID, "year": 2000, "mstat": 1}


def _run(cases: pl.DataFrame, columns: list[str], executable: Path) -> pl.DataFrame:
    lines = [" ".join(columns)]
    # Output controls (the header TAXSIM prints) follow the first record.
    first = cases.row(0, named=True)
    warmup = {**_WARMUP_VALUES, **{c: first[c] for c in ("idtl", "mtr") if c in columns}}
    lines.append(" ".join(str(warmup.get(c, 0)) for c in columns))
    for row in cases.select(columns).iter_rows():
        lines.append(" ".join(str(v) for v in row))
    result = subprocess.run(
        [str(executable)], input="\n".join(lines) + "\n", capture_output=True, text=True, timeout=600
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"{executable} failed: {result.stderr}\n{result.stdout[-2000:]}"
        )
    out_lines = [ln for ln in result.stdout.splitlines() if ln.strip()]
    header = [h.strip() for h in out_lines[0].split(",")]
    rows = [[v.strip() for v in ln.split(",")] for ln in out_lines[1:]]
    out = pl.DataFrame(rows, schema=header, orient="row")
    out = out.with_columns(
        pl.col("taxsimid").cast(pl.Float64).cast(pl.Int64),
        *[pl.col(c).cast(pl.Float64) for c in header if c != "taxsimid"],
    ).filter(pl.col("taxsimid") != _WARMUP_ID)
    # Rolling taxsimtest builds replaced several old vNN columns with semantic
    # names. Normalize the directly corresponding fields so the same comparison
    # scripts can inspect both schemas. The removed `credits` and `v42` fields
    # have no direct replacement and are represented as zero, which makes a
    # substantive difference visible instead of failing during projection.
    present = set(out.columns)
    compatibility_columns = []
    if "credits" not in present:
        compatibility_columns.append(pl.lit(0.0).alias("credits"))
    if "v42" not in present:
        compatibility_columns.append(pl.lit(0.0).alias("v42"))
    for compatibility_name, semantic_name in {
        "v43": "niit",
        "v44": "addmed",
        "v45": "cares",
    }.items():
        if compatibility_name not in present and semantic_name in present:
            compatibility_columns.append(pl.col(semantic_name).alias(compatibility_name))
    return out.with_columns(*compatibility_columns)


def run_oracle(
    cases: pl.DataFrame,
    idtl: int | None = None,
    mtr: int | None = None,
    executable: str | Path | None = None,
) -> pl.DataFrame:
    """TAXSIM outputs for `cases` (one row per `taxsimid`).

    Absent or null inputs are sent as 0, except `dep13`/`dep17`/`dep18`,
    which take `depx` as TAXSIM does. Rows that set a child age run in a
    separate batch with the age columns, since TAXSIM derives dependent counts
    from ages for every row of a file with age columns.
    """
    oracle_executable = Path(executable or TAXSIM_EXE).expanduser().resolve()
    frame = cases
    if idtl is not None:
        frame = frame.with_columns(idtl=pl.lit(idtl))
    if mtr is not None:
        frame = frame.with_columns(mtr=pl.lit(mtr))
    base = [c for c in TAXSIM_INPUTS if c not in _OPTIONAL]
    controls = [c for c in ("idtl", "mtr", "opt1", "opt1v", "opt2", "opt2v") if c in frame.columns]
    # Dependent counts TAXSIM would take from `depx` when not given.
    frame = frame.with_columns(
        pl.col(c).fill_null(pl.col("depx")) if c in frame.columns else pl.col("depx").alias(c)
        for c in DEPENDENT_DEFAULT_INPUTS
        if "depx" in frame.columns
    )
    frame = frame.with_columns(
        pl.col(c).fill_null(0) if c in frame.columns else pl.lit(0).alias(c) for c in base
    )
    ages_present = [c for c in CHILD_AGE_INPUTS if c in frame.columns]
    if ages_present:
        uses_ages = pl.any_horizontal(pl.col(c).is_not_null() for c in ages_present)
        with_ages = frame.filter(uses_ages).with_columns(
            pl.col(c).fill_null(0) if c in frame.columns else pl.lit(0).alias(c) for c in CHILD_AGE_INPUTS
        )
        without_ages = frame.filter(~uses_ages)
    else:
        with_ages = frame.head(0)
        without_ages = frame
    parts = []
    if without_ages.height:
        parts.append(_run(without_ages, [*base, *controls], oracle_executable))
    if with_ages.height:
        # Current TAXSIM-35 rejects a file that supplies both individual ages
        # and depNN age-band counts. Older binaries silently preferred ages.
        age_base = [column for column in base if column not in _DEPENDENT_AGE_COUNT_INPUTS]
        parts.append(
            _run(
                with_ages,
                [*age_base, *controls, *CHILD_AGE_INPUTS],
                oracle_executable,
            )
        )
    return pl.concat(parts, how="diagonal_relaxed")


def knife_edge_ids(
    cases: pl.DataFrame,
    calculate: Callable[[pl.DataFrame], pl.DataFrame],
    rate_columns: list[str],
    mtr: int = 85,
    shift: float = 1.0,
    tolerance: float = 0.015,
) -> set[int]:
    """Ids of `cases` whose marginal rates agree with the oracle once wages move by `shift`.

    TAXSIM computes some thresholds and constants in single precision, so a
    case sitting exactly on a round-number threshold can get a marginal rate a
    few hundredths off from the exact one. A rate mismatch that disappears a
    dollar away is that round-off, not a rule difference.
    """
    if cases.is_empty():
        return set()
    shifted = cases.with_columns(pwages=pl.col("pwages").fill_null(0) + shift)
    ours = calculate(shifted).select("taxsimid", *rate_columns)
    theirs = run_oracle(shifted, mtr=mtr, idtl=2).select(
        "taxsimid", *[pl.col(c).alias(f"{c}_oracle") for c in rate_columns]
    )
    agree = ours.join(theirs, on="taxsimid").filter(
        pl.all_horizontal((pl.col(c) - pl.col(f"{c}_oracle")).abs() <= tolerance for c in rate_columns)
    )
    return set(agree["taxsimid"].to_list())
