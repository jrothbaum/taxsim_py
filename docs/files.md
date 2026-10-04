# Files and command line

## Command line

```bash
taxsim-py INPUT [OUTPUT]
```

`OUTPUT` is optional; without it the result is CSV on standard output.

```bash
taxsim-py households.csv taxes.parquet
taxsim-py households.dta                       # CSV on standard output
taxsim-py households.csv taxes.csv --mode taxsim --batch-rows 50000 --workers 4
```

| Flag | Meaning |
|:--|:--|
| `--mode {statutory,taxsim}` | Calculation mode ([Calculating](calculate.md#calculation-mode)). Default `statutory`. |
| `--year YEAR` | Tax year for every row, instead of the `year` column. |
| `--state-id-type {taxsim,fips}` | How the `state` column is coded. |
| `--idtl {0,2}` | `2` adds the detailed federal and state worksheets. |
| `--mtr CODE` | Marginal-rate code ([codes](calculate.md#marginal-rates-mtr)). |
| `--taxsim-names` | Use TAXSIM's labels for the detail columns. |
| `--keep-intermediate` | Also return intermediate columns. |
| `--lowercase` | Lowercase the input column names (useful for SAS files). |
| `--batch-rows N` | Maximum input rows per batch. |
| `--workers N` | Threads (batches in flight). |
| `--input-format`, `--output-format` | Override the file type when the extension is not useful. |

Errors print as `taxsim-py: message` and exit with status 1.

## File types

The type comes from the extension.

| Type | Extensions | Read | Write |
|:--|:--|:-:|:-:|
| CSV / TSV | `csv`, `txt`, `tsv` | yes | yes |
| Parquet | `parquet`, `pq` | yes | yes |
| Arrow IPC | `arrow`, `feather`, `ipc` | yes | yes |
| JSON lines | `ndjson`, `jsonl` | yes | yes |
| Stata | `dta` | extra | extra |
| SPSS | `sav`, `zsav` | extra | extra |
| SAS | `sas7bdat` | extra | no |

"extra" needs `pip install "taxsim-py[readstat]"`, which adds
[polars-readstat](https://github.com/jrothbaum/polars_readstat).

## From Python

The same readers and writers are available directly:

```python
from taxsim_py import calculate_taxes
from taxsim_py.io.tables import read_table, write_table

frame = read_table("households.dta")
write_table(calculate_taxes(frame), "taxes.parquet")
```

Both take an optional second argument that overrides the file type.
