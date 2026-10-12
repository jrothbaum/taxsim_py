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

## Fixing an error

Check the actual form or statute first; where something is ambiguous, the form and
its instructions decide.

- **A wrong parameter value** is corrected in the file. When the error is TAXSIM's
  own (its table disagrees with the published figure for its year), the published
  value is used in both modes and the case is listed under "Published values that
  replace TAXSIM's" in [Statutory Corrections](statutory_corrections.md). Never
  change a correct value to make an oracle comparison pass.
- **A rule TAXSIM gets wrong** becomes a switch, so `calculation_mode="taxsim"`
  keeps replicating TAXSIM: add a boolean field to `BehaviorProfile` in
  `src/taxsim_py/behavior.py`, set it `False` in `TAXSIM_BEHAVIOR` and `True` in
  `STATUTORY_BEHAVIOR`, read `behavior.<name>` in the calculator (never the mode
  string), and add a ledger entry with the source and the switch name. Years after
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
