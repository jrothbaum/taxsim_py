# Architecture

Tax calculations are split into policy data and calculation mechanics.

## Policy data

All values set by tax law belong under `parameters/` in YAML or CSV files. This
includes rates, thresholds, caps, exemption amounts, phaseout values, and
year-specific adjustment factors. Sparse YAML mappings are forward-filled by
`resolve_year`, so a value is repeated only when it changes.

Adding a tax year should normally require parameter edits and test cases. A
calculator change is appropriate only when the shape of the law changes.

## Calculators

Calculators build Polars expressions from input columns and resolved parameters.
State calculators should use shared functions from `taxsim_py.engine.state` and
other engine modules before adding local helpers. Local functions are reserved
for mechanics unique to one jurisdiction. See [Shared Functions](shared_functions.md)
for the reusable-function catalog and import examples.

`engine.federal_state.resolve_federal_and_state` runs the federal and state
calculators together. Each record is stacked twice, with the `force_itemize`
column True and False, and both choices run in one frame. Each of three passes
feeds the state's income tax back into the federal itemized deduction, using
the larger of that tax and the estimated general sales tax deduction (2004
on); the itemize choice minimizing combined tax is kept. It accepts one state
calculator, or a mapping from TAXSIM state code to calculator so one federal
pass serves records from several states. With a mapping, each pass collects
the federal result once, splits it with `partition_by("state")`, and runs every
state's lazy plan together through `pl.collect_all`. The first two passes
collect only the row keys and the next pass's deduction; only the last pass
collects every column. State calculators receive a `LazyFrame`.

Calculators read the itemize choice through `engine.state.itemize_choice`,
`forced_itemized` and `forced_standard`, not a Python argument, and are called
as `compute_xx_tax(df, year)`.

Docstrings describe the public contract or calculation represented by a
function. Validation history, past bugs, oracle discrepancies, and source
investigation notes belong in project documentation or version control, not in
docstrings.

## Vectorization Contract

Tax calculations operate on whole Polars columns. Production calculators must
not iterate over taxpayer rows, use Python row callbacks, or make formula
choices from dataframe-wide reductions. Express row-dependent behavior with
`pl.when`, joins, and column expressions.

Python loops may traverse fixed policy structures such as filing statuses,
brackets, credit tiers, and feedback iterations when they only build or apply
column expressions. They must never scale with the number of taxpayer records.

Run `uv run scripts/check_vectorization.py` to reject row-wise dataframe APIs
in production modules and oversized expressions.

## Expression Size

Polars expressions are trees: reusing an intermediate expression (say, taxable
income inside several worksheets) copies its whole tree each time, and Polars
evaluates every copy. Nested reuse grows the tree exponentially and makes a
calculator slow, or exhausts memory, no matter how few rows it runs on. Store
an intermediate that is used more than once as a column with
`engine.state.checkpoint` and build later formulas on the returned column
reference. Tables and brackets use lookup-based helpers (`bracket_tax`,
`bracket_rate`, `interpolate_table`) whose size does not grow with the table.
`check_vectorization.py` fails if any expression exceeds 150,000 characters on
sample years.

## Validation

The compiled TAXSIM program is the primary regression oracle. Confirmed oracle
errors are documented and allowed explicitly; real published parameters remain
the source of truth. Test cases stay in the dataframe-driven case tables under
`tests/` and run through the shared validation scripts.

See [Performance](performance.md) for the execution model, benchmark command,
and guidance on lazy collection boundaries.
