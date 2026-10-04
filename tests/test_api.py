"""Tests for the mixed-state public dataframe API."""

import unittest
from unittest.mock import patch

import polars as pl
from polars.testing import assert_frame_equal

from taxsim_py import MarginalInput, calculate_taxes
from taxsim_py.api import OUTPUT_COLUMNS, _default_year_workers
from taxsim_py.engine.detail import (
    FEDERAL_DETAIL_COLUMNS,
    STATE_DETAIL_COLUMNS,
    TAXSIM_FEDERAL_DETAIL_COLUMNS,
    TAXSIM_STATE_DETAIL_COLUMNS,
)


def _case(case_id: int, year: int, state: int) -> dict[str, int]:
    return {
        "case_id": case_id,
        "year": year,
        "state": state,
        "mstat": 1,
        "depx": 0,
        "dep17": 0,
        "dep18": 0,
        "dep6": 0,
        "pwages": 50_000,
        "swages": 0,
    }


class CalculateTaxesTests(unittest.TestCase):
    def test_dispatches_mixed_taxsim_states_and_preserves_order(self) -> None:
        cases = pl.DataFrame([_case(2, 2020, 5), _case(1, 2020, 1)])

        result = calculate_taxes(cases.lazy())

        self.assertEqual(result.get_column("case_id").to_list(), [2, 1])
        self.assertEqual(result.get_column("state").to_list(), [5, 1])
        self.assertTrue({"fiitax", "siitax", "fica"}.issubset(result.columns))

    def test_converts_fips_and_partitions_years(self) -> None:
        cases = pl.DataFrame([_case(1, 2019, 6), _case(2, 2020, 1)])

        result = calculate_taxes(cases, state_id_type="fips")
        expected = pl.concat(
            [
                calculate_taxes(cases.head(1), year=2019, state_id_type="fips"),
                calculate_taxes(cases.tail(1), year=2020, state_id_type="fips"),
            ],
            how="diagonal_relaxed",
        )

        self.assertEqual(result.get_column("case_id").to_list(), [1, 2])
        self.assertEqual(result.get_column("state").to_list(), [6, 1])
        self.assertEqual(result.get_column("year").to_list(), [2019, 2020])
        assert_frame_equal(result, expected)

        single_worker = calculate_taxes(
            cases, state_id_type="fips", max_year_workers=1
        )
        assert_frame_equal(result, single_worker)

    def test_rejects_invalid_year_worker_limit(self) -> None:
        cases = pl.DataFrame([_case(1, 2020, 1)])

        with self.assertRaisesRegex(ValueError, "max_year_workers"):
            calculate_taxes(cases, max_year_workers=0)

    def test_default_year_workers_follow_polars_thread_pool(self) -> None:
        self.assertEqual(_default_year_workers(), pl.thread_pool_size())

    def test_single_year_row_partitions_match_serial_result(self) -> None:
        cases = pl.DataFrame([_case(i, 2020, state) for i, state in enumerate((1, 5, 33, 36))])
        serial = calculate_taxes(cases, max_year_workers=1)

        with patch("taxsim_py.api._BATCH_ROWS", 1):
            parallel = calculate_taxes(cases, max_year_workers=2)

        assert_frame_equal(parallel, serial)

    def test_no_state_and_no_income_tax_states(self) -> None:
        # taxsim2024.exe: 2020 single, $50,000 of wages -> fiitax 2514.50, siitax 0.
        cases = pl.DataFrame([_case(1, 2020, 0), _case(2, 2020, 44), _case(3, 2020, 10)])

        result = calculate_taxes(cases)

        self.assertEqual(result.get_column("siitax").to_list(), [0.0, 0.0, 0.0])
        for fiitax in result.get_column("fiitax").to_list():
            self.assertAlmostEqual(fiitax, 2514.50, places=2)

    def test_missing_inputs_default_to_zero(self) -> None:
        full = pl.DataFrame([_case(1, 2020, 5)])
        sparse = full.select("case_id", "year", "state", "mstat", "pwages")

        outputs = list(OUTPUT_COLUMNS)
        assert_frame_equal(calculate_taxes(sparse).select(outputs), calculate_taxes(full).select(outputs))

    def test_null_inputs_count_as_zero(self) -> None:
        full = pl.DataFrame([_case(1, 2020, 5), _case(2, 2020, 5)])
        with_nulls = full.with_columns(swages=pl.Series([None, 0], dtype=pl.Int64))

        outputs = list(OUTPUT_COLUMNS)
        assert_frame_equal(calculate_taxes(with_nulls).select(outputs), calculate_taxes(full).select(outputs))

    def test_scalar_marginal_code_matches_input_column(self) -> None:
        cases = pl.DataFrame([_case(1, 2020, 5)])

        scalar = calculate_taxes(cases, mtr=85).select("frate", "srate")
        named = calculate_taxes(
            cases, mtr=MarginalInput.PRIMARY_WAGES
        ).select("frate", "srate")
        column = calculate_taxes(cases.with_columns(mtr=pl.lit(85))).select(
            "frate", "srate"
        )

        assert_frame_equal(scalar, named)
        assert_frame_equal(scalar, column)

    def test_rejects_unknown_scalar_marginal_code(self) -> None:
        cases = pl.DataFrame([_case(1, 2020, 5)])

        with self.assertRaisesRegex(ValueError, "Unsupported marginal-rate code.*999"):
            calculate_taxes(cases, mtr=999)

    def test_returns_inputs_unchanged_plus_outputs(self) -> None:
        cases = pl.DataFrame([_case(1, 2020, 5)]).with_columns(pl.col("depx").cast(pl.Float64))

        result = calculate_taxes(cases)

        self.assertEqual(result.columns, [*cases.columns, *OUTPUT_COLUMNS])
        assert_frame_equal(result.select(cases.columns), cases)
        detailed = calculate_taxes(cases, keep_intermediate=True)
        self.assertGreater(len(detailed.columns), len(result.columns))
        assert_frame_equal(detailed.select(result.columns), result)

    def test_keeps_callers_state_column_when_ids_are_elsewhere(self) -> None:
        cases = pl.DataFrame([_case(1, 2020, 6)]).rename({"state": "fips"}).with_columns(state=pl.lit("CA"))

        result = calculate_taxes(cases, state_column="fips", state_id_type="fips")

        self.assertEqual(result.get_column("state").to_list(), ["CA"])
        self.assertEqual(result.get_column("fips").to_list(), [6])

    def test_rejects_null_years(self) -> None:
        cases = pl.DataFrame([_case(1, 2020, 1)]).with_columns(year=pl.lit(None, dtype=pl.Int64))

        with self.assertRaisesRegex(ValueError, "Years cannot be null"):
            calculate_taxes(cases)

    def test_rejects_unknown_state(self) -> None:
        cases = pl.DataFrame([_case(1, 2020, 52)])

        with self.assertRaisesRegex(NotImplementedError, "52"):
            calculate_taxes(cases)

    def test_detail_outputs_cover_both_federal_eras(self) -> None:
        cases = pl.DataFrame([_case(1, 1985, 0), _case(2, 2019, 0)])

        result = calculate_taxes(cases, idtl=2)

        self.assertTrue(set(FEDERAL_DETAIL_COLUMNS).issubset(result.columns))
        self.assertTrue(set(STATE_DETAIL_COLUMNS).issubset(result.columns))
        self.assertEqual(
            result.get_column("federal_adjusted_gross_income").to_list(),
            result.get_column("agi").to_list(),
        )
        self.assertEqual(sum(result.select(FEDERAL_DETAIL_COLUMNS).null_count().row(0)), 0)
        self.assertFalse(set(FEDERAL_DETAIL_COLUMNS) & set(calculate_taxes(cases).columns))
        self.assertNotIn("v10", result.columns)

        compatible = calculate_taxes(cases, idtl=2, taxsim_names=True)
        self.assertTrue(set(TAXSIM_FEDERAL_DETAIL_COLUMNS).issubset(compatible.columns))
        self.assertTrue(set(TAXSIM_STATE_DETAIL_COLUMNS).issubset(compatible.columns))
        self.assertEqual(
            compatible.get_column("v10").to_list(),
            result.get_column("federal_adjusted_gross_income").to_list(),
        )
        self.assertFalse(set(FEDERAL_DETAIL_COLUMNS) & set(compatible.columns))

    def test_rejects_unsupported_detail_level(self) -> None:
        cases = pl.DataFrame([_case(1, 2019, 0)])

        with self.assertRaisesRegex(ValueError, "idtl"):
            calculate_taxes(cases, idtl=5)

    def test_dependent_filer_has_no_exemption(self) -> None:
        cases = pl.DataFrame([{**_case(1, 2015, 0), "mstat": 8}, _case(2, 2015, 0)])

        result = calculate_taxes(cases, idtl=2)

        exemptions = result.get_column("federal_personal_exemptions").to_list()
        self.assertEqual(exemptions[0], 0.0)
        self.assertGreater(exemptions[1], 0.0)

    def test_child_ages_apply_only_to_rows_that_give_them(self) -> None:
        # Ages 4 and 20: one child under 6, 13, 17 and 19.
        with_ages = {**_case(1, 2019, 0), "depx": 2, "age1": 4, "age2": 20, "pwages": 20_000}
        counts = {**_case(2, 2019, 0), "depx": 2, "dep6": 1, "dep13": 1, "dep17": 1, "dep18": 1, "pwages": 20_000}
        all_children = {**_case(3, 2019, 0), "depx": 2, "dep17": 2, "dep18": 2, "pwages": 20_000}
        cases = pl.DataFrame([with_ages, counts, all_children], infer_schema_length=None)

        fiitax = calculate_taxes(cases).get_column("fiitax").to_list()

        self.assertAlmostEqual(fiitax[0], fiitax[1])
        self.assertNotAlmostEqual(fiitax[0], fiitax[2])

    def test_rejects_unsupported_filing_status(self) -> None:
        cases = pl.DataFrame([{**_case(1, 2019, 0), "mstat": 5}])

        with self.assertRaisesRegex(ValueError, "mstat"):
            calculate_taxes(cases)


if __name__ == "__main__":
    unittest.main()
