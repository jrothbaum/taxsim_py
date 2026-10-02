"""Recent statutory Hawaii provisions not present in the TAXSIM-era tables."""

import polars as pl
import pytest

from taxsim_py import calculate_taxes


def test_hawaii_2024_standard_deduction_is_doubled_statutorily() -> None:
    cases = pl.DataFrame(
        [
            {"year": 2023, "state": 12, "mstat": 1, "pwages": 5_000},
            {"year": 2024, "state": 12, "mstat": 1, "pwages": 5_000},
        ]
    )
    result = calculate_taxes(cases, calculation_mode="statutory")

    taxes = result.get_column("siitax").to_list()
    assert taxes[1] < taxes[0]
    assert taxes == pytest.approx([-349.815986, -373.0], abs=0.00001)


def test_hawaii_eitc_becomes_refundable_in_2023() -> None:
    cases = pl.DataFrame(
        [
            {"year": 2022, "state": 12, "mstat": 1, "pwages": 10_000, "depx": 1, "dep17": 1, "dep18": 1},
            {"year": 2023, "state": 12, "mstat": 1, "pwages": 10_000, "depx": 1, "dep17": 1, "dep18": 1},
        ]
    )
    result = calculate_taxes(cases, calculation_mode="statutory")

    # The 2023 credit is 40% of federal EITC and refundable; 2022 remains the
    # 20% nonrefundable rule used by the historical TAXSIM implementation.
    assert result.get_column("siitax").to_list()[1] < result.get_column("siitax").to_list()[0]
