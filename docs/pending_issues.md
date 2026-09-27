# Pending issues

Status as of 2026-09-27.

Line numbers refer to `taxsim_2024_09_21.f`, and "the executable" means `taxsim2024.exe`.

## TAXSIM errors not yet on the design doc's errors tab

These still need to be added to the "Apparent TAXSIM errors" tab. The docs connector wasn't available when they were found.

### California, 1979-1986: unemployment removed twice
- **Where:** lines 2419-2439.
- **What TAXSIM does:** total income (`totinc`) never includes unemployment compensation. The adjustments (`adj`) still subtract taxable unemployment (`data(22)+comnew(78)`), so it is taken out of California AGI a second time. Someone with $10,000 of wages and $4,000 of taxable unemployment gets a California AGI of $6,000.
- **Presumably meant:** either include unemployment in total income, or skip the subtraction.
- **Port:** copies it.

### California, 1987 onward: minimum tax excludes business and rental income
- **Where:** lines 2761-2762.
- **What TAXSIM does:** the income subject to California's minimum tax (`alminy`) subtracts positive self-employment income (`data(17)`) and Schedule E rent and S-corp income (`comnew(8)`). California's actual minimum-tax base (Schedule P) keeps that income.
- **Example (2004):** a couple with $83,000 of pensions, $1,000 of rent and $99,997 of property tax. Each dollar of rent lowers their California tax by 7 cents.
- **Port:** copies it.

### Arizona, 1998 onward: family income credit limit for five or more dependents
- **Where:** line 1139 (`fag98h(itwn(nchild,1,4))`).
- **What TAXSIM does:** the lookup caps the dependent count at 4. The fifth head-of-household limit in the data table (line 909), $26,575 for five or more dependents under A.R.S. 43-1073, is never read, so those filers get $25,200 instead.
- **Confirmed by probe:** a 2010 head of household with 5 dependents and $26,000 of wages gets no credit from the executable. The law allows $240.
- **Port:** uses the statute.

### Indiana, 2009-2010: federal results for large families
- **Where:** lines 6037-6044.
- **What TAXSIM does:** to cap Indiana's earned income credit at two children, it reruns the federal calculation (`nlaw`) with dependents set to 2. It restores the dependent count but not the federal results that rerun overwrote. Families with 3 or more dependents get a reported federal tax, and every later step that reads it, computed as if they had 2.
- **Port:** reports the correct federal tax. `scripts/compare_cps.py` counts these units as a known difference.

### Federal, before 1998: nonrefundable credit total (`comnew(58)`)
- **Where:** found by probing the executable. The credit total is set to tax before credits only when the elderly credit exceeds tax; otherwise it is 0, even when the credit applies. It never reduces `fiitax`.
- **Effect:** states that read this total get the wrong federal tax base. These include LA, UT, OR, ND, AL and AZ; for example, Utah's federal tax deduction.
- **Port:** copies it, in the `nonrefundable_credits` column.

### The executable ignores reported ages under 65
- It pays the no-child EITC to filers under the minimum age: 25, or 19 in 2021.
- It doesn't cap young filers' AMT exemption.
- This may already be on the tab; check before adding it.
- **Port:** uses the reported ages.

## Open work

### Negative capital gains (deferred by the user)
Several calculators assume capital gains aren't negative, but TAXSIM accepts losses. Three test cases show the known failures:
- a couple with a $3,000 loss and wages;
- a single filer with an $8,000 loss;
- a $12,000 long-term gain with a $4,000 short-term loss.

The states that fail:

| State | Failing years |
|---|---|
| Alabama | every year (the $8,000 loss), and 1977-86 (the couple) |
| California | 1977-86 |
| Connecticut | 1977-91 (short-term loss) |
| Illinois | 1977-86 (short-term loss) |
| Hawaii | 1977-86 (short-term loss) |

- Arkansas is fixed. The three cases currently run only for Arkansas (`tests/ar_cases.py`); move them back into `tests/state_new_inputs.py` once the other states are fixed.
- `engine/state.py::federal_capital_gain_in_agi` gives the federal gain included in AGI (TAXSIM `comnew(6)`), and the fixes can reuse it. Several states still rebuild that amount on their own lines (for example `nm.py` and `ma.py`).

### Remaining CPS ASEC differences
Taxes now match on the 2005 and 2011 files, except for the known age errors above. After the sales-tax floor and Virginia below-filing-minimum AGI fixes, only a small number of state worksheet rows still differ even though the tax matches:

| Column | Differing units per file | Main states |
|---|---|---|
| any remaining worksheet column, after itemizing/standard ties | 40 in `cps_2005`, 44 in `cps_2011` | scattered |

Rerun with `uv run scripts/compare_cps.py <cps dir> --out mismatches.parquet`. Pass
`--tax-year YEAR` to use the CPS households and incomes as realistic test inputs
under a different year's tax law.

A cross-year sweep using the 2011 CPS households now covers tax years 1977,
1978, 1981, 1986, 1987, 1991, 1994, 1998, 2000, 2001, 2005, 2009, 2010,
2012, 2014, and 2017-2021. It found and fixed three additional formula gaps:

- Arkansas's 1998-2002 Working Taxpayer Credit now includes combined
  self-employment income (`data(17)`) in its earnings base.
- Maryland's 2012+ top exemption-phaseout tier now removes the aged exemption
  along with the personal exemption, as TAXSIM does.
- California's Young Child Tax Credit now starts in 2019. It had accidentally
  shared the California EITC's 2015 start-year gate, understating 2017 state
  tax for 311 CPS units and 2018 state tax for 347 CPS units by $1,000 per
  qualifying young child.

The comparator also classifies the existing Minnesota 2019+ head-of-household
standard-deduction and New Jersey real top-bracket differences as deliberate
law-over-TAXSIM choices. Representative actual-law runs after those changes had
35 remaining rows in 1994, 65 in 2000, and 39 in 2020 after known differences
and itemizing/standard ties. The newly added years leave 39 rows in 1998, 47
in 2001, 41 in 2005, 47 in 2009, 51 in 2012, 52 in 2014, 51 in 2017, 37 in
2018, and 45 in 2019. Of those, respectively 1, 21, 2, 3, 8, 13, 17, 17, and
17 rows have a tax difference; the rest are worksheet-only. In 2020, 16 of
39 remaining rows have a tax difference.

The main outlier is 1981: 1,681 of 1,734 remaining rows are worksheet-only,
almost all in `federal_alternative_minimum_taxable_income` (TAXSIM `v26`).
Only 53 rows have a tax difference. Keep that early minimum-tax detail-field
cluster separate from liability mismatches when investigating it.

### TAXSIM options (deferred)
The `opt1/opt1v/opt2/opt2v` inputs are accepted but ignored. If revisited, implement only the 8 documented switches.

### Law note: AMT exemption for young filers
Our cap follows TAXSIM's source, which applies it to anyone under the age limit. The real kiddie-tax rule is narrower: it applies only to children with unearned income who are claimed as dependents.

### Uncommitted work
- Everything in this repo is uncommitted, including your chunked-worker changes in `api.py`.
- Outside this repo, `survey_kit_data/src/survey_kit_data/census/cps_asec.py` (line 278) opens the data dictionary with `encoding='latin-1'`. That fix is also uncommitted.
