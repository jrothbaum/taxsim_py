"""Parameter files follow the conventions in docs/updating.md."""

import re
from pathlib import Path

import pytest
import yaml

from taxsim_py.engine.schema import PARAMETERS_ROOT

YAML_FILES = sorted(PARAMETERS_ROOT.rglob("*.yaml"))
STATE_FILES = sorted((PARAMETERS_ROOT / "states").glob("*/income_tax.yaml"))
# Integer keys that are not years: TAXSIM state codes in the sales tax table.
STATE_CODE_TABLES = {("sales_tax_deduction.yaml", "coefficients")}
YEAR_IN_NAME = re.compile(r"(19|20)\d\d")
# States whose law is never projected past its last year: Alaska repealed its
# tax in 1980 and Washington has no income tax.
UNPROJECTED_STATES = {"ak", "wa"}


class _UniqueKeyLoader(yaml.SafeLoader):
    """A loader that rejects a mapping with a repeated key."""

    def construct_mapping(self, node, deep=False):
        keys = [self.construct_object(key, deep=deep) for key, _ in node.value]
        repeated = {k for k in keys if keys.count(k) > 1}
        if repeated:
            raise yaml.constructor.ConstructorError(None, None, f"repeated keys {sorted(repeated)}", node.start_mark)
        return super().construct_mapping(node, deep=deep)


def _load(path: Path):
    with path.open() as stream:
        return yaml.load(stream, Loader=_UniqueKeyLoader)


def _keys(node, path=()):
    if isinstance(node, dict):
        for key, value in node.items():
            yield path, key
            yield from _keys(value, (*path, key))


@pytest.mark.parametrize("path", YAML_FILES, ids=lambda p: str(p.relative_to(PARAMETERS_ROOT)))
def test_keys_are_unique_years_or_names_without_years(path: Path) -> None:
    for parents, key in _keys(_load(path)):
        if isinstance(key, bool):
            continue
        if isinstance(key, int):
            in_state_table = any(t in parents for name, t in STATE_CODE_TABLES if path.name == name)
            assert in_state_table or 1960 <= key <= 2100, f"{'.'.join(map(str, parents))}: integer key {key}"
        else:
            assert not YEAR_IN_NAME.search(str(key)), f"{'.'.join(map(str, (*parents, key)))}: year in a name"


@pytest.mark.parametrize("path", STATE_FILES, ids=lambda p: p.parent.name)
def test_state_files_declare_coverage_and_sources(path: Path) -> None:
    text = path.read_text()
    assert re.search(r"^# Sources?:", text.split("\n\n")[0], re.M), "the opening comment names its sources"
    if path.parent.name not in UNPROJECTED_STATES:
        assert isinstance(_load(path).get("law_coded_through"), int)
