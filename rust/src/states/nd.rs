//! North Dakota income tax (port of `calculators.states.nd`).

use std::path::Path;

use crate::common::Brackets;
use crate::federal::{Fed, Inp};
use crate::params::{self, get, load, num, Fs, YearParams};
use crate::states::{dividend_adjustment, dividend_exclusion_addback, federal_exemption_count, law_year_is_exact, pre1987_gross_itemized};

fn scaled(rows: Vec<Vec<f64>>, factor: f64) -> Vec<Vec<f64>> {
    rows.into_iter().map(|r| vec![r[0] * factor, r[1]]).collect()
}

struct ShortForm {
    rate: f64,
    energy_credit: Option<f64>,
}

struct MarriageCredit {
    deduction: f64,
    maximum: f64,
    earnings_min: f64,
    taxable_income_min: f64,
    single: Brackets,
}

/// The rate schedule: one table, or (from 2001) one for each filing status.
enum Schedule {
    One(Brackets),
    ByStatus { single: Brackets, hoh: Brackets, married: Brackets },
}

struct Percent {
    rate: f64,
    single: (f64, f64),
    married: (f64, f64),
}

pub struct NdParams {
    year: i64,
    schedule: Schedule,
    additional_exemption: Option<f64>,
    // Through 1986.
    capital_gains_addback_share: Option<f64>,
    interest_exclusion: Option<f64>,
    percent: Option<Percent>,
    exemption_amount: Option<f64>,
    // 2001 on.
    capital_gain_exclusion: f64,
    qualified_dividend_exclusion: f64,
    ss_agi_limit: Option<f64>,
    contribution_credit: Option<(f64, f64, f64)>,
    short_form: Option<ShortForm>,
    marriage: Option<MarriageCredit>,
    relief_credit: Option<f64>,
    dividend_adjustment: f64,
}

impl NdParams {
    pub fn load(root: &Path, year: i64) -> Result<Self, String> {
        let doc = load(root, "states/nd/income_tax.yaml")?;
        law_year_is_exact(root, &doc, year)?;
        let p = YearParams::new(&doc, year);
        let opt = |key: &str| -> Result<Option<f64>, String> { Ok(if p.in_effect(key)? { Some(p.f(key)?) } else { None }) };
        let inflation = if year >= 2001 { p.f("bracket_inflation")? } else { 1.0 };

        let tables = p.value("brackets", &[])?;
        let schedule = if tables.is_mapping() {
            let table = |key: &str| -> Result<Brackets, String> {
                Ok(Brackets::new(&scaled(params::brackets(get(tables, key)?)?, inflation)))
            };
            Schedule::ByStatus { single: table("single")?, hoh: table("head_of_household")?, married: table("married")? }
        } else {
            Schedule::One(Brackets::new(&params::brackets(tables)?))
        };
        let percent = match p.opt("standard_deduction_percent")? {
            Some(l) => {
                let pair = |key: &str| -> Result<(f64, f64), String> {
                    let v = get(l, key)?.as_sequence().ok_or("expected a pair")?;
                    Ok((num(&v[0])?, num(&v[1])?))
                };
                Some(Percent { rate: num(get(l, "rate")?)?, single: pair("single")?, married: pair("married")? })
            }
            None => None,
        };
        let marriage = if year >= 2007 {
            let single = match tables.get("single") {
                Some(s) => Brackets::new(&scaled(params::brackets(s)?, inflation)),
                None => return Err("the marriage credit needs the single schedule".into()),
            };
            Some(MarriageCredit {
                deduction: p.f("marriage_credit_deduction")?,
                maximum: p.f("marriage_credit_max")?,
                earnings_min: p.f("marriage_credit_earnings_min")?,
                taxable_income_min: p.f("marriage_credit_taxable_income_min")?,
                single,
            })
        } else {
            None
        };
        let short_form = match opt("short_form_rate")? {
            Some(rate) => Some(ShortForm { rate, energy_credit: opt("energy_credit")? }),
            None => None,
        };
        let contribution_credit = match opt("contribution_credit_cap")? {
            Some(cap) => Some((num(get(&doc, "contribution_credit_rate")?)?, num(get(&doc, "contribution_credit_tax_share")?)?, cap)),
            None => None,
        };
        Ok(Self {
            year,
            schedule,
            additional_exemption: opt("additional_exemption")?,
            capital_gains_addback_share: if year <= 1986 { opt("capital_gains_deduction_addback_share")? } else { None },
            interest_exclusion: if year <= 1986 { opt("interest_exclusion_per_taxpayer")? } else { None },
            percent: if year <= 1986 { percent } else { None },
            exemption_amount: if year <= 1986 { Some(p.f("exemption_amount")?) } else { None },
            capital_gain_exclusion: if year >= 2001 { p.f("capital_gain_exclusion")? } else { 0.0 },
            qualified_dividend_exclusion: if year >= 2001 { p.f("qualified_dividend_exclusion")? } else { 0.0 },
            ss_agi_limit: if year >= 2019 { opt("social_security_subtraction_agi_limit_per_taxpayer")? } else { None },
            contribution_credit,
            short_form,
            marriage,
            relief_credit: opt("relief_credit")?,
            dividend_adjustment: dividend_adjustment(root)?,
        })
    }
}

/// Federal income tax deducted through 2000 (`fded`).
fn federal_tax_deduction(y: i64, f: &Fed) -> f64 {
    let (pretax, credit, earncr, almtax);
    if y <= 1986 {
        pretax = f.pre87.pretax;
        credit = f.pre87.credit;
        earncr = f.pre87.earncr;
        almtax = f.pre87.almtax;
    } else {
        // Federal tax before credits as TAXSIM reconstructs it.
        let regtax = f.regular_tax;
        almtax = f.amt;
        credit = f.nonrefundable_credits;
        earncr = f.eitc;
        let before = if y >= 2000 { regtax + almtax } else { regtax };
        // Before 1998 the reported credit total does not reduce federal tax.
        let taxaft = (before - if y >= 1998 { credit } else { 0.0 }).max(0.0);
        pretax = f.schedule_tax.max(almtax).max(taxaft - earncr + earncr.min(taxaft) + credit);
    }
    (pretax - credit - earncr + almtax).max(0.0)
}

pub fn tax(i: &Inp, f: &Fed, p: &NdParams) -> f64 {
    let y = p.year;
    let fs = i.fs();
    let is_single = fs == Fs::Single;
    let is_joint = fs == Fs::Joint;
    let is_hoh = fs == Fs::Hoh;
    let sep = i.sepret();
    let txp = i.taxpayer_count();
    let fed_agi = f.agi;
    let addex = match p.additional_exemption {
        Some(amount) if is_joint || is_hoh => amount,
        _ => 0.0,
    };
    let fded = if y <= 2000 { federal_tax_deduction(y, f) } else { 0.0 };

    let (taxinc, mut statax);
    if y <= 1986 {
        // ---- AGI ----
        let mut agi = fed_agi;
        if let Some(share) = p.capital_gains_addback_share {
            let capded = (i.stcg + i.ltcg - f.pre87.capgn).max(0.0);
            agi += capded * share;
        } else if y <= 1982 {
            agi -= i.intrec.min(p.interest_exclusion.unwrap_or(0.0) * txp);
            if y == 1982 {
                agi += dividend_exclusion_addback(y, i, f, p.dividend_adjustment);
            }
        } else {
            if y == 1985 {
                agi -= f.taxable_social_security;
            }
            agi -= i.intrec.min(p.interest_exclusion.unwrap_or(0.0) * txp);
        }
        // ---- federal itemized deduction and zero bracket ----
        let gross = pre1987_gross_itemized(i, f);
        let fed_itemizes = f.pre87.itemizes;
        let exemps = federal_exemption_count(y, i);
        let xitded = if fed_itemizes { gross } else { 0.0 };
        let stded = match &p.percent {
            Some(l) => {
                let single_std = (l.rate * agi).max(l.single.0).min(l.single.1);
                let married_std = (l.rate * agi).max(l.married.0 / sep).min(l.married.1 / sep);
                if is_single || is_hoh { single_std } else { married_std }
            }
            None => f.pre87.zbr,
        };
        let deduc = (if fed_itemizes { xitded } else { stded.max(xitded) }) + fded;
        let exemp = exemps * p.exemption_amount.unwrap_or(0.0) + addex;
        taxinc = (agi - deduc - exemp).max(0.0);
        statax = match &p.schedule {
            Schedule::One(t) => t.tax(taxinc),
            Schedule::ByStatus { .. } => 0.0,
        };
    } else if y <= 2000 {
        // State income tax deducted federally is added back for itemizers.
        let addback = if f.itemizes { f.ded } else { 0.0 };
        taxinc = (f.taxable_income + addback - fded - addex).max(0.0);
        statax = match &p.schedule {
            Schedule::One(t) => t.tax(taxinc),
            Schedule::ByStatus { .. } => 0.0,
        };
    } else {
        let (gshort, glong) = (i.stcg, i.ltcg);
        let fullcg = gshort + glong;
        let cgltg = if fullcg > 0.0 && glong > 0.0 && gshort >= 0.0 {
            glong
        } else if fullcg > 0.0 && glong > 0.0 && gshort < 0.0 {
            fullcg
        } else {
            0.0
        };
        let cgexc = p.capital_gain_exclusion * cgltg.max(0.0);
        let qdiv = p.qualified_dividend_exclusion * (i.dividends + p.dividend_adjustment);
        let mut t = (f.taxable_income - cgexc - qdiv).max(0.0);
        // Taxable Social Security is subtracted (2019-2020 only up to an AGI limit).
        if y >= 2019 {
            let mut ss_taxable = f.taxable_social_security;
            if let Some(limit) = p.ss_agi_limit {
                if !(fed_agi <= limit * txp) {
                    ss_taxable = 0.0;
                }
            }
            t = (t - ss_taxable).max(0.0);
        }
        taxinc = t;
        statax = match &p.schedule {
            Schedule::ByStatus { single, hoh, married } => {
                if is_single {
                    single.tax(taxinc)
                } else if is_hoh {
                    hoh.tax(taxinc)
                } else {
                    married.tax(taxinc * sep) / sep
                }
            }
            Schedule::One(_) => 0.0,
        };
    }

    // ---- contribution credit (through 1999) ----
    if let Some((rate, share, cap)) = p.contribution_credit {
        let contcr = (rate * i.charity_cash).min(share * statax).min(cap);
        statax = (statax - contcr).max(0.0);
    }

    // ---- short form: a share of federal tax before credits (1981-2000) ----
    if let Some(s) = &p.short_form {
        let fedtax = if y <= 1986 { f.pre87.taxbc } else { f.regular_tax };
        let short_tax = s.rate * fedtax;
        statax = statax.min(short_tax);
        if let Some(energy) = s.energy_credit {
            let credit = statax.max(0.0).min(energy);
            statax = (statax - credit).max(0.0);
        }
    }

    // ---- marriage credit (2007 on, joint returns) ----
    if let Some(m) = &p.marriage {
        let pw = i.pwages.max(0.0);
        let sw = i.swages.max(0.0);
        let mut lower = pw.min(sw);
        if y >= 2022 {
            // Pensions are shared between spouses.
            let half = i.pensions / 2.0;
            lower = (pw + half).min(sw + half);
        }
        let taxin3 = (lower - m.deduction).max(0.0);
        let taxin4 = (taxinc - taxin3).max(0.0);
        let mut crdmar = (statax - m.single.tax(taxin3) - m.single.tax(taxin4)).max(0.0).min(m.maximum);
        let eligible = is_joint && taxinc > m.taxable_income_min && lower > m.earnings_min;
        if !eligible {
            crdmar = 0.0;
        }
        statax = (statax - crdmar).max(0.0);
    }

    // ---- tax relief credit 2021 ----
    if let Some(credit) = p.relief_credit {
        statax = (statax - credit * txp).max(0.0);
    }
    statax * 1.0
}
