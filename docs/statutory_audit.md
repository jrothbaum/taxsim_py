# Audit of the statutory side

Statutory mode departs from TAXSIM only where TAXSIM is known to be wrong. The rows in `statutory_corrections.md` are
the errors found by reading TAXSIM's source and testing it, so each is a known bug. Only **parameter errors**, where a number
in TAXSIM's tables differs from the published value, need a source to confirm the published number. **Logic errors**
(a variable never set, a typo in a condition, a result overwritten) need nothing more. Where a check shows TAXSIM's
behavior is not actually wrong (a modelling choice, or a rule the inputs cannot decide), statutory mode is moved back to
TAXSIM's behavior in both modes: the switch is removed (a switch exists only to let TAXSIM mode override the correct behavior to reproduce a bug) and listed at the end.

- Switches: 448. Logic errors (no confirmation needed): 224. Parameter errors verified against a primary
  source: 9; partly verified: 10; still to verify: 205. Switch removed because TAXSIM's behavior
  is not a bug: 2.
- Switches whose flip changes at least one household in the sample: 165. The rest change nobody in the sample.

How the impact was measured: for each switch, statutory mode is run on CPS households (40 per state, law years 1985, 2000,
2012, 2022 and 2025) with only that switch set to TAXSIM's behavior. "Changed" counts households whose federal or state tax moves by more than $1;
"average" is the mean move for those households (`scripts/audit_switch_impact.py`). Verdicts live in
`docs/data/statutory_audit_status.yaml`; this page is built by `scripts/build_audit_ledger.py`. The type column is a first sort
by the wording of each row and may misfile a few.

Status: **logic** = nothing more to confirm; **verified** = the published value matches a cited source; **partial** =
confirmed in part; **unverified** = a parameter error whose published value has not been checked yet.

| Switch | Type | States | Households changed | Average change ($) | Status | Statutory side | Source |
|:--|:--:|:--:|:--:|--:|:--:|:--|:--|
| `floor_child_credit_phaseout_at_zero` | logic | all | 100/2040 | 1941 | verified | floored. | IRC 24(b): the credit is reduced by $50 per $1,000 of income over the threshold and cannot go below zero (it is a reduction of the credit amount); checked 2026-11-13 |
| `use_form_8995a_qbi_streams` | logic | all | 62/2040 | 1736 | verified | Form 8995-A streams. | IRS Form 8995-A and its instructions (qualified business income by trade or business, separate W-2 wage and UBIA limits); checked 2026-11-13 |
| `count_other_property_income_from_2024` | logic | all | 22/2040 | 4581 | verified | other property income counted; | IRC 32(i)(2) (same rule, applied to the 2024 on federal routine); checked 2026-11-13 |
| `leave_out_one_time_state_rebates` | parameter | all | 98/2040 | 586 | partial | not paid. Taxsim mode: | Rebates are payments by the state, not tax liability, so statutory mode leaves them out by design; not a legal question |
| `use_maine_year_standard_deduction` | parameter | ME | 60/200 | 823 | partial | that year's amount. | Maine 2012 1040ME booklet and conformity summary: Maine conformed its standard deduction to the federal amount from 2012; earlier years used Maine's own amounts (joint 9,800 against 15,000 under conformity in the fiscal summary). The amounts statutory mode uses for 1989-2011 are not yet compared with the Maine booklets; checked 2026-11-13 |
| `limit_arkansas_low_income_table_to_its_range` | parameter | AR | 57/200 | 844 | partial | step table. | Arkansas AR1000F instructions (2023): the low-income tax tables apply when total income from all sources is within the table's limits; the table range and the step table below it were not compared with the form; checked 2026-11-13 |
| `tax_nebraska_before_1987_per_statute` | logic | NE | 33/200 | 1069 | verified | the share of federal tax. | Nebraska Legislature, individual income tax chronology (nebraskalegislature.gov/app_rev/source/chrono_incometax.htm): through 1986 the tax was a percentage of federal liability, 18/16/18/15/15/18/20/19/20/19% for 1977-86, identical to federal_tax_share in ne/income_tax.yaml; checked 2026-11-13 |
| `exclude_hawaii_pensions` | logic | HI | 30/200 | 1097 | partial | excludes | Hawaii Schedule J instructions: pension distributions after 1997 are exempt to the extent attributable to employer contributions; the part traceable to the employee's own contributions is taxable. Statutory mode excludes all of the pension input, which overstates the exemption when part of the pension is employee-funded (the inputs cannot say); checked 2026-11-13 |
| `keep_itemized_inputs_when_taking_standard_deduction` | parameter | all | 121/2040 | 183 | verified | a forced standard deduction. | IRC 63(b), (e): a taxpayer who elects the standard deduction takes no itemized deductions; checked 2026-11-13 |
| `count_vermont_child_credit_by_statutory_age` | parameter | VT | 15/200 | 1330 | verified | under 6 (under 7 from 2025). | Vermont Dept of Taxes, 2025 tax changes and Act 71 of 2025: child credit up to $1,000 for a child under 6 before 2025 and age 6 or younger from 2025; checked 2026-11-13 |
| `pay_kansas_credits_per_statute` | logic | KS | 38/200 | 475 | logic | refundable credits as published. | Found by reading TAXSIM's source; no form or statute check needed |
| `pay_minnesota_property_tax_refund_through_2023` | logic | MN | 28/200 | 639 | partial | paid. | Minnesota Form M-1PR (homestead credit refund, renter's property tax refund) is a real refundable credit in these years; the refund tables have not been compared line by line; checked 2026-11-13 |
| `deduct_colorado_federal_tax_after_1977` | logic | CO | 32/200 | 484 | logic | both deducted. | Found by reading TAXSIM's source; no form or statute check needed |
| `exclude_transfers_from_provisional_income` | logic | all | 17/2040 | 668 | verified | adds nothing; the law adds tax-exempt interest, which TAXSIM has no input for. The same applies to the Iowa (2014 on) and Montana worksheets. | IRC 86(b)(2): provisional income is modified AGI plus tax-exempt interest plus half of benefits; transfers are not in it; checked 2026-11-13 |
| `subtract_state_tax_in_georgia_itemized_deduction` | logic | GA | 17/200 | 534 | partial | less the state tax. | Georgia Form 500 and Schedule 1 (IT-511 booklet): state income tax claimed on federal Schedule A is added back, and Georgia keeps a 10,000 SALT limit; confirmed from Georgia DOR forms and Intuit/TurboTax expert answers, IT-511 text itself not read; checked 2026-11-13 |
| `use_colorado_statutory_sales_tax_refund` | parameter | CO | 54/200 | 161 | partial | none. | Senior housing credit: Colorado DOR page (tax.colorado.gov/income-qualified-senior-housing-income-tax-credit) and HB22-1205 summary (leg.colorado.gov): $1,000 (2022) / $800 (2024) refundable to 65+, reduced per $500 of AGI over $25,000, halved per spouse on joint returns, exactly TAXSIM's formula, so it is now computed in both modes (no switch). The other parts (sales tax refund steps vs interpolation, 2000-01 child credit age) are TAXSIM logic; checked 2026-11-13 |
| `pay_new_york_empire_child_credit_per_statute` | parameter | NY | 18/200 | 481 | partial | children 4-16. | NY Tax Dept Empire State child credit page and IT-213 instructions: 330 per child at least 4 and under 17, with children under 4 added at 330 before 2025 and 1,000 from 2025; phase-out over 75,000 / 110,000. The pre-2018 statutory rules (which years counted children under 4 and 4-16) were not compared line by line; checked 2026-11-13 |
| `remove_state_tax_from_west_virginia_itemized_deductions` | logic | WV | 9/200 | 905 | logic | removed. | Found by reading TAXSIM's source; no form or statute check needed |
| `count_colorado_credit_children_per_statute` | parameter | CO | 4/200 | 1543 | partial | children under 6 and 6-16 with the flat formula. | Colorado DOR child tax credit page and Family Affordability Tax Credit coverage: FATC up to 3,200 per child under 6 and 2,400 per child 6-16 in 2024 and 2025, phase-out 85,000 single / 95,000 joint; CTC AGI limits 75,000/85,000 (2024) and 77,000/87,000 (2025); numbers from news and agency summaries, not the DR 0104CN text; checked 2026-11-13 |
| `count_other_property_income_in_eitc_disqualified_income_per_statute` | logic | all | 1/2040 | 6015 | verified | counted in every year. | IRC 32(i)(2): disqualified income includes interest, dividends, net rent and royalty income, capital gain net income and passive activity income, so other property income counts in every year; checked 2026-11-13 |
| `split_montana_joint_returns_per_statute` | logic | MT | 24/200 | 250 | logic | higher earner plus a share. | Found by reading TAXSIM's source; no form or statute check needed |
| `file_kentucky_combined_returns_per_statute` | logic | KY | 45/200 | 131 | partial | the higher earner's wages plus half the other income. | Allocating non-wage income between spouses on a combined return is a modelling convention, so TAXSIM's split was not shown wrong; to be moved to KEEP_TAXSIM_BEHAVIOR once the Rust engine can run TAXSIM's split (not yet ported); 2026-11-14 |
| `pay_kansas_homestead_refund_from_2007_per_statute` | parameter | KS | 11/200 | 502 | verified | the refund. | Kansas Dept of Revenue K-40H booklets 2008-2025 (ksrevenue.gov/pdf/k-40hbookYY.pdf): refund is a percentage of property tax, maximum $700, income limit by year (29,700 in 2008 ... 36,600 in 2021, 37,750 / 40,500 / 42,600 / 43,389 for 2022-2025), percentage table identical to the 1997-2021 rows in the parameters (100% to 6,000 down to 5%); senior relief income limits 16,800 (2008) ... 25,380 (2025). The 2022-2025 limits were missing from the parameters and were added; checked 2026-11-13 |
| `compute_missouri_itemized_deduction_before_2018` | logic | MO | 11/200 | 452 | logic | itemized. | Found by reading TAXSIM's source; no form or statute check needed |
| `leave_missouri_federal_tax_deduction_uncapped_before_1994` | parameter | MO | 14/200 | 343 | unverified | published. |  |
| `compute_delaware_before_1996_per_statute` | parameter | DE | 17/200 | 278 | unverified | the rules of each year. |  |
| `allow_west_virginia_disability_deduction` | logic | WV | 5/200 | 936 | logic | under 65 with taxable Social Security. | Found by reading TAXSIM's source; no form or statute check needed |
| `compute_new_jersey_tax_per_statute` | parameter | NJ | 39/200 | 117 | unverified | the published rules (the code before this change). |  |
| `apply_missouri_property_credit_to_tax_paid` | parameter | MO | 5/200 | 771 | unverified | tax paid up to the maximum ($1,100 for owners from 2008) less the income percentage (RSMo 135.030). |  |
| `step_missouri_property_credit_per_statute` | parameter | MO | 10/200 | 364 | unverified | stepped schedule with an income limit. |  |
| `split_district_of_columbia_joint_returns_per_statute` | logic | DC | 23/200 | 156 | logic | the split by each spouse's own income. | Found by reading TAXSIM's source; no form or statute check needed |
| `subtract_west_virginia_social_security_per_statute` | parameter | WV | 9/200 | 389 | unverified | the phased Social Security subtraction for every filer. |  |
| `subtract_ohio_exemption_credit_once` | logic | OH | 26/200 | 120 | logic | the exemption method that gives the lower tax, one joint credit. | Found by reading TAXSIM's source; no form or statute check needed |
| `allow_additional_child_credit_before_2001` | logic | all | 3/2040 | 992 | logic | the unused credit up to the employee share of payroll tax (and half of self-employment tax) less the EITC (IRC 24(d) as enacted by P.L. 105-34). | Found by reading TAXSIM's source; no form or statute check needed |
| `apply_michigan_retirement_tiers` | parameter | MI | 5/200 | 566 | unverified | tiers by the older spouse's birth year; before 1946 the pension and investment subtractions; 1946-1952 up to $20,000 ($40,000 joint) of pensions, against all in |  |
| `pay_vermont_telephone_credit` | parameter | VT | 20/200 | 118 | unverified | published. |  |
| `pay_wisconsin_homestead_credit_from_2023` | parameter | WI | 2/200 | 1168 | unverified | married separate table, homestead for all who qualify. |  |
| `compute_minnesota_property_tax_refund_per_statute` | parameter | MN | 8/200 | 291 | unverified | the Form M1PR refund. |  |
| `exempt_arkansas_unemployment_from_2024_per_statute` | logic | AR | 10/200 | 230 | logic | exempt. | Found by reading TAXSIM's source; no form or statute check needed |
| `pay_hawaii_credits_below_zero_tax` | logic | HI | 22/200 | 102 | logic | refundable. | Found by reading TAXSIM's source; no form or statute check needed |
| `deduct_iowa_federal_tax_after_refundable_credits` | logic | IA | 21/200 | 102 | logic | published. | Found by reading TAXSIM's source; no form or statute check needed |
| `deduct_virginia_age_deduction_per_statute` | parameter | VA | 2/200 | 1035 | unverified | $12,000 per aged taxpayer. |  |
| `cap_oregon_property_tax_deduction_per_statute` | parameter | OR | 3/200 | 635 | unverified | $10,000. |  |
| `split_delaware_income_before_pension_exclusion_per_statute` | logic | DE | 5/200 | 379 | logic | split after the exclusion; no credit. | Found by reading TAXSIM's source; no form or statute check needed |
| `compute_montana_tax_from_2024_per_statute` | parameter | MT | 3/200 | 577 | unverified | the Montana worksheet. |  |
| `split_mississippi_joint_returns_per_statute` | logic | MS | 14/200 | 122 | logic | published split. | Found by reading TAXSIM's source; no form or statute check needed |
| `use_new_mexico_published_child_tax_credit` | parameter | NM | 11/200 | 154 | unverified | the published tiers. |  |
| `require_age_65_for_ohio_retirement_credit` | parameter | OH | 10/200 | 164 | unverified | taxpayers 65 or older. |  |
| `apply_maryland_unmarried_childless_earned_income_credit_per_statute` | logic | MD | 5/200 | 310 | logic | whole federal credit, nonrefundable part first, refundable up to the limit. | Found by reading TAXSIM's source; no form or statute check needed |
| `exclude_arkansas_pensions_per_taxpayer` | parameter | AR | 4/200 | 372 | unverified | per aged taxpayer. |  |
| `compute_virginia_spouse_adjustment_per_statute` | parameter | VA | 12/200 | 121 | unverified | published. |  |
| `reduce_louisiana_joint_tax_once_for_dependents` | logic | LA | 34/200 | 40 | logic | once. | Found by reading TAXSIM's source; no form or statute check needed |
| `deduct_missouri_federal_tax_from_2022` | parameter | MO | 56/200 | 24 | unverified | published. |  |
| `cap_new_york_state_tax_deduction_per_statute` | parameter | NY | 3/200 | 441 | unverified | federal cap. |  |
| `limit_hawaii_state_tax_deduction_from_2011` | parameter | HI | 2/200 | 657 | unverified | 2011. |  |
| `refund_virginia_earned_income_credit_at_2025_rate` | logic | VA | 7/200 | 186 | logic | 20%. | Found by reading TAXSIM's source; no form or statute check needed |
| `count_half_self_employment_tax_in_refundable_child_credit_per_statute` | logic | all | 1/2040 | 1289 | logic | half of the self-employment tax (Form 8812 line "one-half of self-employment tax"; not re-checked for each year). | Found by reading TAXSIM's source; no form or statute check needed |
| `step_maryland_exemption_phaseout_per_statute` | logic | MD | 24/200 | 52 | logic | steps. | Found by reading TAXSIM's source; no form or statute check needed |
| `use_new_york_household_credit_table_1990_2016` | parameter | NY | 30/200 | 41 | unverified | the published table. |  |
| `let_delaware_itemize_when_federal_return_takes_standard_deduction` | logic | DE | 8/200 | 153 | logic | Delaware itemizes on its own. | Found by reading TAXSIM's source; no form or statute check needed |
| `apply_pennsylvania_forgiveness_rules` | logic | PA | 15/200 | 79 | logic | eligibility income adds unemployment compensation and exempt pensions; 10% steps. | Found by reading TAXSIM's source; no form or statute check needed |
| `use_maryland_itemized_deduction_without_federal_itemizing` | logic | MD | 28/200 | 42 | logic | published. | Found by reading TAXSIM's source; no form or statute check needed |
| `deduct_missouri_federal_tax_after_refundable_credits` | parameter | MO | 38/200 | 31 | unverified | after the earned income credit. |  |
| `require_age_65_for_michigan_investment_deduction_per_statute` | logic | MI | 4/200 | 281 | logic | age 65 or older, with Social Security also subtracted. | Found by reading TAXSIM's source; no form or statute check needed |
| `exclude_new_jersey_pensions_per_statute` | parameter | NJ | 6/200 | 183 | unverified | the published rules (the code before this change). |  |
| `compute_new_york_property_credit_per_statute` | parameter | NY | 5/200 | 219 | unverified | the published credit. |  |
| `pay_oregon_kids_credit_per_statute` | parameter | OR | 1/200 | 1050 | unverified | children under 6. |  |
| `apply_minnesota_pension_exclusion_through_1986_per_statute` | logic | MN | 2/200 | 514 | logic | subtracted. | Found by reading TAXSIM's source; no form or statute check needed |
| `allow_connecticut_property_credit_to_all_from_2022` | logic | CT | 5/200 | 201 | logic | any filer from 2022. | Found by reading TAXSIM's source; no form or statute check needed |
| `use_arkansas_statutory_step_credits` | parameter | AR | 22/200 | 45 | unverified |  |  |
| `apply_elderly_credit_before_1987` | parameter | all | 6/2040 | 164 | unverified | 15% of $2,500 ($3,750 for a couple both 65 or older; from 1984 $5,000 and $7,500) less nontaxable Social Security and half of AGI over $7,500 ($10,000 joint) (I |  |
| `step_missouri_federal_tax_deduction_share` | parameter | MO | 42/200 | 23 | unverified | published. |  |
| `apply_state_childless_eitc_minimum_age` | parameter | all | 4/2040 | 242 | unverified | from 18, with no maximum age (R&TC 17052); an unreported age passes. |  |
| `use_louisiana_brackets_per_statute` | parameter | LA | 5/200 | 192 | unverified | $91,000; heads of household $3,500 and $41,000. |  |
| `exempt_missouri_social_security_at_any_age_per_statute` | logic | MO | 1/200 | 953 | logic | all benefits under the income limits. | Found by reading TAXSIM's source; no form or statute check needed |
| `keep_oregon_state_tax_deduction_for_aged_per_statute` | logic | OR | 3/200 | 310 | logic | they keep the state income tax deduction. | Found by reading TAXSIM's source; no form or statute check needed |
| `tax_new_jersey_top_rate_after_1982_per_statute` | parameter | NJ | 5/200 | 181 | unverified | the published schedules. |  |
| `step_ohio_exemption_by_income` | parameter | OH | 47/200 | 17 | unverified | steps. |  |
| `exclude_transfers_from_connecticut_income_per_statute` | logic | CT | 4/200 | 202 | logic | excluded. | Found by reading TAXSIM's source; no form or statute check needed |
| `zero_virginia_tax_below_filing_threshold` | logic | VA | 6/200 | 132 | logic | exempt. | Found by reading TAXSIM's source; no form or statute check needed |
| `pay_maryland_refundable_eitc_only_above_tax` | logic | MD | 5/200 | 156 | logic | only the excess over tax. | Found by reading TAXSIM's source; no form or statute check needed |
| `exempt_alabama_retirement_income_in_2022` | logic | AL | 3/200 | 253 | logic | 2022. | Found by reading TAXSIM's source; no form or statute check needed |
| `ignore_federal_salt_cap_in_california` | parameter | CA | 1/200 | 757 | unverified | California never adopted the cap (Schedule CA (540)); real estate and other taxes are deductible in full, its own income tax not at all. |  |
| `count_ohio_joint_credit_unemployment_as_taxable_per_statute` | logic | OH | 5/200 | 149 | logic | taxable unemployment. | Found by reading TAXSIM's source; no form or statute check needed |
| `include_unemployment_in_california_total_income` | logic | CA | 1/200 | 726 | logic | not subtracted. | Found by reading TAXSIM's source; no form or statute check needed |
| `exempt_new_mexico_social_security_1984_1989` | logic | NM | 1/200 | 706 | logic | subtracted. | Found by reading TAXSIM's source; no form or statute check needed |
| `step_alabama_dependent_exemption_per_statute` | parameter | AL | 28/200 | 23 | unverified | published steps. |  |
| `start_ohio_business_deduction_per_statute` | parameter | OH | 4/200 | 157 | unverified | none until 2015. |  |
| `split_iowa_joint_income_by_wages_only` | logic | IA | 10/200 | 61 | logic | published. | Found by reading TAXSIM's source; no form or statute check needed |
| `count_all_dependents_for_maine_dependent_credit` | parameter | ME | 2/200 | 302 | unverified | all dependents. |  |
| `use_minnesota_2012_joint_working_family_thresholds` | parameter | MN | 2/200 | 301 | unverified | joint thresholds. |  |
| `limit_minnesota_itemized_deduction_from_2024_per_statute` | parameter | MN | 1/200 | 599 | unverified | the dated rules. |  |
| `read_montana_elderly_credit_multiplier_per_statute` | parameter | MT | 3/200 | 197 | unverified | 100 percent to $35,000, then steps. |  |
| `apply_kentucky_pension_cap_per_taxpayer` | logic | KY | 2/200 | 293 | logic | per taxpayer (KRS 141.010(10)), with a joint return's pensions split evenly. | Found by reading TAXSIM's source; no form or statute check needed |
| `deduct_idaho_itemized_from_2018_per_statute` | parameter | ID | 3/200 | 190 | unverified | federal itemized deductions less state income tax. |  |
| `read_north_carolina_child_deduction_per_statute` | parameter | NC | 12/200 | 46 | unverified | step schedule, children under 17. |  |
| `credit_iowa_dependents_before_1998` | logic | IA | 17/200 | 31 | logic | dependents credited, head of household once. | Found by reading TAXSIM's source; no form or statute check needed |
| `compute_new_mexico_deductions_per_statute` | logic | NM | 3/200 | 155 | logic | the existing deduction rules. | Found by reading TAXSIM's source; no form or statute check needed |
| `add_back_iowa_self_employment_tax_twice` | logic | IA | 4/200 | 115 | logic | published. | Found by reading TAXSIM's source; no form or statute check needed |
| `apply_federal_investment_income_limit_to_wisconsin_eitc_per_statute` | parameter | WI | 1/200 | 440 | unverified | the federal limit. |  |
| `count_new_mexico_aged_once_in_rebate` | logic | NM | 12/200 | 37 | logic | once. | Found by reading TAXSIM's source; no form or statute check needed |
| `split_missouri_joint_income_by_earner_only` | logic | MO | 14/200 | 31 | logic | by earner only. | Found by reading TAXSIM's source; no form or statute check needed |
| `pay_utah_retirement_credit_per_statute` | parameter | UT | 2/200 | 216 | unverified | aged taxpayers, plus a Social Security credit. |  |
| `test_arizona_family_credit_before_exemptions` | parameter | AZ | 9/200 | 47 | unverified | before exemptions. |  |
| `split_arkansas_joint_income_by_higher_earner` | logic | AR | 7/200 | 59 | logic | the higher earner's share. | Found by reading TAXSIM's source; no form or statute check needed |
| `use_colorado_itemized_deductions_before_1987` | logic | CO | 4/200 | 91 | logic | both deducted. | Found by reading TAXSIM's source; no form or statute check needed |
| `weight_falling_tables_correctly` | parameter | all | 38/2040 | 9 | unverified |  |  |
| `exclude_delaware_pensions_by_statute_from_2024` | parameter | DE | 1/200 | 341 | unverified | the earlier routine. |  |
| `compute_maryland_itemized_deduction_from_2018_per_statute` | parameter | MD | 1/200 | 340 | unverified | whole state tax. |  |
| `start_oklahoma_joint_top_bracket_per_statute` | parameter | OK | 15/200 | 22 | unverified | $14,400. |  |
| `round_up_phaseout_steps` | parameter | all | 7/2040 | 46 | unverified | in whole steps. |  |
| `compute_iowa_tax_from_2023_per_statute` | logic | IA | 1/200 | 297 | logic | published. | Found by reading TAXSIM's source; no form or statute check needed |
| `omit_vermont_student_credit` | logic | VT | 31/200 | 10 | logic | none (TAXSIM has no student input). | Found by reading TAXSIM's source; no form or statute check needed |
| `use_single_standard_deduction_for_new_york_head_of_household_before_1987` | logic | NY | 2/200 | 148 | logic | the single amount (no head-of-household status before 1987). | Found by reading TAXSIM's source; no form or statute check needed |
| `step_kentucky_family_size_credit_by_poverty_share` | logic | KY | 6/200 | 49 | logic | the step share by multiple of the poverty guideline. | Found by reading TAXSIM's source; no form or statute check needed |
| `use_south_carolina_published_tax_table` | logic | SC | 27/200 | 10 | logic | the published brackets. | Found by reading TAXSIM's source; no form or statute check needed |
| `add_kansas_aged_and_dependent_standard_deduction_per_statute` | parameter | KS | 4/200 | 68 | unverified | aged addition and limit every year, $7,800 joint in 2021. |  |
| `use_ohio_published_tax_brackets` | logic | OH | 15/200 | 18 | logic | the published brackets. | Found by reading TAXSIM's source; no form or statute check needed |
| `add_connecticut_recapture_per_statute` | parameter | CT | 15/200 | 17 | unverified | published. |  |
| `compute_oklahoma_pension_exclusion_per_statute` | parameter | OK | 2/200 | 107 | unverified | taxpayers 65 or older with the stated limits. |  |
| `pay_missouri_working_family_credit_by_statute` | logic | MO | 1/200 | 207 | logic | published. | Found by reading TAXSIM's source; no form or statute check needed |
| `allow_georgia_retirement_exclusion_before_1989_per_statute` | logic | GA | 3/200 | 66 | logic | $2,000-$4,000. | Found by reading TAXSIM's source; no form or statute check needed |
| `phase_out_connecticut_exemption_per_statute` | parameter | CT | 5/200 | 37 | unverified | published. |  |
| `use_connecticut_personal_credit_table_per_statute` | parameter | CT | 24/200 | 7 | unverified | published thresholds. |  |
| `compute_minnesota_marriage_credit_earnings_per_statute` | parameter | MN | 11/200 | 16 | unverified | published. |  |
| `use_rhode_island_published_brackets` | parameter | RI | 1/200 | 165 | unverified | published brackets and steps. |  |
| `subtract_colorado_pensions_for_nonitemizers_before_2021` | logic | CO | 2/200 | 80 | logic | every aged return. | Found by reading TAXSIM's source; no form or statute check needed |
| `count_arizona_dependent_credit_children_under_17_per_statute` | parameter | AZ | 2/200 | 75 | unverified | under 17. |  |
| `compute_montana_retirement_exclusion_per_statute` | logic | MT | 3/200 | 47 | logic | taxpayers 65 or older. | Found by reading TAXSIM's source; no form or statute check needed |
| `use_new_mexico_low_income_rebate_per_statute` | parameter | NM | 22/200 | 6 | unverified | the step table. |  |
| `allow_negative_new_york_property_credit_income_per_statute` | logic | NY | 2/200 | 68 | logic | not floored (not checked against IT-214). | Found by reading TAXSIM's source; no form or statute check needed |
| `phase_out_oregon_federal_tax_limit_in_steps` | parameter | OR | 1/200 | 130 | unverified | the published steps. |  |
| `exclude_massachusetts_bank_interest_from_2024_per_statute` | logic | MA | 18/200 | 6 | logic | $100 per taxpayer. | Found by reading TAXSIM's source; no form or statute check needed |
| `end_michigan_senior_percentage_table_at_statutory_row_per_statute` | parameter | MI | 3/200 | 35 | unverified | the existing table with no closing row (not checked against the MI-1040CR-7 percentage table). |  |
| `pay_maryland_poverty_level_credit_per_statute` | logic | MD | 2/200 | 51 | logic | allowed. | Found by reading TAXSIM's source; no form or statute check needed |
| `require_indiana_federal_credit_for_decoupled_credit_per_statute` | parameter | IN | 3/200 | 31 | unverified | requires the federal credit. |  |
| `test_new_mexico_aged_deduction_on_state_agi` | logic | NM | 2/200 | 43 | logic | New Mexico AGI. | Found by reading TAXSIM's source; no form or statute check needed |
| `require_federal_age_test_for_new_mexico_working_families_credit_per_statute` | logic | NM | 2/200 | 40 | logic | the federal credit. | Found by reading TAXSIM's source; no form or statute check needed |
| `give_south_carolina_pension_deduction_to_each_taxpayer` | parameter | SC | 2/200 | 37 | unverified | $3,000 per taxpayer. |  |
| `test_oklahoma_earned_income_credit_age_per_statute` | parameter | OK | 4/200 | 16 | unverified | as the statute. |  |
| `use_vermont_published_standard_deductions` | parameter | VT | 25/200 | 2 | unverified | the published amounts and exclusion options. |  |
| `compute_oklahoma_itemized_deduction_from_2022_per_statute` | parameter | OK | 1/200 | 52 | unverified | as the statute. |  |
| `apply_maryland_poverty_level_credit_without_eligibility_worksheet_per_statute` | logic | MD | 1/200 | 51 | logic | paid whenever income is at most the poverty-line amount (guideline amounts entered for 2022-26 from the 2026 build; the statute's own eligibility test was not c | Found by reading TAXSIM's source; no form or statute check needed |
| `allow_louisiana_federal_credit_share_per_statute` | logic | LA | 2/200 | 25 | logic | a tenth of the federal child care and elderly credits. | Found by reading TAXSIM's source; no form or statute check needed |
| `use_published_kansas_exemption_from_2024` | parameter | KS | 30/200 | 2 | unverified | the published amounts. |  |
| `use_wisconsin_form_standard_deduction` | parameter | WI | 5/200 | 6 | unverified | the form's schedules. |  |
| `use_idaho_building_fund_limits_per_statute` | parameter | ID | 3/200 | 10 | unverified | published. |  |
| `reduce_iowa_single_tax_before_2017` | parameter | IA | 1/200 | 22 | unverified | every year. |  |
| `add_back_michigan_self_employment_tax_from_2021_per_statute` | logic | MI | 2/200 | 11 | logic | added back. | Found by reading TAXSIM's source; no form or statute check needed |
| `subtract_michigan_social_security_with_investment_deduction_per_statute` | logic | MI | 2/200 | 11 | logic | age 65 or older, with Social Security also subtracted. | Found by reading TAXSIM's source; no form or statute check needed |
| `step_connecticut_pension_deduction_per_statute` | logic | CT | 1/200 | 18 | logic | the step schedule. | Found by reading TAXSIM's source; no form or statute check needed |
| `round_new_york_empire_child_credit_phaseout_per_statute` | parameter | NY | 2/200 | 8 | unverified | the excess as is. |  |
| `use_utah_2022_credit_threshold_per_statute` | parameter | UT | 15/200 | 1 | unverified | 15,548 and so on. |  |
| `use_published_capital_gains_thresholds` | parameter | all | 1/2040 | 14 | unverified | $459,750 for both (Rev. Proc. 2021-45). |  |
| `cap_medicare_in_state_payroll_deduction` | logic | all | 1/2040 | 13 | logic | stops at the Medicare base (the same base before 1991). | Found by reading TAXSIM's source; no form or statute check needed |
| `step_alabama_standard_deduction_per_statute` | parameter | AL | 3/200 | 4 | unverified | published. |  |
| `use_rhode_island_deduction_phaseout_steps_only` | parameter | RI | 1/200 | 12 | unverified | steps only. |  |
| `cap_delaware_property_tax_deduction_per_statute` | parameter | DE | 1/200 | 12 | unverified | capped. |  |
| `enforce_schedule_se_minimum` | parameter | all | 3/2040 | 3 | unverified | none when 92.35% of net business income is under $400, per spouse ([Schedule SE instructions](https://www.irs.gov/pub/irs-prior/i1040sse--2021.pdf)). |  |
| `compute_california_2021_earned_income_credit_per_statute` | parameter | CA | 1/200 | 9 | unverified |  |  |
| `compute_north_dakota_marriage_credit_earnings_per_statute` | logic | ND | 1/200 | 9 | logic | wages (pensions from 2022). | Found by reading TAXSIM's source; no form or statute check needed |
| `end_new_york_property_credit_maximum_tables_at_last_row_per_statute` | logic | NY | 1/200 | 9 | logic | household income as the form defines it; maximum stops at the last row's value (not checked against IT-214). | Found by reading TAXSIM's source; no form or statute check needed |
| `use_vermont_published_brackets` | parameter | VT | 4/200 | 2 | unverified | published. |  |
| `use_published_minnesota_2025_aged_addition_per_statute` | parameter | MN | 1/200 | 5 | unverified | published amounts. |  |
| `use_arkansas_published_2025_rate_table_per_statute` | logic | AR | 3/200 | 2 | logic | published table. | Found by reading TAXSIM's source; no form or statute check needed |
| `apply_payroll_wage_bases_correctly` | logic | all | 0/2040 | 0 | logic | the base is shared with wages. | Found by reading TAXSIM's source; no form or statute check needed |
| `count_business_and_professional_inputs_as_income` | logic | all | 0/2040 | 0 | logic | income. | Found by reading TAXSIM's source; no form or statute check needed |
| `count_nonproperty_income_once` | logic | all | 0/2040 | 0 | logic | once. | Found by reading TAXSIM's source; no form or statute check needed |
| `index_salt_cap_from_2026` | parameter | all | 0/2040 | 0 | unverified | $40,400 / $505,000 and 50%. |  |
| `halve_salt_phaseout_for_separate_returns_per_statute` | parameter | all | 0/2040 | 0 | unverified | threshold and range halved. |  |
| `phase_out_amt_exemption_at_half_rate_from_2026` | parameter | all | 0/2040 | 0 | unverified | $40,400 / $505,000 and 50%. |  |
| `honor_separate_and_unmarried_codes_with_dependents` | logic | all | 0/2040 | 0 | logic | separate or single. | Found by reading TAXSIM's source; no form or statute check needed |
| `tax_alaska_before_1979` | logic | all | 0/2040 | 0 | logic | taxed under Alaska's own schedule. | Found by reading TAXSIM's source; no form or statute check needed |
| `use_vermont_social_security_exemption_starts_per_statute` | parameter | VT | 0/200 | 0 | unverified | starts of $45,000 ($60,000 joint), then higher. |  |
| `use_vermont_capital_gain_exclusion_options` | parameter | VT | 0/200 | 0 | unverified | the published amounts and exclusion options. |  |
| `pay_pennsylvania_child_care_credit_only_from_2023` | parameter | PA | 0/200 | 0 | unverified | from 2023 only. |  |
| `use_published_dependent_standard_deduction_minimum` | parameter | all | 0/2040 | 0 | unverified | the published amounts. |  |
| `use_wisconsin_statutory_school_credit` | parameter | WI | 0/200 | 0 | unverified | the form's rules. |  |
| `use_additional_medicare_separate_threshold` | parameter | all | 0/2040 | 0 | unverified | $125,000. |  |
| `use_iowa_2020_bracket_inflation_per_statute` | parameter | IA | 0/200 | 0 | unverified | 1.666. |  |
| `include_state_taxes_in_iowa_itemized_deduction_from_2020` | logic | IA | 0/200 | 0 | logic | federal itemized deductions net of the state tax. | Found by reading TAXSIM's source; no form or statute check needed |
| `include_business_and_rental_income_in_california_minimum_tax` | logic | CA | 0/200 | 0 | logic | kept, as on Schedule P. | Found by reading TAXSIM's source; no form or statute check needed |
| `count_pre_1998_nonrefundable_credits_correctly` | logic | all | 0/2040 | 0 | logic | the actual credit ( | Found by reading TAXSIM's source; no form or statute check needed |
| `count_additional_medicare_once_in_alabama_fica_deduction` | logic | AL | 0/200 | 0 | logic | counted once. | Found by reading TAXSIM's source; no form or statute check needed |
| `deduct_half_of_fica_in_alabama_itemized_deduction` | logic | AL | 0/200 | 0 | logic | half of FICA. | Found by reading TAXSIM's source; no form or statute check needed |
| `deduct_alabama_federal_tax_before_stimulus_credits` | logic | AL | 0/200 | 0 | logic | before them. | Found by reading TAXSIM's source; no form or statute check needed |
| `use_age_qualified_children_for_dc_eitc` | logic | DC | 0/200 | 0 | logic | the age-qualified count | Found by reading TAXSIM's source; no form or statute check needed |
| `allow_kansas_itemizing_after_2020` | logic | KS | 0/200 | 0 | logic | itemizing follows the federal choice in every year (K-40 Schedule A). | Found by reading TAXSIM's source; no form or statute check needed |
| `deduct_kansas_other_taxes_per_statute` | logic | KS | 0/200 | 0 | logic | other taxes included. | Found by reading TAXSIM's source; no form or statute check needed |
| `compute_louisiana_tax_per_statute` | parameter | LA | 0/200 | 0 | unverified | the published schedules. |  |
| `count_wisconsin_rent_at_statutory_share` | logic | WI | 0/200 | 0 | logic | a quarter. | Found by reading TAXSIM's source; no form or statute check needed |
| `exclude_other_itemized_deductions_from_wisconsin_credit` | logic | WI | 0/200 | 0 | logic | mortgage interest and charity only. | Found by reading TAXSIM's source; no form or statute check needed |
| `use_new_mexico_working_families_rates_per_statute` | logic | NM | 0/200 | 0 | logic | 17% in 2019. | Found by reading TAXSIM's source; no form or statute check needed |
| `pay_new_jersey_renter_credit_per_statute` | parameter | NJ | 0/200 | 0 | unverified | the published rules (the code before this change). |  |
| `exclude_nonproperty_income_from_new_jersey_income` | parameter | NJ | 0/200 | 0 | unverified | the published rules (the code before this change). |  |
| `tax_full_capital_gains_in_new_jersey_before_1987` | parameter | NJ | 0/200 | 0 | unverified | the published schedules. |  |
| `limit_nebraska_property_tax_deduction_to_federal_cap` | logic | NE | 0/200 | 0 | logic | the federal itemized deduction less the capped state tax. | Found by reading TAXSIM's source; no form or statute check needed |
| `use_nebraska_high_income_tables_per_statute` | parameter | NE | 0/200 | 0 | unverified | the published tables. |  |
| `pay_nebraska_refundable_child_care_per_statute` | parameter | NE | 0/200 | 0 | unverified | the federal credit after its limit, 10 points per $1,000. |  |
| `zero_nebraska_standard_deduction_when_federal_itemizes` | logic | NE | 0/200 | 0 | logic | the standard deduction only when the federal return takes it. | Found by reading TAXSIM's source; no form or statute check needed |
| `exclude_niit_from_louisiana_federal_tax_deduction` | logic | LA | 0/200 | 0 | logic | not deducted. | Found by reading TAXSIM's source; no form or statute check needed |
| `use_louisiana_child_care_credit_per_statute` | logic | LA | 0/200 | 0 | logic | Louisiana AGI. | Found by reading TAXSIM's source; no form or statute check needed |
| `apply_kentucky_pension_exclusion_schedule` | parameter | KY | 0/200 | 0 | unverified | the published percentage and maximum for the year. |  |
| `limit_kentucky_itemized_deductions_to_mortgage_interest` | logic | KY | 0/200 | 0 | logic | mortgage interest only. | Found by reading TAXSIM's source; no form or statute check needed |
| `exclude_children_under_4_from_ny_empire_credit` | logic | all | 0/2040 | 0 | logic | children under 4 do not qualify before 2023 (Tax Law 606(c-1)); | Found by reading TAXSIM's source; no form or statute check needed |
| `limit_new_york_property_credit_rent_per_statute` | parameter | NY | 0/200 | 0 | unverified | the quarter-of-rent test. |  |
| `round_new_york_recapture_ratio_per_statute` | parameter | NY | 0/200 | 0 | unverified | rounded to four decimals (Form IT-201 worksheets). |  |
| `pay_new_york_household_credit_to_separate_returns` | logic | NY | 0/200 | 0 | logic | the credit, halved. | Found by reading TAXSIM's source; no form or statute check needed |
| `count_children_under_6_by_dep6` | logic | all | 0/2040 | 0 | logic |  | Found by reading TAXSIM's source; no form or statute check needed |
| `use_idaho_published_brackets` | parameter | ID | 0/200 | 0 | unverified | published. |  |
| `initialize_arizona_short_term_loss` | logic | AZ | 0/200 | 0 | logic | 25% of gains. | Found by reading TAXSIM's source; no form or statute check needed |
| `use_delaware_published_tax_brackets` | parameter | DE | 0/200 | 0 | unverified | published. |  |
| `use_maine_published_phaseout_starts` | parameter | ME | 0/200 | 0 | unverified | published. |  |
| `use_maine_published_2024_sales_tax_credit_per_statute` | parameter | ME | 0/200 | 0 | unverified | published schedule. |  |
| `deflate_maine_earned_income_credit_per_statute` | logic | ME | 0/200 | 0 | logic | n/a. | Found by reading TAXSIM's source; no form or statute check needed |
| `limit_maine_child_care_credit_by_federal_tax_in_2021_per_statute` | parameter | ME | 0/200 | 0 | unverified | limited by federal tax. |  |
| `limit_maine_dependent_filer_standard_deduction_per_statute` | parameter | ME | 0/200 | 0 | unverified | the federal (limited) amount. |  |
| `count_maine_fairness_credit_children_by_dep17` | logic | ME | 0/200 | 0 | logic | under 17. | Found by reading TAXSIM's source; no form or statute check needed |
| `deduct_maine_pensions_at_any_age` | parameter | ME | 0/200 | 0 | unverified | any age. |  |
| `use_maine_current_deductions_when_itemizing` | parameter | ME | 0/200 | 0 | unverified | published. |  |
| `credit_delaware_spouse_low_income_exclusion_to_spouse` | parameter | DE | 0/200 | 0 | unverified | added to the spouse's. |  |
| `allow_qbi_deduction_in_south_carolina` | logic | SC | 0/200 | 0 | logic | allowed. | Found by reading TAXSIM's source; no form or statute check needed |
| `split_virginia_age_deduction_between_spouses_per_statute` | logic | VA | 0/200 | 0 | logic | each spouse's own income. | Found by reading TAXSIM's source; no form or statute check needed |
| `limit_virginia_2021_child_care_deduction_per_statute` | parameter | VA | 0/200 | 0 | unverified | 3,000. |  |
| `add_back_other_itemized_in_california_minimum_tax_per_statute` | logic | CA | 0/200 | 0 | logic | adds back property tax and other items. | Found by reading TAXSIM's source; no form or statute check needed |
| `use_indiana_elderly_credit_steps_per_statute` | logic | IN | 0/200 | 0 | logic | step schedule. | Found by reading TAXSIM's source; no form or statute check needed |
| `limit_new_mexico_dependent_filer_standard_deduction_per_statute` | logic | NM | 0/200 | 0 | logic | the limited amount. | Found by reading TAXSIM's source; no form or statute check needed |
| `leave_out_michigan_schedule_r_income_subtraction_per_statute` | logic | MI | 0/200 | 0 | logic | not subtracted. | Found by reading TAXSIM's source; no form or statute check needed |
| `deny_delaware_personal_credit_to_dependent_filers_per_statute` | logic | DE | 0/200 | 0 | logic | split after the exclusion; no credit. | Found by reading TAXSIM's source; no form or statute check needed |
| `tax_kansas_other_nonproperty_income_per_statute` | logic | KS | 0/200 | 0 | logic | taxed. | Found by reading TAXSIM's source; no form or statute check needed |
| `scale_kansas_zero_tax_income_by_taxpayers_per_statute` | logic | KS | 0/200 | 0 | logic | the limit applies. | Found by reading TAXSIM's source; no form or statute check needed |
| `reconstruct_connecticut_minimum_tax_income_per_statute` | logic | CT | 0/200 | 0 | logic | reconstructed from AGI and the separate-return add-back. | Found by reading TAXSIM's source; no form or statute check needed |
| `halve_minnesota_motor_fuels_credit_for_separate_returns_per_statute` | parameter | MN | 0/200 | 0 | unverified | halved. |  |
| `use_published_minnesota_dependent_standard_deduction_per_statute` | parameter | MN | 0/200 | 0 | unverified | published amounts. |  |
| `add_earnings_to_district_of_columbia_dependent_standard_deduction_per_statute` | logic | DC | 0/200 | 0 | logic | earnings plus the federal addition. | Found by reading TAXSIM's source; no form or statute check needed |
| `apply_illinois_earned_income_credit_age_test_per_statute` | logic | IL | 0/200 | 0 | logic | the age test. | Found by reading TAXSIM's source; no form or statute check needed |
| `add_idaho_aged_standard_deduction_in_projected_years_per_statute` | parameter | ID | 0/200 | 0 | unverified | adds the aged amount. |  |
| `use_north_dakota_published_marriage_credit_limits_per_statute` | parameter | ND | 0/200 | 0 | unverified | published amounts. |  |
| `apply_new_york_earned_income_credit_age_test_per_statute` | logic | NY | 0/200 | 0 | logic | age test and New York AGI. | Found by reading TAXSIM's source; no form or statute check needed |
| `use_new_york_agi_for_earned_income_credit_per_statute` | logic | NY | 0/200 | 0 | logic | age test and New York AGI. | Found by reading TAXSIM's source; no form or statute check needed |
| `exclude_other_itemized_from_missouri_state_tax_limit_per_statute` | logic | MO | 0/200 | 0 | logic | excluded. | Found by reading TAXSIM's source; no form or statute check needed |
| `split_missouri_income_before_capital_gains_subtraction_per_statute` | logic | MO | 0/200 | 0 | logic | before. | Found by reading TAXSIM's source; no form or statute check needed |
| `lift_maryland_childless_refundable_credit_limit_per_statute` | logic | MD | 0/200 | 0 | logic | no cap from 2023. | Found by reading TAXSIM's source; no form or statute check needed |
| `apply_maryland_earned_income_credit_age_test_per_statute` | logic | MD | 0/200 | 0 | logic | federal credit with the age test. | Found by reading TAXSIM's source; no form or statute check needed |
| `use_maryland_published_2025_tax_table_per_statute` | parameter | MD | 0/200 | 0 | unverified | the published rates. |  |
| `use_california_published_2025_brackets_per_statute` | parameter | CA | 0/200 | 0 | unverified | 371,479 and 505,208. |  |
| `use_california_2025_minimum_tax_exemption_per_statute` | parameter | CA | 0/200 | 0 | unverified | 2025 amounts. |  |
| `start_child_tax_credit_phaseout_at_statutory_threshold_for_dependent_filers` | parameter | all | 0/2040 | 0 | unverified | $200,000. |  |
| `count_eitc_children_from_ages_per_statute` | parameter | all | 0/2040 | 0 | unverified | counts the children. |  |
| `count_business_inputs_in_earned_income_before_1987_per_statute` | logic | all | 0/2040 | 0 | logic | not counted / counted. | Found by reading TAXSIM's source; no form or statute check needed |
| `exclude_nonproperty_income_from_maximum_tax_personal_service_income` | logic | all | 0/2040 | 0 | logic | not counted / counted. | Found by reading TAXSIM's source; no form or statute check needed |
| `start_minnesota_itemized_phaseout_without_status_multiplier_per_statute` | logic | MN | 0/200 | 0 | logic | the itemized deduction's own start. | Found by reading TAXSIM's source; no form or statute check needed |
| `add_back_minnesota_unemployment_exclusion_to_working_family_income_per_statute` | parameter | MN | 0/200 | 0 | unverified | added back in the working family credit's income. |  |
| `exclude_utah_other_itemized_from_state_tax_limit_per_statute` | parameter | UT | 0/200 | 0 | unverified | not counted. |  |
| `deflate_wisconsin_exemption_count_per_statute` | parameter | WI | 0/200 | 0 | unverified | the actual count. |  |
| `deflate_massachusetts_payroll_tax_deduction_per_statute` | logic | MA | 0/200 | 0 | logic | n/a. | Found by reading TAXSIM's source; no form or statute check needed |
| `use_massachusetts_2020_part_c_rate_per_statute` | parameter | MA | 0/200 | 0 | unverified | 5.0%. |  |
| `use_massachusetts_2001_rent_cap_per_statute` | parameter | MA | 0/200 | 0 | unverified | 3,000. |  |
| `halve_new_york_married_itemized_limit_for_separate_per_statute` | parameter | NY | 0/200 | 0 | unverified | half the married threshold. |  |
| `compute_new_york_earned_income_credit_base_per_statute` | logic | NY | 0/200 | 0 | logic | computed on New York income. | Found by reading TAXSIM's source; no form or statute check needed |
| `offset_massachusetts_dividends_only_by_losses_per_statute` | logic | MA | 0/200 | 0 | logic | no offset against gains. | Found by reading TAXSIM's source; no form or statute check needed |
| `count_maine_fairness_credit_children_before_deflation_per_statute` | logic | ME | 0/200 | 0 | logic | children. | Found by reading TAXSIM's source; no form or statute check needed |
| `count_arkansas_other_itemized_deductions_per_statute` | logic | AR | 0/200 | 0 | logic | counted. | Found by reading TAXSIM's source; no form or statute check needed |
| `limit_kansas_child_care_credit_by_federal_tax_per_statute` | parameter | KS | 0/200 | 0 | unverified | limited by the federal tax. |  |
| `round_connecticut_property_credit_steps_continuously_per_statute` | logic | CT | 0/200 | 0 | logic | continuous steps. | Found by reading TAXSIM's source; no form or statute check needed |
| `deduct_alabama_federal_tax_components_per_statute` | logic | AL | 0/200 | 0 | logic |  | Found by reading TAXSIM's source; no form or statute check needed |
| `cap_michigan_senior_renter_credit_at_statute_amount` | parameter | MI | 0/200 | 0 | unverified | reduced; capped at the year's maximum. |  |
| `give_michigan_dependent_filers_exemption_per_statute` | logic | MI | 0/200 | 0 | logic |  | Found by reading TAXSIM's source; no form or statute check needed |
| `compute_utah_child_tax_credit_per_statute` | parameter | UT | 0/200 | 0 | unverified | children under 6, taxable income over $43,000 for singles. |  |
| `limit_rhode_island_child_care_credit_by_federal_tax_per_statute` | logic | RI | 0/200 | 0 | logic | limited by federal tax. | Found by reading TAXSIM's source; no form or statute check needed |
| `count_arkansas_s_corporation_income_once` | logic | AR | 0/200 | 0 | logic | once. | Found by reading TAXSIM's source; no form or statute check needed |
| `omit_georgia_itemizer_credit_per_statute` | parameter | GA | 0/200 | 0 | unverified | none. |  |
| `apply_iowa_personal_credits_before_amt_per_statute` | logic | IA | 0/200 | 0 | logic | credits first (not checked against the IA 6251 instructions). | Found by reading TAXSIM's source; no form or statute check needed |
| `add_back_self_employment_loss_to_earned_income_credit_phaseout_per_statute` | logic | all | 0/2040 | 0 | logic | part of the loss (50% in 1997, 75% from 1998) is added back. | Found by reading TAXSIM's source; no form or statute check needed |
| `deflate_delaware_spouse_self_employment_per_statute` | logic | DE | 0/200 | 0 | logic | all amounts deflated alike. | Found by reading TAXSIM's source; no form or statute check needed |
| `allocate_delaware_credits_freely_between_spouses_per_statute` | parameter | DE | 0/200 | 0 | unverified | credits placed where they save the most tax. |  |
| `limit_connecticut_social_security_exclusion_to_base_amount_per_statute` | parameter | CT | 0/200 | 0 | unverified | 25% of the lesser of benefits and the base amount (not checked against form CT-1040). |  |
| `leave_pre_1987_earned_income_credit_out_of_oklahoma_property_credit_income_per_statute` | logic | OK | 0/200 | 0 | logic | no credit before 1987 (federal credit not in the pre-1987 column). | Found by reading TAXSIM's source; no form or statute check needed |
| `read_oklahoma_federal_credit_income_per_statute` | parameter | OK | 0/200 | 0 | unverified | scaled by the AGI ratio; investment income reduces the credit dollar for dollar. |  |
| `use_nebraska_published_social_security_limit_per_statute` | parameter | NE | 0/200 | 0 | unverified | 49,310 with 60% above it. |  |
| `leave_unreported_spouse_age_out_of_eitc_age_test_per_statute` | logic | all | 0/2040 | 0 | logic | only a taxpayer under the minimum age disqualifies it, and an unreported spouse age passes. | Found by reading TAXSIM's source; no form or statute check needed |
| `pay_nebraska_child_tax_credit_per_statute` | logic | NE | 0/200 | 0 | logic | paid (PolicyEngine amounts). | Found by reading TAXSIM's source; no form or statute check needed |
| `leave_michigan_business_loss_out_of_household_income_per_statute` | logic | MI | 0/200 | 0 | logic | not added back (not checked). | Found by reading TAXSIM's source; no form or statute check needed |
| `reduce_michigan_heating_credit_income_by_half_self_employment_tax_per_statute` | logic | MI | 0/200 | 0 | logic | no deduction (not checked against MI-1040CR-7). | Found by reading TAXSIM's source; no form or statute check needed |
| `test_ohio_senior_credit_on_income_after_business_deduction_per_statute` | parameter | OH | 0/200 | 0 | unverified | tested on income after the business deduction (not checked). |  |
| `base_ohio_joint_credit_on_income_after_business_deduction_per_statute` | logic | OH | 0/200 | 0 | logic | taxable income plus business income (not checked against the IT 1040 instructions). | Found by reading TAXSIM's source; no form or statute check needed |
| `start_maryland_child_tax_credit_phaseout_at_threshold_per_statute` | parameter | MD | 0/200 | 0 | unverified | 500 per child, reduced only above 15,000, and the reduction taken once (not checked against Form 502CR). |  |
| `cap_nonrefundable_child_credit_at_three_children_refundable_maximum_per_statute` | logic | all | 0/2040 | 0 | logic | no cap, earnings formula only (Form 8812 for 2022-23 not re-checked). | Found by reading TAXSIM's source; no form or statute check needed |
| `add_back_half_self_employment_tax_before_iowa_social_security_per_statute` | logic | IA | 0/200 | 0 | logic | added back (not checked against a form). | Found by reading TAXSIM's source; no form or statute check needed |
| `credit_iowa_dependents_in_full_on_separate_spouse_returns_per_statute` | logic | IA | 0/200 | 0 | logic | the full credits against the combined tax. | Found by reading TAXSIM's source; no form or statute check needed |
| `split_wisconsin_self_employment_between_spouses_per_statute` | logic | WI | 0/200 | 0 | logic | half the self-employment total each. | Found by reading TAXSIM's source; no form or statute check needed |
| `leave_massachusetts_part_b_deductions_uncapped_per_statute` | logic | MA | 0/200 | 0 | logic | income without the input counted twice, the deductions uncapped. | Found by reading TAXSIM's source; no form or statute check needed |
| `measure_massachusetts_circuit_breaker_income_per_statute` | logic | MA | 0/200 | 0 | logic | income without the input counted twice, the deductions uncapped. | Found by reading TAXSIM's source; no form or statute check needed |
| `exclude_nonproperty_income_from_federal_earned_income_per_statute` | logic | all | 0/2040 | 0 | logic | not earned income. | Found by reading TAXSIM's source; no form or statute check needed |
| `exclude_maryland_pensions_per_taxpayer_per_statute` | logic | MD | 0/200 | 0 | logic | $ per taxpayer 65 or older less benefits. | Found by reading TAXSIM's source; no form or statute check needed |
| `apply_colorado_pension_subtraction_by_statute_from_2021` | parameter | CO | 0/200 | 0 | unverified | the HB24-1142 rules. |  |
| `count_oregon_eitc_young_children_by_age_per_statute` | logic | OR | 0/200 | 0 | logic | 12% from 2022 with ages. | Found by reading TAXSIM's source; no form or statute check needed |
| `limit_georgia_exclusion_for_ages_62_to_64_before_2021_per_statute` | parameter | GA | 0/200 | 0 | unverified | $35,000 from 2012. |  |
| `cap_virginia_state_tax_deduction_per_statute` | parameter | VA | 0/200 | 0 | unverified | $10,000. |  |
| `allow_virginia_standard_deduction_when_itemizing_federally_per_statute` | logic | VA | 0/200 | 0 | logic | the larger of the two. | Found by reading TAXSIM's source; no form or statute check needed |
| `halve_minnesota_deduction_reduction_for_separate_returns_per_statute` | logic | MN | 0/200 | 0 | logic | halved thresholds. | Found by reading TAXSIM's source; no form or statute check needed |
| `deflate_oklahoma_earned_income_credit_income_per_statute` | logic | OK | 0/200 | 0 | logic | deflated income. | Found by reading TAXSIM's source; no form or statute check needed |
| `reinflate_colorado_earned_income_credit_per_statute` | logic | CO | 0/200 | 0 | logic | reinflated. | Found by reading TAXSIM's source; no form or statute check needed |
| `cap_south_carolina_state_tax_addback_per_statute` | parameter | SC | 0/200 | 0 | unverified | federal cap. |  |
| `limit_south_carolina_standard_deduction_addback_to_nonitemizers_per_statute` | parameter | SC | 0/200 | 0 | unverified | nonitemizers only. |  |
| `subtract_state_tax_from_south_carolina_itemized_deductions` | logic | SC | 0/200 | 0 | logic | subtracted. | Found by reading TAXSIM's source; no form or statute check needed |
| `assign_south_carolina_business_income_to_its_earner` | logic | SC | 0/200 | 0 | logic | by earner. | Found by reading TAXSIM's source; no form or statute check needed |
| `floor_maryland_itemized_deduction_at_zero` | logic | MD | 0/200 | 0 | logic | published. | Found by reading TAXSIM's source; no form or statute check needed |
| `use_idaho_standard_deduction_2008_2009_per_statute` | logic | ID | 0/200 | 0 | logic | the published amount plus the allowance. | Found by reading TAXSIM's source; no form or statute check needed |
| `compute_missouri_pension_exemption_per_statute` | parameter | MO | 0/200 | 0 | unverified | less the state tax. |  |
| `exclude_unemployment_in_district_of_columbia_every_year` | logic | DC | 0/200 | 0 | logic | all unemployment compensation is excluded in every year. | Found by reading TAXSIM's source; no form or statute check needed |
| `use_mississippi_federal_itemized_deductions_from_2021` | logic | MS | 0/200 | 0 | logic | federal itemized deductions. | Found by reading TAXSIM's source; no form or statute check needed |
| `count_idaho_child_credit_children_under_17` | logic | ID | 0/200 | 0 | logic | under 17. | Found by reading TAXSIM's source; no form or statute check needed |
| `count_all_dependents_for_hawaii_child_care` | logic | HI | 0/200 | 0 | logic | all dependents. | Found by reading TAXSIM's source; no form or statute check needed |
| `tax_hawaii_gains_net_of_federal_exclusion` | logic | HI | 0/200 | 0 | logic | gains in AGI. | Found by reading TAXSIM's source; no form or statute check needed |
| `phase_out_connecticut_pension_deduction_per_statute` | parameter | CT | 0/200 | 0 | unverified | share, phased out above. |  |
| `use_colorado_statutory_high_income_addback` | parameter | CO | 0/200 | 0 | unverified | only the state tax that fit under the cap. |  |
| `double_minnesota_separate_returns` | parameter | MN | 0/200 | 0 | unverified | doubled and halved. |  |
| `read_minnesota_child_care_from_federal_credit` | logic | MN | 0/200 | 0 | logic | the federal credit. | Found by reading TAXSIM's source; no form or statute check needed |
| `subtract_minnesota_elderly_and_charity_from_income` | logic | MN | 0/200 | 0 | logic | applied. | Found by reading TAXSIM's source; no form or statute check needed |
| `treat_minnesota_other_itemized_as_state_tax_2018_2019` | logic | MN | 0/200 | 0 | logic | counted. | Found by reading TAXSIM's source; no form or statute check needed |
| `count_unknown_ages_as_eligible_for_minnesota_childless_credit` | logic | MN | 0/200 | 0 | logic | unknown ages eligible. | Found by reading TAXSIM's source; no form or statute check needed |
| `reduce_minnesota_deductions_per_statute` | logic | MN | 0/200 | 0 | logic | published. | Found by reading TAXSIM's source; no form or statute check needed |
| `compute_minnesota_child_care_credit_from_2021_per_statute` | parameter | MN | 0/200 | 0 | unverified | published. |  |
| `use_minnesota_2022_working_family_credit_per_statute` | parameter | MN | 0/200 | 0 | unverified | the 2022 credit. |  |
| `use_minnesota_published_brackets_from_2022` | parameter | MN | 0/200 | 0 | unverified | the published brackets. |  |
| `phase_out_minnesota_exemptions_per_statute` | parameter | MN | 0/200 | 0 | unverified | the published start by status. |  |
| `allow_minnesota_personal_exemption_2013_to_2018` | logic | MN | 0/200 | 0 | logic | paid. | Found by reading TAXSIM's source; no form or statute check needed |
| `limit_missouri_payroll_tax_deduction` | logic | MO | 0/200 | 0 | logic | capped. | Found by reading TAXSIM's source; no form or statute check needed |
| `use_vermont_nonrefundable_child_care_credit_before_2003` | parameter | VT | 0/200 | 0 | unverified | nonrefundable before 2003. |  |
| `pay_vermont_renter_rebate_after_1999` | parameter | VT | 0/200 | 0 | unverified | published. |  |
| `use_current_federal_standard_deduction_for_south_carolina_addback` | parameter | SC | 0/200 | 0 | unverified | the current one. |  |
| `subtract_new_york_social_security_at_any_age` | logic | NY | 0/200 | 0 | logic | always subtracted. | Found by reading TAXSIM's source; no form or statute check needed |
| `compute_new_york_supplemental_tax_per_statute` | parameter | NY | 0/200 | 0 | unverified | the published supplemental tax. |  |
| `use_new_york_statutory_child_care_credit` | parameter | NY | 0/200 | 0 | unverified | the published percentage of the federal credit. |  |
| `use_new_york_dependent_standard_deduction_alone` | parameter | NY | 0/200 | 0 | unverified | $3,100. |  |
| `interpolate_new_york_household_credit_per_person` | parameter | NY | 0/200 | 0 | unverified | the interpolated table. |  |
| `use_oregon_standard_deduction_index_per_statute` | parameter | OR | 0/200 | 0 | unverified | $2,155. |  |
| `apply_oregon_dependent_care_credit_per_statute` | parameter | OR | 0/200 | 0 | unverified | that year's table. |  |
| `count_only_children_under_13_for_oregon_dependent_care` | logic | OR | 0/200 | 0 | logic | only children under 13 (ORS 315.264, IRC 21(b)). | Found by reading TAXSIM's source; no form or statute check needed |
| `apply_nonrefundable_credits_before_1998` | logic | all | 0/2040 | 0 | logic | both apply and are reported as the nonrefundable credit total, which supersedes FED-INCOME-001. | Found by reading TAXSIM's source; no form or statute check needed |
| `exempt_amt_from_itemized_limit_1991_1992` | parameter | all | 0/2040 | 0 | unverified | no add-back; the AMT was exempt from the limit from 1991. |  |
| `cap_amt_exemption_for_dependents_only` | logic | all | 0/2040 | 0 | logic | dependent filers only; IRC 59(j) covers children subject to the kiddie tax. | Found by reading TAXSIM's source; no form or statute check needed |
| `use_joint_elderly_credit_threshold` | parameter | all | 0/2040 | 0 | unverified | $10,000 for any joint return (IRC 22(d)). |  |
| `apply_1979_dividend_exclusion` | parameter | all | 0/2040 | 0 | unverified | $100 ($200 joint). The states that add the exclusion back read the same rule ( |  |
| `exclude_wages_from_1977_preference_income` | logic | all | 0/2040 | 0 | logic | neither is a tax-preference item. | Found by reading TAXSIM's source; no form or statute check needed |
| `deduct_mortgage_interest_from_1983_minimum_tax` | logic | all | 0/2040 | 0 | logic | deducted, as the law allowed. | Found by reading TAXSIM's source; no form or statute check needed |
| `correct_analytic_federal_rate` | logic | all | 0/2040 | 0 | logic |  | Found by reading TAXSIM's source; no form or statute check needed |
| `read_federal_child_care_credit_before_1987` | logic | all | 0/2040 | 0 | logic | uses the 1977-1986 federal credit. | Found by reading TAXSIM's source; no form or statute check needed |
| `use_current_rates_in_dc_earner_split` | parameter | DC | 0/200 | 0 | unverified | 2007 rates. |  |
| `use_current_rates_in_dc_low_income_credit` | parameter | DC | 0/200 | 0 | unverified | the year's rates (2000-2017). |  |
| `apply_iowa_eitc_caps_as_in_2010_law` | parameter | IA | 0/200 | 0 | unverified | caps in 2010 only, $16,590, $38,665 and $43,493 for 0, 1 and 2+ children. |  |
| `subtract_illinois_property_credit_once_in_eitc_limit` | logic | IL | 0/200 | 0 | logic | once. | Found by reading TAXSIM's source; no form or statute check needed |
| `suspend_kentucky_itemized_limitation_through_2012` | parameter | KY | 0/200 | 0 | unverified | suspended 2010-2012 (the 2011 Schedule A has no limitation). |  |
| `exclude_separate_returns_from_massachusetts_exemption_carryover` | parameter | MA | 0/200 | 0 | unverified | not on separate returns. |  |
| `allow_massachusetts_child_care_deduction_2011_2020` | logic | MA | 0/200 | 0 | logic | through 2020 (Form 1 line 10b). | Found by reading TAXSIM's source; no form or statute check needed |
| `pay_minnesota_homemaker_credit_to_nondependents` | logic | MN | 0/200 | 0 | logic | filers who are not dependents. | Found by reading TAXSIM's source; no form or statute check needed |
| `order_minnesota_1998_working_family_credit` | logic | MN | 0/200 | 0 | logic | phase-in, plateau and phaseout in order. | Found by reading TAXSIM's source; no form or statute check needed |
| `use_minnesota_working_family_tables_by_children` | parameter | MN | 0/200 | 0 | unverified | each table's own start. |  |
| `limit_minnesota_child_care_credit_by_maximum_after_2017` | parameter | MN | 0/200 | 0 | unverified | the lesser of the credit and $600 ($1,200) less 5% of the excess (Minn. Stat. 290.067 subd. 2b). |  |
| `allow_minnesota_renter_and_homeowner_refunds` | logic | MN | 0/200 | 0 | logic | both refunds (Form M1PR). | Found by reading TAXSIM's source; no form or statute check needed |
| `floor_mississippi_excess_charity` | parameter | MS | 0/200 | 0 | unverified | floored at 0. |  |
| `subtract_only_taxable_unemployment_in_montana` | logic | MT | 0/200 | 0 | logic | the part in federal AGI. | Found by reading TAXSIM's source; no form or statute check needed |
| `subtract_montana_separate_capital_loss_once` | parameter | MT | 0/200 | 0 | unverified | once. |  |
| `suspend_montana_itemized_limit_through_2012` | parameter | MT | 0/200 | 0 | unverified | suspended 2010-2012 (the 2011 Form 2 has none). |  |
| `count_all_children_for_north_carolina_joint_child_care` | parameter | NC | 0/200 | 0 | unverified | all children. |  |
| `take_north_carolina_child_care_credit_once` | logic | NC | 0/200 | 0 | logic | off the larger tax once. | Found by reading TAXSIM's source; no form or statute check needed |
| `use_north_carolina_married_table_without_splitting` | parameter | NC | 0/200 | 0 | unverified | the married table without splitting. |  |
| `use_north_carolina_head_of_household_deduction_per_statute` | parameter | NC | 0/200 | 0 | unverified | published amounts. |  |
| `cap_north_carolina_property_tax_per_statute` | logic | NC | 0/200 | 0 | logic | only the $20,000 limit on mortgage interest plus property tax. | Found by reading TAXSIM's source; no form or statute check needed |
| `add_back_north_dakota_state_tax_for_itemizers_only` | logic | ND | 0/200 | 0 | logic | only when deducted federally. | Found by reading TAXSIM's source; no form or statute check needed |
| `add_nebraska_single_additional_tax_once` | parameter | NE | 0/200 | 0 | unverified | once. |  |
| `set_new_mexico_capital_gains_share_per_statute` | logic | NM | 0/200 | 0 | logic | 40 percent. | Found by reading TAXSIM's source; no form or statute check needed |
| `compute_new_mexico_capital_gains_deduction_per_statute` | parameter | NM | 0/200 | 0 | unverified | never more than the gain, halved on separate returns. |  |
| `compute_maryland_earned_income_credit_per_statute` | parameter | MD | 0/200 | 0 | unverified | floor credit. |  |
| `split_maryland_joint_income_per_statute` | logic | MD | 0/200 | 0 | logic | wages plus half the rest. | Found by reading TAXSIM's source; no form or statute check needed |
| `scale_maryland_child_care_credit_per_statute` | parameter | MD | 0/200 | 0 | unverified | 32.5 percent. |  |
| `compute_oklahoma_sales_tax_credit_income_per_statute` | logic | OK | 0/200 | 0 | logic | household income. | Found by reading TAXSIM's source; no form or statute check needed |
| `read_oklahoma_federal_credit_law_year_per_statute` | parameter | OK | 0/200 | 0 | unverified | 2021 federal credit, limited to tax. |  |
| `limit_oklahoma_2021_child_credits_to_tax` | parameter | OK | 0/200 | 0 | unverified | 2021 federal credit, limited to tax. |  |
| `pay_wisconsin_child_care_credit_per_statute` | parameter | WI | 0/200 | 0 | unverified | share of the federal credit. |  |
| `test_indiana_childless_credit_ages_per_statute` | parameter | IN | 0/200 | 0 | unverified | floor on the federal credit, ages tested only when reported. |  |
| `apply_indiana_federal_credit_floor_from_2011` | parameter | IN | 0/200 | 0 | unverified | floor on the federal credit, ages tested only when reported. |  |
| `use_indiana_decoupled_credit_schedule_in_2021` | parameter | IN | 0/200 | 0 | unverified | floor on the federal credit, ages tested only when reported. |  |
| `keep_california_salt_addback_cents_per_statute` | parameter | CA | 0/200 | 0 | unverified | with the cents. |  |
| `exclude_utah_state_income_tax_from_taxpayer_credit_before_2018` | logic | UT | 0/200 | 0 | logic | other deductions only. | Found by reading TAXSIM's source; no form or statute check needed |
| `start_utah_dependent_filer_credit_phaseout_per_statute` | logic | UT | 0/200 | 0 | logic | normal start. | Found by reading TAXSIM's source; no form or statute check needed |
| `use_wisconsin_published_2022_separate_brackets_per_statute` | logic | WI | 0/200 | 0 | logic | published separate brackets. | Found by reading TAXSIM's source; no form or statute check needed |
| `use_utah_statute_exempt_taxpayer_floor_per_statute` | parameter | UT | 0/200 | 0 | unverified | the federal standard deduction. |  |
| `deny_new_york_2021_earned_income_credit_to_separate_returns` | logic | NY | 0/200 | 0 | logic | by filing status, no credit on separate returns. | Found by reading TAXSIM's source; no form or statute check needed |
| `pick_new_york_worksheet_by_filing_status` | logic | NY | 0/200 | 0 | logic | by filing status, no credit on separate returns. | Found by reading TAXSIM's source; no form or statute check needed |
| `use_kansas_2021_standard_deduction_per_statute` | parameter | KS | 0/200 | 0 | unverified | aged addition and limit every year, $7,800 joint in 2021. |  |
| `credit_maine_1988_standard_deduction_and_exemptions` | logic | ME | 0/200 | 0 | logic | credits. | Found by reading TAXSIM's source; no form or statute check needed |
| `make_maine_elderly_credit_refundable` | logic | ME | 0/200 | 0 | logic | nonrefundable. | Found by reading TAXSIM's source; no form or statute check needed |
| `halve_west_virginia_separate_brackets_per_statute` | logic | WV | 0/200 | 0 | logic | separate returns on half the thresholds. | Found by reading TAXSIM's source; no form or statute check needed |
| `limit_rhode_island_retirement_exclusion_to_aged_per_statute` | logic | RI | 0/200 | 0 | logic | aged taxpayers only. | Found by reading TAXSIM's source; no form or statute check needed |
| `take_arizona_larger_of_property_and_renter_credit` | logic | AZ | 0/200 | 0 | logic | the larger one. | Found by reading TAXSIM's source; no form or statute check needed |
| `read_west_virginia_family_credit_limits_per_statute` | parameter | WV | 0/200 | 0 | unverified | the published limits. |  |
| `step_west_virginia_family_credit_phaseout_per_statute` | parameter | WV | 0/200 | 0 | unverified | steps. |  |
| `leave_out_west_virginia_child_care_credit_from_2024` | logic | WV | 0/200 | 0 | logic | none. | Found by reading TAXSIM's source; no form or statute check needed |
| `require_earned_income_for_ohio_joint_credit` | parameter | OH | 0/200 | 0 | unverified | earned income. |  |
| `test_ohio_credits_before_business_deduction_per_statute` | logic | OH | 0/200 | 0 | logic | before it. | Found by reading TAXSIM's source; no form or statute check needed |
| `tax_south_carolina_capital_gains_1985_1989` | logic | SC | 0/200 | 0 | logic | gains taxed in full. | Found by reading TAXSIM's source; no form or statute check needed |
| `pay_district_of_columbia_property_credit_2014_2018` | parameter | DC | 0/200 | 0 | unverified | the credit schedule. |  |
| `use_wisconsin_separate_standard_deduction_from_2023` | parameter | WI | 0/200 | 0 | unverified | married separate table, homestead for all who qualify. |  |
| `deduct_district_of_columbia_other_itemized_with_state_taxes` | logic | DC | 0/200 | 0 | logic | from the inputs. | Found by reading TAXSIM's source; no form or statute check needed |
| `start_wisconsin_head_of_household_third_tier_per_statute` | parameter | WI | 0/200 | 0 | unverified | from the first tier. |  |
| `use_own_bounds_in_new_york_worksheets` | parameter | NY | 0/200 | 0 | unverified | each worksheet's own bound. |  |
| `charge_new_york_unearned_income_tax_1987_1988` | logic | NY | 0/200 | 0 | logic | 3% (1987) and 2% (1988) of dividends, interest and gains, phased in from $100,000 of AGI. | Found by reading TAXSIM's source; no form or statute check needed |
| `charge_new_york_minimum_tax_on_preferences_only` | parameter | NY | 0/200 | 0 | unverified | none without preference items. |  |
| `floor_new_york_itemized_deductions` | logic | NY | 0/200 | 0 | logic | floored at 0. | Found by reading TAXSIM's source; no form or statute check needed |
| `replace_federal_unemployment_in_oregon_1981_1984` | parameter | OR | 0/200 | 0 | unverified | Oregon's taxable amount replaces it. |  |
| `pay_oregon_2015_2017_kickers` | logic | OR | 0/200 | 0 | logic | 5.6% of tax. | Found by reading TAXSIM's source; no form or statute check needed |
| `pay_oregon_2019_kicker` | logic | OR | 0/200 | 0 | logic | 2019 paid, 2021 not (the published credit for 2021 is not coded). | Found by reading TAXSIM's source; no form or statute check needed |
| `tax_oregon_dependent_filers_per_statute` | logic | OR | 0/200 | 0 | logic | taxed on the single schedule. | Found by reading TAXSIM's source; no form or statute check needed |
| `omit_oregon_2021_kicker` | logic | OR | 0/200 | 0 | logic | 2019 paid, 2021 not (the published credit for 2021 is not coded). | Found by reading TAXSIM's source; no form or statute check needed |
| `use_net_long_term_gain_in_rhode_island` | parameter | RI | 0/200 | 0 | unverified | net long-term gain only. |  |
| `halve_rhode_island_separate_standard_deduction` | logic | RI | 0/200 | 0 | logic | half. | Found by reading TAXSIM's source; no form or statute check needed |
| `subtract_rhode_island_tax_once_in_minimum_tax` | logic | RI | 0/200 | 0 | logic | once. | Found by reading TAXSIM's source; no form or statute check needed |
| `deduct_south_carolina_federal_tax_under_1980_law` | parameter | SC | 0/200 | 0 | unverified | tax under 1980 law. |  |
| `allow_virginia_child_care_deduction_2003` | logic | VA | 0/200 | 0 | logic | the $3,000 federal expense limit. | Found by reading TAXSIM's source; no form or statute check needed |
| `read_virginia_low_income_limits_per_statute` | parameter | VA | 0/200 | 0 | unverified | the guidelines and the year's filing threshold. |  |
| `use_north_dakota_married_separate_brackets_from_2023` | parameter | ND | 0/200 | 0 | unverified | the married-separate table. |  |
| `use_published_north_dakota_brackets_from_2024` | parameter | ND | 0/200 | 0 | unverified | published amounts. |  |
| `allow_unreported_ages_in_dc_childless_credit_from_2024` | logic | DC | 0/200 | 0 | logic | the age tests as the form has them; capped state tax. | Found by reading TAXSIM's source; no form or statute check needed |
| `cap_district_of_columbia_state_tax_deduction_from_2024` | logic | DC | 0/200 | 0 | logic | the age tests as the form has them; capped state tax. | Found by reading TAXSIM's source; no form or statute check needed |
| `subtract_minnesota_social_security_without_age_from_2024` | parameter | MN | 0/200 | 0 | unverified | the dated rules. |  |
| `charge_minnesota_alternative_minimum_tax_from_2024` | parameter | MN | 0/200 | 0 | unverified | the dated rules. |  |
| `limit_idaho_dependent_standard_deduction_per_statute` | logic | ID | 0/200 | 0 | logic | the larger of the minimum and earned income plus an addition. | Found by reading TAXSIM's source; no form or statute check needed |
| `pay_maine_childless_credit_rate_to_all_ages_per_statute` | logic | ME | 0/200 | 0 | logic | 25% for every childless filer. | Found by reading TAXSIM's source; no form or statute check needed |
| `take_utah_small_tax_credit_once` | logic | UT | 0/200 | 0 | logic | once. | Found by reading TAXSIM's source; no form or statute check needed |
| `take_maine_property_fairness_rent_at_statutory_share` | logic | ME | 0/200 | 0 | logic | 12.75%. | Found by reading TAXSIM's source; no form or statute check needed |
| `limit_virginia_itemized_deductions_without_other_items` | parameter | VA | 0/200 | 0 | unverified | published. |  |
| `scale_virginia_itemized_limit_threshold` | parameter | VA | 0/200 | 0 | unverified | in the reduction too. |  |
| `floor_wisconsin_capital_gain_exclusion` | logic | WI | 0/200 | 0 | logic | floored at 0. | Found by reading TAXSIM's source; no form or statute check needed |
| `start_wisconsin_separate_standard_deduction_tier_at_its_range` | parameter | WI | 0/200 | 0 | unverified | from $4,750. |  |
| `limit_wisconsin_capital_loss_to_statutory_amount` | parameter | WI | 0/200 | 0 | unverified | $500. |  |
| `limit_wisconsin_dependent_standard_deduction_from_2023` | logic | WI | 0/200 | 0 | logic | limited to earned income plus an addition. | Found by reading TAXSIM's source; no form or statute check needed |
| `keep_wisconsin_mortgage_interest_cents` | logic | WI | 0/200 | 0 | logic | cents kept. | Found by reading TAXSIM's source; no form or statute check needed |
| `keep_wisconsin_1999_itemized_credit` | logic | WI | 0/200 | 0 | logic | kept. | Found by reading TAXSIM's source; no form or statute check needed |
| `index_wisconsin_joint_brackets` | logic | WI | 0/200 | 0 | logic | indexed like single. | Found by reading TAXSIM's source; no form or statute check needed |
| `base_wisconsin_renter_credit_on_rent` | logic | WI | 0/200 | 0 | logic | based on rent. | Found by reading TAXSIM's source; no form or statute check needed |
| `halve_wisconsin_separate_amt_exemption` | logic | WI | 0/200 | 0 | logic | half the exemption ( | Found by reading TAXSIM's source; no form or statute check needed |
| `add_wisconsin_minimum_tax_once` | logic | WI | 0/200 | 0 | logic | half the exemption ( | Found by reading TAXSIM's source; no form or statute check needed |
| `use_wisconsin_net_household_income_1989_1990` | logic | WI | 0/200 | 0 | logic | net income for both. | Found by reading TAXSIM's source; no form or statute check needed |
| `credit_wisconsin_1983_homestead_excess` | parameter | WI | 0/200 | 0 | unverified | 80%. |  |
| `report_base_state_worksheet` | logic | all | 0/2040 | 0 | logic | drops the $1 and reports the base calculation. | Found by reading TAXSIM's source; no form or statute check needed |
| `report_social_security_taxable_earnings` | logic | all | 0/2040 | 0 | logic | reports both earners' wages plus 92.35% of net business income, each capped at the wage base. | Found by reading TAXSIM's source; no form or statute check needed |

## Switches removed: TAXSIM's behavior is not a bug

| Switch | Why |
|:--|:--|
| `double_kentucky_standard_deduction_for_joint_returns` | Kentucky Form 740 gives one standard deduction amount by year that does not vary by filing status; no source found that a joint return takes two, so TAXSIM's one-per-return was not shown wrong. Statutory mode now uses it (switch removed); 2026-11-14 |
| `exempt_pennsylvania_pensions_from_age_65_per_statute` | Pennsylvania exempts retirement distributions from the plan's retirement age (REV-636) with no fixed age and no per-person rule the inputs can apply, so TAXSIM's reading (a couple's pension to the spouse 55 or older, exempt from 60) is a defensible implementation and was not shown wrong. Statutory mode now uses it; the switch was removed; 2026-11-14 |
