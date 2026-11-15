# Performance

The default engine is the compiled calculator. On a mixed 42-state batch of one million rows,
including marginal rates, it takes **0.50 seconds and 1.1 GiB RAM**, against 16.6 seconds for the
Fortran TAXSIM and about 44 seconds for PolicyEngine's TAXSIM runner on only 15,000 rows, where it already uses 8 GiB. A lazy
input streams into any Polars sink with memory that depends mainly on the thread count and hardly
on the number of rows. Results depend on the data and hardware.

## Comparison with TAXSIM and PolicyEngine

Tax year 2022, 42 states, taxes and weighted-wage marginal rates. Each cell shows runtime and
peak memory for a single first call in a fresh process, with 16 Polars threads and default settings. Each cell is the median of three runs; the runs agree to within a few percent (all runs are in [`runs_2026-11-14.csv`](https://github.com/jrothbaum/taxsim_py/blob/main/docs/benchmarks/runs_2026-11-14.csv)):

| Model | 1,000 | 10,000 | 15,000 | 100,000 | 200,000 | 500,000 | 1,000,000 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| taxsim_py, rust (default) | 0.04 s<br>177 MiB | 0.03 s<br>182 MiB | 0.04 s<br>187 MiB | 0.08 s<br>269 MiB | 0.13 s<br>365 MiB | 0.27 s<br>658 MiB | 0.50 s<br>1,124 MiB |
| taxsim_py, Python engine | 1.02 s<br>346 MiB | 1.21 s<br>469 MiB | 1.30 s<br>551 MiB | 2.58 s<br>742 MiB | 2.99 s<br>1,028 MiB | 4.67 s<br>1,953 MiB | 8.10 s<br>3,064 MiB |
| Fortran TAXSIM | 0.02 s<br>4 MiB | 0.17 s<br>4 MiB | 0.25 s<br>4 MiB | 1.66 s<br>4 MiB | 3.34 s<br>4 MiB | 8.25 s<br>4 MiB | 16.56 s<br>4 MiB |
| PolicyEngine TAXSIM | 19.06 s<br>2,318 MiB | 30.78 s<br>5,943 MiB | 43.72 s<br>8,030 MiB | — | — | — | — |

Memory is peak process RSS (for Fortran TAXSIM, its executable only).
PolicyEngine's runner needs about 0.5 MiB per row. At 15,000 rows it uses 8,030 MiB, just inside the 8 GiB budget; at 20,000
rows it reached the budget (about 8,200 MiB) in all three runs and was stopped, so larger runs were not attempted and those cells are dashes.

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
