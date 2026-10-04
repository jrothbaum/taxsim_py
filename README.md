# taxsim-py

`taxsim-py` is a dataframe-oriented Python implementation of NBER TAXSIM,
independent of and not affiliated with NBER. It calculates federal income tax,
payroll tax, and state income tax for household records, using Polars
`DataFrame` or `LazyFrame` inputs and clear variable names (TAXSIM's `v1`-style
names only on request).

**[Documentation](https://jrothbaum.github.io/taxsim_py/)** ·
**[Try the calculator in your browser](https://jrothbaum.github.io/taxsim_py/calculator/)**

## Install

```bash
pip install taxsim-py
```

## Basic use

Every row needs `mstat` and `state` (TAXSIM codes, or Census FIPS with
`state_id_type="fips"`). Include `year` in the data or pass `year=`.

```python
import polars as pl

from taxsim_py import calculate_taxes

households = pl.DataFrame(
    {
        "year": [2021, 2021],
        "state": [6, 36],       # California, New York
        "mstat": [1, 2],        # single, married filing jointly
        "page": [45, 50],
        "pwages": [50_000, 80_000],
        "swages": [0, 40_000],
        "depx": [0, 2],
    }
)

taxes = calculate_taxes(households)
print(taxes.select("fiitax", "fica", "siitax"))
```

The result is the input plus `fiitax` (federal income tax), `fica` (payroll
taxes), `siitax` (state income tax) and other outputs, and `frate`/`srate`
(marginal rates) when requested. Missing inputs default to zero (`dep13`,
`dep17` and `dep18` default to `depx`). `idtl=2` adds detailed federal and state
worksheets, `taxsim_names=True` renames them to TAXSIM's labels, and
`keep_intermediate=True` keeps every intermediate column for auditing.

## Calculation modes

`statutory` (default) uses the canonical parameter tables and reviewed
corrections to TAXSIM. `calculation_mode="taxsim"` reproduces the compiled
TAXSIM, for replication and comparison; it is not recommended for new analysis.

## In the browser

The [calculator](https://jrothbaum.github.io/taxsim_py/calculator/) runs taxsim-py in
the page through Pyodide: a form for one household, and a CSV upload for many. Nothing
is sent to a server. From Python, `taxsim_py.calculate_row({...})` takes a dict of
TAXSIM inputs and returns a dict of inputs plus results.

## Command line

```bash
taxsim-py households.csv taxes.parquet
taxsim-py households.dta                 # CSV on standard output
taxsim-py households.csv taxes.csv --mode taxsim --batch-rows 50000 --workers 4
```

File types come from the extensions: `csv`, `tsv`, `parquet`, `arrow`, `ndjson`,
and Stata (`dta`), SPSS (`sav`, `zsav`) and SAS (`sas7bdat`, read only) with the
optional reader (`pip install "taxsim-py[readstat]"`, which adds
[polars-readstat](https://github.com/jrothbaum/polars_readstat)). Use
`--input-format`/`--output-format` when the extension is not useful and
`--lowercase` for SAS files with uppercase names. The same readers are
`taxsim_py.io.tables.read_table` and `write_table`.

## What is supported

- **Years:** federal tax 1960-2025 and state tax 1977-2025, all actual law. Other
  years raise an error.
- **States:** all 50 states and DC (TAXSIM codes 1-51; 0 means no state). States
  without an income tax return 0, except Washington's Working Families credit in
  statutory mode.
- **Inputs:** TAXSIM's 35 inputs, with the same meanings and units, plus the
  optional `children_under_3`, `children_under_4` and `children_under_7`. Two
  conventions: `psemp`/`ssemp` get no qualified business income deduction (use
  `pbusinc`/`pprofinc`), and `pensions` is the kind of pension each state exempts.
- **Accuracy:** in `taxsim` mode, to the cent against the compiled TAXSIM on the
  validation matrix, apart from logged TAXSIM errors. `statutory` mode follows the law where TAXSIM is
  wrong ([Statutory corrections](docs/statutory_corrections.md)); its remaining
  differences from PolicyEngine-US (2022-2025) are in the
  [PolicyEngine comparison](docs/policyengine_recent_state_comparison.md).
- **Not modelled:** items TAXSIM has no input for, such as 2025 deductions for tips,
  overtime and car-loan interest, and Washington's capital gains tax.
- **Speed:** on a mixed 42-state batch, 1,000,000 rows including marginal rates
  take about 7.4 s and 2.8 GiB. The compiled TAXSIM takes 17.2 s for the same
  calculation.
  `batch_rows` and `max_year_workers` limit memory. See [Performance](docs/performance.md).

## Documentation

Start with the [user guide](https://jrothbaum.github.io/taxsim_py/). Also:
[Statutory corrections](docs/statutory_corrections.md),
[Performance](docs/performance.md) and
[PolicyEngine comparison](docs/policyengine_recent_state_comparison.md).

## License

MIT; see [LICENSE](LICENSE).
