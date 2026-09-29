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


def test_ca001_statutory_mode_stops_double_subtracting_unemployment() -> None:
    """1979-1986: total income never included taxable unemployment
    compensation, but the adjustment line subtracted it anyway. $30,000
    wages + $4,000 taxable unemployment (well above the era's income
    threshold, so it's fully taxable) should leave CA AGI at $30,000, not
    $26,000."""
    case = pl.DataFrame(
        [{"year": 1980, "state": 5, "mstat": 1, "page": 40, "pwages": 30_000, "ui": 4_000}]
    )
    taxsim = calculate_taxes(case, calculation_mode="taxsim", keep_intermediate=True)
    statutory = calculate_taxes(case, calculation_mode="statutory", keep_intermediate=True)

    assert taxsim.get_column("ca_agi").item() == pytest.approx(26_000.0, abs=0.01)
    assert statutory.get_column("ca_agi").item() == pytest.approx(30_000.0, abs=0.01)


def test_ca002_statutory_mode_keeps_business_and_rental_income_in_minimum_tax() -> None:
    """1987+: TAXSIM's minimum-tax base excludes positive self-employment
    and Schedule E (rent/S-corp) income; real law (Schedule P) keeps it.
    The documented example: a 2004 couple with $83,000 of pensions, $1,000
    of rent and $99,997 of property tax should see the rent raise their CA
    tax by 7 cents on the dollar."""
    case = pl.DataFrame(
        [
            {
                "year": 2004,
                "state": 5,
                "mstat": 2,
                "page": 50,
                "sage": 50,
                "pensions": 83_000,
                "otherprop": 1_000,
                "proptax": 99_997,
            }
        ]
    )
    taxsim = calculate_taxes(case, calculation_mode="taxsim")
    statutory = calculate_taxes(case, calculation_mode="statutory")

    assert statutory.get_column("siitax").item() - taxsim.get_column("siitax").item() == pytest.approx(
        70.0, abs=0.01
    )


def test_fed_income_001_statutory_mode_reports_the_real_pre1998_elderly_credit() -> None:
    """Before 1998, TAXSIM reports tax-before-credits (not the credit) as
    the nonrefundable-credit total when the elderly credit happens to
    exceed tax, and 0 otherwise - never the actual computed credit that
    several states (LA, UT, OR, ND, AL, AZ) read for their own federal-tax
    deduction. This doesn't change `fiitax`, which the elderly credit
    still doesn't reduce pre-1998 in either mode - only what gets reported
    to states as the credit total."""
    cases = pl.DataFrame(
        [
            # federal_elder ($712.50) > tax_before_credits ($97.50): TAXSIM
            # reports the tax, not the credit.
            {"case": "elder_exceeds_tax", "year": 1995, "state": 0, "mstat": 1, "page": 68, "pwages": 8_000},
            # federal_elder ($412.50) < tax_before_credits ($697.50): TAXSIM
            # reports 0 even though a real credit applies.
            {"case": "elder_below_tax", "year": 1995, "state": 0, "mstat": 1, "page": 68, "pwages": 12_000},
        ]
    )
    taxsim = calculate_taxes(cases, calculation_mode="taxsim", keep_intermediate=True)
    statutory = calculate_taxes(cases, calculation_mode="statutory", keep_intermediate=True)

    assert taxsim.get_column("nonrefundable_credits").to_list() == pytest.approx(
        [97.50015, 0.0], abs=0.01
    )
    assert statutory.get_column("nonrefundable_credits").to_list() == pytest.approx(
        taxsim.get_column("federal_elder").to_list(), abs=0.01
    )
    # `fiitax` is unaffected in both modes - only the reported total changes.
    assert taxsim.get_column("fiitax").to_list() == pytest.approx(
        statutory.get_column("fiitax").to_list(), abs=0.01
    )
