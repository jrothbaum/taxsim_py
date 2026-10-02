# Newer NBER TAXSIM parameter audit

Audited 2026-09-29 against the current NBER source downloaded to
`/tmp/taxsim-source-latest.f` (source header build `2026092909`) and the
preserved executable `oracle_artifacts/taxsim_nber_2026092717_linux`.

The newer source is the relevant TAXSIM reference for updates. The older
`taxsim_2024_09_21.f` and `taxsim2024.exe` remain useful only as historical
compatibility oracles. They must not be used to reject a change that is
supported by the newer source.

## 2021 findings

### Federal

No clear 2021 value-level update was found in the current federal tables:

- federal bracket thresholds and rates agree with `parameters/national/income_tax.yaml`;
- EITC maximums and phaseout thresholds agree with
  `parameters/national/eitc.csv`;
- the Social Security wage base agrees with
  `parameters/national/payroll_tax.yaml` (`142800`); and
- the audited AMT and standard-deduction values agree with the existing
  federal parameter tables.

The newer source does contain substantial federal and driver changes for
later years, so this is not evidence that the entire federal model is
complete. It means that blindly changing the 2021 federal YAML/CSV would not
be justified by this source audit.

### Ohio

The newer `ohtax21` routine explicitly supplies the 2021 personal exemption
amounts:

| Parameter | 2021 value |
| --- | ---: |
| Standard exemption | 1900 |
| Low-income exemption (Ohio AGI up to $40,000) | 2400 |
| Middle-income exemption (Ohio AGI up to $80,000) | 2150 |

These values were missing from the Python table, which therefore reused 2020
values through year resolution. They are now recorded in
`parameters/states/oh/income_tax.yaml`, with a regression test in
`tests/test_parameter_provenance.py`. The 2021 Ohio bracket schedule already
matched the newer source.

## Important comparison limitation

The newer executable is not a drop-in replacement for the historical
executable's output schema. On a deterministic 1,000-unit 2021 CPS sample it
changed 30 output fields, including state liabilities and worksheet fields.
Running the existing historical state validator against it produces thousands
of apparent failures because several columns have changed meaning or
availability, not because every corresponding Python tax calculation is
wrong.

Accordingly, the current historical validators remain pinned to their
historical oracle until a separate normalization/comparison profile is added
for the newer executable. New parameter updates should cite the newer source
directly, as this Ohio update does, and should not be inferred from a raw
cross-generation output diff.

## Not yet ported

The newer source contains additional 2021 state routines and later-year law
tables. Those need a state-by-state audit of explicit statutory values versus
comments marked as guessed, formula changes, and output-schema changes before
they are copied into the canonical statutory YAML/CSV tables. This remains
open work rather than an automatic bulk update.
