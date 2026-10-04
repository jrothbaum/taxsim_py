# Documentation

- [Project README](../README.md): install, use, supported years and inputs
- [Statutory corrections](statutory_corrections.md): where the default mode departs from compiled TAXSIM
- [PolicyEngine comparison](policyengine_recent_state_comparison.md): remaining differences from PolicyEngine-US
- [Architecture](architecture.md): calculators, parameters, and the public API
- [Shared functions](shared_functions.md): reusable calculation helpers
- [Performance](performance.md): benchmarks and memory
- [Pending issues](pending_issues.md): open work and validation record
- [Parameter tables](../parameters/README.md): editing YAML and CSV tax-law data

## Development

- Set up from a checkout: `uv sync --group dev --group test`
- Tests: `uv run pytest -q`
- Full validation matrix: `uv run scripts/validate_all.py`
- CPS comparison with the compiled TAXSIM: `uv run scripts/compare_cps.py PATH/TO/cps_2011 --tax-year 2021`
  (uses the `policyengine-taxsim` test dependency)
- Docs and browser calculator: `uv run --no-project --with mkdocs --with mkdocs-material python scripts/build_docs.py`
  builds `site/` (preview with `python -m http.server -d site`); add `--deploy` to publish to `gh-pages`
- Calculator alone: `uv run python scripts/build_web.py`, then `python -m http.server -d web`
