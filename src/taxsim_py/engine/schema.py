"""Parameter loading and effective-year resolution."""

from pathlib import Path
from typing import Any

import yaml

PARAMETERS_ROOT = Path(__file__).resolve().parents[3] / "parameters"


# The C loader parses identically to the pure-Python safe loader, faster.
_YAML_LOADER = getattr(yaml, "CSafeLoader", yaml.SafeLoader)


def load_yaml(path: Path) -> dict[str, Any]:
    with path.open() as f:
        return yaml.load(f, Loader=_YAML_LOADER)


def resolve_year(by_year: dict[int, Any], year: int) -> Any:
    """Return the latest parameter value in effect for a year."""
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
