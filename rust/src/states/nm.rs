//! New Mexico income tax (port of `calculators.states.nm`).

use std::path::Path;

use crate::common::Brackets;
use crate::federal::{Fed, Inp};
use crate::params::{self, get, load, num, status_at, status_direct, Fs, YearParams};
use crate::states::{dividend_adjustment, federal_exemption_count, household_income, law_year_is_exact, unemployment_total, Interp};

struct Percent {
    single: Brackets,
    separate: Brackets,
    married: Brackets,
}

enum Schedule {
    /// Through 1986: tables in percent, scaled by a rate factor.
    Percent(Percent),
    Tables { single: Brackets, married: Brackets, married_separate: Option<Brackets>, hoh: Option<Brackets> },
}

struct AgedDeduction {
    flat: Option<f64>,
    single: Option<Interp>,
    separate: Option<Interp>,
    married: Option<Interp>,
}

struct LowIncomeExemption {
    amount: f64,
    limit_single: f64,
    limit_other: f64,
    start: [f64; 4],
    rate: [f64; 4],
}

enum Rebate {
    /// 2022 on: right-closed steps by household size.
    Steps(Vec<Brackets>),
    /// Before: interpolated tables by household size.
    Interpolated(Vec<Interp>),
}

struct Food {
    single: Interp,
    married: Interp,
}

struct ChildCare {
    income_limit: f64,
    expense_share: f64,
    cap_per_child: f64,
    max_children: f64,
    credit_cap: f64,
}

pub struct NmParams {
    year: i64,
    schedule: Schedule,
    cg_share: f64,
    cg_floor: f64,
    aged: Option<AgedDeduction>,
    standard: Option<[f64; 4]>,
    exemption_per_person: Option<f64>,
    low_income_exemption: Option<LowIncomeExemption>,
    dependent_deduction: f64,
    rebate: Rebate,
    food_credit: f64,
    medical_credit: f64,
    food_by_income: Option<Food>,
    child_care: Option<ChildCare>,
    eitc_match_rate: f64,
    property_rent_share: f64,
    property_threshold: Interp,
    property_income_limit: f64,
    property_max: f64,
    child_tax_credit: Option<Brackets>,
    dividend_adjustment: f64,
}

impl NmParams {
    pub fn load(root: &Path, year: i64) -> Result<Self, String> {
        let doc = load(root, "states/nm/income_tax.yaml")?;
        law_year_is_exact(root, &doc, year)?;
        let p = YearParams::new(&doc, year);
        let opt = |key: &str| -> Result<Option<f64>, String> { Ok(if p.in_effect(key)? { Some(p.f(key)?) } else { None }) };
        let rows = |v: &serde_yaml::Value| params::brackets(v);

        let schedule = if p.in_effect("brackets_percent")? {
            let factor = p.f("rate_factor")?;
            let bracket_14 = opt("bracket_14_rate_percent")?;
            let percent = |kind: &str| -> Result<Brackets, String> {
                let mut out = Vec::new();
                for (index, row) in rows(p.value("brackets_percent", &[kind])?)?.into_iter().enumerate() {
                    let mut rate_pct = (10.0 * row[1] * factor + 0.5).floor() / 10.0;
                    if index + 1 == 14 {
                        if let Some(r) = bracket_14 {
                            rate_pct = r;
                        }
                    }
                    out.push(vec![row[0], rate_pct / 100.0]);
                }
                Ok(Brackets::new(&out))
            };
            Schedule::Percent(Percent { single: percent("single")?, separate: percent("separate")?, married: percent("married")? })
        } else {
            let t = p.value("brackets", &[])?;
            let table = |key: &str| -> Result<Option<Brackets>, String> {
                t.get(key).map(|v| Ok(Brackets::new(&rows(v)?))).transpose()
            };
            Schedule::Tables {
                single: table("single")?.ok_or("single schedule")?,
                married: table("married")?.ok_or("married schedule")?,
                married_separate: table("married_separate")?,
                hoh: table("head_of_household")?,
            }
        };
        let aged = match p.opt("aged_deduction")? {
            None => None,
            Some(v) if year <= 1984 => Some(AgedDeduction { flat: Some(num(v)?), single: None, separate: None, married: None }),
            Some(v) => Some(AgedDeduction {
                flat: None,
                single: Some(Interp::new(&rows(get(v, "single")?)?)),
                separate: Some(Interp::new(&rows(get(v, "married_separate")?)?)),
                married: Some(Interp::new(&rows(get(v, "married")?)?)),
            }),
        };
        let low_income_exemption = if p.in_effect("low_income_exemption_amount")? {
            Some(LowIncomeExemption {
                amount: p.f("low_income_exemption_amount")?,
                limit_single: p.num_at("low_income_exemption_income_limit", &["single"])?,
                limit_other: p.num_at("low_income_exemption_income_limit", &["other"])?,
                start: status_at(get(&doc, "low_income_exemption_phaseout_start")?, year)?,
                rate: status_direct(get(&doc, "low_income_exemption_phaseout_rate")?)?,
            })
        } else {
            None
        };
        let rebate = if p.in_effect("low_income_rebate_steps")? {
            let steps = p.value("low_income_rebate_steps", &[])?.as_sequence().ok_or("steps must be a list")?;
            Rebate::Steps(steps.iter().map(|t| Ok(Brackets::new(&rows(t)?))).collect::<Result<_, String>>()?)
        } else {
            let table = params::matrix(p.value("low_income_rebate", &[])?)?;
            Rebate::Interpolated(
                (1..=6)
                    .map(|size| Interp::new(&table.iter().map(|r| vec![r[0], r[size]]).collect::<Vec<_>>()))
                    .collect(),
            )
        };
        let food_by_income = match p.opt("food_credit_by_income")? {
            Some(v) => Some(Food {
                single: Interp::new(&rows(get(v, "single")?)?),
                married: Interp::new(&rows(get(v, "married")?)?),
            }),
            None => None,
        };
        let child_care = match opt("child_care_income_limit")? {
            Some(income_limit) => Some(ChildCare {
                income_limit,
                expense_share: num(get(&doc, "child_care_expense_share")?)?,
                cap_per_child: num(get(&doc, "child_care_cap_per_child")?)?,
                max_children: num(get(&doc, "child_care_max_children")?)?,
                credit_cap: num(get(&doc, "child_care_credit_cap")?)?,
            }),
            None => None,
        };
        let child_tax_credit = match p.opt("child_tax_credit")? {
            Some(v) => Some(Brackets::new(&rows(v)?)),
            None => None,
        };
        Ok(Self {
            year,
            schedule,
            cg_share: p.f("capital_gains_deduction_share")?,
            cg_floor: p.f("capital_gains_deduction_floor")?,
            aged,
            standard: if p.in_effect("standard_deduction")? { Some(status_direct(p.value("standard_deduction", &[])?)?) } else { None },
            exemption_per_person: opt("exemption_per_person")?,
            low_income_exemption,
            dependent_deduction: p.f("dependent_deduction")?,
            rebate,
            food_credit: if year <= 1989 { p.f("food_credit")? } else { 0.0 },
            medical_credit: if year <= 1985 { p.f("medical_credit")? } else { 0.0 },
            food_by_income,
            child_care,
            eitc_match_rate: p.f("eitc_match_rate")?,
            property_rent_share: num(get(&doc, "property_rebate_rent_share")?)?,
            property_threshold: Interp::new(&rows(get(&doc, "property_rebate_threshold")?)?),
            property_income_limit: num(get(&doc, "property_rebate_income_limit")?)?,
            property_max: num(get(&doc, "property_rebate_max")?)?,
            child_tax_credit,
            dividend_adjustment: dividend_adjustment(root)?,
        })
    }
}

pub fn tax(i: &Inp, f: &Fed, p: &NmParams) -> f64 {
    let y = p.year;
    let fs = i.fs();
    let is_single = fs == Fs::Single;
    let is_joint = fs == Fs::Joint;
    let is_sep = fs == Fs::Separate;
    let is_hoh = fs == Fs::Hoh;
    let sep = i.sepret();
    let txp = i.taxpayer_count();
    let aged = i.aged_count();
    let depx = i.depx;
    let exemps = federal_exemption_count(y, i);
    let fed_agi = f.agi;
    let salt_ded = f.ded;

    let hh = household_income(i, p.dividend_adjustment);
    let business = i.psemp + i.ssemp + i.pwages.min(0.0) + i.swages.min(0.0);
    let modagi_base = i.pwages.max(0.0)
        + i.swages.max(0.0)
        + unemployment_total(i)
        + business.max(0.0)
        + i.ltcg.max(0.0)
        + i.stcg.max(0.0)
        + i.dividends
        + p.dividend_adjustment
        + i.intrec
        + i.gssi
        + i.pensions;
    // Business and S corporation income.
    let business_income =
        [i.pbusinc, i.pprofinc, i.scorp, i.sbusinc, i.sprofinc].iter().map(|v| v.max(0.0)).fold(0.0, |a, b| a + b);
    let modagi = modagi_base + business_income;

    // ---- federal values ----
    let (fed_itemizes, fed_deduc, fed_zbr, capgn, amex);
    if y <= 1986 {
        fed_deduc = i.proptax + i.otheritem + i.mortgage + salt_ded;
        fed_zbr = f.pre87.zbr;
        fed_itemizes = if y <= 1981 { fed_deduc > fed_zbr } else { f.forced_itemize };
        capgn = f.pre87.capgn;
        amex = f.pre87.amex;
    } else {
        fed_itemizes = f.itemizes;
        fed_deduc = f.itemized_deduction;
        fed_zbr = if fed_itemizes { 0.0 } else { f.standard_deduction };
        capgn = f.capgn;
        amex = f.personal_exemptions;
    }
    let fed_ccc = f.federal_chcr;

    // ---- New Mexico AGI ----
    let cgded = (if capgn > 0.0 { capgn.min(p.cg_floor.max(p.cg_share * capgn)) } else { 0.0 }) / sep;
    let mut agi = fed_agi - cgded;
    if (1984..=1989).contains(&y) {
        // Benefits are exempt; TAXSIM adds them a second time instead.
        agi -= f.taxable_social_security;
    }

    // ---- deduction for taxpayers 65 or older ----
    let elded = match &p.aged {
        None => 0.0,
        Some(a) => match a.flat {
            Some(amount) => amount * aged,
            None => {
                let amount = if is_single {
                    a.single.as_ref().map_or(0.0, |t| t.eval(agi))
                } else if is_sep {
                    a.separate.as_ref().map_or(0.0, |t| t.eval(agi))
                } else {
                    a.married.as_ref().map_or(0.0, |t| t.eval(agi))
                };
                amount * if y <= 1986 { aged } else { aged.min(txp) }
            }
        },
    };

    // ---- deductions ----
    let xitded = if fed_itemizes { (fed_deduc - (fed_deduc - fed_zbr).min(salt_ded)).max(0.0) } else { 0.0 };
    let stded = match &p.standard {
        Some(by_status) => by_status[fs as usize],
        None => fed_zbr,
    };
    let deduc = xitded.max(stded);

    // ---- exemptions ----
    let mut exemp = match p.exemption_per_person {
        Some(amount) => amount * exemps,
        None => amex,
    };
    if let Some(l) = &p.low_income_exemption {
        let income_limit = if is_single { l.limit_single } else { l.limit_other / sep };
        let start = l.start[fs as usize];
        let rate = l.rate[fs as usize];
        let per_person = (l.amount - rate * (fed_agi - start).max(0.0)).max(0.0);
        exemp += if fed_agi <= income_limit { exemps / 1.0 * per_person } else { 0.0 };
    }
    if i.is_dependent_filer() {
        exemp = 0.0;
    }
    let cerdep = if is_joint || is_hoh { p.dependent_deduction * (depx - 1.0).max(0.0) } else { 0.0 };
    let taxinc = (agi - elded - deduc - exemp - cerdep).max(0.0);

    // ---- tax ----
    let mut statax = match &p.schedule {
        Schedule::Percent(t) => {
            if is_single {
                t.single.tax(taxinc)
            } else if is_sep {
                t.separate.tax(taxinc)
            } else {
                t.married.tax(taxinc)
            }
        }
        Schedule::Tables { single, married, married_separate, hoh } => {
            let mut married_tax = married.tax(taxinc * sep) / sep;
            if let Some(ms) = married_separate {
                married_tax = if is_sep { ms.tax(taxinc) } else { married.tax(taxinc) };
            }
            if is_single {
                single.tax(taxinc)
            } else if let (Some(h), true) = (hoh, is_hoh) {
                h.tax(taxinc)
            } else {
                married_tax
            }
        }
    };

    // ---- refundable credits ----
    // Taxpayers 65 or older add one to household size.
    let ncred = (txp + depx + aged).min(6.0).floor();
    let mut ycred = 0.0;
    for size in 1..=6usize {
        let lookup = match &p.rebate {
            Rebate::Steps(tables) => tables[size - 1].rate(modagi),
            Rebate::Interpolated(tables) => tables[size - 1].eval(modagi),
        };
        if ncred == size as f64 {
            ycred = lookup;
        }
    }
    if i.is_dependent_filer() {
        ycred = 0.0;
    }
    let xtra = if y <= 1985 {
        (p.food_credit + p.medical_credit) * ncred
    } else if y <= 1989 {
        if ycred > 0.0 { (txp + depx) * p.food_credit } else { 0.0 }
    } else if let Some(food) = &p.food_by_income {
        (if is_single { food.single.eval(hh) } else { food.married.eval(hh / sep) }) * (txp + depx)
    } else {
        0.0
    };
    let chcr = match &p.child_care {
        Some(c) => {
            let children = depx.min(c.max_children);
            let expense = (c.expense_share * i.childcare).min(c.cap_per_child * children);
            if modagi < c.income_limit { (c.credit_cap.min(expense) - fed_ccc).max(0.0) } else { 0.0 }
        }
        None => 0.0,
    };
    let earncr = p.eitc_match_rate * f.eitc;
    // Property tax rebate for taxpayers 65 or older.
    let ptax = i.proptax + p.property_rent_share * i.rentpaid;
    let pmax = p.property_threshold.eval(modagi);
    let pcred = if aged > 0.0 && modagi <= p.property_income_limit {
        (ptax - pmax).max(0.0).min(p.property_max / sep)
    } else {
        0.0
    };
    // Right-closed tiers: income at a threshold takes the lower row.
    let ctc = match &p.child_tax_credit {
        Some(rows) => rows.rate(fed_agi - 0.005) * i.dep18 / sep,
        None => 0.0,
    };
    statax -= ycred + xtra + chcr + pcred + earncr + ctc;
    statax * 1.0
}
