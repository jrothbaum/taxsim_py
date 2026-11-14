//! Washington: no income tax, but the refundable Working Families Tax Credit
//! (statutory mode; port of `calculators.states.wa`).

use std::path::Path;

use crate::federal::{Fed, Inp};
use crate::params::{get, load, num, Fs, YearParams};

pub struct WaParams {
    /// Maximum credit and phaseout band by number of children (0 to 3), when the credit exists.
    credit: Option<([f64; 4], [f64; 4], f64)>,
    /// Published income ceilings (single, joint) by number of children, when the Department has published them.
    ceilings: Option<([f64; 4], [f64; 4])>,
}

impl WaParams {
    pub fn load(root: &Path, year: i64) -> Result<Self, String> {
        let doc = load(root, "states/wa/income_tax.yaml")?;
        let p = YearParams::new(&doc, year);
        if !p.in_effect("working_families_credit_amount")? {
            return Ok(Self { credit: None, ceilings: None });
        }
        let four = |v: &serde_yaml::Value| -> Result<[f64; 4], String> {
            let list = v.as_sequence().ok_or("expected a list of four amounts")?;
            Ok([num(&list[0])?, num(&list[1])?, num(&list[2])?, num(&list[3])?])
        };
        let ceilings = if p.in_effect("working_families_credit_income_ceiling")? {
            let table = p.value("working_families_credit_income_ceiling", &[])?;
            Some((four(get(table, "single")?)?, four(get(table, "joint")?)?))
        } else {
            None
        };
        Ok(Self {
            ceilings,
            credit: Some((
                four(p.value("working_families_credit_amount", &[])?)?,
                four(get(&doc, "working_families_credit_phaseout_start_below_ceiling")?)?,
                num(get(&doc, "working_families_credit_minimum")?)?,
            )),
        })
    }
}

pub fn tax(i: &Inp, f: &Fed, p: &WaParams) -> f64 {
    let Some((amounts, bands, minimum)) = &p.credit else { return 0.0 };
    let Some((phaseout_start, max_credit, rate_out)) = f.eitc_schedule else { return 0.0 };
    let children = f.num_children.max(0.0).min(3.0) as usize;
    let (maximum, band) = (amounts[children], bands[children]);
    // The credit ends where the federal earned income credit does (the published table from 2022 to 2025).
    let ceiling = match &p.ceilings {
        Some((single, joint)) => if i.fs() == Fs::Joint { joint[children] } else { single[children] },
        None => phaseout_start + max_credit / rate_out,
    };
    let income = f.earned_income.max(f.agi);
    // RCW 82.08.0206(3)(b): the rate is set so the minimum credit is reached at the ceiling.
    let reduced = maximum - (income - (ceiling - band)).max(0.0) * (maximum - minimum) / band;
    let credit = minimum.max(reduced.round());
    let eligible = f.eitc > 0.0 && income < ceiling && f.earned_income > 0.0;
    -(if eligible { credit } else { 0.0 })
}
