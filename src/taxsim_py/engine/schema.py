"""Parameter loading and effective-year resolution."""

from collections.abc import Iterator, Mapping
from pathlib import Path
from typing import Any

import yaml

_REPOSITORY_PARAMETERS = Path(__file__).resolve().parents[3] / "parameters"
_INSTALLED_PARAMETERS = Path(__file__).resolve().parents[1] / "parameters"
PARAMETERS_ROOT = (
    _REPOSITORY_PARAMETERS
    if _REPOSITORY_PARAMETERS.exists()
    else _INSTALLED_PARAMETERS
)


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


class YearParams(Mapping[str, Any]):
    """A parameter mapping read at one law year.

    Indexing returns an entry unchanged; `value` resolves a year-keyed entry
    at `year` and `num` also converts it to float.
    """

    def __init__(self, params: Mapping[str, Any], year: int) -> None:
        self.params = params
        self.year = year

    def __getitem__(self, key: str) -> Any:
        return self.params[key]

    def __iter__(self) -> Iterator[str]:
        return iter(self.params)

    def __len__(self) -> int:
        return len(self.params)

    def value(self, key: str) -> Any:
        return resolve_year(self.params[key], self.year)

    def num(self, key: str) -> float:
        return float(self.value(key))


def validate_brackets(brackets: list[list[float]], context: str) -> None:
    """Port of the Fortran look2 sanity check: thresholds must strictly increase."""
    thresholds = [b[0] for b in brackets]
    for prev, curr in zip(thresholds, thresholds[1:]):
        if curr <= prev:
            raise ValueError(
                f"{context}: bracket thresholds must strictly increase, "
                f"got {prev} followed by {curr}"
            )
