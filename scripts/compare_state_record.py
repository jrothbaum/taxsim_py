#!/usr/bin/env python3
"""Show oracle and taxsim-mode state detail for one household.

    uv run python scripts/compare_state_record.py year=2025 state=4 mstat=1 pwages=56000 depx=1
"""

from __future__ import annotations

import os as _os

_os.environ.setdefault("TAXSIM_PY_ENGINE", "python")

import sys
from pathlib import Path

import polars as pl

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

from oracle import run_oracle  # noqa: E402
from taxsim_py import calculate_taxes  # noqa: E402

record = {"taxsimid": 1, "year": 2022, "state": 0, "mstat": 1}
for argument in sys.argv[1:]:
    key, value = argument.split("=")
    record[key] = float(value) if "." in value else int(value)
for key in ("depx",):
    for child in ("dep13", "dep17", "dep18"):
        record.setdefault(child, record.get(key, 0))
frame = pl.DataFrame([record])
expected = run_oracle(frame, idtl=2)
actual = calculate_taxes(frame, calculation_mode="taxsim", engine="python", idtl=2, taxsim_names=True)
for column in ("fiitax", "siitax", "v10", "v13", "v18", "v19", "v28", "v30", "v31", "v32", "v33", "v34", "v35", "v36", "v37", "v38", "v39", "v40", "v41", "staxbc"):
    if column in expected.columns and column in actual.columns:
        flag = "" if abs(expected[column][0] - actual[column][0]) < 0.02 else "   <-- differs"
        print(f"{column:8} oracle {expected[column][0]:>14.2f}   python {actual[column][0]:>14.2f}{flag}")
