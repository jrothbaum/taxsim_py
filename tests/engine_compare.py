"""How the Python and compiled calculators are compared.

Every column must agree to a relative 1e-10 (absolute 1e-8). The marginal
rates `frate` and `srate` are tax differences over a one-cent step, rounded to
two decimals, so floating-point noise of order 1e-13 in a tax can flip the
last printed digit when a rate sits on a half-cent boundary; those two columns
may differ by one rounding unit.
"""

import polars as pl
from polars.testing import assert_frame_equal

REL_TOL, ABS_TOL = 1e-10, 1e-8
RATE_COLUMNS = ("frate", "srate")
RATE_UNIT = 0.0101


def assert_engines_match(expected: pl.DataFrame, found: pl.DataFrame) -> None:
    rates = [c for c in RATE_COLUMNS if c in expected.columns]
    assert_frame_equal(expected.drop(rates), found.drop(rates), rel_tol=REL_TOL, abs_tol=ABS_TOL)
    for column in rates:
        worst = (expected[column] - found[column]).abs().max()
        if worst is not None and worst > RATE_UNIT:
            raise AssertionError(f"{column} differs by {worst}")
