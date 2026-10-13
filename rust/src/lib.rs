//! Compiled Polars expression plugins for taxsim_py state calculators.
//!
//! Each plugin takes one struct of input columns and the law year's resolved
//! parameters as kwargs, and returns a struct of Float64 output columns.
//! Python (`taxsim_py`) remains the reference implementation and resolves
//! every parameter; the kernels here only do the per-household arithmetic.

use pyo3::prelude::*;

mod common;
mod config;
mod federal;
mod federal_law60;
mod federal_pre1987;
mod kernel;
mod params;
mod payroll;
mod sales_tax;
mod states;

/// Resolve `key` then `path` at `year` in a parameter file; JSON text, or an
/// `{"error": ...}` object when the provision is not in effect. Used by the
/// parity test against the Python resolver.
#[pyfunction]
fn resolve_param(root: &str, file: &str, key: &str, path: Vec<String>, year: i64) -> PyResult<String> {
    let doc = params::load(std::path::Path::new(root), file).map_err(pyo3::exceptions::PyIOError::new_err)?;
    let path: Vec<&str> = path.iter().map(String::as_str).collect();
    let result = params::YearParams::new(&doc, year).value(key, &path);
    let json = match result {
        Ok(value) => params::to_json(value),
        Err(error) => serde_json::json!({ "error": error }),
    };
    Ok(json.to_string())
}

/// Whether `key` is in effect at `year` (`YearParams.in_effect`); errors become `None`.
#[pyfunction]
fn param_in_effect(root: &str, file: &str, key: &str, year: i64) -> PyResult<Option<bool>> {
    let doc = params::load(std::path::Path::new(root), file).map_err(pyo3::exceptions::PyIOError::new_err)?;
    Ok(params::YearParams::new(&doc, year).in_effect(key).ok())
}

#[pymodule]
fn _internal(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(resolve_param, m)?)?;
    m.add_function(wrap_pyfunction!(param_in_effect, m)?)?;
    Ok(())
}
