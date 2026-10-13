//! Alaska income tax, through 1979 (port of `calculators.states.ak`); there is
//! none afterwards.

use std::path::Path;

use crate::common::Brackets;
use crate::federal::{Fed, Inp};
use crate::params::{self, get, load, Fs, YearParams};

pub struct AkParams {
    standard: [f64; 4],
    flat_credit: f64,
    single: Brackets,
    joint: Brackets,
    hoh: Brackets,
}

impl AkParams {
    pub fn load(root: &Path, year: i64) -> Result<Self, String> {
        let doc = load(root, "states/ak/income_tax.yaml")?;
        let p = YearParams::new(&doc, year);
        let table = |key: &str| -> Result<Brackets, String> { Ok(Brackets::new(&params::brackets(get(&doc, key)?)?)) };
        // Order of `STATUS_KEYS`: single, joint, separate, head of household.
        Ok(Self {
            standard: [
                p.f("standard_deduction_single")?,
                p.f("standard_deduction_married_joint")?,
                p.f("standard_deduction_married_separate")?,
                p.f("standard_deduction_head_of_household")?,
            ],
            flat_credit: p.f("flat_credit")?,
            single: table("brackets_single")?,
            joint: table("brackets_married_joint")?,
            hoh: table("brackets_head_of_household")?,
        })
    }
}

pub fn tax(i: &Inp, f: &Fed, p: &AkParams) -> f64 {
    let fs = i.fs();
    let taxinc = (f.agi - p.standard[fs as usize]).max(0.0);
    // Separate returns use the single schedule.
    let regtax = match fs {
        Fs::Joint => p.joint.tax(taxinc),
        Fs::Hoh => p.hoh.tax(taxinc),
        _ => p.single.tax(taxinc),
    };
    let credit = if fs == Fs::Joint { 2.0 * p.flat_credit } else { p.flat_credit };
    (regtax - credit).max(0.0)
}
