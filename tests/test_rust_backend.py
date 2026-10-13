"""The compiled calculator must reproduce the Python calculators exactly.

For every state at a spread of years, fresh random households plus the state's own
validation cases are run through both engines (with marginal rates) and compared; the
rest of the file checks the engine choice, lazy plans and sinks, and every case the
compiled calculator must refuse. `scripts/validate_rust.py` runs the same comparison
over every state and year. Skipped without the compiled calculator.
"""

import importlib
import os
import tempfile
import unittest
from unittest.mock import patch

import polars as pl
from polars.testing import assert_frame_equal

from engine_compare import assert_engines_match
from random_households import random_households
from taxsim_py import calculate_taxes
from taxsim_py.calculators.states import STATE_CALCULATOR_PATHS
from taxsim_py.engine import rust_backend

ROWS = 400
SEED = 4321  # not the seed the validation script uses
SAMPLE_YEARS = (1977, 1985, 1990, 1995, 2000, 2005, 2010, 2015, 2020, 2022, 2025)
EARLY_FEDERAL_YEARS = (1960, 1965, 1970, 1976, 1986)  # before state tax (1977) and before the 1987 law


def _frame(state: int, year: int, rows: int = ROWS) -> pl.DataFrame:
    module = STATE_CALCULATOR_PATHS[state][0].rstrip("_")
    parts = [random_households(state, year, rows, seed=SEED)]
    try:
        cases = pl.DataFrame(getattr(importlib.import_module(f"{module}_cases"), f"build_{module}_test_cases")())
        cases = cases.drop("description", strict=False).filter(pl.col("year") == year)
        if cases.height:
            parts.append(cases.with_columns(state=pl.lit(state)))
    except (ImportError, AttributeError):
        pass
    return pl.concat(parts, how="diagonal_relaxed").with_row_index("taxsim_id", offset=1)


def _implemented(error: Exception) -> bool:
    return "no implementation" not in str(error)


@unittest.skipIf(rust_backend._plugin_path() is None, "the compiled calculator is not installed")
class CompiledCalculatorTests(unittest.TestCase):
    def test_every_state_matches_python(self):
        compared = {}
        for state in sorted(STATE_CALCULATOR_PATHS):
            for year in SAMPLE_YEARS:
                with self.subTest(state=state, year=year):
                    frame = _frame(state, year)
                    try:
                        found = calculate_taxes(frame, mtr=11, engine="rust")
                    except RuntimeError as error:
                        # Only a state-year the compiled calculator has no law for may be refused.
                        self.assertFalse(_implemented(error), error)
                        continue
                    try:
                        expected = calculate_taxes(frame, mtr=11, engine="python")
                    except Exception:  # Python has no law for it either
                        continue
                    compared[state] = compared.get(state, 0) + 1
                    assert_engines_match(expected, found)
        self.assertEqual(sorted(compared), sorted(STATE_CALCULATOR_PATHS), "a state was never compared")

    def test_federal_only_years_before_1977_match_python(self):
        """No state tax is coded before 1977, so these years are federal and payroll tax only (state 0)."""
        for year in EARLY_FEDERAL_YEARS:
            with self.subTest(year=year):
                frame = _frame(0, year)
                assert_engines_match(calculate_taxes(frame, mtr=11, engine="python"), calculate_taxes(frame, mtr=11, engine="rust"))
        for year in EARLY_FEDERAL_YEARS[:4]:  # a state with an income tax has no law yet
            for engine in ("python", "rust"):
                with self.subTest(year=year, engine=engine), self.assertRaises((ValueError, RuntimeError)):
                    calculate_taxes(_frame(6, year, 20), engine=engine)

    def test_engine_choice(self):
        frame = _frame(49, 2022, 50)
        self.assertEqual(rust_backend.engine(), "python")  # the test session's default (conftest)
        with patch.dict(os.environ, {"TAXSIM_PY_ENGINE": ""}):
            os.environ.pop("TAXSIM_PY_ENGINE")
            self.assertEqual(rust_backend.engine(), "rust")  # the library's default
        with patch.dict(os.environ, {"TAXSIM_PY_ENGINE": "rust"}):
            assert_engines_match(calculate_taxes(frame, engine="python"), calculate_taxes(frame))
        for bad in ("auto", "fast"):
            with self.assertRaisesRegex(ValueError, "engine must be python or rust"):
                calculate_taxes(frame, engine=bad)

    def test_rust_raises_for_what_it_cannot_do(self):
        frame = _frame(49, 2022, 50)
        with self.assertRaisesRegex(RuntimeError, "statutory mode only"):
            calculate_taxes(frame, engine="rust", calculation_mode="taxsim")
        for options in ({"keep_intermediate": True}, {"idtl": 2}):
            with self.assertRaisesRegex(RuntimeError, "detailed output"):
                calculate_taxes(frame, engine="rust", **options)
        with self.assertRaisesRegex(RuntimeError, "no implementation"):
            calculate_taxes(frame.with_columns(year=pl.lit(1970)), engine="rust")
        with self.assertRaisesRegex(ValueError, "Unsupported marginal-rate code"):
            calculate_taxes(frame, engine="rust", mtr=99)
        with self.assertRaisesRegex(ValueError, "cannot be null"):
            calculate_taxes(frame.with_columns(mstat=pl.lit(None, dtype=pl.Int64)), engine="rust")

    def test_year_can_be_a_column(self):
        """One pass over rows from several years and states equals one pass per year."""
        columns = [*rust_backend._INPUTS, *rust_backend._OPTIONAL_INPUTS]
        parts = [
            random_households(state, year, 300, seed=SEED).with_columns(year=pl.lit(year, dtype=pl.Int64))
            for state, year in [(5, 1985), (33, 2005), (24, 2022), (0, 1990), (5, 2022), (36, 2005)]
        ]
        frame = pl.concat(parts, how="diagonal_relaxed")
        frame = frame.with_columns([pl.lit(0.0).alias(c) for c in columns if c not in frame.columns])
        frame = frame.with_columns([pl.col(c).cast(pl.Float64).fill_null(0.0) for c in columns if c != "state"])
        frame = frame.with_row_index("row")
        together = frame.select("row", rust_backend.expression("year", columns).alias("r")).unnest("r")
        separate = pl.concat(
            [
                frame.filter(pl.col("year") == year).select("row", rust_backend.expression(int(year), columns).alias("r")).unnest("r")
                for year in frame.get_column("year").unique()
            ]
        ).sort("row")
        assert_frame_equal(together, separate)
        with self.assertRaisesRegex(Exception, "non-integer"):
            frame.with_columns(year=pl.col("year") + 0.5).select(rust_backend.expression("year", columns))

    def test_lazy_input_gives_a_lazy_plan_that_matches_python_and_streams_into_any_sink(self):
        parts = [
            random_households(state, year, 150, seed=SEED).with_columns(year=pl.lit(year))
            for state, year in [(5, 1985), (33, 2005), (24, 2022), (0, 1990), (36, 2020)]
        ]
        frame = pl.concat(parts, how="diagonal_relaxed").with_row_index("taxsim_id", offset=1)
        # Nulls and a missing column are filled by the Python preparation, not by the plugin.
        frame = frame.with_columns(pl.when(pl.col("taxsim_id") % 7 == 0).then(None).otherwise(pl.col("ltcg")).alias("ltcg")).drop("dividends")
        expected = calculate_taxes(frame, engine="python")
        plan = calculate_taxes(frame.lazy(), engine="rust")
        self.assertIsInstance(plan, pl.LazyFrame)
        self.assertIsInstance(calculate_taxes(frame, engine="rust"), pl.DataFrame)
        self.assertIsInstance(calculate_taxes(frame.lazy(), engine="python"), pl.DataFrame)
        result = plan.collect()
        # A lazy plan always carries the year outputs; the eager frame only when a year has them.
        assert_engines_match(expected, result.select(expected.columns))
        self.assertEqual(result.columns[: len(frame.columns)], frame.columns)
        assert_engines_match(expected, plan.collect(engine="streaming").select(expected.columns))
        with tempfile.TemporaryDirectory() as folder:
            sinks = {
                "parquet": (plan.sink_parquet, pl.read_parquet),
                "ipc": (plan.sink_ipc, pl.read_ipc),
                "csv": (plan.sink_csv, pl.read_csv),
                "ndjson": (plan.sink_ndjson, pl.read_ndjson),
            }
            for name, (sink, read) in sinks.items():
                path = os.path.join(folder, f"out.{name}")
                sink(path)
                written = read(path)
                self.assertEqual(written.height, frame.height, name)
                for column in ("fiitax", "siitax"):
                    self.assertTrue(((written.get_column(column) - expected.get_column(column)).abs() < 1e-6).all(), f"{name} {column}")

    def test_marginal_rates_work_lazily_and_per_row(self):
        frame = pl.concat(
            [random_households(state, 2022, 300, seed=SEED) for state in (5, 33, 0)], how="diagonal_relaxed"
        ).with_row_index("taxsim_id", offset=1)
        expected = calculate_taxes(frame, mtr=85, engine="python")
        assert_engines_match(expected, calculate_taxes(frame.lazy(), mtr=85, engine="rust").collect().select(expected.columns))
        # A per-row `mtr` column overrides the argument, with 0 meaning none.
        per_row = frame.with_columns(mtr=pl.when(pl.col("taxsim_id") % 3 == 0).then(0).when(pl.col("taxsim_id") % 3 == 1).then(11).otherwise(85))
        expected = calculate_taxes(per_row, engine="python")
        found = calculate_taxes(per_row.lazy(), engine="rust").collect()
        assert_engines_match(expected, found.select(expected.columns))
        self.assertTrue((found.filter(pl.col("mtr") == 0).get_column("frate") == 0).all())


if __name__ == "__main__":
    unittest.main()
