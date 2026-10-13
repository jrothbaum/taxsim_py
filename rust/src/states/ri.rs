//! Rhode Island income tax (port of `calculators.states.ri`).

use std::path::Path;

use crate::common::Brackets;
use crate::federal::{Fed, Inp};
use crate::params::{self, get, load, num, Fs, YearParams};
use crate::states::{dividend_adjustment, federal_exemption_count, household_income, law_year_is_exact, Interp};

/// Values by filing class: single, head of household, married.
#[derive(Clone, Copy)]
struct ByClass {
    single: f64,
    hoh: f64,
    married: f64,
}

impl ByClass {
    fn read(v: &serde_yaml::Value) -> Result<Self, String> {
        Ok(Self { single: num(get(v, "single")?)?, hoh: num(get(v, "head_of_household")?)?, married: num(get(v, "married")?)? })
    }
    fn pick(&self, is_single: bool, is_hoh: bool) -> f64 {
        if is_single { self.single } else if is_hoh { self.hoh } else { self.married }
    }
}

struct Tables {
    single: Brackets,
    hoh: Brackets,
    married: Brackets,
}

struct CapitalGains {
    bracket: ByClass,
    low_rate: f64,
    high_rate: f64,
}

struct AmtRates {
    low: f64,
    high: f64,
    breakpoint: f64,
    backout: f64,
}

struct Amt {
    exemption: ByClass,
    start: ByClass,
    rate: f64,
}

/// How the deduction phases out (2011 on).
enum Phaseout {
    Steps { start: f64, end: f64, step: f64, shares: Vec<f64> },
    PerStep { start: f64, increment: f64, share_per_step: f64 },
}

struct Relief {
    limit: f64,
    inclusive: bool,
    table: Interp,
}

struct Early {
    standard: ByClass,
    aged_addition: Option<(f64, f64)>,
    dependent_floor: Option<f64>,
    dependent_addition: f64,
    capital_gains: Option<CapitalGains>,
    tables: Tables,
    index: f64,
    amt_share: Option<f64>,
    amt: Option<Amt>,
    amt_rates: Option<AmtRates>,
    flat_tax_rate: Option<f64>,
    // 2001-2010 uses the federal taxable income through 2002.
}

struct Later {
    standard: ByClass,
    exemption_amount: f64,
    index: f64,
    phaseout: Phaseout,
    table: Brackets,
}

enum Law {
    Federal(f64),
    Schedule(Early),
    Flat(Later),
}

struct SocialSecurity {
    index: f64,
    single: f64,
    joint: f64,
}

struct Retirement {
    per_aged: f64,
    index: f64,
    single: f64,
    married: f64,
}

pub struct RiParams {
    year: i64,
    law: Law,
    social_security: Option<SocialSecurity>,
    retirement: Option<Retirement>,
    relief_rent_share: f64,
    relief_max: f64,
    relief_single: Relief,
    relief_multi: Relief,
    child_care_share: Option<f64>,
    eitc_share: Option<f64>,
    eitc_refundable: Option<f64>,
    eitc_nonrefundable: Option<f64>,
    dividend_adjustment: f64,
}

impl RiParams {
    pub fn load(root: &Path, year: i64) -> Result<Self, String> {
        let doc = load(root, "states/ri/income_tax.yaml")?;
        law_year_is_exact(root, &doc, year)?;
        let p = YearParams::new(&doc, year);
        let opt = |key: &str| -> Result<Option<f64>, String> { Ok(if p.in_effect(key)? { Some(p.f(key)?) } else { None }) };

        let law = if p.in_effect("federal_tax_share")? {
            Law::Federal(p.f("federal_tax_share")?)
        } else {
            let b = p.value("brackets", &[])?;
            if b.is_mapping() {
                let index = p.f("bracket_index")?;
                let scaled = |key: &str| -> Result<Brackets, String> {
                    let rows: Vec<Vec<f64>> = params::brackets(get(b, key)?)?.into_iter().map(|r| vec![r[0] * index, r[1]]).collect();
                    Ok(Brackets::new(&rows))
                };
                let standard_of = |key: &str| -> Result<f64, String> { p.num_at("standard_deduction", &[key]) };
                let standard = if year > 2002 {
                    ByClass { single: standard_of("single")?, hoh: standard_of("head_of_household")?, married: standard_of("married")? }
                } else {
                    ByClass { single: 0.0, hoh: 0.0, married: 0.0 }
                };
                let aged_addition = if year > 2002 {
                    Some((p.num_at("standard_deduction_aged_addition", &["single"])?, p.num_at("standard_deduction_aged_addition", &["married"])?))
                } else {
                    None
                };
                let capital_gains = match p.opt("capital_gains_bracket")? {
                    Some(c) => Some(CapitalGains {
                        bracket: ByClass::read(c)?,
                        low_rate: num(get(&doc, "capital_gains_low_rate")?)?,
                        high_rate: num(get(&doc, "capital_gains_high_rate")?)?,
                    }),
                    None => None,
                };
                let amt = match p.opt("amt_exemption")? {
                    Some(e) => Some(Amt {
                        exemption: ByClass::read(e)?,
                        start: ByClass::read(p.value("amt_exemption_phaseout_start", &[])?)?,
                        rate: num(get(&doc, "amt_exemption_phaseout_rate")?)?,
                    }),
                    None => None,
                };
                let amt_rates = if amt.is_some() {
                    Some(AmtRates {
                        low: num(get(&doc, "amt_rate_low")?)?,
                        high: num(get(&doc, "amt_rate_high")?)?,
                        breakpoint: num(get(&doc, "amt_rate_breakpoint")?)?,
                        backout: num(get(&doc, "amt_rate_backout")?)?,
                    })
                } else {
                    None
                };
                Law::Schedule(Early {
                    standard,
                    aged_addition: aged_addition.filter(|_| year <= 2010),
                    dependent_floor: if year > 2002 { Some(p.f("dependent_standard_deduction_floor")?) } else { None },
                    dependent_addition: num(get(&doc, "dependent_standard_deduction_earned_addition")?)?,
                    capital_gains,
                    tables: Tables { single: scaled("single")?, hoh: scaled("head_of_household")?, married: scaled("married")? },
                    index,
                    amt_share: opt("federal_amt_share")?,
                    amt,
                    amt_rates,
                    flat_tax_rate: opt("flat_tax_rate")?,
                })
            } else {
                let index = p.f("bracket_index")?;
                let phaseout = if !p.in_effect("deduction_phaseout")? {
                    Phaseout::Steps {
                        start: p.f("deduction_phaseout_start")? * index,
                        end: num(get(&doc, "deduction_phaseout_end")?)? * index,
                        step: num(get(&doc, "deduction_phaseout_step")?)? * index,
                        shares: get(&doc, "deduction_phaseout_shares")?
                            .as_sequence()
                            .ok_or("shares must be a list")?
                            .iter()
                            .map(num)
                            .collect::<Result<_, _>>()?,
                    }
                } else {
                    let d = p.value("deduction_phaseout", &[])?;
                    Phaseout::PerStep {
                        start: num(get(d, "start")?)?,
                        increment: num(get(d, "increment")?)?,
                        share_per_step: num(get(d, "share_per_step")?)?,
                    }
                };
                let standard_of = |key: &str| -> Result<f64, String> { p.num_at("standard_deduction", &[key]) };
                let rows: Vec<Vec<f64>> = params::brackets(b)?.into_iter().map(|r| vec![r[0] * index, r[1]]).collect();
                Law::Flat(Later {
                    standard: ByClass { single: standard_of("single")?, hoh: standard_of("head_of_household")?, married: standard_of("married")? },
                    exemption_amount: p.f("exemption_amount")?,
                    index,
                    phaseout,
                    table: Brackets::new(&rows),
                })
            }
        };
        let relief = p.value("property_relief", &[])?;
        let read_relief = |key: &str| -> Result<Relief, String> {
            let c = get(relief, key)?;
            Ok(Relief {
                limit: num(get(c, "limit")?)?,
                inclusive: c.get("inclusive").and_then(serde_yaml::Value::as_bool).unwrap_or(false),
                table: Interp::new(&params::brackets(get(c, "table")?)?),
            })
        };
        let social_security = if p.in_effect("social_security_index")? {
            let limits = get(&doc, "social_security_agi_limit")?;
            Some(SocialSecurity { index: p.f("social_security_index")?, single: num(get(limits, "single")?)?, joint: num(get(limits, "married_joint")?)? })
        } else {
            None
        };
        let retirement = if p.in_effect("retirement_exclusion_per_aged_taxpayer")? {
            let limits = get(&doc, "retirement_exclusion_agi_limit")?;
            Some(Retirement {
                per_aged: p.f("retirement_exclusion_per_aged_taxpayer")?,
                index: p.f("retirement_exclusion_index")?,
                single: num(get(limits, "single")?)?,
                married: num(get(limits, "married")?)?,
            })
        } else {
            None
        };
        Ok(Self {
            year,
            law,
            social_security,
            retirement,
            relief_rent_share: num(get(&doc, "property_relief_rent_share")?)?,
            relief_max: p.f("property_relief_max")?,
            relief_single: read_relief("single")?,
            relief_multi: read_relief("multi")?,
            child_care_share: opt("child_care_share")?,
            eitc_share: opt("eitc_share")?,
            eitc_refundable: if p.in_effect("eitc_share")? { None } else { opt("eitc_refundable_share")? },
            eitc_nonrefundable: opt("eitc_nonrefundable_share")?,
            dividend_adjustment: dividend_adjustment(root)?,
        })
    }
}

fn schedule_tax(t: &Tables, income: f64, is_single: bool, is_hoh: bool, sep: f64) -> f64 {
    if is_single {
        t.single.tax(income)
    } else if is_hoh {
        t.hoh.tax(income)
    } else {
        t.married.tax(income * sep) / sep
    }
}

fn amt_rate_tax(income: f64, sep: f64, r: &AmtRates) -> f64 {
    if income < r.breakpoint / sep { r.low * income } else { (r.high * income - r.backout / sep).max(0.0) }
}

pub fn tax(i: &Inp, f: &Fed, p: &RiParams) -> f64 {
    let y = p.year;
    let fs = i.fs();
    let is_single = fs == Fs::Single;
    let is_hoh = fs == Fs::Hoh;
    let is_joint = fs == Fs::Joint;
    let sep = i.sepret();
    let fed_agi = f.agi;
    let eitc = if y >= 1987 { f.eitc } else { f.pre87.earncr };
    let aged = i.aged_count();
    let txp = i.taxpayer_count();
    let married = is_joint || fs == Fs::Separate;
    let hh = household_income(i, p.dividend_adjustment);
    let ri_ui = crate::states::unemployment_total(i);

    // ---- AGI ----
    let mut agi = fed_agi;
    if let Some(s) = &p.social_security {
        let ss = f.taxable_social_security;
        let joint_sub = if fed_agi < s.joint * s.index { if aged == 1.0 { 0.5 * ss } else { ss } } else { 0.0 };
        // TAXSIM's test for other returns covers single, head of household and separate returns.
        let other_sub = if fed_agi < s.single * s.index { ss } else { 0.0 };
        if aged > 0.0 && ss > 0.0 {
            agi -= if is_joint { joint_sub } else { other_sub };
        }
    }
    if let Some(r) = &p.retirement {
        let fagi = if married { r.married * r.index / sep } else { r.single * r.index };
        if fed_agi <= fagi {
            agi -= (aged * r.per_aged).min(i.pensions);
        }
    }
    if y == 2009 || y == 2020 {
        // Unemployment compensation is taxed in full.
        agi = agi - f.taxable_unemployment + ri_ui;
    }
    // Net long-term gain.
    let ltg = i.ltcg.min(i.stcg + i.ltcg).max(0.0);
    let (taxbc, fed_ccc) = if y <= 1986 { (f.pre87.taxbc, f.federal_chcr) } else { (f.regular_tax, f.federal_chcr) };

    let (mut tax25, mut tax50) = (0.0, 0.0);
    let mut statax = match &p.law {
        Law::Federal(share) => {
            let mut fedtax = taxbc - fed_ccc;
            if y >= 1987 {
                fedtax -= f.federal_elder;
            }
            if y >= 1986 {
                fedtax -= eitc;
            }
            share * fedtax.max(0.0)
        }
        Law::Schedule(e) => {
            let taxinc = if y <= 2002 {
                f.taxable_income
            } else {
                let mut stded = e.standard.pick(is_single, is_hoh);
                let (aged_single, aged_married) = e.aged_addition.unwrap_or((0.0, 0.0));
                stded += aged * if is_single || is_hoh { aged_single } else { aged_married };
                // TAXSIM gives separate returns the full married amount; the statute halves it.
                stded /= sep;
                let dependent_limit = e.dependent_floor.unwrap_or(0.0).max(f.earned_income + e.dependent_addition);
                if i.is_dependent_filer() {
                    stded = stded.min(dependent_limit);
                }
                let deduc = stded.max(f.itemized_deduction.max(0.0));
                (agi - deduc - f.personal_exemptions).max(0.0)
            };
            let mut statax = schedule_tax(&e.tables, taxinc, is_single, is_hoh, sep);
            if let Some(c) = &e.capital_gains {
                let taxnon = (taxinc - ltg).max(0.0);
                let stanon = schedule_tax(&e.tables, taxnon, is_single, is_hoh, sep);
                let br = taxinc.min(c.bracket.pick(is_single, is_hoh) / sep);
                let taxy25 = br - taxnon.min(br);
                let taxy50 = ltg.min(taxinc) - taxy25;
                if ltg > 0.0 {
                    tax25 = c.low_rate * taxy25;
                    tax50 = c.high_rate * taxy50;
                    statax = (stanon + tax25 + tax50).min(statax);
                }
            }
            statax
        }
        Law::Flat(l) => {
            let stded = l.standard.pick(is_single, is_hoh);
            let exemps = federal_exemption_count(y, i);
            let exemp = exemps * l.exemption_amount;
            let factor = match &l.phaseout {
                Phaseout::Steps { start, end, step, shares } => {
                    let number = ((agi - start) / step + 1.0).floor();
                    let mut perc = 1.0;
                    for (n, share) in shares.iter().enumerate().rev() {
                        perc = if number == (n + 1) as f64 { *share } else { perc };
                    }
                    if agi > *end {
                        0.0
                    } else if agi > *start {
                        perc
                    } else {
                        1.0
                    }
                }
                Phaseout::PerStep { start, increment, share_per_step } => {
                    let steps = ((agi - start).max(0.0) / increment).ceil();
                    (1.0 - share_per_step * steps).max(0.0).min(1.0)
                }
            };
            let taxinc = (agi - stded * factor / sep - exemp * factor).max(0.0);
            let _ = l.index;
            l.table.tax(taxinc)
        }
    };

    // ---- minimum tax ----
    if let Law::Schedule(e) = &p.law {
        if let Some(share) = e.amt_share {
            if f.amt > 0.0 {
                statax += (share * f.amt - statax).max(0.0);
            }
        } else if let (Some(a), Some(r)) = (&e.amt, &e.amt_rates) {
            let alminy_federal = f.amt_income;
            let exemption = a.exemption.pick(is_single, is_hoh) / sep;
            let start = a.start.pick(is_single, is_hoh) / sep;
            let exclnt = (exemption - a.rate * (alminy_federal - start).max(0.0)).max(0.0);
            let alminy = (alminy_federal - exclnt).max(0.0);
            let without_gains = (amt_rate_tax(alminy, sep, r) - statax).max(0.0);
            let with_gains = (amt_rate_tax((alminy - ltg).max(0.0), sep, r) + tax25 + tax50 - statax).max(0.0);
            statax += if ltg < 1.0 { without_gains } else { with_gains };
        }
        if let Some(rate) = e.flat_tax_rate {
            statax = statax.min(rate * fed_agi.max(0.0));
        }
    }

    // ---- property tax relief credit for taxpayers 65 or older ----
    let ptax = i.proptax + p.relief_rent_share * i.rentpaid;
    let frac_for = |c: &Relief| -> f64 {
        let within = if c.inclusive { hh <= c.limit } else { hh < c.limit };
        if within { c.table.eval(hh) } else { 0.0 }
    };
    let frac = if txp > 1.0 || i.depx > 0.0 {
        frac_for(&p.relief_multi)
    } else if txp == 1.0 {
        frac_for(&p.relief_single)
    } else {
        0.0
    };
    let pcred = if aged > 0.0 && frac > 0.0 { (ptax - frac * hh).max(0.0).min(p.relief_max) } else { 0.0 };

    // ---- credits ----
    if let Some(share) = p.child_care_share {
        let chcr = share * fed_ccc.min(taxbc.max(0.0));
        statax = (statax - chcr).max(0.0);
    }
    if let Law::Federal(_) = p.law {
        statax = (statax - pcred).max(0.0);
    }
    if let Some(share) = p.eitc_share {
        statax = (statax - pcred - share * eitc).max(0.0);
    } else if let Some(refundable) = p.eitc_refundable {
        let earncr = match p.eitc_nonrefundable {
            Some(share) => {
                let nonrefundable = statax.min(share * eitc);
                nonrefundable + refundable * (share * eitc - nonrefundable)
            }
            None => refundable * eitc,
        };
        statax -= earncr;
        statax -= pcred;
    }
    statax * 1.0
}
