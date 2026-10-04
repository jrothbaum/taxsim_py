# Comparison with PolicyEngine-US

`taxsim_py` in `statutory` mode is compared with PolicyEngine-US's TAXSIM API
(`scripts/compare_independent.py`, `src/taxsim_py/validation/independent.py`)
for the federal return and all 45 income-tax states, tax years 2022-2025. It is
a diagnostic, not an assertion that PolicyEngine is the oracle: where the two
disagree, the form or statute decides, and the cause is listed below.

## Method

- **Synthetic grid.** Both models see identical, explicit inputs: single and
  joint filers, income from $0 to $1.5M, 0-2 children with explicit ages,
  age-65+ households (pension-only, Social Security-only, interest-only),
  itemizers and renters. Rows where federal tax differs by more than $1 are
  dropped so only state logic is compared; a row is "off" when state tax
  differs by more than $1.
- **CPS ASEC samples** (`scripts/compare_cps.py`, `compare_independent.py`)
  give a realistic income mix but are noisier, because PolicyEngine's adapter
  ignores `dep6` and turns `dep13`/`dep17` counts into children of default
  ages, so credits keyed to a child's age are compared against the wrong child.
- The PolicyEngine run disables state and local tax deductions
  (`disable_salt=True`) and one-time rebates are removed with a twin
  simulation.

## Result

The federal return matches on every grid row. Most states are exact on the
grid in all four years; the rest differ only for the reasons below.

## Remaining differences, and why

| State | Difference | Cause (who is right) |
|:--|:--|:--|
| DE, HI | Port itemizes at middle incomes | TAXSIM imputes a sales-tax itemized deduction; PolicyEngine's run has none. The gap is the deduction times the rate (larger in 2025 with the $40,000 SALT cap). |
| Federal | Self-employment income (`psemp`) gets no 20% business deduction | TAXSIM applies the qualified-business-income deduction only to the business-income inputs (`pbusinc`, `pprofinc`); PolicyEngine also applies it to self-employment income. The port keeps TAXSIM's convention. |
| Federal | Social Security tax when `transfers` is present | TAXSIM counts `transfers` as tax-exempt interest in the Social Security provisional-income test; the independent models do not treat ordinary transfers that way. The port keeps TAXSIM's convention. |
| Federal | `nonprop` | TAXSIM includes it as income; mapping it to Tax-Calculator alimony can exclude it after 2018. |
| ID | Exactly $10 | The port includes the Permanent Building Fund tax (2024 Form 40, line 32); PolicyEngine omits it. |
| CO | Rebate amounts | PolicyEngine includes the TABOR cash-back rebate; one-time payments are deliberately left out. |
| ME, OR | Rebate amounts | Maine affordability payment and Oregon kicker are one-time payments, left out. Maine rent is assumed to exclude utilities. |
| MO, MT | Pension-only rows | PolicyEngine treats TAXSIM's `pensions` as a private pension; the port follows TAXSIM's convention (the single input is the kind each state exempts). |
| KS | Food sales tax credit | Refundable in the statute; PolicyEngine treats it as nonrefundable. The port follows the statute. |
| VA | Low-income credit plus EITC | PolicyEngine pays both; the 2023 Form 760 instructions allow one. The port follows the form. |
| NY | $16-$33 child credit | PolicyEngine uses the current federal child credit; Form IT-213 uses 2017 federal rules, which the port follows. |
| NJ | Pension exclusion at $125,000-$150,000 | PolicyEngine applies the phase-down to the pension; the statute applies it to the maximum exclusion. |
| IN | EITC, one joint row | PolicyEngine pays 10% of the current federal credit but gates eligibility on frozen 2023 rules. |
| WA | Capital gains excise tax | Not modelled: TAXSIM's `ltcg` cannot separate the exempt real-estate and retirement gains. The Working Families credit is modelled and exact. |
| MN, MA, VT, WI | Renter / homeowner refund tables | PolicyEngine differs or lacks the inputs; the Minnesota tables are unvalidated. |
| KS, MS | High-income itemizers | Small residuals, not traced. |
| AR | Low-income tables, $1.6 recapture detail, joint return with one earner at about $30,000 | PolicyEngine applies the main schedule where one spouse has all the income; the port applies the joint low-income table. |
| CA | Joint at $600,000 with children ($91-$280); single childless at $250,000 ($6) | Exemption-credit phase-out details, not traced. |
| CT | Rows on the $1,000-or-part exemption phase-out boundary | TAXSIM adds $0.001 to AGI. |
| MD, ME, IL | $180,000 and above | Small residuals. |

Provisions TAXSIM has no input for are not modelled in either direction:
deductions for tips, overtime and car-loan interest (2025), 529-plan and
military-retirement items, county and local rates, and foster-youth or
residency eligibility.

## Federal 2025 (P.L. 119-21)

Child credit $2,200; senior deduction ($6,000 per person 65+, reduced 6% of
income over $75,000 / $150,000, not for married-separate); SALT cap $40,000,
reduced 30% of AGI over $500,000 to a $10,000 floor. States that start from
federal income either conform or add these back (ID, IA, MT, AZ, SC; ME and DC
keep the pre-2025 standard deduction).

## Sources

State parameters come from PolicyEngine-US's parameter tree, checked against
the state forms and instructions where the two were unclear. Corrections
that differ from TAXSIM are recorded in
[Statutory corrections](statutory_corrections.md).
