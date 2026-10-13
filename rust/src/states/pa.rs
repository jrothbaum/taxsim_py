//! Pennsylvania personal income tax (port of `calculators.states.pa`).

use std::path::Path;

use crate::federal::{Fed, Inp};
use crate::params::{get, load, num, Fs, YearParams};
use crate::states::{dividend_adjustment, law_year_is_exact, unemployment_total};

/// The forgiveness allowance amounts; which ones exist depends on the era.
#[derive(Default)]
struct Allowance {
    base: f64,
    first: f64,
    other: f64,
    joint_base: f64,
    dependent: f64,
}

pub struct PaParams {
    year: i64,
    rate: f64,
    allowance: Allowance,
    phaseout: f64,
    step: Option<(f64, f64)>,
    eitc_share: Option<f64>,
    dividend_adjustment: f64,
}

impl PaParams {
    pub fn load(root: &Path, year: i64) -> Result<Self, String> {
        let doc = load(root, "states/pa/income_tax.yaml")?;
        law_year_is_exact(root, &doc, year)?;
        let p = YearParams::new(&doc, year);
        let a = p.value("forgiveness_allowance", &[])?;
        let field = |key: &str| a.get(key).map(num).transpose().map(|v| v.unwrap_or(0.0));
        let step = match p.opt("forgiveness_step")? {
            Some(s) => Some((num(get(s, "share")?)?, num(get(s, "income")?)?)),
            None => None,
        };
        Ok(Self {
            year,
            rate: p.f("rate")?,
            allowance: Allowance {
                base: field("base")?,
                first: field("first")?,
                other: field("other")?,
                joint_base: field("joint_base")?,
                dependent: field("dependent")?,
            },
            phaseout: p.f("forgiveness_phaseout")?,
            step,
            eitc_share: if p.in_effect("eitc_federal_share")? { Some(p.f("eitc_federal_share")?) } else { None },
            dividend_adjustment: dividend_adjustment(root)?,
        })
    }
}

pub fn tax(i: &Inp, f: &Fed, p: &PaParams) -> f64 {
    let y = p.year;
    // Federal Schedule E income: other property income, plus S corporation income from 1987.
    let schede = i.otherprop + if y >= 1987 { i.scorp } else { 0.0 };
    // Positive income classes only; pensions are exempt for taxpayers 65 or older.
    let business = i.psemp + i.ssemp + i.pbusinc + i.pprofinc + i.sbusinc + i.sprofinc;
    let income = i.pwages.max(0.0)
        + i.swages.max(0.0)
        + i.dividends
        + p.dividend_adjustment
        + i.intrec
        + business.max(0.0)
        + (i.stcg + i.ltcg).max(0.0)
        + schede
        + if i.aged_count() > 0.0 { 0.0 } else { i.pensions };
    let taxinc = income.max(0.0);
    let mut statax = taxinc * p.rate;

    // ---- tax forgiveness ----
    let is_joint = i.fs() == Fs::Joint;
    let taxpayers = i.taxpayer_count();
    let nkid = i.depx;
    let (dep1, dep2) = if y <= 1994 {
        // The spouse is counted as the first claimant after the filer; a dependent filer counts no one.
        let dep1 = if taxpayers < 1.0 { 0.0 } else if is_joint || nkid >= 1.0 { 1.0 } else { 0.0 };
        let dep2 = if taxpayers < 1.0 {
            0.0
        } else if is_joint {
            nkid
        } else {
            (nkid - 1.0).max(0.0)
        };
        (dep1, dep2)
    } else {
        (taxpayers, nkid)
    };
    let a = &p.allowance;
    let allow = if y <= 1993 {
        a.base + dep1 * a.first + dep2 * a.other
    } else if y == 1994 {
        (if is_joint { a.joint_base } else { a.base }) + a.dependent * nkid
    } else if y <= 1996 {
        a.base + (dep1 - 1.0 + dep2) * a.dependent
    } else if y == 1998 {
        let single_allow = a.first * (dep1 + dep2.min(1.0)) + (dep2 - 1.0).max(0.0) * a.other;
        if is_joint { a.first * dep1 + dep2 * a.other } else { single_allow }
    } else {
        a.first * dep1 + a.other * dep2
    };
    // Schedule SP eligibility income adds nontaxable income such as unemployment compensation and exempt pensions.
    let eligibility = taxinc + unemployment_total(i) + if i.aged_count() > 0.0 { i.pensions } else { 0.0 };
    let remain = (eligibility - allow).max(0.0);
    let forgiven_share = match p.step {
        Some((share, income)) => (1.0 - share * (remain / income).ceil()).max(0.0),
        // Forgiveness falls 10% for each tenth of the phaseout (or part).
        None => (1.0 - 0.1 * (remain / (p.phaseout / 10.0)).ceil()).max(0.0),
    };
    statax -= statax * forgiven_share;
    if let Some(share) = p.eitc_share {
        statax -= share * f.eitc;
    }
    statax * 1.0
}
