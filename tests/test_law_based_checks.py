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
