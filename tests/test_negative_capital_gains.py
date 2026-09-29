"""Regression cases for five states that previously mishandled a negative
net capital gain: AL, CA, CT, IL, HI (see statutory_corrections.md's
AL-001/CA-003/CT-001/IL-001/HI-001). Unlike that file's other entries,
these are not documented TAXSIM bugs to preserve for compatibility - the
real oracle already computes them correctly, so the fixes are
unconditional (`calculate_taxes` needs no `calculation_mode` argument to
get them). Each `expected_siitax` is the real `taxsim2024.exe` output.

Run with ``uv run --group test pytest tests/test_negative_capital_gains.py -q``.
"""

import polars as pl
import pytest

from taxsim_py import calculate_taxes

_STATES = {"AL": 1, "CA": 5, "CT": 7, "IL": 14, "HI": 12}

# One row per (state, case): the three shared negative-capital-gains
# scenarios from tests/state_new_inputs.py, with each state's real
# taxsim2024.exe result for 1980.
CASES = [
    ("AL", "couple_loss", {"mstat": 2, "pwages": 50_000, "swages": 30_000, "ltcg": -3_000, "dividends": 2_000}, 2_173.01),
    ("AL", "single_loss", {"mstat": 1, "pwages": 42_000, "ltcg": -8_000}, 906.15),
    ("AL", "ltg_with_stl", {"mstat": 1, "pwages": 40_000, "ltcg": 12_000, "stcg": -4_000}, 1_261.15),
    ("CA", "couple_loss", {"mstat": 2, "pwages": 50_000, "swages": 30_000, "ltcg": -3_000, "dividends": 2_000}, 6_253.80),
    ("CA", "single_loss", {"mstat": 1, "pwages": 42_000, "ltcg": -8_000}, 3_181.90),
    ("CA", "ltg_with_stl", {"mstat": 1, "pwages": 40_000, "ltcg": 12_000, "stcg": -4_000}, 3_489.90),
    ("CT", "couple_loss", {"mstat": 2, "pwages": 50_000, "swages": 30_000, "ltcg": -3_000, "dividends": 2_000}, 160.0),
    ("CT", "single_loss", {"mstat": 1, "pwages": 42_000, "ltcg": -8_000}, 0.0),
    ("CT", "ltg_with_stl", {"mstat": 1, "pwages": 40_000, "ltcg": 12_000, "stcg": -4_000}, 217.0),
    ("IL", "couple_loss", {"mstat": 2, "pwages": 50_000, "swages": 30_000, "ltcg": -3_000, "dividends": 2_000}, 1_962.50),
    ("IL", "single_loss", {"mstat": 1, "pwages": 42_000, "ltcg": -8_000}, 950.0),
    ("IL", "ltg_with_stl", {"mstat": 1, "pwages": 40_000, "ltcg": 12_000, "stcg": -4_000}, 1_175.0),
    ("HI", "couple_loss", {"mstat": 2, "pwages": 50_000, "swages": 30_000, "ltcg": -3_000, "dividends": 2_000}, 6_767.13),
    ("HI", "single_loss", {"mstat": 1, "pwages": 42_000, "ltcg": -8_000}, 3_268.78),
    ("HI", "ltg_with_stl", {"mstat": 1, "pwages": 40_000, "ltcg": 12_000, "stcg": -4_000}, 3_577.30),
]


@pytest.mark.parametrize(
    "state,case_name,inputs,expected_siitax",
    CASES,
    ids=[f"{state}_{case_name}" for state, case_name, _, _ in CASES],
)
def test_matches_real_oracle_for_negative_capital_gains(
    state: str, case_name: str, inputs: dict, expected_siitax: float
) -> None:
    case = pl.DataFrame([{"year": 1980, "state": _STATES[state], "page": 40, **inputs}])
    result = calculate_taxes(case)
    assert result.get_column("siitax").item() == pytest.approx(expected_siitax, abs=0.01)
