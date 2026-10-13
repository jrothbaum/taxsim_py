//! Tennessee Hall income tax on interest and dividends (port of `calculators.states.tn`).

use std::path::Path;

use crate::federal::Inp;
use crate::params::{load, Fs, YearParams};
use crate::states::{dividend_adjustment, household_income, law_year_is_exact};

pub struct TnParams {
    year: i64,
    rate: f64,
    minimum_income_per_taxpayer: Option<f64>,
    exemption: Option<f64>,
    limit_joint: f64,
    limit_single: f64,
    dividend_adjustment: f64,
}

impl TnParams {
    pub fn load(root: &Path, year: i64) -> Result<Self, String> {
        let doc = load(root, "states/tn/income_tax.yaml")?;
        law_year_is_exact(root, &doc, year)?;
        let p = YearParams::new(&doc, year);
        Ok(Self {
            year,
            rate: p.f("rate")?,
            minimum_income_per_taxpayer: if p.in_effect("minimum_income_per_taxpayer")? {
                Some(p.f("minimum_income_per_taxpayer")?)
            } else {
                None
            },
            exemption: if p.in_effect("exemption")? { Some(p.f("exemption")?) } else { None },
            limit_joint: p.num_at("aged_household_income_limit", &["married_joint"])?,
            limit_single: p.num_at("aged_household_income_limit", &["single"])?,
            dividend_adjustment: dividend_adjustment(root)?,
        })
    }
}

pub fn tax(i: &Inp, p: &TnParams) -> f64 {
    let income = i.dividends + p.dividend_adjustment + i.intrec;
    let taxpayers = i.taxpayer_count();
    let mut statax = if let Some(minimum) = p.minimum_income_per_taxpayer {
        if income <= minimum * taxpayers { 0.0 } else { p.rate * income }
    } else if let Some(exemption) = p.exemption {
        p.rate * (income - exemption * taxpayers).max(0.0)
    } else {
        // The tax is repealed.
        0.0
    };
    let limit = if i.fs() == Fs::Joint { p.limit_joint } else { p.limit_single };
    let household = household_income(i, p.dividend_adjustment);
    let exempt = if p.year <= 1985 { household <= limit } else { household < limit };
    if i.aged_count() > 0.0 && exempt {
        statax = 0.0;
    }
    statax * 1.0
}
