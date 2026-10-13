//! Virginia income tax (port of `calculators.states.va`).

use std::path::Path;

use crate::common::Brackets;
use crate::federal::{Fed, Inp};
use crate::params::{self, get, load, num, status_direct, Fs, YearParams};
use crate::states::{dividend_adjustment, law_year_is_exact, pre1987_gross_itemized};

enum FilingMinimum {
    None,
    Flat(f64),
    ByMarriage { single: f64, married: f64 },
    PerTaxpayer(f64),
}

struct Retirement {
    full_limit: f64,
    partial_start: f64,
    partial_end: f64,
    base: f64,
    divisor: f64,
    cap: f64,
}

struct AgeDeduction {
    amount: f64,
    income_limit: Option<(f64, f64)>,
}

enum Standard {
    Share { rate: f64, low: f64, high: f64 },
    PerTaxpayer(f64),
    JointOther { joint: f64, other: f64 },
    SingleMarried { single: f64, married: f64 },
}

enum Threshold {
    Flat(f64),
    ByStatus([f64; 4]),
}

struct ItemizedLimit {
    threshold: Threshold,
    index: f64,
    status_factor: Option<[f64; 4]>,
    share: f64,
    rate: f64,
}

struct ItemizedLate {
    threshold: [f64; 4],
    test_index: f64,
    share: f64,
    rate: f64,
}

enum ZeroTax {
    PerTaxpayer(f64),
    ByMarriage { single: f64, married_per_taxpayer: f64 },
}

struct Spouse {
    cap: f64,
    min_taxable: f64,
    age_deduction: f64,
    aged_exemption: f64,
    credit_per_person: f64,
    limit: f64,
    limit_per_person: f64,
    eitc_rate: Option<f64>,
    eitc_refundable_rate: Option<f64>,
    rebate: Option<(f64, f64)>,
}

struct OldAge {
    base: f64,
    rate: f64,
    income_start: f64,
    retirement_limit: Option<f64>,
}

pub struct VaParams {
    year: i64,
    table: Brackets,
    aged_deduction: Option<f64>,
    filing_minimum: FilingMinimum,
    retirement: Option<Retirement>,
    age_deduction: Option<AgeDeduction>,
    standard: Standard,
    itemized_limit: Option<ItemizedLimit>,
    salt_cap: Option<f64>,
    itemized_late: Option<ItemizedLate>,
    child_care_per_child: Option<f64>,
    exemption: f64,
    aged_exemption: Option<f64>,
    zero_tax: ZeroTax,
    spouse: Option<Spouse>,
    old_age: Option<OldAge>,
    dividend_adjustment: f64,
}

impl VaParams {
    pub fn load(root: &Path, year: i64) -> Result<Self, String> {
        let doc = load(root, "states/va/income_tax.yaml")?;
        law_year_is_exact(root, &doc, year)?;
        let p = YearParams::new(&doc, year);
        let opt = |key: &str| -> Result<Option<f64>, String> { Ok(if p.in_effect(key)? { Some(p.f(key)?) } else { None }) };

        let filing_minimum = if p.in_effect("filing_minimum")? {
            let m = p.value("filing_minimum", &[])?;
            if !m.is_mapping() {
                FilingMinimum::Flat(num(m)?)
            } else if m.get("per_taxpayer").is_some() {
                FilingMinimum::PerTaxpayer(num(get(m, "per_taxpayer")?)?)
            } else {
                FilingMinimum::ByMarriage { single: num(get(m, "single")?)?, married: num(get(m, "married")?)? }
            }
        } else {
            FilingMinimum::None
        };
        let retirement = match p.opt("retirement_subtraction")? {
            Some(r) => Some(Retirement {
                full_limit: num(get(r, "full_limit")?)?,
                partial_start: num(get(r, "partial_start")?)?,
                partial_end: num(get(r, "partial_end")?)?,
                base: num(get(r, "base")?)?,
                divisor: num(get(r, "divisor")?)?,
                cap: num(get(r, "cap")?)?,
            }),
            None => None,
        };
        let age_deduction = if retirement.is_none() && p.in_effect("age_deduction")? {
            let limit = match p.opt("age_deduction_income_limit")? {
                Some(l) => Some((num(get(l, "married_joint")?)?, num(get(l, "single")?)?)),
                None => None,
            };
            Some(AgeDeduction { amount: p.f("age_deduction")?, income_limit: limit })
        } else {
            None
        };
        let standard = if p.in_effect("standard_deduction_share")? {
            let c = p.value("standard_deduction_share", &[])?;
            Standard::Share { rate: num(get(c, "rate")?)?, low: num(get(c, "low")?)?, high: num(get(c, "high")?)? }
        } else {
            let c = p.value("standard_deduction", &[])?;
            if c.get("per_taxpayer").is_some() {
                Standard::PerTaxpayer(num(get(c, "per_taxpayer")?)?)
            } else if c.get("joint").is_some() {
                Standard::JointOther { joint: num(get(c, "joint")?)?, other: num(get(c, "other")?)? }
            } else {
                Standard::SingleMarried { single: num(get(c, "single")?)?, married: num(get(c, "married")?)? }
            }
        };
        let itemized_limit = if year <= 2017 && p.in_effect("itemized_limit_threshold")? {
            let t = p.value("itemized_limit_threshold", &[])?;
            Some(ItemizedLimit {
                threshold: if t.is_mapping() { Threshold::ByStatus(status_direct(t)?) } else { Threshold::Flat(num(t)?) },
                index: p.f("itemized_limit_index")?,
                status_factor: if p.in_effect("itemized_limit_status_factor")? {
                    Some(status_direct(p.value("itemized_limit_status_factor", &[])?)?)
                } else {
                    None
                },
                share: num(get(&doc, "itemized_limit_share")?)?,
                rate: num(get(&doc, "itemized_limit_rate")?)?,
            })
        } else {
            None
        };
        let itemized_late = if year > 2018 {
            // The statutory mode scales the threshold by the test index.
            let index = p.f("itemized_limit_test_index")?;
            let base = status_direct(p.value("itemized_limit_threshold", &[])?)?;
            Some(ItemizedLate {
                threshold: [base[0] * index, base[1] * index, base[2] * index, base[3] * index],
                test_index: 1.0,
                share: num(get(&doc, "itemized_limit_share")?)?,
                rate: num(get(&doc, "itemized_limit_rate")?)?,
            })
        } else {
            None
        };
        let zero = p.value("zero_tax_agi", &[])?;
        let zero_tax = if zero.get("per_taxpayer").is_some() {
            ZeroTax::PerTaxpayer(num(get(zero, "per_taxpayer")?)?)
        } else {
            ZeroTax::ByMarriage { single: num(get(zero, "single")?)?, married_per_taxpayer: num(get(zero, "married_per_taxpayer")?)? }
        };
        let spouse = if p.in_effect("spouse_adjustment_cap")? {
            Some(Spouse {
                cap: p.f("spouse_adjustment_cap")?,
                min_taxable: num(get(&doc, "spouse_adjustment_min_taxable")?)?,
                age_deduction: num(get(&doc, "spouse_adjustment_age_deduction")?)?,
                aged_exemption: num(get(&doc, "spouse_adjustment_aged_exemption")?)?,
                credit_per_person: p.f("low_income_credit_per_person")?,
                limit: p.f("low_income_limit")?,
                limit_per_person: p.f("low_income_limit_per_person")?,
                eitc_rate: opt("eitc_rate")?,
                eitc_refundable_rate: opt("eitc_refundable_rate")?,
                rebate: match p.opt("rebate")? {
                    Some(r) => Some((num(get(r, "married_joint")?)?, num(get(r, "other")?)?)),
                    None => None,
                },
            })
        } else {
            None
        };
        let old_age = if p.in_effect("old_age_credit_base")? {
            Some(OldAge {
                base: p.f("old_age_credit_base")?,
                rate: num(get(&doc, "old_age_credit_rate")?)?,
                income_start: num(get(&doc, "old_age_credit_income_start")?)?,
                retirement_limit: opt("old_age_credit_retirement_limit")?,
            })
        } else {
            None
        };
        Ok(Self {
            year,
            table: Brackets::new(&params::brackets(p.value("brackets", &[])?)?),
            aged_deduction: opt("aged_deduction")?,
            filing_minimum,
            retirement,
            age_deduction,
            standard,
            itemized_limit,
            salt_cap: if year >= 2018 { opt("salt_cap")? } else { None },
            itemized_late,
            child_care_per_child: opt("child_care_deduction_per_child")?,
            exemption: p.f("exemption")?,
            aged_exemption: opt("aged_exemption")?,
            zero_tax,
            spouse,
            old_age,
            dividend_adjustment: dividend_adjustment(root)?,
        })
    }
}

pub fn tax(i: &Inp, f: &Fed, p: &VaParams) -> f64 {
    let y = p.year;
    let fs = i.fs();
    let is_joint = fs == Fs::Joint;
    let is_sep = fs == Fs::Separate;
    let single_type = fs == Fs::Single || fs == Fs::Hoh;
    let sep = i.sepret();
    let txp = i.taxpayer_count();
    let aged = i.aged_count();
    let depx = i.depx;
    let fed_agi = f.agi;
    let salt = f.ded;
    let va_schede = i.otherprop + if y >= 1987 { i.scorp } else { 0.0 };

    // ---- AGI (unemployment compensation is not taxed) ----
    let mut agi = fed_agi - f.taxable_unemployment;
    if let Some(deduction) = p.aged_deduction {
        agi -= deduction * aged;
    }
    if (1982..=1986).contains(&y) {
        agi += f.pre87.twoded;
    }
    if y >= 1984 {
        agi -= f.taxable_social_security;
    }
    let detail_agi = agi;

    // No return below the filing minimum.
    let must_file = match &p.filing_minimum {
        FilingMinimum::None => true,
        FilingMinimum::Flat(m) => agi >= *m,
        FilingMinimum::PerTaxpayer(m) => agi >= m * txp / sep,
        FilingMinimum::ByMarriage { single, married } => agi >= (if is_joint || is_sep { *married } else { *single }) / sep,
    };

    // ---- retirement subtraction (1989) and age deduction (1990+) ----
    let pensions = i.pensions;
    let mut elder = 0.0;
    if let Some(r) = &p.retirement {
        let subtraction = if pensions <= r.full_limit {
            pensions.max(0.0).min(r.full_limit)
        } else if pensions >= r.partial_start && pensions <= r.partial_end {
            ((r.base - pensions) / r.divisor).max(0.0).min(r.cap)
        } else {
            0.0
        };
        if aged >= 1.0 {
            agi -= subtraction;
        }
    } else if let Some(a) = &p.age_deduction {
        elder = fed_agi.min(if y == 1992 { a.amount } else { a.amount * aged });
        if let Some((joint_limit, single_limit)) = a.income_limit {
            let ylimit = if is_joint { joint_limit } else { single_limit };
            let over = fed_agi - f.taxable_social_security - ylimit;
            if over > 0.0 {
                elder = if over > elder { 0.0 } else { elder - over };
            }
        }
        if !(aged >= 1.0) {
            elder = 0.0;
        }
        agi -= elder;
    }

    // ---- standard deduction ----
    let stded = match &p.standard {
        Standard::Share { rate, low, high } => (rate * agi).max(low / sep).min(high / sep),
        Standard::PerTaxpayer(amount) => amount * txp,
        Standard::JointOther { joint, other } => if is_joint { *joint } else { *other },
        Standard::SingleMarried { single, married } => if is_joint || is_sep { married / sep } else { *single },
    };

    // ---- itemized deductions ----
    let (fed_itemizes, itemized) = if y <= 1986 {
        (f.pre87.itemizes, pre1987_gross_itemized(i, f))
    } else {
        (f.itemizes, f.itemized_deduction)
    };
    let mut xitded;
    if y <= 2017 {
        xitded = if fed_itemizes { itemized - salt } else { 0.0 };
        if let Some(l) = &p.itemized_limit {
            let phas92 = match (&l.status_factor, &l.threshold) {
                (None, Threshold::Flat(t)) => t * l.index / sep,
                (None, Threshold::ByStatus(t)) => t[fs as usize] * l.index / sep,
                (Some(factor), Threshold::Flat(t)) => l.index * t * factor[fs as usize],
                (Some(factor), Threshold::ByStatus(t)) => l.index * t[fs as usize] * factor[fs as usize],
            };
            let ag = fed_agi.max(0.0);
            let xtot = salt + i.proptax + i.mortgage + i.charity_cash;
            let xconst = (l.share * xtot).min(l.rate * (ag - phas92));
            let limited = (itemized - (xtot - xconst) / xtot * salt).max(0.0);
            if fed_itemizes && ag >= phas92 && ag > phas92 && xtot > 0.0 {
                xitded = limited;
            }
        }
    } else if y == 2018 {
        let other = i.proptax + i.otheritem;
        let cap = p.salt_cap.unwrap_or(0.0);
        let stt = salt.min((cap / sep).min(other + salt) - other);
        xitded = if fed_itemizes { f.itemized_before_limit - stt } else { 0.0 };
    } else {
        let l = p.itemized_late.as_ref().expect("itemized limit from 2019");
        let deduc1 = i.charity_cash + salt + i.proptax + i.otheritem + i.mortgage;
        let threshold = l.threshold[fs as usize];
        let sttax = (p.salt_cap.unwrap_or(0.0) / sep).min(salt);
        let dedphs = (l.rate * (fed_agi - threshold)).min(l.share * (deduc1 - salt + sttax).max(0.0));
        let perc = if deduc1 > 0.0 { (dedphs / deduc1).max(0.0).min(1.0) } else { 0.0 };
        let limited = deduc1 - dedphs - if deduc1 > 0.0 { sttax * (1.0 - perc) } else { 0.0 };
        let over = fed_agi > threshold * l.test_index;
        xitded = if fed_itemizes { if over { limited } else { deduc1 - salt } } else { 0.0 };
    }

    let child = match p.child_care_per_child {
        Some(amount) => depx.min(2.0) * amount,
        None => 0.0,
    };
    let child = i.childcare.min(child);
    let deduc = stded.max(xitded) + child;
    let exemp = match p.aged_exemption {
        None => (txp + depx + aged) * p.exemption,
        Some(aged_exemption) => (txp + depx) * p.exemption + aged_exemption * aged,
    };
    let taxinc = (agi - deduc - exemp).max(0.0);

    // ---- tax ----
    let mut statax = p.table.tax(taxinc);
    if y <= 1999 {
        // Joint returns: the lesser of joint tax and combined separate taxes.
        let agiw = 0.5 * (agi - i.pwages - i.swages) + i.swages;
        let share_w = agiw / agi;
        let dedw = if xitded > stded { xitded * share_w } else { stded * share_w };
        let taxyw = (agiw - dedw - exemp * share_w).max(0.0);
        let taxyh = taxinc - taxyw;
        let separate = p.table.tax(taxyh.max(0.0)) + p.table.tax(taxyw);
        if is_joint && agi > 0.0 {
            statax = statax.min(separate);
        }
    }

    // No tax at or below the AGI thresholds.
    let zero = match &p.zero_tax {
        ZeroTax::PerTaxpayer(amount) => agi <= amount * txp,
        ZeroTax::ByMarriage { single, married_per_taxpayer } => {
            if single_type { agi <= *single } else { agi <= married_per_taxpayer * txp }
        }
    };
    if zero {
        statax = 0.0;
    }

    // ---- spouse tax adjustment (2000 on) ----
    let mut refundable_extra = 0.0;
    if let Some(s) = &p.spouse {
        let capgn = i.stcg + i.ltcg;
        let ss = f.taxable_social_security;
        let othinc = i.dividends
            + p.dividend_adjustment
            + i.intrec
            + (i.psemp + i.ssemp).max(0.0)
            + capgn
            - 0.5 * f.payroll.setax
            + i.pensions
            + ss
            + va_schede.max(0.0);
        let husb = i.pbusinc + i.pprofinc + i.pwages + 0.5 * othinc;
        let mut wife = i.sbusinc + i.sprofinc + i.swages + 0.5 * othinc;
        wife = wife.min(husb);
        let aged_w = (aged > 0.0) as u8 as f64;
        let taxy23 = (wife - aged_w * s.age_deduction - 0.5 * ss - p.exemption - s.aged_exemption * aged_w).max(0.0);
        let taxy24 = (taxinc - taxy23).max(0.0);
        let taxy25 = taxinc / 2.0;
        let stat26 = p.table.tax(taxy23.min(taxy25));
        let stat27 = p.table.tax(taxy24.max(taxy25));
        let twocrd = (statax - stat26 - stat27).max(0.0).min(s.cap);
        let adjusted = is_joint && taxinc > s.min_taxable;
        if adjusted {
            statax = (statax - twocrd).max(0.0);
        }

        // ---- credit for low-income individuals ----
        let people = depx + txp;
        let limit = s.limit + (people - 1.0) * s.limit_per_person;
        let mut crlow = if agi <= limit { s.credit_per_person * people } else { 0.0 };
        if let Some(rate) = s.eitc_rate {
            let earncr = rate * f.eitc;
            crlow = earncr.max(crlow);
        }
        // Not with the age deduction.
        if elder > 0.0 {
            crlow = 0.0;
        }
        crlow = statax.min(crlow);
        if let Some(rate) = s.eitc_refundable_rate {
            // Electing the refundable credit gives up the nonrefundable one.
            let refundable_claim = rate * f.eitc;
            let elect = refundable_claim > crlow && elder <= 0.0;
            refundable_extra = if elect { refundable_claim } else { 0.0 };
            if elect {
                crlow = 0.0;
            }
        }
        statax -= crlow;
        if let Some((joint_amount, other_amount)) = s.rebate {
            let rebate = statax.max(0.0).min(if is_joint { joint_amount } else { other_amount });
            statax -= rebate;
        }
    }

    // Old age credit (through 1989).
    if let Some(o) = &p.old_age {
        let resid = (agi - o.income_start).max(0.0);
        let mut ocred = o.rate * (o.base * aged - i.gssi - 2.0 * resid).max(0.0);
        if let Some(limit) = o.retirement_limit {
            if pensions >= limit {
                ocred = 0.0;
            }
        }
        statax = (statax - ocred).max(0.0);
    }

    statax = if must_file { statax.max(0.0) } else { 0.0 };
    statax -= refundable_extra;
    let _ = detail_agi;
    statax * 1.0
}
