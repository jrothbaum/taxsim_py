# Latest NBER TAXSIM source

The current NBER Fortran source is published at:

<https://taxsim.nber.org/out2psl/taxsim.f>

The source fetched on 2026-09-29 identifies itself in its header as build
`2026092909` and is
temporarily available on this machine at:

`/tmp/taxsim-source-latest.f`

That path is disposable. The source is deliberately not copied into this
repository because the file's header asks users not to pass it along. The
matching executable is preserved locally as the untracked artifact
`oracle_artifacts/taxsim_nber_2026092717_linux`; it reports build
`2026092717` and has SHA-256
`f6f4d3f1db78b2dc14e9e888a4716f693011cd619171fd4e1a06eb4a7400a1c4`.

To refresh the local source for analysis, download the URL above to a private
temporary path and do not add the source file to git. The source and executable
are one hour apart because NBER's endpoint was updated while this audit was in
progress; the compiled source reproduced the downloaded executable on the
targeted probes.
