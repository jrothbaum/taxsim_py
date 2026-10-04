# Parameter tables

The YAML and CSV files here are the canonical, law-oriented tables used by the
default `calculation_mode="statutory"`.

`calculation_mode="taxsim"` reproduces the compiled TAXSIM. Most differences are
formula, sequencing or intermediate-value behaviors, not alternate table values,
so they are centralized behavior switches in `src/taxsim_py/behavior.py` instead
of duplicate parameter files. If a TAXSIM-specific number is genuinely needed, add
it as a named override next to the canonical value with its source and years; never
change the canonical value just to make an oracle comparison pass.
