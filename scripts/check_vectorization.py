"""Reject row-wise dataframe operations and oversized expressions in calculators.

Expression trees grow when an intermediate expression is reused instead of
stored as a column (`engine.state.checkpoint`); Polars then repeats the
arithmetic for every copy, which makes calculators slow regardless of row
count. The size check runs every registered state (and the federal
calculator through the resolver) on sample years and fails when any single
expression passed to Polars exceeds `MAX_EXPRESSION_CHARS`.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = ROOT / "src" / "taxsim_py"
FORBIDDEN_METHODS = {
    "apply",
    "iter_rows",
    "map_batches",
    "map_elements",
    "map_rows",
    "rows",
    "rows_by_key",
}


MAX_EXPRESSION_CHARS = 150_000
SAMPLE_YEARS = (1980, 1988, 2003, 2010, 2015, 2021)


def _largest_expressions() -> list[tuple[int, str, int]]:
    """Largest expression (in characters) each state builds, per sample year."""
    sys.path[:0] = [str(ROOT / "src"), str(ROOT / "tests"), str(ROOT / "scripts")]
    import polars as pl
    import validate_states
    from taxsim_py.engine.federal_state import resolve_federal_and_state
    from taxsim_py.engine.inputs import with_input_defaults

    largest = [0]

    def measure(original):
        def wrapped(self, *exprs, **named):
            for expr in [*exprs, *named.values()]:
                if isinstance(expr, pl.Expr):
                    largest[0] = max(largest[0], len(str(expr)))
            return original(self, *exprs, **named)
        return wrapped

    originals = (pl.DataFrame.with_columns, pl.LazyFrame.with_columns)
    pl.DataFrame.with_columns = measure(originals[0])
    pl.LazyFrame.with_columns = measure(originals[1])
    results = []
    try:
        for name, (build_cases, calculator, years) in validate_states.STATES.items():
            # Inputs defaulted as the public API does.
            cases = with_input_defaults(
                pl.DataFrame(build_cases(), infer_schema_length=None), pl.col("year").cast(pl.Int64)
            )
            for year in SAMPLE_YEARS:
                if year not in years:
                    continue
                largest[0] = 0
                resolve_federal_and_state(cases.filter(pl.col("year") == year), year, calculator)
                results.append((largest[0], name, year))
    finally:
        pl.DataFrame.with_columns, pl.LazyFrame.with_columns = originals
    return results


def main() -> None:
    violations: list[str] = []
    for path in SOURCE_ROOT.rglob("*.py"):
        tree = ast.parse(path.read_text(), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
                continue
            if node.func.attr in FORBIDDEN_METHODS:
                relative = path.relative_to(ROOT)
                violations.append(f"{relative}:{node.lineno}: {node.func.attr}")

    if violations:
        details = "\n".join(violations)
        raise SystemExit(f"Row-wise dataframe operations found:\n{details}")

    sizes = _largest_expressions()
    oversized = [f"{name} {year}: {chars:,} characters" for chars, name, year in sizes if chars > MAX_EXPRESSION_CHARS]
    if oversized:
        details = "\n".join(oversized)
        raise SystemExit(
            f"Expressions over {MAX_EXPRESSION_CHARS:,} characters (store reused intermediates with "
            f"engine.state.checkpoint):\n{details}"
        )
    worst = max(sizes)
    print(f"Vectorization check passed (largest expression {worst[0]:,} characters: {worst[1]} {worst[2]}).")


if __name__ == "__main__":
    main()
