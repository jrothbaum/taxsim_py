# Pending issues

Open work and known gaps. Every TAXSIM error that statutory mode treats differently is in
[`statutory_corrections.md`](statutory_corrections.md). Line numbers refer to `taxsim_2026_11_09.f`, the oracle for all years.
Parameter differences between TAXSIM generations are in
[`oracle_archive/newer_source_parameter_audit.md`](oracle_archive/newer_source_parameter_audit.md).

## Open work

- **Tag the validated release.** No beta tag exists.
- **Add boundary and property tests.** Generate cases immediately below, at, and above brackets, phaseouts, caps, filing
  thresholds, age cutoffs, and itemization ties. Add invariants such as row-order preservation, serial and parallel
  equivalence, nonnegative taxes where the law requires them, and stable results under irrelevant-column changes.
- **Stress unusual but valid inputs.** Cover very large incomes, losses, zero and negative components, dependent filers,
  married-separate returns, conflicting dependent-age/count inputs, nulls, mixed states and years, and large batches.
- **Add parameter provenance.** Give each YAML/CSV policy table a source citation and a review status.
- **1981 detail field.** TAXSIM `v26` differs on about 1,690 CPS units while taxes mostly agree. Either fix the semantic
  `federal_alternative_minimum_taxable_income` output or document the executable's worksheet behavior.
- **Publish reproducible benchmark and parity artifacts** as compact machine-readable results.
- **`opt1/opt1v/opt2/opt2v`.** Accepted but ignored; decide whether to implement the eight documented switches, and warn or
  reject nonzero values until then.
- **Dataframe adapters.** Consider more only once the Polars API and output names are stable.

## Other open items

- **Alabama and Maine 2021 source differences** (the NIIT worksheet and the
  property tax fairness credit row): `compare_cps.py` names them, and they remain
  candidates for statutory-mode switches.
- **California capital loss:** real law floors the $1,000 cap further for very low
  income filers at a pre-capital-gain income measure (`taxy`) that is not
  replicated. Several other states (for example `nm.py` and `ma.py`) rebuild the
  federal gain on their own instead of using `federal_capital_gain_in_agi`; not
  confirmed broken, not checked.
- **Small residuals needing verification:** Michigan tiers for filers born
  before 1953, Maryland high income,
  Arkansas joint interest-only returns, Minnesota joint at $750,000, California
  joint at $600,000 with children, Indiana 2024 joint, New York 2023 low-income
  two-child rows.
- **Not modelled:** deductions for tips, overtime and car-loan interest (no TAXSIM
  input); Washington's capital gains tax (WA-001); the Minnesota homeowner and
  renter refund tables are TAXSIM-era approximations not validated against
  PolicyEngine.
- **AMT for young filers:** the cap follows TAXSIM's source and applies to anyone
  under the age limit; the real kiddie-tax rule is narrower (children with
  unearned income claimed as dependents).
- **Small data discrepancy:** the 2023 married-separate 15%/20% capital-gains ceiling
  is $276,925 here against $276,900 in PolicyEngine-US.

## Agreement with TAXSIM on every CPS household

`scripts/cps_full_state_check.py` runs all 84,121 CPS ASEC tax units (the 2011 file, aged to each year) through the
compiled TAXSIM and taxsim mode for each state and every year 1977-2026: 4,206,050 state-year households
(`docs/data/cps_full_state_check/summary.csv`, with the differing households in `gaps_<year>.parquet`).
**2,181 differ by more than $1 in state tax** (99.95% agree) and 3,154 in federal tax.

**1977-2021 (not traced):** 1,952 households differ in state tax, 14 to 154 a year from 1990 on and 59 to
89 a year in 1977-1982. 32 of the 51 jurisdictions have some. The largest clusters are California (274; 77 of them in
2018), Montana (222), Maine (206; 105 in 2020), Vermont (197; mostly 1979-1982), Wisconsin (127; 30 in 2021), Minnesota
(124), Missouri (109; 36 in 2017), New York (97), Massachusetts (88; 57 in 1977-79) and Virginia (88). Federal tax differs on 94
to 166 households a year in 1977-1986 and 10 to 44 a year after, except 2008 (1,079: the recovery rebate described
below). No cause has been identified for any of these.

**2022-2026:** **229** households differ by more than $1 in state tax (2022: 31, 2023: 16, 2024: 29,
2025: 48, 2026: 105), and 29 of the 51 jurisdictions have none in any year. Federal tax differs on 81 (2026: 40, most of
them the 2026 build's unindexed values listed below). What remains, by state (households, largest gap):

| State | Years | Households | What it is |
|:--|:--|:--|:--|
| CA | 2022, 2025-26 | 6; 33-39 | $1-3 differences; not traced |
| NY | 2025-26 | 3-4, about $800 | single filers with dependents and high income (listed below) |
| DC | 2023-26 | 1-11, up to $416 | joint returns with unemployment compensation (listed below) |
| MN | 2022-23, 2026 | 1; 9 | one 2022 and one 2023 household (up to $1,006); 2026 spouse self-employment, $3-51 |
| NJ | 2026 | 21, $2-4 | low-wage single filers; not traced |
| VT | 2024 | 17, $2-3 | not traced |
| WI | 2022-26 | 2-3, up to $295 | not traced one by one; earned income credit children through 2022 |
| ME | 2022-26 | 1-2, up to $151 | property tax fairness credit; not traced further |
| MT | 2022-23 | 5; 2, up to $39 | not traced |
| ID | 2022 | 6, $10 | a credit amount; not traced |
| SC | 2026 | 5, up to $27 | not traced |
| MI, MS, RI, AL, GA, IN, MD, CT, MA, WV, IL | scattered | 1-3 each | single households, mostly $3-$30; Illinois 2022 ($240), Maryland 2026 ($66) and West Virginia 2026 ($77) are the largest |

### Independent model commands

`uv run --group test pytest tests/test_independent_models.py -q` runs the small
synthetic suite, and `uv run --group test scripts/compare_independent.py <cps
dir> --tax-year 2024 --sample-size 1500 --calculation-mode statutory` runs a
timed CPS sample against PolicyEngine and Tax-Calculator. The harness calls
PolicyEngine's `PolicyEngineRunner` directly, not `StitchedRunner`, which
delegates years before 2021 to NBER TAXSIM. TAXSIM generations and archived
executable outputs are described in [`oracle_archive/`](oracle_archive/README.md).

## The 2026 build stops on some records

The shipped source stops the whole run, with no output, on two kinds of record.

| State | Years | Input | Cause | Handling |
| --- | --- | --- | --- | --- |
| NJ | 1977-2020 | Dependent filer (`mstat=8`) | `mstat=8` sets the taxpayer count `d(7)=0` on purpose, and NJ's pension exclusion table `penpoa(2,4,2)` is read at that index, before the array, so `tablki` stops (229). The source defines no answer | An error in TAXSIM. `scripts/oracle.py` leaves these records out and prints `TAXSIM ERROR` for each |
| WV | before 2016 | Taxpayer 65 or older with a property tax credit | The senior credit calls `povertf`, which stops for years before 2016. Its result only feeds an `if` whose body is commented out | `scripts/build_oracle.sh` makes `povertf` return the 2016 line; no effect on the tax |

## Uninitialised variables in the shipped source

| State | Years | Cause | Handling |
| --- | --- | --- | --- |

## TAXSIM's answer for a record depends on the records before it

Run one state at a time, TAXSIM is deterministic; mixed with other states, the same Maryland 1977-1984 households
differ by about $90 each (192 of 200 in a test), though they agree when run alone. Some state routine reads a variable
left over from the previous record's state. `scripts/build_comparison_report.py` therefore runs the executable one
state and year at a time. Not traced to a line yet.

## The state validator prints `staxbc` as 0

`scripts/validate_states.py` fails about 88% of cases in every state when it compares worksheet columns, because the 2026 build prints the state tax before credits (`staxbc`, its `taxbc`) and `engine/detail.py` still outputs 0. `TAXSIM_VALIDATE_DETAIL=0` skips the worksheet columns: Georgia then has 1 failure in 4,300 (a zero-tax knife edge from the build's extra cent of AGI). Fixing it means setting tax before credits in each state calculator.

## Federal AGI carries 1 cent more in the oracle

The 2026 build's federal AGI is the inputs plus $0.011, not plus $0.001: it adds $0.01 to wages (even with no wages). The Python federal AGI has only the $0.001 dividend adjustment. Federal validation tolerates the cent, but state tables read by interpolation (Connecticut's personal credit) amplify it into differences of up to 4 cents; those cases are among the Connecticut results that still differ.

## Federal AMT, 2003-2010, large married separate returns

Python's federal AMT for a 2005 married separate return with $350,000 of wages, $15,000 of property tax and $20,000 of mortgage interest is $5,816 against the build's $5,676; Connecticut's minimum tax reads it (the case differs by $400).

## Virginia, remaining differences

Virginia 2022-2023 low-income returns (the Form 760ADJ choice between the low-income credit and the earned income credit, the $250 rebate), the aged couple case of 1989-1999 and married separate itemizers in 2019-2022 still differ by small amounts. Virginia's `x14` (age deduction for 2021) is never set in other years; the oracle build should set it to 0.

## North Carolina child care expense table overwritten (patched in the oracle build)

`nctax77` (1995-2017) writes `child=min(statax,child)`, a typo for `chld`, which assigns to the whole per-child care-expense table and keeps the capped values for every later record in the run, so the child care credit depended on earlier records. `scripts/build_oracle.sh` patch 4 caps `chld` instead. TAXSIM itself still has the bug.

## Delaware uninitialised variables

`detax77` reads `stdedh`, `stdedw` (before 2000) and `stdx` (before 1987) without setting them. With the compiler used for the oracle they behave as $3,250, $3,250 and twice the personal exemption; another compiler or optimisation level may differ. Python reproduces those values in TAXSIM mode.

## New York 2022 on: married separate returns index outside the tax tables

`nytax22` sets `m=filing(mst,1,2,3,4)`, which is 4 for married filing separately, but `tab22`, `wkst22` and the 2024 tables have three filing-status slices, so the tax comes out as 1e20 or garbage. `scripts/build_oracle.sh` (patch 5) uses the single table for them. TAXSIM itself still has the bug.

## New York 2021: worksheets chosen by the wrong filing code

`nytax21` tests the filing index `mri` (single 1, head of household 2, joint 3) against 2 (joint) and 4 (head of household), so joint returns use the single worksheets and head of household returns the joint ones. Python reproduces this in TAXSIM mode (`pick_new_york_worksheet_by_filing_status`).

## Georgia: retirement exclusion ceilings are uninitialised before 2021

`gatax77` sets `rtmaxt`/`rtmaxs` only for law 2021 and later, so the retirement income exclusion for 1989-2020 uses whatever those locals held from an earlier record: the same record gives a different tax depending on which records ran before it in the file (an isolated 1995 aged single pays $1,340, but $500 in a file that already ran a 2021 record). Python keeps the statutory exclusion; the aged Georgia validation cases are order-dependent and cannot be matched.

## District of Columbia 2014-2018: property credit cap read from outside a table

`dctax77` ends with `pcred=max(0,min(pcred,pcreda(5,law)))`, but `pcreda` is dimensioned for 2019 on, so 2014-2018 read whatever memory precedes it. With the compiler used for the oracle that is about $355, $349-341, $353-345, $279-253 and $0 in 2014-2018 (the value moved by a few dollars with the kind of return). Python uses the amounts the validation cases show.

## Federal 2008 recovery rebate at very low incomes (open)

The 2008 rebate in the Fortran tests `agi > zbr`. When the itemize-forced pass (zero bracket amount zeroed) pays the rebate and
the standard pass does not, TAXSIM keeps the itemize pass, so a filer with wages of $1 gets $300 per child. The Python federal
calculation runs one pass for federal-only returns and gives nothing. About 26 rows of `scripts/validate_federal.py` for 2008
(plus a few in 2009-2010 through the same mechanism).

## Federal 2026 values the TAXSIM build does not index

Its SALT cap stays $40,000 with the phaseout starting at $500,000 (the law: $40,400 and $505,000 for 2026), and its AMT
exemption phaseout rate stays 25% (the law: 50% from 2026). TAXSIM mode keeps them
(`index_salt_cap_from_2026`, `phase_out_amt_exemption_at_half_rate_from_2026`). Its 0% capital gains threshold is the 2023
amount carried by its price index (about $450 above the published one in 2026).

## 2026 state law not yet modeled or not confirmed

See `docs/state_2026_parameters.md` for the full verification table. Open items:

- Hawaii's higher personal income tax on income over $1 million (enacted July 2026) and Arizona's HB 4168 items beyond the
  dependent credit (SALT cap on itemizers, adoption subtraction, charitable standard deduction) are not modeled.
- Colorado: the Family Affordability Credit is off for 2026, but whether the separate child tax credit still applies at the
  same amounts is unconfirmed, and the 2026 federal-deduction addback cap ($1,000 / $2,000) comes from PolicyEngine only.
- District of Columbia: the new $1,000 child tax credit is in the model; its survival after the congressional
  disapproval of DC's tax decoupling is unconfirmed.
- New Jersey's $1,250 child credit rests on one secondary source.
- Amounts marked Derived in the table (Rhode Island property credit maximum, New Mexico indexed amounts (the state publishes them in January 2027), the Maine head-of-household standard deduction, New
  York supplemental tax tables, the Arkansas 2026 low-income tables) should be replaced when the states
  publish their 2026 schedules.

## Tracing the oracle

`scripts/trace_failures.py <VALIDATE_OUT dir> state=MT year=2025 min=5 limit=5` traces only the worst disagreeing cases of a validation run, one file each (console shows one line per case). `scripts/trace_oracle.py year=2025 state=27 mstat=1 pwages=60000 ...` prints every nonzero federal result `c(i)` and input `d(i)` the NBER code hands a state routine for one household (the patch is in `scripts/build_oracle.sh`; it runs only when `opt1=30, opt1v=999`). Use it before reading a state routine, to see hidden inputs such as `c(201)` or `c(215)`.

- Wisconsin 2023 married separate: a typo (`82.89d9` for `82.89d0`) in the bracket table made the oracle's tax hugely negative. Patched in `build_oracle.sh`.

- **Minnesota exemption phase-out start rounding (2023-2025).** The statutory start is 100,000 times the index times the status multiplier, unrounded (2023 joint 330,975); Minnesota publishes the starts rounded to 50 (330,950; head of household 275,800 against 275,812.50). The itemized-deduction limit start uses the single value and also differs slightly from the exemption start in 2024 (232,500 against 232,550). Effect: a phase-out that begins at most $25 earlier or later. Fix by storing the published starts in the parameters (Python and Rust). Source: Census Bureau parameter workbook TY2025, MN sheet (MNEXLIM, MNSTDLIM).

- **Georgia retirement exclusion, earned income.** Georgia Comp. R. & Regs. 560-7-4-.02 counts net business income (self-employment) as earned income for the capped earned-income part of the exclusion. The statutory side counts wages only in every year. TAXSIM counts self-employment from 2024 only. Statutory mode should add self-employment earnings (and the Georgia-source proration) for all years from 1989; not changed yet because the statute's treatment of each business item has not been checked against Form 500 Schedule 1.

- **New York 2025 and 2026, single filers with dependents and high income.** In the full CPS check the compiled program standard-deducts where we itemize (our federal itemize choice picks itemizing because New York's tax falls more than the federal tax rises; the compiled program picks the standard deduction), a gap of about $800 for AGI above 150,000. Not traced to the Fortran rule yet.
- **District of Columbia 2026, joint returns with unemployment compensation.** About 12 to 30 dollars: the compiled program's split of income leaves the total unemployment input (`d(82)`) undeflated in a projected year. A first attempt to carry that through `split_agi` made it worse and was reverted.

- **Minnesota 2026, spouse self-employment.** Nine CPS households with large spouse self-employment income still differ by 3 to 51 dollars in 2026; the cause is not traced.
- **New Jersey 2026, low-wage single filers.** About 21 households differ by 2 to 4 dollars in 2026 only; not traced.
- **Wisconsin earned income credit children, through 2022.** The credit counts dependents; TAXSIM counts children differently.

## TAXSIM errors not yet run through the compiled program

Statutory mode differs from TAXSIM on these, read from TAXSIM's source, but no real or random household reaches the rule, so the compiled program has not confirmed them. They are not on the errata page until confirmed (`scripts/confirm_logic_switches.py`, `scripts/confirm_unreached_switches.py`).

- **All 182 → ?** Detailed output `v42` (`ssearn`, earnings subject to the Social Security wage base) is never assigned, so it is always 0 Statutory side: Statutory, FED-PAYROLL-006: reports both earners' wages plus 92.35% of net business income, each capped at the wage base. `report_social_security_taxable_earnings`
- **1987-1997 25883-25910 → ?-?** The nonrefundable credit total that states read (`comnew(58)`) is tax before credits when the elderly credit exceeds it, otherwise 0. A 68-year-old with $8,000 of wages in 1995 has a $712.50 credit and is reported $97.50, so states that read it (LA, UT, OR, ND, AL, AZ) get the wrong federal base. Found by probing the executable Statutory side: Statutory, FED-INCOME-001: the actual credit (`federal_elder`). `count_pre_1998_nonrefundable_credits_correctly`; code: `federal._net_tax`
- **1990 on 25312-25324 → 42874-42886** The AMT exemption cap for young filers (under 14; 18 in 2006-2007; 24 in 2008-2017) applies to anyone that age Statutory side: Statutory, FED-INCOME-008: dependent filers only; IRC 59(j) covers children subject to the kiddie tax. `cap_amt_exemption_for_dependents_only`
- **AL 1982 on 2026: 425-644** The itemized payroll tax deduction is the taxpayer's own payroll taxes (`c(190)`), not half of the reported total Statutory side: Statutory: half of FICA. `deduct_half_of_fica_in_alabama_itemized_deduction`
- **DC 2000 on dctax** The dependent-filer low-income credit's table chain tests the 2000 table first, so every year uses the 2000 rates Statutory side: Statutory, DC-005: the year's rates (2000-2017). `use_current_rates_in_dc_low_income_credit`
- **DC 2007 2026: 7016-7580** The married-couple split uses the 2006 rates Statutory side: Statutory, DC-004: 2007 rates. `use_current_rates_in_dc_earner_split`
- **KS 2021 on 2026: 11164-11668, 11680-11827** Itemizing is no longer allowed Statutory side: Statutory, KS-001: itemizing follows the federal choice in every year (K-40 Schedule A). `allow_kansas_itemizing_after_2020`
- **MN 1998 9937-9940 → 18443-18446** Three statements overwrite each other in the childless Working Family Credit, so $4,000 of earnings gets $70.20, above the $51 maximum Statutory side: Statutory, MN-003: phase-in, plateau and phaseout in order. `order_minnesota_1998_working_family_credit`
- **NY 2006-2022 2026: 24729-26295, 44037-44256, 26381-26752** The Empire State child credit counts children under 4 (TAXSIM has no ages) Statutory side: Statutory, NY-001: children under 4 do not qualify before 2023 (Tax Law 606(c-1)); `children_under_4` is removed from the 2018-2022 rebuilt credit (the 2006-2017 credit still counts them). `exclude_children_under_4_from_ny_empire_credit`
- **NY 2018 on 14357-14405 → 25286-25334** Itemized deductions can be reported negative (tax is unaffected) Statutory side: Statutory, NY-007: floored at 0. `floor_new_york_itemized_deductions`
- **NE 1988-2017 `netax77`** The standard deduction stays the federal figure when the federal return itemizes (the state tax makes most high earners itemize), so the state deducts the larger of the two Statutory side: Statutory: the standard deduction only when the federal return takes it. `zero_nebraska_standard_deduction_when_federal_itemizes`
- **WI 1999 20745 → 35773** The itemized-deduction credit is zeroed on the federal standard-deduction run Statutory side: Statutory, WI-011: kept. `keep_wisconsin_1999_itemized_credit`
- **WI All 20558 → ?** Mortgage interest is truncated to whole dollars Statutory side: Statutory, WI-010: cents kept. `keep_wisconsin_mortgage_interest_cents`
- **NY 2021 2026: 44037-44256** The tax computation worksheet ratio (AGI over the start, divided by $50,000) is not rounded to four decimals Statutory side: Statutory: rounded to four decimals (Form IT-201 worksheets). `round_new_york_recapture_ratio_per_statute`
- **AZ 2021-23 2026: 731-1127** A short-term loss variable is never set (-9), so every return deducts 25% of its long-term gains plus $9 Statutory side: Statutory: 25% of gains. `initialize_arizona_short_term_loss`
- **DE 2000 on 2026: 6384-6831, 6843-7015** A spouse aged 60 or over with low earnings gets the extra $2,000 pension exclusion added to the taxpayer's line, replacing it Statutory side: Statutory: added to the spouse's. `credit_delaware_spouse_low_income_exclusion_to_spouse`
- **AK 1977-1978 645-730 (699: `law.eq.1979`)** Alaska tax is calculated only for 1979; the routine does nothing for 1977-1978 and after (its header: the tax was repealed in 1980) Statutory side: Statutory: taxed under Alaska's own schedule. `tax_alaska_before_1979`
- **WI 2023 on (projected years) 36316** The exemption is $700 times the federal exemption count `c(68)` plus $250 per taxpayer 65 or older (`exemp=700*c(68)+250*d(9)`), and `c(68)` is deflated in a projected year Statutory side: Statutory: the actual count. `deflate_wisconsin_exemption_count_per_statute`
- **ID 2025 on (shown for projected years) 9803-9805** From law 2025 the standard deduction is the federal one, `stded=c(3)`, which already holds the aged amount (2024 and earlier use Idaho's own worksheet) Statutory side: Statutory: adds the aged amount. `add_idaho_aged_standard_deduction_in_projected_years_per_statute`
- **CA 2018-2020** The state and local tax put back above $10,000 uses the state tax estimate without its cents Statutory side: Statutory: with the cents. `keep_california_salt_addback_cents_per_statute`
- **UT 1988** The credit for small tax (12.5% up to $10 when tax is $80 or less) comes off twice Statutory side: Statutory: once. `take_utah_small_tax_credit_once`
- **ME 1988** The standard deduction and exemption credits of 1988 are never taken off the tax Statutory side: Statutory: credits. `credit_maine_1988_standard_deduction_and_exemptions`
- **WI 1988-1998** The separate-return standard deduction's middle reduction is measured from $5,000, not the $4,750 where the range starts Statutory side: Statutory: from $4,750. `start_wisconsin_separate_standard_deduction_tier_at_its_range`
- **DC 2024 on** The childless credit needs a reported age 25 to 64; the itemized deduction adds back the whole state tax estimate Statutory side: Statutory: the age tests as the form has them; capped state tax. `allow_unreported_ages_in_dc_childless_credit_from_2024`, `cap_district_of_columbia_state_tax_deduction_from_2024`
- **CO 2024 on** The earned income credit is not reinflated in a projected year Statutory side: Statutory: reinflated. `reinflate_colorado_earned_income_credit_per_statute`
- **OK 2024 on** The state earned income credit phase-out reads the undeflated federal AGI in a projected year Statutory side: Statutory: deflated income. `deflate_oklahoma_earned_income_credit_income_per_statute`
- **DC 2025 on** The itemized deduction adds back the state and local tax above the phased-down federal cap Statutory side: Statutory: `cap_district_of_columbia_state_tax_deduction_from_2024`
- **IA 2019 on 2026: 10494-11050** When the separate-spouse tax is the lower, each spouse's personal credit is limited to their own tax and the dependents' credits are handed out alternately, each limited to the taxpayer's tax left after the personal credit Statutory side: Statutory: the full credits against the combined tax. `credit_iowa_dependents_in_full_on_separate_spouse_returns_per_statute`
- **AL, MO 2026** Payroll taxes deducted are deflated in a projected year; Alabama deducts the deflated federal tax Statutory side: Alabama: `deduct_alabama_federal_tax_components_per_statute`
- **CT 2024 on** The property tax credit is available to everyone and its phase-out steps round up Statutory side: Statutory: continuous steps. `round_connecticut_property_credit_steps_continuously_per_statute`
- **ME 2026** The sales tax fairness credit counts children as the deflated exemption count less taxpayers, truncated (loses a child) Statutory side: Statutory: children. `count_maine_fairness_credit_children_before_deflation_per_statute`
- **Federal 2024 on** A dependent filer's child tax credit phases out from zero AGI (the $200,000 is multiplied by the number of taxpayers, which is 0) Statutory side: Statutory: $200,000. `start_child_tax_credit_phaseout_at_statutory_threshold_for_dependent_filers`
- **Wisconsin 2022** A separate return is taxed on the joint schedule at twice the income, then halved (the published separate brackets are not exact halves) Statutory side: Statutory: published separate brackets. `use_wisconsin_published_2022_separate_brackets_per_statute`
- **Massachusetts projected years** The payroll tax deduction is computed on deflated payroll tax; S corporation income is not deflated Statutory side: Statutory: n/a. `deflate_massachusetts_payroll_tax_deduction_per_statute`
- **Maine projected years** The earned income credit before the age test is deflated Statutory side: Statutory: n/a. `deflate_maine_earned_income_credit_per_statute`
- **Missouri all** A joint return's income is split between spouses after the capital gains subtraction Statutory side: Statutory: before. `split_missouri_income_before_capital_gains_subtraction_per_statute`
- **New Mexico 2025 on** A dependent filer's standard deduction is the full federal amount Statutory side: Statutory: the limited amount. `limit_new_mexico_dependent_filer_standard_deduction_per_statute`
- **Federal 2022-23 2026 federal child credit** The nonrefundable child credit is capped at three times the refundable maximum per child; with three or more children the payroll-tax alternative replaces the earnings formula. Oregon's federal tax subtraction reads the credit total before that cap (`reported_nonrefundable_credits`), Alabama's reads the capped split Statutory side: Statutory: no cap, earnings formula only (Form 8812 for 2022-23 not re-checked). `cap_nonrefundable_child_credit_at_three_children_refundable_maximum_per_statute`
- **Ohio 2021 on 2026: 28593-28700** The $50 senior credit and the retirement income credit are tested on federal AGI less exemptions before the business income deduction (strictly under 100,000) Statutory side: Statutory: tested on income after the business deduction (not checked). `test_ohio_senior_credit_on_income_after_business_deduction_per_statute`
- **Federal and states 1994 on 2026: 38387 (`eicage`)** A joint return with no spouse age gets a spouse of 40; a childless return passes the age test when either age is 25-64 (19 and older in 2021) or both are unreported Statutory side: Statutory: only a taxpayer under the minimum age disqualifies it, and an unreported spouse age passes. `leave_unreported_spouse_age_out_of_eitc_age_test_per_statute`
- **Delaware 2026 (projected year) 2026: `splitagi`** The spouse's self-employment input is not deflated (`d(216)`) while the total is, and the self-employment taxes used in the split are deflated Statutory side: Statutory: all amounts deflated alike. `deflate_delaware_spouse_self_employment_per_statute`
- **All 24561 → ?** Social Security provisional income adds `transfers` (welfare, workers' compensation), which the input reader stores where tax-exempt interest used to be (`data(41)`) Statutory side: Statutory, FED-INCOME-003: adds nothing; the law adds tax-exempt interest, which TAXSIM has no input for. The same applies to the Iowa (2014 on) and Montana worksheets. `exclude_transfers_from_provisional_income`
- **1998 on** 25733 → 43287, 25758 → ?, 25972 → 43520 Statutory side: The child tax credit adds `.05`, a fraction, to a rate in percent: +0.05 points
- **All** 27860-27880 → ?-38650 Statutory side: The EITC slope is set before the disqualification tests, so it stays in the rate when no credit is paid
- **Household income and state detail, all years 21324 → ?, 21336-21400 → 47626-?** Every record after the first has $1 more household income (this matters for Michigan's Home Heating Credit), and with `idtl=2` the state worksheet is printed from the one-cent marginal-rate run Statutory side: Statutory, STATE-HHY-002: drops the $1 and reports the base calculation. `drop_household_income_record_adjustment`, `report_base_state_worksheet`
- **Several, 2022-2023 59-71 → ?-?** For projected years the dispatcher deflates federal slots 1-98 but passes higher slots undeflated and skips 26, 65, 72 and 73. Massachusetts' payroll deduction, Kentucky's child care credit, self-employment tax, and federal total income in Maine, West Virginia and Indiana mix 2021 and current dollars Statutory side: Projection
- **MN 2017-2020 10164-10174 → ?-?** The child care credit is the whole federal-based credit until AGI is $50,000 over the threshold, then the lesser of it and $600 ($1,200) less 5% of the excess Statutory side: Statutory, MN-006: the lesser of the credit and $600 ($1,200) less 5% of the excess (Minn. Stat. 290.067 subd. 2b). `limit_minnesota_child_care_credit_by_maximum_after_2017`
- **NY 2021 14008 → ?** The married worksheet adds back $526 instead of $474; there are no worksheets for the 10.3% and 10.9% brackets Statutory side: Statutory: from the IT-201 instructions; taxsim mode keeps TAXSIM's worksheets
- **WI 1987 on 20273-20276 → ?-35220** The capital gain exclusion can be negative and raise income Statutory side: Statutory, WI-006: floored at 0. `floor_wisconsin_capital_gain_exclusion`
- **RI 2011 on** The deduction phaseout is applied in 20% steps and again smoothly (78.4% kept at $180,000 instead of 80%) Statutory side: Statutory, RI-004 (2011-2021): steps only. `use_rhode_island_deduction_phaseout_steps_only`
- **WV All** `law.le.1983.and.law.ge.1987` is never true, so the disability deduction never applies Statutory side: Statutory, WV-002: under 65 with taxable Social Security. `allow_west_virginia_disability_deduction`
- **Dependent standard deduction minimum, 2024 (unexplained).** The compiled program deducts $1,350 from a dependent filer's interest income in 2024 (the law is $1,300), but the federal table (`zbrmin`, lines 41811-41813) holds $1,100 for every year 2019-2024, and 2025 and 2026 give $1,350 (the law). The path the executable takes for 2024 is not traced, so 2024 is not on the errata page.
