//! Kansas income tax (port of `calculators.states.ks`).

use std::path::Path;

use crate::common::Brackets;
use crate::federal::{Fed, Inp};
use crate::params::{self, get, load, num, status_direct, Fs, YearParams};
use crate::states::{
    dividend_adjustment, higher_earner_share, household_income, law_year_is_exact, Interp, Socsec,
};

enum Schedule {
    One(Brackets),
    ByStatus { joint: Brackets, other: Brackets },
}

struct Percent {
    rate: f64,
    single: (f64, f64),
    married: (f64, f64),
}

enum Standard {
    Percent(Percent),
    ByStatus { amounts: [f64; 4], dependent_minimum: Option<f64>, aged_single: f64, aged_married: f64 },
}

struct Limitation {
    threshold: f64,
    rate: f64,
    cap_rate: f64,
}

struct ByStatusExemption {
    by_status: [f64; 4],
    dependent: f64,
    hoh_addition: f64,
}

enum Zero {
    None,
    Agi { joint: f64, other: f64 },
    Taxable { single: f64, joint: f64 },
}

struct Recomputed {
    max_persons: f64,
    max_expense: f64,
    top: f64,
    floor: f64,
    start: f64,
    step: f64,
}

enum Share {
    Table(Interp),
    Flat(f64),
}

struct Refund {
    ceiling: f64,
    cap: f64,
    full_income: f64,
    claw: Brackets,
}

struct Senior {
    share: f64,
    limit: f64,
}

enum Homestead {
    Old(Refund),
    New { cap: f64, limit: f64, share: Interp, senior: Option<Senior> },
}

enum Food {
    Aged { amount: f64, limit: f64 },
    Tiers(Vec<(f64, f64, f64)>),
    Credit { amount: f64, ceiling: f64 },
    None,
}

pub struct KsParams {
    year: i64,
    socsec: Socsec,
    ss_agi_limit: Option<f64>,
    fed_caps: Option<(f64, f64)>,
    standard: Standard,
    payroll_caps: (f64, f64),
    limitation: Option<(Limitation, f64)>,
    scaling: f64,
    exemption: f64,
    exemptions_by_status: Option<ByStatusExemption>,
    schedule: Schedule,
    zero: Zero,
    recomputed: Option<Recomputed>,
    chcr_share: Share,
    rent_share: f64,
    homestead: Homestead,
    food: Food,
    eitc_rate: Option<f64>,
    dividend_adjustment: f64,
}

impl KsParams {
    pub fn load(root: &Path, year: i64) -> Result<Self, String> {
        let doc = load(root, "states/ks/income_tax.yaml")?;
        law_year_is_exact(root, &doc, year)?;
        let credits = load(root, "national/credits.yaml")?;
        let p = YearParams::new(&doc, year);
        let opt = |key: &str| -> Result<Option<f64>, String> { Ok(if p.in_effect(key)? { Some(p.f(key)?) } else { None }) };
        let pair = |v: &serde_yaml::Value| -> Result<(f64, f64), String> {
            Ok((num(get(v, "floor")?)?, num(get(v, "cap")?)?))
        };

        let standard = if p.in_effect("standard_deduction_percent")? {
            let sd = p.value("standard_deduction_percent", &[])?;
            Standard::Percent(Percent { rate: num(get(sd, "rate")?)?, single: pair(get(sd, "single")?)?, married: pair(get(sd, "married")?)? })
        } else {
            let aged = p.value("aged_standard_deduction", &[])?.as_sequence().ok_or("expected a pair")?;
            Standard::ByStatus {
                amounts: status_direct(p.value("standard_deduction", &[])?)?,
                dependent_minimum: opt("dependent_standard_deduction_minimum")?,
                aged_single: num(&aged[0])?,
                aged_married: num(&aged[1])?,
            }
        };
        let payroll_caps = if p.in_effect("payroll_tax_deduction_cap")? {
            let c = p.value("payroll_tax_deduction_cap", &[])?;
            (num(get(c, "payroll")?)?, num(get(c, "self_employment")?)?)
        } else {
            (1.0e20, 1.0e20)
        };
        let limitation = if year >= 1991 && p.in_effect("itemized_limitation")? {
            let l = p.value("itemized_limitation", &[])?;
            Some((
                Limitation { threshold: num(get(l, "threshold")?)?, rate: num(get(l, "rate")?)?, cap_rate: num(get(l, "cap_rate")?)? },
                p.f("itemized_limitation_index")?,
            ))
        } else {
            None
        };
        let tables = p.value("brackets", &[])?;
        let schedule = if tables.is_mapping() {
            Schedule::ByStatus {
                joint: Brackets::new(&params::brackets(get(tables, "joint")?)?),
                other: Brackets::new(&params::brackets(get(tables, "other")?)?),
            }
        } else {
            Schedule::One(Brackets::new(&params::brackets(tables)?))
        };
        let zero = if p.in_effect("zero_tax_agi")? {
            let z = p.value("zero_tax_agi", &[])?;
            Zero::Agi { joint: num(get(z, "joint")?)?, other: num(get(z, "other")?)? }
        } else if p.in_effect("zero_tax_taxable_income")? {
            let z = p.value("zero_tax_taxable_income", &[])?;
            Zero::Taxable { single: num(get(z, "single")?)?, joint: num(get(z, "joint")?)? }
        } else {
            Zero::None
        };
        let recomputed = if (1988..=1997).contains(&year) {
            let c = YearParams::new(get(&credits, "child_care_credit")?, year);
            let rate = c.value("rate", &[])?;
            if rate.get("second_phase_start").is_some() {
                return Err("two-phase child care rate is not supported for Kansas".into());
            }
            Some(Recomputed {
                max_persons: c.f("max_qualifying_persons")?,
                max_expense: c.f("max_expense_per_person")?,
                top: num(get(rate, "top_rate")?)?,
                floor: num(get(rate, "floor_rate")?)?,
                start: num(get(rate, "phase_start")?)?,
                step: num(get(rate, "step_amount")?)?,
            })
        } else {
            None
        };
        let share = p.value("child_care_credit_federal_share", &[])?;
        let chcr_share = if share.is_sequence() {
            Share::Table(Interp::new(&params::brackets(share)?))
        } else {
            Share::Flat(num(share)?)
        };
        let homestead = if p.in_effect("homestead_refund")? {
            let hr = p.value("homestead_refund", &[])?;
            Homestead::Old(Refund {
                ceiling: if year == 1996 { 1.0e20 } else { num(get(hr, "income_ceiling")?)? },
                cap: num(get(hr, "cap")?)?,
                full_income: p.f("homestead_full_refund_income")?,
                claw: Brackets::new(&params::brackets(p.value("homestead_reduction_schedule", &[])?)?),
            })
        } else {
            Homestead::New {
                cap: p.f("homestead_property_tax_cap")?,
                limit: p.f("homestead_refund_income_limit")?,
                share: Interp::new(&params::brackets(p.value("homestead_refund_share", &[])?)?),
                senior: match opt("senior_property_relief_share")? {
                    Some(share) => Some(Senior { share, limit: p.f("senior_property_relief_income_limit")? }),
                    None => None,
                },
            }
        };
        let food = if p.in_effect("food_refund_aged")? {
            let fa = p.value("food_refund_aged", &[])?;
            Food::Aged { amount: num(get(fa, "amount")?)?, limit: num(get(fa, "income_limit")?)? }
        } else if p.in_effect("food_refund_tiers")? {
            Food::Tiers(params::matrix(p.value("food_refund_tiers", &[])?)?.into_iter().map(|r| (r[0], r[1], r[2])).collect())
        } else if year >= 1998 {
            Food::Credit { amount: p.f("food_sales_tax_credit_amount")?, ceiling: p.f("food_sales_tax_refund_agi_ceiling")? }
        } else {
            Food::None
        };
        Ok(Self {
            year,
            socsec: Socsec::load(root, year)?,
            ss_agi_limit: if year >= 2007 { Some(p.f("social_security_agi_limit")?) } else { None },
            fed_caps: match p.opt("federal_tax_deduction_caps")? {
                Some(c) => Some((num(get(c, "cap")?)?, num(get(c, "upper_cap")?)?)),
                None => None,
            },
            standard,
            payroll_caps,
            limitation,
            scaling: if year >= 2013 { p.f("itemized_scaling")? } else { 0.0 },
            exemption: p.f("personal_exemption")?,
            exemptions_by_status: match p.opt("exemptions_by_status")? {
                Some(e) => Some(ByStatusExemption {
                    by_status: status_direct(get(e, "by_status")?)?,
                    dependent: num(get(e, "dependent")?)?,
                    hoh_addition: num(get(e, "head_of_household_addition")?)?,
                }),
                None => None,
            },
            schedule,
            zero,
            recomputed,
            chcr_share,
            rent_share: p.f("homestead_rent_share")?,
            homestead,
            food,
            eitc_rate: if year >= 1998 { Some(p.f("eitc_rate")?) } else { None },
            dividend_adjustment: dividend_adjustment(root)?,
        })
    }
}

pub fn tax(i: &Inp, f: &Fed, p: &KsParams) -> f64 {
    let y = p.year;
    let fs = i.fs();
    let is_joint = fs == Fs::Joint;
    let is_hoh = fs == Fs::Hoh;
    let sep = i.sepret();
    let txp = i.taxpayer_count();
    let aged = i.aged_count();
    let depx = i.depx;
    let salt_ded = f.ded;
    let setax = f.payroll.setax;
    let hy = household_income(i, p.dividend_adjustment);

    // ---- AGI ----
    let mut ks_agi = f.agi;
    if let Some(limit) = p.ss_agi_limit {
        if f.agi <= limit {
            ks_agi -= f.taxable_social_security;
        }
    }
    if (2013..=2016).contains(&y) {
        ks_agi += 0.5 * setax - (i.psemp + i.ssemp) - (i.otherprop + i.scorp);
    }
    let fedtax = f.fiitax.max(0.0);
    let fedded = if y <= 1982 || (1987..=1988).contains(&y) {
        fedtax
    } else if let Some((cap, upper)) = p.fed_caps {
        let (cap1, cap2) = (cap * txp, upper * txp);
        if fedtax <= cap1 { fedtax } else if fedtax <= cap2 { cap1 } else { 0.5 * fedtax }
    } else if (1985..=1986).contains(&y) {
        fedtax * ks_agi.max(0.0) / f.agi.max(1.0)
    } else {
        0.0
    };

    // ---- standard deduction ----
    let stded = match &p.standard {
        Standard::Percent(s) => {
            let share = s.rate * ks_agi;
            if matches!(fs, Fs::Single | Fs::Hoh) {
                share.max(s.single.0).min(s.single.1)
            } else {
                share.max(s.married.0 / sep).min(s.married.1 / sep)
            }
        }
        Standard::ByStatus { amounts, dependent_minimum, aged_single, aged_married } => {
            let mut stded = amounts[fs as usize];
            if let Some(minimum) = dependent_minimum {
                if i.is_dependent_filer() {
                    stded = stded.min(minimum.max(f.earned_income));
                }
            }
            let married_type = matches!(fs, Fs::Joint | Fs::Separate);
            stded + if married_type { *aged_married } else { *aged_single } * aged
        }
    };

    // ---- itemized deduction ----
    let (local, itemizing) = if y <= 1986 { (f.pre87.deduc, f.pre87.itemizes) } else { (f.itemized_deduction, f.itemizes) };
    let salt_plus_mortgage = f.salt_capped + i.mortgage;
    let mut xitded = if y <= 1987 {
        let socsec = p.socsec.total(y, i, setax, f.payroll.addmed);
        let soc = socsec.max(0.0).min(p.payroll_caps.0 * txp);
        let addtx = setax.max(0.0).min(p.payroll_caps.1 * txp);
        let base = (local - salt_ded + soc + addtx).max(0.0);
        if itemizing { base } else { 0.0 }
    } else if y <= 1990 {
        if itemizing { (local - salt_ded).max(0.0) } else { 0.0 }
    } else if let Some((l, index)) = &p.limitation {
        let threshold = l.threshold * index;
        let under_thr = f.agi <= threshold / sep;
        let base_low = (local - salt_ded).max(0.0);
        let fline3 = local.max(0.0);
        let fline9 = (l.rate * (f.agi - threshold).max(0.0)).min(l.cap_rate * fline3);
        let sline1 = if fline3 > 0.0 { fline9 / fline3 } else { 0.0 };
        let base_high = if fline3 > 0.0 { (local - salt_ded * (1.0 - sline1)).max(0.0) } else { base_low };
        if itemizing { if under_thr { base_low } else { base_high } } else { 0.0 }
    } else if y <= 2012 {
        if itemizing { (local - salt_ded).max(0.0) } else { 0.0 }
    } else if y <= 2017 {
        if itemizing { p.scaling * (salt_plus_mortgage - salt_ded).max(0.0) } else { 0.0 }
    } else if itemizing {
        p.scaling * (i.proptax + i.otheritem + i.mortgage)
    } else {
        0.0
    };
    if y == 2021 {
        xitded = i.proptax + i.otheritem + i.mortgage;
    }
    let deduc = stded.max(xitded);

    // ---- exemptions ---- The federal exemption count, with one more for a head of household.
    let comnew68 = if y <= 1986 {
        txp + depx + aged
    } else if i.is_dependent_filer() {
        0.0
    } else {
        txp + depx
    };
    let exemps = if is_hoh { comnew68 + 1.0 } else { comnew68 };
    let mut exemption = exemps * p.exemption;
    if let Some(e) = &p.exemptions_by_status {
        exemption = if i.is_dependent_filer() {
            0.0
        } else {
            e.by_status[fs as usize] + e.dependent * depx + if is_hoh { e.hoh_addition } else { 0.0 }
        };
    }
    let taxinc = (ks_agi - deduc - exemption - fedded).max(0.0);

    // ---- tax ----
    let mut statax = match &p.schedule {
        Schedule::ByStatus { joint, other } => if is_joint { joint.tax(taxinc) } else { other.tax(taxinc) },
        Schedule::One(table) => {
            // Joint returns are taxed on half their income, doubled, or on each earner's share if lower.
            let halved = if is_joint { taxinc / 2.0 } else { taxinc };
            let doubler = if is_joint { 2.0 } else { 1.0 };
            let mut statax = table.tax(halved) * doubler;
            let yh = higher_earner_share(i, taxinc);
            let yw = taxinc - yh;
            let split = table.tax(yh.max(0.0)) + table.tax(yw.max(0.0));
            if is_joint {
                statax = statax.min(split);
            }
            statax
        }
    };
    match p.zero {
        Zero::Agi { joint, other } => {
            let zero_agi = if is_joint { joint } else { other };
            if taxinc <= 0.0 || ks_agi <= zero_agi {
                statax = 0.0;
            }
        }
        Zero::Taxable { single, joint } => {
            if if is_joint { taxinc <= joint } else { taxinc <= single } {
                statax = 0.0;
            }
        }
        Zero::None => {}
    }

    // ---- credits ---- A share of the federal child care credit, which TAXSIM leaves at 0 for 1988-1997.
    let mut chcare = match &p.recomputed {
        Some(c) => {
            let rate = (c.top - ((f.agi - c.start) / c.step).max(0.0) * 0.01).max(c.floor);
            let qualifying = i.dep13.max(0.0).min(c.max_persons);
            let qualifying_expense = i.childcare.max(0.0).min(qualifying * c.max_expense);
            let earned_cap = if is_joint { i.pwages.min(i.swages) } else { i.pwages + i.swages };
            rate * qualifying_expense.min(earned_cap).max(0.0)
        }
        None => f.federal_chcr,
    };
    chcare = chcare.min(f.regular_tax.max(0.0));
    let chcr = match &p.chcr_share {
        Share::Table(t) => chcare * t.eval(ks_agi),
        Share::Flat(share) => chcare * share,
    };

    // Homestead property tax refund.
    let pr1 = i.proptax + p.rent_share * i.rentpaid;
    let pcred = match &p.homestead {
        Homestead::Old(r) => {
            let full_refund = hy <= r.full_income;
            let under = hy <= r.ceiling;
            let claw = r.claw.tax(hy);
            let reduced = if y <= 1978 { (pr1 - claw).max(0.0).min(r.cap) } else { (pr1.max(0.0).min(r.cap) - claw).max(0.0) };
            if under { if full_refund { pr1 } else { reduced } } else { 0.0 }
        }
        Homestead::New { cap, limit, share, senior } => {
            let ptax = cap.min(pr1);
            let hhy = if y <= 2005 { hy } else { ks_agi + f.eitc + 0.5 * i.gssi };
            let mut pcred = if hhy < *limit { ptax * share.eval(hhy) } else { 0.0 };
            if let Some(s) = senior {
                if aged > 0.0 && i.proptax > 0.0 && hy < s.limit {
                    pcred = s.share * i.proptax;
                }
            }
            pcred
        }
    };

    // Food sales tax refund.
    let fd = match &p.food {
        Food::Aged { amount, limit } => if hy <= *limit { amount * aged.min(txp + depx) } else { 0.0 },
        Food::Tiers(tiers) => {
            let extra = txp + depx - 1.0;
            let last = tiers.len() - 1;
            let mut fd = 0.0;
            for (index, (limit, base, per_extra)) in tiers.iter().enumerate().rev() {
                let within = if index == last { hy <= *limit } else { hy < *limit };
                fd = if within { base + per_extra * extra } else { fd };
            }
            fd
        }
        Food::Credit { amount, ceiling } => {
            let eligible = depx + aged > 0.0;
            if y <= 2012 {
                if eligible && ks_agi <= *ceiling {
                    2.0 * amount * exemps
                } else if eligible && ks_agi <= 2.0 * ceiling {
                    amount * exemps
                } else {
                    0.0
                }
            } else if eligible && f.agi <= *ceiling {
                amount * comnew68
            } else {
                0.0
            }
        }
        Food::None => 0.0,
    };
    let earncr = p.eitc_rate.map_or(0.0, |rate| rate * f.eitc);
    if y <= 2012 {
        (statax - chcr).max(0.0) - fd - earncr - pcred
    } else {
        (statax - chcr - fd).max(0.0) - earncr - pcred
    }
}
