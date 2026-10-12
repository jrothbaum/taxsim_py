# Known TAXSIM errors

These are apparent errors in NBER TAXSIM that turned up while porting it, and what
taxsim-py does about each one. Each was judged from the Fortran source and from tests
against the compiled program. None has been confirmed with TAXSIM's authors.

Line numbers refer to `taxsim_2024_09_21.f`, and "the executable" means
`taxsim2024.exe`.

The **taxsim-py** column says how each error is handled:

- **Fixed (statutory)**: `calculation_mode="statutory"`, the default, follows the
  law, and `calculation_mode="taxsim"` reproduces TAXSIM. The ID links to the
  write-up in [Statutory corrections](statutory_corrections.md).
- **Fixed (both modes)**: TAXSIM's number is wrong or out of date for its year, so the
  published value is used in both modes.
- **Same as TAXSIM in taxsim mode**: the error is in TAXSIM's projection of 2021 law
  to later years; statutory mode uses actual law for those years.
- **No effect** and **Not changed**: the error cannot change a result, or the right
  rule needs information TAXSIM has no input for.

Entries examined and found not to be errors are listed at the end.

## Federal

### Income tax, credits and payroll tax

| Years | Line | What TAXSIM does | What the law or intent is | taxsim-py |
|:--|:--|:--|:--|:--|
| 1977-1978 | 23742 | Itemizers' preference income for the maximum tax on earned income includes wages and unemployment benefits (Maryland reads this figure too) | Neither is a tax-preference item | Fixed (statutory), [FED-INCOME-014](statutory_corrections.md#federal-income-tax) |
| 1977-1986 | 23926 | The elderly credit is capped at `data(32)`, an input that is never filled, so it is always $0 | Credit for the elderly applies | Fixed (statutory), [FED-INCOME-012](statutory_corrections.md#federal-income-tax) |
| Before 1987 | law79, law60 | Business, professional and S-corporation income are left out of income tax (payroll tax still charges them) | Taxable income | Fixed (statutory), [FED-INCOME-010](statutory_corrections.md#federal-income-tax) |
| 1979 | 23398 | The dividend exclusion is set to `data(13)`, never filled, so it is $0 | $100 ($200 joint) exclusion | Fixed (statutory), [FED-INCOME-011](statutory_corrections.md#federal-income-tax) |
| 1983-1986 | 24106 | The minimum-tax base leaves out qualified mortgage interest | The law allowed it | Fixed (statutory), [FED-INCOME-013](statutory_corrections.md#federal-income-tax) |
| 1987-1999 | 25563-25564 | The AMT is computed every year but added to tax only from 2000 | AMT owed | Fixed (statutory), [FED-INCOME-004](statutory_corrections.md#federal-income-tax) |
| 1987-1997 | | The child and dependent care credit is never subtracted (the code sits inside the 1998 child tax credit block) | Credit applies | Fixed (statutory), [FED-INCOME-005](statutory_corrections.md#federal-income-tax) |
| 1987-1997 | 25883-25910 | The nonrefundable credit total that states read is tax before credits when the elderly credit exceeds it, otherwise 0. A 68-year-old with $8,000 of wages in 1995 shows $97.50 instead of $712.50 | Actual credits | Fixed (statutory), [FED-INCOME-001](statutory_corrections.md#federal-income-tax) |
| 1990 on | 25312-25324 | The AMT exemption cap for young filers (under 14; 18 in 2006-2007; 24 in 2008-2017) applies to anyone that age, not only dependents | The kiddie-tax rule covers dependent children with unearned income | Fixed (statutory), [FED-INCOME-008](statutory_corrections.md#federal-income-tax) |
| 1991-1992 | 25257 | AMT income adds back the itemized-deduction limit, as if the limit applied to the AMT. A 1991 single itemizer with $150,000 of wages has $1,500 more AMT income | The AMT was exempt from the limit from 1991 | Fixed (statutory), [FED-INCOME-007](statutory_corrections.md#federal-income-tax) |
| All | 25662 | The elderly credit's AGI threshold is $7,500 for a couple with one spouse 65 or older | $10,000 for any joint return | Fixed (statutory), [FED-INCOME-006](statutory_corrections.md#federal-income-tax) |
| All | 24561 | Social Security provisional income adds transfers (welfare, workers' compensation) | It adds tax-exempt interest | Fixed (statutory), [FED-INCOME-003](statutory_corrections.md#federal-income-tax) |
| 1998-2000 | 25768-25778 | The additional child tax credit for 3 or more children uses a payroll slot that is never set, so it is always $0 (and detailed output v42 is always 0) | Refundable credit | Fixed (statutory), [FED-INCOME-009](statutory_corrections.md#federal-income-tax) |
| 2021 | | The EITC is zeroed for married filing separately, although a code comment says they qualify | Some separate filers qualify | Not changed: the law requires living apart for six months, which TAXSIM has no input for |
| 2022 on | 25714 | The Credit for Other Dependents is computed but never added, so adult and age-17 dependents get nothing | $500 per dependent who is not a qualifying child | Fixed (statutory), [FED-INCOME-002](statutory_corrections.md#federal-income-tax) |
| 2022 | 27549 | The regular-tax 20% capital gains threshold for single filers is $459,650; the AMT copy uses $459,750 | $459,750 | Fixed (both modes) |
| 2022 on | 27945 | The childless EITC minimum age stays at 2021's 19 | 25 | Fixed (both modes) |
| 2023 | | The EITC uses the 2022 table scaled by inflation (the year cap `lawend` was left at 2022) | The published 2023 table | Fixed (both modes) |
| 2023 | | EITC investment-income limit $10,300; Social Security wage base $153,600; head-of-household AMT exemption left at 2022 | $11,000; $160,200; the 2023 amount | Fixed (both modes) |
| 2023 | | The additional standard deduction for age ($1,750 / $1,400) and the young-filer AMT limit ($8,200) repeat 2022 | $1,850 / $1,500 and $8,800 | Fixed (both modes) |
| 1995-1996, 2008-2009, 2022-2023 | | Dependent standard deduction minimum $600 (law $650), $850 / $900 (law $900 / $950), and stuck at $1,100 for 2022-2023 (law $1,150 and $1,250); earned-income addition $350 from 2022 (law $400) | Published amounts | Fixed (both modes) |
| 2024 on | | The executable refuses to run past 2023 (a hard year check and the `xndxa(1981:2023)` array bound) | | taxsim-py uses actual law through 2025 and raises an error after |
| All | 27940-27952 | The executable ignores reported ages under 65: it pays the childless EITC below the minimum age ($529 for a 17-year-old with $8,000 of wages in 2019) and never caps young filers' AMT exemption. The source applies both | Age tests apply | taxsim-py uses the reported ages |
| 2013 on | | Additional Medicare tax counts combined self-employment income in both spouses' totals: $200,000 of self-employment income gives $1,524.60 | Each person's net earnings counted once | Fixed (statutory), [FED-PAYROLL-001](statutory_corrections.md#federal-payroll-tax) |
| All | | The 92.35% factor is applied to wages plus self-employment income, which creates extra wage-base room | The factor applies to self-employment income only | Fixed (statutory), [FED-PAYROLL-002](statutory_corrections.md#federal-payroll-tax) |
| All | | Self-employment tax is charged from the first dollar | None when 92.35% of net earnings is under $400, per spouse | Fixed (statutory), [FED-PAYROLL-003](statutory_corrections.md#federal-payroll-tax) |
| All | 22300-22330 | Business losses lower payroll tax on wages: $50,000 of wages and a $10,000 loss gives $6,237 instead of $7,650 | Results netted per person, floored at zero | Fixed (statutory), [FED-PAYROLL-004](statutory_corrections.md#federal-payroll-tax) |
| All | 22254 | The payroll lookup mixes up the earners' business income: the primary earner's stream holds both business incomes, the spouse's both professional incomes. The wage base is applied to the wrong person, and the QBI deduction nets the wrong pair | Each person's own income | Fixed (statutory), [FED-PAYROLL-005](statutory_corrections.md#federal-payroll-tax) |

### Federal marginal rate that states read

Some states before 2001 use TAXSIM's analytic federal marginal rate (`comnew(72)`).
These errors affect only that rate, not federal tax. Statutory mode corrects them,
[FED-RATE-001](statutory_corrections.md#federal-income-tax), except the Social
Security term: the branch TAXSIM always takes there is the correct formula, and the
alternative it skips would count the EITC slope twice.

| Years | Line | What TAXSIM does |
|:--|:--|:--|
| 1977-1978 | 23705-23724, 23874 | The alternative capital gains rate is 0 for moderate gains, so Nebraska 1977 reports 0 for such couples |
| 1979-1982 | 24100-24135 | The minimum-tax rate is used only if the zero-bracket amount is 0, which never happens; 1982 nets to minus 60% of the minimum-tax rate for some itemizers |
| 1981 | 23628-23632 | The 1.25% rate-reduction credit is overwritten on the next line, so it reaches the tax but not the rate |
| 1993-1996 | tax93, tax94 | The rate ignores the 28% cap on gains (1993) or uses income less gains even when that is not the cheaper method (1994-1996) |
| 1994 on | 25925-25937 | The Social Security term compares a percent with a fraction, so the alternative formula never runs |
| 1998 on | 25733, 25758, 25972 | The child tax credit adds `.05`, a fraction, to a rate in percent: +0.05 points |
| All | 25946 | The elderly-credit term subtracts a percent from 50 inside a fraction |
| All | 27860-27880 | The EITC slope is set before the disqualification tests, so it stays in the rate when no credit is paid |

## States

| State | Years | Line | What TAXSIM does | taxsim-py |
|:--|:--|:--|:--|:--|
| Several | 2022-2023 | 59-71 | For projected years the dispatcher deflates federal slots 1-98 but passes higher slots undeflated and skips 26, 65, 72 and 73. Massachusetts' payroll deduction, Kentucky's child care credit, self-employment tax, and federal total income in Maine, West Virginia and Indiana mix 2021 and current dollars | Same as TAXSIM in taxsim mode; statutory mode uses actual 2022+ law |
| AL | 2013 on | | The payroll-tax itemized deduction takes half of FICA and then adds Additional Medicare tax again, counting it 1.5 times | Fixed (statutory), [AL-001](statutory_corrections.md#other-states) |
| AR | 2004 | | A chain of year tests checks 2002 twice, so 2004 uses the 1977 thresholds. $20,000 of taxable income gives $780.00 instead of $721.50 | Fixed (both modes) |
| AZ | 1998 on | 1139 | The family income credit caps dependents at 4, so the $26,575 limit for five or more (A.R.S. 43-1073) is never used. A 2010 head of household with 5 dependents and $26,000 of wages loses $240 | Fixed (both modes) |
| AZ | All | | The senior property tax credit's income test includes Social Security, and the credit is not limited to tax paid (A.R.S. 43-1072) | Fixed (statutory), [AZ-001](statutory_corrections.md#other-states) |
| CA | 1979-1986 | | Unemployment is never in total income but is subtracted again: $30,000 of wages and $4,000 of unemployment gives AGI $26,000 | Fixed (statutory), [CA-001](statutory_corrections.md#california) |
| CA | 1987 on | | The minimum-tax base subtracts positive self-employment and Schedule E income | Fixed (statutory), [CA-002](statutory_corrections.md#california) |
| CA | 2001, 2018 | | AMT exemptions: 2001 head of household $45,692 and married separate $30,461 repeat 2000 (should be $48,114 and $32,075); 2018 married separate $44,685 (should be $47,685) | Fixed (both modes) |
| CA | 2018 on | | The childless CalEITC is paid at any age (a 15-year-old with $5,000 of wages gets $208.66); the law requires 18 | Fixed (statutory), [CA-005](statutory_corrections.md#california) |
| CA | 2018 on | | Itemized deductions start from the federal SALT-capped total, though California never adopted the cap | Fixed (statutory), [CA-006](statutory_corrections.md#california) |
| CA | 2019 on | 2911-2919 | The Young Child Tax Credit is paid without testing for a child under 6, and multiplied by the number of young children | Fixed (statutory), [CA-004](statutory_corrections.md#california) |
| CO | 2022 on | | Pensions and Social Security are subtracted only at 65 or older, under one combined cap | Fixed (statutory), [CO-001](statutory_corrections.md#other-states) |
| DC | 1982-1986 | 4476-4479 | The child care credit reads a federal slot that is never filled, so it is $0 | Fixed (statutory), [STATE-CCC-001](statutory_corrections.md#rules-shared-by-several-states) |
| DC | 2000 on | dctax | The low-income table chain tests the 2000 table first, so every year uses it | Fixed (statutory), [DC-005](statutory_corrections.md#other-states) |
| DC | 2007 | | The married-couple split uses the 2006 rates | Fixed (statutory), [DC-004](statutory_corrections.md#other-states) |
| DC | 2015 on | | The EITC child track counts any dependent, not age-qualified children; the childless investment-income test leaves out capital gains | Fixed (statutory), [DC-002, DC-003](statutory_corrections.md#other-states) |
| HI | All | | The pension subtraction reads `data(72)`, which is never set, so pensions are never excluded | Fixed (statutory), [HI-003](statutory_corrections.md#other-states) |
| IA | 1977-1981, 1986 | 6443-6448 | The child care credit reads a federal slot that is never filled, so it is $0 | Fixed (statutory), [STATE-CCC-001](statutory_corrections.md#rules-shared-by-several-states) |
| IA | 2010 | | The 2010 joint caps (the pre-2009 federal limits) are tested against one more child than intended, and the non-conformity caps apply in every year 1990-2021, so a 2019 single parent earning $38,000 loses the credit | Fixed (statutory), [IA-001](statutory_corrections.md#other-states) |
| IL | 2000-2002 | | The EITC limit subtracts the property tax credit twice | Fixed (statutory), [IL-002](statutory_corrections.md#other-states) |
| IL | 2017 on | | The exemption is allowed at any income; the law removes it above $250,000 ($500,000 joint) | Fixed (statutory), [IL-001](statutory_corrections.md#other-states) |
| IN | 2009-2010 | 6037-6044 | To cap the EITC at two children, TAXSIM reruns the federal calculation with 2 dependents and keeps the result, so families with 3 or more get federal tax as if they had 2 | Fixed (both modes) |
| KS | 1995-1996 | kstax | Operator precedence lets 1996 skip the homestead income limit | No effect: the refund's reduction schedule already reaches $0 at the limit |
| KS | 2021 on | | Itemizing is no longer allowed | Fixed (statutory), [KS-001](statutory_corrections.md#other-states) |
| KY | 1995 on | | The pension exclusion cap applies per return, not per taxpayer | Fixed (statutory), [KY-001](statutory_corrections.md#other-states) |
| KY | 2011-2012 | | The test `law.eq.2010.and.law.le.2012` suspends the itemized-deduction phaseout only in 2010 | Fixed (statutory), [KY-002](statutory_corrections.md#other-states) |
| MA | 1985 on | 8374 | `mst.ne.3.or.mst.ne.6` is always true, so unused exemptions carry over to unearned income for every filer, not just couples | Fixed (statutory), [MA-003](statutory_corrections.md#other-states) |
| MA | 2011-2020 | 8473 | The test `law.eq.2010.and.law.le.2020` gives the child care expense deduction only in 2010 | Fixed (statutory), [MA-002](statutory_corrections.md#other-states) |
| MA | All | 8448 | The spouse's payroll deduction reads Social Security benefits (`comnew(84)`) instead of the spouse's payroll tax (`comnew(184)`) | Fixed (statutory), [MA-001](statutory_corrections.md#other-states) |
| MD | 2019-2021 | | The standard deduction's minimum and maximum amounts fill the array in the wrong order: single comes out $1,500-$1,550 for 2019 instead of $1,500-$2,250 | Fixed (both modes) |
| ME | 2022-2023 | | The exemption count is deflated and truncated, so most small households lose the property tax and sales tax fairness credits | Same as TAXSIM in taxsim mode; statutory mode uses actual law |
| MI | All | near 21275 | Household income leaves out self-employment income, then subtracts half the self-employment tax | Fixed (statutory), [STATE-HHY-001](statutory_corrections.md#rules-shared-by-several-states) |
| MI | 2013 on | mitax | The senior deduction is $20,000 per person 65 or older whatever their retirement income | Fixed (statutory), [MI-001](statutory_corrections.md#other-states) |
| MI | 2022-2023 | | The Home Heating Credit uses undeflated income against 2021 dollar amounts | Same as TAXSIM in taxsim mode; statutory mode uses actual law |
| MN | 1977-1986 | 10129-10140 | The child care credit reads a federal slot that is never filled, so it is $0 | Fixed (statutory), [STATE-CCC-001](statutory_corrections.md#rules-shared-by-several-states) |
| MN | 1978-1984 | 9892 | The $50 homemaker credit is zeroed unless the filer is someone's dependent (the test looks reversed) | Fixed (statutory), [MN-002](statutory_corrections.md#other-states) |
| MN | 1998 | 9937-9940 | Three statements overwrite each other in the childless Working Family Credit, so $4,000 of earnings gets $70.20, above the $51 maximum | Fixed (statutory), [MN-003](statutory_corrections.md#other-states) |
| MN | 2009 | 9295, 9305 | The head-of-household minimum-tax exclusion is $49,960 (the 2010 figure); single is $49,860 | Fixed (both modes) |
| MN | 2012 | 10043-10093 | Married couples miss the higher joint Working Family Credit thresholds in 2012 only | Fixed (statutory), [MN-004](statutory_corrections.md#other-states) |
| MN | 2014-2021 | 10007-10035 | The Working Family Credit phaseout for 2 or more children (single) and 3 or more (joint) reads the wrong table: a 2019 single parent of two starts losing it at $22,770 instead of $27,000 | Fixed (statutory), [MN-005](statutory_corrections.md#other-states) |
| MN | 2017 on | 10164-10174 | The credit loses 5% of AGI over $50,000; the law takes the lesser of the credit and $600 ($1,200) less that 5% (Minn. Stat. 290.067 subd. 2b), and TAXSIM's own unreachable branch also differs | Fixed (statutory), [MN-006](statutory_corrections.md#other-states) |
| MN | 2019-2021 | 9427 | The head-of-household standard deduction repeats the single amounts ($12,200 instead of $18,350 in 2019) | Fixed (both modes) |
| MN | All | mncred | The homeowner refund sits in an `else` branch, so renters who also pay property tax get none | Fixed (statutory), [MN-007](statutory_corrections.md#other-states) |
| MO | 1977-1993 | 231-280 | The payroll deduction charges 1.45% Medicare tax above the wage base every year (about $296 too much on $60,000 in 1985) | Fixed (statutory), [STATE-SOCSEC-001](statutory_corrections.md#rules-shared-by-several-states) |
| MO | 2008 on | 11555 | Homeowners' property tax is capped at the old $750, not $1,100 (only elderly filers get this credit) | Fixed (statutory), [MO-001](statutory_corrections.md#other-states) |
| MO | All | motax | The property credit skips the first income step above the base; the federal tax deduction cap applies only 1994-2018 | Fixed (statutory), [MO-001](statutory_corrections.md#other-states); the deduction cap cannot bind after 2018 |
| MO | 2018 | motax | The top rate is set to 5.9% in a shared table that is never reset, so later records in the same file are taxed at 5.9% | Fixed (both modes): each record is computed independently |
| MS | 1977-1978 | 11204-11206 | Excess charity is not floored at zero, so 20% of AGI is added to itemized deductions ($410 of tax instead of $540 at $20,000) | Fixed (statutory), [MS-001](statutory_corrections.md#other-states) |
| MT | 1984-1986, 2009 | 11771 | All unemployment is subtracted, not just the federally taxed part | Fixed (statutory), [MT-001](statutory_corrections.md#other-states) |
| MT | 1984 on | 11779-11782 | Separate filers' capital losses are subtracted again ($3,000 off for a $5,000 loss) | Fixed (statutory), [MT-002](statutory_corrections.md#other-states) |
| MT | 2011-2012 | 11840 | The test `law.eq.2010.and.law.le.2012` keeps the itemized-deduction limit in 2011-2012 | Fixed (statutory), [MT-003](statutory_corrections.md#other-states) |
| MT | All | mttax | The Social Security worksheet is not floored at zero, so negative taxable benefits lower AGI | Fixed (statutory), [MT-004](statutory_corrections.md#other-states) |
| NC | 1977-1988 | 15366 | The child count is reused for the husband's dependent share: 2 children get a $154 credit instead of $308 | Fixed (statutory), [NC-001](statutory_corrections.md#other-states) |
| NC | 1981-1988 | 15688-15689 | The joint child care credit can come off both spouses | Fixed (statutory), [NC-002](statutory_corrections.md#other-states) |
| NC | 1989-1990 | 15558 | The married table is applied with income splitting, so the 6% bracket runs to $43,500 instead of $21,750 | Fixed (statutory), [NC-003](statutory_corrections.md#other-states) |
| ND | 1987-2000 | 15957 | The long form adds back state income tax even for standard-deduction filers | Fixed (statutory), [ND-001](statutory_corrections.md#other-states) |
| NE | 1993-2017 | 12660-12674 | Single filers' additional high-income tax is added twice (about $310 twice at $200,000 in 1995) | Fixed (statutory), [NE-001](statutory_corrections.md#other-states) |
| NE | 1996, 1997 | 12182, 12187 | Band typos: $297,950 for $287,950, and $575,950 for about $588,700 | Fixed (both modes) |
| NE | 2017 | 12058 | The inflation factor is 1.08 instead of 1.046 (threshold $270,000 instead of $261,500) | Fixed (both modes) |
| NJ | All | 13062 | Capital losses beyond $3,000 reduce New Jersey income ($40,000 of wages and a $12,000 loss gives $31,000); New Jersey allows no loss deduction | Fixed (statutory), [NJ-002](statutory_corrections.md#other-states) |
| NJ | 2018-2021 | 12910-12916 | The 2004 schedule is used (top rate 8.97%); the law added 10.75%, so $6 million of 2018 wages is under-taxed by about $17,800 | Fixed (both modes) |
| NJ | 2020 on | | The childless credit uses the federal age test; New Jersey uses 21-64 for 2020 and 18 or older from 2021 | Fixed (statutory), [NJ-001](statutory_corrections.md#california) |
| NM | 1984-1989 | nmtax | The Social Security exemption adds benefits to AGI a second time | Fixed (statutory), [NM-001](statutory_corrections.md#other-states) |
| NM | 1985 on | nmtax | The low-income rebate counts taxpayers 65 or older twice | Fixed (statutory), [NM-002](statutory_corrections.md#other-states) |
| NY | 1977-1986 | 14262 | The head-of-household standard deduction table is zero | Fixed (statutory), [NY-002](statutory_corrections.md#other-states) |
| NY | 1977-1986 | 15000-15001 | The child care credit reads a federal slot that is never filled, so it is $0 | Fixed (statutory), [STATE-CCC-001](statutory_corrections.md#rules-shared-by-several-states) |
| NY | 1986 | 14924 | A six-row maximum-tax table is read as five rows (3.5% instead of 4% above $26,000) | Fixed (statutory), [NY-003](statutory_corrections.md#other-states) |
| NY | 1987-1988 | 14931 | `law.eq.1987.and.law.eq.1988` is never true, so the unearned-income tax is never charged | Fixed (statutory), [NY-004](statutory_corrections.md#other-states) |
| NY | 2006-2022 | | The Empire State child credit counts children under 4 | Fixed (statutory), [NY-001](statutory_corrections.md#other-states) |
| NY | 2012 on | 14787-14877 | Worksheet bounds are missing the inflation factor or wrong (150,000 for 300,000; 200,000 for 2,000,000) | Fixed (statutory), [NY-006](statutory_corrections.md#other-states) |
| NY | 2018 on | 14357-14405 | Itemized deductions can be reported negative (tax is unaffected) | Fixed (statutory), [NY-007](statutory_corrections.md#other-states) |
| NY | 2019 on | 13996 | The child credit's wage-base table is above the real base (no practical effect) | No effect on the credit |
| NY | 2021 | 14008 | The top worksheet's flat rate is 0.0695 instead of 0.0965; the married worksheet adds back $526 instead of $474; there are no worksheets for the 10.3% and 10.9% brackets | Fixed (both modes), from the IT-201 instructions |
| NY | All | 15176-15179 | The minimum tax is computed after refundable credits, so tax below -$5,000 creates a minimum tax ($53.39 in a 2021 example) | Fixed (statutory), [NY-005](statutory_corrections.md#other-states) |
| OH | 1977-1988 | 16484-16509 | The joint filing credit goes to single filers with non-wage income ($44 for $30,000 of wages and $10,000 of self-employment income in 1980) | Fixed (statutory), [OH-001](statutory_corrections.md#other-states) |
| OH | 1983-1988 | 16505-16514 | The $20 exemption credit is subtracted twice | Fixed (statutory), [OH-002](statutory_corrections.md#other-states) |
| OK | 1981-1986 | 16801 | AGI is reduced by a federal slot that is not filled before 1998 | Not changed: the intended item is unknown, so nothing is subtracted |
| OR | 1981-1984 | 17336 | Federal unemployment is added again (Oregon AGI $32,000 on $28,000 of income) | Fixed (statutory), [OR-004](statutory_corrections.md#other-states) |
| OR | 2015, 2017 | 17709 | The surplus credit (kicker) is computed into a variable that is never used | Fixed (statutory), [OR-005](statutory_corrections.md#other-states) |
| OR | 2016 on | | The dependent care credit counts every dependent, not just children under 13 | Fixed (statutory), [OR-003](statutory_corrections.md#other-states) |
| OR | 2019 | 17710-17712 | The kicker is never reset, so later Oregon records in the same file inherit it (-$11,535.43 instead of $3,489.42) | Fixed (both modes): 2019 only |
| PA | All | 17749 | Tax forgiveness ignores nontaxable income such as unemployment and phases out smoothly rather than in 10% steps | Fixed (statutory), [PA-001](statutory_corrections.md#other-states) |
| RI | 2003-2009 | 17998, 18056-18074 | The capital gains rates also apply to short-term gains ($1,000 more tax on a $20,000 short-term gain) | Fixed (statutory), [RI-001](statutory_corrections.md#other-states) |
| RI | 2003-2010 | 18020-18021 | Married filing separately gets the full married standard deduction | Fixed (statutory), [RI-002](statutory_corrections.md#other-states) |
| RI | 2003-2010 | 18133-18134 | The regular tax is subtracted twice in the minimum tax | Fixed (statutory), [RI-003](statutory_corrections.md#other-states) |
| RI | 2011 on | 18095-18110 | The deduction phaseout is applied in 20% steps and again smoothly (78.4% kept at $180,000 instead of 80%) | Fixed (statutory), [RI-004](statutory_corrections.md#other-states) |
| RI | 2016 on | ritax | `mst.eq.4.or.mst.eq.4` is a typo for 4 or 7 (harmless) | No effect |
| SC | 1981-1984 | 18383-18386 | The federal tax deduction meant to use 1980 law reads the current year's tax ($220.20 instead of $215.00 in a 1983 example) | Fixed (statutory), [SC-001](statutory_corrections.md#other-states) |
| SC, VA | SC 2018 on, VA 2000 on | | The spouse split assigns business and professional income to the wrong spouse | Fixed (statutory), [FED-PAYROLL-005](statutory_corrections.md#federal-payroll-tax) |
| VA | 2003 | 19433-19437 | The year ranges skip 2003, so there is no child care deduction | Fixed (statutory), [VA-001](statutory_corrections.md#other-states) |
| VA | 2021 | 19409-19411 | The itemized-deduction limit's threshold is scaled by 1.25 in the test but not in the reduction | Fixed (statutory), [VA-002](statutory_corrections.md#other-states) |
| VT | 1977-1988 | 19135 | The $10 student credit goes to every filer who is not a dependent | Fixed (statutory), [VT-001](statutory_corrections.md#other-states) |
| WI | 1979 on | 20756-20762 | The renter's school property credit multiplies by a yes/no renter flag instead of rent, so renters get about 2 cents | Fixed (statutory), [WI-004](statutory_corrections.md#other-states) |
| WI | 1983 | 20952-20956 | The homestead test repeats `.le.10` where `.gt.10` was meant | Fixed (statutory), [WI-003](statutory_corrections.md#other-states) |
| WI | 1986 | 20593-20599 | There is no schedule for married filing separately, so they owe no tax | Fixed (statutory), [WI-002](statutory_corrections.md#other-states) |
| WI | 1987 on | 20273-20276 | The capital gain exclusion can be negative and raise income | Fixed (statutory), [WI-006](statutory_corrections.md#other-states) |
| WI | 1987-2018 | 20876-20906 | The minimum tax is added back after the married couple credit; separate filers get the full married exemption | Fixed (statutory), [WI-008](statutory_corrections.md#other-states), [WI-009](statutory_corrections.md#other-states) |
| WI | 1988-2015 | 20409, 20458 | Standard deduction formulas start their reductions from the wrong point, so the deduction drops abruptly | Fixed (statutory), [WI-005](statutory_corrections.md#other-states) |
| WI | 1989-1990 | 20969-20972 | Homestead eligibility and the reduction use different income measures | Fixed (statutory), [WI-007](statutory_corrections.md#other-states) |
| WI | 1999 | 20745 | The itemized-deduction credit is zeroed on the standard-deduction run | Fixed (statutory), [WI-011](statutory_corrections.md#other-states) |
| WI | 2015-2018 | 20676-20684 | The joint brackets are not indexed for inflation | Fixed (statutory), [WI-001](statutory_corrections.md#other-states) |
| WI | All | 20558 | Mortgage interest is truncated to whole dollars | Fixed (statutory), [WI-010](statutory_corrections.md#other-states) |
| WV | 1977-1986 | 19731 | Itemized deductions are reduced by a slot that is never filled, so state income tax stays in | Fixed (statutory), [WV-003](statutory_corrections.md#other-states) |
| WV | 1984-1986 | 19766 | An 18-row head-of-household table is read as 17 rows, so the 13% rate is never reached | Fixed (statutory), [WV-001](statutory_corrections.md#other-states) |
| WV | 2011 | 19608 | The family credit limit for five exemptions is $28,170, probably meant to be $26,170 | Fixed (both modes) |
| WV | All | 19708-19709 | `law.le.1983.and.law.ge.1987` is never true, so the disability deduction never applies | Fixed (statutory), [WV-002](statutory_corrections.md#other-states) |

## How the program runs

These come from how the executable processes a file, not from any tax formula.

| Issue | Line | What TAXSIM does | taxsim-py |
|:--|:--|:--|:--|
| Results depend on neighboring records | | Values in shared memory are not reset between records. A Maryland 1983 case ($200,000 of wages and $100,000 of long-term gains) gives $14,785 as the first row and $13,035 as the second | Computes each record independently |
| Extra $1 of household income | 21324 | Every record after the first has $1 more household income (this matters for Michigan's Home Heating Credit) | Adds the $1 in taxsim mode; statutory mode drops it, [STATE-HHY-002](statutory_corrections.md#rules-shared-by-several-states) |
| Single-precision constants | | Constants without `d0` carry round-off, so marginal rates at exact thresholds are slightly off (Maine 7.96% instead of 8%) | Exact arithmetic |
| Single-precision two-earner deduction | 23473 | 10% of $20,000 comes out $2,000.00003, which can move AGI under a round threshold (about $57 of tax for a 1986 New York couple) | Exact arithmetic |
| State detail from the marginal-rate run | 21336-21400 | With `idtl=2`, the state worksheet is printed from the one-cent marginal-rate run, so it shows the extra cent | Same as TAXSIM in taxsim mode; statutory mode reports the base calculation, [STATE-HHY-002](statutory_corrections.md#rules-shared-by-several-states) |
| Itemizing ties | 21662-21690 | Exact ties between itemizing and the standard deduction are settled by round-off | Keeps the standard deduction on exact ties |

## Entries that are not errors

These were logged as possible errors and, on review, match the law.

| Entry | Why it stands |
|:--|:--|
| Head-of-household code 3 never occurs (several states) | Code 3 always appears with code 6 and sets separate-return halving, so it is a legacy separate-return code. Iowa's larger head-of-household standard deduction is Iowa law. |
| Minnesota 1977-1986 state income tax add-back dropped | The 1977-1986 Minnesota itemized deduction already excludes state income tax, so the add-back would count it twice. |
| North Carolina detail output shows federal exemptions less the state adjustment (through 2011) | That difference is the exemption North Carolina allows. |
| South Carolina $15,000 age deduction (1999 on) whatever the income | The deduction is reduced by the retirement deduction, so the total is $15,000 per taxpayer 65 or older. |

