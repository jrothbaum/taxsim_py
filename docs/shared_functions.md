# Shared Functions

Check this catalog before adding a helper to a federal or state calculator.
Shared calculation mechanics live in `src/taxsim_py/engine/`; policy values
belong in `parameters/`.

## State Calculator Helpers

Import these from `taxsim_py.engine.state`.

| Function | Use it for |
| --- | --- |
| `with_defaults(df, columns, default=0.0)` | Adding columns only when missing (resolves a lazy frame's schema once). State calculators do not need it: inputs are defaulted by the API and every federal era reports each federal column states read (0 where that era does not compute it). |
| `by_filing_status(values)` | Selecting a numeric value from a mapping keyed by `single`, `married_joint`, `married_separate`, and `head_of_household`. |
| `interpolate_table(income, rows)` | Evaluating TAXSIM `tablki` tables represented as `[threshold, value]` rows. Keep thresholds constant (shift the income instead, e.g. `income - start`); row-varying thresholds fall back to a much larger per-row expression. |
| `tier_values(value, uppers, *columns, strict=False)` | Picking several values from the first tier whose upper bound is at least `value` (with `strict`, exceeds it, as TAXSIM `tablk`), with one lookup instead of a when/then chain per tier. |
| `unemployment_total()` | Unemployment compensation as TAXSIM combines it (`max(ui, pui + sui)`). |
| `household_income()` | TAXSIM's household-income total (`data(159)`) from raw inputs, with its two input adjustments from `state_adjustments.yaml`. |
| `dividend_input_adjustment()` | The fixed amount TAXSIM adds to dividends (`data(12)`); use it instead of a literal `0.001`. |
| `dividend_exclusion_addback(year)` | The federal dividend exclusion states add back to federal AGI (TAXSIM `divexc`); zero from 1987. |
| `higher_earner_share(income)` | The higher earner's share of joint income (wages plus half the rest), as TAXSIM splits joint returns. |
| `checkpoint(df, **expressions)` | Storing intermediate results as columns and getting references back, so a result used several times is computed once (see Architecture: expression size). |
| `pre1987_federal_itemizing(year)` | Through 1986: the federal gross itemized total (`comnew(30)`), whether the federal return itemizes (`comnew(26)`, following the resolver's forced choice from 1982) and the zero bracket (`comnew(3)`). |
| `itemize_choice(natural)`, `forced_itemized()`, `forced_standard()` | Reading the resolver's `force_itemize` column: the forced choice where set, else the natural comparison; or tests for a forced choice. |
| `with_state_detail(df, **values)` | Ending a state calculator: stores the worksheet requested by `idtl=2` (`agi`, `exemptions`, `standard_deduction`, `itemized_deductions`, `taxable_income`, `property_credit`, `child_care_credit`, `eic`, `credits`, `rate` as a fraction) under expanded `state_*` names; omitted values report 0. |

`engine.inputs` holds the TAXSIM input counts states read: `filing_status()`,
`taxpayer_count()` (`data(7)`), `is_dependent_filer()` (`data(105)`),
`aged_count()` (`data(9)`) and `federal_exemption_count(year)` (`comnew(68)`),
plus the filing-status tests `files_single()`, `files_joint()`,
`files_separate()`, `files_head_of_household()` and `separate_divisor()`
(TAXSIM `sep`: 2 on a separate return, otherwise 1).

Typical imports:

```python
from taxsim_py.engine.inputs import files_joint, separate_divisor
from taxsim_py.engine.state import by_filing_status, interpolate_table
```

Example:

```python
exemption = by_filing_status(parameters["exemption"])
credit_rate = interpolate_table(pl.col("agi"), parameters["credit_rate"])
```

Do not create state-local versions named `_with_default`, `_by_status`,
`_tablki`, or `_table_lookup`. Extend `engine/state.py` when a generally useful
state expression is missing.

## Parameters

Import these from `taxsim_py.engine.schema`.

| Function or value | Use it for |
| --- | --- |
| `PARAMETERS_ROOT` | Building paths under the repository's `parameters/` directory. |
| `load_yaml(path)` | Loading a YAML parameter file. |
| `resolve_year(by_year, year)` | Reading the latest sparse parameter value effective on or before a year. |
| `YearParams(params, year)` | A parameter mapping read at one law year: `p.num("key")` resolves and converts to float, `p.value("key")` resolves, `p["key"]` returns the entry unchanged. |
| `validate_brackets(brackets, context)` | Checking that bracket thresholds strictly increase. |

Sparse parameter mappings contain a value only when the law changes:

```yaml
rate:
  2018: 0.05
  2022: 0.045
```

```python
p = YearParams(STATE_PARAMS, effective_year)
rate = p.num("rate")
```

Rates, thresholds, caps, exemption amounts, phaseout values, inflation factors,
and tax tables must remain in YAML or CSV rather than Python literals.

## State-Year Extrapolation

Import these from `taxsim_py.engine.state_extrapolation`.

| Function | Use it for |
| --- | --- |
| `resolve_state_year(year)` | Getting the last supported state-law year and its inflation factor. |
| `deflate_for_extrapolation(df, flate, extra=())` | Deflating `PROJECTED_YEAR_DEFLATED_COLUMNS` (the inputs and federal results TAXSIM deflates) and any state-derived `extra` columns before applying the last supported state law. |

A state calculator normally resolves the year once, computes the few values
TAXSIM reads undeflated (household income `hy`, self-employment tax), deflates,
computes tax using `effective_year`, and multiplies final `siitax` by `flate`.
Derived columns computed before deflation that TAXSIM deflates go in `extra`.

```python
effective_year, flate = resolve_state_year(year)
df = deflate_for_extrapolation(df, flate, extra=("xx_household_income",))
# Calculate using effective_year.
return df.with_columns(siitax=state_tax * flate)
```

Use `resolve_federal_and_state` from `taxsim_py.engine.federal_state` in callers
that need the iterative federal/state itemized-deduction calculation. Individual
state calculators should not implement that loop.

## Tax Calculation Engines

| Module | Function | Use it for |
| --- | --- | --- |
| `engine.brackets` | `bracket_tax` | Cumulative marginal-rate schedules stored as `[start, rate]` rows. |
| `engine.brackets` | `bracket_rate` | The marginal rate of the bracket containing an income (TAXSIM `look`'s `rt`). |
| `engine.brackets` | `bracket_tax_by_status`, `bracket_rate_by_status` | The same with a schedule per filing status. |
| `engine.brackets` | `scale_brackets` | Multiplying every threshold by an inflation factor. |
| `engine.eitc` | `trapezoid_credit` | Credits with phase-in, plateau, and phaseout regions. |
| `engine.eitc` | `federal_eitc` | The federal EITC from one year's `eitc.csv` rows for a chosen AGI (TAXSIM `eitcr`), e.g. a state recomputing it on its own AGI. |
| `engine.credits` | `child_care_credit_rate` | The post-2020 federal child care credit rate. |
| `engine.credits` | `child_care_credit_rate_pre2021` | The pre-2021 federal child care credit rate. |
| `engine.credits` | `phased_out_nonrefundable_credit` | A linearly phased-out credit capped by tax liability. |
| `engine.capital_gains` | `preferential_rate_tax` | Preferential-rate capital gains and qualified dividends. |
| `engine.amt` | `alternative_minimum_tax` | Federal AMT calculations. |
| `engine.niit` | `net_investment_income_tax` | Net Investment Income Tax calculations. |
| `engine.sales_tax` | `sales_tax_deduction`, `state_sales_tax_deduction` | The optional state and local sales tax deduction; `state_sales_tax_deduction` applies TAXSIM's per-state coefficients (`parameters/national/sales_tax_deduction.yaml`). |

Use `bracket_tax` only for cumulative marginal brackets. Flat-rate lookup,
interpolated, and tax-minus-subtraction tables have different mechanics and
must use the matching shared helper or a clearly named new engine function.

## Payroll Functions

Import payroll and self-employment calculations from
`taxsim_py.engine.payroll_tax`.

| Function | Use it for |
| --- | --- |
| `oasdi_tax` | OASDI tax on wages up to the wage base. |
| `hi_tax` | Medicare tax on wages. |
| `remaining_room_oasdi_tax` | OASDI tax when wages have already consumed part of the wage base. |
| `capped_se_tax` | One spouse's self-employment OASDI or Medicare component. |
| `additional_medicare_tax` | Additional Medicare tax above a household threshold. |
| `marginal_oasdi_rate` | OASDI rate on the last dollar of wages. |
| `marginal_wage_rate_with_se` | Marginal payroll rate when wages and self-employment share a base. |
| `self_employment_tax` | Total self-employment tax for one spouse. |
| `household_self_employment_tax` | Combined self-employment tax for both spouses. |

State calculators that need federal self-employment tax should call
`household_self_employment_tax`; they should not reproduce the wage-base logic.

## Consolidation Candidates

The following mechanics currently appear in more than one state calculator but
do not yet have a shared implementation. Do not add another copy. Extract and
test a shared engine function when the next state needs one.

| Mechanic | Current examples |
| --- | --- |
| Uncapped federal child care credit reconstruction | Kentucky, Louisiana, Maryland, and Maine |
| Federal personal exemption reconstruction | Idaho and Maine |
| Conversion of source upper-bound brackets to `bracket_tax` rows | Arkansas and California |

## Adding a Shared Function

Add a function to the narrowest applicable engine module when two jurisdictions
use the same calculation shape or when a third calculator would otherwise copy
existing logic. Keep policy values out of its implementation, accept Polars
expressions and resolved parameters as arguments, and validate every migrated
caller against the compiled TAXSIM oracle.

Keep the function docstring to one sentence. Put usage details in this file.
