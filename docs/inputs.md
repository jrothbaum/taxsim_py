# Inputs

The inputs are TAXSIM's, with the same names, meanings and units. Dollar amounts are
annual and in nominal dollars of the tax year. Anything absent or null counts as 0,
except where noted. Extra columns in your data are carried through to the result
untouched.

## Required

| Column | Meaning |
|:--|:--|
| `year` | Tax year, 1960-2025. Not needed if you pass `year=` to `calculate_taxes`. |
| `state` | TAXSIM state code 1-51 (alphabetical, with DC as 9), or 0 for no state tax. With `state_id_type="fips"`, Census FIPS codes. |
| `mstat` | Filing status: `1` single (head of household if `depx > 0`), `2` joint, `6` married filing separately, `8` dependent filer. The older codes `3`, `33` and `66` are also accepted. |

## Household

| Column | Meaning |
|:--|:--|
| `taxsimid` | Your case identifier. Optional and carried through. |
| `page`, `sage` | Age of the taxpayer and spouse (0 if none). Used for age-65 and similar provisions. |
| `depx` | Number of dependents. |
| `dep13`, `dep17`, `dep18` | Dependents under 13, 17 and 18 (child care, child tax credit and similar). Default to `depx`. |
| `dep6`, `dep19` | Dependents under 6 and under 19 (or students). |
| `age1`, `age2`, `age3` | Ages of up to three dependents. If any is present, the age counts above come from these. |
| `children_under_3`, `children_under_4`, `children_under_7` | Optional extensions for survey data. Not part of TAXSIM's inputs; default to 0. |

## Income

| Column | Meaning |
|:--|:--|
| `pwages`, `swages` | Wages of the taxpayer and spouse. |
| `psemp`, `ssemp` | Self-employment income of the taxpayer and spouse. Gets no qualified business income deduction; use `pbusinc`/`pprofinc` for that. |
| `dividends`, `intrec` | Dividends and interest received. |
| `stcg`, `ltcg` | Short- and long-term capital gains. |
| `otherprop` | Other property income, subject to the net investment income tax. |
| `nonprop` | Other non-property income, such as alimony received. |
| `pensions` | Taxable pensions and IRA distributions. |
| `gssi` | Gross Social Security benefits. |
| `pui`, `sui` | Unemployment compensation of the taxpayer and spouse. `ui` is the combined older form. |
| `transfers` | Non-taxable transfers, which count for state benefits tests. |
| `scorp` | S-corporation income. |
| `pbusinc`, `sbusinc` | Qualified business income of the taxpayer and spouse. |
| `pprofinc`, `sprofinc` | Qualified business income from specified service trades. |

## Deductions and expenses

| Column | Meaning |
|:--|:--|
| `rentpaid` | Rent paid (for state renter credits). |
| `proptax` | Real estate taxes paid. |
| `otheritem` | Other itemized deductions, such as state and local taxes, medical expenses and charity. |
| `childcare` | Child care expenses. |
| `mortgage` | Mortgage interest and other deductions that are not subject to the usual limits. |

## Calculation controls

| Column | Meaning |
|:--|:--|
| `mtr` | Marginal-rate code per row (see [Calculating](calculate.md#marginal-rates-mtr)). |
| `idtl` | `2` for the detailed worksheets on that row. |

!!! note
    Two conventions to know: `psemp`/`ssemp` get no qualified business income deduction
    (use `pbusinc`/`pprofinc`), and `pensions` is treated as the kind of pension each
    state exempts.
