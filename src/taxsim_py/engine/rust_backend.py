"""The compiled calculator (`taxsim_py/_internal*`, built from `rust/`) and its Polars plan.

One plugin expression takes the household inputs as a struct plus a config
(the law year, or a column of them, a marginal-rate code or column, and the
parameters directory) and returns a struct of the default outputs. Federal
tax, payroll tax, the state tax, the itemizing choice, the state-tax feedback
passes and the marginal rates all run inside it, reading the same YAML
parameters as the Python calculators. Python remains the reference
implementation; `scripts/validate_rust.py` compares the two.

The plugin takes prepared inputs only: numeric columns without nulls. Defaults,
casts and the like happen in Polars before it (`engine.inputs.with_input_defaults`).
It implements the statutory calculation mode only.

`TAXSIM_PY_ENGINE` (or the `engine` argument of `calculate_taxes`) is `rust`
(the default: for real runs, with speed, streaming and low memory) or `python`
(the reference: for developing and testing changes to the law, where a change is
a few readable lines). There is no automatic choice: the compiled calculator is
used or it raises.
"""


from __future__ import annotations
import os
from pathlib import Path
from collections.abc import Sequence
from functools import lru_cache

import polars as pl
from polars.plugins import register_plugin_function

from taxsim_py.behavior import BehaviorProfile, CalculationMode
from taxsim_py.engine.inputs import with_input_defaults
from taxsim_py.engine.schema import PARAMETERS_ROOT

_INPUTS = (
    "state", "mstat", "depx", "dep6", "dep13", "dep17", "dep18", "page", "sage", "pwages", "swages",
    "dividends", "intrec", "stcg", "ltcg", "otherprop", "nonprop", "pensions", "gssi", "ui", "transfers",
    "proptax", "otheritem", "childcare", "mortgage", "scorp", "pbusinc", "pprofinc", "sbusinc", "sprofinc",
    "pui", "sui", "psemp", "ssemp",
)
_OPTIONAL_INPUTS = ("charity_cash", "rentpaid", "children_under_3", "children_under_4", "children_under_7")
# The plugin's marginal-rate outputs; zero for rows without a code.
MARGINAL_COLUMNS = ("frate", "srate")
_KEPT = "__taxsim_py_in_"
_YEAR = "__taxsim_py_year"
_MARGINAL = "__taxsim_py_marginal"
_RESULT = "__taxsim_py_result"


@lru_cache(maxsize=1)
def _plugin_path():
    """The compiled calculator's file, or None in the Python-only build."""
    return next(iter(sorted(Path(__file__).resolve().parents[1].glob("_internal*"))), None)


def engine(choice: str | None = None) -> str:
    """The engine to use: `choice` if given, else `TAXSIM_PY_ENGINE`, else rust."""
    value = (choice if choice is not None else os.environ.get("TAXSIM_PY_ENGINE", "rust")).lower()
    if value not in ("python", "rust"):
        source = "engine" if choice is not None else "TAXSIM_PY_ENGINE"
        raise ValueError(f"{source} must be python or rust, not {value!r}")
    return value


def require_available(behavior: BehaviorProfile, detail: bool) -> None:
    """Raise unless the compiled calculator can do this calculation at all."""
    if _plugin_path() is None:
        raise RuntimeError(
            "engine='rust' needs the compiled calculator (it is built into the platform wheels), "
            "which this installation lacks; pass engine='python'"
        )
    if behavior.mode is not CalculationMode.STATUTORY:
        raise RuntimeError("engine='rust' implements statutory mode only; pass engine='python' for taxsim mode")
    if detail:
        raise RuntimeError(
            "engine='rust' cannot produce detailed output (idtl 2 or keep_intermediate); pass engine='python'"
        )


def expression(
    year: int | str,
    columns: Sequence[str] = (*_INPUTS, *_OPTIONAL_INPUTS),
    marginal: int | str | None = None,
) -> pl.Expr:
    """The compiled calculator as an expression: a struct of the output columns.

    `year` is the law year for every row, or the name of an integer column holding each
    row's law year. `marginal` is a TAXSIM marginal-rate code (`mtr`) for every row, or the
    name of a column of codes (0 for none); `frate` and `srate` are 0 without one.
    `columns` are the input columns, which must exist, hold no nulls and be numeric.
    """
    config = dict(parameters_root=str(PARAMETERS_ROOT))
    inputs = list(columns)
    if isinstance(year, str):
        config["year_column"] = year
        if year not in inputs:
            inputs.append(year)
    else:
        config["year"] = int(year)
    if isinstance(marginal, str):
        config["marginal_column"] = marginal
        if marginal not in inputs:
            inputs.append(marginal)
    elif marginal is not None:
        config["marginal"] = int(marginal)
    return register_plugin_function(
        plugin_path=_plugin_path(),
        function_name="taxsim",
        args=pl.struct(inputs),
        kwargs={"config": config},
        is_elementwise=True,
    )


def lazy_taxes(
    lf: pl.LazyFrame,
    *,
    year: int | None,
    year_column: str,
    state_expr: pl.Expr,
    marginal: int | None,
    marginal_values: pl.Expr | None,
    year_outputs: dict[str, str],
    output_columns: Sequence[str],
    supported_mstat: Sequence[int],
    last_year: int,
) -> pl.LazyFrame:
    """The input frame plus the output columns, as a lazy plan for the compiled calculator.

    The inputs are prepared (defaults, nulls, casts) in Polars exactly as for the Python
    calculation; only then does the plugin see them. The plan streams. `marginal` is one
    marginal-rate code for every row, or `marginal_values` an expression for each row's code.
    """
    names = lf.collect_schema().names()
    year_expr = pl.lit(int(year)) if year is not None else pl.col(year_column).cast(pl.Int64)

    def check(values: pl.Series) -> pl.Series:
        fields = values.struct.fields
        mstat, years = values.struct.field(fields[0]), values.struct.field(fields[1])
        if mstat.null_count():
            raise ValueError("Filing status (mstat) cannot be null")
        bad = set(mstat.unique().to_list()) - set(supported_mstat)
        if bad:
            raise ValueError(f"Unsupported filing status (mstat): {sorted(bad)}")
        if years.null_count():
            raise ValueError("Years cannot be null")
        if years.max() is not None and years.max() > last_year:
            raise ValueError(f"Tax years after {last_year} are not implemented (got {years.max()})")
        return mstat

    # The caller's columns are kept under a prefix; the prepared inputs take the plain names.
    wide = lf.with_columns(pl.all().name.prefix(_KEPT))
    extra = [state_expr.alias("state"), year_expr.alias(_YEAR)]
    if marginal_values is not None:
        extra.append(marginal_values.fill_null(0).cast(pl.Int64).alias(_MARGINAL))
    wide = with_input_defaults(wide.with_columns(extra), year_expr)
    wide = wide.with_columns(
        pl.struct(pl.col("mstat"), pl.col(_YEAR)).map_batches(check, return_dtype=pl.Int64, is_elementwise=True).alias("mstat")
    )
    have = wide.collect_schema().names()
    wide = wide.with_columns(pl.col(c).cast(pl.Float64).fill_null(0.0) for c in _OPTIONAL_INPUTS if c in have)
    inputs = [c for c in (*_INPUTS, *_OPTIONAL_INPUTS) if c in have]
    code = _MARGINAL if marginal_values is not None else marginal
    computed = wide.with_columns(expression(_YEAR, inputs, code).alias(_RESULT))
    added = {c: c for c in output_columns}
    added.update(year_outputs)
    if code is not None:
        added.update({c: c for c in MARGINAL_COLUMNS})
    added = {name: column for name, column in added.items() if name not in names}
    return computed.select(
        *[pl.col(_KEPT + c).alias(c) for c in names],
        *[pl.col(_RESULT).struct.field(column).alias(name) for name, column in added.items()],
    )
