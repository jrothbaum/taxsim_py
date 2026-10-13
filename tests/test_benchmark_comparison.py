"""Resource protection and comparable inputs for the benchmark harness."""

import importlib
import sys
from pathlib import Path

import pytest


@pytest.fixture
def benchmark(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / "scripts"))
    return importlib.import_module("benchmark_comparison")


def test_guard_accepts_result_after_progress(benchmark):
    command = [sys.executable, "-c", 'print("progress"); print(\'BENCHMARK_RESULT {"seconds": 1.25, "peak_mib": 20}\')']
    result = benchmark.run_guarded(command, 256, 1, 5)
    assert result.status == "ok"
    assert result.seconds == 1.25
    assert result.peak_mib == 20


def test_guard_stops_timeout(benchmark):
    result = benchmark.run_guarded([sys.executable, "-c", "import time; time.sleep(10)"], 256, 1, 0.1)
    assert result.status == "timeout"
    assert result.seconds < 5


def test_guard_stops_memory_growth(benchmark):
    command = [sys.executable, "-c", "import time; allocation = bytearray(64 * 1024 * 1024); time.sleep(10)"]
    result = benchmark.run_guarded(command, 32, 1, 5)
    assert result.status == "memory_limit"
    assert result.peak_mib >= 32


def test_guard_does_not_launch_without_reserve(benchmark, monkeypatch):
    monkeypatch.setattr(benchmark, "available_memory_mib", lambda: 100)
    result = benchmark.run_guarded(["must-not-launch"], 256, 200, 5)
    assert result.status == "memory_limit"
    assert result.seconds == 0


def test_fortran_input_requests_same_marginal_mode(benchmark):
    cases = benchmark.multistate.build_cases(["MA"], [2022], 2)
    lines = benchmark.multistate.encode_fortran_input(cases, mtr=11).splitlines()
    assert lines[0].split()[-1] == "mtr"
    assert all(line.split()[-1] == "11" for line in lines[1:])
    assert "mtr" not in benchmark.multistate.encode_fortran_input(cases).splitlines()[0].split()


def test_polars_engine_limits_are_independent(benchmark, monkeypatch, capsys):
    calls = []

    def measure(engine, rows, extra, *limits):
        calls.append((engine, rows, extra))
        if "in-memory" in extra:
            return benchmark.Measurement(1, 8192, "memory_limit")
        return benchmark.Measurement(1, 20)

    monkeypatch.setattr(benchmark, "measure", measure)
    monkeypatch.setattr(sys, "argv", ["benchmark", "--engines", "taxsim_py", "taxsim",
                                     "--polars-engines", "in-memory", "streaming",
                                     "--rows", "1000", "10000"])
    benchmark.main()
    output = capsys.readouterr().out
    assert "taxsim_py[in-memory],10000,,,skipped_after_limit" in output
    assert "taxsim_py[streaming],10000,1.00,20,ok" in output
    assert len([call for call in calls if call[0] == "taxsim"]) == 2
