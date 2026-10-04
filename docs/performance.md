# Performance

`scripts/benchmark_comparison.py` runs taxsim_py, the compiled Fortran TAXSIM
and PolicyEngine-US on the same mixed batch of 42 states (tax year 2022), each
in its own process:

```bash
uv run --group test scripts/benchmark_comparison.py \
  --rows 1000 10000 1000000 --policyengine-rows 1000 10000
```

Time and peak memory by number of rows:

| Model | 1,000 | 10,000 | 100,000 | 200,000 | 500,000 | 1,000,000 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| taxsim_py | 1.3 s<br>330 MB | 1.3 s<br>380 MB | 2.1 s<br>1.1 GB | 2.8 s<br>1.9 GB | 4.7 s<br>2.9 GB | 9.1 s<br>4.4 GB |
| taxsim_py, low memory | 1.3 s<br>350 MB | 1.4 s<br>380 MB | 2.0 s<br>950 MB | 2.6 s<br>1.6 GB | 6.4 s<br>1.8 GB | 11.4 s<br>2.0 GB |
| Fortran TAXSIM | 0.02 s<br>4 MB | 0.18 s<br>4 MB | 1.7 s<br>4 MB | 3.4 s<br>4 MB | 8.5 s<br>4 MB | 16.9 s<br>4 MB |
| PolicyEngine-US | 28 s<br>2.3 GB | 41 s<br>5.9 GB | – | – | – | – |

taxsim_py has a fixed cost of about 1 s per call and then runs at about
110,000 rows/s, against about 57,000 for the Fortran, so it is faster above
roughly 100,000 rows. By default it favours speed: rows are processed in up to
eight parallel batches, which peaks at about 4 GB per million rows. For less
memory at some cost in time, set the batch size and number of workers, for
example `calculate_taxes(df, batch_rows=50_000, max_year_workers=4)` (the
"low memory" row; 3,000,000 rows take 33 s and 3.2 GB). Peak memory then
follows `batch_rows` times the number of workers, not the number of rows.
PolicyEngine's setup dominates its time and it needs about 0.4 MB per row.
