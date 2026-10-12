"""Each statutory correction changes its column only in statutory mode."""

import polars as pl
import pytest

from statutory_correction_table import CASES
from taxsim_py import calculate_taxes


def _results(mode: str) -> pl.DataFrame:
    frame = pl.DataFrame([case["inputs"] for case in CASES], infer_schema_length=None)
    return calculate_taxes(frame, calculation_mode=mode, idtl=2, keep_intermediate=True)


@pytest.fixture(scope="module")
def results() -> dict[str, pl.DataFrame]:
    return {mode: _results(mode) for mode in ("taxsim", "statutory")}


@pytest.mark.parametrize(
    "index", range(len(CASES)), ids=[f"{c['correction']}: {c['description']}" for c in CASES]
)
@pytest.mark.parametrize("mode", ["taxsim", "statutory"])
def test_correction(results: dict[str, pl.DataFrame], index: int, mode: str) -> None:
    case = CASES[index]
    value = results[mode].get_column(case["column"]).item(index)
    assert value == pytest.approx(case[mode], abs=0.01)
