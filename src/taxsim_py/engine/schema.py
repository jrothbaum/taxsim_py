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
    """Return the latest parameter value in effect for a year.

    A `null` entry marks the year a provision ended; reading it in or after
    that year raises.
    """
    coded_years = sorted(by_year)
    candidates = [y for y in coded_years if y <= year]
    if not candidates:
        raise ValueError(
            f"No parameter value coded for year {year} or earlier "
            f"(earliest coded year is {coded_years[0]})"
        )
    effective = max(candidates)
    if by_year[effective] is None:
        raise ValueError(f"Parameter not in effect in {year} (ended {effective})")
    return by_year[effective]


def is_year_keyed(node: Any) -> bool:
    """Whether a parameter entry is a mapping from year to value."""
    return isinstance(node, Mapping) and bool(node) and all(isinstance(k, int) for k in node)


def resolve_path(node: Any, path: tuple[str, ...], year: int) -> Any:
    """Walk `path` into a parameter entry, resolving year-keyed levels at `year`."""
    if is_year_keyed(node):
        node = resolve_year(node, year)
    for key in path:
        node = node[key]
        if is_year_keyed(node):
            node = resolve_year(node, year)
    return node


class YearParams(Mapping[str, Any]):
    """A parameter mapping read at one law year.

    Indexing returns an entry unchanged. `value("a", "b")` reads `a` then `b`,
    resolving any year-keyed level at `year`, so year keys may sit above or
    below the other keys; `num` also converts the result to float.
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

    def value(self, key: str, *path: str) -> Any:
        return resolve_path(self.params[key], path, self.year)

    def num(self, key: str, *path: str) -> float:
        return float(self.value(key, *path))

    def in_effect(self, key: str) -> bool:
        """Whether a year-keyed entry has a non-null value at `year`."""
        by_year = self.params[key]
        started = [y for y in by_year if y <= self.year]
        return bool(started) and by_year[max(started)] is not None


def validate_brackets(brackets: list[list[float]], context: str) -> None:
    """Port of the Fortran look2 sanity check: thresholds must strictly increase."""
    thresholds = [b[0] for b in brackets]
    for prev, curr in zip(thresholds, thresholds[1:]):
        if curr <= prev:
            raise ValueError(
                f"{context}: bracket thresholds must strictly increase, "
                f"got {prev} followed by {curr}"
            )
