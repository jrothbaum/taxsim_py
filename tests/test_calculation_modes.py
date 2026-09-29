"""Tests for explicit TAXSIM-compatible and statutory calculation modes."""

import polars as pl
import pytest
from polars.testing import assert_frame_equal

from taxsim_py import CalculationMode, calculate_taxes


def _self_employment_cases() -> pl.DataFrame:
    return pl.DataFrame(
        [
            {"case": "single_high_se", "year": 2021, "state": 0, "mstat": 1, "psemp": 200_000},
            {"case": "wage_se_cap", "year": 2021, "state": 0, "mstat": 2, "swages": 135_000, "ssemp": 120_000},
            {"case": "below_minimum", "year": 2021, "state": 0, "mstat": 1, "psemp": 300},
            {"case": "losses", "year": 2021, "state": 0, "mstat": 2, "psemp": -5_000, "ssemp": -6_000},
        ]
    )


def test_taxsim_is_the_default_calculation_mode() -> None:
    cases = _self_employment_cases()

    default = calculate_taxes(cases)
    explicit = calculate_taxes(cases, calculation_mode=CalculationMode.TAXSIM)

    assert_frame_equal(default, explicit)


def test_statutory_mode_corrects_verified_self_employment_payroll_cases() -> None:
    result = calculate_taxes(
        _self_employment_cases(), calculation_mode=CalculationMode.STATUTORY
    )

    # These values also match PSL Tax-Calculator for the same 2021 records.
    assert result.get_column("fica").to_list() == pytest.approx(
        [23_063.50, 24_835.98, 0.0, 0.0], abs=0.01
    )
    assert result.get_column("addmed").to_list() == pytest.approx(
        [0.0, 0.0, 0.0, 0.0], abs=0.01
    )


def test_statutory_schedule_se_minimum_is_applied_per_spouse() -> None:
    cases = pl.DataFrame(
        [
            {
                "year": 2021,
                "state": 0,
                "mstat": 2,
                "pwages": 6_000,
                "psemp": 300,
                "ssemp": 100_000,
            }
        ]
    )

    result = calculate_taxes(cases, calculation_mode="statutory")

    # Joint filers submit a separate Schedule SE for each spouse. The primary
    # filer's $277.05 of net earnings remains below the individual minimum.
    assert result.get_column("fica").item() == pytest.approx(15_047.55, abs=0.01)


def test_taxsim_mode_retains_compiled_self_employment_behavior() -> None:
    result = calculate_taxes(
        _self_employment_cases(), calculation_mode=CalculationMode.TAXSIM
    )

    # Representative taxsim2024.exe results guard backward compatibility.
    assert result.get_column("fica").to_list() == pytest.approx(
        [24_588.10, 27_076.35, 42.38865, -1_554.2505], abs=0.00001
    )
    assert result.get_column("addmed").to_list() == pytest.approx(
        [1_524.60, 959.76, 0.0, 0.0], abs=0.01
    )


def test_rejects_unknown_calculation_mode() -> None:
    with pytest.raises(ValueError, match="calculation_mode.*'taxsim'.*'statutory'"):
        calculate_taxes(_self_employment_cases(), calculation_mode="corrected")
