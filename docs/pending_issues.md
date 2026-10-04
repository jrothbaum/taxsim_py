# Pending issues

Status as of 2026-10-04.

Line numbers refer to `taxsim_2024_09_21.f`, and "the executable" means `taxsim2024.exe`.

The newer NBER source/executable is now the preferred TAXSIM reference for
new updates. The older source and executable named above are retained only as
historical compatibility oracles. The first newer-source audit found no
supported 2021 federal table changes and added the missing 2021 Ohio
exemption values. See
[`oracle_archive/newer_source_parameter_audit.md`](oracle_archive/newer_source_parameter_audit.md).

## Prioritized roadmap

The calculation engine is ready for a research beta: all 49,101 federal and
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
- [x] **Add an independent calculator harness.** The test dependency group now
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
- [x] **Document the supported contract.** Done in the README ("What is
  supported"): years, inputs, accuracy, and what is not modelled.
- [x] **Make the repository releasable.** The README has installation, examples
  and the supported contract, and the project is MIT licensed. No contribution
  guidance or versioning policy is planned.
- [ ] **Tag the validated baseline.** Work is committed in reviewable steps;
  no beta tag exists yet.
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
- [x] **Extend the 2022-2024 state audit to the remaining states.** Done: all 45
  income-tax states run actual law for 2022-2025 in statutory mode and were
  compared with PolicyEngine on a synthetic grid with explicit child ages, an
  age-65+ grid, and CPS samples for each year (see
  [`docs/policyengine_recent_state_comparison.md`](policyengine_recent_state_comparison.md)).
  Lessons to reuse: a table whose last entry is 2021 is silently held at 2021 for
  a listed state; TAXSIM's `tablki` interpolation is wrong for statutory step
  schedules; and tests placed exactly on a threshold flip tiers because TAXSIM
  adds $0.001 to AGI.
- [x] **Residual 2022-2024 state differences.** Superseded by the remaining-
  differences table in the PolicyEngine comparison doc (October 2026). The small
  items from the earlier list (Oklahoma credit details, Indiana 2022 joint
  returns, Kentucky's low-income credit, Arkansas recapture, Connecticut tiers,
  Arizona $6, Ohio tier at $80,000, California and Illinois at $250,000) were not
  individually re-verified.
- [x] **Complete the recent state-law imports state by state.** Done for every
  income-tax state through 2025. Provisions TAXSIM has no input for are listed
  in the comparison doc.
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
- [x] Projected state years: statutory mode uses actual law through 2025, and
  years after 2025 raise an error. Taxsim mode keeps TAXSIM's projection only to
  reproduce the compiled model.
- [ ] Consider additional dataframe adapters only after the Polars API and
  semantic output names are stable; TAXSIM-compatible `vNN` names should
  remain an optional final rename.

## Open items

### TAXSIM errors not yet on the design doc's "Apparent TAXSIM errors" tab

Corrected in statutory mode and written up in
[`statutory_corrections.md`](statutory_corrections.md): California unemployment
removed twice (CA-001), California minimum tax (CA-002), California young child
credit (CA-004), the pre-1998 nonrefundable credit total (FED-INCOME-001), the
payroll and self-employment cases (FED-PAYROLL-001 to 004), the California and
New Jersey childless credit age (CA-005/NJ-001), Massachusetts payroll
deduction (MA-001), and Colorado's pension subtraction (CO-001). Still only
recorded here:

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
  the reported ages. This may already be on the tab.

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
- **Small residuals** from the earlier age-65+ and working-age grids that were not
  re-verified: Michigan tiers for filers born before 1953, Maryland high income,
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

Known and left alone: in 1977, 14 Connecticut units differ in federal tax by
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
The sweep found and fixed the California and New Jersey childless EITC minimum
age, the Massachusetts per-spouse payroll deduction, stale Massachusetts
circuit-breaker limits and rent cap for 2022-2025, and the Colorado per-taxpayer
pension subtraction.

### Independent model commands

`uv run --group test pytest tests/test_independent_models.py -q` runs the small
synthetic suite, and `uv run --group test scripts/compare_independent.py <cps
dir> --tax-year 2024 --sample-size 1500 --calculation-mode statutory` runs a
timed CPS sample against PolicyEngine and Tax-Calculator. The harness calls
PolicyEngine's `PolicyEngineRunner` directly, not `StitchedRunner`, which
delegates years before 2021 to NBER TAXSIM. TAXSIM generations and archived
executable outputs are described in [`oracle_archive/`](oracle_archive/README.md).
