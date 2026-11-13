"""Statutory mode departs from TAXSIM only where TAXSIM is known to be wrong."""

from dataclasses import fields

import polars as pl
import pytest

from taxsim_py import calculate_taxes
from taxsim_py.behavior import (
    ALL_OVERRIDES_BEHAVIOR,
    FIXED_IN_2026_BUILD,
    KEEP_TAXSIM_BEHAVIOR,
    STATUTORY_BEHAVIOR,
    TAXSIM_BEHAVIOR,
    VERIFIED_TAXSIM_BUGS,
    resolve_behavior,
)


def _differing(a, b) -> set[str]:
    return {f.name for f in fields(a) if f.name != "mode" and getattr(a, f.name) != getattr(b, f.name)}


def test_statutory_differs_from_taxsim_only_on_known_bugs() -> None:
    assert _differing(STATUTORY_BEHAVIOR, TAXSIM_BEHAVIOR) == set(VERIFIED_TAXSIM_BUGS)


def test_every_other_override_keeps_taxsims_behavior() -> None:
    for name in KEEP_TAXSIM_BEHAVIOR:
        assert getattr(STATUTORY_BEHAVIOR, name) == getattr(TAXSIM_BEHAVIOR, name), name
    assert not KEEP_TAXSIM_BEHAVIOR & VERIFIED_TAXSIM_BUGS
    assert not VERIFIED_TAXSIM_BUGS & FIXED_IN_2026_BUILD


def test_all_overrides_profile_is_the_old_statutory_profile() -> None:
    assert _differing(ALL_OVERRIDES_BEHAVIOR, TAXSIM_BEHAVIOR) == {
        f.name for f in fields(ALL_OVERRIDES_BEHAVIOR) if f.name != "mode"
    } - set(FIXED_IN_2026_BUILD)


def test_statutory_mode_resolves_to_the_policy_profile(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TAXSIM_PY_STATUTORY_PROFILE", raising=False)
    assert resolve_behavior("statutory") is STATUTORY_BEHAVIOR


def test_rust_engine_refuses_the_policy_profile(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TAXSIM_PY_STATUTORY_PROFILE", raising=False)
    frame = pl.DataFrame({"year": [2019], "state": [0], "mstat": [1], "pwages": [50000.0]})
    with pytest.raises(RuntimeError, match="departs from TAXSIM only where"):
        calculate_taxes(frame, calculation_mode="statutory", engine="rust")
