"""Screen streaming execution strategies in sequential, bounded subprocesses.

Run with the Python environment whose Polars version you want to test.
Each successful run verifies its complete API output against --reference.
Thread counts apply before the worker imports Polars. The tax engine itself
is not changed; experimental adapters are scoped to the benchmark process.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

VARIANTS = {
    "default": (16, ["--combined-collect"]),
    "no-subplan": (16, ["--no-subplan-elim"]),
    "group4": (16, ["--collect-batch-size", "4"]),
    "group8": (16, ["--collect-batch-size", "8"]),
    "individual-t16": (16, ["--individual-collect"]),
    "individual-t4": (4, ["--individual-collect"]),
    "individual-t1": (1, ["--individual-collect"]),
    "parallel4-t16": (16, ["--individual-collect", "--collect-workers", "4"]),
    "parallel4-t4": (4, ["--individual-collect", "--collect-workers", "4"]),
    "parallel4-t1": (1, ["--individual-collect", "--collect-workers", "4"]),
    "parallel8-t4": (4, ["--individual-collect", "--collect-workers", "8"]),
    "parallel8-t1": (1, ["--individual-collect", "--collect-workers", "8"]),
    "union-t16": (16, ["--union-collect"]),
    "union-t4": (4, ["--union-collect"]),
}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--reference", type=Path, required=True)
    parser.add_argument("--variants", nargs="+", choices=VARIANTS, default=list(VARIANTS))
    parser.add_argument("--rows", type=int, nargs="+", default=[1000])
    parser.add_argument("--repeats", type=int, default=1)
    parser.add_argument("--workers", type=int, default=1, help="API batch workers")
    parser.add_argument("--calculation-timeout", type=float, default=15)
    parser.add_argument("--process-timeout", type=float, default=60)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    worker = Path(__file__).with_name("benchmark_polars_version.py")
    records = []
    for name in args.variants:
        threads, extra = VARIANTS[name]
        output = args.output / name
        command = [sys.executable, str(worker), "--engine", "streaming",
                   "--workloads", "all", "--rows", *map(str, args.rows),
                   "--repeats", str(args.repeats), "--timeout", str(args.calculation_timeout),
                   "--workers", str(args.workers),
                   "--output", str(output), "--reference", str(args.reference), *extra]
        print(f"START {name}", flush=True)
        started = time.perf_counter()
        record = dict(name=name, threads=threads, command=command)
        try:
            result = subprocess.run(command, env={**os.environ, "POLARS_MAX_THREADS": str(threads)},
                                    capture_output=True, text=True, timeout=args.process_timeout)
            (args.output / f"{name}.log").write_text(result.stdout + "\n" + result.stderr)
            record["status"] = "ok" if result.returncode == 0 else "timeout" if "Timeout (" in result.stderr else "error"
            if record["status"] != "ok":
                record["error"] = result.stderr[-2000:]
        except subprocess.TimeoutExpired as exc:
            record["status"] = "process_timeout"
            (args.output / f"{name}.log").write_bytes((exc.stdout or b"") + b"\n" + (exc.stderr or b""))
        record["elapsed_s"] = time.perf_counter() - started
        if (output / "timings.json").exists():
            record["measurements"] = json.loads((output / "timings.json").read_text())
        records.append(record)
        (args.output / "screening.json").write_text(json.dumps(records, indent=2))
        print(json.dumps(record), flush=True)


if __name__ == "__main__":
    main()
