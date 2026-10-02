# PolicyEngine state parameter audit

Generated from the installed `policyengine-us` package using its
`system.parameters` tree. The date status columns are `2022/2023/2024`.

## Interpretation

A PolicyEngine parameter tree is useful evidence and a source for current
law, but it is not a direct replacement for this project's state YAML.
The state calculators encode TAXSIM-era definitions, deductions, credits,
phaseouts, and filing-status rules. A parameter can only be imported when
the existing calculator's formula means the same thing. Newer law may
require a calculator change, not just a new YAML value.

The `ok` entries mean that PolicyEngine can return a dated value at that
node. They do not prove that the value matches the project's parameter
definition or official state forms.

## State-by-state inventory

| TAXSIM | State | Local YAML | PolicyEngine income children | Candidate paths | Review |
|---:|:---:|:---|:---|:---|:---|
| 1 | AL | 20 top-level keys | agi, deductions, exemptions, rates | rates [ok/ok/ok], deductions.standard [ok/ok/ok], exemptions [ok/ok/ok] | Manual mapping |
| 2 | AK | 8 top-level keys | no income subtree | none | Special/manual |
| 3 | AZ | 66 top-level keys | credits, deductions, exemptions, main, rebate, subtractions | main [ok/ok/ok], deductions.standard [ok/ok/ok], exemptions [ok/ok/ok], credits [ok/ok/ok] | Manual mapping |
| 4 | AR | 33 top-level keys | credits, deductions, exemptions, gross_income, rates | rates [ok/ok/ok], deductions.standard [ok/ok/ok], exemptions [ok/ok/ok], credits [ok/ok/ok] | Manual mapping |
| 5 | CA | 89 top-level keys | agi, amt, credits, deductions, exemptions, mental_health_services, rates, use_tax | rates [ok/ok/ok], deductions.standard [ok/ok/ok], exemptions [ok/ok/ok], credits [ok/ok/ok] | Manual mapping |
| 6 | CO | 45 top-level keys | additions, amt, credits, rate, subtractions | rate [ok/ok/ok], credits [ok/ok/ok] | Manual mapping |
| 7 | CT | 77 top-level keys | add_back, additions, agi, credits, exemptions, rates, rebate, recapture, subtractions | rates [ok/ok/ok], exemptions [ok/ok/ok], credits [ok/ok/ok] | Manual mapping |
| 8 | DE | 45 top-level keys | credits, deductions, rate, subtractions | rate [ok/ok/ok], deductions.standard [ok/ok/ok], credits [ok/ok/ok] | Manual mapping |
| 9 | DC | 64 top-level keys | additions, credits, deductions, joint_separately_option, rates, snap, subtractions | rates [ok/ok/ok], deductions.standard [ok/ok/ok], credits [ok/ok/ok] | Manual mapping |
| 11 | GA | 36 top-level keys | additions, agi, credits, deductions, exemptions, main, subtractions | main [ok/ok/ok], deductions.standard [ok/ok/ok], exemptions [ok/ok/ok], credits [ok/ok/ok] | Manual mapping |
| 12 | HI | 70 top-level keys | additions, alternative_tax, credits, deductions, exemptions, rates, subtractions | rates [ok/ok/ok], deductions.standard [ok/ok/ok], exemptions [ok/ok/ok], credits [ok/ok/ok] | Manual mapping |
| 13 | ID | 48 top-level keys | credits, deductions, main, other_taxes, subtractions | main [ok/ok/ok], credits [ok/ok/ok] | Manual mapping |
| 14 | IL | 6 top-level keys | base, credits, exemption, rate, subtractions, use_tax | rate [ok/ok/ok], exemption [ok/ok/ok], credits [ok/ok/ok] | Manual mapping |
| 15 | IN | 47 top-level keys | agi_rate, county_rates, credits, deductions, exemptions | exemptions [ok/ok/ok], credits [ok/ok/ok] | Manual mapping |
| 16 | IA | 48 top-level keys | alternate_tax, alternative_minimum_tax, credits, deductions, gross_income, income_adjustments, married_filing_separately_on_same_return, modified_income, pension_exclusion, rates, reportable_social_security, subtractions, tax_exempt, tax_reduction, taxable_income | rates [ok/ok/ok], deductions.standard [ok/ok/ok], credits [ok/ok/ok] | Manual mapping |
| 17 | KS | 68 top-level keys | agi, credits, deductions, exemptions, rates | rates [ok/ok/ok], deductions.standard [ok/ok/ok], exemptions [ok/ok/ok], credits [ok/ok/ok] | Manual mapping |
| 18 | KY | 34 top-level keys | credits, deductions, exclusions, rate, subtractions | rate [ok/ok/ok], deductions.standard [ok/ok/ok], credits [ok/ok/ok] | Manual mapping |
| 19 | LA | 38 top-level keys | credits, deductions, exempt_income, exemptions, main | main [ok/ok/ok], deductions.standard [ok/ok/ok], exemptions [ok/ok/ok], credits [ok/ok/ok] | Manual mapping |
| 20 | ME | 97 top-level keys | agi, credits, deductions, main, surcharge | main [ok/ok/ok], deductions.standard [ok/ok/ok], credits [ok/ok/ok] | Manual mapping |
| 21 | MD | 58 top-level keys | agi, capital_gains, credits, deductions, exemptions, rates | rates [ok/ok/ok], deductions.standard [ok/ok/ok], exemptions [ok/ok/ok], credits [ok/ok/ok] | Manual mapping |
| 22 | MA | 44 top-level keys | ald, capital_gains, credits, deductions, exempt_status, exemptions, rates | rates [ok/ok/ok], exemptions [ok/ok/ok], credits [ok/ok/ok] | Manual mapping |
| 23 | MI | 37 top-level keys | additions, credits, deductions, exemptions, household_resources, rate, senior_age, subtractions | rate [ok/ok/ok], deductions.standard [ok/ok/ok], exemptions [ok/ok/ok], credits [ok/ok/ok] | Manual mapping |
| 24 | MN | 124 top-level keys | additions, amt, credits, deductions, exemptions, niit, rates, subtractions | rates [ok/ok/ok], deductions.standard [ok/ok/ok], exemptions [ok/ok/ok], credits [ok/ok/ok] | Manual mapping |
| 25 | MS | 20 top-level keys | adjustments, credits, deductions, exemptions, income_sources, rate | rate [ok/ok/ok], deductions.standard [ok/ok/ok], exemptions [ok/ok/ok], credits [ok/ok/ok] | Manual mapping |
| 26 | MO | 37 top-level keys | credits, deductions, exemptions, minimum_taxable_income, rates, subtractions | rates [ok/ok/ok], exemptions [ok/ok/ok], credits [ok/ok/ok] | Manual mapping |
| 27 | MT | 45 top-level keys | additions, credits, deductions, exemptions, main, married_filing_separately_on_same_return_allowed, social_security, subtractions | main [ok/ok/ok], deductions.standard [ok/ok/ok], exemptions [ok/ok/ok], credits [ok/ok/ok] | Manual mapping |
| 28 | NE | 32 top-level keys | agi, credits, deductions, exemptions, rates | rates [ok/ok/ok], deductions.standard [ok/ok/ok], exemptions [ok/ok/ok], credits [ok/ok/ok] | Manual mapping |
| 30 | NH | 3 top-level keys | exemptions, in_effect, rate | rate [ok/ok/ok], exemptions [ok/ok/ok] | Manual mapping |
| 31 | NJ | 31 top-level keys | additions, all_exclusions, credits, deductions, exclusions, exemptions, filing_threshold, gross_income, main, subtractions | main [ok/ok/ok], exemptions [ok/ok/ok], credits [ok/ok/ok] | Manual mapping |
| 32 | NM | 31 top-level keys | credits, deductions, exemptions, main, modified_gross_income, other_deductions_and_exemptions, rebates | main [ok/ok/ok], exemptions [ok/ok/ok], credits [ok/ok/ok] | Manual mapping |
| 33 | NY | 70 top-level keys | agi, college_tuition, credits, deductions, exemptions, main, supplemental | main [ok/ok/ok], deductions.standard [ok/ok/ok], exemptions [ok/ok/ok], credits [ok/ok/ok] | Manual mapping |
| 34 | NC | 43 top-level keys | credits, deductions, rate | rate [ok/ok/ok], deductions.standard [ok/ok/ok], credits [ok/ok/ok] | Manual mapping |
| 35 | ND | 27 top-level keys | credits, rates, taxable_income | rates [ok/ok/ok], credits [ok/ok/ok] | Manual mapping |
| 36 | OH | 34 top-level keys | additions, agi_threshold, credits, deductions, exemptions, rates | rates [ok/ok/ok], exemptions [ok/ok/ok], credits [ok/ok/ok] | Manual mapping |
| 37 | OK | 27 top-level keys | agi, credits, deductions, exemptions, rates | rates [ok/ok/ok], deductions.standard [ok/ok/ok], exemptions [ok/ok/ok], credits [ok/ok/ok] | Manual mapping |
| 38 | OR | 52 top-level keys | credits, deductions, rates, subtractions | rates [ok/ok/ok], deductions.standard [ok/ok/ok], credits [ok/ok/ok] | Manual mapping |
| 39 | PA | 3 top-level keys | credits, deductions, forgiveness, nontaxable_income_sources, nontaxable_retirement_distribution_sources, rate, retirement_age_threshold | rate [ok/ok/ok], credits [ok/ok/ok] | Manual mapping |
| 40 | RI | 39 top-level keys | agi, credits, deductions, exemption, high_earner_tax, rate | rate [ok/ok/ok], deductions.standard [ok/ok/ok], exemption [ok/ok/ok], credits [ok/ok/ok] | Manual mapping |
| 41 | SC | 38 top-level keys | additions, credits, deductions, exemptions, rates, subtractions | rates [ok/ok/ok], exemptions [ok/ok/ok], credits [ok/ok/ok] | Manual mapping |
| 43 | TN | 4 top-level keys | no income subtree | none | Special/manual |
| 45 | UT | 26 top-level keys | credits, rate | rate [ok/ok/ok], credits [ok/ok/ok] | Manual mapping |
| 46 | VT | 36 top-level keys | agi, child_care_contributions, credits, deductions, exemption, rates | rates [ok/ok/ok], deductions.standard [ok/ok/ok], exemption [ok/ok/ok], credits [ok/ok/ok] | Manual mapping |
| 47 | VA | 40 top-level keys | credits, deductions, exemptions, filing_requirement, rates, rebate, subtractions | rates [ok/ok/ok], deductions.standard [ok/ok/ok], exemptions [ok/ok/ok], credits [ok/ok/ok] | Manual mapping |
| 49 | WV | 18 top-level keys | credits, exemptions, rates, subtractions | rates [ok/ok/ok], exemptions [ok/ok/ok], credits [ok/ok/ok] | Manual mapping |
| 50 | WI | 46 top-level keys | additions, credits, deductions, exemption, rates, subtractions | rates [ok/ok/ok], deductions.standard [ok/ok/ok], exemption [ok/ok/ok], credits [ok/ok/ok] | Manual mapping |

## Immediate issues to resolve

1. `state_cpi_extrapolation.yaml` stops at 2023, so mixed-state runs
   for 2024 currently fail before a PolicyEngine comparison can run.
2. Bracket and rate paths are not uniform. For example, California
   uses `income.rates`, New York uses `income.main`, and Pennsylvania
   uses a flat `income.rate`. There is no safe bulk path-to-YAML copy.
3. Several project calculators use federal or TAXSIM-specific inputs
   alongside state parameters. PolicyEngine values for credits,
   exemptions, retirement income, and itemized deductions need a
   formula-level comparison before adoption.
4. A PolicyEngine value can represent a newer statutory rule than the
   current calculator. We should preserve the source path and verify
   against the state's instructions/forms for each changed rule.
5. States with no project calculator or no income tax should not be
   populated merely because PolicyEngine has a parameter subtree.

## Recommended import order

Start with states whose existing model is structurally simple and whose
PolicyEngine path has the same meaning: flat-rate states and ordinary
bracket schedules. Add dated YAML values with a source comment, then
compare realistic CPS units and hand-built form cases. Leave credits,
retirement exclusions, local taxes, and special phaseouts for a second
pass with official forms as the independent check.
