# Performance

`scripts/benchmark_multistate.py` times `calculate_taxes` against the compiled
Fortran TAXSIM on a mixed batch of 42 income-tax states (tax year 2020),
taking the median of three runs:

```bash
uv run scripts/benchmark_multistate.py --states AL AR ... --years 2020 \
  --rows 1000 10000 100000 250000 --repeats 3 [--calculation-mode statutory]
```

| Rows | taxsim_py, `statutory` mode (default) | taxsim_py, `taxsim` mode | Fortran TAXSIM |
| ---: | ---: | ---: | ---: |
| 1,000 | 1.20 s | 1.09 s | 0.02 s |
| 10,000 | 1.28 s | 1.28 s | 0.17 s |
| 100,000 | 1.94 s | 1.92 s | 1.74 s |
| 250,000 | 2.99 s | 3.11 s | 4.31 s |

taxsim_py has a fixed cost of about 1 s per call (building the calculations),
then runs at about 130,000 rows/s in either mode. The Fortran runs at about
57,000 rows/s, so taxsim_py is faster above roughly 100,000 rows.

Memory is about 290 MB plus 1.3-2.7 KB per row (250,000 rows about 2.1 GB).
For very large files, split them into chunks before calling.
