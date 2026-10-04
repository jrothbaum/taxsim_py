# Architecture

Tax calculations are split into policy data and calculation mechanics.

## Policy data

All values set by tax law live under `parameters/` in YAML or CSV files: rates,
thresholds, caps, exemptions, phaseouts, and year-specific adjustment factors.
Sparse YAML mappings are forward-filled by `resolve_year`, so a value is repeated
only when it changes. Adding a tax year should normally need parameter edits and
test cases; change a calculator only when the shape of the law changes.

## Calculators

Calculators build Polars expressions from input columns and resolved parameters.
State calculators use the shared functions in `taxsim_py.engine.state` and other
engine modules before adding local helpers (see [Shared Functions](shared_functions.md)).
They are called as `compute_xx_tax(df, year, behavior)` and read the itemize
choice through `engine.state.itemize_choice`, `forced_itemized` and
`forced_standard`.

`engine.federal_state.resolve_federal_and_state` runs the federal and state
calculators together. Each record is stacked twice, itemizing and not
(`force_itemize` True and False). Three passes feed the state income tax (or the
estimated sales tax, if larger) back into the federal itemized deduction, and the
choice with the lower combined tax is kept. Each pass builds one federal plan,
splits the result by state, and runs the state plans together. State calculators
see every federal column any state reads, with 0 for the ones an era does not
compute. Payroll and self-employment tax follow TAXSIM's `sstax`
(`engine.payroll_tax`).

`taxsim_py.calculate_taxes` takes an eager or lazy frame with one or more years and
states, splits it into batches (see [Performance](performance.md)), and returns the
input plus the output columns (`api.OUTPUT_COLUMNS`). TAXSIM state codes are the
default; `state_id_type="fips"` accepts Census codes. State 0 and states without a
broad income tax use a zero-tax calculator. Missing inputs default to 0 (`dep13`,
`dep17` and `dep18` default to `depx`, or come from child ages when any age column
is present). `children_under_3`, `children_under_4` and `children_under_7` are
optional extensions for age cutoffs TAXSIM's counts cannot express. Tax years after
`api.LAST_SUPPORTED_YEAR` raise an error.

## Calculation modes

`calculation_mode` resolves once to an immutable behavior profile that calculators
receive explicitly. `"statutory"` (default) enables the corrections in
[Statutory corrections](statutory_corrections.md); `"taxsim"` reproduces the
compiled model for replication and oracle testing.

## Detailed output

`idtl=2` adds meaningfully named federal outputs (`engine.detail`) and a state
worksheet. Each state calculator ends with `engine.state.with_state_detail(df,
agi=..., rate=..., ...)`, which stores the values under `state_*` names; values a
state never sets report 0, as in TAXSIM. `taxsim_names=True` renames these columns
to TAXSIM's `credits`, `v10`-`v45` and `staxbc` as a final step; nothing internal
uses those labels. With a marginal-rate request the state worksheet comes from the
perturbed row, as in TAXSIM, and the federal detail from the base run.

Some states (Nebraska, North Dakota's short form, Rhode Island and Vermont
through 2000) use TAXSIM's analytic federal marginal rate, exported as
`federal_source_rate` (constants in `parameters/national/analytic_rate.yaml`).

## Vectorization contract

Calculations operate on whole Polars columns. Production calculators must not
iterate over taxpayer rows, use Python row callbacks, or choose a formula from a
dataframe-wide reduction; use `pl.when`, joins and column expressions. Python loops
may walk fixed policy structures (filing statuses, brackets, credit tiers, feedback
passes) but never the records. `uv run scripts/check_vectorization.py` rejects
row-wise APIs and oversized expressions.

## Expression size

Reusing an intermediate expression copies its whole tree each time, and Polars
evaluates every copy, so nested reuse grows exponentially. Store an intermediate
used more than once as a column with `engine.state.checkpoint`, and use the
lookup-based helpers (`bracket_tax`, `bracket_rate`, `interpolate_table`) for
tables. `check_vectorization.py` fails on expressions over 150,000 characters.

## Validation

The compiled TAXSIM is the regression oracle (`scripts/validate_federal.py`,
`scripts/validate_states.py`, `scripts/compare_cps.py`). Confirmed oracle errors are
documented and allowed explicitly; published law is the source of truth. Test
cases stay in the dataframe-driven tables under `tests/`. A new state is registered
once, in `calculators.states.STATE_CALCULATOR_PATHS`, with its cases in
`tests/<xx>_cases.py`. Docstrings state the public contract of a function; history
and oracle notes belong in the docs or version control.
