# Performance

`scripts/benchmark_multistate.py` times `calculate_taxes` against the compiled
Fortran TAXSIM on a mixed batch of 42 income-tax states (tax year 2020),
taking the median of three runs:

```bash
uv run scripts/benchmark_multistate.py --states AL AR ... --years 2020 \
  --rows 1000 10000 100000 250000 --repeats 3 [--calculation-mode statutory]
```

| Rows | taxsim_py, `taxsim` mode | taxsim_py, `statutory` mode | Fortran TAXSIM |
| ---: | ---: | ---: | ---: |
| 1,000 | 1.24 s | 2.22 s | 0.02 s |
| 10,000 | 1.32 s | 2.49 s | 0.18 s |
| 100,000 | 2.03 s | 3.96 s | 1.72 s |
| 250,000 | 3.19 s | 6.89 s | 4.29 s |

taxsim_py has a fixed cost of 1-2 s per call (building the calculations), then
runs at about 100,000 rows/s in `taxsim` mode and 40,000 rows/s in `statutory`
mode. The Fortran runs at about 57,000 rows/s. In `taxsim` mode taxsim_py is
faster above roughly 150,000 rows.

Memory is about 290 MB plus 1.3-2.7 KB per row (250,000 rows about 2.1 GB).
For very large files, split them into chunks before calling.
