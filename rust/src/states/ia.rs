//! Iowa income tax (port of `calculators.states.ia`).

use std::path::Path;

use crate::common::Brackets;
use crate::federal::{Fed, Inp};
use crate::params::{self, get, load, num, status_direct, Fs, YearParams};
use crate::states::{higher_earner_share, law_year_is_exact, unemployment_total, Interp};

struct Amt {
    share_of_federal: Option<f64>,
    rate: Option<f64>,
    base: [f64; 4],
    threshold: [f64; 4],
    phaseout_rate: f64,
}

struct Worksheet {
    share: f64,
    start: [f64; 3],
    maximum: [f64; 3],
    rate: [f64; 3],
}

struct Caps {
    none: f64,
    one: f64,
    two_or_more: f64,
}

enum Care {
    ExpenseRate(f64),
    Flat(f64),
    Table(Interp),
}

pub struct IaParams {
    year: i64,
    threshold: f64,
    threshold_by_status: Option<[f64; 4]>,
    index: f64,
    two_earner: Option<(f64, f64)>,
    dividend_interest: Option<f64>,
    ss_base: [f64; 3],
    ss_share: Option<f64>,
    pension_cap: Option<f64>,
    retirement: Option<(f64, f64)>,
    unemployment_addback: Option<f64>,
    federal_tax_deduction: bool,
    qbi_share: f64,
    pct: f64,
    cap_single: f64,
    cap_joint: f64,
    conforms: bool,
    phaseout_rate: f64,
    cap_rate: f64,
    combined_allowed: bool,
    aif: f64,
    table: Brackets,
    by_status: Option<(Brackets, Brackets)>,
    no_tax_single: f64,
    no_tax_other: f64,
    aged_single: f64,
    aged_other: f64,
    alternate_rate: Option<f64>,
    alternate_aged_floor: Option<f64>,
    personal: f64,
    personal_addition: Option<f64>,
    amt: Option<Amt>,
    care: Care,
    eitc: Option<(f64, f64)>,
    worksheet: Option<Worksheet>,
    caps: Option<Caps>,
    joint_caps: Option<Caps>,
}

impl IaParams {
    pub fn load(root: &Path, year: i64) -> Result<Self, String> {
        let doc = load(root, "states/ia/income_tax.yaml")?;
        law_year_is_exact(root, &doc, year)?;
        let pre87_doc = load(root, "national/pre1987.yaml")?;
        let p = YearParams::new(&doc, year);
        let opt = |key: &str| -> Result<Option<f64>, String> { Ok(if p.in_effect(key)? { Some(p.f(key)?) } else { None }) };
        let triple = |v: &serde_yaml::Value, a: &str, b: &str, c: &str| -> Result<[f64; 3], String> {
            Ok([num(get(v, a)?)?, num(get(v, b)?)?, num(get(v, c)?)?])
        };
        let caps = |v: &serde_yaml::Value| -> Result<Caps, String> {
            Ok(Caps { none: num(get(v, "none")?)?, one: num(get(v, "one")?)?, two_or_more: num(get(v, "two_or_more")?)? })
        };

        let threshold = p.value("itemized_limitation_threshold", &[])?;
        let ss = get(&doc, "social_security_base")?;
        let share = p.value("child_care_credit_federal_share", &[])?;
        let care = if p.in_effect("child_care_credit_expense_rate")? {
            Care::ExpenseRate(p.f("child_care_credit_expense_rate")?)
        } else if share.is_sequence() {
            Care::Table(Interp::new(&params::brackets(share)?))
        } else {
            Care::Flat(num(share)?)
        };
        let brackets_by_status = match p.opt("brackets_by_status")? {
            Some(s) => Some((
                Brackets::new(&params::brackets(get(s, "joint")?)?),
                Brackets::new(&params::brackets(get(s, "other")?)?),
            )),
            None => None,
        };
        let amt = if p.in_effect("amt_share_of_federal")? || p.in_effect("amt_rate")? {
            Some(Amt {
                share_of_federal: opt("amt_share_of_federal")?,
                rate: opt("amt_rate")?,
                base: status_direct(get(&doc, "amt_exclusion_base_by_status")?)?,
                threshold: status_direct(get(&doc, "amt_exclusion_phaseout_threshold_by_status")?)?,
                phaseout_rate: num(get(&doc, "amt_exclusion_phaseout_rate")?)?,
            })
        } else {
            None
        };
        let aged = get(&doc, "amt_aged_exempt_income")?;
        // Eligibility caps apply only in the year TAXSIM models the 2010 law (statutory mode applies them in that year alone).
        let caps_year = year == 2010 && p.in_effect("eitc_eligibility_caps")?;
        Ok(Self {
            year,
            threshold: if threshold.is_mapping() { 0.0 } else { num(threshold)? },
            threshold_by_status: if threshold.is_mapping() { Some(status_direct(threshold)?) } else { None },
            index: p.f("itemized_limitation_index")?,
            two_earner: if (1982..=1984).contains(&year) {
                let q = YearParams::new(&pre87_doc, year);
                Some((q.f("two_earner_deduction_rate")?, q.f("two_earner_deduction_cap")?))
            } else {
                None
            },
            dividend_interest: opt("dividend_interest_exclusion_per_taxpayer")?,
            ss_base: triple(ss, "joint", "married_separate", "single")?,
            ss_share: if year <= 2013 { opt("social_security_phaseout_share")? } else { None },
            pension_cap: if year >= 1995 { Some(p.f("pension_exclusion")?) } else { None },
            retirement: match p.opt("retirement_exclusion")? {
                Some(r) => Some((num(get(r, "minimum_age")?)?, num(get(r, "cap_per_taxpayer")?)?)),
                None => None,
            },
            unemployment_addback: opt("unemployment_addback_per_taxpayer")?,
            federal_tax_deduction: p.value("federal_tax_deduction", &[])?.as_bool().ok_or("expected a boolean")?,
            qbi_share: if year >= 2019 { p.f("qbi_deduction_share")? } else { 0.0 },
            pct: p.f("standard_deduction_pct")?,
            cap_single: p.num_at("standard_deduction_cap", &["single"])?,
            cap_joint: p.num_at("standard_deduction_cap", &["joint_or_hoh"])?,
            conforms: p.value("conforms_to_federal_standard_deduction", &[])?.as_bool().ok_or("expected a boolean")?,
            phaseout_rate: if (2018..=2019).contains(&year) { num(get(&doc, "itemized_phaseout_rate")?)? } else { 0.0 },
            cap_rate: if (2018..=2019).contains(&year) { num(get(&doc, "itemized_phaseout_cap_rate")?)? } else { 0.0 },
            combined_allowed: p.value("combined_return_allowed", &[])?.as_bool().ok_or("expected a boolean")?,
            aif: p.f("bracket_inflation")?,
            table: Brackets::new(&params::brackets(p.value("brackets", &[])?)?),
            by_status: brackets_by_status,
            no_tax_single: p.num_at("no_tax_income_limit", &["single"])?,
            no_tax_other: p.num_at("no_tax_income_limit", &["other"])?,
            aged_single: num(get(aged, "single")?)?,
            aged_other: num(get(aged, "other")?)?,
            alternate_rate: opt("alternate_tax_rate")?,
            alternate_aged_floor: if year >= 2007 && p.in_effect("alternate_tax_rate")? { Some(p.f("alternate_tax_aged_floor")?) } else { None },
            personal: p.f("personal_exemption_credit")?,
            personal_addition: opt("personal_credit_taxpayer_addition")?,
            amt,
            care,
            eitc: if year >= 1990 { Some((p.f("eitc_rate")?, p.f("eitc_agi_cap")?)) } else { None },
            worksheet: match p.opt("eitc_joint_worksheet")? {
                Some(e) if year >= 1990 => Some(Worksheet {
                    share: num(get(e, "share")?)?,
                    start: triple(get(e, "phaseout_start")?, "none", "one_or_two", "more")?,
                    maximum: triple(get(e, "maximum")?, "none", "one", "more")?,
                    rate: triple(get(e, "phaseout_rate")?, "none", "one", "more")?,
                }),
                _ => None,
            },
            caps: if caps_year { Some(caps(p.value("eitc_eligibility_caps", &[])?)?) } else { None },
            joint_caps: if caps_year && p.in_effect("eitc_joint_eligibility_caps")? {
                Some(caps(p.value("eitc_joint_eligibility_caps", &[])?)?)
            } else {
                None
            },
        })
    }
}

pub fn tax(i: &Inp, f: &Fed, p: &IaParams) -> f64 {
    let y = p.year;
    let fs = i.fs();
    let is_joint = fs == Fs::Joint;
    let is_hoh = fs == Fs::Hoh;
    let is_single = fs == Fs::Single;
    let sep = i.sepret();
    let txp = i.taxpayer_count();
    let aged = i.aged_count();
    let depx = i.depx;
    let setax = f.payroll.setax;
    let salt = f.ded;
    let phas92 = match &p.threshold_by_status {
        Some(by_status) => by_status[fs as usize] * p.index,
        None => p.threshold * p.index / sep,
    };

    // ---- AGI ----
    let mut agi = f.agi + 0.5 * setax;
    if let Some((rate, cap)) = p.two_earner {
        if is_joint {
            agi += (rate * i.pwages.min(i.swages).max(0.0)).max(0.0).min(cap);
        }
    }
    if let Some(per_taxpayer) = p.dividend_interest {
        let divexc = (i.dividends + i.intrec).min(f.pre87.divexc);
        let own_cap = (i.dividends + i.intrec).max(0.0).min(per_taxpayer * txp);
        agi += divexc - own_cap;
    }
    // Social Security: federal taxable benefits out; through 2013 Iowa's own partial taxation in.
    let pha = if is_joint { p.ss_base[0] } else if fs == Fs::Separate { p.ss_base[1] } else { p.ss_base[2] };
    agi -= f.taxable_social_security;
    let half = 0.5 * i.gssi;
    let addph;
    if y <= 2013 {
        let mut ssb = (0.5 * (half + agi.max(0.0) - pha).max(0.0)).min(half);
        if let Some(share) = p.ss_share {
            ssb *= 1.0 - share;
        }
        if !(i.gssi > 0.0) {
            ssb = 0.0;
        }
        agi += ssb;
        addph = f.taxable_social_security - ssb;
    } else {
        let provisional = f.agi - f.taxable_social_security + half;
        addph = if i.gssi > 0.0 { half.min((provisional - pha).max(0.0) / 2.0) } else { 0.0 };
    }
    // Pension exclusion, taxpayers 65 or older.
    let mut penexc = match p.pension_cap {
        Some(cap) if aged > 0.0 => (txp * cap).min(i.pensions),
        _ => 0.0,
    };
    if let Some((minimum_age, cap)) = p.retirement {
        let eligible = i.page >= minimum_age || i.sage >= minimum_age;
        penexc = if eligible { (cap * txp).min(i.pensions) } else { 0.0 };
    }
    agi -= penexc;
    if let Some(per_taxpayer) = p.unemployment_addback {
        agi += unemployment_total(i).min(per_taxpayer * txp);
    }

    let mut fedded = f.fiitax.max(0.0);
    if y >= 2022 {
        fedded = (f.fiitax - f.payroll.addmed).max(0.0);
    }
    if !p.federal_tax_deduction {
        fedded = 0.0;
    }
    let dedbus = p.qbi_share * f.qbi_deduction;

    // ---- standard deduction ----
    let cap = if matches!(fs, Fs::Single | Fs::Separate) { p.cap_single } else { p.cap_joint };
    let mut stded = (p.pct * agi).max(0.0).min(cap);
    if p.conforms {
        stded = f.standard_deduction;
    }

    // ---- itemized deduction ----
    let smg = if y <= 1986 { i.proptax + i.otheritem + i.mortgage + salt } else { f.salt_capped + i.mortgage };
    let mut xitded = (smg - salt).max(0.0);
    let over_thr = agi > phas92 && (1991..=2017).contains(&y) && f.itemizes;
    let state_share = salt * f.itemized_deduction / smg.max(1e-9);
    if over_thr {
        xitded = (f.itemized_deduction - state_share).max(0.0);
    }
    if (2018..=2019).contains(&y) {
        let deduc1 = i.proptax + i.otheritem + i.mortgage;
        let dlim1 = p.phaseout_rate * (agi - phas92).max(0.0);
        let dlim2 = p.cap_rate * deduc1;
        xitded = (deduc1 - dlim1.min(dlim2)).max(0.0);
    }
    if y >= 2020 {
        xitded = smg;
    }
    if !f.forced_itemize {
        xitded = 0.0;
    }
    let deduc = stded.max(xitded);

    // ---- taxable income ---- Half of self-employment tax is already in AGI, and the whole amount is added again.
    let taxinc = (agi + setax - fedded - dedbus - deduc - if y >= 2025 { f.senior_deduction } else { 0.0 }).max(0.0);

    // ---- joint returns may file combined: each spouse on their own share ----
    let agih = higher_earner_share(i, agi);
    let agiw = agi - agih;
    let is_mfc = is_joint && agi > 0.0 && p.combined_allowed;
    let stded_h_general = (p.pct * agi).max(0.0).min(p.cap_single);
    let xitdh = if agi != 0.0 { xitded * agih / agi } else { 0.0 };
    let xitdw = xitded - xitdh;
    let dedh_general = if stded_h_general >= xitdw { stded_h_general } else { xitdh };
    let dedw_general = stded_h_general.max(xitdw);
    let feddh = if agi != 0.0 { fedded * agih / agi } else { 0.0 };
    let feddw = fedded - feddh;
    let agiw_positive = is_mfc && agiw > 0.0;
    let taxinh = if agiw_positive {
        (agih - dedh_general + 0.25 * setax - feddh - 0.5 * dedbus).max(0.0)
    } else if is_mfc {
        taxinc
    } else {
        0.0
    };
    let taxinw = if agiw_positive { (agiw - dedw_general + 0.25 * setax - feddw - 0.5 * dedbus).max(0.0) } else { 0.0 };

    // ---- tax ----
    let aif = p.aif;
    let mut statax = p.table.tax(taxinc / aif) * aif;
    if let Some((joint, other)) = &p.by_status {
        statax = if is_joint { joint.tax(taxinc) } else { other.tax(taxinc) };
    }
    if is_mfc {
        let (h, w) = if y <= 1986 {
            let denom = agi.max(1e-9);
            (
                (agih - dedh_general + setax * agih / denom - fedded * agih / denom).max(0.0),
                (agiw - dedw_general + setax * agiw / denom - fedded * agiw / denom).max(0.0),
            )
        } else {
            (taxinh, taxinw)
        };
        let split = p.table.tax(h / aif) * aif + p.table.tax(w / aif) * aif;
        statax = statax.min(split);
    }

    // ---- alternate tax (never single) ----
    let not_single = !is_single;
    if let Some(rate) = p.alternate_rate {
        let altax = if y <= 1997 {
            (agi - p.no_tax_other).max(0.0) * rate
        } else {
            let mut subtr = p.no_tax_other;
            if let Some(floor) = p.alternate_aged_floor {
                if aged > 0.0 {
                    subtr = floor;
                }
            }
            (agi + penexc + addph - subtr).max(0.0) * rate
        };
        if not_single {
            statax = statax.min(altax);
        }
    }

    // ---- personal exemption credit ----
    let xmp = p.personal;
    let gcred = if let Some(extra) = p.personal_addition {
        let g = (xmp + extra) * (txp + aged) + xmp * depx;
        if is_hoh { g + xmp + extra } else { g }
    } else if y <= 1997 {
        let g = (xmp / 2.0) * (txp + aged) + xmp * depx;
        if is_hoh { g + xmp / 2.0 } else { g }
    } else {
        let g = xmp * (depx + txp) + (xmp / 2.0) * aged;
        if is_hoh { g + xmp } else { g }
    };
    statax = (statax - gcred).max(0.0);

    // ---- AMT ----
    if let Some(a) = &p.amt {
        let alty = if let Some(share) = a.share_of_federal {
            f.amt * share
        } else {
            // Preferences: property tax only.
            let mut alminy = taxinc + i.proptax;
            if (1991..=2017).contains(&y) && over_thr {
                alminy -= state_share;
            }
            let base = a.base[fs as usize];
            let threshold = a.threshold[fs as usize];
            let exclnt = if alminy > threshold { (base - a.phaseout_rate * (alminy - threshold)).max(0.0) } else { base };
            (a.rate.unwrap_or(0.0) * (alminy - exclnt) - statax).max(0.0)
        };
        let aged_b = aged > 0.0;
        let exempt = (is_single && !aged_b && agi <= p.no_tax_single)
            || (is_single && aged_b && agi <= p.aged_single)
            || (not_single && !aged_b && agi <= p.no_tax_other)
            || (not_single && aged_b && agi <= p.aged_other);
        statax += if exempt { 0.0 } else { alty };
    }

    // ---- child/dependent care credit ----
    let posagi = agi.max(0.0);
    let ccc_base = if y <= 1986 { f.federal_chcr } else { f.ccc_uncapped };
    let chcr = match &p.care {
        Care::ExpenseRate(rate) => i.childcare * rate,
        Care::Flat(share) => ccc_base * share,
        Care::Table(t) => if y <= 1992 { ccc_base * t.eval(f.agi) } else { ccc_base * t.eval(posagi) },
    };

    // ---- EITC ----
    let mut earncr = 0.0;
    if let Some((rate, agi_cap)) = p.eitc {
        earncr = if agi < agi_cap { rate * f.eitc } else { 0.0 };
        if let Some(w) = &p.worksheet {
            let nkid = depx;
            let xlin2 = if nkid == 0.0 { w.start[0] } else if (1.0..=2.0).contains(&nkid) { w.start[1] } else { w.start[2] };
            let xlin3 = (f.earned_income - xlin2).max(0.0);
            let xlin5 = if nkid == 0.0 {
                (w.maximum[0] - xlin3 * w.rate[0]).max(0.0)
            } else if nkid == 1.0 {
                (w.maximum[1] - xlin3 * w.rate[1]).max(0.0)
            } else {
                (w.maximum[2] - xlin3 * w.rate[2]).max(0.0)
            };
            if is_joint && earncr > 0.0 {
                earncr = w.share * xlin5;
            }
        }
        if let Some(c) = &p.caps {
            let over = |cap: f64| f.earned_income > cap || agi > cap;
            let over_flat = (depx == 0.0 && over(c.none)) || (depx == 1.0 && over(c.one)) || (depx >= 2.0 && over(c.two_or_more));
            if let Some(j) = &p.joint_caps {
                let over_joint = (depx == 0.0 && over(j.none)) || (depx == 1.0 && over(j.one)) || (depx >= 2.0 && over(j.two_or_more));
                let in_joint_branch = is_joint && earncr > 0.0;
                if in_joint_branch && over_joint {
                    earncr = 0.0;
                }
                if !in_joint_branch && over_flat {
                    earncr = 0.0;
                }
            } else if over_flat {
                earncr = 0.0;
            }
        }
    }
    if y <= 1989 {
        statax = (statax - chcr - earncr).max(0.0);
    }
    if (1990..=2006).contains(&y) {
        statax = (statax - earncr).max(0.0) - chcr;
    }
    if is_single {
        statax = (agi - p.no_tax_single).min(statax).max(0.0);
    }
    if y >= 2007 {
        statax = statax - earncr - chcr;
    }
    if y >= 1992 {
        let mut low_income = if is_single { f.agi <= p.no_tax_single } else { f.agi <= p.no_tax_other };
        if y >= 2022 {
            low_income = low_income || (aged > 0.0 && if is_single { f.agi <= p.aged_single } else { f.agi <= p.aged_other });
        }
        if statax > 0.0 && low_income {
            statax = 0.0;
        }
    }
    statax
}
