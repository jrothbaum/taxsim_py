"""Federal outputs checked directly against published law, not TAXSIM or an
independent tax calculator. TAXSIM and PolicyEngine can share a bug; this
file exists to catch one that both of them would miss.

Every expected value below is cited to a primary or IRS-sourced reference
directly in the case table, mostly IRS Rev. Proc. 2023-34 (TY2024) and Rev.
Proc. 2024-40 (TY2025), cross-checked against PolicyEngine-US's own sourced
parameters (``policyengine_us/parameters/gov/irs/...``). Cases use
``calculation_mode="statutory"``: TAXSIM-compatibility mode is not expected
to match law where a documented TAXSIM bug applies (see
``docs/statutory_corrections.md``).

Run with ``uv run --group test pytest tests/test_law_based_checks.py -q``.
"""

import polars as pl
import pytest

from taxsim_py import calculate_taxes


def _wage_case(year: int, mstat: int, wages: float, **extra) -> dict:
    return {
        "year": year,
        "state": 0,
        "mstat": mstat,
        "page": 40,
        "sage": 40 if mstat == 2 else 0,
        "pwages": wages,
        **extra,
    }


# (description, case, output column, expected value) - each expected value
# traces to a specific statute or published IRS/SSA figure, independent of
# both TAXSIM and the port's own parameter files.
CASES = [
    (
        "2022 single standard deduction ($12,950, Rev. Proc. 2021-45 p.4):"
        " wages at exactly the deduction leave zero taxable income",
        _wage_case(2022, 1, 12_950),
        "taxable_income",
        0.0,
    ),
    (
        "2023 single standard deduction ($13,850, Rev. Proc. 2022-38 p.4):"
        " wages at exactly the deduction leave zero taxable income",
        _wage_case(2023, 1, 13_850),
        "taxable_income",
        0.0,
    ),
    (
        "2022 single 10% bracket top ($10,275, Rev. Proc. 2021-45 p.4)",
        _wage_case(2022, 1, 12_950 + 10_275),
        "fiitax",
        1_027.50,
    ),
    (
        "2023 single 10% bracket top ($11,000, Rev. Proc. 2022-38 p.4)",
        _wage_case(2023, 1, 13_850 + 11_000),
        "fiitax",
        1_100.0,
    ),
    (
        "2024 single standard deduction ($14,600, Rev. Proc. 2023-34 p.14):"
        " wages at exactly the deduction leave zero taxable income",
        _wage_case(2024, 1, 14_600),
        "taxable_income",
        0.0,
    ),
    (
        "2025 single standard deduction ($15,750, OBBBA H.R.1 2025-07-04,"
        " not a plain inflation step from 2024): wages at exactly the"
        " deduction leave zero taxable income",
        _wage_case(2025, 1, 15_750),
        "taxable_income",
        0.0,
    ),
    (
        "2024 single 10% bracket top ($11,600, Rev. Proc. 2023-34 p.5):"
        " taxable income exactly there owes exactly 10% of it (wages set"
        " well above the childless-EITC phase-out ceiling so the credit"
        " doesn't confound the tax-before-credits check)",
        _wage_case(2024, 1, 14_600 + 11_600),
        "fiitax",
        1_160.0,
    ),
    (
        "2024 married-joint standard deduction ($29,200, Rev. Proc. 2023-34"
        " p.14)",
        _wage_case(2024, 2, 29_200),
        "taxable_income",
        0.0,
    ),
]


@pytest.mark.parametrize(
    "description,case,column,expected",
    CASES,
    ids=[c[0] for c in CASES],
)
def test_against_published_law(description, case, column, expected) -> None:
    result = calculate_taxes(
        pl.DataFrame([case]), calculation_mode="statutory", keep_intermediate=True
    )
    assert result.get_column(column).item() == pytest.approx(expected, abs=0.01), description


def test_2024_oasdi_wage_base_caps_payroll_tax() -> None:
    """$168,600 (SSA Contribution and Benefit Base, ssa.gov/oact/cola/cbb.html)."""
    cases = pl.DataFrame(
        [
            _wage_case(2024, 1, 168_600),
            _wage_case(2024, 1, 168_601),
        ]
    )
    result = calculate_taxes(cases, calculation_mode="statutory")
    fica = result.get_column("fica").to_list()
    # The next dollar of wages only owes the combined 2.9% Medicare rate,
    # since OASDI (12.4% combined) is fully capped by $168,600.
    assert fica[1] - fica[0] == pytest.approx(0.029, abs=0.0005)


@pytest.mark.parametrize(
    "year,wage_base",
    [(2022, 147_000), (2023, 160_200)],
)
def test_recent_oasdi_wage_bases_cap_payroll_tax(year: int, wage_base: int) -> None:
    """SSA Contribution and Benefit Base for TY2022 and TY2023."""
    cases = pl.DataFrame(
        [
            _wage_case(year, 1, wage_base),
            _wage_case(year, 1, wage_base + 1),
        ]
    )
    result = calculate_taxes(cases, calculation_mode="statutory")
    fica = result.get_column("fica").to_list()
    assert fica[1] - fica[0] == pytest.approx(0.029, abs=0.0005)


@pytest.mark.parametrize(
    "year,expected",
    [(2022, 4_800.0), (2023, 4_800.0), (2024, 4_800.0)],
)
def test_recent_eitc_two_children_matches_published_phase_in(year: int, expected: float) -> None:
    """Two qualifying children at $12,000 earned income are in the 40% phase-in."""
    case = _wage_case(year, 1, 12_000, page=30, depx=2, dep17=2, dep18=2, age1=4, age2=8)
    result = calculate_taxes(
        pl.DataFrame([case]), calculation_mode="statutory", keep_intermediate=True
    )
    assert result.get_column("eitc").item() == pytest.approx(expected, abs=0.01)


def test_2025_oasdi_wage_base_caps_payroll_tax() -> None:
    """$176,100 (SSA Contribution and Benefit Base, ssa.gov/oact/cola/cbb.html)."""
    cases = pl.DataFrame(
        [
            _wage_case(2025, 1, 176_100),
            _wage_case(2025, 1, 176_101),
        ]
    )
    result = calculate_taxes(cases, calculation_mode="statutory")
    fica = result.get_column("fica").to_list()
    assert fica[1] - fica[0] == pytest.approx(0.029, abs=0.0005)


def test_2024_eitc_two_children_matches_published_phase_in() -> None:
    """40% phase-in rate, Rev. Proc. 2023-34 p.10 / 26 U.S.C. Sec 32(b): a
    single parent with $12,000 of earned income, still in the phase-in
    range (completes at $17,400 = $6,960 max credit / 40%), gets exactly
    40% of it."""
    case = _wage_case(
        2024, 1, 12_000, page=30, depx=2, dep17=2, dep18=2, age1=4, age2=8
    )
    result = calculate_taxes(
        pl.DataFrame([case]), calculation_mode="statutory", keep_intermediate=True
    )
    assert result.get_column("eitc").item() == pytest.approx(0.40 * 12_000, abs=0.01)


def test_2025_additional_medicare_threshold_unchanged_by_statute() -> None:
    """$200,000 single (26 U.S.C. Sec 3101(b)(2)): statutorily fixed, never
    inflation-indexed, since its 2013 inception - confirm it still applies
    at exactly that dollar threshold in 2025."""
    cases = pl.DataFrame(
        [
            _wage_case(2025, 1, 200_000),
            _wage_case(2025, 1, 200_001),
        ]
    )
    result = calculate_taxes(cases, calculation_mode="statutory", keep_intermediate=True)
    addmed = result.get_column("addmed").to_list()
    assert addmed[0] == pytest.approx(0.0, abs=0.01)
    assert addmed[1] == pytest.approx(0.009, abs=0.001)


@pytest.mark.parametrize("year", [2022, 2023, 2024, 2025])
def test_other_dependent_credit_applies_after_2021(year: int) -> None:
    """IRC 24(h)(4): $500 nonrefundable credit per dependent who is not a
    CTC-qualifying child. TAXSIM's source computes it for 2022+ but drops it
    from the credit total (see FED-INCOME-002)."""
    base = _wage_case(year, 2, 90_000)
    cases = pl.DataFrame([base, {**base, "depx": 1, "dep17": 0, "dep18": 0}])
    statutory = calculate_taxes(cases, calculation_mode="statutory").get_column("fiitax")
    compat = calculate_taxes(cases, calculation_mode="taxsim").get_column("fiitax")
    assert statutory[0] - statutory[1] == pytest.approx(500.0, abs=0.01)
    assert compat[0] - compat[1] == pytest.approx(0.0, abs=0.01)


def test_california_yctc_needs_a_young_child() -> None:
    """R&TC 17052.1: the Young Child Tax Credit needs a child under six.
    TAXSIM pays it above the earnings threshold without that test
    (FED/CA-004 in docs/statutory_corrections.md)."""
    base = {**_wage_case(2019, 1, 26_000), "state": 5, "depx": 1, "dep17": 1, "dep18": 1, "dep13": 1}
    older_child = pl.DataFrame([base])
    statutory = calculate_taxes(older_child, calculation_mode="statutory").get_column("siitax").item()
    compat = calculate_taxes(older_child, calculation_mode="taxsim").get_column("siitax").item()
    assert compat < statutory


def test_2025_federal_obbba_provisions() -> None:
    """2025: $2,200 child credit, $6,000 senior deduction, and the SALT cap that phases down above $500,000."""
    base = {"year": 2025, "state": 44, "mstat": 1}
    cases = pl.DataFrame(
        [
            {**base, "pwages": 40_000.5, "depx": 1, "dep17": 1, "dep18": 1},
            {**base, "pwages": 40_000.5, "depx": 0},
            {**base, "pwages": 40_000.5, "depx": 0, "page": 70},
            {**base, "pwages": 600_000.5, "depx": 0, "proptax": 50_000.0, "mortgage": 30_000.0},
        ]
    )
    result = calculate_taxes(cases, calculation_mode="statutory", keep_intermediate=True)
    no_child_tax = result["fiitax"][1]

    # Child credit: the 2025 amount is $2,200 per child (2024: $2,000).
    assert result["actc"][0] + result["odc"][0] > 2_000
    # Senior deduction: $6,000 less 6% of income over $75,000, at the 12% bracket here.
    assert abs((no_child_tax - result["fiitax"][2]) - 0.12 * 6_000 - 0.12 * 2_000) < 5
    # SALT cap: $40,000 less 30% of AGI over $500,000, but never below $10,000.
    assert result["salt_capped"][3] == 10_000.0
