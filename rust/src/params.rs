//! Parameter files, read directly from the YAML that `taxsim_py` also reads.
//!
//! Mirrors `taxsim_py.engine.schema`: a mapping whose keys are all integers is
//! keyed by year, the latest key at or before the law year applies, and a
//! `null` entry marks the year a provision ended.

use std::collections::HashMap;
use std::path::{Path, PathBuf};
use std::sync::{Arc, Mutex, OnceLock};

use serde_yaml::Value;

static CACHE: OnceLock<Mutex<HashMap<PathBuf, Arc<Value>>>> = OnceLock::new();

/// Parse a parameter file once per process.
pub fn load(root: &Path, file: &str) -> Result<Arc<Value>, String> {
    let path = root.join(file);
    let cache = CACHE.get_or_init(|| Mutex::new(HashMap::new()));
    if let Some(found) = cache.lock().unwrap().get(&path) {
        return Ok(found.clone());
    }
    let text = std::fs::read_to_string(&path).map_err(|e| format!("{}: {e}", path.display()))?;
    let value: Value = serde_yaml::from_str(&text).map_err(|e| format!("{}: {e}", path.display()))?;
    let value = Arc::new(value);
    cache.lock().unwrap().insert(path, value.clone());
    Ok(value)
}

fn int_key(key: &Value) -> Option<i64> {
    key.as_i64()
}

pub fn is_year_keyed(node: &Value) -> bool {
    match node.as_mapping() {
        Some(m) => !m.is_empty() && m.keys().all(|k| int_key(k).is_some()),
        None => false,
    }
}

pub fn resolve_year<'a>(by_year: &'a Value, year: i64) -> Result<&'a Value, String> {
    let map = by_year.as_mapping().ok_or("not a year-keyed entry")?;
    let effective = map.iter().filter_map(|(k, _)| int_key(k)).filter(|y| *y <= year).max();
    let Some(effective) = effective else {
        return Err(format!("No parameter value coded for year {year} or earlier"));
    };
    let value = &map[Value::from(effective)];
    if value.is_null() {
        return Err(format!("Parameter not in effect in {year} (ended {effective})"));
    }
    Ok(value)
}

pub fn resolve_path<'a>(node: &'a Value, path: &[&str], year: i64) -> Result<&'a Value, String> {
    let mut node = node;
    if is_year_keyed(node) {
        node = resolve_year(node, year)?;
    }
    for key in path {
        node = node.get(*key).ok_or_else(|| format!("missing key {key}"))?;
        if is_year_keyed(node) {
            node = resolve_year(node, year)?;
        }
    }
    Ok(node)
}

/// A parameter mapping read at one law year (`YearParams`).
pub struct YearParams<'a> {
    pub params: &'a Value,
    pub year: i64,
}

impl<'a> YearParams<'a> {
    pub fn new(params: &'a Value, year: i64) -> Self {
        Self { params, year }
    }

    pub fn value(&self, key: &str, path: &[&str]) -> Result<&'a Value, String> {
        let entry = self.params.get(key).ok_or_else(|| format!("missing parameter {key}"))?;
        resolve_path(entry, path, self.year)
    }

    /// Whether a year-keyed entry has a non-null value at the law year.
    pub fn in_effect(&self, key: &str) -> Result<bool, String> {
        let entry = self.params.get(key).ok_or_else(|| format!("missing parameter {key}"))?;
        let map = entry.as_mapping().ok_or_else(|| format!("{key} is not year-keyed"))?;
        let latest = map.iter().filter_map(|(k, _)| int_key(k)).filter(|y| *y <= self.year).max();
        Ok(latest.is_some_and(|y| !map[Value::from(y)].is_null()))
    }
}

/// YAML to JSON, with integer keys written as strings (for the parity test).
pub fn to_json(value: &Value) -> serde_json::Value {
    use serde_json::Value as J;
    match value {
        Value::Null => J::Null,
        Value::Bool(b) => J::Bool(*b),
        Value::Number(n) => match n.as_i64() {
            Some(i) => J::from(i),
            None => match n.as_f64() {
                Some(x) if x.is_finite() => J::from(x),
                Some(x) if x.is_nan() => J::String("nan".into()),
                Some(x) => J::String(if x > 0.0 { "inf" } else { "-inf" }.into()),
                None => J::Null,
            },
        },
        Value::String(s) => J::String(s.clone()),
        Value::Sequence(items) => J::Array(items.iter().map(to_json).collect()),
        Value::Mapping(map) => J::Object(
            map.iter()
                .map(|(k, v)| {
                    let key = match k {
                        Value::String(s) => s.clone(),
                        Value::Number(n) => n.to_string(),
                        Value::Bool(b) => b.to_string(),
                        other => format!("{other:?}"),
                    };
                    (key, to_json(v))
                })
                .collect(),
        ),
        Value::Tagged(t) => to_json(&t.value),
    }
}

// ---- typed readers -------------------------------------------------------

pub const STATUS_KEYS: [&str; 4] = ["single", "married_joint", "married_separate", "head_of_household"];

/// Filing status, in the order of `STATUS_KEYS`.
#[derive(Clone, Copy, PartialEq, Eq, Debug)]
pub enum Fs {
    Single = 0,
    Joint = 1,
    Separate = 2,
    Hoh = 3,
}

pub fn num(v: &Value) -> Result<f64, String> {
    v.as_f64().ok_or_else(|| format!("expected a number, found {v:?}"))
}

pub fn get<'a>(v: &'a Value, key: &str) -> Result<&'a Value, String> {
    v.get(key).ok_or_else(|| format!("missing key {key}"))
}

/// A mapping of filing status to a plain number.
pub fn status_direct(v: &Value) -> Result<[f64; 4], String> {
    let mut out = [0.0; 4];
    for (slot, key) in out.iter_mut().zip(STATUS_KEYS) {
        *slot = num(get(v, key)?)?;
    }
    Ok(out)
}

/// A mapping of filing status to a (possibly year-keyed) number, read at `year`.
pub fn status_at(v: &Value, year: i64) -> Result<[f64; 4], String> {
    let mut out = [0.0; 4];
    for (slot, key) in out.iter_mut().zip(STATUS_KEYS) {
        let entry = get(v, key)?;
        let resolved = if is_year_keyed(entry) { resolve_year(entry, year)? } else { entry };
        *slot = num(resolved)?;
    }
    Ok(out)
}

/// Rows of any width: `[[a, b, c, ...], ...]`
pub fn matrix(v: &Value) -> Result<Vec<Vec<f64>>, String> {
    let rows = v.as_sequence().ok_or("expected a list of rows")?;
    rows.iter()
        .map(|row| row.as_sequence().ok_or("row must be a list")?.iter().map(num).collect::<Result<Vec<f64>, String>>())
        .collect::<Result<_, _>>()
}

/// `[[start, rate], ...]`
pub fn brackets(v: &Value) -> Result<Vec<Vec<f64>>, String> {
    let rows = v.as_sequence().ok_or("brackets must be a list")?;
    rows.iter()
        .map(|row| {
            let pair = row.as_sequence().ok_or("bracket row must be a list")?;
            Ok(vec![num(&pair[0])?, num(&pair[1])?])
        })
        .collect()
}

impl<'a> YearParams<'a> {
    pub fn f(&self, key: &str) -> Result<f64, String> {
        num(self.value(key, &[])?)
    }

    pub fn num_at(&self, key: &str, path: &[&str]) -> Result<f64, String> {
        num(self.value(key, path)?)
    }

    pub fn opt(&self, key: &str) -> Result<Option<&'a Value>, String> {
        Ok(if self.in_effect(key)? { Some(self.value(key, &[])?) } else { None })
    }
}
