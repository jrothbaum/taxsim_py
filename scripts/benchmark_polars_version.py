"""Run identical warmed API workloads in independently installed Polars versions.

Use --output per environment; --reference checks saved results from another run.
"""
from __future__ import annotations

import os as _os

# These scripts exercise the Python reference implementation (taxsim mode, worksheets, Polars execution).
_os.environ.setdefault("TAXSIM_PY_ENGINE", "python")

import argparse
import faulthandler
import json
import statistics
import sys
import time
from concurrent.futures import ThreadPoolExecutor
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
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--reference", type=Path)
    parser.add_argument("--rows", nargs="+", type=int, default=[1000, 10000])
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--engine", choices=["auto", "streaming", "in-memory"], default="auto")
    parser.add_argument("--workers", type=int, help="API worker limit (omitted uses package defaults)")
    parser.add_argument("--workloads", nargs="+", choices=["CA", "all"], default=["CA", "all"])
    parser.add_argument("--timeout", type=float, default=0, help="Exit with a traceback if a calculation exceeds this many seconds")
    parser.add_argument("--collect-batch-size", type=int, default=0,
                        help="Limit the number of plans per collect_all call (0 keeps the original)")
    parser.add_argument("--individual-collect", action="store_true",
                        help="Execute each collect_all input using LazyFrame.collect instead")
    parser.add_argument("--collect-workers", type=int, default=1,
                        help="Python workers when using individual collect")
    parser.add_argument("--no-subplan-elim", action="store_true")
    parser.add_argument("--union-collect", action="store_true",
                        help="Experimental: concatenate state plans into one streaming sink")
    parser.add_argument("--combined-collect", action="store_true",
                        help="Reproduce the previous combined collect_all strategy")
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    pl.Config.set_engine_affinity(args.engine)
    original_all = pl.collect_all
    flags = pl.QueryOptFlags()
    if args.no_subplan_elim:
        flags.comm_subplan_elim = False

    def collect_all(plans, **kwargs):
        plans = list(plans)
        if args.no_subplan_elim:
            kwargs["optimizations"] = flags
        if args.union_collect:
            return [pl.concat(plans, how="diagonal_relaxed").collect(**kwargs)] if plans else []
        if args.individual_collect:
            def collect(plan):
                return plan.collect(**kwargs)
            if args.collect_workers > 1 and len(plans) > 1:
                with ThreadPoolExecutor(max_workers=args.collect_workers) as pool:
                    return list(pool.map(collect, plans))
            return [collect(plan) for plan in plans]
        size = args.collect_batch_size or len(plans)
        return [frame for offset in range(0, len(plans), size)
                for frame in original_all(plans[offset:offset+size], **kwargs)] if plans else []

    experimental = (args.individual_collect or args.union_collect or args.combined_collect
                    or args.no_subplan_elim or args.collect_batch_size)
    if experimental:
        with patch.object(federal_state, "_collect_state_plans",
                          lambda plans, executor: collect_all(plans)):
            run(args)
    else:
        run(args)


def run(args):
    states = [p.name.upper() for p in sorted((ROOT / "src/taxsim_py/parameters/states").iterdir())
              if p.name.upper() not in {"AK", "TN", "TX", "WA", "NH"}]
    options = dict(mtr=11, calculation_mode="taxsim", max_year_workers=args.workers)
    results = []
    (args.output / "timings.json").write_text("[]")
    for label, selected in [("CA", ["CA"]), ("all", states)]:
        if label not in args.workloads:
            continue
        print(f"Warming {label}, 100 rows", file=sys.stderr, flush=True)
        if args.timeout:
            faulthandler.dump_traceback_later(args.timeout, exit=True)
        calculate_taxes(benchmark.build_cases(selected, [2022], 100), **options)
        faulthandler.cancel_dump_traceback_later()
        for n in args.rows:
            cases = benchmark.build_cases(selected, [2022], n)
            samples = []
            for _ in range(args.repeats):
                if args.timeout:
                    faulthandler.dump_traceback_later(args.timeout, exit=True)
                started = time.perf_counter()
                output = calculate_taxes(cases, **options)
                samples.append(time.perf_counter()-started)
                faulthandler.cancel_dump_traceback_later()
            name = f"{label}-{n}.arrow"
            if args.reference:
                assert_frame_equal(pl.read_ipc(args.reference / name), output,
                                   rel_tol=1e-10, abs_tol=1e-8)
            output.write_ipc(args.output / name)
            result = dict(polars=pl.__version__, threads=pl.thread_pool_size(),
                          engine=args.engine, workers=args.workers, workload=label,
                          collect_batch_size=args.collect_batch_size,
                          individual_collect=args.individual_collect,
                          collect_workers=args.collect_workers,
                          no_subplan_elim=args.no_subplan_elim,
                          union_collect=args.union_collect,
                          combined_collect=args.combined_collect,
                          states=len(selected), rows=n, median_s=statistics.median(samples),
                          samples_s=samples, reference_verified=bool(args.reference))
            results.append(result)
            (args.output / "timings.json").write_text(json.dumps(results, indent=2))
            print(json.dumps(result), flush=True)


if __name__ == "__main__":
    main()
