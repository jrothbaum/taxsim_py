# Statutory corrections

This ledger records the deliberate differences from the compiled NBER TAXSIM.
`calculation_mode="statutory"` (the default) applies them and uses the canonical
parameter tables; `calculation_mode="taxsim"` keeps the compiled model's behavior,
including its known bugs, for replication and oracle tests. The switches live in
`src/taxsim_py/behavior.py`; formula code reads that profile, never the mode string.
Every correction needs a reason, an implementation reference, and tests for both
behaviors. Parameter-only updates (new years, dated tables) apply in every mode.

## Federal payroll tax

These four came from a 10,000-unit 2021 CPS sample, where together they explain all
145 payroll differences from Tax-Calculator. Statutory mode matches Tax-Calculator
to the cent on the focused records and leaves 3 residuals (Tax-Calculator applies
the $400 threshold to combined spousal income; the port applies it per spouse, as
the Schedule SE instructions require). Code: `engine.payroll_tax.taxsim_payroll`;
tests: `tests/test_calculation_modes.py` (the `single_high_se`, `wage_se_cap`,
`below_minimum` and `losses` records).

| ID | TAXSIM | Statutory mode | Switch | Source |
|:--|:--|:--|:--|:--|
| FED-PAYROLL-001 (2013+) | Additional Medicare counts combined self-employment income in both earners' loops: $200,000 of self-employment gives $1,524.60 | Each taxpayer's non-negative net earnings counted once ($184,700, no tax) | `count_self_employment_once_for_additional_medicare` | [Form 8959 instructions](https://www.irs.gov/pub/irs-prior/i8959--2021.pdf) |
| FED-PAYROLL-002 | The 92.35% factor is applied to the whole running total, including W-2 wages, creating wage-base room | Each item enters the OASDI cap at its own factor (100% wages, 92.35% self-employment) | `coordinate_oasdi_on_net_earnings` | [Pub. 334](https://www.irs.gov/publications/p334) |
| FED-PAYROLL-003 | Self-employment tax from the first dollar | Zero when 92.35% of net business income is under $400, per spouse | `enforce_schedule_se_minimum` | [Schedule SE instructions](https://www.irs.gov/pub/irs-prior/i1040sse--2021.pdf) |
| FED-PAYROLL-004 | A loss gives negative self-employment tax, below wage FICA or below zero | Business results netted per taxpayer, floored at zero, allocated over positive items; a loss does not reduce the Additional Medicare base | `prevent_negative_self_employment_tax` | Schedule SE, Form 8959 |

## Federal income tax

- **FED-INCOME-001 (1987-1997).** TAXSIM applies no nonrefundable credits before
  1998 but reports a credit total (`comnew(58)`) that is tax before credits when the
  elderly credit exceeds it and 0 otherwise (a 68-year-old with $8,000 of wages in
  1995 has a $712.50 credit and is reported $97.50). States that read it (LA, UT,
  OR, ND, AL, AZ) get the wrong federal base. Statutory mode reports the real
  `federal_elder`. Found by probing the executable. Switch:
  `count_pre_1998_nonrefundable_credits_correctly`; code: `federal._net_tax`.
- **FED-INCOME-002 (2022+).** The Credit for Other Dependents is computed
  (`500*max(0, data(8)-ideps)`) and then never added to the credit total after 2020, so
  adult and age-17 dependents earn nothing. Statutory mode gives $500 per dependent
  who is not a CTC-qualifying child, with the CTC phaseout (IRC 24(h)(4)-(5)); about
  100 CPS units per year. Switch: `allow_other_dependent_credit_after_2021`; code:
  `federal._credits`.

## California

- **CA-001 (1979-1986).** Taxable unemployment compensation is never in total
  income but is subtracted again in the AGI adjustment ($30,000 of wages and $4,000
  of unemployment gives AGI $26,000). Statutory mode stops subtracting it. Switch:
  `include_unemployment_in_california_total_income`.
- **CA-002 (1987+).** The minimum-tax base (`alminy`) subtracts positive
  self-employment and Schedule E income; Schedule P keeps them (a 2004 couple's
  rent lowers their tax by 7 cents a dollar). Statutory mode keeps them. Switch:
  `include_business_and_rental_income_in_california_minimum_tax`.
- **CA-004 (2019+).** The Young Child Tax Credit is computed from earnings without
  testing for a child under six (`catax` lines 2911-2919). Statutory mode requires
  one (R&TC 17052.1) and pays one credit per return, where TAXSIM multiplies by the
  number of young children. Switches: `require_young_child_for_california_yctc`,
  `pay_california_yctc_per_young_child`.
- **CA-005 / NJ-001 (CA 2018+, NJ 2020+).** State childless credits start at age
  18, not the federal 25. TAXSIM pays California at any age (a 15-year-old with
  $5,000 of wages gets $208.66) and gives New Jersey only the federal age test.
  California has no maximum age (R&TC 17052). New Jersey requires 21 and under 65
  for 2020 and 18 with no maximum from 2021, and pays a childless filer who meets
  every federal rule except age a flat amount, 40% of the federal childless maximum
  rounded ($224 for 2022, $601 for 2021; NJ-1040 line 58); others get 40% of their
  federal credit. An unreported age passes. Switch:
  `apply_state_childless_eitc_minimum_age`; code: `states.ca`, `states.nj`.

## Other states

- **MA-001.** TAXSIM deducts only the primary earner's payroll tax, capped at
  $2,000 for the return. Each spouse deducts their own employee payroll tax up to
  $2,000 (Form 1 lines 11a/11b), so a couple earning $50,000 and $75,000 gets the
  second $2,000 ($100 of tax at 5%).
- **CO-001 (2022+).** TAXSIM subtracts pensions plus Social Security only when
  someone is 65 or older, under one combined cap. Each taxpayer subtracts pension
  income up to $20,000 (55-64) or $24,000 (65+), less their Social Security
  subtraction; those 65+ subtract all taxable Social Security, those 55-64 within
  the cap (all of it from 2025 when federal AGI is at most $75,000, $95,000 joint;
  HB24-1142). Pension and Social Security inputs are split equally between spouses.
  Sources: C.R.S. 39-22-104(4)(f)-(g), DR 0104AD.
- **HI-003.** TAXSIM's pension subtraction reads `data(72)`, which the input reader
  never sets (pensions are `data(20)`), so Hawaii never excludes a pension. Statutory
  mode excludes `pensions`. Switch: `exclude_hawaii_pensions`.
- **WA-001 (extension, not a correction).** TAXSIM calls no Washington tax routine, so
  state tax is 0. Statutory mode adds the Working Families Tax Credit (RCW 82.08.0206),
  a refundable $300-$1,290 by number of children, phased out below the EITC ceiling
  with a $50 floor, as negative state tax. The 7% capital gains excise tax over
  $250,000 is not modelled: TAXSIM's `ltcg` cannot separate the exempt real estate
  and retirement gains. PolicyEngine taxes all `ltcg`, so large-gain households differ
  by about 7% of the gain above the threshold.
- **CT-001 (2022+).** The personal-credit worksheet is read as discrete AGI bands (the
  first threshold reached), not TAXSIM's linear interpolation between rows
  ([2022 CT-1040 instructions](https://portal.ct.gov/-/media/drs/forms/2022/income/2022-ct-1040-instructions_1222.pdf)).
- **DC-002 (2015+).** The EITC with-child track uses the age-qualified child count
  `dep18`, not `depx > 0`.
- **OR-002.** The 12% under-three EITC match uses `children_under_3`, and the care
  credit cap uses `dep13`.

## Dated 2022+ state tables (STATE-DATED)

TAXSIM's tables stop at 2021 and its later years scale 2021 law. Statutory mode runs
actual law for 2022-2025 from PolicyEngine-US parameters checked against state forms;
taxsim mode keeps the projection. Boundaries are the provisions TAXSIM has no input
for (see the [PolicyEngine comparison](policyengine_recent_state_comparison.md)).

| State (IDs) | Dated values |
|:--|:--|
| AR (AR-001) | Standard deduction, rate and subtraction schedules, low-income tax tables, 2022-2023 inflationary-relief credit |
| CA (CA-003) | Standard deductions, rate thresholds, exemption credits, CalEITC maximums, Young Child Tax Credit amounts |
| DC (DC-001) | Schedule H maximum, AGI tiers, elderly threshold; rent counts at 20% added to property tax |
| HI (HI-002) | 2024 doubled standard deductions, 2023-2024 Food/Excise credit tables, refundable 40% EITC from 2023 (Acts 46, 114, 163) |
| NH (NH-001) | Interest-and-dividends rate 4% (2023), 3% (2024), 0 (2025); exemptions unchanged |
| OH (OH-001) | Brackets, zero-bracket thresholds, exemptions, 30% EITC match ([2024 IT-1040](https://dam.assets.ohio.gov/image/upload/tax.ohio.gov/forms/ohio_individual/individual/2024/it1040-booklet.pdf)) |
| OK (OK-001) | Single/separate and joint/head-of-household rate tables (0.25%-4.75%) |
| OR (OR-001) | Brackets, standard deductions, federal-tax-subtraction limits, exemption credits |
| Others | CO, CT, GA, ID, IL, IN, KY, MI, NC, PA, UT and the remaining income-tax states: see the PolicyEngine comparison |

## Age-65+ rules in actual-law years (STATE-2022-AGED)

Statutory mode only, 2022 onward; each follows the state's form or statute. AZ senior
property-tax credit limited to taxes paid; IN elderly credit as a step schedule; NM
low-income rebate tables; KY pension cap per spouse, combined filing, and
poverty-guideline family-size credit; OH joint-filing credit for pension income; NJ
pension exclusion phase-down; CA credit for age 65+; MN subtraction for the elderly or
disabled; ND marriage credit with shared pensions; MI tier-three standard deduction and
phased-in retirement subtraction; CO high-income add-backs; RI $20,000 retirement
exclusion from 2023 with indexed limits; WV family credit tested on federal AGI; and
smaller items in IA, GA, MT, LA, AR, IL, AL and UT. TAXSIM has one household `pensions`
total, so per-spouse caps and spouse-income tests treat it as split evenly.

## 2025 (2025-LAW)

Federal: child credit $2,200, the senior deduction, and the SALT cap phase-down
(`parameters/national/*`). States: see the PolicyEngine comparison. Provisions that
depart from TAXSIM's conventions (Maine's and DC's refusal of the larger federal
standard deduction, South Carolina's addbacks) are statutory mode only; parameter
updates apply in every mode. Washington's credit extends to 2025.
