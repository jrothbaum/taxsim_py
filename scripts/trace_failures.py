#!/usr/bin/env python3
"""Trace the oracle for the worst disagreeing cases of a validation run, writing full traces to files.

    uv run python scripts/trace_failures.py /tmp/val21 state=MT year=2025 min=5 limit=5

`/tmp/val21` is a `VALIDATE_OUT` directory from scripts/validate_states.py. Only failing cases are traced (largest
absolute siitax gap first). Each full trace goes to `<dir>/traces/<taxsimid>.txt`; the console shows one line per case:
the gap and the trace path, so the output stays short.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

import polars as pl  # noqa: E402

from oracle import TAXSIM_EXE  # noqa: E402

directory = Path(sys.argv[1])
options = dict(argument.split("=") for argument in sys.argv[2:])
failures = pl.read_parquet(directory / "failures.parquet").with_columns(
    gap=(pl.col("siitax") - pl.col("siitax_expected")).abs()
)
if "state" in options:
    failures = failures.filter(pl.col("state_name") == options["state"])
if "year" in options:
    failures = failures.filter(pl.col("year") == int(options["year"]))
failures = failures.filter(pl.col("gap") >= float(options.get("min", 1))).sort("gap", descending=True)
failures = failures.head(int(options.get("limit", 5)))

cases = pl.read_parquet(directory / "cases.parquet")
inputs = [c for c in cases.columns if c not in {"description", "oracle_divergent", "validation_state"}]
(directory / "traces").mkdir(exist_ok=True)
for row in failures.iter_rows(named=True):
    case = cases.filter(pl.col("taxsimid") == row["taxsimid"]).select(inputs).row(0, named=True)
    record = {k: v for k, v in case.items() if v is not None}
    record.update(idtl=2, opt1=99, opt1v=999)
    warmup = {**record, "taxsimid": 999_999_999, "year": 2000, "state": 0}
    text = pl.DataFrame([warmup, record]).write_csv()
    result = subprocess.run([str(TAXSIM_EXE)], input=text, capture_output=True, text=True, check=False)
    lines = [line.replace("TRACE", "").strip() for line in result.stderr.splitlines() if "TRACE" in line]
    starts = [i for i, line in enumerate(lines) if line.startswith("state")]
    path = directory / "traces" / f"{row['taxsimid']}.txt"
    path.write_text("\n".join(lines[starts[-1] :] if starts else lines) + "\n")
    print(f"{row['description'][:60]:60}  gap {row['gap']:10.2f}  {path}")
