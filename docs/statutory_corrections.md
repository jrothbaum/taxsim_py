# Statutory corrections

This ledger records deliberate differences from the compiled NBER TAXSIM
model. It is part of the calculation contract: every correction needs a reason,
an implementation reference, and tests for corrected and compatible behavior.

## Calculation modes

`calculate_taxes(..., calculation_mode="statutory")` is the default. The YAML
and CSV parameter tables are the canonical law-oriented inputs, and this mode
enables the independently verified corrections listed here. It is not yet a
claim that every federal and state formula has been re-audited against law. So
far this covers federal payroll tax, one federal income-tax credit total, and
one state's (California's) income tax; every other state calculator can still
contain uncorrected TAXSIM-specific rules or independently recompute a
TAXSIM-compatible self-employment-tax deduction.

`calculate_taxes(..., calculation_mode="taxsim")` is the explicit compatibility
override. It preserves the compiled model's behavior, including known bugs,
for replication tests and comparisons with the TAXSIM executable. The
differences documented below are currently implemented as behavior overrides
because most are formula or sequencing differences rather than alternate
parameter values; a future numerical TAXSIM override belongs alongside the
canonical parameter when one is identified.

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

## FED-INCOME-002: Credit for Other Dependents dropped after 2021

- **Status:** corrected in statutory mode; retained in TAXSIM mode.
- **Affected years:** 2022 onward.
- **TAXSIM behavior:** `taxsim_2024_09_21.f` computes `odcred = 500*max(0,
  data(8)-ideps)` for 2018+, but adds it to the credit total (`precrd`) only
  when `lawyr.le.2020` (2021 rebuilds `precrd` from ARPA amounts). For 2022
  and later it is computed and then never used, so a filer with an adult or
  age-17 dependent gets no credit.
- **Corrected behavior:** $500 per dependent who is not a CTC-qualifying
  child, sharing the CTC phaseout (IRC 24(h)(4)-(5)).
- **Reason:** the credit is permanent law. Tax-Calculator and PolicyEngine both
  apply it; on a 3,000-unit CPS sample this was about 100 federal differences
  per year (exact $500 multiples).
- **Implementation:** `taxsim_py.calculators.federal._credits`, controlled by
  `allow_other_dependent_credit_after_2021`.
- **Tests:** `test_other_dependent_credit_applies_after_2021`.

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

## CA-004: Young Child Tax Credit paid without a child under six

- **Status:** corrected in statutory mode; retained in TAXSIM mode.
- **Affected years:** 2019 onward.
- **TAXSIM behavior:** `catax` (lines 2911-2919) computes the credit above the
  $25,000 earnings threshold as `max(0, 1000 - .2*(earned-25000))` without
  testing `data(210)`, the number of children under 6. A California EITC
  recipient whose only child is 10 gets the credit until it phases out.
- **Corrected behavior:** the credit requires a child under six.
- **Reason:** R&TC 17052.1 limits the credit to a qualifying child under six.
- **Implementation:** `taxsim_py.calculators.states.ca.compute_ca_tax`,
  controlled by `require_young_child_for_california_yctc`.
- **Tests:** `tests/test_law_based_checks.py::test_california_yctc_needs_a_young_child`.

## CT-001: Recent personal-credit worksheets use discrete tiers

- **Status:** corrected in statutory mode; retained in TAXSIM mode.
- **Affected years:** 2022 onward.
- **Affected records:** Connecticut filers whose AGI is between personal-credit
  worksheet thresholds, including filers exactly at a threshold.
- **TAXSIM behavior:** the historical calculator linearly interpolates the
  personal-credit percentage between worksheet rows.
- **Corrected behavior:** the statutory path selects the rate for the first
  threshold reached. For example, the 2022 single-filer worksheet gives a 15%
  rate in the $26,500-$31,300 band; it does not interpolate that band with the
  preceding row.
- **Reason:** the official [2022 Connecticut Form CT-1040 instructions](https://portal.ct.gov/-/media/drs/forms/2022/income/2022-ct-1040-instructions_1222.pdf)
  publish discrete AGI bands and rates for the personal credit.
- **Implementation:** `taxsim_py.calculators.states.ct.compute_ct_tax`, using
  `tier_values` for recent statutory years.
- **Tests:** `test_connecticut_statutory_personal_credit_uses_discrete_recent_rates`.

## DC-001: Recent Schedule H limits are dated

- **Status:** corrected in statutory mode; retained in TAXSIM mode.
- **Affected years:** 2022-2024.
- **Affected records:** District of Columbia filers eligible for the property
  tax credit, especially near the AGI phaseout thresholds or credit maximum.
- **TAXSIM behavior:** the historical path forward-fills its last dated
  Schedule H row, leaving 2022+ on 2021's `$1,225` maximum and thresholds.
- **Corrected behavior:** statutory mode uses dated PolicyEngine values for the
  maximum, AGI tiers, elderly threshold, and rent share in each recent year.
- **Reason:** the installed PolicyEngine DC parameter tree publishes the
  year-specific 2022-2024 Schedule H values; the local table had stopped at
  2021.
- **Formula note:** recent statutory mode also follows PolicyEngine's Schedule
  H housing-cost formula, adding 20% of rent to property tax. The historical
  TAXSIM path retains its `max(property tax, rent share)` approximation.
- **Implementation:** `parameters/states/dc/income_tax.yaml` and
  `taxsim_py.calculators.states.dc.compute_dc_tax`.
- **Tests:** `test_dc_statutory_property_credit_uses_dated_recent_limits`.

## OH-001: Recent Ohio brackets replace the projected 2021 schedule

- **Status:** corrected in statutory mode; retained in TAXSIM mode.
- **Affected years:** 2022-2024.
- **Affected records:** Ohio filers above the zero bracket, especially those
  near the 2023 bracket consolidation and 2024 top-rate change.
- **TAXSIM behavior:** the compatibility path continues to use the historical
  2021 schedule, with the existing extrapolation behavior for later years.
- **Corrected behavior:** statutory mode uses dated Ohio brackets, zero-bracket
  thresholds, exemption amounts, and the 30% EITC match for 2022-2024.
- **Reason:** PolicyEngine's dated Ohio tree supplies the recent schedules, and
  Ohio's official [2024 IT-1040 booklet](https://dam.assets.ohio.gov/image/upload/tax.ohio.gov/forms/ohio_individual/individual/2024/it1040-booklet.pdf)
  confirms 0% through `$26,050`, 2.75% through `$100,000`, and 3.5% above
  `$100,000` for 2024.
- **Implementation:** `parameters/states/oh/income_tax.yaml` and
  `taxsim_py.calculators.states.oh.compute_oh_tax`.
- **Tests:** `test_ohio_statutory_uses_recent_brackets_without_changing_taxsim`.

## OK-001: Recent Oklahoma rate tables are dated

- **Status:** corrected in statutory mode; retained in TAXSIM mode.
- **Affected years:** 2022-2024.
- **Affected records:** Oklahoma filers with positive taxable income.
- **TAXSIM behavior:** the compatibility path continues to use the historical
  schedule and projected-year behavior.
- **Corrected behavior:** statutory mode uses the dated single/separate and
  joint/head-of-household tables with rates from 0.25% through 4.75%.
- **Reason:** the installed PolicyEngine-US Oklahoma parameter tree publishes
  these rate tables for all three recent years; the local model stopped at its
  older 2016 table.
- **Implementation:** `parameters/states/ok/income_tax.yaml` and
  `taxsim_py.calculators.states.ok.compute_ok_tax`.
- **Tests:** `test_oklahoma_statutory_uses_recent_rate_tables`.

## OR-001: Recent Oregon brackets and deductions are dated

- **Status:** corrected in statutory mode; retained in TAXSIM mode.
- **Affected years:** 2022-2024.
- **Affected records:** Oregon filers using the standard deduction or recent
  marginal-rate thresholds.
- **TAXSIM behavior:** the compatibility path continues to use the historical
  schedule and projected-year behavior.
- **Corrected behavior:** statutory mode uses dated single, separate, joint,
  and head-of-household brackets; standard deductions; federal-tax-subtraction
  limits; and exemption-credit amounts.
- **Reason:** the installed PolicyEngine-US Oregon parameter tree publishes
  year-specific values that were absent from the local table.
- **Implementation:** `parameters/states/or/income_tax.yaml` and
  `taxsim_py.calculators.states.or_.compute_or_tax`.
- **Tests:** `test_oregon_statutory_uses_recent_brackets_and_standard_deduction`.

## OR-002: Recent age and child-count credit inputs are used

- **Status:** corrected in statutory mode; retained in TAXSIM mode.
- **Affected years:** 2022-2024.
- **Affected records:** Oregon returns with an explicitly supplied child under
  age three or with dependents not all eligible for the working-family care
  credit.
- **Corrected behavior:** statutory mode applies the 12% EITC match when an
  `children_under_3` is positive, and uses `dep13` rather than total
  dependents to cap working-family care-credit recipients. The optional
  semantic count is normally populated by survey preparation; explicit child
  ages remain supported for ordinary callers.
- **Reason:** Oregon's recent rules distinguish a young child and define care
  eligibility by qualifying-child age; both signals are available in the
  TAXSIM-shaped input schema.
- **Implementation:** `taxsim_py.calculators.states.or_.compute_or_tax`.
- **Tests:** `test_oregon_statutory_uses_young_child_eitc_match` and
  `test_optional_child_age_counts_are_not_taxsim_inputs`.

## DC-002: Recent EITC uses qualifying-child counts

- **Status:** corrected in statutory mode; retained in TAXSIM mode.
- **Affected years:** 2015 onward, including 2022-2024.
- **Affected records:** DC returns with dependents who are not qualifying EITC
  children, such as older dependents.
- **TAXSIM behavior:** the historical implementation switches to the
  with-child EITC whenever `depx > 0`.
- **Corrected behavior:** statutory mode uses `dep18`, the age-qualified EITC
  child count, for the with-child versus childless EITC track.
- **Reason:** having a dependent on the return does not by itself establish a
  qualifying child for the earned income credit.
- **Implementation:** `taxsim_py.calculators.states.dc.compute_dc_tax`.
- **Test:** `test_dc_statutory_uses_qualifying_child_count_for_eitc`.

## AR-001: Recent Arkansas schedules and credits are dated

- **Status:** corrected in statutory mode; retained as historical behavior in
  TAXSIM mode.
- **Affected years:** 2022-2024.
- **Corrected behavior:** statutory mode uses the dated Arkansas standard
  deduction and rate/subtraction schedules, the 2022-2023 inflationary-relief
  credit, and the age-representable portion of the qualified-individual credit.
- **Reason:** the recent schedules and credits were absent from the older
  TAXSIM parameter horizon. The inflationary-relief credit is documented in
  Arkansas's 2022 and 2023 individual-return instructions.
- **Boundary:** Arkansas's resident-only worksheet rules and detailed
  low-income tax scales are not fully representable by the TAXSIM 35 inputs;
  they remain an explicit comparison item rather than being silently folded
  into the ordinary brackets. The high-income terminal formulas are now
  represented directly from the official tax tables.
- **Implementation:** `parameters/states/ar/income_tax.yaml` and
  `taxsim_py.calculators.states.ar.compute_ar_tax`.
- **Test:** `test_arkansas_statutory_uses_recent_deductions_rates_and_credits`.

## NH-001: New Hampshire interest-and-dividends rates are dated

- **Status:** corrected in statutory mode; retained as 5% in TAXSIM mode.
- **Affected years:** 2023-2024.
- **Corrected behavior:** statutory mode uses a 4% rate for 2023 and 3% for
  2024; the $2,400 taxpayer and $1,200 age-65 exemption amounts remain in
  force.
- **Reason:** the compiled model's parameter table stops at the historical
  5% rate, while the recent statutory schedule phases the rate down.
- **Implementation:** `parameters/states/nh/income_tax.yaml` and
  `taxsim_py.calculators.states.nh.compute_nh_tax`.
- **Boundary:** New Hampshire education credits and disability/blind inputs
  are outside the current TAXSIM-shaped contract.

## CA-003: Recent California schedules and young-child credits are dated

- **Status:** corrected in statutory mode; retained as historical behavior in
  TAXSIM mode.
- **Affected years:** 2022-2024.
- **Corrected behavior:** statutory mode uses dated California standard
  deductions, rate thresholds, personal/dependent exemption credits, CalEITC
  maximums, and Young Child Tax Credit amounts. YCTC is capped at one credit
  per return even when multiple children under six are present.
- **Reason:** the older California table stopped at 2021 and would either
  project old values or fail when asked for a recent actual-law year.
- **Boundary:** foster-youth eligibility, residency, and detailed FTB
  worksheet identity/status tests are not represented by the TAXSIM 35 inputs;
  California AMT values still use the latest existing shared table where no
  recent TAXSIM-shaped input is available.
- **Implementation:** `parameters/states/ca/income_tax.yaml` and
  `taxsim_py.calculators.states.ca.compute_ca_tax`.
- **Test:** `test_california_statutory_uses_recent_tables_and_caps_yctc_per_return`.

## HI-002: Hawaii recent standard deductions, food credit, and EITC

- **Status:** corrected in statutory mode; TAXSIM mode retains the historical
  2018-2022 behavior.
- **Affected years:** 2023-2024, with the doubled standard deduction beginning
  in 2024.
- **Corrected behavior:** statutory mode uses the 2024 standard deductions,
  the 2023-2024 Food/Excise Tax Credit tables, and a refundable state EITC at
  40% of federal EITC beginning in 2023. The 2022 EITC remains 20% and
  nonrefundable.
- **Reason:** Act 114 and Act 163 changed the Hawaii EITC, while Act 46 raised
  the standard deduction for 2024. The bracket expansion in Act 46 begins in
  2025, so it is not applied to 2022-2024.
- **Implementation:** `parameters/states/hi/income_tax.yaml` and
  `taxsim_py.calculators.states.hi.compute_hi_tax`.
- **Test:** `test_hawaii_recent.py`.
- **Boundary:** Act 115 was a one-time refund for 2021 returns and is not an
  ordinary 2022-2024 tax provision. Detailed residency/status worksheets and
  credits needing additional inputs remain outside the TAXSIM 35 contract.

## STATE-2022-AGED: age-65+ rules in actual-law years

- **Affected years:** 2022-2024, statutory mode only.
- **Corrected behavior:** AZ senior property-tax credit limited to taxes paid; IN
  elderly credit as a step schedule; NM low-income rebate tables for 2022-2024; KY
  pension cap per spouse, combined filing, poverty-guideline family-size credit;
  OH joint-filing credit for pension income; NJ pension exclusion phase-down; CA
  credit for age 65+; MN subtraction for the elderly or disabled; ND marriage credit
  with shared pensions; MI tier-three standard deduction and phased-in retirement subtraction; CO high-income add-backs (federal deductions above $12,000/$16,000, QBI deduction); IA/GA/MT/LA/AR/IL/AL/UT age-65+ items (see git history of the PolicyEngine comparison doc); RI $20,000 retirement exclusion from 2023 and indexed
  limits; WV family credit tested on federal AGI.
- **Reason:** each follows the state's form instructions or statute; TAXSIM's frozen
  2021 tables and interpolation do not.
- **Assumption:** TAXSIM has one household `pensions` total, so per-spouse caps (KY)
  and spouse-income tests (OH, ND) treat it as split evenly between joint spouses.
- **Implementation:** the state calculators under `taxsim_py.calculators.states` and
  their `parameters/states/<st>/income_tax.yaml` files.

## CA-005 / NJ-001: State childless EITC minimum age is 18

- **Status:** corrected in statutory mode; retained in TAXSIM mode.
- **Affected years:** California 2018 onward; New Jersey 2020 onward.
- **TAXSIM behavior:** California pays the credit at any age (a 15-year-old with
  $5,000 of wages gets $208.66 in 2021). New Jersey uses the federal credit, so
  the federal age test (25 through 64) applies.
- **Corrected behavior:** California requires age 18 for filers without a
  qualifying child, with no maximum age. New Jersey requires 21 and under 65
  for 2020, and 18 with no maximum age from 2021. An unreported age passes.
  New Jersey pays a childless filer who meets every federal requirement
  except the age test a flat amount, 40% of the federal childless maximum
  rounded to dollars ($224 for 2022, $601 for 2021; NJ-1040 line 58); other
  filers get 40% of their federal credit.
- **Reason:** R&TC 17052 replaces the federal "25 but not 65" with "18" from
  2018. N.J.S.A. 54A:4-7 and the NJ-1040 instructions (line 58) set the New
  Jersey ages and the flat amount.
- **Implementation:** `taxsim_py.calculators.states.ca` and `.nj`, controlled by
  `apply_state_childless_eitc_minimum_age`; New Jersey reads the federal
  `eitc_before_age_test` / `eitc_before_filer_test` columns.
- **Tests:** `tests/test_calculation_modes.py::test_state_childless_eitc_minimum_age_is_18_in_california_and_new_jersey`.

## MA-001: Payroll tax deduction is per spouse

- **Status:** corrected in statutory mode; retained in TAXSIM mode.
- **Affected years:** all years with the deduction.
- **TAXSIM behavior:** the Social Security and Medicare deduction uses only the
  primary earner's payroll tax (`comnew(183)`), capped at $2,000 for the return.
- **Corrected behavior:** each spouse on a joint return deducts their own
  employee payroll tax, up to $2,000 each. A couple earning $50,000 and
  $75,000 gets the second $2,000 ($100 of tax at 5%).
- **Reason:** Form 1 lines 11a and 11b; amounts may not be combined or
  transferred between spouses.
- **Implementation:** `taxsim_py.calculators.states.ma`, with the spouse's
  figures from `engine.payroll_tax.taxsim_payroll`.
- **Tests:** `tests/test_calculation_modes.py::test_massachusetts_statutory_payroll_deduction_is_per_spouse`.

## HI-003: Pensions never excluded

- **Affected years:** all years in statutory mode.
- **Corrected behavior:** Hawaii's AGI excludes `pensions`, as the state exempts them.
- **Reason:** TAXSIM's pension subtraction reads `data(72)`, which the input reader never sets (pensions are `data(20)`), so no pension is ever excluded.
- **Implementation:** `compute_hi_tax`, behavior flag `exclude_hawaii_pensions`.

## WA-001: Washington credit and capital gains tax (extension, not a correction)

- **Affected years:** 2022-2024, statutory mode only.
- **What TAXSIM does:** nothing. In `taxsim_2022_10_21.f` (state dispatch, `48 continue` / `goto 80`) Washington calls no tax routine, so state tax is 0 in every year, like Texas and Wyoming.
- **Added in statutory mode:** the Working Families Tax Credit (RCW 82.08.0206), a refundable credit of $300-$1,290 by number of children (2022-2024 amounts), phased out below the federal EITC income ceiling with a $50 floor, reported as negative state tax. This is a household benefit TAXSIM never modelled, so statutory mode can return a nonzero Washington result where TAXSIM mode returns 0.
- **Not modelled (decision 2026-10-01: leave as is for now):** the 7% capital gains excise tax on long-term gains over $250,000 (2022; $262,000 in 2023, $270,000 in 2024). TAXSIM's `ltcg` cannot separate the real estate and retirement-account gains the tax exempts, and TAXSIM itself never taxed them. PolicyEngine taxes all `ltcg`, so a household with large gains differs from PolicyEngine by about 7% of the gain above the threshold. Revisit if a high-gain Washington case matters.
- **Implementation:** `taxsim_py.calculators.states.wa.compute_wa_tax`, `parameters/states/wa/income_tax.yaml`.
- **Test:** `test_washington_working_families_credit_only_in_statutory_mode`.

## 2025-LAW: provisions added for tax year 2025

Federal: child credit $2,200, senior deduction, SALT cap phase-down (`parameters/national/*`). States: see
policyengine_recent_state_comparison.md. Statutory mode only for provisions that differ from TAXSIM's
conventions (for example Maine's and DC's refusal of the larger federal standard deduction, South Carolina's
non-conformity addbacks); parameter-only updates apply in every mode. Washington's Working Families credit
extends to 2025 (see WA-001).
