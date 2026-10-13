# Performance

The default engine is the compiled calculator. On a mixed 42-state batch of one million rows,
including marginal rates, it takes **0.46 seconds and 1.1 GiB RAM**, against 17.3 seconds for the
Fortran TAXSIM and about 45 seconds for PolicyEngine's TAXSIM runner on only 15,000 rows. A lazy
input streams into any Polars sink with memory that depends mainly on the thread count and hardly
on the number of rows. Results depend on the data and hardware.

## Comparison with TAXSIM and PolicyEngine

Tax year 2022, 42 states, taxes and weighted-wage marginal rates. Each cell shows runtime and
peak memory for a single first call in a fresh process, with 16 Polars threads and default settings:

| Model | 1,000 | 10,000 | 15,000 | 100,000 | 200,000 | 500,000 | 1,000,000 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| taxsim_py, rust (default) | 0.03 s<br>165 MiB | 0.03 s<br>174 MiB | 0.03 s<br>178 MiB | 0.07 s<br>258 MiB | 0.12 s<br>353 MiB | 0.24 s<br>638 MiB | 0.46 s<br>1,103 MiB |
| taxsim_py, Python reference engine | 0.88 s<br>341 MiB | 1.09 s<br>445 MiB | 1.16 s<br>508 MiB | 2.15 s<br>706 MiB | 2.55 s<br>960 MiB | 4.07 s<br>1,821 MiB | 7.13 s<br>2,893 MiB |
| Fortran TAXSIM | 0.03 s<br>4 MiB | 0.18 s<br>4 MiB | 0.27 s<br>4 MiB | 1.78 s<br>4 MiB | 3.53 s<br>4 MiB | 8.87 s<br>4 MiB | 17.32 s<br>4 MiB |
| PolicyEngine TAXSIM | 19.61 s<br>2,306 MiB | 32.81 s<br>5,940 MiB | 44.77 s<br>8,034 MiB |  |  |  |  |

Memory is peak process RSS (for Fortran TAXSIM, its executable only).
PolicyEngine's runner hit the 8 GiB memory budget at 15,000 rows and larger runs were stopped, so
those cells are blank; its figures are from an earlier measurement. Raw results:
[taxsim_py](benchmarks/rust_vs_python_2026-10-12.csv) and
[Fortran TAXSIM](benchmarks/polars_2_engines_2026-10-12.csv).

## Streaming a file

`calculate_taxes(pl.scan_parquet(path), year=2022, mtr=85).sink_parquet(out)` runs the same
plan without loading the file. On 4 million rows without marginal rates (about 1.0 s,
1.6 GB with 16 threads) and 16 million rows (3.3 s, 2.6 GB), memory depends mainly on the number
of Polars threads (it grew from 1.6 GB to 2.6 GB for four times the rows): with `POLARS_MAX_THREADS=8` the 16 million rows
take 3.7 s and 1.4 GB, and with 4 threads 6.0 s and 0.76 GB.

## Memory

With the default engine, memory is set mainly by the Polars thread count: set `POLARS_MAX_THREADS`
before importing Polars to trade speed for memory (see the streaming numbers above). For files
larger than memory, pass a `LazyFrame` and sink the result.

## Run the Benchmark

With `psutil` installed (`uv sync --group test`):

```bash
POLARS_MAX_THREADS=16 uv run scripts/benchmark_comparison.py --engines taxsim_py taxsim \
  --rows 1000 10000 15000 100000 200000 500000 1000000
```

Add `--engines policyengine --policyengine-rows 1000 10000 15000` in an environment with
PolicyEngine's dependencies. The Linux harness monitors process-tree RAM with an 8 GiB budget,
a 2 GiB available RAM reserve and a 600-second timeout, and stops a measurement at a limit.
