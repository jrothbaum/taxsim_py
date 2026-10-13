//! Illinois individual income tax (port of `calculators.states.il`).

use std::path::Path;

use crate::federal::{Fed, Inp};
use crate::params::{get, load, num, Fs, YearParams};
use crate::states::{dividend_adjustment, dividend_exclusion_addback, law_year_is_exact};

pub struct IlParams {
    year: i64,
    rate: f64,
    exemption_amount: f64,
    aged_exemption: Option<f64>,
    income_limit: (f64, f64),
    property_tax_deduction_multiple: Option<f64>,
    property_credit_rate: Option<f64>,
    property_credit_income_cap: Option<f64>,
    eitc: Option<Eitc>,
    child_tax_credit_rate: Option<f64>,
    dividend_adjustment: f64,
}

struct Eitc {
    match_rate: f64,
    minimum_age: Option<f64>,
    refundable: bool,
}

impl IlParams {
    pub fn load(root: &Path, year: i64) -> Result<Self, String> {
        let doc = load(root, "states/il/income_tax.yaml")?;
        law_year_is_exact(root, &doc, year)?;
        let p = YearParams::new(&doc, year);
        let limits = get(&doc, "exemption_income_limit")?;
        let opt = |key: &str| -> Result<Option<f64>, String> {
            Ok(if p.in_effect(key)? { Some(p.f(key)?) } else { None })
        };
        let eitc = if p.in_effect("eitc_match_rate")? {
            Some(Eitc {
                match_rate: p.f("eitc_match_rate")?,
                minimum_age: opt("eitc_childless_minimum_age")?,
                refundable: p.value("eitc_refundable", &[])?.as_bool().ok_or("eitc_refundable must be a boolean")?,
            })
        } else {
            None
        };
        Ok(Self {
            year,
            rate: p.f("rate")?,
            exemption_amount: p.f("personal_exemption")?,
            aged_exemption: opt("aged_exemption")?,
            income_limit: (num(get(limits, "joint")?)?, num(get(limits, "other")?)?),
            property_tax_deduction_multiple: opt("property_tax_deduction_multiple")?,
            property_credit_rate: opt("property_tax_credit_rate")?,
            property_credit_income_cap: opt("property_tax_credit_income_cap")?,
            eitc,
            child_tax_credit_rate: opt("child_tax_credit_rate")?,
            dividend_adjustment: dividend_adjustment(root)?,
        })
    }
}

pub fn tax(i: &Inp, f: &Fed, p: &IlParams) -> f64 {
    let y = p.year;
    // The federal exemption count: taxpayers and dependents (none for a dependent filer from 1987), plus the aged before 1987.
    let exemps = if y <= 1986 {
        i.taxpayer_count() + i.depx + i.aged_count()
    } else if i.is_dependent_filer() {
        0.0
    } else {
        i.taxpayer_count() + i.depx
    };

    // Federal AGI plus the federal dividend and capital-gains exclusions (through 1986).
    let (dividend_addback, capgains_addback) =
        if y <= 1986 { (dividend_exclusion_addback(y, i, f, p.dividend_adjustment), f.pre87.capded) } else { (0.0, 0.0) };
    // Pensions and Social Security benefits are exempt.
    let il_agi = f.agi + dividend_addback + capgains_addback - i.pensions - f.taxable_social_security;
    let mut exemption = p.exemption_amount * exemps;
    if let Some(aged) = p.aged_exemption {
        exemption += aged * i.aged_count();
    }
    // A dependent filer gets one exemption only if income is at most that amount.
    if i.is_dependent_filer() {
        exemption = if il_agi <= p.exemption_amount { p.exemption_amount } else { 0.0 };
    }
    if y >= 2017 {
        // No exemption allowance above the base income limit.
        let limit = if i.fs() == Fs::Joint { p.income_limit.0 } else { p.income_limit.1 };
        if il_agi > limit {
            exemption = 0.0;
        }
    }
    let proptax = i.proptax;

    // Property tax: a deduction 1983-1990, a nonrefundable credit from 1991.
    let ptax_deduction = p.property_tax_deduction_multiple.map_or(0.0, |m| m * proptax);
    let taxinc = (il_agi - exemption - ptax_deduction).max(0.0);
    let tax_before_credits = taxinc * p.rate;

    let property_credit = match p.property_credit_rate {
        Some(rate) => match p.property_credit_income_cap {
            Some(cap) => {
                if f.agi > cap * i.taxpayer_count() { 0.0 } else { tax_before_credits.min(rate * proptax) }
            }
            None => tax_before_credits.min(rate * proptax),
        },
        None => 0.0,
    };
    let tax_after_property_credit = tax_before_credits - property_credit;

    // Earned income credit (2000 on): a share of the federal credit.
    let il_eitc = match &p.eitc {
        Some(e) => {
            let mut federal_eitc = f.eitc;
            if let Some(minimum_age) = e.minimum_age {
                // Childless filers qualify from the state minimum age.
                let older = i.page.max(i.sage);
                let too_young = f.num_children == 0.0 && older > 0.0 && older < minimum_age;
                federal_eitc = if too_young { 0.0 } else { f.eitc_before_age_test };
            }
            let raw = e.match_rate * federal_eitc;
            if e.refundable { raw } else { 0.0f64.max(raw.min(tax_after_property_credit)) }
        }
        None => 0.0,
    };
    // A share of the state EITC for a return with a child under 12 (`dep13`).
    let ctc = match p.child_tax_credit_rate {
        Some(rate) if i.dep13 > 0.0 => rate * il_eitc,
        _ => 0.0,
    };
    (tax_after_property_credit - il_eitc - ctc) * 1.0
}
