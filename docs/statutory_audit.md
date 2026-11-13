# Audit of the statutory side

Statutory mode is only useful if each place it departs from TAXSIM can be defended. This page lists every
statutory switch (`src/taxsim_py/behavior.py`), ranked by how much is at stake, and says whether its statutory
side has been checked against a form or statute.

- Switches: 449. Statutory mode currently departs from TAXSIM on 129 of them (known TAXSIM bugs: 115; checked against a form or statute: 17) and keeps TAXSIM's behavior on the other 320 until verified. Verified against a primary source: 8. Corrected after checking: 0.
  Partly checked: 9. Not yet checked: 316.
  Switches where statutory mode keeps TAXSIM's behavior because TAXSIM was not shown wrong: 2 (listed after the table).
- Switches whose flip changes at least one household in the sample: 166. The rest change nobody in the sample
  (they apply to inputs or years the CPS sample does not reach) and are checked last.

How the ranking was made: for each switch, statutory mode is run on CPS households (40 per state, law years 1985,
2000, 2012, 2022 and 2025) with only that switch set to TAXSIM's behavior. "Changed" counts households whose federal or state
tax moves by more than $1; "average" is the mean move for those households.
(`scripts/audit_switch_impact.py`). Verdicts live in `docs/data/statutory_audit_status.yaml`; this page is built by
`scripts/build_audit_ledger.py`.

**Rule.** Statutory mode departs from TAXSIM only where TAXSIM is shown to be wrong: it contradicts a form or statute,
or its own source is evidently broken (a variable never set, a typo). Where TAXSIM's behavior is a defensible reading, or
the inputs cannot decide, statutory mode keeps TAXSIM's behavior (`KEEP_TAXSIM_BEHAVIOR` in `behavior.py`). Switches
that still show "unverified" depart from TAXSIM without that proof yet; each is a candidate to be moved to the kept list.

Status: **known_bug** = TAXSIM's own source is evidently broken or a documented, tested correction (statutory mode
departs from TAXSIM); **verified** = the statutory rule matches a cited primary source (departs); **partial** = confirmed
in part, still departs because TAXSIM is a known bug; **unverified** = not shown wrong, so statutory mode **keeps TAXSIM's
behavior** for now.

| Switch | States | Households changed | Average change ($) | Status | Statutory side | Source |
|:--|:--:|:--:|--:|:--:|:--|:--|
| `floor_child_credit_phaseout_at_zero` | all | 100/2040 | 1941 | verified | floored. | IRC 24(b): the credit is reduced by $50 per $1,000 of income over the threshold and cannot go below zero (it is a reduction of the credit amount); checked 2026-11-13 |
| `use_form_8995a_qbi_streams` | all | 62/2040 | 1736 | verified | Form 8995-A streams. | IRS Form 8995-A and its instructions (qualified business income by trade or business, separate W-2 wage and UBIA limits); checked 2026-11-13 |
| `count_other_property_income_from_2024` | all | 22/2040 | 4581 | verified | other property income counted; | IRC 32(i)(2) (same rule, applied to the 2024 on federal routine); checked 2026-11-13 |
| `leave_out_one_time_state_rebates` | all | 98/2040 | 586 | partial | not paid. Taxsim mode: | Rebates are payments by the state, not tax liability, so statutory mode leaves them out by design; not a legal question |
| `use_maine_year_standard_deduction` | ME | 60/200 | 823 | partial | that year's amount. | Maine 2012 1040ME booklet and conformity summary: Maine conformed its standard deduction to the federal amount from 2012; earlier years used Maine's own amounts (joint 9,800 against 15,000 under conformity in the fiscal summary). The amounts statutory mode uses for 1989-2011 are not yet compared with the Maine booklets; checked 2026-11-13 |
| `limit_arkansas_low_income_table_to_its_range` | AR | 57/200 | 844 | partial | step table. | Arkansas AR1000F instructions (2023): the low-income tax tables apply when total income from all sources is within the table's limits; the table range and the step table below it were not compared with the form; checked 2026-11-13 |
| `tax_nebraska_before_1987_per_statute` | NE | 33/200 | 1069 | verified | the share of federal tax. | Nebraska Legislature, individual income tax chronology (nebraskalegislature.gov/app_rev/source/chrono_incometax.htm): through 1986 the tax was a percentage of federal liability, 18/16/18/15/15/18/20/19/20/19% for 1977-86, identical to federal_tax_share in ne/income_tax.yaml; checked 2026-11-13 |
| `exclude_hawaii_pensions` | HI | 30/200 | 1097 | partial | excludes | Hawaii Schedule J instructions: pension distributions after 1997 are exempt to the extent attributable to employer contributions; the part traceable to the employee's own contributions is taxable. Statutory mode excludes all of the pension input, which overstates the exemption when part of the pension is employee-funded (the inputs cannot say); checked 2026-11-13 |
| `keep_itemized_inputs_when_taking_standard_deduction` | all | 121/2040 | 183 | verified | a forced standard deduction. | IRC 63(b), (e): a taxpayer who elects the standard deduction takes no itemized deductions; checked 2026-11-13 |
| `count_vermont_child_credit_by_statutory_age` | VT | 15/200 | 1330 | verified | under 6 (under 7 from 2025). | Vermont Dept of Taxes, 2025 tax changes and Act 71 of 2025: child credit up to $1,000 for a child under 6 before 2025 and age 6 or younger from 2025; checked 2026-11-13 |
| `pay_kansas_credits_per_statute` | KS | 38/200 | 475 | known_bug | refundable credits as published. | TAXSIM's behavior is evidently a defect from the Fortran source (an unset variable, typo or overwritten result) or a documented correction with a test; statutory side not yet checked against a form |
| `pay_minnesota_property_tax_refund_through_2023` | MN | 28/200 | 639 | partial | paid. | Minnesota Form M-1PR (homestead credit refund, renter's property tax refund) is a real refundable credit in these years; the refund tables have not been compared line by line; checked 2026-11-13 |
| `deduct_colorado_federal_tax_after_1977` | CO | 32/200 | 484 | known_bug | both deducted. | TAXSIM's behavior is evidently a defect from the Fortran source (an unset variable, typo or overwritten result) or a documented correction with a test; statutory side not yet checked against a form |
| `exclude_transfers_from_provisional_income` | all | 17/2040 | 668 | verified | adds nothing; the law adds tax-exempt interest, which TAXSIM has no input for. The same applies to the Iowa (2014 on) and Montana worksheets. | IRC 86(b)(2): provisional income is modified AGI plus tax-exempt interest plus half of benefits; transfers are not in it; checked 2026-11-13 |
| `subtract_state_tax_in_georgia_itemized_deduction` | GA | 17/200 | 534 | partial | less the state tax. | Georgia Form 500 and Schedule 1 (IT-511 booklet): state income tax claimed on federal Schedule A is added back, and Georgia keeps a 10,000 SALT limit; confirmed from Georgia DOR forms and Intuit/TurboTax expert answers, IT-511 text itself not read; checked 2026-11-13 |
| `use_colorado_statutory_sales_tax_refund` | CO | 54/200 | 161 | unverified | none. |  |
| `pay_new_york_empire_child_credit_per_statute` | NY | 18/200 | 481 | partial | children 4-16. | NY Tax Dept Empire State child credit page and IT-213 instructions: 330 per child at least 4 and under 17, with children under 4 added at 330 before 2025 and 1,000 from 2025; phase-out over 75,000 / 110,000. The pre-2018 statutory rules (which years counted children under 4 and 4-16) were not compared line by line; checked 2026-11-13 |
| `remove_state_tax_from_west_virginia_itemized_deductions` | WV | 9/200 | 905 | known_bug | removed. | TAXSIM's behavior is evidently a defect from the Fortran source (an unset variable, typo or overwritten result) or a documented correction with a test; statutory side not yet checked against a form |
| `double_kentucky_standard_deduction_for_joint_returns` | KY | 57/200 | 135 | kept_taxsim | a standard deduction per taxpayer and the higher earner's wages plus half the other income. | Kentucky Form 740 gives one standard deduction amount by year that does not vary by filing status; no source found that a joint return takes two, so TAXSIM's one-per-return was not shown wrong. Statutory mode now uses it (switch kept in KEEP_TAXSIM_BEHAVIOR); 2026-11-14 |
| `count_colorado_credit_children_per_statute` | CO | 4/200 | 1543 | partial | children under 6 and 6-16 with the flat formula. | Colorado DOR child tax credit page and Family Affordability Tax Credit coverage: FATC up to 3,200 per child under 6 and 2,400 per child 6-16 in 2024 and 2025, phase-out 85,000 single / 95,000 joint; CTC AGI limits 75,000/85,000 (2024) and 77,000/87,000 (2025); numbers from news and agency summaries, not the DR 0104CN text; checked 2026-11-13 |
| `count_other_property_income_in_eitc_disqualified_income_per_statute` | all | 1/2040 | 6015 | verified | counted in every year. | IRC 32(i)(2): disqualified income includes interest, dividends, net rent and royalty income, capital gain net income and passive activity income, so other property income counts in every year; checked 2026-11-13 |
| `split_montana_joint_returns_per_statute` | MT | 24/200 | 250 | unverified | higher earner plus a share. |  |
| `file_kentucky_combined_returns_per_statute` | KY | 45/200 | 131 | partial | a standard deduction per taxpayer and the higher earner's wages plus half the other income. | Allocating non-wage income between spouses on a combined return is a modelling convention, so TAXSIM's split was not shown wrong; to be moved to KEEP_TAXSIM_BEHAVIOR once the Rust engine can run TAXSIM's split (not yet ported); 2026-11-14 |
| `pay_kansas_homestead_refund_from_2007_per_statute` | KS | 11/200 | 502 | unverified | the refund. |  |
| `compute_missouri_itemized_deduction_before_2018` | MO | 11/200 | 452 | unverified | itemized. |  |
| `leave_missouri_federal_tax_deduction_uncapped_before_1994` | MO | 14/200 | 343 | unverified | published. |  |
| `compute_delaware_before_1996_per_statute` | DE | 17/200 | 278 | unverified | the rules of each year. |  |
| `allow_west_virginia_disability_deduction` | WV | 5/200 | 936 | known_bug | under 65 with taxable Social Security. | TAXSIM's behavior is evidently a defect from the Fortran source (an unset variable, typo or overwritten result) or a documented correction with a test; statutory side not yet checked against a form |
| `compute_new_jersey_tax_per_statute` | NJ | 39/200 | 117 | unverified | the published rules (the code before this change). |  |
| `apply_missouri_property_credit_to_tax_paid` | MO | 5/200 | 771 | known_bug | tax paid up to the maximum ($1,100 for owners from 2008) less the income percentage (RSMo 135.030). | TAXSIM's behavior is evidently a defect from the Fortran source (an unset variable, typo or overwritten result) or a documented correction with a test; statutory side not yet checked against a form |
| `step_missouri_property_credit_per_statute` | MO | 10/200 | 364 | unverified | stepped schedule with an income limit. |  |
| `split_district_of_columbia_joint_returns_per_statute` | DC | 23/200 | 156 | unverified | the split by each spouse's own income. |  |
| `subtract_west_virginia_social_security_per_statute` | WV | 9/200 | 389 | unverified | the phased Social Security subtraction for every filer. |  |
| `subtract_ohio_exemption_credit_once` | OH | 26/200 | 120 | known_bug | the exemption method that gives the lower tax, one joint credit. | TAXSIM's behavior is evidently a defect from the Fortran source (an unset variable, typo or overwritten result) or a documented correction with a test; statutory side not yet checked against a form |
| `allow_additional_child_credit_before_2001` | all | 3/2040 | 992 | known_bug | the unused credit up to the employee share of payroll tax (and half of self-employment tax) less the EITC (IRC 24(d) as enacted by P.L. 105-34). | TAXSIM's behavior is evidently a defect from the Fortran source (an unset variable, typo or overwritten result) or a documented correction with a test; statutory side not yet checked against a form |
| `apply_michigan_retirement_tiers` | MI | 5/200 | 566 | known_bug | tiers by the older spouse's birth year; before 1946 the pension and investment subtractions; 1946-1952 up to $20,000 ($40,000 joint) of pensions, against all in | TAXSIM's behavior is evidently a defect from the Fortran source (an unset variable, typo or overwritten result) or a documented correction with a test; statutory side not yet checked against a form |
| `pay_vermont_telephone_credit` | VT | 20/200 | 118 | unverified | published. |  |
| `pay_wisconsin_homestead_credit_from_2023` | WI | 2/200 | 1168 | unverified | married separate table, homestead for all who qualify. |  |
| `compute_minnesota_property_tax_refund_per_statute` | MN | 8/200 | 291 | unverified | the Form M1PR refund. |  |
| `exempt_arkansas_unemployment_from_2024_per_statute` | AR | 10/200 | 230 | unverified | exempt. |  |
| `pay_hawaii_credits_below_zero_tax` | HI | 22/200 | 102 | unverified | refundable. |  |
| `deduct_iowa_federal_tax_after_refundable_credits` | IA | 21/200 | 102 | unverified | published. |  |
| `deduct_virginia_age_deduction_per_statute` | VA | 2/200 | 1035 | known_bug | $12,000 per aged taxpayer. | TAXSIM's behavior is evidently a defect from the Fortran source (an unset variable, typo or overwritten result) or a documented correction with a test; statutory side not yet checked against a form |
| `cap_oregon_property_tax_deduction_per_statute` | OR | 3/200 | 635 | unverified | $10,000. |  |
| `split_delaware_income_before_pension_exclusion_per_statute` | DE | 5/200 | 379 | unverified | split after the exclusion; no credit. |  |
| `compute_montana_tax_from_2024_per_statute` | MT | 3/200 | 577 | unverified | the Montana worksheet. |  |
| `split_mississippi_joint_returns_per_statute` | MS | 14/200 | 122 | unverified | published split. |  |
| `use_new_mexico_published_child_tax_credit` | NM | 11/200 | 154 | unverified | the published tiers. |  |
| `require_age_65_for_ohio_retirement_credit` | OH | 10/200 | 164 | unverified | taxpayers 65 or older. |  |
| `apply_maryland_unmarried_childless_earned_income_credit_per_statute` | MD | 5/200 | 310 | unverified | whole federal credit, nonrefundable part first, refundable up to the limit. |  |
| `exclude_arkansas_pensions_per_taxpayer` | AR | 4/200 | 372 | unverified | per aged taxpayer. |  |
| `compute_virginia_spouse_adjustment_per_statute` | VA | 12/200 | 121 | unverified | published. |  |
| `reduce_louisiana_joint_tax_once_for_dependents` | LA | 34/200 | 40 | known_bug | once. | TAXSIM's behavior is evidently a defect from the Fortran source (an unset variable, typo or overwritten result) or a documented correction with a test; statutory side not yet checked against a form |
| `deduct_missouri_federal_tax_from_2022` | MO | 56/200 | 24 | unverified | published. |  |
| `cap_new_york_state_tax_deduction_per_statute` | NY | 3/200 | 441 | unverified | federal cap. |  |
| `limit_hawaii_state_tax_deduction_from_2011` | HI | 2/200 | 657 | unverified | 2011. |  |
| `refund_virginia_earned_income_credit_at_2025_rate` | VA | 7/200 | 186 | unverified | 20%. |  |
| `count_half_self_employment_tax_in_refundable_child_credit_per_statute` | all | 1/2040 | 1289 | unverified | half of the self-employment tax (Form 8812 line "one-half of self-employment tax"; not re-checked for each year). |  |
| `step_maryland_exemption_phaseout_per_statute` | MD | 24/200 | 52 | unverified | steps. |  |
| `use_new_york_household_credit_table_1990_2016` | NY | 30/200 | 41 | unverified | the published table. |  |
| `let_delaware_itemize_when_federal_return_takes_standard_deduction` | DE | 8/200 | 153 | unverified | Delaware itemizes on its own. |  |
| `apply_pennsylvania_forgiveness_rules` | PA | 15/200 | 79 | known_bug | eligibility income adds unemployment compensation and exempt pensions; 10% steps. | TAXSIM's behavior is evidently a defect from the Fortran source (an unset variable, typo or overwritten result) or a documented correction with a test; statutory side not yet checked against a form |
| `use_maryland_itemized_deduction_without_federal_itemizing` | MD | 28/200 | 42 | unverified | published. |  |
| `deduct_missouri_federal_tax_after_refundable_credits` | MO | 38/200 | 31 | unverified | after the earned income credit. |  |
| `require_age_65_for_michigan_investment_deduction_per_statute` | MI | 4/200 | 281 | unverified | age 65 or older, with Social Security also subtracted. |  |
| `exclude_new_jersey_pensions_per_statute` | NJ | 6/200 | 183 | unverified | the published rules (the code before this change). |  |
| `compute_new_york_property_credit_per_statute` | NY | 5/200 | 219 | unverified | the published credit. |  |
| `pay_oregon_kids_credit_per_statute` | OR | 1/200 | 1050 | known_bug | children under 6. | TAXSIM's behavior is evidently a defect from the Fortran source (an unset variable, typo or overwritten result) or a documented correction with a test; statutory side not yet checked against a form |
| `apply_minnesota_pension_exclusion_through_1986_per_statute` | MN | 2/200 | 514 | known_bug | subtracted. | TAXSIM's behavior is evidently a defect from the Fortran source (an unset variable, typo or overwritten result) or a documented correction with a test; statutory side not yet checked against a form |
| `allow_connecticut_property_credit_to_all_from_2022` | CT | 5/200 | 201 | unverified | any filer from 2022. |  |
| `use_arkansas_statutory_step_credits` | AR | 22/200 | 45 | unverified |  |  |
| `apply_elderly_credit_before_1987` | all | 6/2040 | 164 | known_bug | 15% of $2,500 ($3,750 for a couple both 65 or older; from 1984 $5,000 and $7,500) less nontaxable Social Security and half of AGI over $7,500 ($10,000 joint) (I | TAXSIM's behavior is evidently a defect from the Fortran source (an unset variable, typo or overwritten result) or a documented correction with a test; statutory side not yet checked against a form |
| `step_missouri_federal_tax_deduction_share` | MO | 42/200 | 23 | unverified | published. |  |
| `apply_state_childless_eitc_minimum_age` | all | 4/2040 | 242 | known_bug | from 18, with no maximum age (R&TC 17052); an unreported age passes. | TAXSIM's behavior is evidently a defect from the Fortran source (an unset variable, typo or overwritten result) or a documented correction with a test; statutory side not yet checked against a form |
| `use_louisiana_brackets_per_statute` | LA | 5/200 | 192 | known_bug | $91,000; heads of household $3,500 and $41,000. | TAXSIM's behavior is evidently a defect from the Fortran source (an unset variable, typo or overwritten result) or a documented correction with a test; statutory side not yet checked against a form |
| `exempt_missouri_social_security_at_any_age_per_statute` | MO | 1/200 | 953 | unverified | all benefits under the income limits. |  |
| `keep_oregon_state_tax_deduction_for_aged_per_statute` | OR | 3/200 | 310 | unverified | they keep the state income tax deduction. |  |
| `tax_new_jersey_top_rate_after_1982_per_statute` | NJ | 5/200 | 181 | unverified | the published schedules. |  |
| `step_ohio_exemption_by_income` | OH | 47/200 | 17 | unverified | steps. |  |
| `exclude_transfers_from_connecticut_income_per_statute` | CT | 4/200 | 202 | unverified | excluded. |  |
| `zero_virginia_tax_below_filing_threshold` | VA | 6/200 | 132 | unverified | exempt. |  |
| `pay_maryland_refundable_eitc_only_above_tax` | MD | 5/200 | 156 | unverified | only the excess over tax. |  |
| `exempt_alabama_retirement_income_in_2022` | AL | 3/200 | 253 | unverified | 2022. |  |
| `ignore_federal_salt_cap_in_california` | CA | 1/200 | 757 | known_bug | California never adopted the cap (Schedule CA (540)); real estate and other taxes are deductible in full, its own income tax not at all. | TAXSIM's behavior is evidently a defect from the Fortran source (an unset variable, typo or overwritten result) or a documented correction with a test; statutory side not yet checked against a form |
| `count_ohio_joint_credit_unemployment_as_taxable_per_statute` | OH | 5/200 | 149 | unverified | taxable unemployment. |  |
| `include_unemployment_in_california_total_income` | CA | 1/200 | 726 | known_bug | not subtracted. | TAXSIM's behavior is evidently a defect from the Fortran source (an unset variable, typo or overwritten result) or a documented correction with a test; statutory side not yet checked against a form |
| `exempt_new_mexico_social_security_1984_1989` | NM | 1/200 | 706 | known_bug | subtracted. | TAXSIM's behavior is evidently a defect from the Fortran source (an unset variable, typo or overwritten result) or a documented correction with a test; statutory side not yet checked against a form |
| `step_alabama_dependent_exemption_per_statute` | AL | 28/200 | 23 | unverified | published steps. |  |
| `start_ohio_business_deduction_per_statute` | OH | 4/200 | 157 | unverified | none until 2015. |  |
| `split_iowa_joint_income_by_wages_only` | IA | 10/200 | 61 | unverified | published. |  |
| `count_all_dependents_for_maine_dependent_credit` | ME | 2/200 | 302 | unverified | all dependents. |  |
| `use_minnesota_2012_joint_working_family_thresholds` | MN | 2/200 | 301 | known_bug | joint thresholds. | TAXSIM's behavior is evidently a defect from the Fortran source (an unset variable, typo or overwritten result) or a documented correction with a test; statutory side not yet checked against a form |
| `limit_minnesota_itemized_deduction_from_2024_per_statute` | MN | 1/200 | 599 | unverified | the dated rules. |  |
| `read_montana_elderly_credit_multiplier_per_statute` | MT | 3/200 | 197 | unverified | 100 percent to $35,000, then steps. |  |
| `apply_kentucky_pension_cap_per_taxpayer` | KY | 2/200 | 293 | known_bug | per taxpayer (KRS 141.010(10)), with a joint return's pensions split evenly. | TAXSIM's behavior is evidently a defect from the Fortran source (an unset variable, typo or overwritten result) or a documented correction with a test; statutory side not yet checked against a form |
| `deduct_idaho_itemized_from_2018_per_statute` | ID | 3/200 | 190 | unverified | federal itemized deductions less state income tax. |  |
| `read_north_carolina_child_deduction_per_statute` | NC | 12/200 | 46 | unverified | step schedule, children under 17. |  |
| `credit_iowa_dependents_before_1998` | IA | 17/200 | 31 | known_bug | dependents credited, head of household once. | TAXSIM's behavior is evidently a defect from the Fortran source (an unset variable, typo or overwritten result) or a documented correction with a test; statutory side not yet checked against a form |
| `compute_new_mexico_deductions_per_statute` | NM | 3/200 | 155 | unverified | the existing deduction rules. |  |
| `add_back_iowa_self_employment_tax_twice` | IA | 4/200 | 115 | unverified | published. |  |
| `apply_federal_investment_income_limit_to_wisconsin_eitc_per_statute` | WI | 1/200 | 440 | unverified | the federal limit. |  |
| `count_new_mexico_aged_once_in_rebate` | NM | 12/200 | 37 | known_bug | once. | TAXSIM's behavior is evidently a defect from the Fortran source (an unset variable, typo or overwritten result) or a documented correction with a test; statutory side not yet checked against a form |
| `split_missouri_joint_income_by_earner_only` | MO | 14/200 | 31 | unverified | by earner only. |  |
| `pay_utah_retirement_credit_per_statute` | UT | 2/200 | 216 | unverified | aged taxpayers, plus a Social Security credit. |  |
| `test_arizona_family_credit_before_exemptions` | AZ | 9/200 | 47 | unverified | before exemptions. |  |
| `split_arkansas_joint_income_by_higher_earner` | AR | 7/200 | 59 | unverified | the higher earner's share. |  |
| `use_colorado_itemized_deductions_before_1987` | CO | 4/200 | 91 | known_bug | both deducted. | TAXSIM's behavior is evidently a defect from the Fortran source (an unset variable, typo or overwritten result) or a documented correction with a test; statutory side not yet checked against a form |
| `weight_falling_tables_correctly` | all | 38/2040 | 9 | unverified |  |  |
| `exclude_delaware_pensions_by_statute_from_2024` | DE | 1/200 | 341 | unverified | the earlier routine. |  |
| `compute_maryland_itemized_deduction_from_2018_per_statute` | MD | 1/200 | 340 | unverified | whole state tax. |  |
| `start_oklahoma_joint_top_bracket_per_statute` | OK | 15/200 | 22 | unverified | $14,400. |  |
| `round_up_phaseout_steps` | all | 7/2040 | 46 | unverified | in whole steps. |  |
| `compute_iowa_tax_from_2023_per_statute` | IA | 1/200 | 297 | unverified | published. |  |
| `omit_vermont_student_credit` | VT | 31/200 | 10 | known_bug | none (TAXSIM has no student input). | TAXSIM's behavior is evidently a defect from the Fortran source (an unset variable, typo or overwritten result) or a documented correction with a test; statutory side not yet checked against a form |
| `use_single_standard_deduction_for_new_york_head_of_household_before_1987` | NY | 2/200 | 148 | known_bug | the single amount (no head-of-household status before 1987). | TAXSIM's behavior is evidently a defect from the Fortran source (an unset variable, typo or overwritten result) or a documented correction with a test; statutory side not yet checked against a form |
| `step_kentucky_family_size_credit_by_poverty_share` | KY | 6/200 | 49 | unverified | the step share by multiple of the poverty guideline. |  |
| `use_south_carolina_published_tax_table` | SC | 27/200 | 10 | unverified | the published brackets. |  |
| `add_kansas_aged_and_dependent_standard_deduction_per_statute` | KS | 4/200 | 68 | unverified | aged addition and limit every year, $7,800 joint in 2021. |  |
| `use_ohio_published_tax_brackets` | OH | 15/200 | 18 | unverified | the published brackets. |  |
| `add_connecticut_recapture_per_statute` | CT | 15/200 | 17 | unverified | published. |  |
| `compute_oklahoma_pension_exclusion_per_statute` | OK | 2/200 | 107 | unverified | taxpayers 65 or older with the stated limits. |  |
| `pay_missouri_working_family_credit_by_statute` | MO | 1/200 | 207 | unverified | published. |  |
| `allow_georgia_retirement_exclusion_before_1989_per_statute` | GA | 3/200 | 66 | unverified | $2,000-$4,000. |  |
| `phase_out_connecticut_exemption_per_statute` | CT | 5/200 | 37 | unverified | published. |  |
| `use_connecticut_personal_credit_table_per_statute` | CT | 24/200 | 7 | unverified | published thresholds. |  |
| `compute_minnesota_marriage_credit_earnings_per_statute` | MN | 11/200 | 16 | unverified | published. |  |
| `use_rhode_island_published_brackets` | RI | 1/200 | 165 | unverified | published brackets and steps. |  |
| `subtract_colorado_pensions_for_nonitemizers_before_2021` | CO | 2/200 | 80 | unverified | every aged return. |  |
| `count_arizona_dependent_credit_children_under_17_per_statute` | AZ | 2/200 | 75 | unverified | under 17. |  |
| `compute_montana_retirement_exclusion_per_statute` | MT | 3/200 | 47 | known_bug | taxpayers 65 or older. | TAXSIM's behavior is evidently a defect from the Fortran source (an unset variable, typo or overwritten result) or a documented correction with a test; statutory side not yet checked against a form |
| `use_new_mexico_low_income_rebate_per_statute` | NM | 22/200 | 6 | unverified | the step table. |  |
| `allow_negative_new_york_property_credit_income_per_statute` | NY | 2/200 | 68 | unverified | not floored (not checked against IT-214). |  |
| `phase_out_oregon_federal_tax_limit_in_steps` | OR | 1/200 | 130 | known_bug | the published steps. | TAXSIM's behavior is evidently a defect from the Fortran source (an unset variable, typo or overwritten result) or a documented correction with a test; statutory side not yet checked against a form |
| `exclude_massachusetts_bank_interest_from_2024_per_statute` | MA | 18/200 | 6 | unverified | $100 per taxpayer. |  |
| `end_michigan_senior_percentage_table_at_statutory_row_per_statute` | MI | 3/200 | 35 | unverified | the existing table with no closing row (not checked against the MI-1040CR-7 percentage table). |  |
| `pay_maryland_poverty_level_credit_per_statute` | MD | 2/200 | 51 | known_bug | allowed. | TAXSIM's behavior is evidently a defect from the Fortran source (an unset variable, typo or overwritten result) or a documented correction with a test; statutory side not yet checked against a form |
| `require_indiana_federal_credit_for_decoupled_credit_per_statute` | IN | 3/200 | 31 | unverified | requires the federal credit. |  |
| `test_new_mexico_aged_deduction_on_state_agi` | NM | 2/200 | 43 | unverified | New Mexico AGI. |  |
| `require_federal_age_test_for_new_mexico_working_families_credit_per_statute` | NM | 2/200 | 40 | unverified | the federal credit. |  |
| `give_south_carolina_pension_deduction_to_each_taxpayer` | SC | 2/200 | 37 | unverified | $3,000 per taxpayer. |  |
| `test_oklahoma_earned_income_credit_age_per_statute` | OK | 4/200 | 16 | unverified | as the statute. |  |
| `use_vermont_published_standard_deductions` | VT | 25/200 | 2 | unverified | the published amounts and exclusion options. |  |
| `compute_oklahoma_itemized_deduction_from_2022_per_statute` | OK | 1/200 | 52 | unverified | as the statute. |  |
| `apply_maryland_poverty_level_credit_without_eligibility_worksheet_per_statute` | MD | 1/200 | 51 | unverified | paid whenever income is at most the poverty-line amount (guideline amounts entered for 2022-26 from the 2026 build; the statute's own eligibility test was not c |  |
| `allow_louisiana_federal_credit_share_per_statute` | LA | 2/200 | 25 | known_bug | a tenth of the federal child care and elderly credits. | TAXSIM's behavior is evidently a defect from the Fortran source (an unset variable, typo or overwritten result) or a documented correction with a test; statutory side not yet checked against a form |
| `use_published_kansas_exemption_from_2024` | KS | 30/200 | 2 | unverified | the published amounts. |  |
| `use_wisconsin_form_standard_deduction` | WI | 5/200 | 6 | unverified | the form's schedules. |  |
| `use_idaho_building_fund_limits_per_statute` | ID | 3/200 | 10 | unverified | published. |  |
| `reduce_iowa_single_tax_before_2017` | IA | 1/200 | 22 | unverified | every year. |  |
| `add_back_michigan_self_employment_tax_from_2021_per_statute` | MI | 2/200 | 11 | unverified | added back. |  |
| `subtract_michigan_social_security_with_investment_deduction_per_statute` | MI | 2/200 | 11 | unverified | age 65 or older, with Social Security also subtracted. |  |
| `step_connecticut_pension_deduction_per_statute` | CT | 1/200 | 18 | known_bug | the step schedule. | TAXSIM's behavior is evidently a defect from the Fortran source (an unset variable, typo or overwritten result) or a documented correction with a test; statutory side not yet checked against a form |
| `round_new_york_empire_child_credit_phaseout_per_statute` | NY | 2/200 | 8 | unverified | the excess as is. |  |
| `use_utah_2022_credit_threshold_per_statute` | UT | 15/200 | 1 | unverified | 15,548 and so on. |  |
| `use_published_capital_gains_thresholds` | all | 1/2040 | 14 | known_bug | $459,750 for both (Rev. Proc. 2021-45). | TAXSIM's behavior is evidently a defect from the Fortran source (an unset variable, typo or overwritten result) or a documented correction with a test; statutory side not yet checked against a form |
| `cap_medicare_in_state_payroll_deduction` | all | 1/2040 | 13 | known_bug | stops at the Medicare base (the same base before 1991). | TAXSIM's behavior is evidently a defect from the Fortran source (an unset variable, typo or overwritten result) or a documented correction with a test; statutory side not yet checked against a form |
| `step_alabama_standard_deduction_per_statute` | AL | 3/200 | 4 | unverified | published. |  |
| `use_rhode_island_deduction_phaseout_steps_only` | RI | 1/200 | 12 | known_bug | steps only. | TAXSIM's behavior is evidently a defect from the Fortran source (an unset variable, typo or overwritten result) or a documented correction with a test; statutory side not yet checked against a form |
| `cap_delaware_property_tax_deduction_per_statute` | DE | 1/200 | 12 | unverified | capped. |  |
| `enforce_schedule_se_minimum` | all | 3/2040 | 3 | known_bug | none when 92.35% of net business income is under $400, per spouse ([Schedule SE instructions](https://www.irs.gov/pub/irs-prior/i1040sse--2021.pdf)). | TAXSIM's behavior is evidently a defect from the Fortran source (an unset variable, typo or overwritten result) or a documented correction with a test; statutory side not yet checked against a form |
| `compute_california_2021_earned_income_credit_per_statute` | CA | 1/200 | 9 | unverified |  |  |
| `compute_north_dakota_marriage_credit_earnings_per_statute` | ND | 1/200 | 9 | unverified | wages (pensions from 2022). |  |
| `end_new_york_property_credit_maximum_tables_at_last_row_per_statute` | NY | 1/200 | 9 | unverified | household income as the form defines it; maximum stops at the last row's value (not checked against IT-214). |  |
| `use_vermont_published_brackets` | VT | 4/200 | 2 | unverified | published. |  |
| `use_published_minnesota_2025_aged_addition_per_statute` | MN | 1/200 | 5 | unverified | published amounts. |  |
| `use_arkansas_published_2025_rate_table_per_statute` | AR | 3/200 | 2 | unverified | published table. |  |
| `apply_payroll_wage_bases_correctly` | all | 0/2040 | 0 | known_bug | the base is shared with wages. | TAXSIM's behavior is evidently a defect from the Fortran source (an unset variable, typo or overwritten result) or a documented correction with a test; statutory side not yet checked against a form |
| `count_business_and_professional_inputs_as_income` | all | 0/2040 | 0 | unverified | income. |  |
| `count_nonproperty_income_once` | all | 0/2040 | 0 | unverified | once. |  |
| `index_salt_cap_from_2026` | all | 0/2040 | 0 | unverified | $40,400 / $505,000 and 50%. |  |
| `halve_salt_phaseout_for_separate_returns_per_statute` | all | 0/2040 | 0 | unverified | threshold and range halved. |  |
| `phase_out_amt_exemption_at_half_rate_from_2026` | all | 0/2040 | 0 | unverified | $40,400 / $505,000 and 50%. |  |
| `honor_separate_and_unmarried_codes_with_dependents` | all | 0/2040 | 0 | unverified | separate or single. |  |
| `tax_alaska_before_1979` | all | 0/2040 | 0 | unverified | taxed under Alaska's own schedule. |  |
| `use_vermont_social_security_exemption_starts_per_statute` | VT | 0/200 | 0 | unverified | starts of $45,000 ($60,000 joint), then higher. |  |
| `use_vermont_capital_gain_exclusion_options` | VT | 0/200 | 0 | unverified | the published amounts and exclusion options. |  |
| `pay_pennsylvania_child_care_credit_only_from_2023` | PA | 0/200 | 0 | unverified | from 2023 only. |  |
| `use_published_dependent_standard_deduction_minimum` | all | 0/2040 | 0 | known_bug | the published amounts. | TAXSIM's behavior is evidently a defect from the Fortran source (an unset variable, typo or overwritten result) or a documented correction with a test; statutory side not yet checked against a form |
| `use_wisconsin_statutory_school_credit` | WI | 0/200 | 0 | unverified | the form's rules. |  |
| `use_additional_medicare_separate_threshold` | all | 0/2040 | 0 | known_bug | $125,000. | TAXSIM's behavior is evidently a defect from the Fortran source (an unset variable, typo or overwritten result) or a documented correction with a test; statutory side not yet checked against a form |
| `use_iowa_2020_bracket_inflation_per_statute` | IA | 0/200 | 0 | unverified | 1.666. |  |
| `include_state_taxes_in_iowa_itemized_deduction_from_2020` | IA | 0/200 | 0 | unverified | federal itemized deductions net of the state tax. |  |
| `include_business_and_rental_income_in_california_minimum_tax` | CA | 0/200 | 0 | known_bug | kept, as on Schedule P. | TAXSIM's behavior is evidently a defect from the Fortran source (an unset variable, typo or overwritten result) or a documented correction with a test; statutory side not yet checked against a form |
| `count_pre_1998_nonrefundable_credits_correctly` | all | 0/2040 | 0 | known_bug | the actual credit ( | TAXSIM's behavior is evidently a defect from the Fortran source (an unset variable, typo or overwritten result) or a documented correction with a test; statutory side not yet checked against a form |
| `count_additional_medicare_once_in_alabama_fica_deduction` | AL | 0/200 | 0 | known_bug | counted once. | TAXSIM's behavior is evidently a defect from the Fortran source (an unset variable, typo or overwritten result) or a documented correction with a test; statutory side not yet checked against a form |
| `deduct_half_of_fica_in_alabama_itemized_deduction` | AL | 0/200 | 0 | unverified | half of FICA. |  |
| `deduct_alabama_federal_tax_before_stimulus_credits` | AL | 0/200 | 0 | unverified | before them. |  |
| `use_age_qualified_children_for_dc_eitc` | DC | 0/200 | 0 | known_bug | the age-qualified count | TAXSIM's behavior is evidently a defect from the Fortran source (an unset variable, typo or overwritten result) or a documented correction with a test; statutory side not yet checked against a form |
| `allow_kansas_itemizing_after_2020` | KS | 0/200 | 0 | known_bug | itemizing follows the federal choice in every year (K-40 Schedule A). | TAXSIM's behavior is evidently a defect from the Fortran source (an unset variable, typo or overwritten result) or a documented correction with a test; statutory side not yet checked against a form |
| `deduct_kansas_other_taxes_per_statute` | KS | 0/200 | 0 | unverified | other taxes included. |  |
| `compute_louisiana_tax_per_statute` | LA | 0/200 | 0 | known_bug | the published schedules. | TAXSIM's behavior is evidently a defect from the Fortran source (an unset variable, typo or overwritten result) or a documented correction with a test; statutory side not yet checked against a form |
| `count_wisconsin_rent_at_statutory_share` | WI | 0/200 | 0 | unverified | a quarter. |  |
| `exclude_other_itemized_deductions_from_wisconsin_credit` | WI | 0/200 | 0 | unverified | mortgage interest and charity only. |  |
| `use_new_mexico_working_families_rates_per_statute` | NM | 0/200 | 0 | unverified | 17% in 2019. |  |
| `pay_new_jersey_renter_credit_per_statute` | NJ | 0/200 | 0 | unverified | the published rules (the code before this change). |  |
| `exclude_nonproperty_income_from_new_jersey_income` | NJ | 0/200 | 0 | unverified | the published rules (the code before this change). |  |
| `tax_full_capital_gains_in_new_jersey_before_1987` | NJ | 0/200 | 0 | unverified | the published schedules. |  |
| `limit_nebraska_property_tax_deduction_to_federal_cap` | NE | 0/200 | 0 | unverified | the federal itemized deduction less the capped state tax. |  |
| `use_nebraska_high_income_tables_per_statute` | NE | 0/200 | 0 | known_bug | the published tables. | TAXSIM's behavior is evidently a defect from the Fortran source (an unset variable, typo or overwritten result) or a documented correction with a test; statutory side not yet checked against a form |
| `pay_nebraska_refundable_child_care_per_statute` | NE | 0/200 | 0 | unverified | the federal credit after its limit, 10 points per $1,000. |  |
| `zero_nebraska_standard_deduction_when_federal_itemizes` | NE | 0/200 | 0 | unverified | the standard deduction only when the federal return takes it. |  |
| `exclude_niit_from_louisiana_federal_tax_deduction` | LA | 0/200 | 0 | unverified | not deducted. |  |
| `use_louisiana_child_care_credit_per_statute` | LA | 0/200 | 0 | unverified | Louisiana AGI. |  |
| `apply_kentucky_pension_exclusion_schedule` | KY | 0/200 | 0 | unverified | the published percentage and maximum for the year. |  |
| `limit_kentucky_itemized_deductions_to_mortgage_interest` | KY | 0/200 | 0 | unverified | mortgage interest only. |  |
| `exclude_children_under_4_from_ny_empire_credit` | all | 0/2040 | 0 | known_bug | children under 4 do not qualify before 2023 (Tax Law 606(c-1)); | TAXSIM's behavior is evidently a defect from the Fortran source (an unset variable, typo or overwritten result) or a documented correction with a test; statutory side not yet checked against a form |
| `limit_new_york_property_credit_rent_per_statute` | NY | 0/200 | 0 | unverified | the quarter-of-rent test. |  |
| `round_new_york_recapture_ratio_per_statute` | NY | 0/200 | 0 | unverified | rounded to four decimals (Form IT-201 worksheets). |  |
| `pay_new_york_household_credit_to_separate_returns` | NY | 0/200 | 0 | unverified | the credit, halved. |  |
| `count_children_under_6_by_dep6` | all | 0/2040 | 0 | unverified |  |  |
| `use_idaho_published_brackets` | ID | 0/200 | 0 | unverified | published. |  |
| `initialize_arizona_short_term_loss` | AZ | 0/200 | 0 | known_bug | 25% of gains. | TAXSIM's behavior is evidently a defect from the Fortran source (an unset variable, typo or overwritten result) or a documented correction with a test; statutory side not yet checked against a form |
| `use_delaware_published_tax_brackets` | DE | 0/200 | 0 | unverified | published. |  |
| `use_maine_published_phaseout_starts` | ME | 0/200 | 0 | unverified | published. |  |
| `use_maine_published_2024_sales_tax_credit_per_statute` | ME | 0/200 | 0 | unverified | published schedule. |  |
| `deflate_maine_earned_income_credit_per_statute` | ME | 0/200 | 0 | unverified | n/a. |  |
| `limit_maine_child_care_credit_by_federal_tax_in_2021_per_statute` | ME | 0/200 | 0 | unverified | limited by federal tax. |  |
| `limit_maine_dependent_filer_standard_deduction_per_statute` | ME | 0/200 | 0 | unverified | the federal (limited) amount. |  |
| `count_maine_fairness_credit_children_by_dep17` | ME | 0/200 | 0 | unverified | under 17. |  |
| `deduct_maine_pensions_at_any_age` | ME | 0/200 | 0 | unverified | any age. |  |
| `use_maine_current_deductions_when_itemizing` | ME | 0/200 | 0 | unverified | published. |  |
| `credit_delaware_spouse_low_income_exclusion_to_spouse` | DE | 0/200 | 0 | unverified | added to the spouse's. |  |
| `allow_qbi_deduction_in_south_carolina` | SC | 0/200 | 0 | unverified | allowed. |  |
| `split_virginia_age_deduction_between_spouses_per_statute` | VA | 0/200 | 0 | unverified | each spouse's own income. |  |
| `limit_virginia_2021_child_care_deduction_per_statute` | VA | 0/200 | 0 | unverified | 3,000. |  |
| `add_back_other_itemized_in_california_minimum_tax_per_statute` | CA | 0/200 | 0 | unverified | adds back property tax and other items. |  |
| `use_indiana_elderly_credit_steps_per_statute` | IN | 0/200 | 0 | unverified | step schedule. |  |
| `limit_new_mexico_dependent_filer_standard_deduction_per_statute` | NM | 0/200 | 0 | unverified | the limited amount. |  |
| `leave_out_michigan_schedule_r_income_subtraction_per_statute` | MI | 0/200 | 0 | unverified | not subtracted. |  |
| `deny_delaware_personal_credit_to_dependent_filers_per_statute` | DE | 0/200 | 0 | unverified | split after the exclusion; no credit. |  |
| `tax_kansas_other_nonproperty_income_per_statute` | KS | 0/200 | 0 | unverified | taxed. |  |
| `scale_kansas_zero_tax_income_by_taxpayers_per_statute` | KS | 0/200 | 0 | unverified | the limit applies. |  |
| `reconstruct_connecticut_minimum_tax_income_per_statute` | CT | 0/200 | 0 | unverified | reconstructed from AGI and the separate-return add-back. |  |
| `halve_minnesota_motor_fuels_credit_for_separate_returns_per_statute` | MN | 0/200 | 0 | unverified | halved. |  |
| `use_published_minnesota_dependent_standard_deduction_per_statute` | MN | 0/200 | 0 | unverified | published amounts. |  |
| `add_earnings_to_district_of_columbia_dependent_standard_deduction_per_statute` | DC | 0/200 | 0 | known_bug | earnings plus the federal addition. | TAXSIM's behavior is evidently a defect from the Fortran source (an unset variable, typo or overwritten result) or a documented correction with a test; statutory side not yet checked against a form |
| `apply_illinois_earned_income_credit_age_test_per_statute` | IL | 0/200 | 0 | unverified | the age test. |  |
| `add_idaho_aged_standard_deduction_in_projected_years_per_statute` | ID | 0/200 | 0 | unverified | adds the aged amount. |  |
| `use_north_dakota_published_marriage_credit_limits_per_statute` | ND | 0/200 | 0 | unverified | published amounts. |  |
| `apply_new_york_earned_income_credit_age_test_per_statute` | NY | 0/200 | 0 | unverified | age test and New York AGI. |  |
| `use_new_york_agi_for_earned_income_credit_per_statute` | NY | 0/200 | 0 | unverified | age test and New York AGI. |  |
| `exclude_other_itemized_from_missouri_state_tax_limit_per_statute` | MO | 0/200 | 0 | unverified | excluded. |  |
| `split_missouri_income_before_capital_gains_subtraction_per_statute` | MO | 0/200 | 0 | unverified | before. |  |
| `lift_maryland_childless_refundable_credit_limit_per_statute` | MD | 0/200 | 0 | unverified | no cap from 2023. |  |
| `apply_maryland_earned_income_credit_age_test_per_statute` | MD | 0/200 | 0 | unverified | federal credit with the age test. |  |
| `use_maryland_published_2025_tax_table_per_statute` | MD | 0/200 | 0 | unverified | the published rates. |  |
| `use_california_published_2025_brackets_per_statute` | CA | 0/200 | 0 | known_bug | 371,479 and 505,208. | TAXSIM's behavior is evidently a defect from the Fortran source (an unset variable, typo or overwritten result) or a documented correction with a test; statutory side not yet checked against a form |
| `use_california_2025_minimum_tax_exemption_per_statute` | CA | 0/200 | 0 | unverified | 2025 amounts. |  |
| `start_child_tax_credit_phaseout_at_statutory_threshold_for_dependent_filers` | all | 0/2040 | 0 | unverified | $200,000. |  |
| `count_eitc_children_from_ages_per_statute` | all | 0/2040 | 0 | unverified | counts the children. |  |
| `count_business_inputs_in_earned_income_before_1987_per_statute` | all | 0/2040 | 0 | unverified | not counted / counted. |  |
| `exclude_nonproperty_income_from_maximum_tax_personal_service_income` | all | 0/2040 | 0 | unverified | not counted / counted. |  |
| `start_minnesota_itemized_phaseout_without_status_multiplier_per_statute` | MN | 0/200 | 0 | unverified | the itemized deduction's own start. |  |
| `add_back_minnesota_unemployment_exclusion_to_working_family_income_per_statute` | MN | 0/200 | 0 | unverified | added back in the working family credit's income. |  |
| `exclude_utah_other_itemized_from_state_tax_limit_per_statute` | UT | 0/200 | 0 | unverified | not counted. |  |
| `deflate_wisconsin_exemption_count_per_statute` | WI | 0/200 | 0 | unverified | the actual count. |  |
| `deflate_massachusetts_payroll_tax_deduction_per_statute` | MA | 0/200 | 0 | unverified | n/a. |  |
| `use_massachusetts_2020_part_c_rate_per_statute` | MA | 0/200 | 0 | unverified | 5.0%. |  |
| `use_massachusetts_2001_rent_cap_per_statute` | MA | 0/200 | 0 | unverified | 3,000. |  |
| `halve_new_york_married_itemized_limit_for_separate_per_statute` | NY | 0/200 | 0 | unverified | half the married threshold. |  |
| `compute_new_york_earned_income_credit_base_per_statute` | NY | 0/200 | 0 | unverified | computed on New York income. |  |
| `offset_massachusetts_dividends_only_by_losses_per_statute` | MA | 0/200 | 0 | unverified | no offset against gains. |  |
| `count_maine_fairness_credit_children_before_deflation_per_statute` | ME | 0/200 | 0 | unverified | children. |  |
| `count_arkansas_other_itemized_deductions_per_statute` | AR | 0/200 | 0 | unverified | counted. |  |
| `limit_kansas_child_care_credit_by_federal_tax_per_statute` | KS | 0/200 | 0 | unverified | limited by the federal tax. |  |
| `round_connecticut_property_credit_steps_continuously_per_statute` | CT | 0/200 | 0 | unverified | continuous steps. |  |
| `deduct_alabama_federal_tax_components_per_statute` | AL | 0/200 | 0 | unverified |  |  |
| `cap_michigan_senior_renter_credit_at_statute_amount` | MI | 0/200 | 0 | unverified | reduced; capped at the year's maximum. |  |
| `give_michigan_dependent_filers_exemption_per_statute` | MI | 0/200 | 0 | unverified |  |  |
| `compute_utah_child_tax_credit_per_statute` | UT | 0/200 | 0 | unverified | children under 6, taxable income over $43,000 for singles. |  |
| `limit_rhode_island_child_care_credit_by_federal_tax_per_statute` | RI | 0/200 | 0 | unverified | limited by federal tax. |  |
| `count_arkansas_s_corporation_income_once` | AR | 0/200 | 0 | unverified | once. |  |
| `omit_georgia_itemizer_credit_per_statute` | GA | 0/200 | 0 | unverified | none. |  |
| `apply_iowa_personal_credits_before_amt_per_statute` | IA | 0/200 | 0 | unverified | credits first (not checked against the IA 6251 instructions). |  |
| `add_back_self_employment_loss_to_earned_income_credit_phaseout_per_statute` | all | 0/2040 | 0 | known_bug | part of the loss (50% in 1997, 75% from 1998) is added back. | TAXSIM's behavior is evidently a defect from the Fortran source (an unset variable, typo or overwritten result) or a documented correction with a test; statutory side not yet checked against a form |
| `deflate_delaware_spouse_self_employment_per_statute` | DE | 0/200 | 0 | unverified | all amounts deflated alike. |  |
| `allocate_delaware_credits_freely_between_spouses_per_statute` | DE | 0/200 | 0 | unverified | credits placed where they save the most tax. |  |
| `limit_connecticut_social_security_exclusion_to_base_amount_per_statute` | CT | 0/200 | 0 | unverified | 25% of the lesser of benefits and the base amount (not checked against form CT-1040). |  |
| `leave_pre_1987_earned_income_credit_out_of_oklahoma_property_credit_income_per_statute` | OK | 0/200 | 0 | unverified | no credit before 1987 (federal credit not in the pre-1987 column). |  |
| `read_oklahoma_federal_credit_income_per_statute` | OK | 0/200 | 0 | unverified | scaled by the AGI ratio; investment income reduces the credit dollar for dollar. |  |
| `use_nebraska_published_social_security_limit_per_statute` | NE | 0/200 | 0 | unverified | 49,310 with 60% above it. |  |
| `leave_unreported_spouse_age_out_of_eitc_age_test_per_statute` | all | 0/2040 | 0 | unverified | only a taxpayer under the minimum age disqualifies it, and an unreported spouse age passes. |  |
| `pay_nebraska_child_tax_credit_per_statute` | NE | 0/200 | 0 | known_bug | paid (PolicyEngine amounts). | TAXSIM's behavior is evidently a defect from the Fortran source (an unset variable, typo or overwritten result) or a documented correction with a test; statutory side not yet checked against a form |
| `leave_michigan_business_loss_out_of_household_income_per_statute` | MI | 0/200 | 0 | unverified | not added back (not checked). |  |
| `reduce_michigan_heating_credit_income_by_half_self_employment_tax_per_statute` | MI | 0/200 | 0 | unverified | no deduction (not checked against MI-1040CR-7). |  |
| `test_ohio_senior_credit_on_income_after_business_deduction_per_statute` | OH | 0/200 | 0 | unverified | tested on income after the business deduction (not checked). |  |
| `base_ohio_joint_credit_on_income_after_business_deduction_per_statute` | OH | 0/200 | 0 | unverified | taxable income plus business income (not checked against the IT 1040 instructions). |  |
| `start_maryland_child_tax_credit_phaseout_at_threshold_per_statute` | MD | 0/200 | 0 | unverified | 500 per child, reduced only above 15,000, and the reduction taken once (not checked against Form 502CR). |  |
| `cap_nonrefundable_child_credit_at_three_children_refundable_maximum_per_statute` | all | 0/2040 | 0 | unverified | no cap, earnings formula only (Form 8812 for 2022-23 not re-checked). |  |
| `add_back_half_self_employment_tax_before_iowa_social_security_per_statute` | IA | 0/200 | 0 | unverified | added back (not checked against a form). |  |
| `credit_iowa_dependents_in_full_on_separate_spouse_returns_per_statute` | IA | 0/200 | 0 | unverified | the full credits against the combined tax. |  |
| `split_wisconsin_self_employment_between_spouses_per_statute` | WI | 0/200 | 0 | unverified | half the self-employment total each. |  |
| `leave_massachusetts_part_b_deductions_uncapped_per_statute` | MA | 0/200 | 0 | unverified | income without the input counted twice, the deductions uncapped. |  |
| `measure_massachusetts_circuit_breaker_income_per_statute` | MA | 0/200 | 0 | unverified | income without the input counted twice, the deductions uncapped. |  |
| `exclude_nonproperty_income_from_federal_earned_income_per_statute` | all | 0/2040 | 0 | unverified | not earned income. |  |
| `exclude_maryland_pensions_per_taxpayer_per_statute` | MD | 0/200 | 0 | unverified | $ per taxpayer 65 or older less benefits. |  |
| `apply_colorado_pension_subtraction_by_statute_from_2021` | CO | 0/200 | 0 | unverified | the HB24-1142 rules. |  |
| `count_oregon_eitc_young_children_by_age_per_statute` | OR | 0/200 | 0 | unverified | 12% from 2022 with ages. |  |
| `limit_georgia_exclusion_for_ages_62_to_64_before_2021_per_statute` | GA | 0/200 | 0 | unverified | $35,000 from 2012. |  |
| `cap_virginia_state_tax_deduction_per_statute` | VA | 0/200 | 0 | unverified | $10,000. |  |
| `allow_virginia_standard_deduction_when_itemizing_federally_per_statute` | VA | 0/200 | 0 | unverified | the larger of the two. |  |
| `halve_minnesota_deduction_reduction_for_separate_returns_per_statute` | MN | 0/200 | 0 | unverified | halved thresholds. |  |
| `deflate_oklahoma_earned_income_credit_income_per_statute` | OK | 0/200 | 0 | unverified | deflated income. |  |
| `reinflate_colorado_earned_income_credit_per_statute` | CO | 0/200 | 0 | unverified | reinflated. |  |
| `cap_south_carolina_state_tax_addback_per_statute` | SC | 0/200 | 0 | unverified | federal cap. |  |
| `limit_south_carolina_standard_deduction_addback_to_nonitemizers_per_statute` | SC | 0/200 | 0 | unverified | nonitemizers only. |  |
| `subtract_state_tax_from_south_carolina_itemized_deductions` | SC | 0/200 | 0 | unverified | subtracted. |  |
| `assign_south_carolina_business_income_to_its_earner` | SC | 0/200 | 0 | unverified | by earner. |  |
| `floor_maryland_itemized_deduction_at_zero` | MD | 0/200 | 0 | unverified | published. |  |
| `use_idaho_standard_deduction_2008_2009_per_statute` | ID | 0/200 | 0 | known_bug | the published amount plus the allowance. | TAXSIM's behavior is evidently a defect from the Fortran source (an unset variable, typo or overwritten result) or a documented correction with a test; statutory side not yet checked against a form |
| `compute_missouri_pension_exemption_per_statute` | MO | 0/200 | 0 | unverified | less the state tax. |  |
| `exclude_unemployment_in_district_of_columbia_every_year` | DC | 0/200 | 0 | unverified | all unemployment compensation is excluded in every year. |  |
| `use_mississippi_federal_itemized_deductions_from_2021` | MS | 0/200 | 0 | unverified | federal itemized deductions. |  |
| `count_idaho_child_credit_children_under_17` | ID | 0/200 | 0 | unverified | under 17. |  |
| `count_all_dependents_for_hawaii_child_care` | HI | 0/200 | 0 | unverified | all dependents. |  |
| `tax_hawaii_gains_net_of_federal_exclusion` | HI | 0/200 | 0 | unverified | gains in AGI. |  |
| `phase_out_connecticut_pension_deduction_per_statute` | CT | 0/200 | 0 | unverified | share, phased out above. |  |
| `use_colorado_statutory_high_income_addback` | CO | 0/200 | 0 | unverified | only the state tax that fit under the cap. |  |
| `double_minnesota_separate_returns` | MN | 0/200 | 0 | unverified | doubled and halved. |  |
| `read_minnesota_child_care_from_federal_credit` | MN | 0/200 | 0 | unverified | the federal credit. |  |
| `subtract_minnesota_elderly_and_charity_from_income` | MN | 0/200 | 0 | known_bug | applied. | TAXSIM's behavior is evidently a defect from the Fortran source (an unset variable, typo or overwritten result) or a documented correction with a test; statutory side not yet checked against a form |
| `treat_minnesota_other_itemized_as_state_tax_2018_2019` | MN | 0/200 | 0 | unverified | counted. |  |
| `count_unknown_ages_as_eligible_for_minnesota_childless_credit` | MN | 0/200 | 0 | unverified | unknown ages eligible. |  |
| `reduce_minnesota_deductions_per_statute` | MN | 0/200 | 0 | unverified | published. |  |
| `compute_minnesota_child_care_credit_from_2021_per_statute` | MN | 0/200 | 0 | unverified | published. |  |
| `use_minnesota_2022_working_family_credit_per_statute` | MN | 0/200 | 0 | unverified | the 2022 credit. |  |
| `use_minnesota_published_brackets_from_2022` | MN | 0/200 | 0 | unverified | the published brackets. |  |
| `phase_out_minnesota_exemptions_per_statute` | MN | 0/200 | 0 | unverified | the published start by status. |  |
| `allow_minnesota_personal_exemption_2013_to_2018` | MN | 0/200 | 0 | unverified | paid. |  |
| `limit_missouri_payroll_tax_deduction` | MO | 0/200 | 0 | unverified | capped. |  |
| `use_vermont_nonrefundable_child_care_credit_before_2003` | VT | 0/200 | 0 | unverified | nonrefundable before 2003. |  |
| `pay_vermont_renter_rebate_after_1999` | VT | 0/200 | 0 | unverified | published. |  |
| `use_current_federal_standard_deduction_for_south_carolina_addback` | SC | 0/200 | 0 | unverified | the current one. |  |
| `subtract_new_york_social_security_at_any_age` | NY | 0/200 | 0 | unverified | always subtracted. |  |
| `compute_new_york_supplemental_tax_per_statute` | NY | 0/200 | 0 | unverified | the published supplemental tax. |  |
| `use_new_york_statutory_child_care_credit` | NY | 0/200 | 0 | known_bug | the published percentage of the federal credit. | TAXSIM's behavior is evidently a defect from the Fortran source (an unset variable, typo or overwritten result) or a documented correction with a test; statutory side not yet checked against a form |
| `use_new_york_dependent_standard_deduction_alone` | NY | 0/200 | 0 | unverified | $3,100. |  |
| `interpolate_new_york_household_credit_per_person` | NY | 0/200 | 0 | unverified | the interpolated table. |  |
| `use_oregon_standard_deduction_index_per_statute` | OR | 0/200 | 0 | unverified | $2,155. |  |
| `apply_oregon_dependent_care_credit_per_statute` | OR | 0/200 | 0 | known_bug | that year's table. | TAXSIM's behavior is evidently a defect from the Fortran source (an unset variable, typo or overwritten result) or a documented correction with a test; statutory side not yet checked against a form |
| `count_only_children_under_13_for_oregon_dependent_care` | OR | 0/200 | 0 | known_bug | only children under 13 (ORS 315.264, IRC 21(b)). | TAXSIM's behavior is evidently a defect from the Fortran source (an unset variable, typo or overwritten result) or a documented correction with a test; statutory side not yet checked against a form |
| `apply_nonrefundable_credits_before_1998` | all | 0/2040 | 0 | known_bug | both apply and are reported as the nonrefundable credit total, which supersedes FED-INCOME-001. | TAXSIM's behavior is evidently a defect from the Fortran source (an unset variable, typo or overwritten result) or a documented correction with a test; statutory side not yet checked against a form |
| `exempt_amt_from_itemized_limit_1991_1992` | all | 0/2040 | 0 | known_bug | no add-back; the AMT was exempt from the limit from 1991. | TAXSIM's behavior is evidently a defect from the Fortran source (an unset variable, typo or overwritten result) or a documented correction with a test; statutory side not yet checked against a form |
| `cap_amt_exemption_for_dependents_only` | all | 0/2040 | 0 | known_bug | dependent filers only; IRC 59(j) covers children subject to the kiddie tax. | TAXSIM's behavior is evidently a defect from the Fortran source (an unset variable, typo or overwritten result) or a documented correction with a test; statutory side not yet checked against a form |
| `use_joint_elderly_credit_threshold` | all | 0/2040 | 0 | known_bug | $10,000 for any joint return (IRC 22(d)). | TAXSIM's behavior is evidently a defect from the Fortran source (an unset variable, typo or overwritten result) or a documented correction with a test; statutory side not yet checked against a form |
| `apply_1979_dividend_exclusion` | all | 0/2040 | 0 | known_bug | $100 ($200 joint). The states that add the exclusion back read the same rule ( | TAXSIM's behavior is evidently a defect from the Fortran source (an unset variable, typo or overwritten result) or a documented correction with a test; statutory side not yet checked against a form |
| `exclude_wages_from_1977_preference_income` | all | 0/2040 | 0 | known_bug | neither is a tax-preference item. | TAXSIM's behavior is evidently a defect from the Fortran source (an unset variable, typo or overwritten result) or a documented correction with a test; statutory side not yet checked against a form |
| `deduct_mortgage_interest_from_1983_minimum_tax` | all | 0/2040 | 0 | known_bug | deducted, as the law allowed. | TAXSIM's behavior is evidently a defect from the Fortran source (an unset variable, typo or overwritten result) or a documented correction with a test; statutory side not yet checked against a form |
| `correct_analytic_federal_rate` | all | 0/2040 | 0 | unverified |  |  |
| `read_federal_child_care_credit_before_1987` | all | 0/2040 | 0 | known_bug | uses the 1977-1986 federal credit. | TAXSIM's behavior is evidently a defect from the Fortran source (an unset variable, typo or overwritten result) or a documented correction with a test; statutory side not yet checked against a form |
| `use_current_rates_in_dc_earner_split` | DC | 0/200 | 0 | known_bug | 2007 rates. | TAXSIM's behavior is evidently a defect from the Fortran source (an unset variable, typo or overwritten result) or a documented correction with a test; statutory side not yet checked against a form |
| `use_current_rates_in_dc_low_income_credit` | DC | 0/200 | 0 | known_bug | the year's rates (2000-2017). | TAXSIM's behavior is evidently a defect from the Fortran source (an unset variable, typo or overwritten result) or a documented correction with a test; statutory side not yet checked against a form |
| `apply_iowa_eitc_caps_as_in_2010_law` | IA | 0/200 | 0 | known_bug | caps in 2010 only, $16,590, $38,665 and $43,493 for 0, 1 and 2+ children. | TAXSIM's behavior is evidently a defect from the Fortran source (an unset variable, typo or overwritten result) or a documented correction with a test; statutory side not yet checked against a form |
| `subtract_illinois_property_credit_once_in_eitc_limit` | IL | 0/200 | 0 | known_bug | once. | TAXSIM's behavior is evidently a defect from the Fortran source (an unset variable, typo or overwritten result) or a documented correction with a test; statutory side not yet checked against a form |
| `suspend_kentucky_itemized_limitation_through_2012` | KY | 0/200 | 0 | known_bug | suspended 2010-2012 (the 2011 Schedule A has no limitation). | TAXSIM's behavior is evidently a defect from the Fortran source (an unset variable, typo or overwritten result) or a documented correction with a test; statutory side not yet checked against a form |
| `exclude_separate_returns_from_massachusetts_exemption_carryover` | MA | 0/200 | 0 | known_bug | not on separate returns. | TAXSIM's behavior is evidently a defect from the Fortran source (an unset variable, typo or overwritten result) or a documented correction with a test; statutory side not yet checked against a form |
| `allow_massachusetts_child_care_deduction_2011_2020` | MA | 0/200 | 0 | known_bug | through 2020 (Form 1 line 10b). | TAXSIM's behavior is evidently a defect from the Fortran source (an unset variable, typo or overwritten result) or a documented correction with a test; statutory side not yet checked against a form |
| `pay_minnesota_homemaker_credit_to_nondependents` | MN | 0/200 | 0 | known_bug | filers who are not dependents. | TAXSIM's behavior is evidently a defect from the Fortran source (an unset variable, typo or overwritten result) or a documented correction with a test; statutory side not yet checked against a form |
| `order_minnesota_1998_working_family_credit` | MN | 0/200 | 0 | known_bug | phase-in, plateau and phaseout in order. | TAXSIM's behavior is evidently a defect from the Fortran source (an unset variable, typo or overwritten result) or a documented correction with a test; statutory side not yet checked against a form |
| `use_minnesota_working_family_tables_by_children` | MN | 0/200 | 0 | known_bug | each table's own start. | TAXSIM's behavior is evidently a defect from the Fortran source (an unset variable, typo or overwritten result) or a documented correction with a test; statutory side not yet checked against a form |
| `limit_minnesota_child_care_credit_by_maximum_after_2017` | MN | 0/200 | 0 | known_bug | the lesser of the credit and $600 ($1,200) less 5% of the excess (Minn. Stat. 290.067 subd. 2b). | TAXSIM's behavior is evidently a defect from the Fortran source (an unset variable, typo or overwritten result) or a documented correction with a test; statutory side not yet checked against a form |
| `allow_minnesota_renter_and_homeowner_refunds` | MN | 0/200 | 0 | known_bug | both refunds (Form M1PR). | TAXSIM's behavior is evidently a defect from the Fortran source (an unset variable, typo or overwritten result) or a documented correction with a test; statutory side not yet checked against a form |
| `floor_mississippi_excess_charity` | MS | 0/200 | 0 | known_bug | floored at 0. | TAXSIM's behavior is evidently a defect from the Fortran source (an unset variable, typo or overwritten result) or a documented correction with a test; statutory side not yet checked against a form |
| `subtract_only_taxable_unemployment_in_montana` | MT | 0/200 | 0 | known_bug | the part in federal AGI. | TAXSIM's behavior is evidently a defect from the Fortran source (an unset variable, typo or overwritten result) or a documented correction with a test; statutory side not yet checked against a form |
| `subtract_montana_separate_capital_loss_once` | MT | 0/200 | 0 | known_bug | once. | TAXSIM's behavior is evidently a defect from the Fortran source (an unset variable, typo or overwritten result) or a documented correction with a test; statutory side not yet checked against a form |
| `suspend_montana_itemized_limit_through_2012` | MT | 0/200 | 0 | known_bug | suspended 2010-2012 (the 2011 Form 2 has none). | TAXSIM's behavior is evidently a defect from the Fortran source (an unset variable, typo or overwritten result) or a documented correction with a test; statutory side not yet checked against a form |
| `count_all_children_for_north_carolina_joint_child_care` | NC | 0/200 | 0 | known_bug | all children. | TAXSIM's behavior is evidently a defect from the Fortran source (an unset variable, typo or overwritten result) or a documented correction with a test; statutory side not yet checked against a form |
| `take_north_carolina_child_care_credit_once` | NC | 0/200 | 0 | known_bug | off the larger tax once. | TAXSIM's behavior is evidently a defect from the Fortran source (an unset variable, typo or overwritten result) or a documented correction with a test; statutory side not yet checked against a form |
| `use_north_carolina_married_table_without_splitting` | NC | 0/200 | 0 | known_bug | the married table without splitting. | TAXSIM's behavior is evidently a defect from the Fortran source (an unset variable, typo or overwritten result) or a documented correction with a test; statutory side not yet checked against a form |
| `use_north_carolina_head_of_household_deduction_per_statute` | NC | 0/200 | 0 | unverified | published amounts. |  |
| `cap_north_carolina_property_tax_per_statute` | NC | 0/200 | 0 | unverified | only the $20,000 limit on mortgage interest plus property tax. |  |
| `add_back_north_dakota_state_tax_for_itemizers_only` | ND | 0/200 | 0 | known_bug | only when deducted federally. | TAXSIM's behavior is evidently a defect from the Fortran source (an unset variable, typo or overwritten result) or a documented correction with a test; statutory side not yet checked against a form |
| `add_nebraska_single_additional_tax_once` | NE | 0/200 | 0 | known_bug | once. | TAXSIM's behavior is evidently a defect from the Fortran source (an unset variable, typo or overwritten result) or a documented correction with a test; statutory side not yet checked against a form |
| `set_new_mexico_capital_gains_share_per_statute` | NM | 0/200 | 0 | unverified | 40 percent. |  |
| `compute_new_mexico_capital_gains_deduction_per_statute` | NM | 0/200 | 0 | unverified | never more than the gain, halved on separate returns. |  |
| `compute_maryland_earned_income_credit_per_statute` | MD | 0/200 | 0 | unverified | floor credit. |  |
| `split_maryland_joint_income_per_statute` | MD | 0/200 | 0 | unverified | wages plus half the rest. |  |
| `scale_maryland_child_care_credit_per_statute` | MD | 0/200 | 0 | unverified | 32.5 percent. |  |
| `compute_oklahoma_sales_tax_credit_income_per_statute` | OK | 0/200 | 0 | unverified | household income. |  |
| `read_oklahoma_federal_credit_law_year_per_statute` | OK | 0/200 | 0 | unverified | 2021 federal credit, limited to tax. |  |
| `limit_oklahoma_2021_child_credits_to_tax` | OK | 0/200 | 0 | unverified | 2021 federal credit, limited to tax. |  |
| `pay_wisconsin_child_care_credit_per_statute` | WI | 0/200 | 0 | known_bug | share of the federal credit. | TAXSIM's behavior is evidently a defect from the Fortran source (an unset variable, typo or overwritten result) or a documented correction with a test; statutory side not yet checked against a form |
| `test_indiana_childless_credit_ages_per_statute` | IN | 0/200 | 0 | unverified | floor on the federal credit, ages tested only when reported. |  |
| `apply_indiana_federal_credit_floor_from_2011` | IN | 0/200 | 0 | unverified | floor on the federal credit, ages tested only when reported. |  |
| `use_indiana_decoupled_credit_schedule_in_2021` | IN | 0/200 | 0 | unverified | floor on the federal credit, ages tested only when reported. |  |
| `keep_california_salt_addback_cents_per_statute` | CA | 0/200 | 0 | unverified | with the cents. |  |
| `exclude_utah_state_income_tax_from_taxpayer_credit_before_2018` | UT | 0/200 | 0 | known_bug | other deductions only. | TAXSIM's behavior is evidently a defect from the Fortran source (an unset variable, typo or overwritten result) or a documented correction with a test; statutory side not yet checked against a form |
| `start_utah_dependent_filer_credit_phaseout_per_statute` | UT | 0/200 | 0 | unverified | normal start. |  |
| `use_wisconsin_published_2022_separate_brackets_per_statute` | WI | 0/200 | 0 | unverified | published separate brackets. |  |
| `use_utah_statute_exempt_taxpayer_floor_per_statute` | UT | 0/200 | 0 | unverified | the federal standard deduction. |  |
| `deny_new_york_2021_earned_income_credit_to_separate_returns` | NY | 0/200 | 0 | unverified | by filing status, no credit on separate returns. |  |
| `pick_new_york_worksheet_by_filing_status` | NY | 0/200 | 0 | unverified | by filing status, no credit on separate returns. |  |
| `use_kansas_2021_standard_deduction_per_statute` | KS | 0/200 | 0 | unverified | aged addition and limit every year, $7,800 joint in 2021. |  |
| `credit_maine_1988_standard_deduction_and_exemptions` | ME | 0/200 | 0 | known_bug | credits. | TAXSIM's behavior is evidently a defect from the Fortran source (an unset variable, typo or overwritten result) or a documented correction with a test; statutory side not yet checked against a form |
| `make_maine_elderly_credit_refundable` | ME | 0/200 | 0 | unverified | nonrefundable. |  |
| `halve_west_virginia_separate_brackets_per_statute` | WV | 0/200 | 0 | unverified | separate returns on half the thresholds. |  |
| `limit_rhode_island_retirement_exclusion_to_aged_per_statute` | RI | 0/200 | 0 | unverified | aged taxpayers only. |  |
| `take_arizona_larger_of_property_and_renter_credit` | AZ | 0/200 | 0 | unverified | the larger one. |  |
| `read_west_virginia_family_credit_limits_per_statute` | WV | 0/200 | 0 | known_bug | the published limits. | TAXSIM's behavior is evidently a defect from the Fortran source (an unset variable, typo or overwritten result) or a documented correction with a test; statutory side not yet checked against a form |
| `step_west_virginia_family_credit_phaseout_per_statute` | WV | 0/200 | 0 | unverified | steps. |  |
| `leave_out_west_virginia_child_care_credit_from_2024` | WV | 0/200 | 0 | unverified | none. |  |
| `require_earned_income_for_ohio_joint_credit` | OH | 0/200 | 0 | unverified | earned income. |  |
| `test_ohio_credits_before_business_deduction_per_statute` | OH | 0/200 | 0 | unverified | before it. |  |
| `tax_south_carolina_capital_gains_1985_1989` | SC | 0/200 | 0 | unverified | gains taxed in full. |  |
| `pay_district_of_columbia_property_credit_2014_2018` | DC | 0/200 | 0 | unverified | the credit schedule. |  |
| `use_wisconsin_separate_standard_deduction_from_2023` | WI | 0/200 | 0 | unverified | married separate table, homestead for all who qualify. |  |
| `deduct_district_of_columbia_other_itemized_with_state_taxes` | DC | 0/200 | 0 | unverified | from the inputs. |  |
| `start_wisconsin_head_of_household_third_tier_per_statute` | WI | 0/200 | 0 | unverified | from the first tier. |  |
| `use_own_bounds_in_new_york_worksheets` | NY | 0/200 | 0 | known_bug | each worksheet's own bound. | TAXSIM's behavior is evidently a defect from the Fortran source (an unset variable, typo or overwritten result) or a documented correction with a test; statutory side not yet checked against a form |
| `charge_new_york_unearned_income_tax_1987_1988` | NY | 0/200 | 0 | known_bug | 3% (1987) and 2% (1988) of dividends, interest and gains, phased in from $100,000 of AGI. | TAXSIM's behavior is evidently a defect from the Fortran source (an unset variable, typo or overwritten result) or a documented correction with a test; statutory side not yet checked against a form |
| `charge_new_york_minimum_tax_on_preferences_only` | NY | 0/200 | 0 | known_bug | none without preference items. | TAXSIM's behavior is evidently a defect from the Fortran source (an unset variable, typo or overwritten result) or a documented correction with a test; statutory side not yet checked against a form |
| `floor_new_york_itemized_deductions` | NY | 0/200 | 0 | known_bug | floored at 0. | TAXSIM's behavior is evidently a defect from the Fortran source (an unset variable, typo or overwritten result) or a documented correction with a test; statutory side not yet checked against a form |
| `replace_federal_unemployment_in_oregon_1981_1984` | OR | 0/200 | 0 | known_bug | Oregon's taxable amount replaces it. | TAXSIM's behavior is evidently a defect from the Fortran source (an unset variable, typo or overwritten result) or a documented correction with a test; statutory side not yet checked against a form |
| `pay_oregon_2015_2017_kickers` | OR | 0/200 | 0 | known_bug | 5.6% of tax. | TAXSIM's behavior is evidently a defect from the Fortran source (an unset variable, typo or overwritten result) or a documented correction with a test; statutory side not yet checked against a form |
| `pay_oregon_2019_kicker` | OR | 0/200 | 0 | known_bug | 2019 paid, 2021 not (the published credit for 2021 is not coded). | TAXSIM's behavior is evidently a defect from the Fortran source (an unset variable, typo or overwritten result) or a documented correction with a test; statutory side not yet checked against a form |
| `tax_oregon_dependent_filers_per_statute` | OR | 0/200 | 0 | known_bug | taxed on the single schedule. | TAXSIM's behavior is evidently a defect from the Fortran source (an unset variable, typo or overwritten result) or a documented correction with a test; statutory side not yet checked against a form |
| `omit_oregon_2021_kicker` | OR | 0/200 | 0 | known_bug | 2019 paid, 2021 not (the published credit for 2021 is not coded). | TAXSIM's behavior is evidently a defect from the Fortran source (an unset variable, typo or overwritten result) or a documented correction with a test; statutory side not yet checked against a form |
| `use_net_long_term_gain_in_rhode_island` | RI | 0/200 | 0 | known_bug | net long-term gain only. | TAXSIM's behavior is evidently a defect from the Fortran source (an unset variable, typo or overwritten result) or a documented correction with a test; statutory side not yet checked against a form |
| `halve_rhode_island_separate_standard_deduction` | RI | 0/200 | 0 | known_bug | half. | TAXSIM's behavior is evidently a defect from the Fortran source (an unset variable, typo or overwritten result) or a documented correction with a test; statutory side not yet checked against a form |
| `subtract_rhode_island_tax_once_in_minimum_tax` | RI | 0/200 | 0 | known_bug | once. | TAXSIM's behavior is evidently a defect from the Fortran source (an unset variable, typo or overwritten result) or a documented correction with a test; statutory side not yet checked against a form |
| `deduct_south_carolina_federal_tax_under_1980_law` | SC | 0/200 | 0 | known_bug | tax under 1980 law. | TAXSIM's behavior is evidently a defect from the Fortran source (an unset variable, typo or overwritten result) or a documented correction with a test; statutory side not yet checked against a form |
| `allow_virginia_child_care_deduction_2003` | VA | 0/200 | 0 | known_bug | the $3,000 federal expense limit. | TAXSIM's behavior is evidently a defect from the Fortran source (an unset variable, typo or overwritten result) or a documented correction with a test; statutory side not yet checked against a form |
| `read_virginia_low_income_limits_per_statute` | VA | 0/200 | 0 | unverified | the guidelines and the year's filing threshold. |  |
| `use_north_dakota_married_separate_brackets_from_2023` | ND | 0/200 | 0 | unverified | the married-separate table. |  |
| `use_published_north_dakota_brackets_from_2024` | ND | 0/200 | 0 | unverified | published amounts. |  |
| `allow_unreported_ages_in_dc_childless_credit_from_2024` | DC | 0/200 | 0 | unverified | the age tests as the form has them; capped state tax. |  |
| `cap_district_of_columbia_state_tax_deduction_from_2024` | DC | 0/200 | 0 | unverified | the age tests as the form has them; capped state tax. |  |
| `subtract_minnesota_social_security_without_age_from_2024` | MN | 0/200 | 0 | unverified | the dated rules. |  |
| `charge_minnesota_alternative_minimum_tax_from_2024` | MN | 0/200 | 0 | unverified | the dated rules. |  |
| `limit_idaho_dependent_standard_deduction_per_statute` | ID | 0/200 | 0 | unverified | the larger of the minimum and earned income plus an addition. |  |
| `pay_maine_childless_credit_rate_to_all_ages_per_statute` | ME | 0/200 | 0 | unverified | 25% for every childless filer. |  |
| `take_utah_small_tax_credit_once` | UT | 0/200 | 0 | unverified | once. |  |
| `take_maine_property_fairness_rent_at_statutory_share` | ME | 0/200 | 0 | unverified | 12.75%. |  |
| `limit_virginia_itemized_deductions_without_other_items` | VA | 0/200 | 0 | unverified | published. |  |
| `scale_virginia_itemized_limit_threshold` | VA | 0/200 | 0 | known_bug | in the reduction too. | TAXSIM's behavior is evidently a defect from the Fortran source (an unset variable, typo or overwritten result) or a documented correction with a test; statutory side not yet checked against a form |
| `floor_wisconsin_capital_gain_exclusion` | WI | 0/200 | 0 | known_bug | floored at 0. | TAXSIM's behavior is evidently a defect from the Fortran source (an unset variable, typo or overwritten result) or a documented correction with a test; statutory side not yet checked against a form |
| `start_wisconsin_separate_standard_deduction_tier_at_its_range` | WI | 0/200 | 0 | unverified | from $4,750. |  |
| `limit_wisconsin_capital_loss_to_statutory_amount` | WI | 0/200 | 0 | unverified | $500. |  |
| `limit_wisconsin_dependent_standard_deduction_from_2023` | WI | 0/200 | 0 | unverified | limited to earned income plus an addition. |  |
| `keep_wisconsin_mortgage_interest_cents` | WI | 0/200 | 0 | known_bug | cents kept. | TAXSIM's behavior is evidently a defect from the Fortran source (an unset variable, typo or overwritten result) or a documented correction with a test; statutory side not yet checked against a form |
| `keep_wisconsin_1999_itemized_credit` | WI | 0/200 | 0 | known_bug | kept. | TAXSIM's behavior is evidently a defect from the Fortran source (an unset variable, typo or overwritten result) or a documented correction with a test; statutory side not yet checked against a form |
| `index_wisconsin_joint_brackets` | WI | 0/200 | 0 | known_bug | indexed like single. | TAXSIM's behavior is evidently a defect from the Fortran source (an unset variable, typo or overwritten result) or a documented correction with a test; statutory side not yet checked against a form |
| `base_wisconsin_renter_credit_on_rent` | WI | 0/200 | 0 | known_bug | based on rent. | TAXSIM's behavior is evidently a defect from the Fortran source (an unset variable, typo or overwritten result) or a documented correction with a test; statutory side not yet checked against a form |
| `halve_wisconsin_separate_amt_exemption` | WI | 0/200 | 0 | known_bug | half the exemption ( | TAXSIM's behavior is evidently a defect from the Fortran source (an unset variable, typo or overwritten result) or a documented correction with a test; statutory side not yet checked against a form |
| `add_wisconsin_minimum_tax_once` | WI | 0/200 | 0 | known_bug | half the exemption ( | TAXSIM's behavior is evidently a defect from the Fortran source (an unset variable, typo or overwritten result) or a documented correction with a test; statutory side not yet checked against a form |
| `use_wisconsin_net_household_income_1989_1990` | WI | 0/200 | 0 | known_bug | net income for both. | TAXSIM's behavior is evidently a defect from the Fortran source (an unset variable, typo or overwritten result) or a documented correction with a test; statutory side not yet checked against a form |
| `credit_wisconsin_1983_homestead_excess` | WI | 0/200 | 0 | known_bug | 80%. | TAXSIM's behavior is evidently a defect from the Fortran source (an unset variable, typo or overwritten result) or a documented correction with a test; statutory side not yet checked against a form |
| `report_base_state_worksheet` | all | 0/2040 | 0 | known_bug | drops the $1 and reports the base calculation. | TAXSIM's behavior is evidently a defect from the Fortran source (an unset variable, typo or overwritten result) or a documented correction with a test; statutory side not yet checked against a form |
| `report_social_security_taxable_earnings` | all | 0/2040 | 0 | known_bug | reports both earners' wages plus 92.35% of net business income, each capped at the wage base. | TAXSIM's behavior is evidently a defect from the Fortran source (an unset variable, typo or overwritten result) or a documented correction with a test; statutory side not yet checked against a form |

## Statutory mode keeps TAXSIM's behavior

| Switch | Why |
|:--|:--|
| `double_kentucky_standard_deduction_for_joint_returns` | Kentucky Form 740 gives one standard deduction amount by year that does not vary by filing status; no source found that a joint return takes two, so TAXSIM's one-per-return was not shown wrong. Statutory mode now uses it (switch kept in KEEP_TAXSIM_BEHAVIOR); 2026-11-14 |
| `exempt_pennsylvania_pensions_from_age_65_per_statute` | Pennsylvania exempts retirement distributions from the plan's retirement age (REV-636) with no fixed age and no per-person rule the inputs can apply, so TAXSIM's reading (a couple's pension to the spouse 55 or older, exempt from 60) is a defensible implementation and was not shown wrong. Statutory mode now uses it; the switch was removed; 2026-11-14 |
