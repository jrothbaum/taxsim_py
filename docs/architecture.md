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
collects every column. State calculators receive a `LazyFrame` holding every
federal column any state reads: each federal era adds 0 (not itemizing) for
the ones it does not compute, from fixed lists rather than a schema check.
The law-1987+ federal calculator, `compute_federal_income_tax`, runs as
stages: income, deductions, regular tax, AMT, credits, net tax. Because
state calculators deflate federal columns in place for projected years, the
final pass keeps the federal pass's own columns and joins in only the columns
each state adds. The public API normally projects state plans to `siitax`
before collection; `keep_intermediate=True` retains their worksheet columns.

The public `taxsim_py.calculate_taxes` API accepts an eager or lazy Polars
frame containing one or more years and states. It partitions by year, builds
one shared federal plan for each feedback pass, partitions the collected
federal result by state, and collects only the state plans represented in the
batch. Pass `state_id_type="fips"` for Census state FIPS codes; TAXSIM state
codes are the default. State 0 (no state) and the states without a broad
income tax (Florida, Nevada, South Dakota, Texas, Washington, Wyoming) use a
zero-tax calculator; the federal sales tax deduction still uses their rates.
Absent or null inputs default to 0 (`dep13`, `dep17` and `dep18` to `depx`), counts are
cast to integers and dollar amounts to floats. A row that gives any child age
(`age1`-`age3`) takes its age-group dependent counts from the ages instead;
TAXSIM applies ages to every row of a file with age columns, so the oracle
runner sends rows with ages as a separate batch. The result is the input frame,
unchanged, plus `api.OUTPUT_COLUMNS`; `keep_intermediate=True` adds every
intermediate federal and state column.

Survey adapters may also provide `children_under_3` and `children_under_4` as
semantic extensions for state provisions that need an age cutoff not represented
by TAXSIM's standard dependent counts. They default to zero and are not part of
the 35-variable TAXSIM input set.

The public `calculation_mode` resolves once to an immutable behavior profile
before rows are partitioned. `"statutory"` is the default: the YAML and CSV
parameter tables are the canonical law-oriented inputs, and the reviewed
corrections listed in `docs/statutory_corrections.md` are enabled. Explicit
`"taxsim"` is a compatibility mode for reproducing the compiled model and
testing against its executable. Calculators receive the profile explicitly so
formula code does not depend on ambient state or opaque string checks.

When marginal rates are requested, the base and upward-perturbed rows are
stacked into one resolver call so they share plan-construction overhead. Only
rows whose upward difference falls outside TAXSIM's accepted rate range are
rerun with a downward perturbation.

`idtl=2` adds meaningfully named detailed federal outputs from `engine.detail`,
including `federal_adjusted_gross_income`, `federal_standard_deduction`,
`federal_itemized_deductions`, `federal_taxable_income`,
`federal_earned_income_tax_credit`, and
`federal_alternative_minimum_tax`. Each is read from the base run's federal
columns. Pre-1987 law fills different TAXSIM slots, so the 1960-1986
calculators export `pre1987_` columns for them. Several federal figures that
states read have their own shared column: `federal_chcr` is the child care
credit states see (`comnew(53)`), `ccc_uncapped` the credit before its
liability limit (`comnew(176)`, 0 before 1987).

`idtl=2` also adds a meaningfully named state worksheet: household income,
rent, adjusted gross income, exemptions, standard and itemized deductions,
taxable income, property tax credit, child and dependent care credit, earned
income tax credit, total credits, and marginal rate. Each state calculator
ends with `engine.state.with_state_detail(df, agi=..., rate=..., ...)`, which
stores the values under expanded `state_*` names; values a state never sets
report 0, as TAXSIM zeroes them before each state. Detail values are in
law-year dollars for projected years; household income and rent are the
undeflated inputs. `state_tax_before_credits` is always 0 because TAXSIM never
sets it. The reported rate is the rate of the bracket from TAXSIM's last
lookup in that state routine, which is not always the taxpayer's statutory
bracket (for example a spouse's share on split joint returns).

`calculate_taxes(..., taxsim_names=True)` performs a final compatibility-only
rename of these detail columns to `credits`, `v10`-`v45`, and `staxbc`. No
calculation or internal engine step uses those opaque labels.

TAXSIM prints the state worksheet of the last calculation it runs for a
record, which is the marginal-rate perturbation (+$0.01, or -$0.01 when the
increase gives an out-of-range rate), while federal detail comes from the base
run. With a marginal-rate input the API does the same: the state worksheet
columns come from that perturbed row, and household income moves by the step
unless the perturbed input is a deduction (`data(47)`-`data(63)`).

Several states (Nebraska through 1986, North Dakota's short form, Rhode Island
and Vermont through 2000) report a share of TAXSIM's own analytic federal
marginal rate (`comnew(72)`), which is not the finite-difference `frate`. The
federal calculators export it as `federal_source_rate`, in percent: the
1977-1986 calculator follows law79 and `federal._law87_analytic_rate` follows
law87 for 1987-2000. It is the bracket rate of TAXSIM's tax routine plus the
slopes of the phase-ins and phase-outs the record is in (Social Security,
EITC, child care and elderly credits, exemption and itemized deduction
phase-outs, surtaxes and the AMT), with the inputs kept as `analytic_*`
columns and constants in `parameters/national/analytic_rate.yaml`. Later years
keep the bracket rate, since no state reads the analytic rate after 2000.

Input handling shared by the federal eras lives in `engine.inputs`: filing
status and the TAXSIM counts of taxpayers (0 for a dependent filer, `mstat` 8)
and of taxpayers 65 or older. Payroll and self-employment tax follow TAXSIM's
`sstax` (`engine.payroll_tax.taxsim_payroll`, via `calculators.payroll.
payroll_parts`), which federal AGI, the payroll outputs and the states that
read self-employment tax all use.

Both validators compare marginal rates. A rate-only mismatch that disappears
when wages move by $1 comes from TAXSIM's single-precision constants at an
exact threshold and is reported as a count, not a failure.

The state validator runs with `idtl=2` and compares the state worksheet too
(with a relative tolerance of 1e-6 for projected-year deflation round-off).
TAXSIM adds $1 to household income for every record after the first in a run,
so the oracle runner prepends a warm-up record. A worksheet-only mismatch on a
record whose itemizing and standard totals tie exactly is TAXSIM's round-off
choosing the itemized run; the validator counts it when itemizing on ties
reproduces the oracle. Other worksheet-only mismatches that agree a dollar
away are the same threshold round-off: TAXSIM prints the downward run's
worksheet when round-off puts the upward rate out of range. A state tax
mismatch counts as the same round-off only when the records a dollar above
and a dollar below both agree (for example TAXSIM's single-precision
two-earner deduction leaving AGI a fraction of a cent under a table row). A
2023 case whose federal tax, payroll tax or standard
deduction differs from the oracle's (known real-parameter choices) counts as
an expected difference.

The state validator builds its registry from `tests/<xx>_cases.py` (each
defines `STATE_<XX>`, `build_<xx>_test_cases` and `YEARS`, and optionally
`shared_case_divergent(row)` to flag shared new-input cases that hit a known
oracle divergence) and takes calculators from
`calculators.states.STATE_CALCULATOR_PATHS`, so a new state is registered
once, in the API registry.

Calculators read the itemize choice through `engine.state.itemize_choice`,
`forced_itemized` and `forced_standard`, not a Python argument, and are called
as `compute_xx_tax(df, year, behavior)`. Nearly every state ignores `behavior`
today; California is the first to use it (see `statutory_corrections.md`'s
CA-001/CA-002).

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

See [Performance](internal/performance_notes.md) for the execution model, benchmark command,
and guidance on lazy collection boundaries.
