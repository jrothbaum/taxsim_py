"""Time and peak memory of taxsim_py, the compiled TAXSIM and PolicyEngine-US.

Each measurement runs in its own process on the same mixed-state batch:

    uv run --group test scripts/benchmark_comparison.py \
        --rows 1000 100000 --policyengine-rows 100 1000
"""

from __future__ import annotations

import argparse
import importlib
import json
import os
import resource
import signal
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import benchmark_multistate as multistate  # noqa: E402

ROOT = multistate.ROOT
YEAR = 2022
NO_INCOME_TAX_CASES = {"AK", "TN", "TX", "WA", "NH"}
ENGINES = ("taxsim_py", "taxsim", "policyengine")
RESULT_PREFIX = "BENCHMARK_RESULT "


@dataclass
class Measurement:
    seconds: float
    peak_mib: float
    status: str = "ok"
    detail: str = ""


def available_memory_mib() -> float:
    for line in Path("/proc/meminfo").read_text().splitlines():
        if line.startswith("MemAvailable:"):
            return int(line.split()[1]) / 1024
    raise RuntimeError("Linux MemAvailable is required for memory protection")


def process_tree_rss_mib(pid: int) -> float:
    pending = [pid]
    seen = set()
    total_kib = 0
    while pending:
        current = pending.pop()
        if current in seen:
            continue
        seen.add(current)
        proc = Path(f"/proc/{current}")
        try:
            for line in (proc / "status").read_text().splitlines():
                if line.startswith("VmRSS:"):
                    total_kib += int(line.split()[1])
            for task in (proc / "task").iterdir():
                try:
                    pending.extend(map(int, (task / "children").read_text().split()))
                except (FileNotFoundError, ProcessLookupError):
                    pass
        except (FileNotFoundError, ProcessLookupError):
            pass
    return total_kib / 1024


def run_guarded(command: list[str], memory_mib: float, reserve_mib: float, timeout: float) -> Measurement:
    budget = min(memory_mib, available_memory_mib() - reserve_mib)
    if budget <= 0:
        return Measurement(0, 0, "memory_limit", "Not enough available RAM for the requested reserve")
    started = time.perf_counter()
    peak = 0.0
    # Files avoid pipe backpressure from dependency progress messages.
    with tempfile.TemporaryFile(mode="w+") as stdout, tempfile.TemporaryFile(mode="w+") as stderr:
        with subprocess.Popen(
            command, stdout=stdout, stderr=stderr, stdin=subprocess.DEVNULL, start_new_session=True
        ) as process:
            status = "ok"
            detail = ""
            try:
                while process.poll() is None:
                    peak = max(peak, process_tree_rss_mib(process.pid))
                    if peak >= budget:
                        status, detail = "memory_limit", f"Process-tree RSS reached the {budget:.0f} MiB budget"
                        break
                    if available_memory_mib() < reserve_mib:
                        status, detail = "memory_limit", f"Available RAM fell below the {reserve_mib:.0f} MiB reserve"
                        break
                    if time.perf_counter() - started >= timeout:
                        status, detail = "timeout", f"Reached the {timeout:g} second time limit"
                        break
                    time.sleep(0.01)
            finally:
                # Kill descendants as well as the worker on limits or interruption.
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                process.wait()
            elapsed = time.perf_counter() - started
            if status != "ok":
                return Measurement(elapsed, peak, status, detail)
            stderr.seek(0)
            if process.returncode:
                return Measurement(elapsed, peak, "error", stderr.read()[-4000:])
            stdout.seek(0)
            for line in reversed(stdout.read().splitlines()):
                if line.startswith(RESULT_PREFIX):
                    result = json.loads(line[len(RESULT_PREFIX):])
                    return Measurement(result["seconds"], result["peak_mib"])
            return Measurement(elapsed, peak, "error", "Worker exited without a benchmark result")


def states(selected: list[str] | None = None) -> list[str]:
    if selected:
        return [state.upper() for state in selected]
    return [p.name.upper() for p in sorted((ROOT / "parameters" / "states").iterdir()) if p.name.upper() not in NO_INCOME_TAX_CASES]


def worker(engine: str, rows: int, taxsim_py_options: dict, selected_states: list[str] | None = None) -> None:
    cases = multistate.build_cases(states(selected_states), [YEAR], rows)
    if engine == "taxsim":
        payload = multistate.encode_fortran_input(cases, mtr=11)
        # The Fortran is its own process; GNU time reports its peak memory (KB).
        started = time.perf_counter()
        result = subprocess.run(
            ["/usr/bin/time", "-f", "%M", str(multistate.TAXSIM_EXE)], input=payload, capture_output=True, text=True, check=True
        )
        peak_mib = int(result.stderr.split()[-1]) / 1024
    else:
        if engine == "taxsim_py":
            runner = multistate.calculate_taxes
        else:
            from taxsim_py.validation.independent import run_policyengine

            importlib.import_module("policyengine_taxsim.runners.policyengine_runner")
            runner = run_policyengine
        started = time.perf_counter()
        result = runner(cases, mtr=11, **taxsim_py_options) if engine == "taxsim_py" else runner(cases)
        if result.height != rows:
            raise RuntimeError(f"Expected {rows} output rows, got {result.height}")
        peak_mib = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024
    print(RESULT_PREFIX + json.dumps({"seconds": time.perf_counter() - started, "peak_mib": peak_mib}))


def measure(engine: str, rows: int, extra: list[str], memory_mib: float, reserve_mib: float, timeout: float) -> Measurement:
    return run_guarded(
        [sys.executable, __file__, "--worker", engine, str(rows), *extra], memory_mib, reserve_mib, timeout
    )


def positive_int(value: str) -> int:
    number = int(value)
    if number < 1:
        raise argparse.ArgumentTypeError("must be at least 1")
    return number


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--rows", type=positive_int, nargs="+", default=[1000, 100000])
    parser.add_argument("--policyengine-rows", type=positive_int, nargs="+", default=[100, 1000])
    parser.add_argument("--engines", nargs="+", choices=ENGINES, default=ENGINES)
    parser.add_argument("--worker", nargs=2, help=argparse.SUPPRESS)
    parser.add_argument("--batch-rows", type=int, help="taxsim_py batch_rows (default: automatic)")
    parser.add_argument("--workers", type=int, help="taxsim_py max_year_workers")
    parser.add_argument("--states", nargs="+", help="state postal codes to benchmark (default: all 42 with validation cases)")
    parser.add_argument("--memory-limit-mib", type=positive_int, default=8192, help="monitored process-tree RSS budget (default: 8192 MiB)")
    parser.add_argument("--reserve-memory-mib", type=positive_int, default=2048, help="minimum available system RAM (default: 2048 MiB)")
    parser.add_argument("--timeout", type=positive_int, default=600, help="maximum wall-clock seconds per measurement (default: 600)")
    args = parser.parse_args()
    options = {"batch_rows": args.batch_rows, "max_year_workers": args.workers}
    if args.worker:
        worker(args.worker[0], int(args.worker[1]), options, args.states)
        return
    if not Path("/proc/self/status").exists():
        parser.error("memory protection requires Linux /proc")
    extra = [f"--{k.replace('_', '-')}={v}" for k, v in (("batch_rows", args.batch_rows), ("workers", args.workers)) if v]
    if args.states:
        extra += ["--states", *args.states]
    print("engine,rows,seconds,peak_mib,status", flush=True)
    for engine in args.engines:
        limited_rows = None
        for rows in args.policyengine_rows if engine == "policyengine" else args.rows:
            if limited_rows is not None and rows >= limited_rows:
                print(f"{engine},{rows},,,skipped_after_limit", flush=True)
                continue
            print(f"Running {engine}: {rows} rows", file=sys.stderr, flush=True)
            result = measure(engine, rows, extra, args.memory_limit_mib, args.reserve_memory_mib, args.timeout)
            print(f"{engine},{rows},{result.seconds:.2f},{result.peak_mib:.0f},{result.status}", flush=True)
            if result.detail:
                print(result.detail, file=sys.stderr, flush=True)
            if result.status in {"memory_limit", "timeout"}:
                limited_rows = rows


if __name__ == "__main__":
    main()
