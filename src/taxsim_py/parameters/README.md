# Parameter tables

The YAML and CSV files here are the canonical, law-oriented tables used by the
default `calculation_mode="statutory"`. `docs/updating.md` explains how they are
organized and how to add a year; `tests/test_parameter_conventions.py` checks the
rules below.

- Values are keyed by the first tax year they apply and carry forward until the
  next key; `null` marks the year a provision ended.
- Names describe the concept, never a year (`standard_deduction`, not
  `standard_deduction_2018`). Integer keys are years, except TAXSIM state codes in
  `national/sales_tax_deduction.yaml`.
- Each file opens with a comment naming its sources; each state file has
  `law_coded_through`, the last year of actual law statutory mode uses.

`calculation_mode="taxsim"` reproduces the compiled TAXSIM. Most differences are
formula, sequencing or intermediate-value behaviors, not alternate table values,
so they are centralized behavior switches in `src/taxsim_py/behavior.py` instead
of duplicate parameter files. If a TAXSIM-specific number is genuinely needed, add
it as a named override next to the canonical value with its source and years; never
change the canonical value just to make an oracle comparison pass.
