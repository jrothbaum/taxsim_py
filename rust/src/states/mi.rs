//! Michigan individual income tax (port of `calculators.states.mi`).

use std::path::Path;

use crate::federal::{Fed, Inp};
use crate::params::{get, load, num, Fs, YearParams};
use crate::states::{dividend_adjustment, household_income, law_year_is_exact, unemployment_total, Interp};

struct Holiday {
    threshold: f64,
    lower_rate: f64,
    upper_rate: f64,
    amount: f64,
}

/// Pension exclusion caps: by return (through 1993) or per taxpayer.
enum PensionExclusion {
    ByReturn { single: f64, joint: f64 },
    PerTaxpayer { amount: f64, joint_cap: Option<f64> },
}

struct Tiers {
    first_birth_year: f64,
    last_birth_year: f64,
    age: f64,
}

struct Expanded {
    share: f64,
    maximum_single: f64,
    maximum_joint: f64,
}

/// How the heating credit's base amount is found.
enum Heating {
    /// Through 1984: a table by exemptions, inflated.
    Inflated { base: Vec<f64>, inflation: f64 },
    /// 1985-2005: a table by exemptions plus an amount for each exemption past six.
    Table { base: Vec<f64>, per_extra: f64 },
    /// 2006 on: a base plus an amount for each exemption past one.
    Base { base: f64, per_extra: f64 },
}

pub struct MiParams {
    year: i64,
    rate: f64,
    personal_exemption: f64,
    special_exemption: f64,
    dependent_exemption: f64,
    dependent_filer_exemption: f64,
    special_exemption_ui_share: f64,
    holiday: Option<Holiday>,
    addback_rate: Option<f64>,
    pension: PensionExclusion,
    senior_investment: Option<f64>,
    senior_standard: Option<f64>,
    tiers: Tiers,
    expanded: Option<Expanded>,
    property_phaseout_start: f64,
    property_income_share: f64,
    property_se_rate: f64,
    property_rate: f64,
    property_cap: f64,
    property_phaseout_rate: f64,
    property_phaseout_step: f64,
    property_phaseout_range: f64,
    property_rent_share: f64,
    senior_income_share: Option<Interp>,
    senior_percentage: Interp,
    senior_rent_income_share: Option<f64>,
    heating: Heating,
    heating_income_rate: f64,
    heating_share: f64,
    eitc_rate: Option<f64>,
    dividend_adjustment: f64,
}

impl MiParams {
    pub fn load(root: &Path, year: i64) -> Result<Self, String> {
        let doc = load(root, "states/mi/income_tax.yaml")?;
        law_year_is_exact(root, &doc, year)?;
        let p = YearParams::new(&doc, year);
        let opt = |key: &str| -> Result<Option<f64>, String> { Ok(if p.in_effect(key)? { Some(p.f(key)?) } else { None }) };
        let table = |key: &str| -> Result<Vec<f64>, String> {
            p.value(key, &[])?.as_sequence().ok_or("expected a list")?.iter().map(num).collect()
        };
        let rows = |v: &serde_yaml::Value| -> Result<Vec<Vec<f64>>, String> { crate::params::brackets(v) };

        let pension = if p.in_effect("pension_exclusion_by_return")? {
            let caps = p.value("pension_exclusion_by_return", &[])?;
            PensionExclusion::ByReturn { single: num(get(caps, "single")?)?, joint: num(get(caps, "joint")?)? }
        } else {
            PensionExclusion::PerTaxpayer { amount: p.f("pension_exclusion_per_taxpayer")?, joint_cap: opt("pension_exclusion_joint_cap")? }
        };
        let expanded = if year >= 2023 {
            Some(Expanded {
                share: p.f("expanded_retirement_share")?,
                maximum_single: p.num_at("expanded_retirement_maximum", &["single"])?,
                maximum_joint: p.num_at("expanded_retirement_maximum", &["joint"])?,
            })
        } else {
            None
        };
        let heating = if p.in_effect("heating_credit_inflation")? {
            Heating::Inflated { base: table("heating_credit_base_by_exemptions")?, inflation: p.f("heating_credit_inflation")? }
        } else if p.in_effect("heating_credit_base_by_exemptions")? {
            Heating::Table { base: table("heating_credit_base_by_exemptions")?, per_extra: p.f("heating_credit_per_extra_exemption")? }
        } else {
            Heating::Base { base: p.f("heating_credit_base")?, per_extra: p.f("heating_credit_per_extra_exemption")? }
        };
        Ok(Self {
            year,
            rate: p.f("rate")?,
            personal_exemption: p.f("personal_exemption")?,
            special_exemption: if year >= 1987 { p.f("special_exemption")? } else { 0.0 },
            dependent_exemption: if year >= 1987 { p.f("dependent_exemption")? } else { 0.0 },
            dependent_filer_exemption: p.f("dependent_filer_exemption")?,
            special_exemption_ui_share: if year >= 1987 { p.f("special_exemption_ui_share")? } else { 0.0 },
            holiday: match p.opt("self_employment_tax_agi_addback_holiday")? {
                Some(h) => Some(Holiday {
                    threshold: num(get(h, "threshold")?)?,
                    lower_rate: num(get(h, "lower_rate")?)?,
                    upper_rate: num(get(h, "upper_rate")?)?,
                    amount: num(get(h, "amount")?)?,
                }),
                None => None,
            },
            addback_rate: opt("self_employment_tax_agi_addback_rate")?,
            pension,
            senior_investment: opt("senior_investment_deduction_per_taxpayer")?,
            senior_standard: opt("senior_standard_deduction")?,
            tiers: Tiers {
                first_birth_year: num(get(&doc, "senior_tier_two_first_birth_year")?)?,
                last_birth_year: num(get(&doc, "senior_tier_two_last_birth_year")?)?,
                age: num(get(&doc, "senior_standard_deduction_age")?)?,
            },
            expanded,
            property_phaseout_start: p.f("property_credit_phaseout_start")?,
            property_income_share: p.f("property_credit_income_share")?,
            property_se_rate: p.f("property_credit_self_employment_tax_deduction_rate")?,
            property_rate: p.f("property_credit_rate")?,
            property_cap: p.f("property_credit_cap")?,
            property_phaseout_rate: p.f("property_credit_phaseout_rate")?,
            property_phaseout_step: p.f("property_credit_phaseout_step")?,
            property_phaseout_range: p.f("property_credit_phaseout_range")?,
            property_rent_share: p.f("property_credit_rent_share")?,
            senior_income_share: if year >= 1980 { Some(Interp::new(&rows(p.value("property_credit_senior_income_share", &[])?)?)) } else { None },
            senior_percentage: Interp::new(&rows(get(&doc, "property_credit_senior_percentage")?)?),
            senior_rent_income_share: if year >= 1980 { Some(p.f("property_credit_senior_rent_income_share")?) } else { None },
            heating,
            heating_income_rate: p.f("heating_credit_income_rate")?,
            heating_share: p.f("heating_credit_share")?,
            eitc_rate: opt("eitc_rate")?,
            dividend_adjustment: dividend_adjustment(root)?,
        })
    }
}

pub fn tax(i: &Inp, f: &Fed, p: &MiParams) -> f64 {
    let y = p.year;
    let fs = i.fs();
    let is_joint = fs == Fs::Joint;
    let is_sep = fs == Fs::Separate;
    let n_tp = i.taxpayer_count();
    let aged = i.aged_count();
    let dependent = i.is_dependent_filer();
    let setax = f.payroll.setax;
    let ui_total = unemployment_total(i);
    let hh_income = household_income(i, p.dividend_adjustment);

    // ---- AGI ----
    let mut agi = f.agi;
    if (1982..=1986).contains(&y) {
        agi += f.pre87.twoded;
    }
    if y >= 1984 {
        agi -= f.taxable_social_security;
    }
    if let Some(h) = &p.holiday {
        agi += if setax <= h.threshold { h.lower_rate * setax } else { h.upper_rate * setax + h.amount };
    } else if let Some(rate) = p.addback_rate {
        agi += rate * setax;
    }

    // ---- retirement subtraction for taxpayers 65 or older ----
    let pensions = i.pensions;
    let mut penexc = match &p.pension {
        PensionExclusion::ByReturn { single, joint } => {
            if is_joint || is_sep {
                (pensions / if is_sep { 2.0 } else { 1.0 }).min(*joint)
            } else {
                pensions.min(*single)
            }
        }
        PensionExclusion::PerTaxpayer { amount, joint_cap } => {
            let mut e = pensions.min(amount * n_tp);
            if let (Some(cap), true) = (joint_cap, is_joint) {
                e = pensions.min(*cap);
            }
            e
        }
    };
    penexc = if aged >= 1.0 { penexc } else { 0.0 };
    // From 1994, interest and dividends (and from 1997 capital gains) instead.
    let divded = match p.senior_investment {
        Some(amount) => {
            let mut unearned = i.dividends + i.intrec;
            if y >= 1997 {
                unearned += (i.stcg + i.ltcg).max(0.0);
            }
            if aged > 0.0 { unearned.min(amount * n_tp) } else { 0.0 }
        }
        None => 0.0,
    };
    let mut pens = penexc.max(divded);
    let older = i.page.max(i.sage);
    let tiered = p.senior_standard.is_some() && y <= 2022;
    let born = y as f64 - older;
    let tier_two = born >= p.tiers.first_birth_year && born <= p.tiers.last_birth_year;
    let tier_three = born > p.tiers.last_birth_year;
    let reached = older >= p.tiers.age;
    let tier_amount = p.senior_standard.unwrap_or(0.0) * if is_joint { 2.0 } else { 1.0 };
    if tiered {
        let tier_two_deduction = if reached { tier_amount } else { pensions.min(tier_amount) };
        pens = if tier_two {
            tier_two_deduction
        } else if tier_three {
            0.0
        } else {
            pens
        };
    } else if let Some(deduction) = p.senior_standard {
        // A flat deduction per taxpayer 65 or older replaces it.
        pens = if aged > 0.0 { deduction * aged } else { pens };
    }

    // ---- exemptions ----
    let num_exemptions = n_tp + i.depx.floor() + aged;
    let xmp1 = p.personal_exemption;
    let mut exemp = if y < 1987 {
        num_exemptions * xmp1
    } else {
        let ump = if ui_total >= p.special_exemption_ui_share * f.agi { 1.0 } else { 0.0 };
        (num_exemptions - aged) * xmp1 + (aged + ump) * p.special_exemption + p.dependent_exemption * i.depx
    };
    if dependent {
        exemp = p.dependent_filer_exemption;
    }
    if tiered && tier_three && reached {
        // Born after the second tier: the deduction less exemptions from the age.
        pens = (tier_amount - exemp).max(0.0);
    }
    if let Some(e) = &p.expanded {
        // MI-1040 Worksheet 2: the senior standard deduction less exemptions.
        if aged > 0.0 {
            pens = (pens - exemp).max(0.0);
        }
        let cap = if is_joint { e.maximum_joint } else { e.maximum_single };
        let expanded = pensions.min(e.share * cap);
        if aged > 0.0 {
            pens = pens.max(expanded);
        }
    }
    let taxinc = (agi - pens - exemp).max(0.0);
    let mut regtax = p.rate * taxinc;
    if dependent && agi < p.dependent_filer_exemption {
        regtax = 0.0;
    }

    // ---- homestead property tax credit ----
    let hhy = (hh_income - p.property_se_rate * setax).max(0.0);
    let allow = p.property_phaseout_start;
    let rentpaid = i.rentpaid;
    let ptax = i.proptax + p.property_rent_share * rentpaid;
    let pcred_under65 = p.property_rate * (ptax - p.property_income_share * hhy).max(0.0);
    // Taxpayers 65 or older: property tax over a sliding share of income; renters may claim rent over 40% of income.
    let pcred_aged = match &p.senior_income_share {
        None => ptax,
        Some(table) => {
            let mut c = (ptax - table.eval(hhy) * hhy).max(0.0);
            if y >= 2012 {
                c *= p.senior_percentage.eval(hhy);
            }
            if rentpaid > 0.0 {
                c = c.max(rentpaid - p.senior_rent_income_share.unwrap_or(0.0) * hhy);
            }
            c
        }
    };
    let mut pcred = (if aged > 0.0 { pcred_aged } else { pcred_under65 }).min(p.property_cap);
    if hhy > allow {
        pcred *= (1.0 - p.property_phaseout_rate * (hhy - allow) / p.property_phaseout_step).max(0.0);
    }
    if !(hhy <= allow + p.property_phaseout_range) {
        pcred = 0.0;
    }

    // ---- home heating credit ----
    let amex = if dependent { 0.0 } else { n_tp + i.depx };
    let nexemp = amex.min(6.0).floor();
    let hy = hh_income;
    let by_exemptions = |base: &[f64]| if nexemp >= 1.0 { base[nexemp as usize - 1] } else { 0.0 };
    let fc = match &p.heating {
        Heating::Inflated { base, inflation } => by_exemptions(base) * inflation,
        Heating::Table { base, per_extra } => by_exemptions(base) + (amex - 6.0).max(0.0) * per_extra,
        Heating::Base { base, per_extra } => base + (amex - 1.0).max(0.0) * per_extra,
    };
    let fuel = (if nexemp > 0.0 { (fc - p.heating_income_rate * hy).max(0.0) } else { 0.0 }) * p.heating_share;

    let earncr = p.eitc_rate.map_or(0.0, |rate| rate * f.eitc);
    (regtax.max(0.0) - pcred - fuel - earncr) * 1.0
}
