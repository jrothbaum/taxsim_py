"""Tests for explicit TAXSIM-compatible and statutory calculation modes."""

import polars as pl
import pytest
from polars.testing import assert_frame_equal

from taxsim_py import CalculationMode, calculate_taxes
from taxsim_py.engine.inputs import TAXSIM_INPUTS


def _self_employment_cases() -> pl.DataFrame:
    return pl.DataFrame(
        [
            {"case": "single_high_se", "year": 2021, "state": 0, "mstat": 1, "psemp": 200_000},
            {"case": "wage_se_cap", "year": 2021, "state": 0, "mstat": 2, "swages": 135_000, "ssemp": 120_000},
            {"case": "below_minimum", "year": 2021, "state": 0, "mstat": 1, "psemp": 300},
            {"case": "losses", "year": 2021, "state": 0, "mstat": 2, "psemp": -5_000, "ssemp": -6_000},
        ]
    )


def test_statutory_is_the_default_calculation_mode() -> None:
    cases = _self_employment_cases()

    default = calculate_taxes(cases)
    explicit = calculate_taxes(cases, calculation_mode=CalculationMode.STATUTORY)

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


def test_iowa_eitc_uses_state_agi_for_2021_eligibility() -> None:
    """Iowa's EITC cap is tested against Iowa, not federal, AGI."""
    cases = pl.DataFrame(
        [
            {
                "year": 2021,
                "state": 16,
                "mstat": 1,
                "page": 35,
                "depx": 1,
                "dep17": 1,
                "dep18": 1,
                "pwages": 24_500,
                "ui": 7_760,
            }
        ]
    )
    result = calculate_taxes(
        cases, calculation_mode="taxsim", keep_intermediate=True
    )

    assert result.get_column("ia_agi").item() == pytest.approx(32_260.001, abs=0.01)
    # Federal AGI is higher because of the unemployment compensation; Iowa
    # AGI remains below the state EITC cap and receives the 15% credit.
    assert result.get_column("ia_earncr").item() == pytest.approx(
        0.15 * result.get_column("eitc").item(), abs=0.01
    )


def test_alabama_statutory_uses_2022_standard_deduction_changes() -> None:
    """Alabama's 2022 deduction increases are statutory-only updates."""
    cases = pl.DataFrame(
        [
            {"year": 2022, "state": 1, "mstat": 1, "pwages": 10_000},
            {"year": 2023, "state": 1, "mstat": 2, "pwages": 10_000, "swages": 5_000},
        ]
    )
    taxsim = calculate_taxes(cases, calculation_mode="taxsim", keep_intermediate=True)
    statutory = calculate_taxes(cases, calculation_mode="statutory", keep_intermediate=True)

    assert statutory.get_column("al_stded").to_list() == pytest.approx([3000.0, 8500.0])
    assert taxsim.get_column("al_stded").to_list() != pytest.approx([3000.0, 8500.0])

    # The reviewed statutory mapping also makes 2024 a real state-law year,
    # rather than sending it through the historical CPI extrapolation path.
    recent = calculate_taxes(
        pl.DataFrame([{"year": 2024, "state": 1, "mstat": 2, "pwages": 10_000, "swages": 5_000}]),
        calculation_mode="statutory",
        keep_intermediate=True,
    )
    assert recent.get_column("al_stded").item() == pytest.approx(8500.0)


def test_arkansas_statutory_uses_recent_deductions_rates_and_credits() -> None:
    cases = pl.DataFrame(
        [
            {"year": 2022, "state": 4, "mstat": 1, "pwages": 30_000},
            {"year": 2023, "state": 4, "mstat": 2, "pwages": 30_000, "swages": 10_000},
            {"year": 2024, "state": 4, "mstat": 1, "page": 70, "pwages": 30_000},
        ]
    )
    statutory = calculate_taxes(cases, calculation_mode="statutory", keep_intermediate=True)

    assert statutory.get_column("ar_stded").to_list() == pytest.approx([2270.0, 4680.0, 2410.0])
    assert statutory.get_column("ar_statutory_extra_credit").to_list() == pytest.approx([150.0, 300.0, 0.0])
    assert statutory.get_column("siitax").to_list() == pytest.approx([551.52, 391.89, 579.15], abs=0.02)


def test_new_hampshire_statutory_uses_recent_interest_dividend_rates() -> None:
    cases = pl.DataFrame(
        [
            {"year": 2022, "state": 30, "mstat": 1, "dividends": 10_000},
            {"year": 2023, "state": 30, "mstat": 1, "dividends": 10_000},
            {"year": 2024, "state": 30, "mstat": 1, "dividends": 10_000},
        ]
    )
    result = calculate_taxes(cases, calculation_mode="statutory", keep_intermediate=True)

    assert result.get_column("state_taxable_income").to_list() == pytest.approx([7600.001] * 3)
    assert result.get_column("siitax").to_list() == pytest.approx([380.0, 304.0, 228.0], abs=0.01)


def test_arizona_statutory_property_credit_excludes_social_security() -> None:
    """Arizona's senior property-credit AGI excludes gross Social Security."""
    case = pl.DataFrame(
        [
            {
                "year": 2022,
                "state": 3,
                "mstat": 1,
                "page": 67,
                "gssi": 8_000,
                "proptax": 526,
            }
        ]
    )
    taxsim = calculate_taxes(case, calculation_mode="taxsim", keep_intermediate=True)
    statutory = calculate_taxes(case, calculation_mode="statutory", keep_intermediate=True)

    assert taxsim.get_column("az_credit").item() == pytest.approx(0.0, abs=0.01)
    assert statutory.get_column("az_credit").item() == pytest.approx(502.0, abs=0.01)


def test_connecticut_statutory_personal_credit_uses_discrete_recent_rates() -> None:
    """Recent Connecticut worksheets select a rate tier at the threshold."""
    case = pl.DataFrame(
        [{"year": 2022, "state": 7, "mstat": 1, "page": 40, "pwages": 29_990}]
    )
    taxsim = calculate_taxes(case, calculation_mode="taxsim", keep_intermediate=True)
    statutory = calculate_taxes(case, calculation_mode="statutory", keep_intermediate=True)

    assert taxsim.get_column("ct_credp").item() == pytest.approx(0.17299, abs=0.0001)
    assert statutory.get_column("ct_credp").item() == pytest.approx(0.15, abs=0.0001)
    assert statutory.get_column("siitax").item() == pytest.approx(467.075, abs=0.01)


def test_dc_statutory_property_credit_uses_dated_recent_limits() -> None:
    """DC Schedule H maximums and AGI cutoffs are dated from 2022 onward."""
    cases = pl.DataFrame(
        [
            {"year": 2022, "state": 9, "mstat": 1, "pwages": 24_990, "proptax": 5_000, "rentpaid": 5_000},
            {"year": 2023, "state": 9, "mstat": 1, "pwages": 24_990, "proptax": 5_000},
            {"year": 2024, "state": 9, "mstat": 1, "pwages": 24_990, "proptax": 5_000},
        ]
    )
    result = calculate_taxes(cases, calculation_mode="statutory", keep_intermediate=True)

    assert result.get_column("dc_pcred").to_list() == pytest.approx([1250.0, 1325.0, 1375.0])


def test_ohio_statutory_uses_recent_brackets_without_changing_taxsim() -> None:
    """Ohio's recent statutory rates replace the projected 2021 schedule."""
    cases = pl.DataFrame(
        [
            {"year": 2022, "state": 36, "mstat": 1, "pwages": 30_000},
            {"year": 2023, "state": 36, "mstat": 1, "pwages": 30_000},
            {"year": 2024, "state": 36, "mstat": 1, "pwages": 30_000},
        ]
    )
    taxsim = calculate_taxes(cases.head(1), calculation_mode="taxsim")
    statutory = calculate_taxes(cases, calculation_mode="statutory")

    assert taxsim.get_column("siitax").item() == pytest.approx(384.71, abs=0.01)
    assert statutory.get_column("siitax").to_list() == pytest.approx([383.65, 383.42, 383.32], abs=0.01)


def test_oklahoma_statutory_uses_recent_rate_tables() -> None:
    """Oklahoma's 2022+ rate tables are statutory-only updates."""
    case = pl.DataFrame(
        [{"year": 2022, "state": 37, "mstat": 1, "pwages": 30_000}]
    )
    taxsim = calculate_taxes(case, calculation_mode="taxsim")
    statutory = calculate_taxes(case, calculation_mode="statutory")

    assert taxsim.get_column("siitax").item() == pytest.approx(926.69, abs=0.01)
    assert statutory.get_column("siitax").item() == pytest.approx(887.38, abs=0.01)


def test_oregon_statutory_uses_recent_brackets_and_standard_deduction() -> None:
    """Oregon's ordinary recent-year wage calculation follows dated law."""
    cases = pl.DataFrame(
        [{"year": year, "state": 38, "mstat": 1, "pwages": 30_000} for year in (2022, 2023, 2024)]
    )
    result = calculate_taxes(cases, calculation_mode="statutory")

    assert result.get_column("siitax").to_list() == pytest.approx(
        [1769.21, 1725.74, 1693.41], abs=0.01
    )


def test_oregon_statutory_uses_young_child_eitc_match() -> None:
    """An explicitly supplied child under three gets Oregon's 12% match."""
    case = pl.DataFrame(
        [
            {
                "year": 2022,
                "state": 38,
                "mstat": 1,
                "depx": 1,
                "dep18": 1,
                "age1": 2,
                "pwages": 20_000,
            }
        ]
    )
    taxsim = calculate_taxes(case, calculation_mode="taxsim", keep_intermediate=True)
    statutory = calculate_taxes(case, calculation_mode="statutory", keep_intermediate=True)

    assert taxsim.get_column("or_earncr").item() < 0.10 * taxsim.get_column("eitc").item()
    assert statutory.get_column("or_earncr").item() == pytest.approx(0.12 * statutory.get_column("eitc").item(), abs=0.01)


def test_optional_child_age_counts_are_not_taxsim_inputs() -> None:
    """Survey adapters may pass semantic child counts without expanding TAXSIM."""
    assert "children_under_3" not in TAXSIM_INPUTS
    assert "children_under_4" not in TAXSIM_INPUTS

    cases = pl.DataFrame(
        [
            {"year": 2022, "state": 38, "mstat": 1, "depx": 1, "dep18": 1, "pwages": 20_000},
            {
                "year": 2022,
                "state": 38,
                "mstat": 1,
                "depx": 1,
                "dep18": 1,
                "pwages": 20_000,
                "children_under_3": 1,
            },
        ]
    )
    result = calculate_taxes(cases, calculation_mode="statutory", keep_intermediate=True)

    assert result.get_column("or_earncr").to_list()[1] > result.get_column("or_earncr").to_list()[0]


def test_utah_statutory_uses_optional_under_four_count() -> None:
    """Utah's child credit (tax year 2024 on) uses the semantic under-four count."""
    rows = []
    for year in (2022, 2024):
        rows.append({"year": year, "state": 45, "mstat": 1, "pwages": 30_000})
        rows.append({"year": year, "state": 45, "mstat": 1, "pwages": 30_000, "children_under_4": 1})
    result = calculate_taxes(pl.DataFrame(rows), calculation_mode="statutory", keep_intermediate=True)
    tax = result.get_column("siitax").to_list()

    assert tax[1] == tax[0]  # no credit before 2024
    assert tax[3] < tax[2]


def test_dc_statutory_uses_qualifying_child_count_for_eitc() -> None:
    """An older dependent does not switch DC to its with-child EITC track."""
    cases = pl.DataFrame(
        [
            {"year": 2022, "state": 9, "mstat": 1, "depx": 1, "dep18": 0, "pwages": 15_000},
            {"year": 2022, "state": 9, "mstat": 1, "depx": 1, "dep18": 1, "pwages": 15_000},
            {"year": 2022, "state": 9, "mstat": 1, "depx": 0, "dep18": 0, "pwages": 15_000},
        ]
    )
    statutory = calculate_taxes(cases, calculation_mode="statutory", keep_intermediate=True)

    assert statutory.get_column("dc_earncr").item(0) < statutory.get_column("dc_earncr").item(1)
    assert statutory.get_column("dc_earncr").item(0) == pytest.approx(
        statutory.get_column("dc_earncr").item(2), abs=0.01
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


def test_california_statutory_uses_recent_tables_and_caps_yctc_per_return() -> None:
    cases = pl.DataFrame(
        [
            {"year": 2022, "state": 5, "mstat": 1, "pwages": 20_000, "depx": 2, "dep17": 2, "dep18": 2, "dep6": 2},
            {"year": 2023, "state": 5, "mstat": 1, "pwages": 20_000, "depx": 2, "dep17": 2, "dep18": 2, "dep6": 2},
            {"year": 2024, "state": 5, "mstat": 1, "pwages": 20_000, "depx": 2, "dep17": 2, "dep18": 2, "dep6": 2},
        ]
    )
    result = calculate_taxes(cases, calculation_mode="statutory", keep_intermediate=True)

    # Two dependents make TAXSIM's mstat=1 record head-of-household, whose
    # statutory deduction is the joint-sized amount.
    assert result.get_column("ca_stded").to_list() == pytest.approx([10404.0, 10726.0, 11080.0])
    assert result.get_column("ca_young").to_list() == pytest.approx([1083.0, 1117.0, 1154.0])


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


def test_washington_working_families_credit_only_in_statutory_mode() -> None:
    """Washington has no income tax; the refundable Working Families Tax Credit is statutory-only."""
    cases = pl.DataFrame(
        [{"year": 2023, "state": 48, "mstat": 1, "pwages": 15_000.5, "depx": 1, "dep17": 1, "dep18": 1}]
    )
    taxsim = calculate_taxes(cases, calculation_mode="taxsim").get_column("siitax").item()
    statutory = calculate_taxes(cases, calculation_mode="statutory").get_column("siitax").item()

    assert taxsim == 0
    assert 0 > statutory >= -625


def test_louisiana_2025_flat_tax_with_standard_deduction() -> None:
    """Louisiana's 2025 flat 3% on income after a $12,500 deduction (Act 11 of 2024)."""
    cases = pl.DataFrame([{"year": 2025, "state": 19, "mstat": 1, "pwages": 62_500.0}])
    tax = calculate_taxes(cases, calculation_mode="statutory").get_column("siitax").item()

    assert abs(tax - 0.03 * (62_500 - 12_500)) < 1.0


def test_state_childless_eitc_minimum_age_is_18_in_california_and_new_jersey() -> None:
    # CalEITC (R&TC 17052, 2018+) and the NJEITC (2021+) start at 18 with no
    # maximum age; the NJEITC was 21 and under 65 in 2020. TAXSIM keeps the
    # federal age test (and pays California at any age).
    cases = pl.DataFrame(
        [
            {"case": f"{state}_{year}_{age}", "state": state, "year": year, "mstat": 1, "pwages": 5_000.0, "page": age}
            for state, year in ((5, 2022), (31, 2022), (31, 2021), (31, 2020))
            for age in (15, 17, 18, 24, 25, 66)
        ]
    )

    statutory = calculate_taxes(cases, calculation_mode="statutory").with_columns(cases.get_column("case"))
    paid = {row["case"]: row["siitax"] < 0 for row in statutory.iter_rows(named=True)}
    # State, year, then the youngest age that is paid, and whether age 66 is.
    for state, year, first_paid_age, aged_paid in ((5, 2022, 18, True), (31, 2022, 18, True), (31, 2021, 18, True), (31, 2020, 24, False)):
        for age in (15, 17, 18, 24, 25, 66):
            expected = age >= first_paid_age and (aged_paid or age < 65)
            assert paid[f"{state}_{year}_{age}"] == expected, (state, year, age)

    # New Jersey pays filers who miss only the federal age test a flat amount
    # (NJ-1040 line 58: $224 for 2022, $601 for 2021) and the rest 40% of
    # the federal credit ($382.50 of phase-in at $5,000 of wages).
    amounts = {row["case"]: round(row["siitax"], 2) for row in statutory.iter_rows(named=True)}
    assert amounts["31_2022_24"] == -224.0
    assert amounts["31_2022_66"] == -224.0
    assert amounts["31_2022_25"] == pytest.approx(-153.0, abs=0.01)
    assert amounts["31_2021_18"] == -601.0

    taxsim = calculate_taxes(cases, calculation_mode="taxsim").with_columns(cases.get_column("case"))
    taxsim_paid = {row["case"]: row["siitax"] < 0 for row in taxsim.iter_rows(named=True)}
    assert taxsim_paid["5_2022_15"]
    assert not taxsim_paid["31_2022_18"]


def test_massachusetts_statutory_payroll_deduction_is_per_spouse() -> None:
    # Form 1 lines 11a/11b: each spouse deducts their own payroll tax, up to
    # $2,000 each. TAXSIM credits only the primary earner's.
    cases = pl.DataFrame(
        [
            {"state": 22, "year": 2023, "mstat": 2, "pwages": 50_000.0, "swages": 75_000.0, "page": 29, "sage": 29},
            {"state": 22, "year": 2023, "mstat": 2, "pwages": 125_000.0, "swages": 0.0, "page": 29, "sage": 29},
        ]
    )

    two_earners, one_earner = calculate_taxes(cases, calculation_mode="statutory").get_column("siitax").to_list()

    assert one_earner - two_earners == pytest.approx(100.0, abs=0.01)  # 5% of a second $2,000


def test_colorado_statutory_pension_subtraction_is_per_taxpayer_by_age() -> None:
    # DR 0104AD: $20,000 each at 55-64, $24,000 each at 65+, nothing below 55.
    # TAXSIM's cap applies only when someone is 65 or older.
    cases = pl.DataFrame(
        [
            {"mstat": 2, "page": 63, "sage": 63, "pensions": 50_000.0},
            {"mstat": 2, "page": 70, "sage": 60, "pensions": 80_000.0},
            {"mstat": 1, "page": 60, "sage": 0, "pensions": 30_000.0},
            {"mstat": 1, "page": 50, "sage": 0, "pensions": 30_000.0},
            {"mstat": 1, "page": 70, "sage": 0, "pensions": 30_000.0},
        ]
    ).with_columns(state=pl.lit(6), year=pl.lit(2024), taxsimid=pl.int_range(1, 6))

    result = calculate_taxes(cases, keep_intermediate=True).get_column("co_pension_exclusion").to_list()

    assert result == pytest.approx([40_000.0, 44_000.0, 20_000.0, 0.0, 24_000.0])
