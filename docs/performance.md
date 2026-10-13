# Performance

The default engine is the compiled calculator (`engine="rust"`). On a mixed 42-state batch
of one million rows, including marginal rates, it takes **0.46 seconds and 1.1 GiB RAM**. The
Python engine (`engine="python"`, the reference implementation) takes 7.13 seconds and 2.8 GiB,
the Fortran TAXSIM 17.3 seconds, and PolicyEngine's TAXSIM runner about 45 seconds for just
15,000 rows. A lazy input streams into any Polars sink with memory that depends mainly on the
thread count and hardly on the number of rows (see below). Results depend on the data and hardware.

## Comparison with TAXSIM and PolicyEngine

Tax year 2022, 42 states, taxes and weighted-wage marginal rates, statutory mode (the Fortran
TAXSIM and PolicyEngine compute their own versions of the same taxes). Each cell shows runtime
and peak process memory for a single first call in a fresh process, with 16 Polars threads and
the default settings:

| Model | 1,000 | 10,000 | 15,000 | 100,000 | 200,000 | 500,000 | 1,000,000 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| taxsim_py, rust (default) | 0.03 s<br>165 MiB | 0.03 s<br>174 MiB | 0.03 s<br>178 MiB | 0.07 s<br>258 MiB | 0.12 s<br>353 MiB | 0.24 s<br>638 MiB | 0.46 s<br>1,103 MiB |
| taxsim_py, python | 0.88 s<br>341 MiB | 1.09 s<br>445 MiB | 1.16 s<br>508 MiB | 2.15 s<br>706 MiB | 2.55 s<br>960 MiB | 4.07 s<br>1,821 MiB | 7.13 s<br>2,893 MiB |
| Fortran TAXSIM | 0.03 s<br>4 MiB | 0.18 s<br>4 MiB | 0.27 s<br>4 MiB | 1.78 s<br>4 MiB | 3.53 s<br>4 MiB | 8.87 s<br>4 MiB | 17.32 s<br>4 MiB |
| PolicyEngine TAXSIM | 19.61 s<br>2,306 MiB | 32.81 s<br>5,940 MiB | 44.77 s<br>8,034 MiB | n/a | n/a | n/a | n/a |

PolicyEngine's runner (unmodified) hit the 8 GiB memory budget at 15,000 rows; larger runs were
stopped at the limit after about 50 seconds. Its figures are from an earlier measurement; the
Fortran TAXSIM figures come from the Polars 2.0 run below, and its memory covers only the
executable. Raw results: [rust_vs_python_2026-10-12.csv](benchmarks/rust_vs_python_2026-10-12.csv).

## Streaming a file

`calculate_taxes(pl.scan_parquet(path), year=2022, mtr=85).sink_parquet(out)` runs the same
plan without loading the file. On 4 million rows without marginal rates (about 1.0 s,
1.6 GB with 16 threads) and 16 million rows (3.3 s, 2.6 GB), memory depends mainly on the number
of Polars threads (it grew from 1.6 GB to 2.6 GB for four times the rows): with `POLARS_MAX_THREADS=8` the 16 million rows
take 3.7 s and 1.4 GB, and with 4 threads 6.0 s and 0.76 GB.

## Python engine

On Polars 2.0.0 with the in-memory engine, a mixed 42-state batch of one million
rows takes **7.18 seconds and 2.09 GiB RAM**. The streaming engine takes
**8.34 seconds and 2.50 GiB** on the same input. Results depend on the data and hardware.

### Benchmarks

Rerun on October 12, 2026 with the default independent state-collection strategy.
Tax year 2022, 42 states. All models calculate taxes and weighted-wage marginal
rates. Each cell shows runtime and peak memory:

| Model / engine | 1,000 | 10,000 | 15,000 | 100,000 | 200,000 | 500,000 | 1,000,000 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| taxsim_py, in-memory | 0.87 s<br>286 MiB | 1.04 s<br>381 MiB | 1.18 s<br>456 MiB | 2.12 s<br>569 MiB | 2.52 s<br>767 MiB | 4.06 s<br>1,413 MiB | 7.18 s<br>2,137 MiB |
| taxsim_py, streaming | 1.92 s<br>457 MiB | 2.29 s<br>568 MiB | 2.31 s<br>704 MiB | 3.86 s<br>762 MiB | 4.41 s<br>1,034 MiB | 6.00 s<br>1,669 MiB | 8.34 s<br>2,556 MiB |
| Fortran TAXSIM | 0.03 s<br>4 MiB | 0.18 s<br>4 MiB | 0.27 s<br>4 MiB | 1.78 s<br>4 MiB | 3.53 s<br>4 MiB | 8.87 s<br>4 MiB | 17.32 s<br>4 MiB |

These are single first-call timings in fresh worker processes, using statutory
mode, Polars 2.0.0 and 16 Polars threads. The API's default worker and batch
settings are used. Python engine imports and input generation are outside the
timer. Memory is peak process RSS, except Fortran's figure covers only its executable.
All runs completed within the 8 GiB process-tree budget. This rerun used an isolated
Polars 2.0 environment; it does not change the project's installed Polars version.
Separate checks confirmed matching output columns, dtypes, row order and values
between both engines across the full row-count grid (float tolerances: relative
`1e-10`, absolute `1e-8`). Verification is outside the timed worker processes.

[Raw results](benchmarks/polars_2_engines_2026-10-12.csv) and
[configuration](benchmarks/polars_2_engines_2026-10-12.json) are included for reproduction.

## Memory Controls

`batch_rows` limits input rows per calculation batch; `max_year_workers`
limits concurrent batches and state-plan collection:

```python
calculate_taxes(df, batch_rows=50_000, max_year_workers=4)
```

Reducing either limit can lower peak RAM, especially when many rows belong to
one state. Runtime varies with the workload. The full input and output frames
still occupy memory proportional to the total row count.

## Execution Strategy

Small mixed-state batches materialize each federal feedback pass once, then
collect independent state plans with up to eight Python workers. Larger or
mixed-year workloads use parallel API batches; each batch collects its states
serially to avoid nested worker pools. A single state plan is collected directly.
`max_year_workers=1` disables both levels of Python parallelism.

Each plan uses the configured Polars engine. The package does not change
`POLARS_MAX_THREADS` or engine affinity. Set `POLARS_MAX_THREADS` before importing
Polars to tune its thread pool independently of the Python workers.

For local comparisons, `scripts/benchmark_polars_version.py` times complete calculations,
and `scripts/benchmark_streaming.py` screens streaming collection strategies in
bounded subprocesses with output verification.

## Run the Benchmark

Use a Python environment with `polars==2.0.0`, `pyyaml` and `psutil` to reproduce
the table. With that environment activated:

```bash
POLARS_MAX_THREADS=16 python scripts/benchmark_comparison.py \
  --engines taxsim_py taxsim --polars-engines in-memory streaming \
  --rows 1000 10000 15000 100000 200000 500000 1000000
```

Use `--engines policyengine --policyengine-rows 1000 10000 15000 100000`
in an environment with PolicyEngine's dependencies, or
`--batch-rows 50000 --workers 4` to benchmark memory controls.
The Linux harness monitors process-tree RAM with an 8 GiB budget, a 2 GiB available
RAM reserve and a 600-second timeout. It stops a measurement at a limit and skips
larger samples. These are monitored safeguards, not a hard memory quota.
