# Pending issues

Status as of 2026-09-28.

Line numbers refer to `taxsim_2024_09_21.f`, and "the executable" means `taxsim2024.exe`.

## Prioritized roadmap

The calculation engine is ready for a research beta: all 49,101 federal and
272,692 state validation cases pass after explicitly classified TAXSIM errors
and threshold round-off, and realistic CPS comparisons cover representative
actual-law years from 1977 through 2021. The remaining work is primarily about
independent confidence, a reproducible public release, and clearly defining
where compatibility ends.

Known TAXSIM bugs that already have an opt-in correction are tracked in
[`statutory_corrections.md`](statutory_corrections.md). The default remains
backward-compatible TAXSIM behavior.

### P0: before calling it a public TAXSIM replacement

- [ ] **Investigate the remaining CPS tax differences.** Triage each
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
- [ ] **Automate the full validation matrix.** Provide one command that runs
  API tests, vectorization checks, federal validation, state validation, and a
  small cached CPS smoke matrix. It should fail on new unclassified
  differences and print a compact machine-readable summary.
- [ ] **Add continuous integration.** Run the API/unit tests and checks on
  every change. Decide whether licensed or platform-specific TAXSIM binaries
  can run in CI; if not, publish a generated oracle fixture or run the full
  oracle suite in a separate trusted release job.
- [ ] **Document the supported contract.** State the exact supported federal
  and state years, input columns and units, filing-status semantics, actual-law
  versus projected years, known law-over-TAXSIM choices, ignored inputs, and
  numerical tolerances. Label projected years separately rather than implying
  the same confidence as actual-law years.
- [ ] **Make the repository releasable.** Add a README with installation and
  realistic examples, a license, citation information, contribution guidance,
  changelog/versioning policy, and complete package metadata/build-system
  configuration. Do not package the TAXSIM executable: the test-only
  `policyengine-taxsim` dependency provides cross-platform binaries for oracle
  validation.
- [ ] **Commit and tag the validated baseline.** Split the current uncommitted
  work into reviewable commits, record the exact validation commands and
  results, and create an initial beta tag so later backward fixes have a stable
  reference point.
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

- [ ] **Test every actual-law year with realistic data.** Run the CPS units
  through every year from 1977 through 2021, not only policy-boundary years,
  and retain per-year counts by tax and semantic worksheet output. This is
  mainly a regression grid; projected years can remain out of scope for now.
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
- [ ] **Resolve the 1981 detail-output cluster.** Determine why TAXSIM `v26`
  differs on about 1,690 CPS units while taxes mostly agree. Either fix the
  semantic `federal_alternative_minimum_taxable_income` output or document the
  executable's historical worksheet behavior and classify it explicitly.
- [ ] **Publish reproducible benchmark and parity artifacts.** Save compact
  machine-readable results for the validation matrix, CPS comparisons, and
  performance benchmarks so users can evaluate a release without trusting a
  prose claim.

### P2: explicitly deferred or optional

- [ ] Fix the negative-capital-gain state cases listed below, then move their
  shared regression cases back into `tests/state_new_inputs.py`.
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
  `2026092717`.

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
replication, but resolve legal corrections in this maintained Python code and
its statutory mode rather than waiting for another Fortran release.

`scripts/archive_oracle_generations.py` (see `docs/oracle_archive/`) confirms
two things on a small synthetic matrix: `policyengine-taxsim35-legacy`
(`.venv/share/policyengine_taxsim/taxsim35/`) is behaviorally identical to
`frozen-2024` there (0 differing outputs), so it is the same generation as the
frozen baseline, not a fifth one; and the two `taxsimtest` builds (repo's July
`2026072709` and PolicyEngine's bundled August `2026081819`) differ from each
other on only 6 outputs there, while both differ from the frozen baseline on
22 outputs including `frate`/`ficar` on every case. The September NBER build
(`2026092717`) is not cached on this machine as of 2026-09-28 and is recorded
as an unavailable generation in the archive; re-download it before it becomes
unreachable if a build-to-build comparison against it still matters.

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

### Uncommitted work
- Everything in this repo is uncommitted, including your chunked-worker changes in `api.py`.
- Outside this repo, `survey_kit_data/src/survey_kit_data/census/cps_asec.py` (line 278) opens the data dictionary with `encoding='latin-1'`. That fix is also uncommitted.
