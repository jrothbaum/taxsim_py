# TAXSIM executable generations

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
