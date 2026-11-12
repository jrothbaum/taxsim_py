#!/usr/bin/env python3
"""Run one household through the oracle and print what it hands the state routine.

    uv run python scripts/trace_oracle.py year=2025 state=27 mstat=1 pwages=60000

Prints the oracle's federal results c(i) and inputs d(i) (the nonzero ones) as the state routine sees them,
after the state's id, law year, deflator and tax. Needs the oracle built by scripts/build_oracle.sh.
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

record = {"taxsimid": 1, "year": 2025, "state": 0, "mstat": 1, "idtl": 2, "opt1": 30, "opt1v": 999}
dump = None
for argument in sys.argv[1:]:
    key, value = argument.split("=")
    if key == "dump":
        # A state routine's own debug dump (`write(*,nml)` when extnd(30) equals this code, e.g. Maine 120).
        dump = int(value)
        continue
    record[key] = float(value) if "." in value else int(value)
if dump is not None:
    record.update(opt1=30, opt1v=dump)
# A throwaway first record, as scripts/oracle.py does, so the real one runs in the same state as in batches.
warmup = {**record, "taxsimid": 999_999_999, "year": 2000, "state": 0}
text = pl.DataFrame([warmup, record]).write_csv()
result = subprocess.run([str(TAXSIM_EXE)], input=text, capture_output=True, text=True, check=False)
if dump is not None:
    # Namelist blocks go to standard output: show the last one (the final record's last pass).
    blocks = result.stdout.split("&")[1:]
    print("&" + blocks[-1].split("/")[0].strip() + "\n/" if blocks else "(no dump: this routine has none for that code)")
    sys.exit(0)
lines = [line for line in result.stderr.splitlines() if "TRACE" in line]
starts = [i for i, line in enumerate(lines) if "TRACE state" in line]
for line in lines[starts[-1] :] if starts else []:
    print(line.replace("TRACE", "").strip())
if not starts:
    print("(no trace: no state routine ran, or the oracle predates the trace patch)")
