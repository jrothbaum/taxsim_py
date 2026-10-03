# Performance

Use `scripts/benchmark_execution.py` to time `calculate_taxes` on one state's
validation cases at several batch sizes, without the compiled TAXSIM oracle:

```bash
uv run scripts/benchmark_execution.py MA ME MI \
  --year 2020 --rows 10000 --chunks 0 5000 1000
```

`chunk=all` runs one dataframe. Other values split the same rows into batches.
The script verifies that every run produces the same `siitax`. Benchmarks go
through the public API, not internal functions.

Use `scripts/benchmark_multistate.py` to compare mixed-state, mixed-year API
scaling with the compiled Fortran executable:

```bash
uv run scripts/benchmark_multistate.py \
  --rows 100 1000 10000 100000 250000 --repeats 3
```

It reports the Python dataframe API, raw Fortran execution with pre-serialized
input, and Fortran end-to-end time including dataframe serialization and output
parsing. Each timing is the median after a warmup.

### Mixed API versus Fortran

Eight states (AL, CA, CT, MA, MI, MN, NY, OH), four years (1990, 2000,
2010, 2020), 32 state-year partitions, and three repeats per measurement.
`Python API` is the default call; `--calculation-mode` selects the mode timed.

| Rows | Python (taxsim mode) | Python (statutory mode) | Fortran execution | Fortran dataframe |
| ---: | ---: | ---: | ---: | ---: |
| 100 | 0.778 s | 2.731 s | 0.003 s | 0.004 s |
| 1,000 | 0.773 s | 2.840 s | 0.020 s | 0.022 s |
| 10,000 | 0.827 s | 2.947 s | 0.178 s | 0.196 s |
| 25,000 | 0.882 s | 3.112 s | 0.441 s | 0.486 s |
| 50,000 | 0.997 s | 3.375 s | 0.862 s | 0.971 s |
| 100,000 | 1.196 s | 3.974 s | 1.756 s | 1.926 s |
| 250,000 | 1.984 s | 6.574 s | 4.351 s | 4.850 s |

Python's fixed cost per call (building the federal and state expressions) is
about 0.75 s in `taxsim` mode and 2.7 s in `statutory` mode. In `taxsim` mode
the API overtakes the Fortran at roughly 50,000 rows and reaches about 126,000
rows/s at 250,000 rows; in `statutory` mode it does not overtake within 250,000
rows (38,000 rows/s there). The Fortran runs at about 57,000 rows/s.

Memory: about 290 MB fixed plus 1.3-2.7 KB per row (250,000 rows about 2.1 GB;
2,000,000 rows about 5.4 GB). Split files of very many rows (such as 100
million) into chunks before calling.

Before year-level concurrency, the same Python measurements were 0.818 seconds
at 100 rows, 0.898 seconds at 10,000 rows, and 1.389 seconds at 100,000 rows.
Running the four years concurrently improved those calls by 2.1-2.6x. An
eight-year test improved by 3.1-3.8x with eight workers.

A fused alternative built every year's federal plans sequentially, collected
them together, then collected all state-year plans together. It produced
identical results but took 0.73, 0.78, and 1.12 seconds at 100, 10,000, and
100,000 rows respectively, versus 0.32, 0.36, and 0.63 seconds for threaded
year resolvers. Parallel expression construction is more valuable than a
single cross-year `collect_all` for the current calculators.

Projecting the final state plans to `siitax` for the normal API path avoids
materializing state worksheet columns and cut the 100,000-row call by about
12%. Batching repeated federal and state default-column checks reduces lazy
schema resolution overhead, primarily benefiting small batches.

For marginal-rate calls, stacking base and upward-perturbed records into one
resolver call reduced 100, 10,000, and 100,000-row measurements from 0.61,
0.84, and 1.35 seconds to 0.32, 0.53, and 1.19 seconds respectively. Large
batches gain less because they still perform the arithmetic for twice as many
rows.

## Current Execution Model

Federal, payroll and state calculators accept `DataFrame` or `LazyFrame`. For
each feedback pass the resolver builds federal, payroll and the sales-tax
estimate as one lazy graph, collects it, partitions the result by state, and
collects every state's lazy plan together with `pl.collect_all`. Intermediate
passes collect only the deduction the next pass needs. Python loops in bracket
and category helpers build Polars expressions; they do not loop over taxpayer
rows.

`resolve_federal_and_state` stacks the forced-itemized and forced-standard
branches into one frame (the `force_itemize` column) and performs three
feedback iterations on it, so each input batch incurs three federal and three
state calculations. Given a mapping of state code to calculator, one federal
pass serves records from several states. Splitting a batch repeats the fixed
cost for every chunk.

`taxsim_py.calculate_taxes` is the public dispatcher. It collects a lazy input
once, partitions mixed-year inputs by year, and lazy-loads only the state
calculator modules needed by the batch. Within each year it uses the shared
federal pass and state partitioning described above. Independent year
partitions run in a thread pool capped by the number of years. Its default
maximum is Polars' thread pool size, so `POLARS_MAX_THREADS` caps both Polars
and the year workers; pass `max_year_workers` to override it. Scalar-year
calls do not create a pool. Across all 135,435 validation cases (47 years, 34
states) threaded and single-worker runs give identical results; threading took
6.1 seconds against 19.9 seconds.

The state validation harness puts every selected state's cases in one batch
with unique ids and makes one `calculate_taxes` call, so each year's federal
pass is shared across states and years run concurrently; the oracle runs on
the same batch in a background thread, with Oregon's 2019 records last (the
oracle carries Oregon's 2019 surplus credit into later records). A 2023 case
counts as an expected difference when our federal income tax, payroll tax or
standard deduction differs from the oracle's (known real-parameter choices). All 45 income-tax states (265,925 cases, run
with `idtl=2`) take about 25 seconds, one state 2-3 seconds. `POLARS_MAX_THREADS` limits
its threads like any other caller. Parameter YAML is parsed with the C loader.

Fixed cost per call is dominated by expression size, not rows (see
[Architecture: Expression Size](architecture.md#expression-size)). Bracket and
table helpers use `search_sorted`/`gather` lookups whose size is independent of
the table, and calculators store reused intermediates with
`engine.state.checkpoint`.

## Baseline Results

Measured on the development machine with 2020 validation cases repeated to the
requested row count:

| State | Rows | Chunk | Seconds | Federal calls | Nested federal calls |
| --- | ---: | ---: | ---: | ---: | ---: |
| Massachusetts | 1,000 | all | 5.973 | 6 | 6 |
| Massachusetts | 1,000 | 250 | 23.111 | 24 | 24 |
| Maine | 1,000 | all | 0.438 | 6 | 6 |
| Maine | 1,000 | 250 | 1.604 | 24 | 24 |
| Michigan | 1,000 | all | 0.238 | 6 | 0 |
| Michigan | 1,000 | 250 | 0.834 | 24 | 0 |
| Massachusetts | 10,000 | all | 6.627 | 6 | 6 |
| Maine | 10,000 | all | 0.901 | 6 | 6 |
| Michigan | 10,000 | all | 0.579 | 6 | 0 |

After exposing federal taxable unemployment and materializing Massachusetts
form boundaries, the same 1,000-row Massachusetts run takes 0.353 seconds with
six federal calls and no nested federal calls. A direct Massachusetts state
calculation fell from about 0.9 seconds to about 0.02 seconds. The 10,000-row
run takes 0.713 seconds, down from 6.627 seconds.

After combining federal and payroll into one lazy graph per feedback pass and
migrating the remaining unemployment consumers, current measurements are:

| State | Rows | Seconds | Nested federal calls |
| --- | ---: | ---: | ---: |
| Massachusetts | 1,000 | 0.359 | 0 |
| Maine | 1,000 | 0.265 | 0 |
| Michigan | 1,000 | 0.221 | 0 |
| Massachusetts | 10,000 | 0.588 | 0 |
| Maine | 10,000 | 0.482 | 0 |
| Michigan | 10,000 | 0.399 | 0 |
| Massachusetts | 100,000 | 2.901 | 0 |
| Michigan | 100,000 | 2.703 | 0 |

These results show that small row chunks multiply fixed-point overhead. They do
not improve the current hybrid lazy/eager execution.

After stacking both itemize branches, sharing federal passes, lookup-based
bracket and table helpers, and the C YAML loader (three federal and three state
calls per batch):

| State | Rows | Seconds |
| --- | ---: | ---: |
| Massachusetts | 1,000 | 0.156 |
| Maine | 1,000 | 0.116 |
| Michigan | 1,000 | 0.097 |
| Massachusetts | 10,000 | 0.222 |
| Maine | 10,000 | 0.178 |
| Michigan | 10,000 | 0.145 |
| Massachusetts | 100,000 | 0.844 |
| Michigan | 100,000 | 0.590 |

Validation: Minnesota alone (6,204 cases, 1977-2023) runs in about 1.8
seconds; all 31 states (110,525 cases) in about 10 seconds; the federal suite
(45,105 cases) in about 5 seconds.

### State partitions (September 2026)

Measured on a mixed frame of all 30 states' 2015 validation cases (shuffled,
repeated to the row count), full resolve including both itemize branches and
three passes:

| Resolver | 1,000 rows | 10,000 rows | 100,000 rows |
| --- | ---: | ---: | ---: |
| Filter per state, eager state calculators | 1.49 s | 1.69 s | 2.80 s |
| Partition by state, lazy plans via `collect_all`, fused passes | 0.52 s | 0.58 s | 1.21 s |
| Plus batched `with_defaults` and CT/NY expression trimming | 0.43 s | 0.50 s | 1.12 s |

For 1995 cases: 1.33 s to 0.31 s at 1,000 rows, 2.42 s to 0.85 s at 100,000.

Findings:

- A state plan's collect time is the same for 1 row as for 1,000 and grows with
  plan size (about half query optimization, half per-expression dispatch).
  Shrinking a state's expressions is what lowers it; splitting a state's rows
  into more plans (for example by filing status) adds a fixed cost per plan.
- Polars' default optimizations are faster than running with them off.
- `"x" in df.columns` on a `LazyFrame` resolves the whole plan's schema; 476
  such checks took most of plan-building time. Use `with_defaults` for column
  lists, and `df.collect_schema().names()` once where a check is needed.
- Re-running only records whose deduction changed between passes gave identical
  results but no speedup, because the per-plan cost dominates.
- Connecticut: checkpointing its minimum-tax intermediates and looking up the
  property-credit phaseout with constant thresholds cut its plan from 65k to
  25k characters and 35 ms to 13 ms. New York: `tier_values` for real-law
  recapture and credit checkpoints cut 23 ms to 19 ms.

Validation: all 31 states (110,525 cases) in about 7 seconds.

The original baseline predates the `taxable_unemployment` federal output.
Alabama, DC, Georgia, Hawaii, Idaho, Indiana, Kentucky, Maine, and Massachusetts
now consume that column directly. State calculators no longer invoke nested
federal calculations.

## Further Work

State calculators run lazily; `checkpoint` columns are their materialization
points within one plan. The slowest remaining plans per call are New York
(about 19 ms), Connecticut, Minnesota and Massachusetts (about 12 ms each).

Continue exposing commonly reconstructed federal intermediates as federal
output columns so state calculators do not create nested federal plans.

Benchmark before and after each change. Compare output values, call counts,
elapsed time, and peak memory on both small and large batches.
