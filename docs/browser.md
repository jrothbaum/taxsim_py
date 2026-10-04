# Browser

**[Open the calculator](calculator/index.html).** It runs taxsim-py in the page through
[Pyodide](https://pyodide.org/), in a web worker. Nothing is sent to a server. (The link
works on the published site; locally, build it as shown below.)

- **One household:** a form grouped like TAXSIM's, with marginal rates, detailed
  worksheets and calculation mode as options.
- **Upload a file:** a CSV with one household per row (needs `state`, `mstat` and
  `year`, or set the year on the page), a preview of the results and a CSV download.
  The calculation runs in a web worker, so the page stays responsive, but very large
  files still need browser memory; use the [command line](files.md) for those.

## Run it

```bash
uv run python scripts/build_web.py      # builds the wheel into web/
python -m http.server -d web            # then open http://localhost:8000
```

It must be served over http, not opened as a file. The documentation site includes the
calculator at `calculator/`. Build the whole site, with preview, using
`uv run --no-project --with mkdocs --with mkdocs-material python scripts/build_docs.py`
and then `python -m http.server -d site`. Add `--deploy` to publish it to the `gh-pages`
branch (GitHub Pages must be set to deploy from that branch).

## Use it from your own page

```js
import { loadPyodide } from "https://cdn.jsdelivr.net/pyodide/v314.0.7/full/pyodide.mjs";

const py = await loadPyodide();
await py.loadPackage(["polars", "pyyaml", "micropip"]);
await py.pyimport("micropip").install("emfs:/tmp/taxsim_py-0.1.0-py3-none-any.whl");  // after writing the wheel to py.FS

const calculate = py.runPython("import taxsim_py; taxsim_py.calculate_row");
const result = calculate(py.toPy({ year: 2023, state: 5, mstat: 2, pwages: 80000 }))
  .toJs({ dict_converter: Object.fromEntries });
```

`calculate_row` takes and returns plain dicts, so values cross the JavaScript boundary
without conversion code. Pass keyword options such as `mtr` or `idtl` from Python.
