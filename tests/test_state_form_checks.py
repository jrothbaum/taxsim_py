"""State rules checked against state forms and statutes in statutory mode.

Each case compares two records that differ in one input, so the expected
relationship follows from the cited rule without recomputing the whole state
return: `equal` (the input must not matter), `higher` or `lower` (the second
record's value against the first), or `positive`/`zero` for the first record
alone.
"""

import polars as pl
import pytest

from taxsim_py import calculate_taxes


def _record(year: int, state: int, **inputs) -> dict:
    return {"year": year, "state": state, "mstat": 1, "page": 40, **inputs}


# (description, first record, changes for the second record or None, column, relation)
CASES = [
    (
        "CO 2022 (DR 0104AD): with no pension or Social Security income, age 55-64"
        " gives a dependent filer no subtraction",
        _record(2022, 6, mstat=8, page=40, pwages=30_000),
        {"page": 58},
        "siitax",
        "equal",
    ),
    (
        "DC 2022 (D-40 EITC checklist): childless credit allowed with $10,200 of"
        " interest, under the $10,300 investment income limit",
        _record(2022, 9, page=30, pwages=10_000, intrec=10_200),
        None,
        "state_earned_income_tax_credit",
        "positive",
    ),
    (
        "DC 2023 (D-40 EITC checklist): capital gains count as investment income;"
        " $11,500 is over the $11,000 limit",
        _record(2023, 9, page=30, pwages=10_000, ltcg=11_500),
        None,
        "state_earned_income_tax_credit",
        "zero",
    ),
    (
        "IA 2023 (Iowa DOR 2023 changes): no separate filing on a combined return,"
        " so a joint return's tax does not depend on how wages split",
        _record(2023, 16, mstat=2, sage=40, pwages=70_000, swages=0),
        {"pwages": 40_000, "swages": 30_000},
        "siitax",
        "equal",
    ),
    (
        "MA 2021 (St. 2021, c. 9, s. 26): unemployment compensation excluded at"
        " household income under twice the poverty level",
        _record(2021, 22, pwages=15_000),
        {"pui": 5_000},
        "state_taxable_income",
        "equal",
    ),
    (
        "MA 2022 (St. 2021, c. 9, s. 26 covers 2020-2021 only): unemployment"
        " compensation is taxable",
        _record(2022, 22, pwages=15_000),
        {"pui": 5_000},
        "state_taxable_income",
        "higher",
    ),
    (
        "NY 2021 (Tax Law 606(c-1)): a child under 4 does not qualify for the Empire"
        " State child credit before 2023",
        _record(2021, 33, mstat=2, sage=40, pwages=30_000, depx=1, age1=5),
        {"age1": 3},
        "siitax",
        "higher",
    ),
    (
        "OR 2019 (ORS 315.264): dependent care expenses count only for a child"
        " under 13",
        _record(2019, 38, pwages=25_000, depx=1, age1=15),
        {"childcare": 5_000},
        "siitax",
        "equal",
    ),
]


@pytest.mark.parametrize("description,first,changes,column,relation", CASES, ids=[c[0][:40] for c in CASES])
def test_state_rule(description, first, changes, column, relation) -> None:
    records = [first] if changes is None else [first, {**first, **changes}]
    result = calculate_taxes(pl.DataFrame(records), calculation_mode="statutory", idtl=2, keep_intermediate=True)
    values = result.get_column(column).to_list()
    if relation == "positive":
        assert values[0] > 0, description
    elif relation == "zero":
        assert values[0] == pytest.approx(0.0, abs=0.005), description
    elif relation == "equal":
        assert values[1] == pytest.approx(values[0], abs=0.005), description
    elif relation == "higher":
        assert values[1] > values[0] + 0.005, description
    else:
        assert values[1] < values[0] - 0.005, description
