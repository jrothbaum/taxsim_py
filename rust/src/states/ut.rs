//! Utah income tax (port of `calculators.states.ut`).

use std::path::Path;

use crate::common::Brackets;
use crate::federal::{Fed, Inp};
use crate::params::{self, get, load, num, status_direct, Fs, YearParams};
use crate::states::{federal_exemption_count, law_year_is_exact, pre1987_gross_itemized};

struct StandardShare {
    rate: f64,
    low: f64,
    high: f64,
}

struct ItemizedLimit {
    threshold: f64,
    index: f64,
    share: f64,
    rate: f64,
}

struct SmallTaxCredit {
    tax_limit: f64,
    rate: f64,
    cap: f64,
}

/// The graduated schedule in force through 2007.
struct Graduated {
    single: Brackets,
    married: Brackets,
    /// Through 1986.
    standard_share: Option<StandardShare>,
    federal_tax_share: f64,
    itemized_limit: Option<ItemizedLimit>,
    retirement_under65: f64,
    retirement_aged: f64,
    retirement_phaseout_rate: f64,
    single_rate_alternative: Option<f64>,
    small_tax_credit: Option<SmallTaxCredit>,
}

struct ChildTaxCredit {
    amount: f64,
    reduction_rate: f64,
    phaseout_start: [f64; 4],
    under_four: bool,
}

struct RetirementAge {
    age: f64,
    credit_max: f64,
    ss_phaseout_start: [f64; 4],
}

/// The flat-rate law from 2008.
struct Flat {
    rate: f64,
    salt_cap: Option<f64>,
    threshold: PhaseoutThreshold,
    index: f64,
    credit_rate: f64,
    credit_phaseout_rate: f64,
    credit_personal_exemption: Option<f64>,
    retirement_phaseout_rate: f64,
    retirement_credit_max: f64,
    retirement_age: Option<RetirementAge>,
    ss_credit_rate: f64,
    ss_credit_per_taxpayer: f64,
    child_tax_credit: Option<ChildTaxCredit>,
    earned_income_rate: Option<f64>,
}

enum PhaseoutThreshold {
    PerTaxpayer { head_of_household: f64, per_taxpayer: f64 },
    ByStatus([f64; 4]),
}

enum Law {
    Graduated(Graduated),
    Flat(Flat),
}

pub struct UtParams {
    year: i64,
    phase_single: f64,
    phase_other: f64,
    exemption_per_person: Option<f64>,
    federal_exemption_share: Option<f64>,
    dependent_exemption: Option<f64>,
    law: Law,
}

impl UtParams {
    pub fn load(root: &Path, year: i64) -> Result<Self, String> {
        let doc = load(root, "states/ut/income_tax.yaml")?;
        law_year_is_exact(root, &doc, year)?;
        let p = YearParams::new(&doc, year);
        let opt = |key: &str| -> Result<Option<f64>, String> { Ok(if p.in_effect(key)? { Some(p.f(key)?) } else { None }) };
        let phases = get(&doc, "retirement_phaseout_start")?;

        let exemption_per_person = opt("exemption_per_person")?;
        let federal_exemption_share = if exemption_per_person.is_none() { opt("federal_exemption_share")? } else { None };
        let dependent_exemption =
            if exemption_per_person.is_none() && federal_exemption_share.is_none() { Some(p.f("dependent_exemption")?) } else { None };

        let law = if p.in_effect("brackets")? {
            let schedule = p.value("brackets", &[])?;
            let standard_share = if year <= 1986 {
                let share = p.value("standard_deduction_share", &[])?;
                Some(StandardShare { rate: num(get(share, "rate")?)?, low: num(get(share, "low")?)?, high: num(get(share, "high")?)? })
            } else {
                None
            };
            Law::Graduated(Graduated {
                single: Brackets::new(&params::brackets(get(schedule, "single")?)?),
                married: Brackets::new(&params::brackets(get(schedule, "married")?)?),
                standard_share,
                federal_tax_share: p.f("federal_tax_share")?,
                itemized_limit: match opt("itemized_limit_threshold")? {
                    Some(threshold) => Some(ItemizedLimit {
                        threshold,
                        index: p.f("itemized_limit_index")?,
                        share: num(get(&doc, "itemized_limit_share")?)?,
                        rate: num(get(&doc, "itemized_limit_rate")?)?,
                    }),
                    None => None,
                },
                retirement_under65: p.f("retirement_deduction_under65")?,
                retirement_aged: p.f("retirement_deduction_aged")?,
                retirement_phaseout_rate: num(get(&doc, "retirement_deduction_phaseout_rate")?)?,
                single_rate_alternative: opt("single_rate_alternative")?,
                small_tax_credit: match p.opt("small_tax_credit")? {
                    Some(c) => Some(SmallTaxCredit {
                        tax_limit: num(get(c, "tax_limit")?)?,
                        rate: num(get(c, "rate")?)?,
                        cap: num(get(c, "cap")?)?,
                    }),
                    None => None,
                },
            })
        } else {
            let t = p.value("taxpayer_credit_phaseout_threshold", &[])?;
            let threshold = if t.get("per_taxpayer").is_some() {
                PhaseoutThreshold::PerTaxpayer {
                    head_of_household: num(get(t, "head_of_household")?)?,
                    per_taxpayer: num(get(t, "per_taxpayer")?)?,
                }
            } else {
                PhaseoutThreshold::ByStatus(status_direct(t)?)
            };
            let retirement_age = if p.in_effect("retirement_credit_age")? {
                Some(RetirementAge {
                    age: p.f("retirement_credit_age")?,
                    credit_max: p.f("retirement_credit_max")?,
                    ss_phaseout_start: status_direct(p.value("social_security_credit_phaseout_start", &[])?)?,
                })
            } else {
                None
            };
            let child_tax_credit = match p.opt("child_tax_credit")? {
                Some(k) => Some(ChildTaxCredit {
                    amount: num(get(k, "amount")?)?,
                    reduction_rate: num(get(k, "reduction_rate")?)?,
                    phaseout_start: status_direct(get(k, "phaseout_start")?)?,
                    under_four: num(get(k, "child_age_limit")?)? == 4.0,
                }),
                None => None,
            };
            Law::Flat(Flat {
                rate: p.f("flat_rate")?,
                salt_cap: opt("salt_cap")?,
                threshold,
                index: p.f("taxpayer_credit_index")?,
                credit_rate: num(get(&doc, "taxpayer_credit_rate")?)?,
                credit_phaseout_rate: num(get(&doc, "taxpayer_credit_phaseout_rate")?)?,
                credit_personal_exemption: opt("taxpayer_credit_personal_exemption")?,
                retirement_phaseout_rate: num(get(&doc, "retirement_credit_phaseout_rate")?)?,
                retirement_credit_max: p.f("retirement_credit_max")?,
                retirement_age,
                ss_credit_rate: num(get(&doc, "social_security_credit_rate")?)?,
                ss_credit_per_taxpayer: if p.in_effect("social_security_credit_per_taxpayer")? {
                    p.f("social_security_credit_per_taxpayer")?
                } else {
                    0.0
                },
                child_tax_credit,
                earned_income_rate: opt("earned_income_credit_rate")?,
            })
        };
        Ok(Self {
            year,
            phase_single: num(get(phases, "single")?)?,
            phase_other: num(get(phases, "other")?)?,
            exemption_per_person,
            federal_exemption_share,
            dependent_exemption,
            law,
        })
    }
}

pub fn tax(i: &Inp, f: &Fed, p: &UtParams) -> f64 {
    let y = p.year;
    let fs = i.fs();
    let is_hoh = fs == Fs::Hoh;
    let married_table = fs == Fs::Joint || is_hoh;
    let sep = i.sepret();
    let txp = i.taxpayer_count();
    let aged = i.aged_count();
    let agi = f.agi;
    let phase = if fs == Fs::Single { p.phase_single } else { p.phase_other / sep };
    let salt = f.ded;

    let exemp = if let Some(amount) = p.exemption_per_person {
        amount * federal_exemption_count(y, i)
    } else if let Some(share) = p.federal_exemption_share {
        share * f.personal_exemptions
    } else {
        p.dependent_exemption.unwrap_or(0.0) * i.depx
    };

    match &p.law {
        Law::Graduated(g) => {
            // ---- deductions ----
            let (fed_itemizes, itemized, stded, taxbc, almtax, fed_credit);
            if y <= 1986 {
                let gross = pre1987_gross_itemized(i, f);
                fed_itemizes = f.pre87.itemizes;
                itemized = gross;
                let s = g.standard_share.as_ref().expect("standard deduction share exists through 1986");
                let mut share = (s.rate * agi).max(s.low / sep).min(s.high / sep);
                if y > 1980 {
                    share = if fed_itemizes { 0.0 } else { share };
                }
                stded = share;
                taxbc = f.pre87.taxbc;
                almtax = f.pre87.almtax;
                fed_credit = f.pre87.credit;
            } else {
                fed_itemizes = f.itemizes;
                itemized = f.itemized_deduction;
                stded = if fed_itemizes { 0.0 } else { f.standard_deduction };
                taxbc = f.regular_tax;
                almtax = f.amt;
                fed_credit = f.nonrefundable_credits;
            }
            let xitded = ((if fed_itemizes { itemized } else { 0.0 }) - salt).max(0.0);
            let deduc = xitded.max(stded);
            let fedtax = (almtax + taxbc - fed_credit).max(0.0) * g.federal_tax_share;

            // ---- state income tax deduction lost to the federal-style limit (1993 on) ----
            let mut tx = 0.0;
            if let Some(l) = &g.itemized_limit {
                let phas92 = l.threshold * l.index / sep;
                let xtot = salt + i.proptax + i.mortgage + i.charity_cash;
                let over = agi - phas92;
                let xconst = (l.share * xtot).min(l.rate * over);
                let lost = xconst / xtot * salt;
                tx = if xitded > stded && xtot > 0.0 && over > 0.0 { lost } else { 0.0 };
            }
            // ---- retirement income deduction ----
            let investment = i.dividends + i.intrec;
            let retinc = i.pensions + f.taxable_social_security + investment * aged.min(1.0);
            let retded = if y <= 1987 {
                let cap = if aged == 0.0 {
                    txp * g.retirement_under65
                } else if aged == 1.0 {
                    g.retirement_aged + (txp - 1.0) * g.retirement_under65
                } else {
                    txp * g.retirement_aged
                };
                retinc.max(0.0).min(cap)
            } else {
                let reduction = g.retirement_phaseout_rate * (agi - phase).max(0.0);
                let amount = if aged == 0.0 {
                    if retinc > 0.0 { txp * retinc.min(g.retirement_under65) - reduction } else { 0.0 }
                } else {
                    aged.min(2.0) * g.retirement_aged - reduction
                };
                amount.max(0.0)
            };
            let taxinc = (agi - retded - fedtax - exemp - deduc + tx).max(0.0);
            let mut statax = if married_table { g.married.tax(taxinc) } else { g.single.tax(taxinc) };
            if let Some(rate) = g.single_rate_alternative {
                statax = statax.min(rate * agi.max(0.0));
            }
            if let Some(c) = &g.small_tax_credit {
                let credits = if statax <= c.tax_limit { (c.rate * statax).max(0.0).min(c.cap) } else { 0.0 };
                statax = (statax - credits).max(0.0);
            }
            statax * 1.0
        }
        Law::Flat(g) => {
            let taxinc = agi.max(0.0);
            let statax = g.rate * taxinc;
            let fed_itemizes = f.itemizes;
            let itemized = match g.salt_cap {
                None => (f.itemized_deduction - salt).max(0.0),
                Some(cap) => {
                    let other = i.proptax + i.otheritem;
                    let sttax = (cap / sep).min(other + salt);
                    let stt = salt.min(sttax - other);
                    f.itemized_before_limit - stt
                }
            };
            let deduc = if fed_itemizes { itemized } else { f.standard_deduction };
            let threshold = match &g.threshold {
                PhaseoutThreshold::PerTaxpayer { head_of_household, per_taxpayer } => {
                    if is_hoh { head_of_household * g.index } else { per_taxpayer * g.index * txp }
                }
                PhaseoutThreshold::ByStatus(by_status) => by_status[fs as usize] * g.index,
            };
            let credit_base = match g.credit_personal_exemption {
                Some(amount) => deduc + amount * i.depx,
                None => exemp + deduc,
            };
            let credit = (g.credit_rate * credit_base - g.credit_phaseout_rate * (taxinc - threshold).max(0.0)).max(0.0);
            let ss = f.taxable_social_security;
            let phaseout_rate = g.retirement_phaseout_rate;
            let retcrd = match &g.retirement_age {
                Some(r) => {
                    let taxpayers = (i.page >= r.age) as u8 as f64 + (i.sage >= r.age) as u8 as f64;
                    let retirement = (r.credit_max * taxpayers - phaseout_rate * (agi - phase).max(0.0)).max(0.0);
                    let ss_threshold = r.ss_phaseout_start[fs as usize];
                    let ss_credit = (g.ss_credit_rate * ss - phaseout_rate * (agi - ss_threshold).max(0.0)).max(0.0);
                    retirement.max(ss_credit)
                }
                None => {
                    let mut retcrd = if aged > 0.0 {
                        (g.retirement_credit_max * aged - phaseout_rate * (agi - phase).max(0.0)).max(0.0)
                    } else {
                        0.0
                    };
                    retcrd += if ss > 0.0 { (g.ss_credit_per_taxpayer * (txp - aged)).min(g.ss_credit_rate * ss) } else { 0.0 };
                    retcrd
                }
            };
            let ctc = match &g.child_tax_credit {
                Some(k) => {
                    let children = if k.under_four { i.children_under_4 } else { i.dep6 };
                    (k.amount * children - k.reduction_rate * (taxinc - k.phaseout_start[fs as usize]).max(0.0)).max(0.0)
                }
                None => 0.0,
            };
            let earned_income_credit = match g.earned_income_rate {
                Some(rate) => (rate * f.eitc).min((i.pwages + i.swages).max(0.0)),
                None => 0.0,
            };
            (statax - credit - retcrd - ctc - earned_income_credit).max(0.0) * 1.0
        }
    }
}
