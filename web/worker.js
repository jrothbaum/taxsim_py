// Runs Pyodide and taxsim-py off the page's main thread.
// Requests: {id, op: "row", record, mode, idtl, mtr} or {id, op: "file", bytes, year, mode}.
// Replies: {type: "status", message}, {type: "ready"}, {type: "error", message} while loading,
// and {id, result} or {id, error} for each request.
const PYODIDE = "https://cdn.jsdelivr.net/pyodide/v314.0.7/full/";
const toObjects = { dict_converter: Object.fromEntries };

let py;
const ready = (async () => {
  const { loadPyodide } = await import(PYODIDE + "pyodide.mjs");
  py = await loadPyodide({ indexURL: PYODIDE });
  await py.loadPackage(["polars", "pyyaml", "micropip"]);
  postMessage({ type: "status", message: "Installing taxsim-py…" });
  const wheel = (await (await fetch("wheel.txt")).text()).trim();
  py.FS.writeFile("/tmp/" + wheel, new Uint8Array(await (await fetch(wheel)).arrayBuffer()));
  await py.pyimport("micropip").install("emfs:/tmp/" + wheel);
  py.runPython(`
import polars as pl
import taxsim_py
from taxsim_py.io.tables import read_table

def run_row(record, mode, idtl, mtr):
    opts = {"calculation_mode": mode}
    if idtl: opts["idtl"] = idtl
    if mtr: opts["mtr"] = mtr
    return taxsim_py.calculate_row(record, **opts)

def run_file(path, year, mode):
    frame = read_table(path, "csv")
    result = taxsim_py.calculate_taxes(frame, year=int(year) or None, calculation_mode=mode)
    return result.write_csv(), result.height, result.head(20).to_dicts(), result.columns
`);
})();

ready.then(
  () => postMessage({ type: "ready" }),
  (e) => postMessage({ type: "error", message: e.message }),
);

onmessage = async ({ data }) => {
  const { id, op } = data;
  try {
    await ready;
    let result;
    if (op === "row") {
      result = py.globals.get("run_row")(py.toPy(data.record), data.mode, data.idtl, data.mtr).toJs(toObjects);
    } else {
      py.FS.writeFile("/tmp/input.csv", new Uint8Array(data.bytes));
      result = py.globals.get("run_file")("/tmp/input.csv", data.year, data.mode).toJs(toObjects);
    }
    postMessage({ id, result });
  } catch (e) {
    postMessage({ id, error: String(e.message).split("\n").filter(Boolean).pop() });
  }
};
