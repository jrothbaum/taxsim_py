# Performance

With default settings, a mixed 42-state batch of one million rows takes about
**7.4 seconds and 2.8 GiB RAM**. Results depend on the data and hardware.

## Benchmarks

Tax year 2022, 42 states. All models calculate taxes and weighted-wage marginal
rates. Each cell shows runtime and peak memory:

| Model | 1,000 | 10,000 | 15,000 | 100,000 | 200,000 | 500,000 | 1,000,000 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| taxsim_py | 1.50 s<br>357 MiB | 1.69 s<br>439 MiB | 1.73 s<br>518 MiB | 2.19 s<br>683 MiB | 2.54 s<br>979 MiB | 4.16 s<br>1,804 MiB | 7.37 s<br>2,888 MiB |
| Fortran TAXSIM | 0.02 s<br>4 MiB | 0.18 s<br>4 MiB | 0.26 s<br>4 MiB | 1.73 s<br>4 MiB | 3.48 s<br>4 MiB | 8.65 s<br>4 MiB | 17.22 s<br>4 MiB |
| PolicyEngine TAXSIM | 19.61 s<br>2,306 MiB | 32.81 s<br>5,940 MiB | 44.77 s<br>8,034 MiB | - | - | - | - |

These are first-call timings; taxsim_py uses statutory mode and 16 Polars
threads. Python engine imports are outside the timer. Memory is peak process
RSS, except Fortran's figure covers only its executable.
PolicyEngine uses its unmodified runner. Under the 8 GiB budget it completes 15,000
rows, and every larger run exceeded 8 GiB: 20,000+ rows
were stopped at the limit after ~50 s.

## Memory Controls

`batch_rows` limits input rows per calculation batch; `max_year_workers`
limits concurrent batches:

```python
calculate_taxes(df, batch_rows=50_000, max_year_workers=4)
```

Reducing either limit can lower peak RAM, especially when many rows belong to
one state. Runtime varies with the workload. The full input and output frames
still occupy memory proportional to the total row count.

## Run the Benchmark

Reproduce the Python and Fortran measurements:

```bash
uv run --group test scripts/benchmark_comparison.py \
  --engines taxsim_py taxsim --rows 1000 10000 100000 200000 500000 1000000
```

Use `--engines policyengine --policyengine-rows 1000 10000 15000 100000`
for PolicyEngine, or `--batch-rows 50000 --workers 4` to benchmark memory controls.
The Linux harness monitors process-tree RAM with an 8 GiB budget, a 2 GiB available
RAM reserve and a 600-second timeout. It stops a measurement at a limit and skips
larger samples. These are monitored safeguards, not a hard memory quota.
