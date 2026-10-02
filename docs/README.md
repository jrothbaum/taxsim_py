# Documentation

## For users

- [Project README](../README.md): install the package and run a calculation
- [Statutory corrections](statutory_corrections.md): why the default mode can differ from compiled TAXSIM

## For contributors

- [Architecture](architecture.md): calculators, parameters, and the public API
- [Shared functions](shared_functions.md): reusable calculation helpers
- [Performance](performance.md): benchmarks and scaling notes
- [Pending issues](pending_issues.md): validation status and future work
- [Parameter tables](../parameters/README.md): editing YAML and CSV tax-law data

The project has two calculation modes:

- `statutory` is the default for new analysis.
- `taxsim` is the explicit compatibility mode for reproducing the compiled
  model and running oracle comparisons.
