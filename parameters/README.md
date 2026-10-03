# Parameter tables

The YAML and CSV files in this directory are the canonical, law-oriented
parameter tables used by the default `calculation_mode="statutory"`.

`calculation_mode="taxsim"` is an explicit compatibility override for
replication and regression testing against the compiled TAXSIM model. Most
known differences are not alternate table values: they are TAXSIM formula,
sequencing, or intermediate-value behaviors. Those differences are therefore
implemented as centralized behavior switches in
`src/taxsim_py/behavior.py`, rather than duplicating every parameter file.

When a future TAXSIM-specific numeric value is genuinely needed, add it as a
named override next to the canonical parameter and document the source and
affected years. Do not replace the canonical statutory value merely to make an
oracle comparison pass.
