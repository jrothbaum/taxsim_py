# taxsim-py

`taxsim-py` is a dataframe-oriented Python implementation of NBER TAXSIM.
It calculates federal income tax, payroll tax, and state income tax for
realistic household records.

The public API uses clear internal variable names and accepts Polars
`DataFrame` or `LazyFrame` objects. TAXSIM's older `v1`-style detail names are
available only when requested.

## Install

The package requires Python 3.10 or newer.

```bash
python -m pip install taxsim-py
```

With `uv`:

```bash
uv add taxsim-py
```

For development from this repository, install the project in editable mode:

```bash
python -m pip install -e .
uv sync --group dev --group test
```

## Basic use

Every row needs `mstat` and `state`. Include `year` in the data, or pass one
year to the function. TAXSIM state codes are used by default; Census FIPS
codes can be passed with `state_id_type="fips"`.

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

Missing optional inputs default to zero. `dep13`, `dep17`, and `dep18`
default to `depx` when they are not supplied.

The result contains the original input columns plus output columns such as:

- `fiitax`: federal income tax
- `fica`: payroll taxes
- `siitax`: state income tax
- `frate`, `srate`: marginal rates, when requested

Use `idtl=2` for detailed, meaningfully named federal and state worksheets:

```python
detail = calculate_taxes(households, idtl=2)
```

Use `taxsim_names=True` only when another tool requires TAXSIM's compatibility
labels. Use `keep_intermediate=True` when debugging or auditing calculations.

## Calculation modes

`statutory` is the default. It uses the canonical parameter tables and
reviewed corrections to TAXSIM behavior.

```python
taxsim_compatible = calculate_taxes(
    households,
    calculation_mode="taxsim",
)
```

The `taxsim` mode is for replication and comparison with the compiled TAXSIM
model. It preserves known compatibility behavior. It is not the recommended
default for new analysis.

In `taxsim` mode the results match the compiled TAXSIM on the repository's
validation matrix (`scripts/validate_federal.py` and `scripts/validate_states.py`);
differences that remain are logged in [Statutory corrections](docs/statutory_corrections.md).
Speed against the compiled model is in [Performance](docs/performance.md)
(`scripts/benchmark_multistate.py`, `scripts/benchmark_execution.py`); the
API is faster than the Fortran above roughly 20,000 rows.

The default `statutory` mode is also compared with PolicyEngine-US (all states,
tax years 2022-2025) and Tax-Calculator (federal): see
[PolicyEngine comparison](docs/policyengine_recent_state_comparison.md) and
`scripts/compare_independent.py`.

## Tests

Run the normal test suite with:

```bash
uv run pytest -q
```

Useful validation commands from a source checkout include:

```bash
uv run scripts/validate_all.py
uv run scripts/compare_cps.py PATH/TO/cps_2011 --tax-year 2021
```

The CPS comparison uses locally cached CPS ASEC data and the TAXSIM executable
provided by the `policyengine-taxsim` test dependency.

## Documentation

Start with the short [documentation index](docs/README.md).

- [Architecture](docs/architecture.md): how the calculators and parameters fit together
- [Statutory corrections](docs/statutory_corrections.md): reviewed differences from compiled TAXSIM
- [Performance](docs/performance.md): benchmarks against the compiled model, memory and scaling
- [PolicyEngine comparison](docs/policyengine_recent_state_comparison.md): 2022-2025 state results and known differences
- [Pending issues](docs/pending_issues.md): current project status and remaining work
- [Parameter tables](parameters/README.md): how law-oriented YAML and CSV data are maintained
