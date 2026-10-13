//! TAXSIM's estimated general sales tax deduction (`saletx`), which the
//! resolver compares with state income tax (port of
//! `engine.sales_tax.state_sales_tax_deduction`).

use std::collections::HashMap;
use std::path::Path;

use serde_yaml::Value;

use crate::params::{get, load, num};

pub struct SalesTax {
    active: bool,
    factor: f64,
    coefficients: HashMap<i64, [f64; 3]>,
    table_size: i64,
}

impl SalesTax {
    pub fn load(root: &Path, year: i64) -> Result<Self, String> {
        let doc = load(root, "national/sales_tax_deduction.yaml")?;
        let first_year = num(get(&doc, "first_year")?)? as i64;
        if year < first_year {
            return Ok(Self { active: false, factor: 1.0, coefficients: HashMap::new(), table_size: 0 });
        }
        let cpi = load(root, "national/state_cpi_extrapolation.yaml")?;
        let last = num(get(&doc, "last_coefficient_year")?)? as i64;
        let coefficient_year = year.min(last);
        let xndxa = get(&cpi, "xndxa")?.as_mapping().ok_or("xndxa must be a mapping")?;
        let lookup = |y: i64| -> Result<f64, String> {
            num(xndxa.get(Value::from(y)).ok_or_else(|| format!("xndxa has no entry for {y}"))?)
        };
        let deflation_year = year.min(xndxa.keys().filter_map(Value::as_i64).max().ok_or("empty xndxa")?);
        let factor = lookup(coefficient_year)? / lookup(deflation_year)?;
        let table = get(&doc, "coefficients")?
            .as_mapping()
            .and_then(|m| m.get(Value::from(coefficient_year)))
            .and_then(Value::as_mapping)
            .ok_or_else(|| format!("no sales tax coefficients for {coefficient_year}"))?;
        let mut coefficients = HashMap::new();
        for (code, triple) in table {
            let code = code
                .as_i64()
                .or_else(|| code.as_str().and_then(|s| s.parse().ok()))
                .ok_or("bad sales tax state code")?;
            let t = triple.as_sequence().ok_or("coefficients must be a list")?;
            coefficients.insert(code, [num(&t[0])?, num(&t[1])?, num(&t[2])?]);
        }
        Ok(Self { active: true, factor, table_size: table.len() as i64, coefficients })
    }

    pub fn deduction(&self, resources: f64, family_size: f64, state: i64) -> f64 {
        if !self.active {
            return 0.0;
        }
        let in_table = state >= 1 && state <= self.table_size;
        if !(in_table && resources > 0.0 && family_size > 0.0) {
            return 0.0;
        }
        let [a, b, c] = self.coefficients.get(&state).copied().unwrap_or([0.0; 3]);
        let deflated = self.factor * resources.max(0.011);
        (a + b * deflated.ln() + c * family_size.ln()).exp() / self.factor
    }
}
