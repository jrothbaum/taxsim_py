"""Time and peak memory of taxsim_py, the compiled TAXSIM and PolicyEngine-US.

Each measurement runs in its own process on the same mixed-state batch:

    uv run --group test scripts/benchmark_comparison.py \
        --rows 1000 100000 --policyengine-rows 100 1000
"""

from __future__ import annotations

import argparse
import resource
import subprocess
import sys
import time
from pathlib import Path

import polars as pl

sys.path.insert(0, str(Path(__file__).resolve().parent))
import benchmark_multistate as multistate  # noqa: E402

ROOT = multistate.ROOT
YEAR = 2022
NO_INCOME_TAX_CASES = {"AK", "TN", "TX", "WA", "NH"}
ENGINES = ("taxsim_py", "taxsim", "policyengine")


def states() -> list[str]:
    return [p.name.upper() for p in sorted((ROOT / "parameters" / "states").iterdir()) if p.name.upper() not in NO_INCOME_TAX_CASES]


def worker(engine: str, rows: int, taxsim_py_options: dict) -> None:
    from taxsim_py.validation.independent import run_policyengine

    cases = multistate.build_cases(states(), [YEAR], rows)
    payload = multistate.encode_fortran_input(cases)
    started = time.perf_counter()
    if engine == "taxsim":
        # The Fortran is its own process; GNU time reports its peak memory (KB).
        result = subprocess.run(
            ["/usr/bin/time", "-f", "%M", str(multistate.TAXSIM_EXE)], input=payload, capture_output=True, text=True, check=True
        )
        peak_mb = int(result.stderr.split()[-1]) / 1024
    else:
        runner = multistate.calculate_taxes if engine == "taxsim_py" else run_policyengine
        runner(cases, **taxsim_py_options) if engine == "taxsim_py" else runner(cases)
        peak_mb = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024
    print(f"{time.perf_counter() - started:.2f} {peak_mb:.0f}")


def measure(engine: str, rows: int, extra: list[str]) -> tuple[float, float]:
    out = subprocess.run(
        [sys.executable, __file__, "--worker", engine, str(rows), *extra], capture_output=True, text=True, check=True
    ).stdout.split()
    return float(out[0]), float(out[1])


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--rows", type=int, nargs="+", default=[1000, 100000])
    parser.add_argument("--policyengine-rows", type=int, nargs="+", default=[100, 1000])
    parser.add_argument("--worker", nargs=2, metavar=("ENGINE", "ROWS"))
    parser.add_argument("--batch-rows", type=int, help="taxsim_py batch_rows (default: fastest)")
    parser.add_argument("--workers", type=int, help="taxsim_py max_year_workers")
    args = parser.parse_args()
    options = {"batch_rows": args.batch_rows, "max_year_workers": args.workers}
    if args.worker:
        worker(args.worker[0], int(args.worker[1]), options)
        return
    extra = [f"--{k.replace('_', '-')}={v}" for k, v in (("batch_rows", args.batch_rows), ("workers", args.workers)) if v]
    print("engine,rows,seconds,peak_mb")
    for engine in ENGINES:
        for rows in args.policyengine_rows if engine == "policyengine" else args.rows:
            seconds, peak = measure(engine, rows, extra)
            print(f"{engine},{rows},{seconds:.2f},{peak:.0f}", flush=True)


if __name__ == "__main__":
    main()
