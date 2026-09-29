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
law. So far this covers federal payroll tax, one federal income-tax credit
total, and one state's (California's) income tax; every other state calculator
can still contain uncorrected TAXSIM-specific rules or independently
recompute a TAXSIM-compatible self-employment-tax deduction.

The behavior switches are centralized in `src/taxsim_py/behavior.py`. Formula
code must use that profile rather than testing mode strings directly. State
calculators receive it as a third `behavior` argument (added when the first
state-level correction, CA-001, needed it) - every state's `compute_XX_tax`
accepts it even when, like nearly all of them today, it just ignores it.

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

## FED-INCOME-001: Pre-1998 nonrefundable credit total misreports the elderly credit

- **Status:** corrected in statutory mode; retained in TAXSIM mode.
- **Affected years:** 1987-1997 (before 1998, TAXSIM applies no nonrefundable
  credits against `fiitax` at all, so the elderly credit never reduces
  federal tax in either mode - only what gets *reported* as the credit total
  changes here).
- **Affected records:** aged taxpayers with a positive elderly credit
  (`federal_elder`) in 1987-1997, read by state calculators that consume the
  nonrefundable-credit total (LA, UT, OR, ND, AL, AZ - for example, Utah's
  federal tax deduction).
- **TAXSIM behavior:** the reported total (`comnew(58)`) is tax-before-credits
  when the elderly credit happens to exceed it, and 0 otherwise - never the
  actual credit amount. A single 68-year-old with $8,000 of wages in 1995 has
  a $712.50 elderly credit but TAXSIM reports only $97.50 (that year's tax);
  the same filer with $12,000 of wages has a $412.50 elderly credit but
  TAXSIM reports $0.
- **Corrected behavior:** report the real computed elderly credit
  (`federal_elder`) as the total.
- **Reason:** found by probing the executable, not from a specific published
  worksheet - the formula is neither the credit nor a plausible substitute for
  it, and produces a wrong federal-tax base wherever a state reads it.
- **Implementation:** `taxsim_py.calculators.federal._net_tax`, controlled by
  `count_pre_1998_nonrefundable_credits_correctly`.
- **Tests:** `test_fed_income_001_statutory_mode_reports_the_real_pre1998_elderly_credit`.

## CA-001: 1979-1986 unemployment compensation removed from AGI twice

- **Status:** corrected in statutory mode; retained in TAXSIM mode.
- **Affected years:** 1979-1986.
- **Affected records:** California filers with taxable unemployment
  compensation above that era's income-based exclusion threshold.
- **TAXSIM behavior:** total income (`ca_totinc`) never includes taxable
  unemployment compensation, but the AGI adjustment still subtracts it
  anyway - removing it a second time. A filer with $30,000 of wages and
  $4,000 of taxable unemployment gets a $26,000 CA AGI instead of $30,000.
- **Corrected behavior:** the adjustment no longer subtracts taxable
  unemployment compensation for 1979-1986 (it was never added to total
  income in the first place, so the correct fix is to stop subtracting it,
  not to add it - adding it instead would also change the base the
  1985-1986 aged exclusion computes from, an unrelated provision).
- **Reason:** unemployment compensation should appear in CA AGI once or not
  at all, never as a pure subtraction with no matching addition.
- **Implementation:** `taxsim_py.calculators.states.ca.compute_ca_tax`,
  controlled by `include_unemployment_in_california_total_income`.
- **Tests:** `test_ca001_statutory_mode_stops_double_subtracting_unemployment`.

## CA-002: 1987+ minimum tax excludes business and rental income

- **Status:** corrected in statutory mode; retained in TAXSIM mode.
- **Affected years:** 1987 onward.
- **Affected records:** filers with positive self-employment income or
  Schedule E (rental/S-corp) income who owe California's minimum tax.
- **TAXSIM behavior:** the minimum-tax base (`alminy`) subtracts positive
  self-employment income and Schedule E income before applying the
  exemption and rate. California's actual minimum-tax base (Schedule P)
  keeps that income in. A 2004 couple with $83,000 of pensions, $1,000 of
  rent and $99,997 of property tax sees each dollar of rent lower their CA
  tax by 7 cents under TAXSIM's formula.
- **Corrected behavior:** the minimum-tax base no longer subtracts positive
  self-employment or Schedule E income.
- **Reason:** Schedule P's income base does not exclude these items; nothing
  in California law parallels TAXSIM's subtraction.
- **Implementation:** `taxsim_py.calculators.states.ca.compute_ca_tax`,
  controlled by `include_business_and_rental_income_in_california_minimum_tax`.
- **Tests:** `test_ca002_statutory_mode_keeps_business_and_rental_income_in_minimum_tax`.

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

FED-INCOME-001, CA-001 and CA-002 were identified from probing the executable
directly (`pending_issues.md`'s "TAXSIM errors" list), not from a CPS sample,
and are verified against the documented worked examples rather than an
independent-model comparison - PolicyEngine and Tax-Calculator either don't
model California at all or don't share TAXSIM's specific formula, so they
can't confirm or refute these the way the payroll corrections above were
confirmed against Tax-Calculator.

Run the focused checks with:

```bash
uv run --group test pytest tests/test_calculation_modes.py tests/test_independent_models.py -q
```
