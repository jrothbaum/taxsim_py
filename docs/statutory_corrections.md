# TAXSIM errors and corrections

This page lists the apparent errors in NBER TAXSIM that turned up while porting it,
what taxsim-py does about each one, and the other deliberate differences from the
compiled program. Each error was judged from the Fortran source and from tests
against the compiled program. None has been confirmed with TAXSIM's authors.

Line numbers refer to `taxsim_2024_09_21.f`, and "the executable" means
`taxsim2024.exe`.

The **taxsim-py** column says how each error is handled:

- **Statutory**: `calculation_mode="statutory"`, the default, follows the law, and
  `calculation_mode="taxsim"` reproduces TAXSIM for replication and oracle tests.
  Each correction has an ID and a switch, named in the row, in
  `src/taxsim_py/behavior.py`; formula code reads that profile, never the mode
  string. Tests are rows of `tests/statutory_correction_table.py`, run by
  `tests/test_statutory_corrections.py` (federal payroll tax:
  `tests/test_calculation_modes.py`). Years after 2021 run only in statutory mode
  for states, so a fix confined to those years needs no switch.
- **Both modes**: TAXSIM's number is wrong or out of date for its year, so the
  published value is used in both modes, as for parameter updates (new years,
  dated tables). `scripts/validate_states.py` counts these state years as expected
  differences (`KNOWN_ORACLE_DIVERGENT_STATE_YEARS`); `scripts/validate_federal.py`
  marks the federal cases `oracle_divergent`.
- **Projection**: the error is in TAXSIM's projection of 2021 law to later years.
  Taxsim mode keeps it; statutory mode uses actual law for those years.
- **No effect** and **Not changed**: the error cannot change a result, or the right
  rule needs information TAXSIM has no input for.

Every entry needs a reason, an implementation reference, and tests for both
behaviors. Differences that are not TAXSIM errors (extensions and actual law for
2022 on), how the executable processes a file, and entries found not to be errors
are listed at the end.

## Federal

### Payroll tax

The first four came from a 10,000-unit 2021 CPS sample, where together they explain
all 145 payroll differences from Tax-Calculator. Statutory mode matches
Tax-Calculator to the cent on the focused records and leaves 3 residuals
(Tax-Calculator applies the $400 threshold to combined spousal income; the port
applies it per spouse, as the Schedule SE instructions require). Code:
`engine.payroll_tax.taxsim_payroll`; tests: the `single_high_se`, `wage_se_cap`,
`below_minimum` and `losses` records.

| Years | Line | What TAXSIM does | taxsim-py |
|:--|:--|:--|:--|
| 2013 on | | Additional Medicare tax counts combined self-employment income in both earners' totals: $200,000 of self-employment income gives $1,524.60 | Statutory, FED-PAYROLL-001: each taxpayer's non-negative net earnings counted once ($184,700, no tax) ([Form 8959 instructions](https://www.irs.gov/pub/irs-prior/i8959--2021.pdf)). `count_self_employment_once_for_additional_medicare` |
| All | | The 92.35% factor is applied to the whole running total, including W-2 wages, which creates extra wage-base room | Statutory, FED-PAYROLL-002: each item enters the OASDI cap at its own factor (100% wages, 92.35% self-employment) ([Pub. 334](https://www.irs.gov/publications/p334)). `coordinate_oasdi_on_net_earnings` |
| All | | Self-employment tax is charged from the first dollar | Statutory, FED-PAYROLL-003: none when 92.35% of net business income is under $400, per spouse ([Schedule SE instructions](https://www.irs.gov/pub/irs-prior/i1040sse--2021.pdf)). `enforce_schedule_se_minimum` |
| All | 22300-22330 | A business loss gives negative self-employment tax, below wage FICA or below zero: $50,000 of wages and a $10,000 loss gives $6,237 instead of $7,650 | Statutory, FED-PAYROLL-004: business results netted per taxpayer, floored at zero, allocated over positive items; a loss does not reduce the Additional Medicare base (Schedule SE, Form 8959). `prevent_negative_self_employment_tax` |
| All | 22254 | The payroll lookup gives the primary earner both non-professional business incomes and the spouse both professional ones, so the wage base and the QBI netting use the wrong person's income ($50,000 of spouse business income over a primary earner already above the 2019 base gets no OASDI tax). South Carolina (2018 on) and Virginia (2000 on) split spouses' business income the same way | Statutory, FED-PAYROLL-005: each earner's own business and professional income, in the federal calculation and in the SC and VA splits (Schedule SE). `assign_business_income_to_its_earner` |
| 1977-1993 | `sstax` | Self-employment Medicare tax is capped at the Medicare wage base by itself, whatever the wages (a taxpayer with $30,000 of wages and $50,000 of self-employment income in 1980 pays Medicare tax on $25,900 of it, though the wages already use the whole base) | Statutory, FED-PAYROLL-008: the base is shared with wages. `apply_payroll_wage_bases_correctly` |
| All | 182 | Detailed output `v42` (`ssearn`, earnings subject to the Social Security wage base) is never assigned, so it is always 0 | Statutory, FED-PAYROLL-006: reports both earners' wages plus 92.35% of net business income, each capped at the wage base. `report_social_security_taxable_earnings` |

### Income tax and credits

| Years | Line | What TAXSIM does | taxsim-py |
|:--|:--|:--|:--|
| 1977-1978 | 23742 | Itemizers' preference income for the maximum tax on earned income includes wages and unemployment compensation (Maryland reads this figure too) | Statutory, FED-INCOME-014: neither is a tax-preference item. `exclude_wages_from_1977_preference_income` |
| 1977-1986 | 23926 | The credit for the elderly is capped at `data(32)`, an input that is never filled, so it is always $0 | Statutory, FED-INCOME-012: 15% of $2,500 ($3,750 for a couple both 65 or older; from 1984 $5,000 and $7,500) less nontaxable Social Security and half of AGI over $7,500 ($10,000 joint) (IRC 37 as amended in 1976; IRC 22 from 1984). `apply_elderly_credit_before_1987` |
| Before 1987 | law79, law60 | Business, professional and S corporation income are left out of income tax (payroll tax still charges them) | Statutory, FED-INCOME-010: taxed. `include_business_income_before_1987` |
| 1979 | 23398 | The dividend exclusion is set to `data(13)`, never filled, so it is $0 | Statutory, FED-INCOME-011: $100 ($200 joint). The states that add the exclusion back read the same rule (`engine.state.federal_dividend_exclusion_limit`). `apply_1979_dividend_exclusion` |
| 1983-1986 | 24106 | The minimum-tax base leaves out qualified housing interest | Statutory, FED-INCOME-013: deducted, as the law allowed. `deduct_mortgage_interest_from_1983_minimum_tax` |
| 1987-1999 | 25563-25564 | The AMT is computed every year but added to tax only from 2000 | Statutory, FED-INCOME-004: AMT owed. `add_amt_before_2000` |
| 1987-1997 | | The child and dependent care credit and the credit for the elderly are computed but never subtracted (the code sits inside the 1998 child tax credit block) | Statutory, FED-INCOME-005: both apply and are reported as the nonrefundable credit total, which supersedes FED-INCOME-001. `apply_nonrefundable_credits_before_1998` |
| 1987-1997 | 25883-25910 | The nonrefundable credit total that states read (`comnew(58)`) is tax before credits when the elderly credit exceeds it, otherwise 0. A 68-year-old with $8,000 of wages in 1995 has a $712.50 credit and is reported $97.50, so states that read it (LA, UT, OR, ND, AL, AZ) get the wrong federal base. Found by probing the executable | Statutory, FED-INCOME-001: the actual credit (`federal_elder`). `count_pre_1998_nonrefundable_credits_correctly`; code: `federal._net_tax` |
| 1990 on | 25312-25324 | The AMT exemption cap for young filers (under 14; 18 in 2006-2007; 24 in 2008-2017) applies to anyone that age | Statutory, FED-INCOME-008: dependent filers only; IRC 59(j) covers children subject to the kiddie tax. `cap_amt_exemption_for_dependents_only` |
| 1991-1992 | 25257 | AMT income adds back the itemized-deduction limit, as if the limit applied to the AMT. A 1991 single itemizer with $150,000 of wages has $1,500 more AMT income | Statutory, FED-INCOME-007: no add-back; the AMT was exempt from the limit from 1991. `exempt_amt_from_itemized_limit_1991_1992` |
| All | 25662 | The elderly credit's AGI threshold is $7,500 for a joint return with one spouse 65 or older | Statutory, FED-INCOME-006: $10,000 for any joint return (IRC 22(d)). `use_joint_elderly_credit_threshold` |
| All | 24561 | Social Security provisional income adds `transfers` (welfare, workers' compensation), which the input reader stores where tax-exempt interest used to be (`data(41)`) | Statutory, FED-INCOME-003: adds nothing; the law adds tax-exempt interest, which TAXSIM has no input for. The same applies to the Iowa (2014 on) and Montana worksheets. `exclude_transfers_from_provisional_income` |
| 1998-2000 | 25768-25778 | The additional child tax credit for 3 or more children uses a payroll figure that is never set, so it is always $0 (and detailed output v42 is always 0) | Statutory, FED-INCOME-009: the unused credit up to the employee share of payroll tax (and half of self-employment tax) less the EITC (IRC 24(d) as enacted by P.L. 105-34). `allow_additional_child_credit_before_2001` |
| 2021 | | The EITC is zeroed for married filing separately, although a code comment says they qualify | Not changed: the law requires living apart for six months, which TAXSIM has no input for |
| 2022 on | 25714 | The Credit for Other Dependents is computed (`500*max(0, data(8)-ideps)`) but never added to the credit total, so adult and age-17 dependents get nothing | Statutory, FED-INCOME-002: $500 per dependent who is not a CTC-qualifying child, with the CTC phaseout (IRC 24(h)(4)-(5)); about 100 CPS units per year. `allow_other_dependent_credit_after_2021`; code: `federal._credits` |
| 2022 | 27549 | The regular-tax 20% capital gains threshold for single filers is $459,650; the AMT copy uses $459,750 | Statutory, FED-INCOME-015: $459,750 for both (Rev. Proc. 2021-45). `use_published_capital_gains_thresholds` |
| 2022 on | 27945 | The childless EITC minimum age stays at 2021's 19 | Fixed in the 2026 build: 25 in both modes |
| 2023 | | The EITC uses the 2022 table scaled by inflation (the year cap `lawend` was left at 2022) | Fixed in the 2026 build: the published 2023 table in both modes |
| 2023 | | EITC investment-income limit $10,300; Social Security wage base $153,600; head-of-household AMT exemption left at 2022 | Fixed in the 2026 build: $11,000; $160,200; the 2023 amount in both modes |
| 2023 | | The additional standard deduction for age ($1,750 / $1,400) and the young-filer AMT limit ($8,200) repeat 2022 | Fixed in the 2026 build: $1,850 / $1,500 and $8,800 in both modes |
| 1995-1996, 2022-2023 | | Dependent standard deduction minimum $600 (law $650) in 1995-1996 and stuck at $1,100 for 2022-2023 (law $1,150 and $1,250); the 2008-2009 and earned-income-addition errors are fixed in the 2026 build | Statutory, FED-INCOME-016: the published amounts. `use_published_dependent_standard_deduction_minimum` |
| 2024 on | | The executable refuses to run past 2023 (a hard year check and the `xndxa(1981:2023)` array bound) | taxsim-py uses actual law through 2025 and raises an error after |
| All | 27940-27952 | The executable ignores reported ages under 65: it pays the childless EITC below the minimum age ($529 for a 17-year-old with $8,000 of wages in 2019) and never caps young filers' AMT exemption. The source applies both | taxsim-py uses the reported ages |

### Federal marginal rate that states read

Some states before 2001 (Nebraska, North Dakota, Rhode Island and Vermont report it
as their bracket rate) use TAXSIM's analytic federal marginal rate (`comnew(72)`).
These errors affect only that rate, not federal tax. Statutory mode corrects each,
FED-RATE-001 (`correct_analytic_federal_rate`), except the Social Security term:
the branch TAXSIM always takes there is the correct formula, and the alternative it
skips would count the EITC slope twice.

| Years | Line | What TAXSIM does |
|:--|:--|:--|
| 1977-1978 | 23705-23724, 23874 | The alternative capital gains rate is 0 for moderate gains, so Nebraska 1977 reports 0 for such couples |
| 1979-1982 | 24100-24135 | The minimum-tax rate is used only if the zero-bracket amount is 0, which never happens; 1982 nets to minus 60% of the minimum-tax rate for some itemizers |
| 1981 | 23628-23632 | The 1.25% rate-reduction credit is overwritten on the next line, so it reaches the tax but not the rate |
| 1993-1996 | tax93, tax94 | The rate ignores the 28% cap on gains (1993) or uses income less gains even when that is not the cheaper method (1994-1996) |
| 1994 on | 25925-25937 | The Social Security term compares a percent with a fraction, so the alternative formula never runs (not changed, see above) |
| 1998 on | 25733, 25758, 25972 | The child tax credit adds `.05`, a fraction, to a rate in percent: +0.05 points |
| All | 25946 | The elderly-credit term subtracts a percent from 50 inside a fraction |
| All | 27860-27880 | The EITC slope is set before the disqualification tests, so it stays in the rate when no credit is paid |

## States

### Rules shared by several states

| States and years | Line | What TAXSIM does | taxsim-py |
|:--|:--|:--|:--|
| DC 1982-1986, IA 1977-1981 and 1986, NY 1977-1986 | DC 4476-4479, IA 6443-6448, NY 15000-15001 | The child care credit is a share of the federal credit, which TAXSIM's early federal routine never fills in, so it is $0 | Statutory, STATE-CCC-001: uses the 1977-1986 federal credit. `read_federal_child_care_credit_before_1987` |
| Household income (MI and others), all years | near 21275 | Household income (`data(159)`) leaves out self-employment income, then subtracts half the self-employment tax | Statutory, STATE-HHY-001: includes it. The 2026 build includes it too (all of it, no half-tax subtraction), so `include_self_employment_in_household_income` is in `FIXED_IN_2026_BUILD` |
| Household income and state detail, all years | 21324, 21336-21400 | Every record after the first has $1 more household income (this matters for Michigan's Home Heating Credit), and with `idtl=2` the state worksheet is printed from the one-cent marginal-rate run | Statutory, STATE-HHY-002: drops the $1 and reports the base calculation. `drop_household_income_record_adjustment`, `report_base_state_worksheet` |
| KS, MO, VT; MO 1977-1993 | 231-280 | The payroll tax these states deduct (`socsec`) charges 1.45% on wages above the Social Security base in every year (about $296 too much on $60,000 in Missouri 1985) | Statutory, STATE-SOCSEC-001: stops at the Medicare base (the same base before 1991). `cap_medicare_in_state_payroll_deduction` |
| SC 2018 on, VA 2000 on | | The spouse split assigns business and professional income to the wrong spouse | Statutory, FED-PAYROLL-005 (see [payroll tax](#payroll-tax)) |
| Several, 2022-2023 | 59-71 | For projected years the dispatcher deflates federal slots 1-98 but passes higher slots undeflated and skips 26, 65, 72 and 73. Massachusetts' payroll deduction, Kentucky's child care credit, self-employment tax, and federal total income in Maine, West Virginia and Indiana mix 2021 and current dollars | Projection |

### Each state

| State | Years | Line | What TAXSIM does | taxsim-py |
|:--|:--|:--|:--|:--|
| AL | 2013 on | | (2024 build only; the 2026 build deducts the taxpayer's own payroll taxes, below) The payroll-tax itemized deduction takes half of FICA and then adds the employee-only Additional Medicare tax, which TAXSIM's FICA total already includes, counting it 1.5 times | Statutory, AL-001: counted once. `count_additional_medicare_once_in_alabama_fica_deduction` |
| AL | 1982 on | | The itemized payroll tax deduction is the taxpayer's own payroll taxes (`c(190)`), not half of the reported total | Statutory: half of FICA. `deduct_half_of_fica_in_alabama_itemized_deduction` |
| AL | 1977-1981 | | The itemized deduction includes other itemized deductions | Both modes |
| AL | 2009-2010, 2020 | | The federal tax deduction is net of the Making Work Pay credit (2009-2010) and the recovery rebate (2020) | Statutory: before them. `deduct_alabama_federal_tax_before_stimulus_credits` |
| AL | 2007 on | | The dependent exemption falls from $1,000 to $500 over the first AGI range, then to nothing over the second, then $300 | Statutory: published steps. `step_alabama_dependent_exemption_per_statute` |
| AL | 2007 on | | The standard deduction falls linearly between two incomes (a separate return halves the single table; joint returns carry a $4,001 minimum through 2021) | Statutory: published. `step_alabama_standard_deduction_per_statute` |
| AL | 2022 | | The retirement income exemption starts in 2023 | Statutory: 2022. `exempt_alabama_retirement_income_in_2022` |
| AR | 2004 | | A chain of year tests checks 2002 twice, so 2004 uses the 1977 thresholds ($3,000, $6,000, $9,000, $15,000, $25,000). $20,000 of taxable income gives $780.00 instead of $721.50 (before the 3% surcharge) | Both modes: $3,400, $6,800, $10,300, $17,100, $28,500 ([DFA 2004 Indexed Tax Brackets](https://dfa.arkansas.gov/wp-content/uploads/2004brackets.pdf)) |
| AZ | 1998 on | 1139 | The family income credit caps dependents at 4, so the $26,575 limit for five or more (A.R.S. 43-1073) is never used. A 2010 head of household with 5 dependents and $26,000 of wages loses $240 | Both modes: the statute's limits |
| AZ | All | | The senior property tax credit's income test includes Social Security (which TAXSIM's household income includes), and the credit is not limited to tax paid | Statutory, AZ-001: Social Security excluded and the credit limited to property tax paid (A.R.S. 43-1072). `limit_arizona_property_credit_per_statute` |
| CA | 1979-1986 | | Taxable unemployment is never in total income but is subtracted again in the AGI adjustment: $30,000 of wages and $4,000 of unemployment gives AGI $26,000 | Statutory, CA-001: not subtracted. `include_unemployment_in_california_total_income` |
| CA | 1987 on | | The minimum-tax base (`alminy`) subtracts positive self-employment and Schedule E income (a 2004 couple's rent lowers their tax by 7 cents a dollar) | Statutory, CA-002: kept, as on Schedule P. `include_business_and_rental_income_in_california_minimum_tax` |
| CA | 2001, 2018 | | AMT exemptions: 2001 head of household $45,692 and married separate $30,461 repeat 2000; 2018 married separate $44,685 | Both modes: Schedule P (540) line 22, $48,114 and $32,075 (2001), $47,685 (2018) |
| CA | 2018 on | | The childless CalEITC is paid at any age (a 15-year-old with $5,000 of wages gets $208.66) | Statutory, CA-005: from 18, with no maximum age (R&TC 17052); an unreported age passes. `apply_state_childless_eitc_minimum_age` (shared with NJ-001); code: `states.ca` |
| CA | 2018 on | | Itemized deductions start from the federal SALT-capped total | Statutory, CA-006: California never adopted the cap (Schedule CA (540)); real estate and other taxes are deductible in full, its own income tax not at all. `ignore_federal_salt_cap_in_california` |
| CA | 2019 on | 2911-2919 | The 2024 build computed the Young Child Tax Credit from earnings without testing for a child under 6 (the 2026 build tests for one; the per-child multiplication may remain) | Statutory, CA-004: requires one (R&TC 17052.1) and pays one credit per return. `require_young_child_for_california_yctc`, `pay_california_yctc_per_young_child` |
| CO | 2022 on | | Pensions and Social Security are subtracted only when someone is 65 or older, under one combined cap | Statutory, CO-001: each taxpayer subtracts pension income up to $20,000 (55-64) or $24,000 (65+), less their Social Security subtraction; those 65+ subtract all taxable Social Security, those 55-64 within the cap (all of it from 2025 when federal AGI is at most $75,000, $95,000 joint; HB24-1142). Pension and Social Security inputs are split equally between spouses (C.R.S. 39-22-104(4)(f)-(g), DR 0104AD) |
| DC | 2000 on | dctax | The dependent-filer low-income credit's table chain tests the 2000 table first, so every year uses the 2000 rates | Statutory, DC-005: the year's rates (2000-2017). `use_current_rates_in_dc_low_income_credit` |
| DC | 2007 | | The married-couple split uses the 2006 rates | Statutory, DC-004: 2007 rates. `use_current_rates_in_dc_earner_split` |
| DC | 2015 on | | The EITC child track counts any dependent (`depx > 0`), not age-qualified children; the childless investment-income test counts only interest and dividends | Statutory, DC-002: the age-qualified count `dep18` (`use_age_qualified_children_for_dc_eitc`). DC-003: positive capital gains count too (D-40 EITC checklist) (`count_capital_gains_in_dc_childless_eitc_investment_income`) |
| HI | All | | The pension subtraction reads `data(72)`, which the input reader never sets (pensions are `data(20)`), so pensions are never excluded | Statutory, HI-003: excludes `pensions`. `exclude_hawaii_pensions` |
| HI | through 2022 | | General, renter, excise, food, low-income and child care credits are not paid beyond the tax (only from 2023) | Statutory: refundable. `pay_hawaii_credits_below_zero_tax` |
| HI | all | | Child care counts the dependents under 13 (the build's `d(207)`), not all dependents | Statutory: all dependents. `count_all_dependents_for_hawaii_child_care` |
| HI | 2011-2012 | | The limit on the state tax deduction starts in 2013 | Statutory: 2011. `limit_hawaii_state_tax_deduction_from_2011` |
| HI | 2009-2015 | | The personal exemption falls continuously with AGI | Statutory: in whole steps. `round_up_phaseout_steps` |
| HI | all | | The alternative capital gains tax uses the long-term gain input before any federal exclusion | Statutory: gains in AGI. `tax_hawaii_gains_net_of_federal_exclusion` |
| HI | 2018 on | | Itemized deductions add back state and local taxes over the federal cap | Both modes |
| IA | 2010 | | The 2010 joint caps (the pre-2009 federal limits) are tested against one more child than intended. The 2024 build also capped the credit in every year 1990-2021; the 2026 build caps 2010 joint returns only | Statutory, IA-001: caps in 2010 only, $16,590, $38,665 and $43,493 for 0, 1 and 2+ children. `apply_iowa_eitc_caps_as_in_2010_law` |
| IL | 2000-2002 | | The EITC limit subtracts the property tax credit twice | Statutory, IL-002: once. `subtract_illinois_property_credit_once_in_eitc_limit` |
| IL | 2017 on | | The exemption is allowed at any income | Statutory, IL-001: none above $250,000 ($500,000 joint) (35 ILCS 5/204(g)). `apply_illinois_exemption_income_limit` |
| IN | 2009-2010 | 6037-6044 | To cap the EITC at two children, TAXSIM reruns the federal calculation with 2 dependents and keeps the result, so families with 3 or more get federal tax as if they had 2 | Both modes: the correct federal tax; `compare_cps.py` counts these as a known difference |
| KS | 1995-1996 | kstax | Operator precedence lets 1996 skip the homestead income limit | No effect: the refund's reduction schedule already reaches $0 at the limit |
| KS | 2021 on | | Itemizing is no longer allowed | Statutory, KS-001: itemizing follows the federal choice in every year (K-40 Schedule A). `allow_kansas_itemizing_after_2020` |
| KS | all | | Every credit stops at the tax (homestead refund, child care, food refund, earned income credit); only the earned income credit's excess over the tax after child care is paid, and only from 2013 | Statutory: refundable credits as published. `pay_kansas_credits_per_statute` |
| KY | 1995 on | | The pension exclusion cap applies per return | Statutory, KY-001: per taxpayer (KRS 141.010(10)), with a joint return's pensions split evenly. `apply_kentucky_pension_cap_per_taxpayer` |
| KY | 2011-2012 | | The test `law.eq.2010.and.law.le.2012` suspends the itemized-deduction limitation only in 2010 | Statutory, KY-002: suspended 2010-2012 (the 2011 Schedule A has no limitation). `suspend_kentucky_itemized_limitation_through_2012` |
| MA | 1985 on | 8374 | `mst.ne.3.or.mst.ne.6` is always true, so unused exemptions carry over to unearned income for every filer, including separate returns | Statutory, MA-003: not on separate returns. `exclude_separate_returns_from_massachusetts_exemption_carryover` |
| MA | 2011-2020 | 8473 | The test `law.eq.2010.and.law.le.2020` gives the child care expense deduction only in 2010 | Statutory, MA-002: through 2020 (Form 1 line 10b). `allow_massachusetts_child_care_deduction_2011_2020` |
| MA | All | 8448 | The spouse's payroll deduction reads Social Security benefits (`comnew(84)`) instead of the spouse's payroll tax (`comnew(184)`), so only the primary earner's payroll tax is deducted, capped at $2,000 for the return | Statutory, MA-001: each spouse deducts their own employee payroll tax up to $2,000 (Form 1 lines 11a/11b), so a couple earning $50,000 and $75,000 gets the second $2,000 ($100 of tax at 5%). `deduct_massachusetts_employee_payroll_tax_per_spouse` |
| MD | 2019-2021 | | The standard deduction's minimum and maximum amounts fill the array in the wrong order: single comes out $1,500-$1,550 for 2019 instead of $1,500-$2,250 | Both modes: Resident Booklet, Instruction 16: single $1,500-$2,250, $1,550-$2,300, $1,550-$2,350; joint $3,050-$4,550, $3,100-$4,650, $3,100-$4,700 |
| ME | 2022-2023 | | The exemption count is deflated and truncated, so most small households lose the property tax and sales tax fairness credits | Projection |
| MI | 2013 on | mitax | The senior deduction is $20,000 per taxpayer 65 or older against all income, whatever their retirement income | Statutory, MI-001 (2013-2022): tiers by the older spouse's birth year; before 1946 the pension and investment subtractions; 1946-1952 up to $20,000 ($40,000 joint) of pensions, against all income from 67; later, from 67 less exemptions. `apply_michigan_retirement_tiers` |
| MI | 2022-2023 | | The Home Heating Credit uses undeflated income against 2021 dollar amounts | Projection |
| MN | 1978-1984 | 9892 | The $50 homemaker credit is zeroed unless the filer is someone's dependent (the test looks reversed) | Statutory, MN-002: filers who are not dependents. `pay_minnesota_homemaker_credit_to_nondependents` |
| MN | 1998 | 9937-9940 | Three statements overwrite each other in the childless Working Family Credit, so $4,000 of earnings gets $70.20, above the $51 maximum | Statutory, MN-003: phase-in, plateau and phaseout in order. `order_minnesota_1998_working_family_credit` |
| MN | 2009 | 9295, 9305 | The head-of-household minimum-tax exclusion is $49,960 (the 2010 figure) | Both modes: equal to single, $49,860 |
| MN | 2012 | 10043-10093 | Married couples miss the higher joint Working Family Credit thresholds in 2012 only | Statutory, MN-004: joint thresholds. `use_minnesota_2012_joint_working_family_thresholds` |
| MN | 2014-2018 | 10007-10035 | The Working Family Credit phaseout for 2 or more children (single) reads the one-child start (the 2019 build fixed 2019 on): a 2015 single parent of two starts losing it at $21,520 instead of $25,530 | Statutory, MN-005: each table's own start. `use_minnesota_working_family_tables_by_children` |
| MN | 2017-2020 | 10164-10174 | The child care credit is the whole federal-based credit until AGI is $50,000 over the threshold, then the lesser of it and $600 ($1,200) less 5% of the excess | Statutory, MN-006: the lesser of the credit and $600 ($1,200) less 5% of the excess (Minn. Stat. 290.067 subd. 2b). `limit_minnesota_child_care_credit_by_maximum_after_2017` |
| MN | 2019-2021 | 9427 | The head-of-household standard deduction repeats the single amounts ($12,200 instead of $18,350 in 2019) | Both modes: the published amounts |
| MN | All | mncred | The homeowner refund sits in an `else` branch, so renters who also pay property tax get none | Statutory, MN-007 (1989 on): both refunds (Form M1PR). `allow_minnesota_renter_and_homeowner_refunds` |
| MO | All | 11555, motax | The property credit reduces the maximum rather than the tax paid and skips the first income step above the base; from 2008 homeowners' property tax is capped at the old $750, not $1,100 (only elderly filers get this credit). The federal tax deduction cap applies only 1994-2018 | Statutory, MO-001: tax paid up to the maximum ($1,100 for owners from 2008) less the income percentage (RSMo 135.030). `apply_missouri_property_credit_to_tax_paid`. The deduction cap cannot bind after 2018 |
| MO | 2018 | motax | The top rate is set to 5.9% in a shared table that is never reset, so later records in the same file are taxed at 5.9% | Both modes: each record is computed independently |
| MS | 1977-1978 | 11204-11206 | Excess charity is not floored at zero, so 20% of AGI is added to itemized deductions ($410 of tax instead of $540 at $20,000) | Statutory, MS-001: floored at 0. `floor_mississippi_excess_charity` |
| MS | all | | A joint return splits AGI as `splitagi` does and gives the exemptions and deduction to the higher-income spouse first (`optalloc`) | Statutory: published split. `split_mississippi_joint_returns_per_statute` |
| MS | 1991 | | State income tax stays in the itemized deduction through 1991 (not deductible from 1992) | Both modes |
| MS | 2021 on | | The itemized deduction is capped property tax plus mortgage interest and a few other items, with no other itemized deductions | Statutory: federal itemized deductions. `use_mississippi_federal_itemized_deductions_from_2021` |
| MT | 1984-1986, 2009 | 11771 | All unemployment compensation is subtracted, not just the federally taxed part | Statutory, MT-001: the part in federal AGI. `subtract_only_taxable_unemployment_in_montana` |
| MT | 1984 on | 11779-11782 | Separate filers' capital losses are subtracted again ($3,000 off for a $5,000 loss) | Statutory, MT-002: once. `subtract_montana_separate_capital_loss_once` |
| MT | 2011-2012 | 11840 | The test `law.eq.2010.and.law.le.2012` keeps the itemized-deduction limit in 2011-2012 | Statutory, MT-003: suspended 2010-2012 (the 2011 Form 2 has none). `suspend_montana_itemized_limit_through_2012` |
| MT | 1984 on | mttax | The Social Security worksheet is not floored at zero, so negative taxable benefits lower AGI | Statutory, MT-004: floored at 0. `floor_montana_social_security_worksheet` |
| NC | 1977-1988 | 15366 | The child count is reused for the husband's dependent share: 2 children get a $154 joint child care credit instead of $308 | Statutory, NC-001: all children. `count_all_children_for_north_carolina_joint_child_care` |
| NC | 1981-1988 | 15688-15689 | The joint child care credit can come off both spouses | Statutory, NC-002: off the larger tax once. `take_north_carolina_child_care_credit_once` |
| NC | 1989-1990 | 15558 | The married table is applied with income splitting, so the 6% bracket runs to $43,500 instead of $21,750 | Statutory, NC-003: the married table without splitting. `use_north_carolina_married_table_without_splitting` |
| ND | 1987-2000 | 15957 | The long form adds back state income tax even for standard-deduction filers | Statutory, ND-001: only when deducted federally. `add_back_north_dakota_state_tax_for_itemizers_only` |
| NE | 1993-2017 | 12660-12674 | Single filers' additional high-income tax is added twice (about $310 twice at $200,000 in 1995) | Statutory, NE-001: once. `add_nebraska_single_additional_tax_once` |
| NE | 1996, 1997 | 12182, 12187 | Band typos: $297,950 for $287,950 (1996 single), and $575,950 for $588,700 (1997 joint); the bands step by fixed amounts every year | Both modes: $287,950 and $588,700 |
| NE | 2017 | 12058 | The inflation factor is 1.08 instead of 1.046 (threshold $270,000 instead of $261,500) | Both modes: 1.046, the factor every other state uses |
| NY | 1990-2016 | `nytax77` | The household credit is a closed form ($15 for single filers, $30 per exemption otherwise, less $1 per $500 of AGI over $10,000 or $15,000) | Statutory: the published table. `use_new_york_household_credit_table_1990_2016` |
| NY | 2000-2021 | `nytax77` | The child and dependent care credit multiplies the federal credit by the federal rate a second time (2000-2005) or uses the expense, a 90% cap on the share (2006-2021) and a $75,000 limit for three dependents (a typo) | Statutory: the published percentage of the federal credit. `use_new_york_statutory_child_care_credit` |
| NY | 2006-2021 | `nytax77` | The Empire State child credit counts dependents under 18 less those under 6 and adds the federal child care credit before its limit (not the child tax credit); from 2018 the $100 minimum applies only up to the phase-out start | Statutory: children 4-16. `pay_new_york_empire_child_credit_per_statute` |
| NY | 2017-2021 | `nytax77` | The household credit adds $15 or $10 per extra exemption below $25,000 of AGI and nothing above | Statutory: the interpolated table. `interpolate_new_york_household_credit_per_person` |
| NM | 1987 on | `nmtax77` | The itemized deduction removes the state tax share of capped state and local taxes by the line 10 worksheet, and the standard deduction stays the federal figure (1991-2024) even when the federal return itemizes | Statutory: the existing deduction rules. `compute_new_mexico_deductions_per_statute` |
| NM | 2019-2022 | `eicr` | The working families credit is 20% of the federal credit from 2019 (the statute pays 17% in 2019) | Statutory: 17% in 2019. `use_new_mexico_working_families_rates_per_statute` |
| NM | 2022-2023 | `nmtax22` | The low income comprehensive tax rebate interpolates the 2021 table (times 1.042 in 2023) instead of stepping | Statutory: the step table. `use_new_mexico_low_income_rebate_per_statute` |
| OH | 1983-1988 | `ohtax77` | TAXSIM applies the joint filing credit to method 1 twice and compares method 2 with the once-credited method 1 | Statutory: the exemption method that gives the lower tax, one joint credit. `subtract_ohio_exemption_credit_once` |
| OH | 1983 on | `ohtax77` | The retirement income credit is paid at any age (pensions less a cent, taxable income under $100,000) | Statutory: taxpayers 65 or older. `require_age_65_for_ohio_retirement_credit` |
| OH | 2017-2021 | `tab17`-`tab21` | Ohio's tax comes from the build's own tables with rounded base amounts | Statutory: the published brackets. `use_ohio_published_tax_brackets` |
| WI | 1987 on | `witax77` | The itemized credit counts other itemized deductions (`d(66)`) with mortgage interest and charity | Statutory: mortgage interest and charity only. `exclude_other_itemized_deductions_from_wisconsin_credit` |
| WI | 1991 on | `witax77` | The homestead credit counts a fifth of rent as property tax | Statutory: a quarter. `count_wisconsin_rent_at_statutory_share` |
| NJ | All, fixed in the 2026 build | 13062 | The 2024 build let capital losses beyond $3,000 reduce New Jersey income ($40,000 of wages and a $12,000 loss gave $31,000); the 2026 build does not | Statutory, NJ-002: no loss deduction, in both modes now. `disallow_new_jersey_capital_losses` |
| NJ | 1977-2020 | `njtax77` | The tax is computed on the 2.5% top rate through 1990, with no gain exclusion before 1987 counted back and no unemployment or Social Security in income | Statutory: the published schedules. `tax_new_jersey_top_rate_after_1982_per_statute`, `tax_full_capital_gains_in_new_jersey_before_1987` |
| NJ | All | `njtax77`, `njtax21` | Everything after gross income follows the TAXSIM routines: pensions of anyone 62 or older excluded by an income-share table, other non-property income taxed, a $65/$35 credit through 1984, the 1985-1989 property tax floor as a credit only when it exceeds income, 1990-2008 homestead relief as credits (not rebates), the 1996-2003 property tax deduction taxed on the 1991 schedule, the 2021 on property tax remembered on the standard-deduction pass, child care and child tax credits interpolated, and a flat minimum earned income credit from 18 | Statutory: the published rules (the code before this change). `compute_new_jersey_tax_per_statute`, `exclude_new_jersey_pensions_per_statute`, `exclude_nonproperty_income_from_new_jersey_income`, `pay_new_jersey_renter_credit_per_statute` |
| NJ | 2018-2021 | 12910-12916 | The 2004 schedule is used (top rate 8.97%); the law added 10.75%, so $6 million of 2018 wages is under-taxed by about $17,800 | Both modes: the published schedule |
| NJ | 2020 on | | The childless credit uses the federal age test (25) | Statutory, NJ-001: 21 and under 65 for 2020, 18 with no maximum from 2021. A childless filer who meets every federal rule except age gets a flat 40% of the federal childless maximum, rounded ($224 for 2022, $601 for 2021; NJ-1040 line 58); others get 40% of their federal credit. An unreported age passes. `apply_state_childless_eitc_minimum_age` (shared with CA-005); code: `states.nj` |
| NM | 1984-1989 | nmtax | The Social Security exemption adds taxable benefits to AGI a second time | Statutory, NM-001: subtracted. `exempt_new_mexico_social_security_1984_1989` |
| NM | 1985 on | nmtax | The low-income rebate counts taxpayers 65 or older twice in household size | Statutory, NM-002: once. `count_new_mexico_aged_once_in_rebate` |
| NY | 1977-1986 | 14262 | The head-of-household standard deduction table is zero | Statutory, NY-002 (1985-1986): the single amount (no head-of-household status before 1987). `use_single_standard_deduction_for_new_york_head_of_household_before_1987` |
| NY | 1986 | 14924 | A six-row maximum-tax table is read as five rows (3.5% instead of 4% above $26,000) | Statutory, NY-003: six rows. `complete_new_york_1986_maximum_tax_table` |
| NY | 1987-1988 | 14931 | `law.eq.1987.and.law.eq.1988` is never true, so the unearned-income tax is never charged | Statutory, NY-004: 3% (1987) and 2% (1988) of dividends, interest and gains, phased in from $100,000 of AGI. `charge_new_york_unearned_income_tax_1987_1988` |
| NY | 2006-2022 | | The Empire State child credit counts children under 4 (TAXSIM has no ages) | Statutory, NY-001: children under 4 do not qualify before 2023 (Tax Law 606(c-1)); `children_under_4` is removed from the 2018-2022 rebuilt credit (the 2006-2017 credit still counts them). `exclude_children_under_4_from_ny_empire_credit` |
| NY | 2012 on | 14787-14877 | Several worksheets phase in from another worksheet's bound or one missing the inflation factor (150,000 for 300,000; 200,000 for 2,000,000) | Statutory, NY-006 (2012-2020): each worksheet's own bound. `use_own_bounds_in_new_york_worksheets` |
| NY | 2018 on | 14357-14405 | Itemized deductions can be reported negative (tax is unaffected) | Statutory, NY-007: floored at 0. `floor_new_york_itemized_deductions` |
| NY | 2019 on | 13996 | The child credit's wage-base table is above the real base | No effect on the credit |
| NY | 2021 | 14008 | The top worksheet's flat rate is 0.0695 instead of 0.0965; the married worksheet adds back $526 instead of $474; there are no worksheets for the 10.3% and 10.9% brackets | Both modes: from the IT-201 instructions |
| NY | All | 15176-15179 | The minimum tax is computed after refundable credits, so tax below -$5,000 creates a minimum tax ($53.39 in a 2021 example) | Statutory, NY-005: none without preference items. `charge_new_york_minimum_tax_on_preferences_only` |
| OH | 1977-1988 | 16484-16509 | The joint filing credit goes to any return with two income shares, including single filers with non-wage income ($44 for $30,000 of wages and $10,000 of self-employment income in 1980) | Statutory, OH-001: joint returns only. `limit_ohio_joint_credit_to_joint_returns` |
| OH | 1983-1988 | 16505-16514 | The $20 exemption credit is subtracted twice, and a couple using the credit method loses the joint credit | Statutory, OH-002: once; both methods get the joint credit and the lower tax applies. `subtract_ohio_exemption_credit_once` |
| OK | 1981-1986 | 16801 | AGI is reduced by a federal slot that is not filled before 1998 | Not changed: the intended item is unknown, so nothing is subtracted |
| NE | 1977-1986 | `netax77` | The tax is a share of a federal tax variable that is never set, so it is always $0 | Statutory, NE-001: the share of federal tax. `tax_nebraska_before_1987_per_statute` |
| NE | 1988-2017 | `netax77` | The standard deduction stays the federal figure when the federal return itemizes (the state tax makes most high earners itemize), so the state deducts the larger of the two | Statutory: the standard deduction only when the federal return takes it. `zero_nebraska_standard_deduction_when_federal_itemizes` |
| NE | 1998-2023 | `netax77` | The refundable child care credit is a share of the federal credit before its tax limit; the share falls 1 point (not 10) per $1,000 of AGI over $22,000 through 2020 | Statutory: the federal credit after its limit, 10 points per $1,000. `pay_nebraska_refundable_child_care_per_statute` |
| NE | 1996, 1997, 2017, 2022-2023 | `sab96s`, `sab97j`, `aif13`, `aif14` | Typos in the high-income tables ($297,950 for single in 1996, $575,950 for joint in 1997), a 1.08 inflation factor in 2017 and an unrounded 2014 table times its own factor in 2022-2023 | Statutory, NE-002: the published tables. `use_nebraska_high_income_tables_per_statute` |
| NE | 2018 on | `netax77` | The property tax is added back to the itemized deduction after removing the capped state and property tax | Statutory: the federal itemized deduction less the capped state tax. `limit_nebraska_property_tax_deduction_to_federal_cap` |
| OR | 1981-1984 | 17336 | Federal taxable unemployment is added again (Oregon AGI $32,000 on $28,000 of income) | Statutory, OR-004: Oregon's taxable amount replaces it. `replace_federal_unemployment_in_oregon_1981_1984` |
| OR | 2015, 2017 | 17709 | The surplus credit (kicker) is computed into a variable that is never used | Statutory, OR-005: 5.6% of tax. `pay_oregon_2015_2017_kickers` |
| OR | 2016 on | | The working family household and dependent care credit counts every dependent | Statutory, OR-003: only children under 13 (ORS 315.264, IRC 21(b)). `count_only_children_under_13_for_oregon_dependent_care` |
| OR | 2019 | 17710-17712 | The kicker is never reset, so later Oregon records in the same file inherit it (-$11,535.43 instead of $3,489.42) | Both modes: 2019 only |
| OR | 2019, 2021 | `ortax77` | Each surplus credit line overwrites the one before, so only the last survives: the 2019 credit (17.171%) is never paid and 17.341% of tax is paid in 2021 | Statutory, OR-006: 2019 paid, 2021 not (the published credit for 2021 is not coded). `pay_oregon_2019_kicker`, `omit_oregon_2021_kicker` |
| OR | through 2021 | `ortax77` | The federal tax subtraction limit phases out continuously over $25,000 of federal AGI per unit above $122,500, in every year | Statutory, OR-007: the published steps. `phase_out_oregon_federal_tax_limit_in_steps` |
| OR | 2022 on | `ortax22` | The federal tax subtraction limit is read from a table by interpolation, on federal AGI per person | Statutory, OR-007. `phase_out_oregon_federal_tax_limit_in_steps` |
| OR | 2016 | | The standard deduction index is rounded to 1.314 (the published deduction is $2,155) | Statutory: $2,155. `use_oregon_standard_deduction_index_per_statute` |
| OR | 2016 on | | The dependent care credit uses the 2024 publication's table at every year (income scaled by the poverty guideline), for any child under 13 | Statutory, OR-003: that year's table. `apply_oregon_dependent_care_credit_per_statute` |
| OR | 2022 on | `ortax22` | The Kids Credit counts children under 13 (`numlt(6)`) rather than under 6, with its own income limits | Statutory, OR-009: children under 6. `pay_oregon_kids_credit_per_statute` |
| OR | 2022 on | `ortax22` | A dependent filer's taxable income is divided by zero taxpayers, so the tax is always $0 | Statutory, OR-008: taxed on the single schedule. `tax_oregon_dependent_filers_per_statute` |
| OR | 2018 on | | Other itemized deductions sit outside the $10,000 state and local tax cap | Both modes |
| OR | 2021 | `ortax77` | The federal tax subtraction also takes off the refundable child and dependent care credit | Both modes |
| PA | All | 17749 | Tax forgiveness ignores nontaxable income such as unemployment and phases out smoothly rather than in 10% steps | Statutory, PA-001: eligibility income adds unemployment compensation and exempt pensions; 10% steps. `apply_pennsylvania_forgiveness_rules` |
| RI | 2003-2009 | 17998, 18056-18074 | The capital gains rates also apply to short-term gains ($1,000 more tax on a $20,000 short-term gain) | Statutory, RI-001: net long-term gain only. `use_net_long_term_gain_in_rhode_island` |
| RI | 2003-2010 | 18020-18021 | Married filing separately gets the full married standard deduction | Statutory, RI-002: half. `halve_rhode_island_separate_standard_deduction` |
| RI | 2003-2010 | 18133-18134 | The regular tax is subtracted twice in the minimum tax without gains | Statutory, RI-003: once. `subtract_rhode_island_tax_once_in_minimum_tax` |
| RI | 2011 on | 18095-18110 | The deduction phaseout is applied in 20% steps and again smoothly (78.4% kept at $180,000 instead of 80%) | Statutory, RI-004 (2011-2021): steps only. `use_rhode_island_deduction_phaseout_steps_only` |
| RI | 2016 on | ritax | `mst.eq.4.or.mst.eq.4` is a typo for 4 or 7 | No effect |
| RI | 2011-2023 | | The 2011 schedule is scaled by TAXSIM's own yearly factor (2016: 1.1016, 2018: 1.1408 ...), not the published brackets, and the deduction and exemption fall in a straight line over a width of 5% of $175,000 times the factor (rounded down to $50), not in steps | Statutory: published brackets and steps. `use_rhode_island_published_brackets` |
| SC | 1981-1984 | 18383-18386 | The federal tax deduction meant to use 1980 law reads the current year's tax ($220.20 instead of $215.00 in a 1983 example) | Statutory, SC-001: tax under 1980 law. `deduct_south_carolina_federal_tax_under_1980_law` |
| VA | 2003 | 19433-19437 | The year ranges skip 2003, so there is no child care deduction | Statutory, VA-001: the $3,000 federal expense limit. `allow_virginia_child_care_deduction_2003` |
| VA | 2021 | 19409-19411 | The itemized-deduction limit's threshold is scaled by 1.25 in the test but not in the reduction | Statutory, VA-002: in the reduction too. `scale_virginia_itemized_limit_threshold` |
| VA | 2000 on | | The spouse tax adjustment splits AGI as `splitagi` does and takes a fixed $930 ($800 more at 65) off each share | Statutory: published. `compute_virginia_spouse_adjustment_per_statute` |
| VA | all | | No return is exempt below the filing minimum or AGI thresholds (the tests are commented out) | Statutory: exempt. `zero_virginia_tax_below_filing_threshold` |
| VA | 1990 on | | The age deduction is lost for a lone aged taxpayer (a variable never set for years other than 2021), kept (income limited) for two aged taxpayers, and $12,000 each at 82 | Statutory: $12,000 per aged taxpayer. `deduct_virginia_age_deduction_per_statute` |
| VA | 1991-2018 | | The itemized limitation total includes other itemized deductions, and the 2018 cap sum leaves them out | Statutory: published. `limit_virginia_itemized_deductions_without_other_items` |
| VA | 2019 on | | The capped state tax is added to the other items and, below the AGI limit, the whole state tax is subtracted again | Statutory: published. `limit_virginia_itemized_deductions_without_other_items` |
| VT | 1977-1988 | 19135 | The $10 student credit goes to every filer who is not a dependent | Statutory, VT-001: none (TAXSIM has no student input). `omit_vermont_student_credit` |
| WI | 1979 on | 20756-20762 | The renter's school property credit multiplies by a yes/no renter flag instead of rent, so renters get about 2 cents | Statutory, WI-004: based on rent. `base_wisconsin_renter_credit_on_rent` |
| WI | 1983 | 20952-20956 | The homestead test repeats `.le.10` where `.gt.10` was meant, so credit amounts above $10 get nothing | Statutory, WI-003: 80%. `credit_wisconsin_1983_homestead_excess` |
| WI | 1986 | 20593-20599 | There is no schedule for married filing separately, so they owe no tax | Statutory, WI-002: the joint schedule at half the bounds. `tax_wisconsin_1986_separate_returns` |
| WI | 1987 on | 20273-20276 | The capital gain exclusion can be negative and raise income | Statutory, WI-006: floored at 0. `floor_wisconsin_capital_gain_exclusion` |
| WI | 1987-2018 | 20876-20906 | Separate filers get the full married AMT exemption, and the minimum tax is added again after the married couple credit | Statutory, WI-008: half the exemption (`halve_wisconsin_separate_amt_exemption`). WI-009: added once (`add_wisconsin_minimum_tax_once`) |
| WI | 1988-2015, fixed in the 2026 build | 20409, 20458 | Head-of-household (2000-2015) and separate (1988-1998) standard deduction tiers start their reductions before their range, so the deduction drops abruptly | Statutory, WI-005: at their range. `start_wisconsin_standard_deduction_tiers_at_their_range` |
| WI | 1989-1990 | 20969-20972 | Homestead eligibility and the reduction use different income measures | Statutory, WI-007: net income for both. `use_wisconsin_net_household_income_1989_1990` |
| WI | 1999 | 20745 | The itemized-deduction credit is zeroed on the federal standard-deduction run | Statutory, WI-011: kept. `keep_wisconsin_1999_itemized_credit` |
| WI | 2015-2018 | 20676-20684 | The joint brackets are not indexed for inflation | Statutory, WI-001: indexed like single. `index_wisconsin_joint_brackets` |
| WI | All | 20558 | Mortgage interest is truncated to whole dollars | Statutory, WI-010: cents kept. `keep_wisconsin_mortgage_interest_cents` |
| WV | 1977-1986 | 19731 | Itemized deductions are reduced by a slot that is never filled, so state income tax stays in | Statutory, WV-003: removed. `remove_state_tax_from_west_virginia_itemized_deductions` |
| WV | 1984-1986 | 19766 | An 18-row head-of-household table is read as 17 rows, so the 13% rate is never reached | Statutory, WV-001: 18 rows. `complete_west_virginia_1984_head_of_household_table` |
| WV | 2011 | 19608 | The family credit limit for five exemptions is $28,170 | Both modes: $26,170; the limits step by $3,820 |
| WV | All | 19708-19709 | `law.le.1983.and.law.ge.1987` is never true, so the disability deduction never applies | Statutory, WV-002: under 65 with taxable Social Security. `allow_west_virginia_disability_deduction` |

## Other differences from TAXSIM

These are statutory-mode differences that do not fix a TAXSIM error.

- **WA-001 (extension).** TAXSIM calls no Washington tax routine, so state tax is 0.
  Statutory mode adds the Working Families Tax Credit (RCW 82.08.0206), a refundable
  $300-$1,290 by number of children, phased out below the EITC ceiling with a $50
  floor, as negative state tax, through 2025. The 7% capital gains excise tax over
  $250,000 is not modelled: TAXSIM's `ltcg` cannot separate the exempt real estate
  and retirement gains. PolicyEngine taxes all `ltcg`, so large-gain households
  differ by about 7% of the gain above the threshold. Switch:
  `add_washington_working_families_credit`.
- **CT-001 (2022+).** The personal-credit worksheet is read as discrete AGI bands
  (the first threshold reached), not TAXSIM's linear interpolation between rows
  ([2022 CT-1040 instructions](https://portal.ct.gov/-/media/drs/forms/2022/income/2022-ct-1040-instructions_1222.pdf)).
- **OR-002.** The 12% under-three EITC match uses `children_under_3`, and the care
  credit cap uses `dep13`.

### Dated 2022+ state tables (STATE-DATED)

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

### Age-65+ rules in actual-law years (STATE-2022-AGED)

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

### 2025 (2025-LAW)

Federal: child credit $2,200, the senior deduction, and the SALT cap phase-down
(`src/taxsim_py/parameters/national/*`). States: see the PolicyEngine comparison. Provisions that
depart from TAXSIM's conventions (Maine's and DC's refusal of the larger federal
standard deduction, South Carolina's addbacks) are statutory mode only; parameter
updates apply in every mode.

## How the program runs

These come from how the executable processes a file, not from any tax formula.

| Issue | Line | What TAXSIM does | taxsim-py |
|:--|:--|:--|:--|
| Results depend on neighboring records | | Values in shared memory are not reset between records. A Maryland 1983 case ($200,000 of wages and $100,000 of long-term gains) gives $14,785 as the first row and $13,035 as the second | Computes each record independently |
| Extra $1 of household income | 21324 | Every record after the first has $1 more household income | Taxsim mode adds the $1; statutory mode drops it, [STATE-HHY-002](#rules-shared-by-several-states) |
| Single-precision constants | | Constants without `d0` carry round-off, so marginal rates at exact thresholds are slightly off (Maine 7.96% instead of 8%) | Exact arithmetic |
| Single-precision two-earner deduction | 23473 | 10% of $20,000 comes out $2,000.00003, which can move AGI under a round threshold (about $57 of tax for a 1986 New York couple) | Exact arithmetic |
| State detail from the marginal-rate run | 21336-21400 | With `idtl=2`, the state worksheet is printed from the one-cent marginal-rate run, so it shows the extra cent | Same as TAXSIM in taxsim mode; statutory mode reports the base calculation, [STATE-HHY-002](#rules-shared-by-several-states) |
| Itemizing ties | 21662-21690 | Exact ties between itemizing and the standard deduction are settled by round-off | Keeps the standard deduction on exact ties |

## Entries that are not errors

These were logged as possible errors and, on review, match the law.

| Entry | Why it stands |
|:--|:--|
| Head-of-household code 3 never occurs (several states) | Code 3 always appears with code 6 and sets separate-return halving, so it is a legacy separate-return code. Iowa's larger head-of-household standard deduction is Iowa law. |
| Minnesota 1977-1986 state income tax add-back dropped | The 1977-1986 Minnesota itemized deduction already excludes state income tax, so the add-back would count it twice. |
| North Carolina detail output shows federal exemptions less the state adjustment (through 2011) | That difference is the exemption North Carolina allows. |
| South Carolina $15,000 age deduction (1999 on) whatever the income | The deduction is reduced by the retirement deduction, so the total is $15,000 per taxpayer 65 or older. |

## The 2026 build (`taxsim_2026_11_09.f`)

The newest NBER source (build `2026110917`, built with `scripts/build_oracle.sh`) is
now the oracle for all years. It covers federal law through 2026 and state law
through 2025 (2026 state law is extrapolated). Line numbers in this section refer
to `taxsim_2026_11_09.f`. Status: federal 1960-2023 is being re-baselined; state
2022 on and the 2024-2026 federal law are not yet done.

### New errors in the 2026 build

| Years | Line | What TAXSIM does | taxsim-py |
| --- | --- | --- | --- |
| All | 47651 | The marginal-rate loop starts at `isgn=0`, whose zero perturbation exits the loop at once, so `frate` and `srate` are always 0 | Rates are computed as before. `scripts/build_oracle.sh` builds `taxsim2026_mtr.exe`, the same source with the loop starting at 1, as the rate oracle |
| All | 47583-47591 | `ficar` is never assigned (always 0) | Not compared; the value stays the weighted payroll rate |
| All | 45230-45290 | The employer's halves of `fica` are not capped: OASDI above the wage base is still charged 6.2%, and Medicare above the cap in 1990-1993 | Statutory: capped. Taxsim mode: uncapped. `apply_payroll_wage_bases_correctly` |
| 1960-1993 | 45247-45255 | Once wages plus net self-employment income reach the Medicare cap, the employee Medicare tax is charged on the full cap even with no wages | Same switch |
| 2018 on | 41729-41860, `pbusinc` etc. | `pbusinc`, `pprofinc`, `sbusinc` and `sprofinc` are not income (only self-employment tax and the QBI deduction use them), and they count as investment income for the EITC test and the net investment income tax | Statutory: income. `count_business_and_professional_inputs_as_income` |
| 2018 on | 42520-42545 | The QBI deduction treats all self-employment (`psemp`, `ssemp`), S corporation and business income as qualified, nets half of all self-employment tax against professional income only, and phases out over $50,000 for every filing status | Statutory: Form 8995-A streams. `use_form_8995a_qbi_streams` |
| All | 41930 | `nonprop` enters adjusted gross income twice | Statutory: once. `count_nonproperty_income_once` |
| 1998-2000, 2018-2020 | 43298-43310 | The child credit phaseout is not floored: a filer with no qualifying children above the threshold has a negative credit (an added tax of 5% of the excess), and the refundable credit is based on what the nonrefundable credit leaves of the credit before the phaseout | Statutory: floored. `floor_child_credit_phaseout_at_zero` |
| All | 47420-47440 | Codes 6, 66 and 33 with dependents file as head of household | Statutory: separate or single. `honor_separate_and_unmarried_codes_with_dependents` |
| 1991-2003 | output | `v16` (itemized deduction phaseout) is 0 when the standard deduction is taken | Both modes report 0 |

### Errors of the 2024 build the 2026 build fixes

Taxsim mode now follows the statutory rule for these (they remain in
`FIXED_IN_2026_BUILD` in `behavior.py`; the switches go in a later cleanup).

| Years | What the 2024 build did | Now |
| --- | --- | --- |
| 1960-1989 | Wage Medicare tax used one half of the rate | Both halves charged (`hi_rate_combined`), in both modes |
| All | The EITC fell dollar for dollar above the investment income limit | The credit is 0 above the limit, in both modes |
| 1995-1999 | No AMT | AMT applies (`add_amt_before_2000`) |
| 2022 on | Credit for other dependents computed but dropped | Applied (`allow_other_dependent_credit_after_2021`) |
| All | Self-employment wage-base coordination, loss handling, Additional Medicare and the spouse split | Statutory (FED-PAYROLL-001 to 005) |
| Before 1987 | Business income not income | Income (`include_business_income_before_1987`) |
| 2008 | No stimulus rebate | Rebate credit, in both modes (`economic_stimulus_rebate`) |
| 2013 on | Net investment income tax deducted state tax for non-itemizers | Only itemizers deduct it, in both modes |

### State differences found so far (2022 on)

| State | Years | What the 2026 build does | taxsim-py |
| --- | --- | --- | --- |
| All with a rebate | 2021-2025 | One-time rebates (AZ, CO, CT, DE, GA, HI, ID, IL, IN, ME, MA, RI, VA and others) are subtracted in the year they were paid, one year after the tax year that earned them | Statutory: not paid. Taxsim mode: `parameters/national/state_rebates.yaml`. `leave_out_one_time_state_rebates` |
| AR | 2022 on | The tax before credits is capped at AGI times the low-income table's `ftax / finish`, at every AGI, not only inside the table's range; the step table is not used | Statutory: step table. `limit_arkansas_low_income_table_to_its_range` |
| AR | 2021 on | The lesser of a return with the pension exclusion (up to $6,000 per spouse aged 55 or over, pensions allocated by age) and a return with no exclusion and a credit per taxpayer aged 65 or over | Same switch family: `use_arkansas_statutory_step_credits` |
| AR | 2022-2025 | The inflation relief and qualified individual credits fall continuously with taxable income, and joint returns compare the joint and split returns after each one's credits | Same switch |
| VT | 2022 on | The child tax credit counts children under 13 (TAXSIM's age bands for 6 and 7 are the under-13 count) when no ages are given | Statutory: under 6 (under 7 from 2025). `count_vermont_child_credit_by_statutory_age` |
| WA | 2023 on | The Working Families Tax Credit is paid | Both modes (`add_washington_working_families_credit` is now fixed in taxsim mode) |
| All | 2021 on and earlier | The table lookup `tablki` weights a table that rises with income the wrong way round (the 2024 build) | Both modes weight rising tables correctly. A table that falls with income is weighted the wrong way round by the 2026 build: taxsim mode reproduces that (`weight_falling_tables_correctly`) |
| CO | 1978-1986 | | The federal tax deduction is computed through 1977 only, and the itemized deduction is never used (only the standard deduction and the gasoline tax allowance) | Statutory: both deducted. `deduct_colorado_federal_tax_after_1977`, `use_colorado_itemized_deductions_before_1987` |
| CO | 1987-2020 | | The pension subtraction sits inside the itemizer-only state tax add-back block, which starts in 1992: nonitemizers, and everyone before 1992, get none | Statutory: every aged return. `subtract_colorado_pensions_for_nonitemizers_before_2021` |
| CO | 2018 on | | The state tax add-back is the lesser of the state tax and all state and local taxes up to the federal cap (2022 on), so property tax at the cap does not stop it | Statutory: only the state tax that fit under the cap. `use_colorado_statutory_high_income_addback` |
| CO | 2022-2023 | | No high-income deduction add-back in 2022; from 2023 the larger deduction over $12,000 ($16,000 joint) when AGI is over $300,000 | Statutory: published. `use_colorado_statutory_high_income_addback` |
| CO | 2022, 2024 | | A refundable senior housing credit (up to $1,000, then $800) for a taxpayer 65 or older | Statutory: none. `use_colorado_statutory_sales_tax_refund` |
| CO | 2000-2001 | | The $300 child credit counts the children under 13 (the "under 6" helper returns that count) | Statutory: none. `use_colorado_statutory_sales_tax_refund` |
| CO | 2022 | | The $750 per-taxpayer rebate has no age test | Both modes |
| CO | 2022 on | The sales tax refund is interpolated between thresholds from its own table | Statutory: steps. `use_colorado_statutory_sales_tax_refund` |
| VT | 2022 on | The standard deduction is 1.5 (head of household) and 2 (joint) times the single amount, and the capital gain exclusion is the smaller of the long-term gain and $5,000 | Statutory: the published amounts and exclusion options. `use_vermont_published_standard_deductions`, `use_vermont_capital_gain_exclusion_options` |
| NM | 2022 | The one-time rebate is $500 (single) or $1,000 (joint, head of household and married separate) below $75,000 of AGI per filer | Statutory: not paid. Taxsim mode: `state_rebates.yaml` |
| NM | 2023 on | The child tax credit interpolates a seven-row table of 2023 amounts, indexed, on federal AGI less $0.10 | Statutory: the published tiers. `use_new_mexico_published_child_tax_credit` |
| NY | 2022 on | The Empire State child credit is never paid in 2022 and 2025 (the result is read from a variable that stays zero), and in 2023-24 it is 33% of the larger of $100 per child under 19 and the 2024 worksheet amount, only below $75,000 / $110,000 / $55,000 of federal AGI | Statutory: the published credit. `pay_new_york_empire_child_credit_per_statute` |
| NY | 2022 on | Taxable Social Security is not subtracted unless a spouse is 60 or older | Statutory: always subtracted. `subtract_new_york_social_security_at_any_age` |
| NY | 2022 on | The tax, and its recapture of lower-bracket benefits, come from the build's own bracket and worksheet tables (2023 head of household has a $17,700 second bracket) | Statutory: the published supplemental tax. `compute_new_york_supplemental_tax_per_statute` |
| NY | 2022 on | The child and dependent care credit is the federal rate times New York's own expense limit ($3,000 to $9,000 by children under 13) times a percentage read from a table that rises to 116% near $55,000 | Statutory: the published percentage. `use_new_york_statutory_child_care_credit` |
| NY | 2022 on | The household credit adds a flat $15, $10 or $5 per extra exemption (no interpolation) | Statutory: interpolated. `interpolate_new_york_household_credit_per_person` |
| NY | 2022 on | A dependent filer's standard deduction is the regular amount plus $3,100 | Statutory: $3,100. `use_new_york_dependent_standard_deduction_alone` |
| NY | 2022 on | A married-separate filer reads past the end of the rate table (the build uses three filing statuses and indexes a fourth), so tax is a garbage number (for example $603,427 on $120,000 of wages) | Not reproduced in either mode; the cases stay failing against the oracle |
| NY | all years | The pension exclusion applies at 59½ (age 60 in whole years), not 65 (the 2024 build used 65) | Both modes: 60 |
| NY | 2022 on | The itemized deduction phase-out thresholds for 2022-25 come from the build's table | Both modes (published amounts) |
| Federal | 2018 on | Other itemized deductions sit outside the SALT cap (the 2024 build counted them with state and local taxes) | Both modes |
| All | all years | `numlt(6)` returns the count of children under 13 (`dep13`): the age bands in the function are off by one. Federal credits and state code that read `dep6` directly are not affected | Statutory: `dep6`. `count_children_under_6_by_dep6`, used state by state |
| OH | 2021 on | The personal exemption interpolates between $2,400 (to $40,000), $2,150 (to $80,000) and $1,900 | Statutory: steps. `step_ohio_exemption_by_income` |
| OH | 2022-25 | The tax comes from the build's own rounded tables (`tplus`, `tminus`) | Statutory: the published brackets. `use_ohio_published_tax_brackets` |
| OH | 2021 on | The retirement income credit ($200 at most) is paid at any age, from pensions less a cent | Statutory: aged 65 or over. `require_age_65_for_ohio_retirement_credit` |
| SC | 2022 on | The tax comes from rounded subtract-from-flat-rate tables | Statutory: the published brackets. `use_south_carolina_published_tax_table` |
| SC | 2022 on | The retirement deduction goes to the spouse aged 55 or over (half each if both or neither), $3,000 each | Statutory: $3,000 per taxpayer. `give_south_carolina_pension_deduction_to_each_taxpayer` |
| SC | 2022 on | The state tax added back for itemizers is measured against the 2021 federal standard deduction | Statutory: the current one. `use_current_federal_standard_deduction_for_south_carolina_addback` |
| ID | 2022-23 | The 2022 bracket tops are $1 lower than the statute, and the 2023 no-tax amount is $4,489 | Statutory: published. `use_idaho_published_brackets` |
| ID | 2022 on | The building fund tax limit is the federal filing requirement by status and age | Both modes |
| ID | 2008-2009 | | The standard deduction is only the property tax allowance (it adds to a variable never set) | Statutory: the published amount plus the allowance. `use_idaho_standard_deduction_2008_2009_per_statute` |
| ID | 1977-2020 | | The permanent building fund limits stay at the 1978 amounts (none in 1977) | Statutory: published. `use_idaho_building_fund_limits_per_statute` |
| ID | 2018 on | | The child tax credit counts the dependents of earned income credit age (under 19), not under 17 | Statutory: under 17. `count_idaho_child_credit_children_under_17` |
| ID | 1987-1992 | | The standard deduction is read from the pass TAXSIM is on, so it is kept when itemizing | Both modes |
| AZ | 2021-23 | A short-term loss variable is never set (-9), so every return deducts 25% of its long-term gains plus $9 | Statutory: 25% of gains. `initialize_arizona_short_term_loss` |
| AZ | all | | The family income credit test, the itemized limitation test and the pre-1990 standard deduction use AGI after exemptions (Arizona's "AGI" includes them) | Statutory: before exemptions. `test_arizona_family_credit_before_exemptions` |
| CT | 1991-2020 | | The exemption phases out $1,000 early for joint, head of household and separate returns (joint from $47,000, not $48,000); from 2021 a separate return takes the head of household terms | Statutory: published. `phase_out_connecticut_exemption_per_statute` |
| CT | 1991-2023 | | The personal credit is read from one table: single returns scale it by year-specific factors from 2000, joint and head of household returns keep the 1995 thresholds, 1991-1994 reads income per taxpayer, and from 1995 the table is read $0.002 under AGI | Statutory: published thresholds. `use_connecticut_personal_credit_table_per_statute` |
| CT | 2011-2023 | | The 3% bracket add-back starts earlier for single ($51,250) and at its own rates for others, and the high-income recapture added is the bracket recapture (two tiers from 2015); the other recapture is computed and dropped | Statutory: published. `add_connecticut_recapture_per_statute` |
| CT | 2019-2021 | | The pension deduction is all pensions below the AGI limit and the share above it (none in 2021) | Statutory: share, phased out above. `phase_out_connecticut_pension_deduction_per_statute` |
| CT | 2017-2023 | | The property tax credit still needs a dependent or a taxpayer 65 or older | Statutory: any filer from 2022. `allow_connecticut_property_credit_to_all_from_2022` |
| CT and others | 2022 on | Phase-outs fall continuously because the helpers `ceil` and `rup` only round when an option (`extnd(88)`) is set, which never happens | Statutory: whole steps. `round_up_phaseout_steps` |
| All | 2010 on | The pass that takes the standard deduction zeroes the itemized inputs but not the state income tax it refills as it iterates, so it still itemizes when that tax exceeds the standard deduction. The pass that forces itemizing zeroes the federal zero bracket amount (`comnew(3)`) | Statutory: a forced standard deduction. `keep_itemized_inputs_when_taking_standard_deduction` |
| VT | 2000-23 | No renter rebate after 1999 except 2020 (a tenth of rent); no telephone credit; the 2003-21 child care credit is refundable at 72% outside those years; capital gains exclusion is the flat amount only; 2018-21 exclusion uses long plus short gains; tax from the build's own tables (unrounded 2022, subtract-from-flat 2023 on) | Statutory: published. `pay_vermont_renter_rebate_after_1999`, `pay_vermont_telephone_credit`, `use_vermont_published_brackets` |
| VT | 2018 on | The Social Security exemption share is a half point low (2022 on rounded to the nearest point) because `ceil` does not round | Statutory: published |
| MO | 1977 on | Federal tax deducted is the regular tax after nonrefundable credits plus AMT, before the refundable earned income credit | Statutory: after the earned income credit. `deduct_missouri_federal_tax_after_refundable_credits` |
| IA | through 2022 | The federal tax deduction is the regular tax plus self-employment tax less the child care and child credits (the child credit can be negative through 2000), before the refundable credits; joint returns split income as `splitagi` does | Statutory: published. `deduct_iowa_federal_tax_after_refundable_credits`, `split_iowa_joint_income_by_wages_only` |
| IA | 1977-1997 | | No credit for dependents (the formula multiplies the taxpayer and aged counts only), and a head of household gets the personal credit twice more (the second from an uninitialised variable, patched in the oracle to the year's credit) | Statutory: dependents credited, head of household once. `credit_iowa_dependents_before_1998` |
| IA | through 2016 | The tax-reduction worksheet for single filers (tax limited to income over the no-tax floor) applies from 2017 only; lower-income single filers owe the full bracket tax | Statutory: every year. `reduce_iowa_single_tax_before_2017` |
| IA | 1987 on | A joint return splitting income by spouse leaves out the self-employment tax add-back (a quarter of it, or all of it through 1986 from the combined tax) and deducts federal tax without the self-employment tax | Statutory: published. `add_back_iowa_self_employment_tax_twice` |
| IA | 2013-22 | The federal tax deduction subtracts the Additional Medicare Tax from 2013 | Statutory: from 2022. `deduct_iowa_federal_tax_after_refundable_credits` |
| IA | 2020 | | The bracket inflation factor stays at 2019's 1.638 (law 1.666) | Statutory: 1.666. `use_iowa_2020_bracket_inflation_per_statute` |
| IA | 2020 on | The itemized deduction is property tax plus mortgage interest, leaving out other itemized deductions and state taxes | Statutory: federal itemized deductions net of the state tax. `include_state_taxes_in_iowa_itemized_deduction_from_2020` |
| IA | 2021 | The federal tax deduction subtracts the child tax credit twice (it is held in both credit variables) | Both modes: once |
| IA | 2023 on | Iowa tax is figured from federal taxable income less pensions and taxable Social Security, with the alternate tax and the child care credit read at taxable income | Statutory: published. `compute_iowa_tax_from_2023_per_statute` |
| All | 2013 on | Additional Medicare Tax on a separate return uses the $200,000 single threshold ($125,000 in law) | Statutory, FED-PAYROLL-009: $125,000. `use_additional_medicare_separate_threshold` |
| MD | through 2017 | Itemized deduction is floored at the federal standard deduction and counts only when the federal return itemizes | Statutory: published. `floor_maryland_itemized_deduction_at_zero`, `use_maryland_itemized_deduction_without_federal_itemizing` |
| MD | through 2019 | The refundable earned income credit is paid in full on top of the nonrefundable half | Statutory: only the excess over tax. `pay_maryland_refundable_eitc_only_above_tax` |
| KY | 1995 on | The retirement income exclusion is $31,110 for each spouse's share, in every year, with the pension split as `penalloc` does (to the spouse 25 or older, or 0 or older from 2022) | Statutory: the published percentage and maximum for the year. `apply_kentucky_pension_exclusion_schedule` |
| KY | through 2021 | The standard deduction is one per return, not one per taxpayer, and joint returns taxed as two returns use `splitagi` and the larger of the standard deduction and half the itemized deductions for each spouse (2018-2021 too) | Statutory: a standard deduction per taxpayer and the higher earner's wages plus half the other income. `double_kentucky_standard_deduction_for_joint_returns`, `file_kentucky_combined_returns_per_statute` |
| KY | 2018 on | Itemized deductions include other itemized deductions as well as mortgage interest | Statutory: mortgage interest only. `limit_kentucky_itemized_deductions_to_mortgage_interest` |
| KY | 2022 on | The family size credit share is interpolated between the rows of the guideline table, by federal AGI, and applied to the tax after the aged credit (which can leave it negative) | Statutory: the step share by multiple of the poverty guideline. `step_kentucky_family_size_credit_by_poverty_share` |
| LA | 2009-2021 | `tab09m`, `tab09h` | The joint table's 6% bracket starts at $910,000 (a typo for $91,000) and heads of household use the single thresholds ($8,000 and $45,500) | Statutory, LA-001: $91,000; heads of household $3,500 and $41,000. `use_louisiana_brackets_per_statute` |
| LA | through 2021 | `latax77` | A joint return's tax is reduced by the dependent credit twice | Statutory, LA-002: once. `reduce_louisiana_joint_tax_once_for_dependents` |
| LA | 1986-2020 | `latax77` | The federal credit share is overwritten, so it applies only in 1980-1984 (a tenth of the federal credits and earned income credit) | Statutory, LA-003: a tenth of the federal child care and elderly credits. `allow_louisiana_federal_credit_share_per_statute` |
| LA | 2003-2020 | `latax77` | The child care credit uses federal AGI and the federal credit after its liability limit | Statutory: Louisiana AGI. `use_louisiana_child_care_credit_per_statute` |
| LA | 2021 on | `latax21` | The tax is computed by the IT-540B worksheet: exemptions come off the first bracket's income only, dependents and aged taxpayers count $1,000 each, the 2021 federal tax deduction is unlimited by the exempt-income rule, and 2025 uses a $12,500 deduction with no floor on taxable income | Statutory, LA-004: the published schedules. `compute_louisiana_tax_per_statute` |
| LA | 2013 on | `latax77` | The federal tax deduction adds the net investment income tax | Statutory: not deducted. `exclude_niit_from_louisiana_federal_tax_deduction` |
| All | 2018 on | `nlaw` | TAXSIM's first federal pass deducts the estimated sales tax before any state tax exists (earlier years read an input that is never set) | Both modes |
| MN | through 2023 | No property tax or renter refund ("it isn't on the M1 form") | Statutory: paid. `pay_minnesota_property_tax_refund_through_2023` |
| MN | 2013-2018 | No personal exemption (the amount comes from a federal table the build leaves empty) | Statutory: paid. `allow_minnesota_personal_exemption_2013_to_2018` |
| MN | 1987-2016 | | The child care credit starts from the federal credit's qualifying expenses, not the federal credit | Statutory: the federal credit. `read_minnesota_child_care_from_federal_credit` |
| MN | 2021 on | | The child care credit is 35% (down a point per $2,000 of AGI above $15,000, to 20%) of the expenses of children under 13, limited above the threshold | Statutory: published. `compute_minnesota_child_care_credit_from_2021_per_statute` |
| MN | 1988 on | | The elderly subtraction (to 2016) and the nonitemizer charitable subtraction (1999-2020) are computed and never applied | Statutory: applied. `subtract_minnesota_elderly_and_charity_from_income` |
| MN | 1999 on | | The marriage credit's earnings count each spouse's wages, half of pensions and taxable benefits, and 92.35% of business income less a count of older dependents or of children under 6 (misread inputs); through 2017 it subtracts nothing (the federal exemption and zero bracket arrays are empty) | Statutory: published. `compute_minnesota_marriage_credit_earnings_per_statute` |
| MN | 2018-2019 | | Other itemized deductions are left out of the state and local tax add-back | Statutory: counted. `treat_minnesota_other_itemized_as_state_tax_2018_2019` |
| MN | 2019-2022 | | The standard deduction (3%) and the itemized deductions (3%, 2018-2022) are reduced from the 2019-2022 federal-AGI start TAXSIM tabulates, ahead of the published reduction | Statutory: published. `reduce_minnesota_deductions_per_statute` |
| MN | 2019 on | | The childless Working Family Credit needs a taxpayer aged 19-64, so a missing age earns nothing | Statutory: unknown ages eligible. `count_unknown_ages_as_eligible_for_minnesota_childless_credit` |
| MN | 1987-2009 | | The federal zero bracket amount is read from the pass TAXSIM is on, so the pass that takes the standard deduction keeps it when itemizing is natural | Both modes |
| MN | all years | The status code is capped at 2 before the divisor and filer class are read: a separate return is taxed on the joint table (single filer class, single standard-deduction amounts, 2018 joint amounts) and nothing is halved | Statutory: doubled and halved. `double_minnesota_separate_returns` |
| MO | 1977 on | The federal tax deduction is the regular tax before credits plus AMT, capped at $5,000 per taxpayer in every year, and from 2019 its share falls continuously (35% less 1 point per $7,143 over $50,000); from 2022 it is zero | Statutory: published. `deduct_missouri_federal_tax_after_refundable_credits`, `leave_missouri_federal_tax_deduction_uncapped_before_1994`, `step_missouri_federal_tax_deduction_share`, `deduct_missouri_federal_tax_from_2022` |
| MO | 1987-2017 | No itemized deduction (the total is set only from 2018) | Statutory: itemized. `compute_missouri_itemized_deduction_before_2018` |
| MO | 2018 on | The itemized deduction adds all employee payroll tax (and the additional Medicare tax twice) and has no self-employment deduction | Statutory: capped. `limit_missouri_payroll_tax_deduction` |
| MO | 2023 | No working family credit; from 2024 a fifth of the federal credit with no investment income test | Statutory: published. `pay_missouri_working_family_credit_by_statute` |
| MO | 1994 on | Joint returns split income by earner as `splitagi` does (pensions and Social Security by age 55, business income to its earner) | Statutory: by earner only. `split_missouri_joint_income_by_earner_only` |
| SC | 2009-21 | Bracket tops are typed by year in the build, not indexed from one base | Statutory: published. `use_south_carolina_published_tax_table` |
| SC | 2021 on | The federal qualified business income deduction is added back | Statutory: allowed. `allow_qbi_deduction_in_south_carolina` |
| SC | all years | The two-wage-earner credit gives the husband both spouses' business and the wife both professional incomes, less half of each own self-employment tax | Statutory: by earner. `assign_south_carolina_business_income_to_its_earner` |
| SC | 1977-84 | The itemized deductions keep the state income tax (the build no longer subtracts it) | Statutory: subtracted. `subtract_state_tax_from_south_carolina_itemized_deductions` |
| SC | 1991 on | The state tax added back for itemizers is limited to the itemized total above the federal standard deduction; the long-term gain deduction is net of a short-term loss; the child care credit is limited by the lower earner's earnings and counts children under 13 | Both modes |
| ME | 1989-2017 | The standard deduction is the 2021 federal amount ($12,550, $18,800 head of household, $25,100 joint) in every year not given its own formula | Statutory: that year's amount. `use_maine_year_standard_deduction` |
| ME | 2022 on | On a federal itemizing pass the standard deduction is the 2021 federal amount plus $1,700/$1,350 per taxpayer 65 or older, and itemized deductions are property tax plus mortgage interest up to a cap | Statutory: published. `use_maine_current_deductions_when_itemizing` |
| ME | 2022 on | The deduction and exemption phase-outs start at the 2018 start times the build's own indexes, and the tax brackets are the build's unrounded tops (and 2018-21 indexes) | Statutory: published. `use_maine_published_phaseout_starts` |
| ME | 2022 on | The pension deduction goes to a taxpayer aged 55 or over, split like Social Security | Statutory: any age. `deduct_maine_pensions_at_any_age` |
| ME | 2018 on | The dependent credit counts dependents under 17 in 2022 on (all dependents 2018-21) and a single return's 2025 phase-out starts at $125,000 | Statutory: all dependents. `count_all_dependents_for_maine_dependent_credit` |
| ME | 2022 on | The fairness credits count children as dependents (`depx`), not those under 17 | Statutory: under 17. `count_maine_fairness_credit_children_by_dep17` |
| ME | 2022 | The one-time $850 relief payment is per return, not per filer | TAXSIM mode only |
| DE | 2014-23 | The tax is rate times income less a rounded amount (the 5.55% row subtracts $386, the statute $385.50) | Statutory: published. `use_delaware_published_tax_brackets` |
| DE | 2018 on | On a standard-deduction pass the build zeroes the federal itemized totals, so Delaware does not itemize | Statutory: Delaware itemizes on its own. `let_delaware_itemize_when_federal_return_takes_standard_deduction` |
| DE | 2000 on | A spouse aged 60 or over with low earnings gets the extra $2,000 pension exclusion added to the taxpayer's line, replacing it | Statutory: added to the spouse's. `credit_delaware_spouse_low_income_exclusion_to_spouse` |
| DE | 1996 on | Pension exclusions go by age 60, the personal credit adds $110 at 60 or over, and joint returns take the lower of the joint tax and each spouse's tax with income, deductions and dependents allocated as TAXSIM's `splitagi` does | Both modes |
| DE | 2019 on | Itemized deductions are property tax (up to $10,000), mortgage interest and charity: no state income tax and no other itemized deductions | Both modes |
| Federal | 2021 | A separate return claims the earned income credit (with the single-filer parameters) | Both modes |

| GA | 1987 on | | The itemized deduction is the federal one with the state income tax left in | Statutory: less the state tax. `subtract_state_tax_in_georgia_itemized_deduction` || MO | 1977 on | | Federal tax deducted before refundable credits (to 1986); itemized deduction left at zero through 2017; pension exclusion of up to $6,000 per taxpayer for everyone from 1999, and the Social Security exclusion halved by age 62 and subtracted twice | Statutory: `compute_missouri_pension_exemption_per_statute`, `deduct_missouri_federal_tax_after_refundable_credits`, `compute_missouri_itemized_deduction_before_2018` |
| NC | 2016-2022 | | The head of household standard deduction lags the statute by a year ($12,000 in 2016, $13,200 in 2017, $14,000 in 2019, $15,000 in 2020, $16,125 in 2022 for the tables in use) | Statutory: published amounts. `use_north_carolina_head_of_household_deduction_per_statute` |
| NC | 2022 on | | The child deduction schedule is read as a table to interpolate, so the amount rises with income inside each band, and counts dependents under 18 | Statutory: step schedule, children under 17. `read_north_carolina_child_deduction_per_statute` |
| NC | 2018 on | | The federal limit on state and local taxes is also applied to the property tax in the itemized deduction (halved on separate returns) | Statutory: only the $20,000 limit on mortgage interest plus property tax. `cap_north_carolina_property_tax_per_statute` |
| NC | 2012-2013 | | `nonprop` is subtracted once from the double-counted federal AGI | Same switch as the federal row, `count_nonproperty_income_once` |
| NM | 1977-1986, 1987 on | | The itemized deduction follows the line 10 worksheet in every year (state tax share of the capped state and local tax deduction removed); for 2021 the worksheet keeps the federal standard deduction | Statutory: removes the state income tax. `compute_new_mexico_deductions_per_statute` |
| NM | 1977 on | | The low-income rebate household size counts each person 65 or older twice | Statutory: once. `count_new_mexico_aged_once_in_rebate` |
| NM | 2006, 2019 | | The capital gains deduction is 50 percent in 2006 (statute: 40 percent) and 2019 (statute: 40 percent) | Statutory: 40 percent. `set_new_mexico_capital_gains_share_per_statute` |
| NM | 2022 on | | The aged deduction is tested on federal AGI, not New Mexico AGI | Statutory: New Mexico AGI. `test_new_mexico_aged_deduction_on_state_agi` |
| NM | 2022 on | | Capital gains deduction is the larger of the floor (halved on separate returns) and 40 percent of the gain, even above the gain; 2025 the gain up to $2,500 | Statutory: never more than the gain, halved on separate returns. `compute_new_mexico_capital_gains_deduction_per_statute` |
| NM | 2022 on | | Taxable Social Security is exempt below $100,000 (single) or $150,000 (others, halved on separate returns) of federal AGI. This was missing from both modes | Both modes now |
| DE | 1977-1995 | | The 1996 on rules (pension exclusion by age, income split between spouses, tax on separate shares) apply in every year; a combined separate return gets a $3,250 standard deduction each and $110 per dependent in every year; a single return with a taxpayer 65 or older adds twice the personal exemption to the standard deduction before 1987 | Statutory: the rules of each year. `compute_delaware_before_1996_per_statute` |
| VT | 2018 on | | Taxable Social Security is exempt in full below $55,000 ($70,000 joint) of AGI, $60,000 ($75,000) from 2022, phasing out over $10,000 | Statutory: starts of $45,000 ($60,000 joint), then higher. `use_vermont_social_security_exemption_starts_per_statute` |
| MT | 2023 | | A $1,250 rebate per taxpayer (the 2021 tax year rebate, paid 2023), capped by the tax after nonrefundable credits; on a joint return filed as separate shares each spouse's cap is the tax on their share as if itemizing, less their own earned income credit (so the rebate can exceed the tax) | Statutory: no one-time rebates. `leave_out_one_time_state_rebates` |
| MT | 1977 on | | Spouses on a joint return are split by TAXSIM's `splitagi` (each spouse's own income, the rest shared), with dependents' exemptions allocated to cut the larger taxable income first | Statutory: higher earner plus a share. `split_montana_joint_returns_per_statute` |
| MT | 1991 on | | The retirement income exclusion has no age test and divides by a never-set variable (always 1); its cap scales with the number of taxpayers | Statutory: taxpayers 65 or older. `compute_montana_retirement_exclusion_per_statute` |
| MT | 1999 on | | The elderly homeowner and renter credit multiplier falls straight-line from 100 percent at zero income to 40 percent at $35,000 | Statutory: 100 percent to $35,000, then steps. `read_montana_elderly_credit_multiplier_per_statute` |
| MT | 2021 | | Standard deduction minimum was $2,140 (the table carried $2,130) | Both modes |
| MD | 2020-2021 | | An unmarried filer with no children gets back the whole tax less a $530 credit (nothing is owed once the earned income credit is gone) and a $300 rebate in 2021; the refundable earned income rate in 2021 is 45 percent | Statutory: floor credit. `compute_maryland_earned_income_credit_per_statute` (the 2021 rate is a table fix in both modes) |
| MD | 1992 on | | Joint returns split income with `splitagi`, so the two-earner subtraction needs income in both names | Statutory: wages plus half the rest. `split_maryland_joint_income_per_statute` |
| MD | 1997-2019 | | The poverty level credit is worked out but never taken off the tax | Statutory: allowed. `pay_maryland_poverty_level_credit_per_statute` |
| MD | 2001-2018 | | The child care credit rate is ten times the statute's (up to 325 percent of the federal credit) | Statutory: 32.5 percent. `scale_maryland_child_care_credit_per_statute` (also: from 2019 the 32 percent is cut by 1 percent of itself per step, not a point) |
| MD | 2008 on | | The exemption amount interpolates between the AGI tiers instead of stepping | Statutory: steps. `step_maryland_exemption_phaseout_per_statute` |
| MD | 2018 on | | The itemized deduction removes only the state and local tax above the $10,000 cap (property tax first), not the whole state tax | Statutory: whole state tax. `compute_maryland_itemized_deduction_from_2018_per_statute` |
| OK | 1977 on | | The pension exclusion has no age test (the age only decides who holds the pension) and is limited by federal AGI per taxpayer only in 2007-2009 | Statutory: taxpayers 65 or older with the stated limits. `compute_oklahoma_pension_exclusion_per_statute` |
| OK | 1999 on | | The sales tax credit is tested on federal AGI plus losses, untaxed benefits, transfers and earned income credits | Statutory: household income. `compute_oklahoma_sales_tax_credit_income_per_statute` |
| OK | 2021 | | The state earned income credit reads the 2020 federal credit; the child care and child tax credit use the whole 2021 federal amounts without a tax limit | Statutory: 2021 federal credit, limited to tax. `read_oklahoma_federal_credit_law_year_per_statute`, `limit_oklahoma_2021_child_credits_to_tax` |
| OK | 2022 on | | The itemized deduction is the federal total less the state and local tax above the cap, limited to $17,000, with charity added again; the earned income credit has no age test | Statutory: as the statute. `compute_oklahoma_itemized_deduction_from_2022_per_statute`, `test_oklahoma_earned_income_credit_age_per_statute` |
| MA | all | | The payroll tax deduction is each spouse's own payroll tax only (no deduction for Social Security benefits); business and professional inputs are not income for Part B | Statutory: business inputs are income (`count_business_and_professional_inputs_as_income`). The benefits term was an error in both modes |
| WI | 1984-85, 2022 | | The child care credit multiplies a federal variable that is never set, so it is zero | Statutory: share of the federal credit. `pay_wisconsin_child_care_credit_per_statute` |
| WI | 2016 on | | The head of household third standard deduction tier is measured from where the second ends | Statutory: from the first tier. `start_wisconsin_head_of_household_third_tier_per_statute` |
| WI | 1986 | | Separate returns now use the joint schedule at half the bounds (2026 build) | Same in both modes (`tax_wisconsin_1986_separate_returns` is in `FIXED_IN_2026_BUILD`) |
| WI | 2018 on | | Owners get the school property tax credit (the 2024 build paid them nothing) | Both modes now |
| IN | 2003 on | | The earned income credit has no floor on the federal credit, childless filers need a reported age from 25 to 64, the credit uses 9 percent of the federal rates, and (2021 on) up to three children | Statutory: floor on the federal credit, ages tested only when reported. `apply_indiana_federal_credit_floor_from_2011`, `test_indiana_childless_credit_ages_per_statute`, `use_indiana_decoupled_credit_schedule_in_2021` (the rates are corrected in both modes) |
| CA | 2021 on | | The state earned income credit uses the 2021-style formula (rate, maximum, inflection point) and is paid on separate and dependent returns too | Statutory: `caleitc_schedule`, none on those returns. `compute_california_2021_earned_income_credit_per_statute` |
| CA | 2018-2020 | | The itemized deduction puts back the state and local tax above $10,000 after the high-income reduction | Statutory: real estate tax in full, no state income tax. `ignore_federal_salt_cap_in_california` |
| CA | 2018, 2020 | | The old formula's income points for 2018 and 2020 differ from the form's (the 2026 build's tables) | Both modes use the build's tables (Fortran is the source for this `caleitc` table) |
| CA | 2021 | | The 2021 tax brackets are exact (not the 2012 table times an index) | Both modes now |
| UT | 2008-2025 | | The taxpayer credit counts the state income tax in the itemized deduction (it subtracts a variable it never sets; from 2018 it tries to remove the state tax by recomputing the federal itemized deduction, which changes nothing) | Statutory: other deductions only. `exclude_utah_state_income_tax_from_taxpayer_credit_before_2018` |
| UT | 2008-2021 | | The retirement credit needs a reported age and a birth year of 1952 or earlier (the spouse's replaces the taxpayer's), is 450 per taxpayer with no earnings, and the Social Security credit exists only in 2021 | Statutory: aged taxpayers, plus a Social Security credit. `pay_utah_retirement_credit_per_statute` |
| UT | 2022 | | The taxpayer credit phaseout thresholds use an index of 1.299 | Statutory: 15,548 and so on. `use_utah_2022_credit_threshold_per_statute` |
| NY | 2021 | | Joint returns use the single recapture worksheets and head of household the joint ones; separate returns get the earned income credit on the single schedule | Statutory: by filing status, no credit on separate returns. `pick_new_york_worksheet_by_filing_status`, `deny_new_york_2021_earned_income_credit_to_separate_returns` |
| AR | all | | Other non-property income is added to Arkansas income (once, from the double-counted federal figure) | Statutory: not income. `count_nonproperty_income_once` |
| AR | 1985-2020 | | The pension exclusion is one $6,000 per return whatever the ages | Statutory: per aged taxpayer. `exclude_arkansas_pensions_per_taxpayer` |
| AR | all | | On a joint return the primary taxpayer's wages and business income form one share and the spouse's the other, the rest split evenly | Statutory: the higher earner's share. `split_arkansas_joint_income_by_higher_earner` |
| KS | 1998-2020 | | No extra standard deduction for taxpayers 65 or older and no dependent-filer limit (TAXSIM has them only in 1988-1997 and from 2021); the 2021 joint standard deduction is $8,000 | Statutory: aged addition and limit every year, $7,800 joint in 2021. `add_kansas_aged_and_dependent_standard_deduction_per_statute`, `use_kansas_2021_standard_deduction_per_statute` |
| ME | 1988 | | The standard deduction and exemption credits of 1988 are never taken off the tax | Statutory: credits. `credit_maine_1988_standard_deduction_and_exemptions` |
| ME | 1978-2016 | | The credit for the elderly is refundable (listed with the refundable credits) | Statutory: nonrefundable. `make_maine_elderly_credit_refundable` |
| ID | 2021 | | The permanent building fund tax limit is the federal filing requirement plus an amount per aged taxpayer; Social Security benefits are not counted toward it (any year) | Statutory: the 1981 limits in 2021. `use_idaho_building_fund_limits_per_statute` (the Social Security part is corrected in both modes); the 2021 standard deductions in the table are corrected ($12,550 single and separate) |
| WV | 2022 on | | Every filer, separate returns included, uses the joint bracket thresholds undivided (income halved, tax doubled) | Statutory: separate returns on half the thresholds. `halve_west_virginia_separate_brackets_per_statute` |
| WV | 1984-1986 | | The 2026 build reads the whole head of household table, so the old 17-row bug is gone | Same in both modes (`complete_west_virginia_1984_head_of_household_table` is in `FIXED_IN_2026_BUILD`) |
| OH | all | | The joint filing credit needs only $500 of income for each spouse (pensions included), not earned income | Statutory: earned income. `require_earned_income_for_ohio_joint_credit` |
| OH | 2013-2014 | | A business income deduction (50 percent, then 75 percent, to $250,000) with the rest at a flat 3 percent, which the statute did not have | Statutory: none until 2015. `start_ohio_business_deduction_per_statute` |
| PA | all | | Other non-property income is part of Pennsylvania income | Statutory: not income. `count_nonproperty_income_once` |
| ND | 2007 on | | The marriage credit's lower earner is found from net earnings (self-employment income less half its tax) plus pension shares | Statutory: wages (pensions from 2022). `compute_north_dakota_marriage_credit_earnings_per_statute` |
| SC | 1985-1989 | | Federal taxable income leaves out the net long-term capital gain | Statutory: gains taxed in full. `tax_south_carolina_capital_gains_1985_1989` |
| DC | 1977-1981 | | Other non-property income stays in the DC income | Statutory: not income. `count_nonproperty_income_once` |
| DC | 2014-2018 | | The property tax credit is capped at a number read from memory past a table ($355, $349, $353, $279, $0) | Statutory: the credit schedule. `pay_district_of_columbia_property_credit_2014_2018` |
| DC | 2018 on | | The itemized deduction removes the state tax inside the cap measured from property tax only (not other itemized), starting from the federal total | Statutory: from the inputs. `deduct_district_of_columbia_other_itemized_with_state_taxes` |
| WI | 2022 on | | A child care credit of half the federal credit before the tax limit, no child care expense subtraction; other itemized deductions count in the credit in 2022 only | Statutory: expense subtraction to 2021 (as the table), no credit. `pay_wisconsin_child_care_credit_per_statute` |
| WI | 2023 | | Separate returns use the single standard deduction; the homestead credit goes only to a return with a taxpayer 65 or older | Statutory: married separate table, homestead for all who qualify. `use_wisconsin_separate_standard_deduction_from_2023`, `pay_wisconsin_homestead_credit_from_2023` |
| MO | all | | The property tax credit for taxpayers 65 or older is the smaller of the property tax (plus a fifth of rent) and the maximum, less 0.0625 percent of household income, with no income limit or steps | Statutory: stepped schedule with an income limit. `step_missouri_property_credit_per_statute` |
