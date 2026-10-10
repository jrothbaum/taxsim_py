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
import signal
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

import psutil

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


MIB = 1024 * 1024


def available_memory_mib() -> float:
    return psutil.virtual_memory().available / MIB


def process_tree(pid: int) -> list[psutil.Process]:
    try:
        root = psutil.Process(pid)
        return [root, *root.children(recursive=True)]
    except psutil.NoSuchProcess:
        return []


def process_tree_rss_mib(pid: int) -> float:
    total = 0
    for process in process_tree(pid):
        try:
            total += process.memory_info().rss
        except psutil.NoSuchProcess:
            pass
    return total / MIB


def kill_process_tree(process: subprocess.Popen) -> None:
    if os.name == "posix":
        # The worker leads its own session, so this also reaches orphaned descendants.
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        return
    # Windows has no process groups; skip an exited worker so a reused PID is never killed.
    if process.poll() is not None:
        return
    for member in reversed(process_tree(process.pid)):
        try:
            member.kill()
        except psutil.NoSuchProcess:
            pass


def peak_rss_mib() -> float:
    if sys.platform == "win32":
        return psutil.Process().memory_info().peak_wset / MIB
    import resource

    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    # ru_maxrss is bytes on macOS and KiB on Linux.
    return peak / MIB if sys.platform == "darwin" else peak / 1024


def windows_child_peak_mib(process: subprocess.Popen) -> float:
    """Peak working set of an exited child, read through the handle Popen still holds."""
    import ctypes
    from ctypes import wintypes

    class ProcessMemoryCounters(ctypes.Structure):
        _fields_ = [
            ("cb", wintypes.DWORD),
            ("PageFaultCount", wintypes.DWORD),
            ("PeakWorkingSetSize", ctypes.c_size_t),
            ("WorkingSetSize", ctypes.c_size_t),
            ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
            ("QuotaPagedPoolUsage", ctypes.c_size_t),
            ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
            ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
            ("PagefileUsage", ctypes.c_size_t),
            ("PeakPagefileUsage", ctypes.c_size_t),
        ]

    get_memory_info = ctypes.WinDLL("kernel32", use_last_error=True).K32GetProcessMemoryInfo
    get_memory_info.argtypes = [wintypes.HANDLE, ctypes.POINTER(ProcessMemoryCounters), wintypes.DWORD]
    get_memory_info.restype = wintypes.BOOL
    counters = ProcessMemoryCounters(cb=ctypes.sizeof(ProcessMemoryCounters))
    if not get_memory_info(int(process._handle), ctypes.byref(counters), counters.cb):
        raise ctypes.WinError(ctypes.get_last_error())
    return counters.PeakWorkingSetSize / MIB


def run_taxsim_exe(payload: str) -> float:
    """Run the compiled TAXSIM and return its peak memory in MiB."""
    if sys.platform == "win32":
        with subprocess.Popen(
            [str(multistate.TAXSIM_EXE)], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
        ) as process:
            _, stderr = process.communicate(payload)
            if process.returncode:
                raise subprocess.CalledProcessError(process.returncode, process.args, stderr=stderr)
            return windows_child_peak_mib(process)
    # GNU time reports the Fortran's peak memory (KB).
    result = subprocess.run(
        ["/usr/bin/time", "-f", "%M", str(multistate.TAXSIM_EXE)], input=payload, capture_output=True, text=True, check=True
    )
    return int(result.stderr.split()[-1]) / 1024


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
                kill_process_tree(process)
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
        # The Fortran is its own process, so its peak is measured separately.
        started = time.perf_counter()
        peak_mib = run_taxsim_exe(payload)
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
        peak_mib = peak_rss_mib()
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
