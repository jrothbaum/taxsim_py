# Statutory corrections

This ledger records the deliberate differences from the compiled NBER TAXSIM.
`calculation_mode="statutory"` (the default) applies them and uses the canonical
parameter tables; `calculation_mode="taxsim"` keeps the compiled model's behavior,
including its known bugs, for replication and oracle tests. The switches live in
`src/taxsim_py/behavior.py`; formula code reads that profile, never the mode string.
Every correction needs a reason, an implementation reference, and tests for both
behaviors. Parameter-only updates (new years, dated tables) apply in every mode.
The full list of apparent TAXSIM errors, including those reproduced in both modes,
is in [Known TAXSIM errors](taxsim_errors.md).

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
| FED-PAYROLL-005 | The lookup gives the primary earner both non-professional business incomes and the spouse both professional ones, so the wage base and the QBI netting use the wrong person's income ($50,000 of spouse business income over a primary earner already above the 2019 base gets no OASDI tax) | Each earner's own business and professional income; South Carolina (2018 on) and Virginia (2000 on) split spouses' business income the same way | `assign_business_income_to_its_earner` | Schedule SE |

## Federal income tax

Tests for these and the state corrections below are rows of
`tests/statutory_correction_table.py`, run by `tests/test_statutory_corrections.py`.

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
- **FED-INCOME-003.** Social Security provisional income adds `transfers`, which the
  input reader stores where tax-exempt interest used to be (`data(41)`). The law adds
  tax-exempt interest, which TAXSIM has no input for. Statutory mode adds nothing;
  the same applies to the Iowa (2014 on) and Montana worksheets. Switch:
  `exclude_transfers_from_provisional_income`.
- **FED-INCOME-004 (1987-1999).** The AMT is computed but added to tax only from
  2000. Switch: `add_amt_before_2000`.
- **FED-INCOME-005 (1987-1997).** The child and dependent care credit and the credit
  for the elderly are computed but never subtracted (the code sits inside the 1998
  child tax credit block). Statutory mode applies both and reports them as the
  nonrefundable credit total, which supersedes FED-INCOME-001. Switch:
  `apply_nonrefundable_credits_before_1998`.
- **FED-INCOME-006.** The elderly credit's AGI threshold is $7,500 for a joint return
  with one spouse 65 or older; IRC 22(d) sets $10,000 for any joint return. Switch:
  `use_joint_elderly_credit_threshold`.
- **FED-INCOME-007 (1991-1992).** AMT income adds back the itemized-deduction
  limitation, from which the AMT was exempt from 1991. Switch:
  `exempt_amt_from_itemized_limit_1991_1992`.
- **FED-INCOME-008 (1990 on).** The young-filer AMT exemption cap applies to anyone
  under the age limit; IRC 59(j) limits it to children subject to the kiddie tax.
  Statutory mode applies it to dependent filers only. Switch:
  `cap_amt_exemption_for_dependents_only`.
- **FED-INCOME-009 (1998-2000).** The additional child credit for three or more
  children reads a payroll figure that is never set, so it is always $0. Statutory
  mode pays the unused credit up to the employee share of payroll tax (and half of
  self-employment tax) less the EITC (IRC 24(d) as enacted by P.L. 105-34). Switch:
  `allow_additional_child_credit_before_2001`.
- **FED-INCOME-010 (before 1987).** Business, professional and S corporation income
  are left out of income (in 1960-1986 law). Switch:
  `include_business_income_before_1987`.
- **FED-INCOME-011 (1979).** The dividend exclusion is read from an input that is
  never set, so it is $0; the law kept $100 ($200 joint). The states that add the
  exclusion back read the same rule (`engine.state.federal_dividend_exclusion_limit`).
  Switch: `apply_1979_dividend_exclusion`.
- **FED-INCOME-012 (1977-1986).** The credit for the elderly is capped at an input
  that is never set, so it is $0. Statutory mode pays 15% of $2,500 ($3,750 for a
  couple both 65 or older; from 1984 $5,000 and $7,500) less nontaxable Social
  Security and half of AGI over $7,500 ($10,000 joint) (IRC 37 as amended in 1976;
  IRC 22 from 1984). Switch: `apply_elderly_credit_before_1987`.
- **FED-INCOME-013 (1983-1986).** The minimum-tax base leaves out qualified housing
  interest, which the law allowed. Switch:
  `deduct_mortgage_interest_from_1983_minimum_tax`.
- **FED-INCOME-014 (1977-1978).** Itemizers' preference income for the maximum tax on
  earned income includes wages and unemployment compensation. Switch:
  `exclude_wages_from_1977_preference_income`.
- **FED-RATE-001 (1977-2000).** TAXSIM's analytic federal marginal rate
  (`comnew(72)`), which Nebraska, North Dakota, Rhode Island and Vermont report as
  their bracket rate, has several slips: no rate for moderate 1977-1978 gains, no
  1979-1982 minimum-tax rate, the 1981 rate reduction overwritten, the 1993-1996
  gains cap ignored or misapplied, the child tax credit's 5 points added as 0.05,
  the elderly credit's slope mixing a percent into a fraction, and the EITC slope
  kept when the credit is disqualified. Statutory mode corrects each; tax is
  unaffected. Switch: `correct_analytic_federal_rate`.

Not changed: the 2021 married-separate EITC (the law requires living apart for six
months, which TAXSIM has no input for), and the analytic rate's Social Security
term (the branch TAXSIM always takes is the correct formula).

## Rules shared by several states

- **STATE-CCC-001 (before 1987).** District of Columbia (1982-1986), Iowa
  (1977-1981, 1986), Minnesota and New York credits are shares of the federal child
  care credit, which TAXSIM's early federal routine never fills in. Statutory mode
  uses the 1977-1986 federal credit. Switch: `read_federal_child_care_credit_before_1987`.
- **STATE-HHY-001.** TAXSIM's household income (`data(159)`) leaves out
  self-employment income. Switch: `include_self_employment_in_household_income`.
- **STATE-HHY-002.** Household income includes a leftover $1 from the input reader,
  and the state worksheet (`idtl=2`) is printed from the one-cent marginal-rate run.
  Statutory mode drops the $1 and reports the base calculation. Switches:
  `drop_household_income_record_adjustment`, `report_base_state_worksheet`.
- **STATE-SOCSEC-001.** The payroll tax states deduct (`socsec`; Kansas, Missouri,
  Vermont) charges 1.45% on wages above the Social Security base in every year.
  Statutory mode stops at the Medicare base (the same base before 1991). Switch:
  `cap_medicare_in_state_payroll_deduction`.

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
- **CA-006 (2018+).** TAXSIM starts California itemized deductions from the
  federal SALT-capped total. California never adopted the cap (Schedule CA (540)):
  real estate and other taxes are deductible in full, its own income tax not at
  all. Switch: `ignore_federal_salt_cap_in_california`.

## Other states

- **AL-001 (2013+).** The itemized deduction for federal payroll tax takes half of
  FICA and then adds the employee-only Additional Medicare tax, which TAXSIM's FICA
  total already includes, so it is counted one and a half times. Statutory mode
  counts it once. Switch: `count_additional_medicare_once_in_alabama_fica_deduction`.
- **AZ-001.** The senior property tax credit: the income test excludes Social
  Security (which TAXSIM's household income includes) and the credit is limited to
  property tax paid (A.R.S. 43-1072). Applied in every statutory year. Switch:
  `limit_arizona_property_credit_per_statute`.
- **IL-001 (2017+).** No exemption allowance when income exceeds $250,000
  ($500,000 joint) (35 ILCS 5/204(g)); TAXSIM allows it at any income. Switch:
  `apply_illinois_exemption_income_limit`.
- **KS-001 (2021+).** TAXSIM stops letting Kansas filers itemize after 2020.
  Kansas itemizing follows the federal choice in every year (K-40 Schedule A).
  Switch: `allow_kansas_itemizing_after_2020`.
- **KY-001 (1995+).** The pension exclusion cap is per taxpayer (KRS
  141.010(10)), with a joint return's pensions split evenly; TAXSIM applies one cap
  per return. Applied in every statutory year. Switch:
  `apply_kentucky_pension_cap_per_taxpayer`.
- **MA-001.** TAXSIM deducts only the primary earner's payroll tax, capped at
  $2,000 for the return. Each spouse deducts their own employee payroll tax up to
  $2,000 (Form 1 lines 11a/11b), so a couple earning $50,000 and $75,000 gets the
  second $2,000 ($100 of tax at 5%). Switch:
  `deduct_massachusetts_employee_payroll_tax_per_spouse`.
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
  by about 7% of the gain above the threshold. Switch:
  `add_washington_working_families_credit`.
- **CT-001 (2022+).** The personal-credit worksheet is read as discrete AGI bands (the
  first threshold reached), not TAXSIM's linear interpolation between rows
  ([2022 CT-1040 instructions](https://portal.ct.gov/-/media/drs/forms/2022/income/2022-ct-1040-instructions_1222.pdf)).
- **DC-002 (2015+).** The EITC with-child track uses the age-qualified child count
  `dep18`, not `depx > 0`. Switch: `use_age_qualified_children_for_dc_eitc`.
- **DC-003 (2015+).** The childless credit's investment income counts positive
  capital gains as well as interest and dividends (D-40 EITC checklist); TAXSIM
  counts interest and dividends only. Switch:
  `count_capital_gains_in_dc_childless_eitc_investment_income`.
- **NY-001 (2006-2022).** Children under 4 do not qualify for the Empire State child
  credit before 2023 (Tax Law 606(c-1)); TAXSIM has no ages. Statutory mode removes
  `children_under_4` from the 2018-2022 rebuilt credit (the 2006-2017 credit still
  counts them). Switch: `exclude_children_under_4_from_ny_empire_credit`.
- **OR-003 (2016+).** The working family household and dependent care credit counts
  only children under 13 (ORS 315.264, IRC 21(b)); TAXSIM counts every dependent.
  Switch: `count_only_children_under_13_for_oregon_dependent_care`.
- **OR-002.** The 12% under-three EITC match uses `children_under_3`, and the care
  credit cap uses `dep13`.

Corrections of TAXSIM coding errors (one switch each, named in
`src/taxsim_py/behavior.py`):

| ID | Years | TAXSIM | Statutory mode | Switch |
|:--|:--|:--|:--|:--|
| DC-004 | 2007 | The married split uses 2006 rates | 2007 rates | `use_current_rates_in_dc_earner_split` |
| DC-005 | 2000-2017 | The dependent-filer low-income credit uses the 2000 rate table every year | The year's rates | `use_current_rates_in_dc_low_income_credit` |
| IA-001 | 1990-2021 | The 2010 non-conformity caps apply every year, and the 2010 joint caps are one child off | Caps in 2010 only: $16,590, $38,665 and $43,493 for 0, 1 and 2+ children | `apply_iowa_eitc_caps_as_in_2010_law` |
| IL-002 | 2000-2002 | The EITC limit subtracts the property tax credit twice | Once | `subtract_illinois_property_credit_once_in_eitc_limit` |
| KY-002 | 2011-2012 | The itemized limitation's suspension test is true only in 2010 | Suspended 2010-2012 (the 2011 Schedule A has no limitation) | `suspend_kentucky_itemized_limitation_through_2012` |
| MA-002 | 2011-2020 | The child care deduction applies only in 2010 | Through 2020 (Form 1 line 10b) | `allow_massachusetts_child_care_deduction_2011_2020` |
| MA-003 | 1985 on | A status test that is always true carries unused exemptions to unearned income on separate returns | Not on separate returns | `exclude_separate_returns_from_massachusetts_exemption_carryover` |
| MI-001 | 2013-2022 | $20,000 per taxpayer 65 or older against all income | Tiers by the older spouse's birth year: before 1946 the pension and investment subtractions; 1946-1952 up to $20,000 ($40,000 joint) of pensions, against all income from 67; later, from 67 less exemptions | `apply_michigan_retirement_tiers` |
| MN-002 | 1978-1984 | The homemaker credit goes only to dependent filers | Filers who are not dependents | `pay_minnesota_homemaker_credit_to_nondependents` |
| MN-003 | 1998 | The childless Working Family Credit's tests overwrite each other | Phase-in, plateau and phaseout in order | `order_minnesota_1998_working_family_credit` |
| MN-004 | 2012 | No joint Working Family Credit thresholds | Joint thresholds | `use_minnesota_2012_joint_working_family_thresholds` |
| MN-005 | 2014-2021 | Phaseouts for 2+ children (unmarried) and 3+ (joint) start at the wrong table's amount | Each table's own start | `use_minnesota_working_family_tables_by_children` |
| MN-006 | 2017 on | The child care credit loses 5% of AGI over $50,000 | The lesser of the credit and $600 ($1,200) less 5% of the excess (Minn. Stat. 290.067 subd. 2b) | `limit_minnesota_child_care_credit_by_maximum_after_2017` |
| MN-007 | 1989 on | A qualifying renter gets no homeowner refund | Both refunds (Form M1PR) | `allow_minnesota_renter_and_homeowner_refunds` |
| MO-001 | All | The property credit reduces the maximum, not the tax paid, and skips the first income step | Tax paid up to the maximum ($1,100 owners from 2008) less the income percentage (RSMo 135.030) | `apply_missouri_property_credit_to_tax_paid` |
| MS-001 | 1977-1978 | Excess charity is not floored, so 20% of AGI is added to itemized deductions | Floored at 0 | `floor_mississippi_excess_charity` |
| MT-001 | 1984-1986, 2009 | All unemployment compensation is subtracted | The part in federal AGI | `subtract_only_taxable_unemployment_in_montana` |
| MT-002 | 1984 on | A separate filer's capital loss is subtracted again | Once | `subtract_montana_separate_capital_loss_once` |
| MT-003 | 2011-2012 | The itemized limit applies | Suspended 2010-2012 (the 2011 Form 2 has none) | `suspend_montana_itemized_limit_through_2012` |
| MT-004 | 1984 on | The Social Security worksheet can go negative | Floored at 0 | `floor_montana_social_security_worksheet` |
| NC-001 | 1977-1988 | The joint child care credit counts the husband's share of dependents | All children | `count_all_children_for_north_carolina_joint_child_care` |
| NC-002 | 1981-1988 | The joint child care credit can come off both spouses | Off the larger tax once | `take_north_carolina_child_care_credit_once` |
| NC-003 | 1989-1990 | Joint returns get income splitting on the married table | The married table | `use_north_carolina_married_table_without_splitting` |
| ND-001 | 1987-2000 | State income tax is added back for every return | Only when deducted federally | `add_back_north_dakota_state_tax_for_itemizers_only` |
| NE-001 | 1993-2017 | Single filers' additional tax is added twice | Once | `add_nebraska_single_additional_tax_once` |
| NJ-002 | All | Capital losses beyond $3,000 reduce income | No loss deduction | `disallow_new_jersey_capital_losses` |
| NM-001 | 1984-1989 | Taxable Social Security is added to AGI again | Subtracted | `exempt_new_mexico_social_security_1984_1989` |
| NM-002 | 1985 on | Taxpayers 65 or older count twice in the rebate's household size | Once | `count_new_mexico_aged_once_in_rebate` |
| NY-002 | 1985-1986 | No standard deduction for heads of household | The single amount (no head-of-household status before 1987) | `use_single_standard_deduction_for_new_york_head_of_household_before_1987` |
| NY-003 | 1986 | The maximum-tax table is read one row short | Six rows | `complete_new_york_1986_maximum_tax_table` |
| NY-004 | 1987-1988 | The unearned-income tax is never charged | 3% (1987) and 2% (1988) of dividends, interest and gains, phased in from $100,000 of AGI | `charge_new_york_unearned_income_tax_1987_1988` |
| NY-005 | All | The minimum tax is computed after refundable credits | None without preference items | `charge_new_york_minimum_tax_on_preferences_only` |
| NY-006 | 2012-2020 | Several worksheets phase in from another worksheet's or an unscaled bound | Each worksheet's own bound | `use_own_bounds_in_new_york_worksheets` |
| NY-007 | 2018 on | Itemized deductions can be reported negative | Floored at 0 | `floor_new_york_itemized_deductions` |
| OH-001 | 1977-1988 | The joint filing credit goes to any return with two income shares | Joint returns | `limit_ohio_joint_credit_to_joint_returns` |
| OH-002 | 1983-1988 | The exemption credit is subtracted twice, and a couple using the credit method loses the joint credit | Once; both methods get the joint credit and the lower tax applies | `subtract_ohio_exemption_credit_once` |
| OR-004 | 1981-1984 | The federal taxable unemployment is added again | Oregon's taxable amount replaces it | `replace_federal_unemployment_in_oregon_1981_1984` |
| OR-005 | 2015, 2017 | The surplus credit is computed but never applied | 5.6% of tax | `pay_oregon_2015_2017_kickers` |
| PA-001 | All | Forgiveness ignores nontaxable income and phases out smoothly | Eligibility income adds unemployment compensation and exempt pensions; 10% steps | `apply_pennsylvania_forgiveness_rules` |
| RI-001 | 2003-2009 | The gains rates apply to short-term gains | Net long-term gain | `use_net_long_term_gain_in_rhode_island` |
| RI-002 | 2003-2010 | Separate returns get the full married standard deduction | Half | `halve_rhode_island_separate_standard_deduction` |
| RI-003 | 2003-2010 | The minimum tax without gains subtracts the tax twice | Once | `subtract_rhode_island_tax_once_in_minimum_tax` |
| RI-004 | 2011-2021 | The deduction phaseout is cut in 20% steps and again smoothly | Steps only | `use_rhode_island_deduction_phaseout_steps_only` |
| SC-001 | 1981-1984 | The federal tax deduction uses the current year's tax | Tax under 1980 law | `deduct_south_carolina_federal_tax_under_1980_law` |
| VA-001 | 2003 | No child care deduction | The $3,000 federal expense limit | `allow_virginia_child_care_deduction_2003` |
| VA-002 | 2021 | The itemized limit threshold is scaled only in the test | In the reduction too | `scale_virginia_itemized_limit_threshold` |
| VT-001 | 1977-1988 | The $10 student credit goes to every filer who is not a dependent | None (no student input) | `omit_vermont_student_credit` |
| WI-001 | 2015-2018 | Joint brackets are not indexed | Indexed like single | `index_wisconsin_joint_brackets` |
| WI-002 | 1986 | No schedule for separate returns, so no tax | The joint schedule at half the bounds | `tax_wisconsin_1986_separate_returns` |
| WI-003 | 1983 | Homestead credit amounts above $10 get nothing | 80% | `credit_wisconsin_1983_homestead_excess` |
| WI-004 | 1979 on | The renter school property credit multiplies a renter flag, not rent | Rent | `base_wisconsin_renter_credit_on_rent` |
| WI-005 | 1988-2015 | Head-of-household (2000-2015) and separate (1988-1998) standard deduction tiers start before their range | At their range | `start_wisconsin_standard_deduction_tiers_at_their_range` |
| WI-006 | 1987 on | The gain exclusion can be negative | Floored at 0 | `floor_wisconsin_capital_gain_exclusion` |
| WI-007 | 1989-1990 | Homestead eligibility and reduction use different income measures | Net income for both | `use_wisconsin_net_household_income_1989_1990` |
| WI-008 | 1987-2018 | Separate returns get the full married AMT exemption | Half | `halve_wisconsin_separate_amt_exemption` |
| WI-009 | 1987-2018 | The minimum tax is added again after the married couple credit | Once | `add_wisconsin_minimum_tax_once` |
| WI-010 | All | Mortgage interest is truncated to whole dollars | Cents kept | `keep_wisconsin_mortgage_interest_cents` |
| WI-011 | 1999 | The itemized-deduction credit is zeroed on the federal standard-deduction run | Kept | `keep_wisconsin_1999_itemized_credit` |
| WV-001 | 1984-1986 | The head-of-household table is read one row short, so 13% is never reached | 18 rows | `complete_west_virginia_1984_head_of_household_table` |
| WV-002 | All | The disability deduction's year test is never true | Under 65 with taxable Social Security | `allow_west_virginia_disability_deduction` |
| WV-003 | 1977-1986 | State income tax stays in itemized deductions | Removed | `remove_state_tax_from_west_virginia_itemized_deductions` |

## Published values that replace TAXSIM's (both modes)

Where TAXSIM's own table is wrong for its year, the published value is used in both
modes, as for parameter updates.

- **AR (2004).** Rate thresholds from the DFA 2004 Indexed Tax Brackets ($3,400,
  $6,800, $10,300, $17,100, $28,500); TAXSIM reuses the 1977 thresholds.
- **CA (2001, 2018).** Schedule P (540) line 22 exemptions: 2001 head of household
  $48,114 and married separate $32,075 (TAXSIM repeats 2000's), 2018 married separate
  $47,685 (TAXSIM $44,685).
- **Federal (2022).** The single 20% capital-gains threshold is $459,750 (Rev. Proc.
  2021-45); TAXSIM's regular-tax copy has $459,650.
- **MD (2019-2021).** Standard deduction limits from the Resident Booklet,
  Instruction 16: single $1,500-$2,250, $1,550-$2,300, $1,550-$2,350; joint
  $3,050-$4,550, $3,100-$4,650, $3,100-$4,700 (TAXSIM fills its arrays out of order).
- **MN (2009).** The head-of-household AMT exclusion equals single, $49,860 (TAXSIM has
  2010's $49,960).
- **NE (1996, 1997, 2017).** Additional-tax bands $287,950 (1996 single) and $588,700
  (1997 joint), which step by fixed amounts every year (TAXSIM $297,950 and
  $575,950); the 2017 inflation factor 1.046 used by every other state (TAXSIM 1.08).
- **WV (2011).** The family credit limit for five exemptions is $26,170; the limits
  step by $3,820 (TAXSIM $28,170).

`scripts/validate_states.py` counts these state years as expected differences
(`KNOWN_ORACLE_DIVERGENT_STATE_YEARS`), as `scripts/validate_federal.py` does for
2023.

## Dated 2022+ state tables (STATE-DATED)

TAXSIM's tables stop at 2021 and its later years scale 2021 law. Statutory mode runs
actual law for 2022-2025 from PolicyEngine-US parameters checked against state forms;
taxsim mode keeps the projection. Boundaries are the provisions TAXSIM has no input
for (see the [PolicyEngine comparison](policyengine_recent_state_comparison.md)).

| State (IDs) | Dated values |
|:--|:--|
| AR (AR-001) | Standard deduction, rate and subtraction schedules, low-income tax tables, 2022-2023 inflationary-relief credit |
| CA (CA-003) | Standard deductions, rate thresholds, exemption credits, CalEITC maximums, Young Child Tax Credit amounts, AMT exemptions and phaseout starts (Schedule P (540)) |
| DC (DC-001) | Schedule H maximum, AGI tiers, elderly threshold; rent counts at 20% added to property tax |
| HI (HI-002) | 2024 doubled standard deductions, 2023-2024 Food/Excise credit tables, refundable 40% EITC from 2023 (Acts 46, 114, 163) |
| NH (NH-001) | Interest-and-dividends rate 4% (2023), 3% (2024), 0 (2025); exemptions unchanged |
| OH (OH-001) | Brackets, zero-bracket thresholds, exemptions, 30% EITC match ([2024 IT-1040](https://dam.assets.ohio.gov/image/upload/tax.ohio.gov/forms/ohio_individual/individual/2024/it1040-booklet.pdf)) |
| OK (OK-001) | Single/separate and joint/head-of-household rate tables (0.25%-4.75%) |
| OR (OR-001) | Brackets, standard deductions, federal-tax-subtraction limits, exemption credits |
| Others | CO, CT, GA, ID, IL, IN, KY, MI, NC, PA, UT and the remaining income-tax states: see the PolicyEngine comparison |

## Age-65+ rules in actual-law years (STATE-2022-AGED)

Statutory mode only, 2022 onward; each follows the state's form or statute. AZ senior
property-tax credit limited to taxes paid (all statutory years, AZ-001); IN elderly
credit as a step schedule; NM low-income rebate tables; KY pension cap per spouse (all
statutory years, KY-001), combined filing, and
poverty-guideline family-size credit; OH joint-filing credit for pension income; NJ
pension exclusion phase-down; CA credit for age 65+; MN subtraction for the elderly or
disabled; ND marriage credit with shared pensions; MI tier-three standard deduction and
phased-in retirement subtraction; CO high-income add-backs; RI $20,000 retirement
exclusion from 2023 with indexed limits; WV family credit tested on federal AGI; and
smaller items in IA, GA, MT, LA, AR, IL, AL and UT. TAXSIM has one household `pensions`
total, so per-spouse caps and spouse-income tests treat it as split evenly.

## 2025 (2025-LAW)

Federal: child credit $2,200, the senior deduction, and the SALT cap phase-down
(`src/taxsim_py/parameters/national/*`). States: see the PolicyEngine comparison. Provisions that
depart from TAXSIM's conventions (Maine's and DC's refusal of the larger federal
standard deduction, South Carolina's addbacks) are statutory mode only; parameter
updates apply in every mode. Washington's credit extends to 2025.
