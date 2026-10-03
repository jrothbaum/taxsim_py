# Performance

`scripts/benchmark_comparison.py` runs taxsim_py, the compiled Fortran TAXSIM
and PolicyEngine-US on the same mixed batch of 42 states (tax year 2022), each
in its own process:

```bash
uv run --group test scripts/benchmark_comparison.py \
  --rows 1000 100000 --policyengine-rows 100 1000
```

| Model | Rows | Time | Peak memory |
| --- | ---: | ---: | ---: |
| taxsim_py | 1,000 | 1.4 s | 340 MB |
| taxsim_py | 100,000 | 2.1 s | 1.1 GB |
| Fortran TAXSIM | 1,000 | 0.02 s | 4 MB |
| Fortran TAXSIM | 100,000 | 1.7 s | 4 MB |
| PolicyEngine-US | 100 | 26 s | 1.9 GB |
| PolicyEngine-US | 1,000 | 28 s | 2.3 GB |

taxsim_py has a fixed cost of about 1 s per call and then runs at about
130,000 rows/s, against about 57,000 for the Fortran, so it is faster above
roughly 100,000 rows. It uses about 1.3-2.7 KB per row beyond a fixed ~300 MB
(250,000 rows about 2.1 GB), so split files of very many rows into chunks.
PolicyEngine's setup dominates its time and it needs about 0.3 MB per row.
