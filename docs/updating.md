# Updating the Tax Law

This page is for whoever adds a tax year, fixes a value or rule, or codes a law
change. Most updates touch only the parameter files under `parameters/`; the
calculators change only when the shape of the law changes.

## Where things live

| What | Where |
| --- | --- |
| Federal law from 1987 | `parameters/national/income_tax.yaml` (brackets, standard deductions, QBI, senior deduction), `itemized.yaml`, `personal_exemption.yaml`, `credits.yaml`, `capital_gains.yaml`, `amt.yaml`, `niit.yaml`, `payroll_tax.yaml`, `social_security.yaml`, `eitc_misc.yaml` |
| Federal EITC schedule | `parameters/national/eitc.csv`: one row per year, number of children (0-3) and filing status |
| Federal law 1960-1986 | `parameters/national/law60.yaml`, `pre1987.yaml` (fixed history) |
| A state's law | `parameters/states/xx/income_tax.yaml` (plus any CSV tables beside it) |
| Federal calculator | `src/taxsim_py/calculators/federal.py` (`federal_law60.py`, `federal_pre1987.py` for the early law) |
| A state's calculator | `src/taxsim_py/calculators/states/xx.py` (`in_.py` and `or_.py` for Indiana and Oregon) |
| Shared formulas | `src/taxsim_py/engine/` (see [Shared Functions](shared_functions.md)) |
| Statutory-vs-TAXSIM differences | `src/taxsim_py/behavior.py` and [Statutory Corrections](statutory_corrections.md) |

## How a parameter file reads

Each file opens with what it covers and its sources. Each state file then says
how far its actual law is coded:

```yaml
# Ohio individual income tax.
#
# Sources: 1977-2021 NBER TAXSIM (`ohtax`, 2021 from the newer `ohtax21`);
# 2022 on, Ohio Revised Code 5747, IT 1040 instructions and PolicyEngine-US
# `gov.states.oh.tax.income`.

# Statutory mode uses actual law through this year.
law_coded_through: 2025
```

The rules, which `tests/test_parameter_conventions.py` enforces:

- **A value is keyed by the first year it applies** and carries forward until the
  next key. Add a year only when the value changes:

  ```yaml
  eitc_rate: {2013: 0.05, 2014: 0.10, 2019: 0.30}   # 0.30 for 2019 and every later year
  ```

- **`null` ends a provision.** Reading a parameter in or after its `null` year
  raises an error, so code asks first (below):

  ```yaml
  high_income_limit: {2025: 750000.0}
  no_tax_threshold: {2005: 10000.0, 2017: null}       # 2005-2016 only
  ```

- **Names describe the concept, never a year.** Write `child_tax_credit` with year
  keys, not `child_tax_credit_2023plus`. A provision whose structure changes keeps
  one name, and its year entries take the new shape:

  ```yaml
  household_credit:
    1978: [[5000, 65], [6000, 50], [7000, 40], [25000, 35], [1.0e+20, 0]]
    1986: {single: [...], married: [...], per_additional_person: [...]}
  ```

- **Year keys may sit above or below other keys.** `{single: {1987: 2540, ...}}` and
  `{2013: {single: 250000, ...}}` both work; choose whichever keeps the file
  readable. Integer keys are always years; use words for counts (`one`, `two`).
- **Comments say what a value is**, with its source where known ("Source:
  Rev. Proc. 2024-40"). They do not record how it was found.
- **Numbers live here, not in code.** A rate, threshold, cap or table belongs in a
  parameter file even if it is used once.

## Reading parameters in a calculator

```python
p = YearParams(XX_PARAMS, effective_year)
rate = p.num("rate")                                 # the value for the year, as a float
limit = p.num("exemption_income_limit", "joint")     # walks keys, resolving year levels
if p.in_effect("renter_credit"):                      # started and not ended (null)
    credit = p.value("renter_credit")
```

Use `in_effect` where a provision simply starts or stops. Keep a year comparison
(`if y <= 1986:`) only where the formula itself changes shape, such as a different
base or a different set of steps.

State calculators begin with

```python
effective_year, flate = resolve_state_year(year, behavior, XX_PARAMS)
```

Through the state's `law_coded_through` (statutory mode), or TAXSIM's last state
year 2021 (TAXSIM mode), this returns the year itself. After it, the calculator runs
that last year's law on incomes deflated by a price index and reinflates the tax,
as TAXSIM does.

## Adding a tax year

The example is moving from 2025 to 2026.

1. **List what may need a new value.**

   ```bash
   uv run python scripts/year_status.py 2026 --federal-only
   uv run python scripts/year_status.py 2026 --states ny,ca
   ```

   It prints each entry still in effect whose latest year is before 2026 and that
   changed in the last five years (`--all` for every one). Inflation-indexed values
   usually appear on it; a value that legitimately carries forward needs no entry.

2. **Federal.** The annual IRS Revenue Procedure on inflation adjustments covers
   most values: rate brackets (`income_tax.yaml`), standard deductions, the aged and
   dependent standard deductions, the AMT exemptions, phaseout thresholds, rate
   breakpoint and child exemption cap (`amt.yaml`), the capital gains rate
   thresholds (`capital_gains.yaml` and `amt.yaml`'s own copy), EITC amounts and the
   investment income limit (`eitc.csv`, `eitc_misc.yaml`), the refundable child
   credit cap and QBI thresholds. The Social Security wage base comes from SSA.
   Check new legislation for anything that starts or ends: add a `null` for an
   expiring provision rather than deleting it. Then raise `law_coded_through` in
   `parameters/national/income_tax.yaml`, which sets the last year
   `calculate_taxes` accepts.

3. **States.** For each state with new law, enter the new values from its forms and
   instructions and raise its `law_coded_through`. A state left at an earlier year
   keeps working: statutory mode projects its last coded year forward with the
   price index (`parameters/national/state_cpi_extrapolation.yaml`, continued at
   2.5% a year past its last entry).

4. **Tests.** Add a published-law case for each value worth guarding:
   federal values in `tests/test_law_based_checks.py` (an input record and the
   expected output), state rules in `tests/test_state_form_checks.py` (two records
   whose relationship follows from the cited rule). Cite the form or document in
   the case description.

5. **Check** as in the last section.

## When the law changes shape

A new credit, a phaseout replacing a flat amount, or a new filing rule needs code:

1. Add the parameters with their start year (and `null` where they end), with a
   comment saying what they are and the source.
2. In the calculator, gate the new piece with `p.in_effect(...)` and read values
   with `p.num` / `p.value`. Reuse the engine helpers (`bracket_tax`,
   `bracket_rate`, `interpolate_table`, `by_filing_status`, `trapezoid_credit`,
   `checkpoint` and the rest in [Shared Functions](shared_functions.md)) before
   writing a new one.
3. Keep expressions vectorized: no Python loops over rows and no
   dataframe-wide reductions inside a formula.

The examples below use a state file `parameters/states/xx/income_tax.yaml` and its
calculator. `p` is the `YearParams` for the year, and `statax` and `fed_agi` are
the calculator's tax and federal AGI.

### A new credit starts

A refundable credit of $100 per dependent begins in 2026.

```yaml
# Refundable credit per dependent from 2026 (Source: XX Code 1-2-3).
dependent_credit: {2026: 100}
```

```python
if p.in_effect("dependent_credit"):
    statax = statax - p.num("dependent_credit") * pl.col("depx")
```

Before 2026 `in_effect` is false and the code is skipped, so earlier years are
unchanged. If the credit is repealed, add `2030: null` to the entry; the code
stays as it is.

### A flat amount gains a phaseout

The $2,000 exemption is reduced by 5% of federal AGI over $100,000 from 2026.

```yaml
exemption: {1990: 1500, 2010: 2000}
# From 2026 the exemption falls by `rate` of federal AGI over `start`, not
# below 0.
exemption_phaseout: {2026: {start: 100000, rate: 0.05}}
```

```python
exemption = pl.lit(p.num("exemption"))
if p.in_effect("exemption_phaseout"):
    phaseout = p.value("exemption_phaseout")
    reduction = phaseout["rate"] * (fed_agi - phaseout["start"]).clip(0, None)
    exemption = (exemption - reduction).clip(0, None)
```

The exemption keeps its own entry; the phaseout is a separate entry that starts
in 2026.

### Brackets become a flat rate

The state replaces its brackets with a single 4% rate in 2026.

```yaml
brackets:
  1990: [[0, 0.02], [10000, 0.04], [40000, 0.06]]
  2026: null
flat_rate: {2026: 0.04}
```

```python
if p.in_effect("flat_rate"):
    statax = p.num("flat_rate") * taxinc
    rate = pl.lit(p.num("flat_rate"))
else:
    statax = bracket_tax(taxinc, p.value("brackets"))
    rate = bracket_rate(taxinc, p.value("brackets"))
```

The `null` makes any leftover read of `brackets` in 2026 or later fail loudly
rather than return the old schedule.

### One amount becomes amounts by filing status

The standard deduction was one amount for everyone; from 2026 it differs by
filing status. The entry keeps its name, and its 2026 value takes the new shape.

```yaml
standard_deduction:
  1990: 3000
  2026: {single: 3500, married_joint: 7000, married_separate: 3500, head_of_household: 5250}
```

```python
amount = p.value("standard_deduction")
if isinstance(amount, dict):
    stded = by_filing_status(amount)
else:
    stded = pl.lit(float(amount))
```

When a change needs a different formula rather than new values, as here, add a
test row for a year on each side of the change (2025 and 2026) to
`tests/test_state_form_checks.py`, citing the form.

## Adding a new variable or output

A new law often brings a figure that users want to see on its own, such as the
2020-2021 recovery rebate credit (Economic Impact Payment). The calculation is
the work; making the figure available is a short checklist. The rebate, held in
the column `cares`, serves as the example.

### Where outputs come from

`calculate_taxes` returns the input columns plus one of three sets:

| Option | Columns added |
|:--|:--|
| none | `OUTPUT_LABELS` in `src/taxsim_py/api.py`, any `YEAR_OUTPUTS` in effect in the input's years, and `frate`/`srate` with `mtr` |
| `idtl=2` | the above plus the named federal and state detail outputs (`engine/detail.py`) |
| `keep_intermediate=True` | every working column of the federal and state calculators |

Any column a calculator creates is already returned with `keep_intermediate=True`.
The steps below make it a named, documented output. Keep the default set short:
every default column is carried through the whole calculation for every row,
which costs memory on large files.

The labels in `api.py` are the only list of default outputs: the browser
calculator receives them from the package (`output_labels()`), and a column without
a label is shown under its own name.

### A federal variable

1. **Parameters.** Add the amounts to the right file under a descriptive name with
   year keys, ended by `null` if the provision expires:

   ```yaml
   recovery_rebate:
     2020:
       phaseout_start: {single: 75000, married_joint: 150000, head_of_household: 112500, married_separate: 75000}
       phaseout_rate: 0.05
       payments: [{per_adult: 1200, per_child: 500}, {per_adult: 600, per_child: 600}]
     2021: {...}
     2022: null
   ```

2. **Compute it in the stage it belongs to** (`_income`, `_deductions`,
   `_regular_tax`, `_alternative_minimum_tax`, `_credits` or `_net_tax` in
   `calculators/federal.py`), gated by `in_effect`, and store it as a column that
   is 0 in other years:

   ```python
   credits_p = YearParams(FEDERAL_CREDITS_PARAMS, year)
   if credits_p.in_effect("recovery_rebate"):
       rebate = credits_p.value("recovery_rebate")
       cares = ...                                  # the formula
   else:
       cares = pl.lit(0.0)
   df = df.with_columns(cares=pl.when(is_dependent_filer()).then(0.0).otherwise(cares))
   ```

3. **Use it in the tax**, if it affects tax: the rebate is subtracted in the
   `fiitax` total at the end of `_net_tax`. A figure reported for information only
   skips this step.

4. **Make it safe for every year and state.**
   - Add the name to `_LAW87_ONLY` in `engine/federal_state.py` so the 1960-1986
     calculators, which never compute it, supply 0.
   - If it is a dollar amount, add it to `PROJECTED_YEAR_DEFLATED_COLUMNS` in
     `engine/state_extrapolation.py`, so a state projecting old law deflates it
     with the other federal amounts.

5. **Read it in the states whose law uses it.** Louisiana and Oregon count the
   rebate in their federal tax deduction:

   ```python
   fedtax = (fedtax - pl.col("cares")).clip(0, None)
   ```

6. **Name it as an output.** Choose one level:
   - *Detail output* (`idtl=2`), the usual choice: add a line to `federal_detail`
     in `engine/detail.py`, and the name to `FEDERAL_DETAIL_NAME_TO_TAXSIM` with
     TAXSIM's column if it has one, or the same name if not:

     ```python
     col("cares").alias("federal_recovery_rebate_credit"),   # in federal_detail
     "federal_recovery_rebate_credit": "v45",                # in FEDERAL_DETAIL_NAME_TO_TAXSIM
     ```

   - *Default output in every year*: add the name and its label to `OUTPUT_LABELS`
     in `api.py`. Prefer a readable name, and rename the working column to it
     rather than exposing an internal name.
   - *Default output in its years only*, for a provision that exists only in some
     years: add it to `YEAR_OUTPUTS` in `api.py` with its working column, the
     parameter entry whose years it follows, and its label. The column then
     appears only when the input includes a year in which that entry is in effect:

     ```python
     YEAR_OUTPUTS = {
         "recovery_rebate_credit": ("cares", "national/credits.yaml", "recovery_rebate", "Recovery rebate credit (EIP)"),
     }
     ```

     A later extension of the provision (a new year in the parameter file)
     extends the output's years with no code change.

7. **Document it.** Add a row to [Outputs](outputs.md) at the level chosen. The
   browser calculator picks up the label from `api.py`.

8. **Test it** with a published-law case in `tests/test_law_based_checks.py` (for
   example a 2020 couple with two children, from the IRS Recovery Rebate Credit
   worksheet), and run the checks below. `calculate_row`, the command line and the
   Stata wrapper all call `calculate_taxes`, so they need no change.

### A state variable

- **One state's figure** (a new state credit) is a column with the state's prefix,
  such as `mo_wftc`, in that state's calculator. It is returned with
  `keep_intermediate=True` and needs nothing else. Do not add it to
  `OUTPUT_COLUMNS`: each state's rows are computed separately and then stacked, so
  a column only one state creates would be missing for the rest.
- **A figure every state can report** goes through the state worksheet:
  1. add a keyword to `_DETAIL_NAMES` in `engine/state.py`
     (`"renter_credit": "state_renter_credit"`);
  2. add the public name to `STATE_DETAIL_SOURCES` and `STATE_DETAIL_NAME_TO_TAXSIM`
     in `engine/detail.py` (map it to itself where TAXSIM has no column);
  3. pass the keyword to `with_state_detail` in each state where it applies.
     States that do not pass it report 0.

  Then document it in [Outputs](outputs.md) under `idtl=2` and test it as for a
  federal variable.

## Fixing an error

Check the actual form or statute first; where something is ambiguous, the form and
its instructions decide.

- **A wrong parameter value** is corrected in the file. When the error is TAXSIM's
  own (its table disagrees with the published figure for its year), the published
  value is used in both modes and the case is listed under "Published values that
  replace TAXSIM's" in [Statutory Corrections](statutory_corrections.md); add the
  state year to `KNOWN_ORACLE_DIVERGENT_STATE_YEARS` in `scripts/validate_states.py`
  (federal: mark the case `oracle_divergent`). Never change a correct value to make
  an oracle comparison pass.
- **A rule TAXSIM gets wrong** becomes a switch, so `calculation_mode="taxsim"`
  keeps replicating TAXSIM: add a boolean field to `BehaviorProfile` in
  `src/taxsim_py/behavior.py` with the statutory value as its default (TAXSIM mode
  flips every switch), read `behavior.<name>` in the calculator (never the mode
  string), add a row to `tests/statutory_correction_table.py` with the column's
  value in each mode, and add a ledger entry with the source and the switch name.
  Mark the error's row in [Known TAXSIM errors](taxsim_errors.md). Years after
  2021 run only in statutory mode for states, so a fix confined to those years
  needs no switch.

## Checking a change

```bash
uv run --group test pytest -q                 # all tests, including the conventions
uv run python scripts/check_vectorization.py  # expression size limits
uv run ruff check src tests scripts
```

To see exactly which outputs a change moves, compare a grid of seeded random
households against the previous commit:

```bash
git worktree add ../taxsim_base HEAD
(cd ../taxsim_base && uv run --group test python scripts/regression_grid.py run /tmp/base)
uv run --group test python scripts/regression_grid.py run /tmp/new
uv run --group test python scripts/regression_grid.py compare /tmp/base /tmp/new
```

A pure clean-up should report no differing columns. A law change should move only
the states, years and modes it targets, plus any federal effect through the state
income tax deduction, and any state that reads a changed federal value. State law
starts in 1977; for earlier federal years run `--states 0 --years 1960-1976`.

## Appendix: code patterns

Short excerpts from the calculators, trimmed to the lines that show each pattern.
Read the full file before changing one.

### Federal

**1. Each year runs one era's calculator.** `compute_federal_income_tax`
(`calculators/federal.py`) sends 1960-1976 to `federal_law60.py` and 1977-1986 to
`federal_pre1987.py`; 1987 on runs as a chain of stages, each adding columns.

```python
if year <= 1976:
    return compute_federal_income_tax_law60(df, year, behavior)
if year <= 1986:
    return compute_federal_income_tax_pre1987(df, year, behavior)
df = _income(df, year, behavior)
for stage in (_deductions, _regular_tax, _alternative_minimum_tax, _credits, _net_tax):
    df = stage(df, year, behavior)
```

A new federal provision goes in the stage it belongs to, never in a new branch on
the year range. States read the columns these stages add, such as `agi`,
`taxable_income`, `regular_tax`, `itemizes` and `eitc`
(`engine/federal_state.py` lists the ones an era does not compute).

**2. A value by filing status.** Parameters keyed by status become one expression
that picks the row's value from its `filing_status` column.

```python
std_ded_by_status = {
    status: resolve_year(FEDERAL_INCOME_TAX_PARAMS["standard_deduction"][status], year)
    for status in FILING_STATUSES
    if status != "married_separate"
}
std_ded_by_status["married_separate"] = std_ded_by_status["married_joint"] / 2.0
std_ded_expr = by_filing_status(std_ded_by_status)
```

**3. Brackets by filing status.** Each schedule is `[[start, rate], ...]`;
`bracket_tax_by_status` applies the row's schedule.

```python
brackets_by_status = _brackets_by_status(year)   # one schedule per status
tax_expr = bracket_tax_by_status(ordinary_income, brackets_by_status)
federal_source_rate = 100.0 * bracket_rate_by_status(ordinary_income, brackets_by_status)
```

**4. A provision that exists only in some years.** `in_effect` is false before the
first year and from a `null` year on, so nothing runs outside the provision's
life (here the 2009-2010 Making Work Pay credit).

```python
if credits_p.in_effect("making_work_pay"):
    mwp = credits_p.value("making_work_pay")
    num_filers = pl.when(files_joint()).then(2.0).otherwise(1.0)
    phase_in = pl.min_horizontal(mwp["max_credit"] * num_filers, mwp["rate"] * pl.col("earned_income"))
    reduction = mwp["phaseout_rate"] * (agi - mwp["phaseout_start"] * num_filers).clip(0, None)
    making_work_pay = (phase_in - reduction).clip(0, None)
else:
    making_work_pay = pl.lit(0.0)
```

**5. A phaseout.** Reductions are written as a rate times the income above the
start, floored at zero, then subtracted from the amount, floored again.

```python
tcja_reduction = ((agi - odc_threshold_expr).clip(0, None) / 1000) * odc_rate_per_1000
combined_after_phaseout = (combined_base - tcja_reduction).clip(0, None)
```

**6. A table too large for YAML.** The EITC, which varies by year, status and number
of children, is a CSV joined onto the rows for the year; the credit is then the
shared trapezoid (phase-in, plateau, phaseout).

```python
eitc_params_year = FEDERAL_EITC_PARAMS.filter(pl.col("year") == year).select(
    "filing_status", "num_children", "rate_in", "max_credit", "phaseout_start", "rate_out"
)
df = df.join(eitc_params_year, on=["filing_status", "num_children"], how="left")
eitc = trapezoid_credit(
    pl.col("earned_income"), phaseout_income,
    pl.col("rate_in"), pl.col("max_credit"), pl.col("phaseout_start"), pl.col("rate_out"),
)
```

**7. A nonrefundable credit limited to tax.** Credits come off in a fixed order,
each limited to the tax left by the ones before it.

```python
ccc = pl.min_horizontal(ccc_amount, pl.col("tax_before_credits").clip(0, None))
remaining = (pl.col("tax_before_credits") - pl.col("ccc") - pl.col("elderly_credit_raw")).clip(0, None)
nonrefundable_credit = pl.min_horizontal(combined_after_phaseout, remaining)
```

**8. Payroll tax per earner.** Each earner's wages and self-employment items are
taxed against that earner's own wage base (`engine/payroll_tax.py`); the
self-employment items are first multiplied by the net-earnings factor (92.35%).

```python
PAYROLL_ITEMS = (
    ("pwages", "psemp", "pbusinc", "pprofinc"),   # primary earner
    ("swages", "ssemp", "sbusinc", "sprofinc"),   # spouse
)
for names in payroll_items(behavior):
    items = [pl.col(names[0]).clip(0, None), *self_employment_items]
    oasdi, capped = _capped_items(items, factors, oasdi_rates, wage_base, ...)
```

**9. A statutory correction.** A switch in `behavior.py` chooses between the law and
TAXSIM's behavior; the default is the law.

```python
# behavior.py
use_joint_elderly_credit_threshold: bool = True

# federal.py
one_aged = (aged_count() == 1) & (sepret != 2)
single_threshold = (~files_joint() & (sepret != 2)) if behavior.use_joint_elderly_credit_threshold else one_aged
threshold = pl.when(single_threshold).then(eld_p["agi_threshold"]["one_aged"]).otherwise(
    eld_p["agi_threshold"]["two_aged"] / sepret
)
```

**10. A year-specific quirk kept only for TAXSIM mode.** Where the law is in the
parameter file, TAXSIM's departure from it is a short, commented branch.

```python
if year == 1979 and not behavior.apply_1979_dividend_exclusion:
    # TAXSIM reads the 1979 exclusion from an input it never sets.
    return pl.lit(0.0)
```

### States

**11. The shape of a state calculator.** Every state module has one function with
this signature, registered by its TAXSIM code in
`calculators/states/__init__.py` (`14: ("il", "compute_il_tax")`). It receives the
federal results as columns and must add `siitax`.

```python
IL_PARAMS = load_yaml(PARAMETERS_ROOT / "states" / "il" / "income_tax.yaml")

def compute_il_tax(df, year, behavior=TAXSIM_BEHAVIOR):
    effective_year, flate = resolve_state_year(year, behavior, IL_PARAMS)
    p = YearParams(IL_PARAMS, effective_year)
    ...
    df = df.with_columns(siitax=pl.col("il_tax_after_eitc") * flate)
    return with_state_detail(
        df,
        agi=pl.col("il_agi"),
        exemptions=pl.col("il_exemption"),
        taxable_income=pl.col("il_taxinc"),
        credits=pl.col("il_property_tax_credit") + pl.col("il_eitc"),
        rate=rate,
    )
```

`with_state_detail` sets the worksheet outputs (`idtl=2`); keywords it is not
given report 0. Name working columns with the state prefix (`il_`) so they do not
collide with federal ones.

**12. Projected years.** Past the last coded year, `resolve_state_year` returns
that year and an inflation factor `flate`. Dollar inputs are divided by it, the
old law runs, and the tax is multiplied back. Through the last coded year
`flate` is 1, so the same code serves every year.

```python
df = deflate_for_extrapolation(df, flate)          # most states
df = df.with_columns(il_agi=(...) / flate)         # or divide only what is used
df = df.with_columns(siitax=statax * flate)
```

**13. Starting from federal AGI.** Most states take federal AGI and add or subtract
items; each adjustment is gated by its parameter.

```python
agi = pl.col("agi") - pl.col("pensions") - pl.col("taxable_social_security")
if effective_year <= 1986:
    agi = agi + dividend_exclusion_addback(effective_year, behavior) + pl.col("pre1987_capded")
```

**14. A change of formula between eras.** Use a year test only where the formula
itself differs; new values in the same formula belong in the parameter file.

```python
if effective_year <= 1986:
    exemps = taxpayer_count() + pl.col("depx") + aged_count()
else:
    exemps = pl.when(is_dependent_filer()).then(0.0).otherwise(taxpayer_count() + pl.col("depx"))
```

**15. Brackets indexed for inflation.** A state that indexes its brackets stores
the base schedule and an index; `scale_brackets` multiplies every threshold.

```python
aif = p.num("bracket_inflation")
brackets = scale_brackets(p.value("brackets"), aif)
statax = bracket_tax(taxinc, brackets)
rate_expr = bracket_rate(taxinc, brackets)
```

**16. Separate schedules by filing status.** The year's `brackets` entry is a
mapping of schedules; pick one per row.

```python
tables = p.value("brackets")
statax = (
    pl.when(is_single).then(bracket_tax(taxinc, tables["single"]))
    .when(is_hoh).then(bracket_tax(taxinc, tables["head_of_household"]))
    .otherwise(bracket_tax(taxinc * sep, tables["married"]) / sep)
)
```

Married separate returns use the married schedule at doubled income, halved
(`sep` is 2 on a separate return, otherwise 1).

**17. Joint returns taxed as two people.** Several states let a couple pay the
lesser of the joint tax and the tax on each spouse's share.

```python
statax = bracket_tax(taxinc, brackets)
split_tax = bracket_tax(taxh, brackets) + bracket_tax(taxw, brackets)
statax = pl.when(is_joint & (agi > 0)).then(pl.min_horizontal(statax, split_tax)).otherwise(statax)
```

**18. An amount read from a table by income.** `interpolate_table` reads
`[income, amount]` rows the way TAXSIM's `tablki` does (linear between rows);
`tier_values` picks the row an income falls in, for step tables.

```yaml
low_income_credit_per_exemption:
  1992: [[6000, 26], [8000, 20], [10000, 14], [15000, 8], [20000, 5], [100000000, 0]]
```

```python
rows = [[float(t), float(r)] for t, r in p.value("low_income_credit_per_exemption")]
credit = interpolate_table(pl.col("agi"), rows) * (pl.col("ga_taxpayers") + pl.col("depx") + aged_count())
```

**19. A share of a federal credit.** State EITCs and child care credits are often a
percentage of the federal amount, refundable or limited to tax.

```python
il_eitc_raw = p.num("eitc_match_rate") * (pl.col("eitc") / flate)
if not p.value("eitc_refundable"):
    il_eitc = pl.max_horizontal(0.0, pl.min_horizontal(il_eitc_raw, pl.col("il_tax_after_property_credit")))
else:
    il_eitc = il_eitc_raw
```

**20. A household-income credit.** Property tax and renter credits use TAXSIM's
household income (`household_income(behavior)`: income including nontaxable
benefits) and usually a share of rent as property tax. Simplified from Missouri's
property tax credit, which reduces the credit in income steps.

```python
hy = household_income(behavior)
ptax = pl.col("proptax") + float(p["property_credit_rent_share"]) * pl.col("rentpaid")
credit = (pl.min_horizontal(ptax, maximum) - hy * income_share).clip(0, None)
credit = pl.when((aged_count() > 0) & (hy < income_limit)).then(credit).otherwise(0.0)
```

**21. Keeping long formulas fast.** `checkpoint` stores expressions as columns so
later steps refer to the column instead of repeating the whole expression.

```python
df, (stded, xitded, exemp) = checkpoint(df, mn_stded=stded, mn_xitded=xitded, mn_exemp=exemp)
deduc = pl.max_horizontal(stded, xitded)          # reads the stored columns
```

Use it after any expression that later steps read more than once;
`scripts/check_vectorization.py` fails when an expression grows too large.

**22. A statutory correction in a state.** As in the federal code (example 9):
a switch, the law by default, and TAXSIM's behavior in a commented branch.

```python
ceiling = pl.col("il_tax_after_property_credit")
if not behavior.subtract_illinois_property_credit_once_in_eitc_limit:
    # TAXSIM subtracts the property tax credit twice in the limit.
    ceiling = ceiling - pl.col("il_property_tax_credit")
```

**23. Actual law after TAXSIM's last year.** States run actual law through their
`law_coded_through` year in statutory mode; TAXSIM mode projects 2021 law. New
2022-on provisions therefore need only `in_effect` gates, not switches (Missouri's
Working Family Tax Credit shown).

```yaml
law_coded_through: 2025
working_family_credit:
  2023: {match: 0.1, investment_income_limit: 4050}
  2024: {match: 0.2, investment_income_limit: 4300}
```

```python
if p.in_effect("working_family_credit"):
    c = p.value("working_family_credit")
    eligible = (investment <= float(c["investment_income_limit"])) & ~is_dependent_filer()
    wftc = pl.when(eligible).then(pl.min_horizontal(float(c["match"]) * pl.col("eitc"), statax.clip(0, None))).otherwise(0.0)
    statax = statax - wftc
```
