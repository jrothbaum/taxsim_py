# Outputs

The result is your input columns, unchanged and in order, followed by these.

## Default columns

| Column | Meaning |
|:--|:--|
| `fiitax` | Federal income tax liability, after credits and including capital gains rates, AMT and surtaxes. Can be negative with refundable credits. |
| `siitax` | State income tax liability. |
| `fica` | Payroll tax: Social Security and Medicare, employee plus employer, plus self-employment tax. |
| `tfica` | The taxpayer's own share of `fica`. |
| `ficar` | Payroll tax rate (percent). |
| `addmed` | Additional Medicare tax. |
| `agi` | Federal adjusted gross income. |
| `taxable_income` | Federal taxable income. |
| `taxable_unemployment` | Unemployment compensation included in federal AGI. |
| `earned_income` | Earned income. |
| `regular_tax` | Federal regular income tax, before credits and AMT. |

## With `mtr`

| Column | Meaning |
|:--|:--|
| `frate` | Federal marginal rate, percent, with respect to the chosen income. |
| `srate` | State marginal rate, percent. |

## With `idtl=2`

Descriptive names; `taxsim_names=True` swaps in the label in the right-hand column.

**Federal**

| Column | TAXSIM |
|:--|:--|
| `federal_adjusted_gross_income` | `v10` |
| `federal_taxable_unemployment_compensation` | `v11` |
| `federal_taxable_social_security_benefits` | `v12` |
| `federal_standard_deduction` | `v13` |
| `federal_personal_exemptions` | `v14` |
| `federal_exemption_phaseout` | `v15` |
| `federal_itemized_deduction_phaseout` | `v16` |
| `federal_itemized_deductions` | `v17` |
| `federal_taxable_income` | `v18` |
| `federal_income_tax_before_credits` | `v19` |
| `federal_exemption_surtax` | `v20` |
| `federal_general_tax_credit` | `v21` |
| `federal_child_tax_credit` | `v22` |
| `federal_additional_child_tax_credit` | `v23` |
| `federal_child_and_dependent_care_credit` | `v24` |
| `federal_earned_income_tax_credit` | `v25` |
| `federal_alternative_minimum_taxable_income` | `v26` |
| `federal_alternative_minimum_tax` | `v27` |
| `federal_regular_income_tax` | `v28` |
| `federal_payroll_tax` | `v29` |
| `federal_social_security_taxable_earnings` | `v42` |
| `federal_net_investment_income_tax` | `v43` |
| `federal_additional_medicare_tax` | `v44` |
| `federal_recovery_rebate_credit` | `v45` |
| `federal_nonrefundable_credits` | `credits` |

**State**

| Column | TAXSIM |
|:--|:--|
| `state_household_income` | `v30` |
| `state_rent_paid` | `v31` |
| `state_adjusted_gross_income` | `v32` |
| `state_exemptions` | `v33` |
| `state_standard_deduction` | `v34` |
| `state_itemized_deductions` | `v35` |
| `state_taxable_income` | `v36` |
| `state_property_tax_credit` | `v37` |
| `state_child_and_dependent_care_credit` | `v38` |
| `state_earned_income_tax_credit` | `v39` |
| `state_total_credits` | `v40` |
| `state_marginal_rate_percent` | `v41` |
| `state_tax_before_credits` | `staxbc` |

States that do not use a line report 0 for it, as TAXSIM does.
