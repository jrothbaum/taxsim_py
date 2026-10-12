"""Small regression checks for values audited against the current TAXSIM source."""

import polars as pl

from taxsim_py import calculate_taxes
from taxsim_py.behavior import STATUTORY_BEHAVIOR
from taxsim_py.engine.schema import PARAMETERS_ROOT, YearParams, load_yaml
from taxsim_py.engine.state_extrapolation import resolve_state_year


def _state_params(state: str, year: int) -> YearParams:
    return YearParams(load_yaml(PARAMETERS_ROOT / "states" / state / "income_tax.yaml"), year)


def test_ohio_2021_exemptions_match_ohtax21_source() -> None:
    """The newer NBER ``ohtax21`` routine supplies these three values."""
    p = _state_params("oh", 2021)
    assert p.num("exemption") == 1900.0
    assert p.num("exemption_low_income") == 2400.0
    assert p.num("exemption_middle_income") == 2150.0


def test_policyengine_flat_rate_imports_are_actual_year_only() -> None:
    """Statutory mode uses imported years; TAXSIM mode keeps projection rules."""
    for state in ("co", "il", "mi", "pa", "ut"):
        params = load_yaml(PARAMETERS_ROOT / "states" / state / "income_tax.yaml")
        assert resolve_state_year(2024, STATUTORY_BEHAVIOR, params) == (2024, 1.0)

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
    recent = {y: _state_params("ut", y) for y in (2022, 2023, 2024)}
    assert {y: p.num("flat_rate") for y, p in recent.items()} == {2022: 0.0485, 2023: 0.0465, 2024: 0.0455}
    assert {y: p.num("earned_income_credit_rate") for y, p in recent.items()} == {2022: 0.15, 2023: 0.20, 2024: 0.20}
    assert recent[2024].value("child_tax_credit")["amount"] == 1000
    assert {y: p.num("taxpayer_credit_personal_exemption") for y, p in recent.items()} == {
        2022: 1802,
        2023: 1941,
        2024: 2046,
    }
