"""Time API boundaries, then profile captured plans independently.

Run with .venv/bin/python scripts/profile_execution.py --output /tmp/taxsim-profile.
Instrumentation is process-local; tax calculators are not modified. Replay
totals are serial diagnostics, not the wall time of parallel state collection.
"""
from __future__ import annotations

import os as _os

# These scripts exercise the Python reference implementation (taxsim mode, worksheets, Polars execution).
_os.environ.setdefault("TAXSIM_PY_ENGINE", "python")

import argparse
import cProfile
import csv
import json
import statistics
import sys
import time
from collections import Counter
from pathlib import Path
from unittest.mock import patch

import polars as pl
from polars.testing import assert_frame_equal

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
import benchmark_multistate as benchmark  # noqa: E402
from taxsim_py import calculate_taxes  # noqa: E402
from taxsim_py.engine import federal_state  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("/tmp/taxsim-profile"))
    parser.add_argument("--rows", type=int, nargs="+", default=[1000, 10000, 100000])
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--mtr", type=int, default=11)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    states = [p.name.upper() for p in sorted((ROOT / "src/taxsim_py/parameters/states").iterdir())
              if p.name.upper() not in {"AK", "TN", "TX", "WA", "NH"}]
    metadata = {"polars": pl.__version__, "threads": pl.thread_pool_size(),
                "year": 2022, "mtr": args.mtr, "mode": "taxsim",
                "max_year_workers": 1, "states": states}
    (args.output / "metadata.json").write_text(json.dumps(metadata, indent=2))
    options = dict(mtr=args.mtr, calculation_mode="taxsim", max_year_workers=1)
    original_collect = pl.LazyFrame.collect
    original_states = federal_state._collect_state_plans
    original_schema = pl.LazyFrame.collect_schema
    summaries = []
    node_rows = []
    for label, selected in [("CA", ["CA"]), ("all", states)]:
        warmup = benchmark.build_cases(selected, [2022], max(100, len(selected)))
        calculate_taxes(warmup, **options)
        for n in args.rows:
            cases = benchmark.build_cases(selected, [2022], n)
            plain = []
            for _ in range(args.repeats):
                t = time.perf_counter()
                reference = calculate_taxes(cases, **options)
                plain.append(time.perf_counter() - t)
            boundaries = Counter()
            counts = Counter()
            plans = []
            capture = n == min(args.rows)
            in_state_collection = False

            def collect(lf, *a, **kw):
                if in_state_collection:
                    return original_collect(lf, *a, **kw)
                t = time.perf_counter()
                out = original_collect(lf, *a, **kw)
                boundaries["collect"] += time.perf_counter() - t
                counts["collect"] += 1
                if capture:
                    plans.append(("collect", lf, out))
                return out

            def collect_states(lfs, executor):
                nonlocal in_state_collection
                t = time.perf_counter()
                in_state_collection = True
                try:
                    outs = original_states(lfs, executor)
                finally:
                    in_state_collection = False
                boundaries["state_collect"] += time.perf_counter() - t
                counts["state_collect"] += 1
                counts["state_plans"] += len(lfs)
                if capture:
                    plans.extend(("state", lf, out) for lf, out in zip(lfs, outs))
                return outs

            def schema(lf, *a, **kw):
                t = time.perf_counter()
                out = original_schema(lf, *a, **kw)
                boundaries["schema"] += time.perf_counter() - t
                counts["schema"] += 1
                return out

            with patch.object(pl.LazyFrame, "collect", collect), patch.object(federal_state, "_collect_state_plans", collect_states), patch.object(pl.LazyFrame, "collect_schema", schema):
                t = time.perf_counter()
                instrumented = calculate_taxes(cases, **options)
                elapsed = time.perf_counter() - t
            assert_frame_equal(reference, instrumented)
            row = dict(workload=label, states=len(selected), rows=n,
                       baseline_s=statistics.median(plain), instrumented_s=elapsed,
                       collect_s=boundaries["collect"], state_collect_s=boundaries["state_collect"],
                       schema_s=boundaries["schema"], outside_boundaries_s=elapsed-sum(boundaries.values()),
                       **{f"{k}_count": counts[k] for k in ("collect", "state_collect", "state_plans", "schema")})
            if capture:
                profiler = cProfile.Profile()
                profiler.runcall(calculate_taxes, cases, **options)
                profiler.dump_stats(str(args.output / f"{label}.pstats"))
                for variant in ("default", "no_cse"):
                    trials = []
                    failure = None
                    flags = pl.QueryOptFlags()
                    if variant == "no_cse":
                        flags.comm_subexpr_elim = False
                    for repeat in range(args.repeats):
                        totals = Counter()
                        for index, (kind, lf, expected) in enumerate(plans):
                            t = time.perf_counter()
                            try:
                                result, nodes = lf.profile(engine="in-memory", optimizations=flags)
                            except pl.exceptions.PolarsError as exc:
                                if variant == "default":
                                    raise
                                failure = f"plan {index}: {type(exc).__name__}: {str(exc).splitlines()[0]}"
                                break
                            duration = time.perf_counter()-t
                            assert_frame_equal(expected, result)
                            opt = sum(end-start for node, start, end in nodes.iter_rows() if node == "optimization") / 1e6
                            end_s = nodes["end"].max()/1e6
                            totals["wall_s"] += duration
                            totals["optimization_s"] += opt
                            totals["after_optimization_s"] += end_s-opt
                            for node, start, end in nodes.iter_rows():
                                node_rows.append(dict(workload=label, variant=variant, repeat=repeat,
                                                      plan=index, kind=kind, node=node, start_us=start, end_us=end))
                        if failure:
                            break
                        trials.append(totals)
                    if failure:
                        row[f"profile_{variant}_error"] = failure
                    else:
                        row.update({f"profile_{variant}_{key}": statistics.median(t[key] for t in trials)
                                    for key in trials[0]})
                row["profile_plans"] = len(plans)
            summaries.append(row)
            print(json.dumps(row), flush=True)
    keys = list(dict.fromkeys(key for row in summaries for key in row))
    with (args.output / "summary.csv").open("w") as f:
        writer = csv.DictWriter(f, fieldnames=keys)
        writer.writeheader()
        writer.writerows(summaries)
    with (args.output / "nodes.csv").open("w") as f:
        writer = csv.DictWriter(f, fieldnames=list(node_rows[0]))
        writer.writeheader()
        writer.writerows(node_rows)


if __name__ == "__main__":
    main()
