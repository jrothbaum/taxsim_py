# Calculating

## `calculate_row`

```python
calculate_row(record: Mapping[str, Any], **options) -> dict
```

One household in, one dict out. `record` maps [input names](inputs.md) to values;
`options` are any of the keyword options of `calculate_taxes` below.

```python
from taxsim_py import calculate_row

calculate_row({"year": 2023, "state": 5, "mstat": 1, "pwages": 60_000}, mtr=85)
```

It builds a one-row dataframe and calls `calculate_taxes`, so for more than a handful
of households use `calculate_taxes` directly.

## `calculate_taxes`

```python
calculate_taxes(df, *, year=None, mtr=None, idtl=None, ...) -> pl.DataFrame
```

`df` is a Polars `DataFrame` or `LazyFrame`. The result is an eager `DataFrame`:
the input, unchanged and in order, plus the [output columns](outputs.md).

```python
from taxsim_py import calculate_taxes

calculate_taxes(households)
```

## Options

Everything below is a keyword argument, and each is optional.

### Which year, which state coding

| Option | Default | Meaning |
|:--|:--|:--|
| `year` | the `year` column | Use this tax year for every row. Without it the frame needs a `year` column, and rows may mix years. |
| `year_column` | `"year"` | Name of the year column. |
| `state_column` | `"state"` | Name of the state column. |
| `state_id_type` | `"taxsim"` | `"taxsim"` (codes 1-51, 0 for no state) or `"fips"` (Census state FIPS codes). |

```python
calculate_taxes(df, year=2024)
calculate_taxes(df, state_id_type="fips")   # state=6 is California, not Colorado
```

### Marginal rates: `mtr`

`mtr` adds `frate` (federal) and `srate` (state) marginal rates, in percent, with respect
to one kind of income or deduction. Pass a `MarginalInput` or TAXSIM's own numeric code,
or put an integer `mtr` column in the frame to choose per row. `0` means no rates.

```python
from taxsim_py import MarginalInput

calculate_taxes(df, mtr=MarginalInput.PRIMARY_WAGES)   # same as mtr=85
```

| `MarginalInput` | Code | | `MarginalInput` | Code |
|:--|--:|:--|:--|--:|
| `NONE` | 0 | | `OTHER_PROPERTY_INCOME` | 79 |
| `BOTH_WAGES` | 11 | | `PRIMARY_UNEMPLOYMENT` | 82 |
| `DIVIDENDS` | 12 | | `PRIMARY_WAGES` | 85 |
| `INTEREST_RECEIVED` | 14 | | `SPOUSE_WAGES` | 86 |
| `PRIMARY_SELF_EMPLOYMENT_INCOME` | 17 | | `SOCIAL_SECURITY_BENEFITS` | 91 |
| `PENSIONS` | 20 | | `RENT_PAID` | 160 |
| `NONPROPERTY_INCOME` | 30 | | `SPOUSE_UNEMPLOYMENT` | 180 |
| `TRANSFERS` | 41 | | `PRIMARY_BUSINESS_INCOME` | 211 |
| `PROPERTY_TAX` | 51 | | `PRIMARY_PROFESSIONAL_INCOME` | 212 |
| `OTHER_ITEMIZED_DEDUCTIONS` | 54 | | `S_CORPORATION_INCOME` | 213 |
| `MORTGAGE_INTEREST` | 56 | | `SPOUSE_BUSINESS_INCOME` | 214 |
| `CHILDCARE_EXPENSES` | 64 | | `SPOUSE_PROFESSIONAL_INCOME` | 215 |
| `SHORT_TERM_CAPITAL_GAINS` | 68 | | | |
| `LONG_TERM_CAPITAL_GAINS` | 70 | | | |

Marginal rates cost extra calculations, so leave `mtr` off when you do not need them.

### Detail: `idtl`, `taxsim_names`, `keep_intermediate`

- `idtl=2` (or an `idtl` column holding 2) adds the federal and state worksheet lines
  with descriptive names, such as `federal_taxable_income` and
  `state_adjusted_gross_income`. See [Outputs](outputs.md).
- `taxsim_names=True` renames only those worksheet columns to TAXSIM's labels
  (`credits`, `v10` to `v45`, `staxbc`).
- `keep_intermediate=True` also returns every intermediate federal and state column,
  for auditing.

```python
calculate_taxes(df, idtl=2)
calculate_taxes(df, idtl=2, taxsim_names=True)
```

### Calculation mode

| `calculation_mode` | Behavior |
|:--|:--|
| `"statutory"` (default) | Uses the current-law parameter tables and the reviewed corrections to TAXSIM. Use this for new analysis. |
| `"taxsim"` | Reproduces the compiled TAXSIM, known bugs included, for replication and comparison. |

You can also pass `taxsim_py.CalculationMode.STATUTORY` or `.TAXSIM`. The list of
differences is in [Statutory corrections](statutory_corrections.md).

### Speed and memory

| Option | Meaning |
|:--|:--|
| `batch_rows` | Maximum input rows per batch. Lower it to cap memory on very large inputs. |
| `max_year_workers` | Threads used to run years and row batches concurrently. The default is Polars' thread pool size (set `POLARS_MAX_THREADS` to change it). |

See [Performance](performance.md) for timings and memory.

## Errors

`calculate_taxes` raises `ValueError` for a missing `state`, `mstat` or year column; a
null `mstat` or year; an unsupported `mstat`; an empty frame; and years outside 1960-2025
(state tax from 1977).
