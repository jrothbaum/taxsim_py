# taxsim-py

Federal income tax, payroll tax and state income tax for household records, as a
Polars-first Python package. It follows [NBER TAXSIM](https://taxsim.nber.org/)'s
inputs and meanings but is independent of, and not affiliated with, NBER.

## Install

```bash
pip install taxsim-py                    # Python 3.9 or newer
pip install "taxsim-py[readstat]"        # also read and write Stata, SPSS and SAS files
```

## Quick start

### One household

```python
from taxsim_py import calculate_row

result = calculate_row({"year": 2023, "state": 5, "mstat": 2, "page": 40, "pwages": 80_000})
result["fiitax"], result["siitax"], result["fica"]
# (5836.0, 1287.36, 12240.0)
```

The result is a plain `dict`: your inputs plus the results. `state` is the TAXSIM
state code (5 is California; 0 means no state tax) and `mstat` is the filing status
(2 is married filing jointly).

### Many households (a dataframe)

```python
import polars as pl
from taxsim_py import calculate_taxes

households = pl.DataFrame(
    {
        "year": [2021, 2021],
        "state": [6, 36],       # TAXSIM codes: 6 Colorado, 36 Ohio
        "mstat": [1, 2],        # single, married filing jointly
        "page": [45, 50],
        "pwages": [50_000, 80_000],
        "swages": [0, 40_000],
        "depx": [0, 2],
    }
)

taxes = calculate_taxes(households)
taxes.select("fiitax", "siitax", "fica")
```

The result is the input, unchanged and in order, plus the output columns. Rows can mix
states and years. Inputs you leave out count as zero; only `mstat`, `state` and the
year are required.

### A file

From Python, scan the file with Polars and pass it in. A `LazyFrame` works as well as a
`DataFrame`; the result is an eager `DataFrame`.

```python
import polars as pl
from taxsim_py import calculate_taxes

taxes = calculate_taxes(pl.scan_parquet("households.parquet"))
taxes.write_parquet("taxes.parquet")
```

From the command line, with any of the [supported file types](files.md):

```bash
taxsim-py households.csv taxes.csv
```

### In the browser

The [calculator](calculator/index.html) is a form and CSV upload that runs the same code
locally in the page through Pyodide; see [Browser](browser.md).

## What is supported

- **Years:** federal tax 1960-2025, state tax 1977-2025. Other years raise an error.
- **States:** all 50 states and DC. States without an income tax return 0.
- **Inputs:** all of TAXSIM's inputs, with the same meanings and units
  ([Inputs](inputs.md)).
- **Speed:** a million mixed-state rows with marginal rates take about 7 seconds
  ([Performance](performance.md)).

## Where to go next

- [Calculating](calculate.md): every option of `calculate_taxes` and `calculate_row`
- [Inputs](inputs.md) and [Outputs](outputs.md): the columns in and out
- [Files and command line](files.md): file types and `taxsim-py` flags
- [Statutory corrections](statutory_corrections.md): where the default mode differs
  from the compiled TAXSIM
