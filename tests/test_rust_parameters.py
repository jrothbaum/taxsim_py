"""The compiled calculators read the YAML parameters themselves, so their
resolution must match `taxsim_py.engine.schema` exactly: every parameter path
in every file, at every law year. Skipped without the compiled calculator."""

import json
import math
import unittest

from taxsim_py.engine.schema import PARAMETERS_ROOT, is_year_keyed, load_yaml, resolve_path

try:
    from taxsim_py import _internal
except ImportError:
    _internal = None

YEARS = range(1977, 2026)
_ERROR = object()  # the path is absent or the provision not in effect


def _paths(node, path=()):
    """Every string-key path through the tree, looking inside each year's value."""
    yield path
    if not isinstance(node, dict):
        return
    if is_year_keyed(node):
        for child in node.values():
            yield from _paths(child, path)
    else:
        for key, child in node.items():
            if isinstance(key, str):
                yield from _paths(child, path + (key,))


def _normalise(value):
    """Python value as the Rust side reports it: JSON-shaped, infinities as strings.

    PyYAML reads `1e+20` (no decimal point) as a string where YAML 1.2 and
    Rust read a number; the calculators convert with `float()`, so a numeric
    string compares as that number.
    """
    if isinstance(value, str):
        try:
            value = float(value)
        except ValueError:
            return value
    if isinstance(value, float) and not math.isfinite(value):
        return "nan" if math.isnan(value) else ("inf" if value > 0 else "-inf")
    if isinstance(value, dict):
        return {str(k): _normalise(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_normalise(v) for v in value]
    return value


@unittest.skipIf(_internal is None, "the compiled calculator is not installed")
class ParameterParityTests(unittest.TestCase):
    def test_resolution_matches_python(self):
        checked = 0
        for file in sorted(PARAMETERS_ROOT.rglob("*.yaml")):
            relative = file.relative_to(PARAMETERS_ROOT).as_posix()
            doc = load_yaml(file)
            for key, entry in doc.items():
                for path in sorted(set(_paths(entry))):
                    for year in YEARS:
                        with self.subTest(file=relative, key=key, path=path, year=year):
                            try:
                                expected = _normalise(resolve_path(entry, path, year))
                            except (ValueError, KeyError, TypeError):
                                expected = _ERROR
                            found = json.loads(
                                _internal.resolve_param(str(PARAMETERS_ROOT), relative, key, list(path), year)
                            )
                            if expected is _ERROR:
                                self.assertTrue(isinstance(found, dict) and "error" in found, found)
                            else:
                                self.assertEqual(expected, found)
                        checked += 1
                if is_year_keyed(entry):
                    for year in YEARS:
                        started = [y for y in entry if y <= year]
                        expected = bool(started) and entry[max(started)] is not None
                        self.assertEqual(
                            expected, _internal.param_in_effect(str(PARAMETERS_ROOT), relative, key, year),
                            (relative, key, year),
                        )
        self.assertGreater(checked, 100_000)


if __name__ == "__main__":
    unittest.main()
