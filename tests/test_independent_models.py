"""Small independent-model checks for taxsim_py.

Run with ``uv run --group test pytest tests/test_independent_models.py -q``.
"""

import polars as pl
import pytest
from polars.testing import assert_frame_equal

from taxsim_py import calculate_taxes
from taxsim_py.validation.independent import run_policyengine, run_taxcalc


def _wage_cases(years: tuple[int, ...]) -> pl.DataFrame:
    rows = []
    case_id = 1
    for year in years:
        for marital_status, primary_wages, spouse_wages in (
            (1, 20_000, 0),
            (1, 50_000, 0),
            (1, 125_000, 0),
            (2, 60_000, 20_000),
        ):
            rows.append(
                {
                    "taxsimid": case_id,
                    "year": year,
                    "state": 0,
                    "mstat": marital_status,
                    "page": 40,
                    "sage": 40 if marital_status == 2 else 0,
                    "depx": 0,
                    "dep17": 0,
                    "dep18": 0,
                    "pwages": primary_wages,
                    "swages": spouse_wages,
                }
            )
            case_id += 1
    return pl.DataFrame(rows)


def test_taxcalc_matches_wage_only_cases_across_supported_history() -> None:
    cases = _wage_cases((2013, 2017, 2018, 2020, 2021, 2023))

    ours = calculate_taxes(cases, calculation_mode="statutory").select("taxsimid", "year", "fiitax", "fica")
    independent = run_taxcalc(cases)

    assert_frame_equal(ours, independent, check_dtypes=False, abs_tol=0.01)


def test_taxcalc_matches_households_with_children() -> None:
    cases = pl.DataFrame(
        [
            {
                "taxsimid": index,
                "year": year,
                "state": 0,
                "mstat": marital_status,
                "page": 40,
                "sage": 40 if marital_status == 2 else 0,
                "depx": dependents,
                "dep6": young_children,
                "dep17": dependents,
                "dep18": dependents,
                "pwages": primary_wages,
                "swages": spouse_wages,
            }
            for index, (
                year,
                marital_status,
                dependents,
                young_children,
                primary_wages,
                spouse_wages,
            ) in enumerate(
                (
                    (2013, 1, 1, 1, 30_000, 0),
                    (2018, 2, 2, 1, 45_000, 20_000),
                    (2021, 1, 2, 2, 15_000, 0),
                    (2023, 2, 1, 0, 70_000, 30_000),
                ),
                start=1,
            )
        ]
    )

    ours = calculate_taxes(cases, calculation_mode="statutory").select("taxsimid", "year", "fiitax", "fica")
    independent = run_taxcalc(cases)

    assert_frame_equal(ours, independent, check_dtypes=False, abs_tol=0.01)


def test_statutory_payroll_matches_taxcalc_self_employment_edge_cases() -> None:
    cases = pl.DataFrame(
        [
            {"taxsimid": 1, "year": 2021, "state": 0, "mstat": 1, "page": 40, "sage": 0,
             "depx": 0, "dep17": 0, "dep18": 0, "pwages": 0, "swages": 0, "psemp": 200_000},
            {"taxsimid": 2, "year": 2021, "state": 0, "mstat": 2, "page": 40, "sage": 40,
             "depx": 0, "dep17": 0, "dep18": 0, "pwages": 0, "swages": 135_000, "ssemp": 120_000},
            {"taxsimid": 3, "year": 2021, "state": 0, "mstat": 1, "page": 40, "sage": 0,
             "depx": 0, "dep17": 0, "dep18": 0, "pwages": 0, "swages": 0, "psemp": 300},
            {"taxsimid": 4, "year": 2021, "state": 0, "mstat": 2, "page": 40, "sage": 40,
             "depx": 0, "dep17": 0, "dep18": 0, "pwages": 0, "swages": 0,
             "psemp": -5_000, "ssemp": -6_000},
        ]
    )

    ours = calculate_taxes(cases, calculation_mode="statutory").select(
        "taxsimid", "year", "fica"
    )
    independent = run_taxcalc(cases).select("taxsimid", "year", "fica")

    assert_frame_equal(ours, independent, check_dtypes=False, abs_tol=0.01)


@pytest.mark.xfail(
    strict=True,
    reason="2018 childless EITC differs from Tax-Calculator by $1.53",
)
def test_taxcalc_matches_2018_childless_eitc() -> None:
    cases = _wage_cases((2018,)).head(1).with_columns(pwages=pl.lit(12_000))

    ours = calculate_taxes(cases, calculation_mode="statutory").select("taxsimid", "year", "fiitax", "fica")
    independent = run_taxcalc(cases)

    assert_frame_equal(ours, independent, check_dtypes=False, abs_tol=0.01)


@pytest.fixture(scope="module")
def policyengine_comparison() -> tuple[pl.DataFrame, pl.DataFrame]:
    cases = pl.DataFrame(
        [
            {
                "taxsimid": index,
                "year": year,
                "state": state,
                "mstat": marital_status,
                "page": 40,
                "sage": 40 if marital_status == 2 else 0,
                "depx": len(dependent_ages),
                "dep6": sum(age < 6 for age in dependent_ages),
                "dep17": sum(age < 17 for age in dependent_ages),
                "dep18": sum(age < 19 for age in dependent_ages),
                "age1": dependent_ages[0] if len(dependent_ages) > 0 else 0,
                "age2": dependent_ages[1] if len(dependent_ages) > 1 else 0,
                "age3": dependent_ages[2] if len(dependent_ages) > 2 else 0,
                "pwages": primary_wages,
                "swages": spouse_wages,
            }
            for index, (
                year,
                state,
                marital_status,
                primary_wages,
                spouse_wages,
                dependent_ages,
            ) in enumerate(
                (
                    (2021, 0, 1, 50_000, 0, ()),
                    (2022, 5, 1, 80_000, 0, ()),
                    (2023, 33, 2, 80_000, 35_000, ()),
                    (2023, 24, 1, 125_000, 0, ()),
                    (2021, 0, 1, 15_000, 0, (4, 8)),
                    (2023, 0, 2, 70_000, 30_000, (10,)),
                ),
                start=1,
            )
        ]
    )

    ours = calculate_taxes(cases, calculation_mode="statutory").select(
        "taxsimid", "year", "state", "fiitax", "siitax", "fica"
    )
    independent = run_policyengine(cases)

    return ours, independent


def test_policyengine_taxsim_api_matches_recent_federal_and_payroll_tax(
    policyengine_comparison: tuple[pl.DataFrame, pl.DataFrame],
) -> None:
    ours, independent = policyengine_comparison

    assert_frame_equal(
        ours.select("taxsimid", "year", "state", "fiitax", "fica"),
        independent.select("taxsimid", "year", "state", "fiitax", "fica"),
        check_dtypes=False,
        abs_tol=0.01,
    )


def test_policyengine_taxsim_api_matches_recent_state_tax(
    policyengine_comparison: tuple[pl.DataFrame, pl.DataFrame],
) -> None:
    ours, independent = policyengine_comparison

    assert_frame_equal(
        ours.select("taxsimid", "year", "state", "siitax"),
        independent.select("taxsimid", "year", "state", "siitax"),
        check_dtypes=False,
        abs_tol=0.01,
    )
