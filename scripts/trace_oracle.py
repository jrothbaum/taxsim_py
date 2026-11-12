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

record = {"taxsimid": 1, "year": 2025, "state": 0, "mstat": 1, "idtl": 2, "opt1": 99, "opt1v": 999}
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
    # A routine can print several namelists; show the last pass of each.
    last = {}
    for block in blocks:
        last[block.split()[0]] = block.split("/")[0].strip()
    for text_block in last.values():
        print("&" + text_block + "\n/")
    if not blocks:
        print("(no dump: this routine has none for that code)")
    sys.exit(0)
lines = [line for line in result.stderr.splitlines() if "TRACE" in line]
starts = [i for i, line in enumerate(lines) if "TRACE state" in line]
CALC_NAMES = (
    "hy rent agi exemp stded xitded taxinc pcred chcr earncr credit rt taxbc rebate solcrd ctc gcred amt fedtax "
    "rcred refcrd taxinh taxinw stath statw"
).split()
# The oracle calls the state routine once per pass and iteration (itemizing forced on and off); show the last call whose
# tax is the one the oracle reported.
reported = None
rows = [row for row in result.stdout.strip().splitlines() if row]
if len(rows) >= 3:
    header, last_row = rows[0].split(","), rows[-1].split(",")
    reported = float(last_row[header.index("siitax")])
chosen = starts[-1] if starts else None
for start in starts:
    fields = lines[start].replace("TRACE", "").replace("state", "").split()
    if reported is not None and len(fields) >= 4 and abs(float(fields[3]) - reported) < 0.011:
        chosen = start
if chosen is not None:
    print(f"(oracle reported siitax {reported}; showing the matching state call)")
for line in lines[chosen:] if chosen is not None else []:
    text_line = line.replace("TRACE", "").strip()
    if text_line.startswith("calcv"):
        # The state routine's /calc/ block, by name (this is where the routine leaves its AGI, deductions, tax, credits).
        values = [float(v) for v in text_line.split()[1:]]
        print("state routine /calc/ block:")
        for name, value in zip(CALC_NAMES, values):
            print(f"  {name:8} {value:16.4f}")
        continue
    print(text_line)
if not starts:
    print("(no trace: no state routine ran, or the oracle predates the trace patch)")
