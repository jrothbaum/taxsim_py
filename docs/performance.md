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
| taxsim_py | 1.4 s<br>340 MB | 1.4 s<br>390 MB | 2.1 s<br>1.1 GB | 2.7 s<br>1.9 GB | 4.6 s<br>2.8 GB | 9.0 s<br>4.1 GB |
| Fortran TAXSIM | 0.02 s<br>4 MB | 0.18 s<br>4 MB | 1.7 s<br>4 MB | 3.4 s<br>4 MB | 8.5 s<br>4 MB | 16.9 s<br>4 MB |
| PolicyEngine-US | 28 s<br>2.3 GB | 41 s<br>5.9 GB | – | – | – | – |

taxsim_py has a fixed cost of about 1 s per call and then runs at about
110,000 rows/s, against about 57,000 for the Fortran, so it is faster above
roughly 100,000 rows. It uses about 1.3-2.7 KB per row beyond a fixed ~300 MB
(1,000,000 rows about 4.1 GB), so split files of very many rows into chunks.
PolicyEngine's setup dominates its time and it needs about 0.4 MB per row.
