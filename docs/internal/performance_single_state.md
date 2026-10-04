# Single-state performance (internal)

Same harness and machine as [Performance](../performance.md), but every row is a
Massachusetts household (tax year 2022; Massachusetts has an ordinary number of tax
parameters). Not linked from the public docs.

```bash
uv run --group test scripts/benchmark_comparison.py --states MA \
  --engines taxsim_py taxsim --rows 1000 10000 15000 100000 200000 500000 1000000
```

Each cell shows runtime and peak memory:

| Model | 1,000 | 10,000 | 15,000 | 100,000 | 200,000 | 500,000 | 1,000,000 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| taxsim_py, defaults | 0.20 s<br>125 MiB | 0.27 s<br>186 MiB | 0.32 s<br>220 MiB | 0.85 s<br>692 MiB | 1.51 s<br>1,111 MiB | 3.62 s<br>1,483 MiB | 6.97 s<br>2,497 MiB |
| taxsim_py, `batch_rows=50_000, max_year_workers=4` (before the default changed to 50,000 rows with 8 workers) | - | - | - | - | 1.55 s<br>828 MiB | 3.78 s<br>1,230 MiB | 7.58 s<br>2,304 MiB |
| Fortran TAXSIM | 0.02 s<br>4 MiB | 0.17 s<br>4 MiB | 0.26 s<br>4 MiB | 1.74 s<br>4 MiB | 3.46 s<br>4 MiB | 8.64 s<br>3 MiB | 17.31 s<br>4 MiB |
| PolicyEngine TAXSIM | 4.06 s<br>1,034 MiB | 6.02 s<br>1,369 MiB | 8.69 s<br>1,469 MiB | 34.95 s<br>2,751 MiB | 66.62 s<br>2,746 MiB | 154.43 s<br>2,978 MiB | 312.97 s<br>3,312 MiB |

PolicyEngine also ran 20,000 rows (10.0 s, 1,582 MiB) and 50,000 rows (20.2 s, 2,493 MiB).
Its memory levels off near 3 GiB, and its time grows linearly (about 0.3 ms per row).

## Compared with the 42-state batch

- **Small inputs are much cheaper.** The fixed cost is building expressions for the
  states present, so one state takes 0.2 s against about 1.5 s for 42 states.
- **Large inputs are slower with defaults.** At 1,000,000 rows one state takes
  10.4 s and 3.9 GiB against 7.9 s and 2.9 GiB for the mixed batch: with one state
  there is no state split to spread the work across, so each batch is larger and the
  work less parallel. `batch_rows=50_000, max_year_workers=4` fixes that here (7.6 s,
  2.3 GiB); on the mixed batch the same setting mainly trades time for memory.
- **PolicyEngine fits far more rows.** One state needs about 2.7 GiB at 200,000 rows,
  where 42 states exceed 8 GiB beyond 15,000. Its memory levels off, but it is
  still 30-40 times slower than taxsim_py.

## Batch settings at 1,000,000 rows

Time and peak memory for taxsim_py before the default changed (up to 8 batches of at
least 25,000 rows):

| Batch rows, workers | 42 states | Massachusetts only |
| --- | ---: | ---: |
| defaults | 7.58 s<br>2,920 MiB | 10.29 s<br>3,345 MiB |
| 50,000, 4 | 8.65 s<br>2,723 MiB | 7.72 s<br>2,285 MiB |
| 50,000, 8 | 7.35 s<br>2,905 MiB | 6.89 s<br>2,494 MiB |
| 100,000, 4 | 8.86 s<br>2,740 MiB | 8.77 s<br>2,418 MiB |
| 100,000, 8 | 8.01 s<br>3,098 MiB | 8.66 s<br>2,808 MiB |

50,000 rows with 8 workers is the only setting that is never worse than the defaults:
the same speed and memory on the mixed batch, and 33% faster with 25% less memory on a
single state. The default is now that setting (a fixed 50,000 rows per batch, up to 8 at
a time), which the table at the top reflects. Rechecked from 1,000 to 1,000,000 rows,
the only regressions were 0.13 s at 100,000 rows (one state) and about 200 MiB at
500,000 rows (42 states).
