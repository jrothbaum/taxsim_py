# Pending issues

Status as of 2026-10-12. Update 2026-11-09: the new NBER source `taxsim_2026_11_09.f`
is the oracle for all years; see "The 2026 build" in
[`statutory_corrections.md`](statutory_corrections.md). Federal 1960-2023 is mostly
re-baselined (about 300 of 48,700 cases differ); the 2022-2026 states and 2024-2026
federal law are next.

Line numbers refer to `taxsim_2024_09_21.f`, and "the executable" means `taxsim2024.exe`.

The current NBER source/executable is the preferred TAXSIM reference. The
source and executable named above serve as compatibility oracles. Parameter
differences are documented in
[`oracle_archive/newer_source_parameter_audit.md`](oracle_archive/newer_source_parameter_audit.md).

## Prioritized roadmap

The calculation engine is ready for a research beta: all 48,713 federal and
278,781 state validation cases pass after explicitly classified TAXSIM errors
and threshold round-off. Realistic CPS comparisons cover every year from 1977
through 2021 (against the compiled TAXSIM) and 2022-2025 (against PolicyEngine),
and all 45 income-tax states run actual law through 2025. Years after 2025
raise an error. The remaining work is primarily a reproducible public release
and further test coverage.

Known TAXSIM bugs that already have a correction are tracked in
[`statutory_corrections.md`](statutory_corrections.md). Statutory behavior is
the public default; backward-compatible TAXSIM behavior is an explicit
testing/replication mode.

### P0: before calling it a public TAXSIM replacement

- [x] **Investigate the remaining CPS tax differences.** Triage each
  state-year cluster in the cross-year sweep as a port bug, a TAXSIM bug,
  expected numerical behavior, or a documented input-model limitation. Add a
  regression case for every resolved formula bug and a named comparator rule
  for every confirmed deliberate divergence.
- [x] **Add independent law-based checks.** For a representative set of
  federal and state years, compare published tax-form examples, statutory
  brackets, and official agency calculators or tables. TAXSIM is an excellent
  regression oracle, but agreement with a program being replaced cannot by
  itself detect errors copied from that program. `tests/test_law_based_checks.py`
  checks `calculation_mode="statutory"` federal outputs directly against IRS
  Rev. Proc. 2023-34/2024-40 and SSA's published wage base, independent of
  both TAXSIM and PolicyEngine. State law is checked against
  PolicyEngine and state forms (see the PolicyEngine comparison doc and
  `statutory_corrections.md`).
- [x] **Add an independent calculator harness.** The test dependency group
  includes PSL Tax-Calculator and PolicyEngine's TAXSIM emulator. The strict
  synthetic matrix compares Tax-Calculator federal and payroll tax from 2013
  onward and PolicyEngine federal and payroll tax from 2021 onward. A sampled
  CPS command provides broader diagnostic comparisons without slowing the
  normal test suite.
- [x] **Inventory PolicyEngine state parameters.** The installed
  `policyengine-us` package exposes dated values through its simple
  `system.parameters` API. `scripts/audit_policyengine_state_parameters.py`
  records the available 2022-2025 state trees and candidate paths in
  [`docs/policyengine_state_parameter_audit.md`](policyengine_state_parameter_audit.md).
  The inventory confirms that this is a source for state-law updates, but not
  a safe bulk YAML conversion: state structures differ, Alaska and Tennessee
  have no PolicyEngine `tax.income` subtree, and parameter values do not prove
  that the existing TAXSIM-shaped formula has the same meaning.
- [x] **Automate the full validation matrix.**
  `scripts/validate_all.py` runs the API/unit tests, vectorization checks,
  federal validation, state validation, and an optional deterministic sampled
  CPS independent-model smoke matrix. It fails on a nonzero gate and prints
  each gate's output; the individual validators retain detailed artifacts
  where applicable.
- [x] **Continuous integration: skipped.** Tests and the validation scripts are
  run locally before each release.
- [x] **Document the supported contract.** The README's "What is supported"
  section covers years, inputs, accuracy, and what is not modelled.
- [x] **Make the repository releasable.** The README has installation, examples
  and the supported contract, and the project is MIT licensed. No contribution
  guidance or versioning policy is planned.
- [ ] **Tag the validated release.** No beta tag exists.
- [x] **Archive upstream oracle metadata.** Record the executable build stamps,
  hashes, output schemas, validation summaries, and representative outputs
  needed to reproduce each upstream generation even if its download disappears.
  `scripts/archive_oracle_generations.py` records this for every generation
  present in the checkout, using a small deterministic synthetic matrix so it
  needs no cached CPS data; see `docs/oracle_archive/` for the dated JSON
  report and per-generation representative CSV outputs. The large-N CPS diff
  counts above remain the separate validation-summary evidence; this archive
  does not regenerate them. Re-run it if a new build shows up or an existing
  one is about to disappear.

### P1: confidence and maintainability after the beta

- [x] **Test every actual-law year with realistic data.** CPS validation covers
  every year from 1977 through 2021 against the compiled TAXSIM and
  2022-2025 against PolicyEngine (results below).
- [ ] **Add boundary and property tests.** Generate cases immediately below,
  at, and above brackets, phaseouts, caps, filing thresholds, age cutoffs, and
  itemization ties. Add invariants such as row-order preservation, serial and
  parallel equivalence, nonnegative taxes where the law requires them, and
  stable results under irrelevant-column changes.
- [ ] **Stress unusual but valid inputs.** Cover very large incomes, losses,
  zero and negative components, dependent filers, married-separate returns,
  conflicting dependent-age/count inputs, nulls, mixed states and years, and
  large batches. Record which inputs intentionally follow TAXSIM's permissive
  behavior instead of tax-form validation rules.
- [ ] **Add parameter provenance.** Give each YAML/CSV policy table a source
  citation or source note and a review status. Preserve corrections as
  year-specific parameter changes or narrowly scoped mechanics, with a test
  covering the corrected behavior.
- [x] **Validate 2022-2025 state law.** All 45 income-tax states run actual law
  for 2022-2025 in statutory mode. PolicyEngine comparisons cover a synthetic
  grid with explicit child ages, an age-65+ grid, and CPS samples for each year (see
  [`docs/policyengine_recent_state_comparison.md`](policyengine_recent_state_comparison.md)).
  Parameter checks must account for tables held at their last entry,
  TAXSIM's `tablki` interpolation of statutory step schedules, and the
  $0.001 AGI adjustment that can change tiers at exact thresholds.
- [x] **Track residual state differences.** The remaining-differences table
  is in the PolicyEngine comparison doc. Oklahoma credit details, Indiana 2022
  joint returns, Kentucky's low-income credit, Arkansas recapture, Connecticut tiers,
  Arizona $6, Ohio tier at $80,000, and California and Illinois at $250,000
  need individual verification.
- [x] **Provide state law through 2025.** Every income-tax state is covered.
  Provisions TAXSIM has no input for are listed in the comparison doc.
- [ ] **Resolve the 1981 detail-output cluster.** Determine why TAXSIM `v26`
  differs on about 1,690 CPS units while taxes mostly agree. Either fix the
  semantic `federal_alternative_minimum_taxable_income` output or document the
  executable's worksheet behavior and classify it explicitly.
- [ ] **Publish reproducible benchmark and parity artifacts.** Save compact
  machine-readable results for the validation matrix, CPS comparisons, and
  performance benchmarks so users can evaluate a release without trusting a
  prose claim.

### P2: explicitly deferred or optional

- [x] **Cover negative capital gains.** Shared regression cases for Alabama,
  California, Connecticut, Illinois and Hawaii are in `tests/state_new_inputs.py`.
- [ ] Decide whether to implement the eight documented
  `opt1/opt1v/opt2/opt2v` switches. Until then, warn or reject when callers
  supply nonzero option values instead of silently ignoring them.
- [x] Projected state years: statutory mode uses actual law through 2025, and
  years after 2025 raise an error. Taxsim mode keeps TAXSIM's projection only to
  reproduce the compiled model.
- [ ] Consider additional dataframe adapters only after the Polars API and
  semantic output names are stable; TAXSIM-compatible `vNN` names should
  remain an optional final rename.

## Open items

### Recent TAXSIM errors

Every apparent TAXSIM error, with how the port handles it, is listed in
[`statutory_corrections.md`](statutory_corrections.md); add new ones there. Errors
found most recently, all included there:

- **Federal, 2024 onward:** the compiled TAXSIM refuses to run past 2023 (a hard
  year check and the `xndxa(1981:2023)` array bound). The port computes real law
  through 2025 and raises an error after.
- **Arizona, 1998 onward (line 1139):** the family income credit lookup caps the
  dependent count at 4, so the $26,575 head-of-household limit for five or more
  dependents (A.R.S. 43-1073) is never used. A 2010 head of household with 5
  dependents and $26,000 of wages gets no credit; the law allows $240. The port
  uses the statute.
- **Indiana, 2009-2010 (lines 6037-6044):** to cap the earned income credit at two
  children, TAXSIM reruns the federal calculation with dependents set to 2 and
  does not restore the federal results. Families with 3 or more dependents get
  federal tax computed as if they had 2. The port reports the correct tax;
  `compare_cps.py` counts these as a known difference.
- **Reported ages:** the executable pays the childless EITC below the minimum age
  (25, or 19 in 2021) and does not cap young filers' AMT exemption. The port uses
  the reported ages.
- **Arkansas, 2004:** TAXSIM reuses the 1977 bracket thresholds ($3,000, $6,000,
  $9,000, $15,000, $25,000). The published 2004 thresholds are $3,400, $6,800,
  $10,300, $17,100 and $28,500
  ([DFA 2004 Indexed Tax Brackets](https://dfa.arkansas.gov/wp-content/uploads/2004brackets.pdf)).
  On $20,000 of Arkansas taxable income the rate schedule gives $780.00 with
  TAXSIM's thresholds and $721.50 with the published ones (before the 3%
  surcharge). The port uses the published
  values in both modes.
- **California AMT exemption, 2001:** TAXSIM repeats the 2000 amounts for head of
  household ($45,692) and married filing separately ($30,461). Schedule P (540)
  2001 line 22 gives $48,114 and $32,075. The port uses the published values in
  both modes.
- **California AMT exemption, 2018:** TAXSIM has $44,685 for married filing
  separately, below 2017's $45,895. Schedule P (540) 2018 line 22 gives $47,685
  (an apparent digit slip). The port uses the published value in both modes.

### Other open items

- **1981 detail field:** TAXSIM `v26` (alternative minimum taxable income) differs on
  about 1,690 CPS units while taxes mostly agree (see the roadmap).
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
  PolicyEngine; 2026 parameters are not entered.
- **AMT for young filers:** the cap follows TAXSIM's source and applies to anyone
  under the age limit; the real kiddie-tax rule is narrower (children with
  unearned income claimed as dependents).
- **TAXSIM options:** `opt1/opt1v/opt2/opt2v` are accepted but ignored; if revisited,
  implement only the 8 documented switches.
- **Small data discrepancy:** the 2023 married-separate 15%/20% capital-gains ceiling
  is $276,925 here against $276,900 in PolicyEngine-US.
- **Outside this repo:** `survey_kit_data/src/survey_kit_data/census/cps_asec.py`
  (line 278) opens the data dictionary with `encoding='latin-1'`; whether that fix
  is committed in its own repository was not checked.

## Validation record

### Full-year CPS sweep (October 2026)

All 104,404 units of the 2011 CPS sample, taxsim mode, against the compiled
TAXSIM, tax years 1977-2021. Units differing for no classified reason:

| Years | Units per year |
|:--|--:|
| 1977-1986 | 31-59 |
| 1987-1989 | 8-10 |
| 1990-1999 | 1-2 |
| 2000-2002 | 18-29 |
| 2003-2006 | 1-2 |
| 2007-2010 | 0 |
| 2011-2020 | 6-14 |
| 2021 | 0 |

Known 1977 difference: 14 Connecticut units differ in federal tax by
$2,000-$19,000 because TAXSIM itemizes Connecticut filers with property tax
(and so loses the 50% cap on earned income) for reasons not yet identified.
The comparator names each deliberate difference (childless EITC age, Minnesota
2019+ head-of-household deduction, New Jersey top bracket, New York 2021+
worksheet, Alabama NIIT, Maine PTFC, worksheet-only detail) instead of counting
it as a failure. Rerun with `uv run scripts/compare_cps.py <cps dir> --tax-year
YEAR --out mismatches.parquet`.

Tax years 2022-2025 (statutory mode, 1,500 sampled units, PolicyEngine). Federal
tax differs on about 6% of units each year, nearly all of it self-employment
income, where TAXSIM gives no 20% business deduction on `psemp` and the port
keeps that convention. State tax differs on 10-11% of units, and what remains is
the list in [PolicyEngine comparison](policyengine_recent_state_comparison.md).

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

## TAXSIM mode against the oracle on CPS households

On CPS ASEC households ([comparison page](comparisons.md)) TAXSIM mode agreed with TAXSIM for 99.1% of households (99.6% after the fixes below),
with a few states at 91-96% for decades. Tracing the Fortran (`scripts/find_cps_gaps.py` lists the failing households,
`scripts/grid_state.py` runs an age and income grid against the executable) found, and fixed, age rules the synthetic
cases never exercised: Georgia's retirement exclusion starts at 62, Iowa's pension exclusion at 55, Michigan's senior
investment deduction has no age test, Pennsylvania exempts pensions from 60, Oregon's retirement credit starts at 62
(rows in [`statutory_corrections.md`](statutory_corrections.md)). On 200 households per state for 1995, 2005, 2015 and
2021, the households that differ fell from 190 to 28 across GA, IA, MI, OR and PA. What remains:

| State | Years | Households that differ |
| --- | --- | --- |
| PA | 1995 on | negative other property income with self-employment income |
| OR | 2015 | a single filer with $60,000 of wages and $40,000 of pensions and no interest (grid case; $194) |
| AR, CO, NY, NC, CA, VT, DE | 2021 and 2025 | fixed in the second pass (see the rows in `statutory_corrections.md`); TAXSIM reads `dep6` as the number of children under 3 when no ages are given (as in Oregon's EITC), and the comparison runs now give TAXSIM mode no extension child counts |
| MA | 1985 and SE households | 1985 (10 of 400) and self-employment households ($2-7) |
| ME | 2005-2021 | the property tax fairness credit amounts (TAXSIM pays $900 where the 2013-2015 table says $600) |
| WV | 2020-2021 | singles aged 66 with interest: $90-97 |
| MD | 2026 only | joint returns with self-employment income or children, $3-11 on four of 1,000 households (2022-25: 0 of 1,000) |
| OK | 1987-2006, 2010, 2021 | one joint household of 38-year-olds with three children and about $27,000 of transfers (tax deducted from the federal credit, $2-$18); 2010 round-number wages of exactly $100,000 sit on TAXSIM's AGI edge for the child care credit (knife edge) |
| KS | 2010 | two households with wages of exactly $35,000: TAXSIM's first AGI is $34,999.999, ours $35,000.011 (the one-cent AGI difference), which flips the earned income credit tier ($180) |
| CT | 2026 | one joint household aged 71 with Social Security of $92,000 and wages (about $10) |
| DE | 1979 | one household whose state difference comes from the federal 1977-81 gap (see above) |
| NY | 1981, 2025 | one household each: 1981 federal tie (itemized pass chosen on state tax alone) and a $3.47 difference in 2025 |

## The state validator prints `staxbc` as 0

`scripts/validate_states.py` fails about 88% of cases in every state when it compares worksheet columns, because the 2026 build prints the state tax before credits (`staxbc`, its `taxbc`) and `engine/detail.py` still outputs 0. `TAXSIM_VALIDATE_DETAIL=0` skips the worksheet columns: Georgia then has 1 failure in 4,300 (a zero-tax knife edge from the build's extra cent of AGI). Fixing it means setting tax before credits in each state calculator.

## Federal AGI carries 1 cent more in the oracle

The 2026 build's federal AGI is the inputs plus $0.011, not plus $0.001: it adds $0.01 to wages (even with no wages). The Python federal AGI has only the $0.001 dividend adjustment. Federal validation tolerates the cent, but state tables read by interpolation (Connecticut's personal credit) amplify it into differences of up to 4 cents; those cases are among the Connecticut results that still differ.

## Federal AMT, 2003-2010, large married separate returns

Python's federal AMT for a 2005 married separate return with $350,000 of wages, $15,000 of property tax and $20,000 of mortgage interest is $5,816 against the build's $5,676; Connecticut's minimum tax reads it (the case differs by $400).

## Virginia, remaining differences

Virginia 2022-2023 low-income returns (the Form 760ADJ choice between the low-income credit and the earned income credit, the $250 rebate), the aged couple case of 1989-1999 and married separate itemizers in 2019-2022 still differ by small amounts. Virginia's `x14` (age deduction for 2021) is never set in other years; the oracle build should set it to 0.

## North Carolina child care expense table overwritten (fixed in the oracle build)

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
- Minnesota's Social Security subtraction thresholds were left at the 2023 amounts for 2024 and 2025 before this
  update; they now follow the published indexed amounts (2024 from House Research, 2025 and 2026 from the Revenue
  Department).
- Amounts marked Derived in the table (Rhode Island property credit maximum, New Mexico indexed amounts (the state publishes them in January 2027), the Maine head-of-household standard deduction, New
  York supplemental tax tables, the Arkansas 2026 low-income tables) should be replaced when the states
  publish their 2026 schedules.

## Tracing the oracle

`scripts/trace_failures.py <VALIDATE_OUT dir> state=MT year=2025 min=5 limit=5` traces only the worst disagreeing cases of a validation run, one file each (console shows one line per case). `scripts/trace_oracle.py year=2025 state=27 mstat=1 pwages=60000 ...` prints every nonzero federal result `c(i)` and input `d(i)` the NBER code hands a state routine for one household (the patch is in `scripts/build_oracle.sh`; it runs only when `opt1=30, opt1v=999`). Use it before reading a state routine, to see hidden inputs such as `c(201)` or `c(215)`.


- Wisconsin 2023 married separate: a typo (`82.89d9` for `82.89d0`) in the bracket table made the oracle's tax hugely negative. Patched in `build_oracle.sh`.

- **Minnesota exemption phase-out start rounding (2023-2025).** The statutory start is 100,000 times the index times the status multiplier, unrounded (2023 joint 330,975); Minnesota publishes the starts rounded to 50 (330,950; head of household 275,800 against 275,812.50). The itemized-deduction limit start uses the single value and also differs slightly from the exemption start in 2024 (232,500 against 232,550). Effect: a phase-out that begins at most $25 earlier or later. Fix by storing the published starts in the parameters (Python and Rust). Source: Census Bureau parameter workbook TY2025, MN sheet (MNEXLIM, MNSTDLIM).

- **Taxsim-mode residuals found by `scripts/confirm_logic_switches.py` (2026-11-13), traced and fixed.** Running each logic-error switch's affected CPS households through the compiled TAXSIM showed mismatches in taxsim mode; the causes were:
  - Missouri working family credit: TAXSIM applies the investment income limit from 2024 (the code assumed none).
  - Utah earned income credit: TAXSIM does not cap it at wages (the cap zeroed it for filers with only self-employment or other earned income).
  - Vermont Social Security exemption: Polars rounds exact halves to even, the Fortran `anint` rounds away from zero, so a fully phased-out exemption lost its -1 point. The same rounding was changed in CO, NJ, NY and WA.
  - Minnesota, 2024 on (`mntax24`): the compiled program applies the elderly subtraction (9,600 single, 12,000 otherwise, no halving for separate returns) from its own formula; through 2023 it is not applied.
  - Virginia spouse tax adjustment: from 2022 the split adds the untaxed unemployment compensation back (`splitagi(agi+c(78), ...)`); through 2021 it leaves out the age deduction (`exclt`, `excls`) by the primary and spouse ages, with a typo that gives nothing when only the spouse is 65 or older.
  - New Mexico working families credit, 2026: the credit before the age test (`comnew(188)`) is not deflated by the build, so the match has to be divided by the projection factor.
  Remaining: Virginia 2026, 4 of 577 affected households, $1 to $4, spouse self-employment income (the spouse's self-employment income is not deflated in the split in projected years, as noted for other states). Separately, Minnesota random households still differ from the compiled program in 2022-2026 (6 to 22 of 300 per year, from cents up to $470; for example a married-separate filer with a long-term gain and dividends, 2024) in situations unrelated to these switches; not traced.

- **Missouri taxsim-mode residuals, 2024-2026 (found 2026-11-13, not traced).** Random households (200 per year, seed 1) still differ from the compiled program in 1 (2024), 2 (2025) and 8 (2026) cases, mostly married-separate returns and filers 65 or older with rent. Example, 2024: married, 32 and 74, 3 children, wages 12,500 and 31,900, dividends 3,000, Social Security 15,000, rent 9,000: compiled program -219.88, taxsim mode 0.00; removing the rent or the Social Security makes them agree, so it is in the refundable property tax credit for a taxpayer 65 or older. Fixed on the way: the working family credit is read before the federal filer-status test (a married separate return gets it), which removed most of the 12, 20 and 7 earlier mismatches.
