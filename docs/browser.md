# Browser

**[Open the calculator](calculator/index.html).** It runs taxsim-py in the page through
[Pyodide](https://pyodide.org/), in a web worker. Nothing is sent to a server.

- **One household:** a form grouped like TAXSIM's, with marginal rates, detailed
  worksheets and calculation mode as options.
- **Upload a file:** a CSV with one household per row (needs `state`, `mstat` and
  `year`, or set the year on the page), a preview of the results and a CSV download.
  The calculation runs in a web worker, so the page stays responsive, but very large
  files still need browser memory; use the [command line](files.md) for those.
