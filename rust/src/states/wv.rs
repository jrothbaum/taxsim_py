//! West Virginia personal income tax, 1987 and later (port of
//! `taxsim_py.calculators.states.wv`).

use std::path::Path;

use crate::common::Brackets;
use crate::federal::{Fed, Inp};
use crate::params::{self, get, load, num, Fs, YearParams};
use crate::states::law_year_is_exact;

struct SocialSecurity {
    share: f64,
    above_limit_share: f64,
    agi_per_taxpayer: f64,
}

struct Stepped {
    first_share: f64,
    share_per_step: f64,
}

struct Family {
    limits: Vec<f64>,
    full_share: f64,
    points: f64,
    step: f64,
    stepped: Option<Stepped>,
}

struct Property {
    base: f64,
    per_person: f64,
    rate: f64,
    cap: f64,
}

pub struct WvParams {
    year: i64,
    /// The rate schedule; before 1987 heads of household may have their own.
    table: Brackets,
    head_of_household_table: Option<Brackets>,
    /// Through 1986: a share of AGI up to a cap, and itemized deductions.
    standard_deduction_share: Option<(f64, f64)>,
    surtax: Option<f64>,
    surtax_income_per_taxpayer: f64,
    social_security: Option<SocialSecurity>,
    senior_deduction: f64,
    exemption: f64,
    dependent_filer_exemption: Option<f64>,
    low_income_exclusion: Option<f64>,
    family: Option<Family>,
    property: Option<Property>,
}

impl WvParams {
    pub fn load(root: &Path, year: i64) -> Result<Self, String> {
        let doc = load(root, "states/wv/income_tax.yaml")?;
        law_year_is_exact(root, &doc, year)?;
        let p = YearParams::new(&doc, year);
        let tables = p.value("brackets", &[])?;
        // Before 1987 the schedule may be a general table with one for heads of household.
        let (general, hoh) = if tables.is_mapping() {
            (params::brackets(get(tables, "general")?)?, Some(params::brackets(get(tables, "head_of_household")?)?))
        } else {
            (params::brackets(tables)?, None)
        };
        let standard_deduction_share = match p.opt("standard_deduction_share")? {
            Some(c) => Some((num(get(c, "rate")?)?, num(get(c, "cap")?)?)),
            None => None,
        };
        let family = match p.opt("family_credit_limit")? {
            Some(limits) => {
                let stepped = match p.opt("family_credit_stepped_phaseout")? {
                    Some(s) => Some(Stepped { first_share: num(get(s, "first_share")?)?, share_per_step: num(get(s, "share_per_step")?)? }),
                    None => None,
                };
                Some(Family {
                    limits: limits.as_sequence().ok_or("limits must be a list")?.iter().map(num).collect::<Result<_, _>>()?,
                    full_share: p.f("family_credit_full_share")?,
                    points: p.f("family_credit_phaseout_points")?,
                    step: num(get(&doc, "family_credit_phaseout_step")?)?,
                    stepped,
                })
            }
            None => None,
        };
        let property = if p.in_effect("property_credit_base")? {
            Some(Property {
                base: p.f("property_credit_base")?,
                per_person: p.f("property_credit_per_person")?,
                rate: num(get(&doc, "property_credit_rate")?)?,
                cap: num(get(&doc, "property_credit_cap")?)?,
            })
        } else {
            None
        };
        let social_security = if p.in_effect("social_security_subtraction_share")? {
            Some(SocialSecurity {
                share: p.f("social_security_subtraction_share")?,
                above_limit_share: if p.in_effect("social_security_above_limit_share")? {
                    p.f("social_security_above_limit_share")?
                } else {
                    0.0
                },
                agi_per_taxpayer: num(get(&doc, "social_security_subtraction_agi_per_taxpayer")?)?,
            })
        } else {
            None
        };
        Ok(Self {
            year,
            table: Brackets::new(&general),
            head_of_household_table: hoh.map(|h| Brackets::new(&h)),
            standard_deduction_share,
            surtax: if p.in_effect("surtax")? { Some(p.f("surtax")?) } else { None },
            surtax_income_per_taxpayer: num(get(&doc, "surtax_income_per_taxpayer")?)?,
            social_security,
            senior_deduction: num(get(&doc, "senior_deduction")?)?,
            exemption: p.f("exemption")?,
            dependent_filer_exemption: if p.in_effect("dependent_filer_exemption")? {
                Some(p.f("dependent_filer_exemption")?)
            } else {
                None
            },
            low_income_exclusion: if p.in_effect("low_income_exclusion")? { Some(p.f("low_income_exclusion")?) } else { None },
            family,
            property,
        })
    }
}

/// West Virginia income tax (`siitax`) for one household.
pub fn tax(i: &Inp, f: &Fed, p: &WvParams) -> f64 {
    let is_joint = i.fs() == Fs::Joint;
    let sep = i.sepret();
    let txp = i.taxpayer_count();
    let aged = i.aged_count();
    let dependent_filer = i.is_dependent_filer();
    let depx = i.depx;
    let fed_agi = f.agi;
    let ss = f.taxable_social_security;
    let total_income = f.agi + 0.5 * f.payroll.setax;
    let y = p.year;
    // The federal exemption count: aged taxpayers add one through 1986.
    let exemps = if y <= 1986 {
        txp + depx + aged
    } else if dependent_filer {
        0.0
    } else {
        txp + depx
    };

    let mut agi = fed_agi;
    if (1982..=1986).contains(&y) {
        agi += f.pre87.twoded;
    }
    if (1984..=1986).contains(&y) {
        agi -= ss;
    }
    if let Some(s) = &p.social_security {
        let share = if fed_agi <= s.agi_per_taxpayer * txp { s.share } else { s.above_limit_share };
        agi -= share * ss;
    }
    // Senior citizen deduction.
    let cap = p.senior_deduction;
    let ti = total_income + ss;
    let pw = i.pwages.max(0.0);
    let sw = i.swages.max(0.0);
    let one = if is_joint {
        (0.5 * (ti - pw - ss) + ss + i.pensions + sw).min(cap)
    } else {
        total_income.min(cap)
    };
    let half_other = 0.5 * (ti - pw - sw);
    let two = (half_other + pw).min(cap) + (half_other + sw).min(cap);
    let mut oldded = if aged == 1.0 { one } else if aged == 2.0 { two } else { 0.0 };
    if aged == 0.0 && ss > 0.0 {
        let agimod = agi - pw - sw - ss;
        let disabled = if is_joint {
            (0.5 * agimod + pw + 0.5 * ss).min(cap) + (0.5 * agimod + sw + 0.5 * ss).min(cap)
        } else {
            agi.min(cap)
        };
        oldded = disabled.max(0.0);
    }
    let agi = (agi - oldded).max(0.0);

    // Deductions (none from 1987): a share of AGI or the federal itemized deductions less state income tax.
    let deduc = match p.standard_deduction_share {
        Some((rate, cap)) => {
            let stded = (rate * agi).min(cap);
            let gross = f.ded + i.proptax + i.otheritem + i.mortgage + i.charity_cash;
            let gross = (gross - f.ded).max(0.0);
            let xitded = if f.pre87.itemizes { gross } else { 0.0 };
            stded.max(xitded)
        }
        None => 0.0,
    };
    let mut exemp = exemps * p.exemption;
    if let (Some(e), true) = (p.dependent_filer_exemption, dependent_filer) {
        exemp = e;
    }
    let mut taxinc = (agi - deduc - exemp).max(0.0);
    if let Some(excl) = p.low_income_exclusion {
        let limit = excl / sep;
        if fed_agi <= limit {
            taxinc = (taxinc - limit.min(f.earned_income)).max(0.0);
        }
    }

    let mut statax = if y <= 1986 {
        // Joint returns use the table on half their income, doubled.
        let split = if is_joint { 2.0 * p.table.tax(taxinc / 2.0) } else { p.table.tax(taxinc) };
        match &p.head_of_household_table {
            Some(hoh) if i.fs() == Fs::Hoh => hoh.tax(taxinc),
            _ => split,
        }
    } else {
        p.table.tax(taxinc * sep) / sep
    };
    if let Some(surtax) = p.surtax {
        if taxinc > p.surtax_income_per_taxpayer * txp {
            statax *= surtax;
        }
    }

    if let Some(fam) = &p.family {
        let nexemp = exemps.floor();
        // First tier whose upper bound (1..7, then unbounded) is at least `nexemp`.
        let idx = (nexemp.max(1.0) as usize - 1).min(fam.limits.len() - 1);
        let fcp = fam.limits[idx] / sep;
        let step = fam.step / sep;
        let mut share = if agi <= fcp {
            fam.full_share
        } else {
            (0.01 * (100.0 * fam.full_share - fam.points * (agi - fcp) / step)).max(0.0)
        };
        if let Some(s) = &fam.stepped {
            let steps = ((fed_agi - fcp).max(0.0) / step).floor();
            share = if fed_agi < fcp { 1.0 } else { (s.first_share - s.share_per_step * steps).max(0.0) };
        }
        let mut famcrd = if dependent_filer { 0.0 } else { share * statax };
        if !(statax > 0.0 && nexemp > 0.0) {
            famcrd = 0.0;
        }
        statax = (statax - famcrd).max(0.0);
    }
    if let Some(pr) = &p.property {
        let pagi = pr.base + depx * pr.per_person + if is_joint { pr.per_person } else { 0.0 };
        let income = fed_agi + i.gssi - ss;
        let mut pcred = (i.proptax - pr.rate * income).max(0.0).min(pr.cap);
        if !(fed_agi < pagi) {
            pcred = 0.0;
        }
        statax -= pcred;
    }
    statax
}
