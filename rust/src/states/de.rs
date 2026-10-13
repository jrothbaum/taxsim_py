//! Delaware personal income tax (port of `calculators.states.de`).

use std::path::Path;

use crate::common::Brackets;
use crate::federal::{Fed, Inp};
use crate::params::{self, get, load, num, status_direct, Fs, YearParams};
use crate::states::{dividend_adjustment, law_year_is_exact, unemployment_total};

struct LowIncome {
    exclusion: f64,
    earned_limit: f64,
    agi_limit: f64,
}

enum Standard {
    Percent { rate: f64, cap_per_taxpayer: f64 },
    Fixed { single_or_hoh: f64, married: f64 },
    PerTaxpayer(f64),
}

enum Threshold {
    Flat(f64),
    ByStatus([f64; 4]),
}

struct Limitation {
    rate: f64,
    cap_rate: f64,
    reduction_share: f64,
    threshold: Threshold,
    index: f64,
}

enum Eitc {
    None,
    /// 2006-2020: a share of the federal credit.
    NonRefundable(f64),
    /// 2021 on: the refundable share applies when it exceeds the tax.
    Either { refundable: f64, nonrefundable: f64 },
}

pub struct DeParams {
    year: i64,
    table: Brackets,
    pension_young_per_taxpayer: f64,
    pension_aged: f64,
    low_income: Option<LowIncome>,
    standard: Standard,
    aged_standard: f64,
    itemized_multiplier: Option<f64>,
    state_tax_cap: Option<f64>,
    limitation: Option<Limitation>,
    personal_exemption: Option<f64>,
    federal_tax_exemption: Option<f64>,
    chcr_share: f64,
    chcr_cap: Option<f64>,
    pecred: Option<f64>,
    eitc: Eitc,
    dividend_adjustment: f64,
}

impl DeParams {
    pub fn load(root: &Path, year: i64) -> Result<Self, String> {
        let doc = load(root, "states/de/income_tax.yaml")?;
        law_year_is_exact(root, &doc, year)?;
        let p = YearParams::new(&doc, year);
        let opt = |key: &str| -> Result<Option<f64>, String> { Ok(if p.in_effect(key)? { Some(p.f(key)?) } else { None }) };
        let standard = if p.in_effect("standard_deduction_percent")? {
            let sd = p.value("standard_deduction_percent", &[])?;
            Standard::Percent { rate: num(get(sd, "rate")?)?, cap_per_taxpayer: num(get(sd, "cap_per_taxpayer")?)? }
        } else if p.in_effect("standard_deduction")? {
            let sd = p.value("standard_deduction", &[])?;
            Standard::Fixed { single_or_hoh: num(get(sd, "single_or_hoh")?)?, married: num(get(sd, "married")?)? }
        } else {
            Standard::PerTaxpayer(p.f("standard_deduction_per_taxpayer")?)
        };
        let limitation = match p.opt("itemized_limitation")? {
            Some(l) => {
                let t = get(l, "threshold")?;
                Some(Limitation {
                    rate: num(get(l, "rate")?)?,
                    cap_rate: num(get(l, "cap_rate")?)?,
                    reduction_share: num(get(l, "reduction_share")?)?,
                    threshold: if t.is_mapping() { Threshold::ByStatus(status_direct(t)?) } else { Threshold::Flat(num(t)?) },
                    index: p.f("itemized_limitation_index")?,
                })
            }
            None => None,
        };
        let low_income = match p.opt("aged_low_income_exclusion")? {
            Some(e) => Some(LowIncome {
                exclusion: num(get(e, "exclusion")?)?,
                earned_limit: num(get(e, "earned_limit")?)?,
                agi_limit: num(get(e, "agi_limit")?)?,
            }),
            None => None,
        };
        let eitc = if p.in_effect("eitc_refundable_share")? {
            Eitc::Either { refundable: p.f("eitc_refundable_share")?, nonrefundable: p.f("eitc_federal_share")? }
        } else if p.in_effect("eitc_federal_share")? {
            Eitc::NonRefundable(p.f("eitc_federal_share")?)
        } else {
            Eitc::None
        };
        Ok(Self {
            year,
            table: Brackets::new(&params::brackets(p.value("brackets", &[])?)?),
            pension_young_per_taxpayer: num(get(&doc, "pension_exclusion_young_per_taxpayer")?)?,
            pension_aged: p.f("pension_exclusion_aged")?,
            low_income,
            standard,
            aged_standard: if year >= 1987 { p.f("aged_standard_deduction")? } else { 0.0 },
            itemized_multiplier: if year > 1986 && year <= 2017 { opt("itemized_deduction_multiplier")? } else { None },
            state_tax_cap: if year > 2017 { opt("state_tax_deduction_cap")? } else { None },
            limitation,
            personal_exemption: opt("personal_exemption")?,
            federal_tax_exemption: if p.in_effect("personal_exemption")? { opt("federal_tax_exemption_per_taxpayer")? } else { None },
            chcr_share: p.f("child_care_credit_federal_share")?,
            chcr_cap: opt("child_care_credit_cap")?,
            pecred: opt("personal_exemption_credit")?,
            eitc,
            dividend_adjustment: dividend_adjustment(root)?,
        })
    }
}

pub fn tax(i: &Inp, f: &Fed, p: &DeParams) -> f64 {
    let y = p.year;
    let fs = i.fs();
    let sep = i.sepret();
    let taxpayers = i.taxpayer_count();
    let aged = i.aged_count();

    // Federal exemptions, floored; 1989 adds the aged.
    let exemps_raw = if y <= 1986 {
        taxpayers + i.depx + aged
    } else if i.is_dependent_filer() {
        0.0
    } else {
        taxpayers + i.depx
    };
    let mut de_num = exemps_raw.floor();
    if y == 1989 {
        de_num += aged;
    }

    // ---- AGI ---- federal AGI less taxable Social Security (2021 also excludes unemployment compensation).
    let mut agi = f.agi - f.taxable_social_security;
    if y == 2021 {
        agi -= unemployment_total(i);
    }
    // Pension exclusion.
    let pensions = i.pensions;
    let investment = i.intrec + i.dividends + p.dividend_adjustment + (i.stcg + i.ltcg).max(0.0);
    let penexc = (if aged < 1.0 {
        (p.pension_young_per_taxpayer * taxpayers).min(pensions)
    } else if aged == 1.0 {
        p.pension_aged.min(pensions + 0.5 * investment)
    } else {
        (2.0 * p.pension_aged).min(pensions + investment)
    })
    .max(0.0);
    agi -= penexc;
    if let Some(e) = &p.low_income {
        let low = aged > 0.0 && f.earned_income < e.earned_limit * aged && agi <= e.agi_limit * aged;
        if low {
            agi -= e.exclusion * aged;
        }
    }

    // ---- standard deduction ----
    let mut stded = match &p.standard {
        Standard::Percent { rate, cap_per_taxpayer } => (cap_per_taxpayer * taxpayers / sep).min(rate * agi.max(0.0)),
        Standard::Fixed { single_or_hoh, married } => {
            if fs == Fs::Single || fs == Fs::Hoh { *single_or_hoh } else { married / sep }
        }
        Standard::PerTaxpayer(amount) => amount * taxpayers,
    };
    if y >= 1987 {
        stded += p.aged_standard * aged;
    }

    // ---- itemized deduction ---- Through 1986 the itemized inputs.
    let salt = f.ded;
    let raw_itemized = if y <= 1986 { i.proptax + i.otheritem + i.mortgage } else { f.salt_capped + i.mortgage };
    let xitded_base = if y <= 1986 {
        raw_itemized
    } else if y <= 2017 {
        let mut b = (raw_itemized - salt).max(0.0);
        if let Some(m) = p.itemized_multiplier {
            b *= m;
        }
        b
    } else if let Some(cap) = p.state_tax_cap {
        // Only the state tax deduction's share of the cap is removed.
        let sttax = (cap / sep).min(i.proptax + salt + i.otheritem);
        let room = sttax - (i.proptax + i.otheritem);
        (raw_itemized - salt.min(room)).max(0.0)
    } else {
        raw_itemized
    };
    let mut xitded = match &p.limitation {
        Some(l) => {
            let phas92 = match &l.threshold {
                Threshold::ByStatus(by_status) => by_status[fs as usize] * l.index,
                Threshold::Flat(v) => v * l.index / sep,
            };
            let reduce = if agi > phas92 { (l.cap_rate * xitded_base).min(l.rate * (agi - phas92)) } else { 0.0 };
            (xitded_base - reduce * l.reduction_share).max(0.0)
        }
        None => xitded_base,
    };
    // A forced standard deduction zeroes itemized deductions in 1999 only.
    if y == 1999 && !f.forced_itemize {
        xitded = 0.0;
    }
    let deduc = stded.max(xitded);

    // ---- exemption (through 1995) ----
    let exemp = match p.personal_exemption {
        Some(amount) => {
            let mut e = de_num * amount;
            if let Some(per_taxpayer) = p.federal_tax_exemption {
                // Plus federal income tax up to an amount per taxpayer.
                e += f.fiitax.max(0.0).min(per_taxpayer * taxpayers);
            }
            e
        }
        None => 0.0,
    };
    let taxinc = (agi - deduc - exemp).max(0.0);

    // ---- joint returns: the lower of the joint tax and the tax on each earner's share ----
    let is_joint_relief = fs == Fs::Joint && agi > 0.0;
    let wages = i.pwages + i.swages;
    let higher = i.pwages.max(i.swages) + (agi - wages) / 2.0;
    let lower = agi - higher;
    let dedh = if agi != 0.0 { deduc * higher / agi } else { 0.0 };
    let dedw = deduc - dedh;
    let taxinh = (higher - dedh - 0.5 * exemp).max(0.0);
    let taxinw = (lower - dedw - 0.5 * exemp).max(0.0);

    let stat = p.table.tax(taxinc);
    let mut statax = if is_joint_relief { stat.min(p.table.tax(taxinh) + p.table.tax(taxinw)) } else { stat };

    // ---- child/dependent care credit ----
    let mut chcr = f.federal_chcr.max(0.0) * p.chcr_share;
    if let Some(cap) = p.chcr_cap {
        chcr = chcr.min(cap);
    }
    // ---- personal exemption credit (1996+) ----
    let pecred = p.pecred.map_or(0.0, |per_unit| (de_num + aged) * per_unit);
    statax = (statax - (chcr + pecred)).max(0.0);

    // ---- EITC (2006+) ----
    match &p.eitc {
        Eitc::Either { refundable, nonrefundable } => {
            let refundable_amt = refundable * f.eitc.max(0.0);
            let nonrefundable_amt = nonrefundable * f.eitc.max(0.0);
            let use_refundable = refundable_amt > statax;
            let earncr = if use_refundable { refundable_amt } else { nonrefundable_amt };
            statax = if use_refundable { statax - earncr } else { (statax - earncr).max(0.0) };
        }
        Eitc::NonRefundable(share) => {
            statax = (statax - share * f.eitc.max(0.0)).max(0.0);
        }
        Eitc::None => {}
    }
    statax * 1.0
}
