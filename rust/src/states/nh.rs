//! New Hampshire interest and dividends tax (port of `calculators.states.nh`).

use std::path::Path;

use crate::federal::Inp;
use crate::params::{load, YearParams};
use crate::states::{dividend_adjustment, law_year_is_exact};

pub struct NhParams {
    rate: f64,
    exemption_per_taxpayer: f64,
    exemption_per_aged: f64,
    dividend_adjustment: f64,
}

impl NhParams {
    pub fn load(root: &Path, year: i64) -> Result<Self, String> {
        let doc = load(root, "states/nh/income_tax.yaml")?;
        law_year_is_exact(root, &doc, year)?;
        let p = YearParams::new(&doc, year);
        Ok(Self {
            rate: p.f("rate")?,
            exemption_per_taxpayer: p.f("exemption_per_taxpayer")?,
            exemption_per_aged: p.f("exemption_per_aged_taxpayer")?,
            dividend_adjustment: dividend_adjustment(root)?,
        })
    }
}

pub fn tax(i: &Inp, p: &NhParams) -> f64 {
    let income = i.dividends + p.dividend_adjustment + i.intrec;
    let exemption = i.taxpayer_count() * p.exemption_per_taxpayer + i.aged_count() * p.exemption_per_aged;
    let taxinc = (income - exemption).max(0.0);
    p.rate * taxinc * 1.0
}
