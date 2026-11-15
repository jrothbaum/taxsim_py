# Performance

The default engine is the compiled calculator. On a mixed 42-state batch of one million rows,
including marginal rates, it takes **0.56 seconds and 1.1 GiB RAM**, against 16.6 seconds for the
Fortran TAXSIM and about 44 seconds for PolicyEngine's TAXSIM runner on only 15,000 rows, where it already uses 8 GiB. A lazy
input streams into any Polars sink with memory that depends mainly on the thread count and hardly
on the number of rows. Results depend on the data and hardware.

## Comparison with TAXSIM and PolicyEngine

Tax year 2022, 42 states, taxes and weighted-wage marginal rates. Each cell shows runtime and
peak memory for a single first call in a fresh process, with 16 Polars threads and default settings:

| Model | 1,000 | 10,000 | 15,000 | 100,000 | 200,000 | 500,000 | 1,000,000 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| taxsim_py, rust (default) | 0.03 s<br>174 MiB | 0.04 s<br>182 MiB | 0.04 s<br>187 MiB | 0.08 s<br>269 MiB | 0.14 s<br>370 MiB | 0.32 s<br>654 MiB | 0.56 s<br>1,134 MiB |
| taxsim_py, Python engine | 1.05 s<br>335 MiB | 1.23 s<br>474 MiB | 1.34 s<br>542 MiB | 2.71 s<br>744 MiB | 3.17 s<br>1,024 MiB | 4.98 s<br>1,946 MiB | 8.48 s<br>2,987 MiB |
| Fortran TAXSIM | 0.02 s<br>4 MiB | 0.17 s<br>4 MiB | 0.25 s<br>4 MiB | 1.69 s<br>4 MiB | 3.33 s<br>4 MiB | 8.29 s<br>4 MiB | 16.62 s<br>4 MiB |
| PolicyEngine TAXSIM | 19.03 s<br>2,318 MiB | 31.19 s<br>5,943 MiB | 43.76 s<br>8,030 MiB | — | — | — | — |

Memory is peak process RSS (for Fortran TAXSIM, its executable only).
PolicyEngine's runner needs about 0.5 MiB per row. At 15,000 rows it uses 8,030 MiB, just inside the 8 GiB budget; at 20,000
rows it reached the budget (8,192 MiB) and was stopped, so larger runs were not attempted and those cells are dashes.

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
