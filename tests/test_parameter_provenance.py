"""Small regression checks for values audited against the current TAXSIM source."""

from pathlib import Path

import polars as pl
import yaml

from taxsim_py import calculate_taxes
from taxsim_py.engine.state_extrapolation import resolve_state_year


ROOT = Path(__file__).resolve().parents[1]


def test_ohio_2021_exemptions_match_ohtax21_source() -> None:
    """The newer NBER ``ohtax21`` routine supplies these three values."""
    with (ROOT / "parameters" / "states" / "oh" / "income_tax.yaml").open() as stream:
        params = yaml.safe_load(stream)

    assert params["exemption"][2021] == 1900.0
    assert params["exemption_low_income"][2021] == 2400.0
    assert params["exemption_middle_income"][2021] == 2150.0


def test_policyengine_flat_rate_imports_are_actual_year_only() -> None:
    """Statutory mode uses imported years; TAXSIM mode keeps projection rules."""
    for state in (6, 14, 23, 39, 45):
        assert resolve_state_year(2024, {6: "co", 14: "il", 23: "mi", 39: "pa", 45: "ut"}[state]) == (2024, 1.0)

    cases = pl.DataFrame(
        {
            "state": [6, 14, 23, 39, 45],
            "mstat": [1] * 5,
            "pwages": [50_000.0] * 5,
            "depx": [0] * 5,
        }
    )
    statutory = calculate_taxes(cases, year=2024, calculation_mode="statutory")
    assert statutory.get_column("siitax").len() == 5


def test_utah_recent_policyengine_parameters_are_loaded() -> None:
    with (ROOT / "parameters" / "states" / "ut" / "income_tax.yaml").open() as stream:
        params = yaml.safe_load(stream)

    assert {year: params["flat_rate"][year] for year in (2022, 2023, 2024)} == {
        2022: 0.0485,
        2023: 0.0465,
        2024: 0.0455,
    }
    recent = (2022, 2023, 2024)
    assert {y: params["earned_income_credit_rate"][y] for y in recent} == {2022: 0.15, 2023: 0.20, 2024: 0.20}
    assert {y: params["child_tax_credit_amount"][y] for y in recent} == {2022: 1000, 2023: 1000, 2024: 1000}
    assert {y: params["taxpayer_credit_personal_exemption"][y] for y in recent} == {2022: 1802, 2023: 1941, 2024: 2046}
