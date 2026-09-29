# Statutory corrections

This ledger records deliberate differences from the compiled NBER TAXSIM
model. It is part of the calculation contract: every correction needs a reason,
an implementation reference, and tests for corrected and compatible behavior.

## Calculation modes

`calculate_taxes(..., calculation_mode="taxsim")` is the default. It preserves
the compiled model's behavior for backward replication, including known bugs.

`calculate_taxes(..., calculation_mode="statutory")` starts from the same model
but enables only the independently verified corrections listed here. It is not
yet a claim that every federal and state formula has been re-audited against
law. In this first implementation, the corrections affect federal payroll tax
and federal income-tax calculations that consume self-employment tax. State
calculators can still contain TAXSIM-specific rules or independently recompute
a TAXSIM-compatible self-employment-tax deduction.

The behavior switches are centralized in `src/taxsim_py/behavior.py`. Formula
code must use that profile rather than testing mode strings directly.

## FED-PAYROLL-001: Additional Medicare duplicated self-employment income

- **Status:** corrected in statutory mode; retained in TAXSIM mode.
- **Affected years:** 2013 onward, when Additional Medicare Tax applies.
- **TAXSIM behavior:** `sstax` adds combined primary and spouse
  self-employment income during both iterations of its earner loop. A single
  filer with $200,000 of gross self-employment income is therefore treated as
  having $369,400 of net self-employment earnings for this tax and is charged
  $1,524.60.
- **Corrected behavior:** each taxpayer's nonnegative net self-employment
  earnings are counted once and combined with the return's Medicare wages. The
  example has $184,700 of net earnings and owes no Additional Medicare Tax.
- **Reason:** Form 8959 combines the return's Medicare wages and
  self-employment income once. It also says not to consider a self-employment
  loss. See the [2021 Form 8959 instructions](https://www.irs.gov/pub/irs-prior/i8959--2021.pdf).
- **Implementation:** `taxsim_py.engine.payroll_tax.taxsim_payroll`, controlled
  by `count_self_employment_once_for_additional_medicare`.
- **Tests:** `test_statutory_mode_corrects_verified_self_employment_payroll_cases`
  and `test_taxsim_mode_retains_compiled_self_employment_behavior`.

## FED-PAYROLL-002: OASDI cap discounted prior W-2 wages

- **Status:** corrected in statutory mode; retained in TAXSIM mode.
- **Affected records:** returns with both wages and self-employment income near
  or above the Social Security wage base.
- **TAXSIM behavior:** when a self-employment item follows wages, `sstax`
  multiplies the entire running earnings total, including W-2 wages, by the
  92.35% Schedule SE factor. This creates wage-base room that does not exist.
- **Corrected behavior:** each income item enters the running cap at its own
  factor: 100% for wages and 92.35% for gross self-employment income.
- **Reason:** the Social Security limit applies to the combination of wages and
  net earnings from self-employment, not to 92.35% of wages. The IRS describes
  the cap as applying to combined wages, tips, and net earnings in
  [Publication 334](https://www.irs.gov/publications/p334).
- **Implementation:** `taxsim_py.engine.payroll_tax._capped_items`, controlled
  by `coordinate_oasdi_on_net_earnings`.
- **Tests:** the `wage_se_cap` record in both calculation-mode tests and the
  independent Tax-Calculator comparison.

## FED-PAYROLL-003: Missing $400 Schedule SE minimum

- **Status:** corrected in statutory mode; retained in TAXSIM mode.
- **Affected records:** taxpayers whose net earnings from self-employment are
  positive but below $400.
- **TAXSIM behavior:** SECA is calculated from the first positive dollar.
- **Corrected behavior:** self-employment tax is zero when 92.35% of the
  taxpayer's net business income is below $400.
- **Reason:** the filing and tax computation threshold is $400 of net earnings
  from self-employment. Joint filers with self-employment income for both
  spouses file a separate Schedule SE for each spouse. See the
  [2021 Schedule SE instructions](https://www.irs.gov/pub/irs-prior/i1040sse--2021.pdf).
- **Implementation:** `taxsim_py.engine.payroll_tax.taxsim_payroll`, controlled
  by `enforce_schedule_se_minimum`.
- **Tests:** the `below_minimum` record in both calculation-mode tests and the
  independent Tax-Calculator comparison.

## FED-PAYROLL-004: Negative self-employment tax

- **Status:** corrected in statutory mode; retained in TAXSIM mode.
- **Affected records:** taxpayers with a net self-employment loss.
- **TAXSIM behavior:** negative business items can produce negative SECA and
  reduce total payroll tax below wage FICA or below zero.
- **Corrected behavior:** business results are netted within each taxpayer,
  floored at zero, and allocated proportionally over positive input categories
  so QBI-related intermediate values retain their meaning. A loss also does not
  reduce the Additional Medicare earnings base.
- **Reason:** Schedule SE produces no SE tax from a net loss, and Form 8959
  explicitly excludes a self-employment loss from Additional Medicare Tax.
- **Implementation:** `taxsim_py.engine.payroll_tax.taxsim_payroll`, controlled
  by `prevent_negative_self_employment_tax`.
- **Tests:** the `losses` record in both calculation-mode tests and the
  independent Tax-Calculator comparison.

## Verification record

The four corrections were identified from a 10,000-unit 2021 CPS ASEC sample.
Together they classify all 145 payroll-tax differences from PSL
Tax-Calculator in that sample. The focused four-record suite produces payroll
taxes of $23,063.50, $24,835.98, $0, and $0 in statutory mode, matching
Tax-Calculator to the cent. TAXSIM mode retains the corresponding compiled
results of $24,588.10, $27,076.35, $42.38865, and -$1,554.2505.

On the full 10,000-unit sample, statutory mode reduces Tax-Calculator payroll
differences from 145 to 3. The three residual records have $2 or $300 of
self-employment income for one spouse and more than $5,000 for the other.
Tax-Calculator applies the $400 threshold to their combined income; the port
applies it separately because the Schedule SE instructions require each spouse
to file a separate schedule. These residuals are therefore classified as an
independent-comparator limitation, not changed to force agreement.

A separate 1,000-unit PolicyEngine comparison under 2021 law left one payroll
difference of $0.02. Federal and state liability differences remain and are
tracked in `pending_issues.md`; they should not be inferred to be fixed by this
payroll correction set.

Run the focused checks with:

```bash
uv run --group test pytest tests/test_calculation_modes.py tests/test_independent_models.py -q
```
