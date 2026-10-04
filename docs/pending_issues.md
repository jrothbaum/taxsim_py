# Pending issues

Status as of 2026-09-29.

Line numbers refer to `taxsim_2024_09_21.f`, and "the executable" means `taxsim2024.exe`.

The newer NBER source/executable is now the preferred TAXSIM reference for
new updates. The older source and executable named above are retained only as
historical compatibility oracles. The first newer-source audit found no
supported 2021 federal table changes and added the missing 2021 Ohio
exemption values. See
[`oracle_archive/newer_source_parameter_audit.md`](oracle_archive/newer_source_parameter_audit.md).

## Prioritized roadmap

The calculation engine is ready for a research beta: all 49,101 federal and
272,692 state validation cases pass after explicitly classified TAXSIM errors
and threshold round-off, and realistic CPS comparisons cover representative
actual-law years from 1977 through 2021. The remaining work is primarily about
independent confidence, a reproducible public release, and clearly defining
where compatibility ends.

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
  both TAXSIM and PolicyEngine - see "Federal law support beyond 2023" below
  for what this investigation actually found. State-year law-based checks are
  not yet covered (federal only so far).
- [x] **Add an independent calculator harness.** The test dependency group now
  includes PSL Tax-Calculator and PolicyEngine's TAXSIM emulator. The strict
  synthetic matrix compares Tax-Calculator federal and payroll tax from 2013
  onward and PolicyEngine federal and payroll tax from 2021 onward. A sampled
  CPS command provides broader diagnostic comparisons without slowing the
  normal test suite.
- [x] **Inventory PolicyEngine state parameters.** The installed
  `policyengine-us` package exposes dated values through its simple
  `system.parameters` API. `scripts/audit_policyengine_state_parameters.py`
  records the available 2022-2024 state trees and candidate paths in
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
- [x] **Document the supported contract.** Done in the README ("What is
  supported"): years, inputs, accuracy, and what is not modelled.
- [ ] **Make the repository releasable.** Add a README with installation and
  realistic examples, a license, citation information, contribution guidance,
  changelog/versioning policy, and complete package metadata/build-system
  configuration. Do not package the TAXSIM executable: the test-only
  `policyengine-taxsim` dependency provides cross-platform binaries for oracle
  validation.
- [ ] **Commit and tag the validated baseline.** Everything is now committed
  (through `5dc8faf`, 2026-09-29), but as a handful of large commits rather
  than reviewable, split-out ones, and no beta tag exists yet.
- [x] **Archive the final upstream oracle metadata.** TAXSIM is being sunset
  after its maintainer's retirement, so record the executable build stamps,
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

- [x] **Test every actual-law year with realistic data.** The CPS units were run
  through every year from 1977 through 2021 against the compiled TAXSIM and
  through 2022-2025 against PolicyEngine (October 2026; results below).
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
  showing the old failure.
- [ ] **Extend the 2022-2024 state audit to the remaining states.** The 22
  states in `ACTUAL_STATE_PARAMETER_YEARS` were compared with PolicyEngine on a
  synthetic grid with explicit child ages and fixed in two passes (2026-09-29 and
  2026-10-01; see
  [`docs/policyengine_recent_state_comparison.md`](policyengine_recent_state_comparison.md)).
  What is left there is explained (imputed sales-tax deduction, rebates, the Idaho
  $10 tax) or small: Oklahoma credit details, Indiana 2022 joint returns, Kentucky's
  low-income credit. The roughly 23 other income-tax states are not in the list at
  all and still run 2021 law scaled by the CPI proxy for 2022-2024, so those years
  are projections. Lessons to reuse: a table whose last entry is 2021 is silently
  held at 2021 for a listed state; TAXSIM's `tablki` interpolation is wrong for
  statutory step schedules; and tests placed exactly on a threshold flip tiers
  because TAXSIM adds $0.001 to AGI.
- [ ] **Residual 2022-2024 state differences, logged 2026-10-01.** None is
  known to be a port bug except where noted; each needs a source check before
  changing code.
  - Oklahoma: credit details, $4-$60 (property/sales-tax credit eligibility,
    child credit base).
  - Indiana 2022 joint returns: PolicyEngine's decoupled formula gives less credit.
  - Kentucky: low-income family-size credit, $10-$120.
  - Arkansas: $1.60 high-income recapture detail; joint-return low-income table
    versus PolicyEngine's main schedule (see comparison doc).
  - Colorado 2022: sales-tax refund on joint returns (PolicyEngine does not double it).
  - Connecticut: personal credit tier at AGI boundaries, $6-$26.
  - Arizona 2022: $6 bracket detail above $45,000.
  - Utah 2024: child credit needs the optional `children_under_4` input.
  - California and Illinois: exemption details at $250,000.
  - Ohio: exemption tier at exactly $80,000 MAGI.
  - Michigan pension/retirement subtractions and Idaho, Georgia retirement
    exclusions were not compared (no TAXSIM input for the qualifying age cohorts).
- [ ] **Complete the recent state-law imports state by state.** Dated
  PolicyEngine values are now wired for Alabama, Arkansas, Arizona, California, Colorado, Connecticut, Delaware, Hawaii, New Hampshire, the
  District of Columbia, Georgia, Idaho, Illinois, Indiana, Kentucky, Michigan, North Carolina,
  Ohio, Oklahoma, Oregon, Pennsylvania, and Utah. The state-filtered CPS results
  and remaining differences are in
  [`docs/policyengine_recent_state_comparison.md`](policyengine_recent_state_comparison.md),
  Continue
  until each remaining state's 2022-2024 parameters are either implemented or
  explicitly documented as outside the TAXSIM input contract.
- [ ] **Resolve the 1981 detail-output cluster.** Determine why TAXSIM `v26`
  differs on about 1,690 CPS units while taxes mostly agree. Either fix the
  semantic `federal_alternative_minimum_taxable_income` output or document the
  executable's historical worksheet behavior and classify it explicitly.
- [ ] **Publish reproducible benchmark and parity artifacts.** Save compact
  machine-readable results for the validation matrix, CPS comparisons, and
  performance benchmarks so users can evaluate a release without trusting a
  prose claim.

### P2: explicitly deferred or optional

- [x] Fix the negative-capital-gain state cases (Alabama, California,
  Connecticut, Illinois, Hawaii - all fixed unconditionally, see
  "Negative capital gains" below) and move their shared regression cases
  back into `tests/state_new_inputs.py` - done.
- [ ] Decide whether to implement the eight documented
  `opt1/opt1v/opt2/opt2v` switches. Until then, warn or reject when callers
  supply nonzero option values instead of silently ignoring them.
- [ ] Revisit projected/interpolated state years only when their intended use
  and validation standard are defined.
- [ ] Consider additional dataframe adapters only after the Polars API and
  semantic output names are stable; TAXSIM-compatible `vNN` names should
  remain an optional final rename.

## TAXSIM errors not yet on the design doc's errors tab

These still need to be added to the "Apparent TAXSIM errors" tab. The docs connector wasn't available when they were found.

### Federal, 2024 onward: no NBER support at all past 2023
- **Where:** `taxsim.f`'s `if (lawyr.lt.1960.or.lawyr.gt.2023)` hard error (federal), and `common /xndxac/ xndxa(1981:2023)`'s array bound (state-law CPI extrapolation and the federal sales-tax deduction, which reuses that same table).
- **What TAXSIM does:** refuses to run at all for `year>2023` - not a wrong answer, an outright rejection. NBER's maintainer retired without ever shipping a 2024 update to this file (see "TAXSIM executable generations" above); the compiled `taxsimtest` builds are newer but track a different codebase this project hasn't audited.
- **Effect on this project:** the port's own federal parameter tables were built from this same 2023-capped source and inherited the same ceiling, but silently - `year=2024`/`2025` either crashed (the sales-tax lookup) or would have quietly reused 2023's numbers (everything routed through `resolve_year()`, which forward-fills with no upper bound by design). Fixed for federal years 2024-2025 using real IRS/SSA sources; see "Federal law support beyond 2023" below.
- **Port:** does not copy the rejection - real law obviously continues past 2023, so `calculation_mode="statutory"` now computes 2024/2025 from actual published parameters instead of erroring or guessing.

### California, 1979-1986: unemployment removed twice
- **Where:** lines 2419-2439.
- **What TAXSIM does:** total income (`totinc`) never includes unemployment compensation. The adjustments (`adj`) still subtract taxable unemployment (`data(22)+comnew(78)`), so it is taken out of California AGI a second time. Someone with $10,000 of wages and $4,000 of taxable unemployment gets a California AGI of $6,000.
- **Presumably meant:** either include unemployment in total income, or skip the subtraction.
- **Port:** corrected in statutory mode (skips the subtraction); retained in TAXSIM mode. See CA-001 in `statutory_corrections.md`.

### California, 1987 onward: minimum tax excludes business and rental income
- **Where:** lines 2761-2762.
- **What TAXSIM does:** the income subject to California's minimum tax (`alminy`) subtracts positive self-employment income (`data(17)`) and Schedule E rent and S-corp income (`comnew(8)`). California's actual minimum-tax base (Schedule P) keeps that income.
- **Example (2004):** a couple with $83,000 of pensions, $1,000 of rent and $99,997 of property tax. Each dollar of rent lowers their California tax by 7 cents.
- **Port:** corrected in statutory mode; retained in TAXSIM mode. See CA-002 in `statutory_corrections.md`.

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
- **Port:** corrected in statutory mode (reports the real `federal_elder`
  credit instead); retained in TAXSIM mode - `fiitax` itself is unaffected
  either way, since TAXSIM applies no nonrefundable credits pre-1998
  regardless. See FED-INCOME-001 in `statutory_corrections.md`.

### The executable ignores reported ages under 65
- It pays the no-child EITC to filers under the minimum age: 25, or 19 in 2021.
- It doesn't cap young filers' AMT exemption.
- This may already be on the tab; check before adding it.
- **Port:** uses the reported ages.

### Federal payroll tax: self-employment edge cases

The 10,000-unit 2021 independent-model sample found 145 payroll-tax differences
from Tax-Calculator. Every difference has at least one of these overlapping
TAXSIM behaviors:

- **Additional Medicare tax counts combined self-employment income twice**
  (55 sampled rows). In `sstax`, the Additional Medicare earnings loop includes
  combined self-employment income (`data(17)`) in both the taxpayer and spouse
  streams. A single filer with $200,000 of self-employment income therefore has
  a $1,524.60 Additional Medicare tax in TAXSIM, while Tax-Calculator and
  PolicyEngine calculate zero because statutory net earnings are $184,700,
  below the $200,000 threshold.
- **The OASDI cap discounts wages when self-employment income follows them**
  (21 sampled rows). TAXSIM applies the 92.35% net-earnings factor to the
  running total, including W-2 wages, when testing the combined wage base. This
  leaves too much OASDI wage-base room for self-employment income.
- **No $400 net-earnings filing threshold** (51 sampled rows). TAXSIM assesses
  SECA on small positive amounts that Tax-Calculator treats as below the
  Schedule SE threshold.
- **Negative self-employment tax is allowed** (44 sampled rows). A loss can
  reduce TAXSIM's reported `fica`, including below the W-2 payroll tax and even
  below zero; Tax-Calculator and PolicyEngine floor SECA at zero.

These counts overlap. Together they classify all 145 payroll differences. The
Python port matches `taxsim2024.exe` to the cent on representative records.
They remain the default for compatibility, while
`calculation_mode="statutory"` corrects all four behaviors. Each implementation
and test is recorded in
[`statutory_corrections.md`](statutory_corrections.md).

## Open work

### Negative capital gains - fixed (2026-09-28)
Several calculators assumed capital gains weren't negative, but TAXSIM accepts
losses. Three test cases exposed the failures:
- a couple with a $3,000 loss and wages;
- a single filer with an $8,000 loss;
- a $12,000 long-term gain with a $4,000 short-term loss.

All five previously-failing states (Alabama, California, Connecticut,
Illinois, Hawaii) are now fixed, along with Arkansas, which was already
fixed. **Important:** despite earlier notes in this file and in
`statutory_corrections.md` describing some of these as `calculation_mode`-
gated corrections, that was a mistake - the real `taxsim2024.exe` already
computes all five correctly, so these are plain bugs in this port with no
compatibility reason to preserve, fixed unconditionally (not gated). Verified
against the real oracle with `uv run scripts/validate_states.py`: 278,776
cases across all 45 implemented states, 0 failures. Root causes, each
different:

- **Alabama:** never implemented the source's own documented rule that
  Alabama allows the full capital loss (no $3,000 federal-style cap) -
  `taxsim_py.calculators.states.al.compute_al_tax`.
- **Connecticut:** its pre-1991 capital-gains figure clipped `stcg`/`ltcg` to
  zero individually before combining, discarding a loss instead of netting
  it - `taxsim_py.calculators.states.ct.compute_ct_tax`. Two related bugs
  surfaced by the same investigation, also fixed: the 1987-1988 CT-only
  additional exclusion had the identical clipping mistake one level down,
  and reported `state_taxable_income` (`v36`) didn't floor a net loss at
  zero before adding dividends/interest (a reporting-only detail - it never
  affected the tax itself).
- **Illinois:** computed its federal-exclusion addback from gross `ltcg`
  instead of the net-of-loss amount federal AGI actually excluded -
  `taxsim_py.calculators.states.il.compute_il_tax`.
- **California:** applied no cap at all on a net capital loss; real law (and
  `taxsim.f`'s `catax`, around line 2463) caps it at $1,000 per return
  (halved for married-separate), tighter than federal's $3,000 -
  `taxsim_py.calculators.states.ca.compute_ca_tax`. Known remaining gap:
  real law floors that cap further, for very low-income filers, at a
  computed pre-capital-gain income measure (`taxy`) that isn't replicated;
  the plain $1,000 cap matched every case checked (all ordinary wage
  levels), so only unusually low-income returns could still disagree.
- **Hawaii:** its alternative capital-gains tax recomputed a cruder
  netted-gain figure that applied the federal exclusion to the full `ltcg`
  regardless of an offsetting short-term loss, instead of the properly
  netted federal amount - `taxsim_py.calculators.states.hi.compute_hi_tax`.

`engine/state.py::federal_capital_gain_in_agi` gives the federal gain
included in AGI (TAXSIM `comnew(6)`), and was the fix in three of the five
cases (CT, IL, HI). Several other states still rebuild that amount on their
own lines instead of reusing it (for example `nm.py` and `ma.py`) - not
confirmed broken, just not yet checked.

Regression tests: `tests/test_negative_capital_gains.py` (one case per
state per scenario, checked directly against the real oracle). The three
shared cases moved from `tests/ar_cases.py` (AR-only) into
`tests/state_new_inputs.py`'s shared pool, so every state's own test suite
now runs them via `scripts/validate_states.py`.

### Remaining CPS ASEC differences
Taxes now match on the 2005 and 2011 files, except for the known age errors above. After the sales-tax floor and Virginia below-filing-minimum AGI fixes, only a small number of state worksheet rows still differ even though the tax matches:

| Column | Differing units per file | Main states |
|---|---|---|
| any remaining worksheet column, after itemizing/standard ties | 40 in `cps_2005`, 44 in `cps_2011` | scattered |

Rerun with `uv run scripts/compare_cps.py <cps dir> --out mismatches.parquet`. Pass
`--tax-year YEAR` to use the CPS households and incomes as realistic test inputs
under a different year's tax law.

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

Known and left alone: in 1977, 14 Connecticut units differ in federal tax by
$2,000-$19,000 because TAXSIM itemizes Connecticut filers with property tax
(and so loses the 50% cap on earned income) for reasons not yet identified.

Tax years 2022-2025 (statutory mode, 1,500 sampled units, PolicyEngine). Federal
tax differs on about 6% of units each year, nearly all of it self-employment
income, where TAXSIM gives no 20% business deduction on `psemp` and the port
keeps that convention. State tax differs on 10-11% of units, and what remains is
the list in [PolicyEngine comparison](policyengine_recent_state_comparison.md).
The sweep found and fixed: the California and New Jersey childless EITC minimum
age (CA-005/NJ-001), the Massachusetts per-spouse payroll deduction (MA-001),
stale Massachusetts circuit-breaker limits and rent cap for 2022-2025, and the
Colorado per-taxpayer pension subtraction (CO-001). Tax-year 2022-2025 results
for TAXSIM's own projected years are not investigated.

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

The 2021 sweep initially showed 453 state-liability differences, concentrated
in New York (333 units) and DC (102 units). The port bugs are fixed: DC's 2021
childless EITC used the correct larger credit but retained the pre-2021 age
floor of 25, and New York's special rule also applied the 2020 age eligibility
when it should use 2021's lower minimum while retaining its 65+ exclusion.
New York's remaining high-income differences are the documented real-law
worksheet choice, now classified by `compare_cps.py` under "New York 2021+
real worksheet".

The latest 2021 rerun initially left 39 post-tie residual units (0.04% of
104,404), including 26 state-liability differences. Investigation produced one
port fix and explicit comparator classifications:

- Iowa's 2021 EITC eligibility caps were incorrectly checked against federal
  AGI. The state calculator now uses Iowa AGI, matching the `iatax` source and
  resolving the seven material Iowa residuals. The focused regression is in
  `tests/test_calculation_modes.py`.
- Alabama's remaining high-income cases are a worksheet difference over
  whether the state federal-tax deduction includes NIIT. The frozen executable
  and newer source use different formulas; `compare_cps.py` names this as
  `Alabama 2021 NIIT worksheet` rather than silently treating it as a port
  failure.
- Maine's three non-age-related cases are the 2021 Property Tax Fairness
  Credit worksheet row. The comparator names them `Maine 2021 PTFC worksheet`.
  Other Maine differences in the full file are propagated young-filer
  compatibility cases and are covered by the childless-EITC age classification.
- New York's remaining post-tie rows are below three cents and are classified
  as `New York worksheet roundoff`; high-income rows retain the existing
  real-law worksheet classification.
- Scattered state detail-only differences are reported as
  `worksheet-only detail` when federal, state, and payroll liabilities match.

The young-couple age rule was also corrected: both spouses are checked for
the under-19/under-25 case, rather than requiring the other spouse to be over
65. The 2021 CPS comparison now has zero unclassified differences after these
classifications and the itemizing/standard-deduction tie check. The Alabama
and Maine source differences remain candidates for future statutory-profile
switches; they are deliberately not applied unconditionally to the
backward-compatible calculation.

The reproducible artifact is `/tmp/cps_2021_residual_final.parquet` from:

`uv run scripts/compare_cps.py /home/jrothbaum/Coding/claude_code/survey_kit_data/.scratch/cached_files/cps_2011 --tax-year 2021 --out /tmp/cps_2021_residual_final.parquet`

### Independent model comparisons

Install and run the small synthetic suite with:

`uv sync --group test && uv run --group test pytest tests/test_independent_models.py -q`

Run a deterministic, timed CPS sample with:

`uv run --group test scripts/compare_independent.py <cps dir> --tax-year 2021 --sample-size 100`

Pass `--calculation-mode statutory` to measure reviewed corrections against the
independent model instead of reproducing TAXSIM's compatibility behavior.

The harness calls PolicyEngine's local `PolicyEngineRunner` directly. It does
not use `StitchedRunner`, because that delegates years before 2021 to NBER
TAXSIM and would not be an independent check. Current strict expected failures
record two comparison targets:

- Tax-Calculator's 2018 childless EITC is $1.53 below the port for a
  40-year-old single filer with $12,000 of wages.
- PolicyEngine's 2021-2023 federal and payroll results match the small wage
  and age-based dependent matrix, while the selected CA, NY, and MN state
  liabilities do not yet all match the TAXSIM-compatible results. PolicyEngine
  does not consume the `dep6` count, so CPS records with only age-band counts
  cannot distinguish all 2021 child-credit cases; actual `age1`, `age2`, etc.
  are preferred. Use the CPS sampler to cluster differences by state and year
  before changing formulas.

A deterministic 10,000-unit CPS sample under 2021 law produced 1,192 federal
and 145 payroll differences from Tax-Calculator, and 2,366 federal, 3,865 state,
and 147 payroll differences from PolicyEngine. The leading explanations are
not all formula bugs in the port:

- TAXSIM's self-employment payroll behaviors above account for every
  Tax-Calculator payroll difference in compatibility mode. Statutory mode
  resolves 142 of 145; the remaining three come from Tax-Calculator applying
  the $400 Schedule SE threshold to combined spousal income instead of each
  spouse's separate Schedule SE.
- TAXSIM's classic `psemp`/`ssemp` inputs do not receive the TCJA qualified
  business income deduction; mapping them to Tax-Calculator Schedule C income
  does. This creates many large federal differences.
- `transfers` deliberately combines non-taxable welfare-like income and
  tax-exempt interest in the old TAXSIM input model. TAXSIM includes it in the
  Social Security provisional-income calculation, while the independent models
  do not treat ordinary transfers as tax-exempt interest.
- Count-only dependent inputs cannot distinguish 17-year-olds, who qualify for
  the expanded 2021 child credit, from older EITC-qualifying students.
- PolicyEngine's TAXSIM adapter does not consume `dep6`; without actual child
  ages it misses the extra 2021 credit for children under six.
- `nonprop` has no clean post-TCJA Tax-Calculator mapping: TAXSIM includes it as
  income, while mapping it to Tax-Calculator alimony can exclude it after 2018.

A 3,000-unit CPS sample with `state=0` was rerun on 2026-09-29 for 2022-2024
after a federal fix (FED-INCOME-002: the Credit for Other Dependents was
silently dropped by TAXSIM for 2022 onward). Federal-tax differences over $1
fell from about 270-330 units per year to 165-230 against Tax-Calculator and to
167-176 against PolicyEngine. What remains is the documented input-model
behavior: 75-95% of the remaining rows have self-employment or property income that
only the independent models give the qualified business income deduction, and
most of the rest have `transfers` (which TAXSIM counts as tax-exempt interest
in Social Security taxation) or are a few dollars of IRS tax-table rounding.

A fresh 1,000-unit CPS sample was rerun on 2026-09-29 under statutory mode for
2021, 2022, and 2023. Tax-Calculator had federal-tax differences in 113, 93,
and 105 units respectively, with zero payroll differences in all three years.
PolicyEngine had federal differences in 238, 93, and 91 units; state-tax
differences in 383, 548, and 558 units; and one $0.02 payroll difference in
each year. These are diagnostic comparisons, not pass/fail claims, because
the models do not share every TAXSIM input or state-policy convention.

The same 1,000 CPS records were tested under 2024 federal law with `state=0`.
Tax-Calculator differed on federal income tax in 85 units and matched payroll
tax for all units. PolicyEngine differed on federal income tax in 85 units and
had one payroll difference of about two cents. Mixed-state 2024 comparisons
are not yet supported because the state CPI extrapolation table currently ends
at 2023.

### TAXSIM executable generations

The executables are validation tools, not package data. As of 2026-09-27 there
are several materially different generations available:

- `taxsim2024.exe` is the frozen compatibility baseline built from the local
  `taxsim_2024_09_21.f` snapshot.
- The repository's `taxsim.exe` identifies itself as `taxsimtest` build
  `2026072709`.
- `policyengine-taxsim` 2.32.1 bundles build `2026081819` under
  `<venv>/share/policyengine_taxsim/taxsimtest/`.
- NBER's `out2psl/linux` download on 2026-09-27 identified itself as build
  `2026092717`; its matching source was refreshed on 2026-09-29 as build
  `2026092909` and is recorded in `docs/oracle_archive/latest_nber_source.md`.

Set `TAXSIM_EXE=/path/to/executable` when running the oracle scripts to select
a build explicitly. Do not silently replace the frozen baseline: newer
`taxsimtest` builds have a revised output schema, reject simultaneous dependent
ages and `depNN` counts, and materially change historical results. On a fixed
10,000-unit CPS sample under 2021 law, the PolicyEngine August build differs
from the frozen baseline on 909 federal-income-tax, 2,622 state-income-tax, 334
`fica`, and 100 `tfica` records. The September NBER build differs from the
PolicyEngine August build on 133 federal and 1,482 state records, while payroll
outputs agree on that sample. These generations need separate, named parity
artifacts rather than one moving definition of “matches TAXSIM.”

TAXSIM's maintainer has retired and the service is being sunset, so these
should be treated as finite upstream snapshots, not as a rolling dependency
that this project expects to keep advancing. The newest available build may be
the final upstream snapshot. Preserve compatibility with a named build for
replication, and port confirmed law and parameter updates from it into the
maintained Python models. Do not treat the new executable as a drop-in
replacement for the frozen compatibility oracle: its historical
detail/payroll conventions and many state worksheets changed. Update work
must therefore be year- and state-scoped, with both frozen-oracle and
newest-source regression cases retained.

`scripts/archive_oracle_generations.py` (see `docs/oracle_archive/`) confirms
two things on a small synthetic matrix: `policyengine-taxsim35-legacy`
(`.venv/share/policyengine_taxsim/taxsim35/`) is behaviorally identical to
`frozen-2024` there (0 differing outputs), so it is the same generation as the
frozen baseline, not a fifth one; and the two `taxsimtest` builds (repo's July
`2026072709` and PolicyEngine's bundled August `2026081819`) differ from each
other on only 6 outputs there, while both differ from the frozen baseline on
22 outputs including `frate`/`ficar` on every case. The September NBER build
(`2026092717`) is now preserved locally as the untracked artifact
`oracle_artifacts/taxsim_nber_2026092717_linux`; the source itself remains in
disposable `/tmp/taxsim-source-latest.f` storage per its redistribution notice.

### Federal law support beyond 2023 (found and mostly fixed 2026-09-28)

The frozen NBER source only ever supported federal law through 2023
(`taxsim.f`'s own `if (lawyr.lt.1960.or.lawyr.gt.2023)` hard error, and
`xndxa(1981:2023)`'s array bound), and this project's own parameter tables
were built from that source, so they also stopped at 2023 with no upper-year
guard. Concretely, before this fix: `calculate_taxes(..., year=2024)` raised
`KeyError: 2024` in `engine/sales_tax.py` (a direct `xndxa[year]` dict lookup
with no cap), and had that crash not happened first, `year=2024` and
`year=2025` would have *silently* run on 2023's standard deduction, tax
brackets, and OASDI wage base - `engine/schema.py`'s `resolve_year()`
forward-fills the latest coded year with no upper bound, by design, for
years where nothing changed, but nothing in that mechanism can tell "flat
by law" apart from "we haven't entered next year's numbers yet."

Fixed federal-only, for 2024 and 2025, using IRS Rev. Proc. 2023-34/2024-40
and SSA's Contribution and Benefit Base, cross-checked against
PolicyEngine-US's own sourced YAML parameters (`.venv/.../policyengine_us/
parameters/gov/irs/...` - installed as a test dependency, not vendored):
federal brackets and standard deduction, AMT exemption/phaseout/26-28%
breakpoint/capital-gains ceiling, the personal (non-AMT) capital-gains 0%/
15%/20% ceilings, the OASDI wage base, the EITC parameter table (`eitc.csv`,
previously an exact-year match with no fallback at all - 2024/2025 would
have silently zeroed the credit instead of erroring), the ACTC refundable
cap, the EITC disqualified-investment-income limit, the QBI phase-in range,
and the aged/dependent standard deduction amounts. `sales_tax.py`'s crash is
fixed by capping the CPI-proxy year lookup the same way the sales-tax
coefficient year was already capped, reusing 2023's ratio (there is no real
2024+ NBER observation to use instead - matches this file's own "resolve
legal corrections in Python, not by chasing another Fortran release" stance
above). New tests: `tests/test_law_based_checks.py`.

**Not covered - known gaps, not oversights:**
- All 50 state tables still stop wherever they already stopped (unrelated to
  this pass; scoped out deliberately - states come next).
- OBBBA (H.R.1, enacted 2025-07-04) changed several things beyond what's
  fixed here: it raised the SALT cap to $40,000 (single/joint/HoH) for 2025
  with an income-based phase-out down to a $10,000 floor above $500,000
  MAGI - only the flat $40,000 cap is modeled (`itemized.yaml`'s `salt_cap`);
  the phase-out is real new formula logic, not a data update, and isn't
  implemented. It also raised the base Child Tax Credit to $2,200 for 2025
  (permanently, and now inflation-indexed) - `credits.yaml`'s CTC base
  amount was not touched this pass because 2022's branch in
  `calculators/federal.py` reuses ARPA-era keys in a way that needs reading
  before extending (there is already a suspected, separate TAXSIM-bug-
  compatibility reason 2022 shares ARPA's structure - don't guess at this
  without reading that branch first). It also created a new, separate
  $6,000-ish temporary "senior bonus" deduction for filers 65+ that has no
  home in this project's model at all yet.
- A small, pre-existing $25 discrepancy in `amt.yaml`/`capital_gains.yaml`'s
  married-separate 15%/20% capital-gains ceiling for tax year 2023 ($276,925
  here vs. $276,900 in PolicyEngine-US) was noticed but not chased down - it
  predates this session and only affects one filing-status/year cell.
- Fixed a stale, self-contradicting doc comment in `income_tax.yaml`'s
  `standard_deduction.single` block (claimed married-separate reuses
  single's value directly; the very next section already said that was a
  fixed bug and married-separate is really `married_joint / 2`).

### TAXSIM options (deferred)
The `opt1/opt1v/opt2/opt2v` inputs are accepted but ignored. If revisited, implement only the 8 documented switches.

### Law note: AMT exemption for young filers
Our cap follows TAXSIM's source, which applies it to anyone under the age limit. The real kiddie-tax rule is narrower: it applies only to children with unearned income who are claimed as dependents.

### Age-65+ audit residuals (2026-10-01)

All 45 states were compared with PolicyEngine on an age-65+ grid. Open items:
Pension type is settled: 2022+ keeps TAXSIM's convention that `pensions` is the kind each state exempts (decided 2026-10-01), so PolicyEngine's private-pension treatment in MO/MT/MI is an expected difference; MI tiers for filers born before 1953 (the grid only tests age 70); CO TABOR rebate; DE, MD, ME and IL differences at $180,000+; the $10 Idaho fee;
MN joint at $750,000.  Aged interest-only rows were rechecked: CT is at an exemption boundary, DE/HI are the imputed sales-tax deduction.

### Open after the 2022-2024 sweeps (2026-10-01)

Working-age: WA capital gains excise tax deliberately not modelled (see WA-001 in statutory_corrections.md); CA joint at $600,000 with children ($91-$280); IN 2024 joint with two children ($57); NY 2023 two-child low-income rows ($66-$291). Settled as PolicyEngine differences: VA (both credits), NY child credit (2017 federal rules). Age 65+: MD high income ($115), NM small, AR joint interest-only (income attribution between spouses), MN joint $750,000. Test grids should pass children's ages to the port as well as to PolicyEngine (`synm2`-style), or age-keyed credits (UT, NY) look wrong.

### 2025 (2026-10-02)

Added for federal and all states; see policyengine_recent_state_comparison.md. Open: tips,
overtime and car-loan deductions (no TAXSIM input); Washington capital gains tax; Minnesota homeowner/renter
refund tables are TAXSIM-era approximations not validated against PolicyEngine; state parameters for 2026 are not
yet entered (years past 2025 still run 2025 law unindexed).

### Large inputs

There is no chunking in the API; for very large files, split the input into row chunks and call
`calculate_taxes` on each (rows are independent). Measured peak memory of one call (one year, 45 states): 250,000
rows 2.1 GB, 500,000 rows 2.8 GB, 1,000,000 rows 4.1 GB, 2,000,000 rows 5.4 GB; about 70,000 rows per second; each
call also spends about 2 s rebuilding its expressions, so use chunks of 250,000 rows or more. PolicyEngine
comparisons need far more memory (about 0.3 MB per row) and must be run in chunks of a few thousand rows.

### Uncommitted work
- This repo is now committed and pushed through `5dc8faf` (2026-09-29), including the chunked-worker changes in `api.py`, the 2024/2025 federal parameter work, the oracle-generation archive, and the AL/CA/CT/IL/HI negative-capital-gains fixes.
- Outside this repo, `survey_kit_data/src/survey_kit_data/census/cps_asec.py` (line 278) opens the data dictionary with `encoding='latin-1'`. That fix's commit status wasn't checked as part of this pass - it lives in a separate repository.
