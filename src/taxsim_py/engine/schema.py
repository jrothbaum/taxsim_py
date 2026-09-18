"""Parameter loading and effective-year resolution.

Parameters are stored with sparse integer-year keys (see the design doc): a value
is only present for a year if it changed from the prior coded year. Resolution
picks the value at the largest coded year <= the requested year.
"""

from pathlib import Path
from typing import Any

import yaml

PARAMETERS_ROOT = Path(__file__).resolve().parents[3] / "parameters"


def load_yaml(path: Path) -> dict[str, Any]:
    with path.open() as f:
        return yaml.safe_load(f)


def resolve_year(by_year: dict[int, Any], year: int) -> Any:
    """Pick the value at the largest coded year <= `year`.

    Raises if `year` is before the first coded year - callers should not
    silently extrapolate backward past known law.
    """
    coded_years = sorted(by_year)
    candidates = [y for y in coded_years if y <= year]
    if not candidates:
        raise ValueError(
            f"No parameter value coded for year {year} or earlier "
            f"(earliest coded year is {coded_years[0]})"
        )
    return by_year[max(candidates)]


def validate_brackets(brackets: list[list[float]], context: str) -> None:
    """Port of the Fortran look2 sanity check: thresholds must strictly increase."""
    thresholds = [b[0] for b in brackets]
    for prev, curr in zip(thresholds, thresholds[1:]):
        if curr <= prev:
            raise ValueError(
                f"{context}: bracket thresholds must strictly increase, "
                f"got {prev} followed by {curr}"
            )
