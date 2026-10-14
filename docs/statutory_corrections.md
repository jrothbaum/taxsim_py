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
| 2022 | 27549 | The regular-tax 20% capital gains threshold for single filers is $459,650; the AMT copy uses $459,750 | Both modes: $459,750 (Rev. Proc. 2021-45) |
| 2022 on | 27945 | The childless EITC minimum age stays at 2021's 19 | Both modes: 25 |
| 2023 | | The EITC uses the 2022 table scaled by inflation (the year cap `lawend` was left at 2022) | Both modes: the published 2023 table |
| 2023 | | EITC investment-income limit $10,300; Social Security wage base $153,600; head-of-household AMT exemption left at 2022 | Both modes: $11,000; $160,200; the 2023 amount |
| 2023 | | The additional standard deduction for age ($1,750 / $1,400) and the young-filer AMT limit ($8,200) repeat 2022 | Both modes: $1,850 / $1,500 and $8,800 |
| 1995-1996, 2008-2009, 2022-2023 | | Dependent standard deduction minimum $600 (law $650), $850 / $900 (law $900 / $950), and stuck at $1,100 for 2022-2023 (law $1,150 and $1,250); earned-income addition $350 from 2022 (law $400) | Both modes: the published amounts |
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
| DC 1982-1986, IA 1977-1981 and 1986, MN 1977-1986, NY 1977-1986 | DC 4476-4479, IA 6443-6448, MN 10129-10140, NY 15000-15001 | The child care credit is a share of the federal credit, which TAXSIM's early federal routine never fills in, so it is $0 | Statutory, STATE-CCC-001: uses the 1977-1986 federal credit. `read_federal_child_care_credit_before_1987` |
| Household income (MI and others), all years | near 21275 | Household income (`data(159)`) leaves out self-employment income, then subtracts half the self-employment tax | Statutory, STATE-HHY-001: includes it. `include_self_employment_in_household_income` |
| Household income and state detail, all years | 21324, 21336-21400 | Every record after the first has $1 more household income (this matters for Michigan's Home Heating Credit), and with `idtl=2` the state worksheet is printed from the one-cent marginal-rate run | Statutory, STATE-HHY-002: drops the $1 and reports the base calculation. `drop_household_income_record_adjustment`, `report_base_state_worksheet` |
| KS, MO, VT; MO 1977-1993 | 231-280 | The payroll tax these states deduct (`socsec`) charges 1.45% on wages above the Social Security base in every year (about $296 too much on $60,000 in Missouri 1985) | Statutory, STATE-SOCSEC-001: stops at the Medicare base (the same base before 1991). `cap_medicare_in_state_payroll_deduction` |
| SC 2018 on, VA 2000 on | | The spouse split assigns business and professional income to the wrong spouse | Statutory, FED-PAYROLL-005 (see [payroll tax](#payroll-tax)) |
| Several, 2022-2023 | 59-71 | For projected years the dispatcher deflates federal slots 1-98 but passes higher slots undeflated and skips 26, 65, 72 and 73. Massachusetts' payroll deduction, Kentucky's child care credit, self-employment tax, and federal total income in Maine, West Virginia and Indiana mix 2021 and current dollars | Projection |

### Each state

| State | Years | Line | What TAXSIM does | taxsim-py |
|:--|:--|:--|:--|:--|
| AL | 2013 on | | The payroll-tax itemized deduction takes half of FICA and then adds the employee-only Additional Medicare tax, which TAXSIM's FICA total already includes, counting it 1.5 times | Statutory, AL-001: counted once. `count_additional_medicare_once_in_alabama_fica_deduction` |
| AR | 2004 | | A chain of year tests checks 2002 twice, so 2004 uses the 1977 thresholds ($3,000, $6,000, $9,000, $15,000, $25,000). $20,000 of taxable income gives $780.00 instead of $721.50 (before the 3% surcharge) | Both modes: $3,400, $6,800, $10,300, $17,100, $28,500 ([DFA 2004 Indexed Tax Brackets](https://dfa.arkansas.gov/wp-content/uploads/2004brackets.pdf)) |
| AZ | 1998 on | 1139 | The family income credit caps dependents at 4, so the $26,575 limit for five or more (A.R.S. 43-1073) is never used. A 2010 head of household with 5 dependents and $26,000 of wages loses $240 | Both modes: the statute's limits |
| AZ | All | | The senior property tax credit's income test includes Social Security (which TAXSIM's household income includes), and the credit is not limited to tax paid | Statutory, AZ-001: Social Security excluded and the credit limited to property tax paid (A.R.S. 43-1072). `limit_arizona_property_credit_per_statute` |
| CA | 1979-1986 | | Taxable unemployment is never in total income but is subtracted again in the AGI adjustment: $30,000 of wages and $4,000 of unemployment gives AGI $26,000 | Statutory, CA-001: not subtracted. `include_unemployment_in_california_total_income` |
| CA | 1987 on | | The minimum-tax base (`alminy`) subtracts positive self-employment and Schedule E income (a 2004 couple's rent lowers their tax by 7 cents a dollar) | Statutory, CA-002: kept, as on Schedule P. `include_business_and_rental_income_in_california_minimum_tax` |
| CA | 2001, 2018 | | AMT exemptions: 2001 head of household $45,692 and married separate $30,461 repeat 2000; 2018 married separate $44,685 | Both modes: Schedule P (540) line 22, $48,114 and $32,075 (2001), $47,685 (2018) |
| CA | 2018 on | | The childless CalEITC is paid at any age (a 15-year-old with $5,000 of wages gets $208.66) | Statutory, CA-005: from 18, with no maximum age (R&TC 17052); an unreported age passes. `apply_state_childless_eitc_minimum_age` (shared with NJ-001); code: `states.ca` |
| CA | 2018 on | | Itemized deductions start from the federal SALT-capped total | Statutory, CA-006: California never adopted the cap (Schedule CA (540)); real estate and other taxes are deductible in full, its own income tax not at all. `ignore_federal_salt_cap_in_california` |
| CA | 2019 on | 2911-2919 | The Young Child Tax Credit is computed from earnings without testing for a child under 6, and multiplied by the number of young children | Statutory, CA-004: requires one (R&TC 17052.1) and pays one credit per return. `require_young_child_for_california_yctc`, `pay_california_yctc_per_young_child` |
| CO | 2022 on | | Pensions and Social Security are subtracted only when someone is 65 or older, under one combined cap | Statutory, CO-001: each taxpayer subtracts pension income up to $20,000 (55-64) or $24,000 (65+), less their Social Security subtraction; those 65+ subtract all taxable Social Security, those 55-64 within the cap (all of it from 2025 when federal AGI is at most $75,000, $95,000 joint; HB24-1142). Pension and Social Security inputs are split equally between spouses (C.R.S. 39-22-104(4)(f)-(g), DR 0104AD) |
| DC | 2000 on | dctax | The dependent-filer low-income credit's table chain tests the 2000 table first, so every year uses the 2000 rates | Statutory, DC-005: the year's rates (2000-2017). `use_current_rates_in_dc_low_income_credit` |
| DC | 2007 | | The married-couple split uses the 2006 rates | Statutory, DC-004: 2007 rates. `use_current_rates_in_dc_earner_split` |
| DC | 2015 on | | The EITC child track counts any dependent (`depx > 0`), not age-qualified children; the childless investment-income test counts only interest and dividends | Statutory, DC-002: the age-qualified count `dep18` (`use_age_qualified_children_for_dc_eitc`). DC-003: positive capital gains count too (D-40 EITC checklist) (`count_capital_gains_in_dc_childless_eitc_investment_income`) |
| HI | All | | The pension subtraction reads `data(72)`, which the input reader never sets (pensions are `data(20)`), so pensions are never excluded | Statutory, HI-003: excludes `pensions`. `exclude_hawaii_pensions` |
| IA | 2010 | | The 2010 joint caps (the pre-2009 federal limits) are tested against one more child than intended, and the non-conformity caps apply in every year 1990-2021, so a 2019 single parent earning $38,000 loses the credit | Statutory, IA-001: caps in 2010 only, $16,590, $38,665 and $43,493 for 0, 1 and 2+ children. `apply_iowa_eitc_caps_as_in_2010_law` |
| IL | 2000-2002 | | The EITC limit subtracts the property tax credit twice | Statutory, IL-002: once. `subtract_illinois_property_credit_once_in_eitc_limit` |
| IL | 2017 on | | The exemption is allowed at any income | Statutory, IL-001: none above $250,000 ($500,000 joint) (35 ILCS 5/204(g)). `apply_illinois_exemption_income_limit` |
| IN | 2009-2010 | 6037-6044 | To cap the EITC at two children, TAXSIM reruns the federal calculation with 2 dependents and keeps the result, so families with 3 or more get federal tax as if they had 2 | Both modes: the correct federal tax; `compare_cps.py` counts these as a known difference |
| KS | 1995-1996 | kstax | Operator precedence lets 1996 skip the homestead income limit | No effect: the refund's reduction schedule already reaches $0 at the limit |
| KS | 2021 on | | Itemizing is no longer allowed | Statutory, KS-001: itemizing follows the federal choice in every year (K-40 Schedule A). `allow_kansas_itemizing_after_2020` |
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
| MN | 2014-2021 | 10007-10035 | The Working Family Credit phaseout for 2 or more children (single) and 3 or more (joint) reads the wrong table: a 2019 single parent of two starts losing it at $22,770 instead of $27,000 | Statutory, MN-005: each table's own start. `use_minnesota_working_family_tables_by_children` |
| MN | 2017 on | 10164-10174 | The child care credit loses 5% of AGI over $50,000 (TAXSIM's own unreachable branch also differs from the law) | Statutory, MN-006: the lesser of the credit and $600 ($1,200) less 5% of the excess (Minn. Stat. 290.067 subd. 2b). `limit_minnesota_child_care_credit_by_maximum_after_2017` |
| MN | 2019-2021 | 9427 | The head-of-household standard deduction repeats the single amounts ($12,200 instead of $18,350 in 2019) | Both modes: the published amounts |
| MN | All | mncred | The homeowner refund sits in an `else` branch, so renters who also pay property tax get none | Statutory, MN-007 (1989 on): both refunds (Form M1PR). `allow_minnesota_renter_and_homeowner_refunds` |
| MO | All | 11555, motax | The property credit reduces the maximum rather than the tax paid and skips the first income step above the base; from 2008 homeowners' property tax is capped at the old $750, not $1,100 (only elderly filers get this credit). The federal tax deduction cap applies only 1994-2018 | Statutory, MO-001: tax paid up to the maximum ($1,100 for owners from 2008) less the income percentage (RSMo 135.030). `apply_missouri_property_credit_to_tax_paid`. The deduction cap cannot bind after 2018 |
| MO | 2018 | motax | The top rate is set to 5.9% in a shared table that is never reset, so later records in the same file are taxed at 5.9% | Both modes: each record is computed independently |
| MS | 1977-1978 | 11204-11206 | Excess charity is not floored at zero, so 20% of AGI is added to itemized deductions ($410 of tax instead of $540 at $20,000) | Statutory, MS-001: floored at 0. `floor_mississippi_excess_charity` |
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
| NJ | All | 13062 | Capital losses beyond $3,000 reduce New Jersey income ($40,000 of wages and a $12,000 loss gives $31,000) | Statutory, NJ-002: New Jersey allows no loss deduction. `disallow_new_jersey_capital_losses` |
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
| OR | 1981-1984 | 17336 | Federal taxable unemployment is added again (Oregon AGI $32,000 on $28,000 of income) | Statutory, OR-004: Oregon's taxable amount replaces it. `replace_federal_unemployment_in_oregon_1981_1984` |
| OR | 2015, 2017 | 17709 | The surplus credit (kicker) is computed into a variable that is never used | Statutory, OR-005: 5.6% of tax. `pay_oregon_2015_2017_kickers` |
| OR | 2016 on | | The working family household and dependent care credit counts every dependent | Statutory, OR-003: only children under 13 (ORS 315.264, IRC 21(b)). `count_only_children_under_13_for_oregon_dependent_care` |
| OR | 2019 | 17710-17712 | The kicker is never reset, so later Oregon records in the same file inherit it (-$11,535.43 instead of $3,489.42) | Both modes: 2019 only |
| PA | All | 17749 | Tax forgiveness ignores nontaxable income such as unemployment and phases out smoothly rather than in 10% steps | Statutory, PA-001: eligibility income adds unemployment compensation and exempt pensions; 10% steps. `apply_pennsylvania_forgiveness_rules` |
| RI | 2003-2009 | 17998, 18056-18074 | The capital gains rates also apply to short-term gains ($1,000 more tax on a $20,000 short-term gain) | Statutory, RI-001: net long-term gain only. `use_net_long_term_gain_in_rhode_island` |
| RI | 2003-2010 | 18020-18021 | Married filing separately gets the full married standard deduction | Statutory, RI-002: half. `halve_rhode_island_separate_standard_deduction` |
| RI | 2003-2010 | 18133-18134 | The regular tax is subtracted twice in the minimum tax without gains | Statutory, RI-003: once. `subtract_rhode_island_tax_once_in_minimum_tax` |
| RI | 2011 on | 18095-18110 | The deduction phaseout is applied in 20% steps and again smoothly (78.4% kept at $180,000 instead of 80%) | Statutory, RI-004 (2011-2021): steps only. `use_rhode_island_deduction_phaseout_steps_only` |
| RI | 2016 on | ritax | `mst.eq.4.or.mst.eq.4` is a typo for 4 or 7 | No effect |
| SC | 1981-1984 | 18383-18386 | The federal tax deduction meant to use 1980 law reads the current year's tax ($220.20 instead of $215.00 in a 1983 example) | Statutory, SC-001: tax under 1980 law. `deduct_south_carolina_federal_tax_under_1980_law` |
| VA | 2003 | 19433-19437 | The year ranges skip 2003, so there is no child care deduction | Statutory, VA-001: the $3,000 federal expense limit. `allow_virginia_child_care_deduction_2003` |
| VA | 2021 | 19409-19411 | The itemized-deduction limit's threshold is scaled by 1.25 in the test but not in the reduction | Statutory, VA-002: in the reduction too. `scale_virginia_itemized_limit_threshold` |
| VT | 1977-1988 | 19135 | The $10 student credit goes to every filer who is not a dependent | Statutory, VT-001: none (TAXSIM has no student input). `omit_vermont_student_credit` |
| WI | 1979 on | 20756-20762 | The renter's school property credit multiplies by a yes/no renter flag instead of rent, so renters get about 2 cents | Statutory, WI-004: based on rent. `base_wisconsin_renter_credit_on_rent` |
| WI | 1983 | 20952-20956 | The homestead test repeats `.le.10` where `.gt.10` was meant, so credit amounts above $10 get nothing | Statutory, WI-003: 80%. `credit_wisconsin_1983_homestead_excess` |
| WI | 1986 | 20593-20599 | There is no schedule for married filing separately, so they owe no tax | Statutory, WI-002: the joint schedule at half the bounds. `tax_wisconsin_1986_separate_returns` |
| WI | 1987 on | 20273-20276 | The capital gain exclusion can be negative and raise income | Statutory, WI-006: floored at 0. `floor_wisconsin_capital_gain_exclusion` |
| WI | 1987-2018 | 20876-20906 | Separate filers get the full married AMT exemption, and the minimum tax is added again after the married couple credit | Statutory, WI-008: half the exemption (`halve_wisconsin_separate_amt_exemption`). WI-009: added once (`add_wisconsin_minimum_tax_once`) |
| WI | 1988-2015 | 20409, 20458 | Head-of-household (2000-2015) and separate (1988-1998) standard deduction tiers start their reductions before their range, so the deduction drops abruptly | Statutory, WI-005: at their range. `start_wisconsin_standard_deduction_tiers_at_their_range` |
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
