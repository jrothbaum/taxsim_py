//! Georgia income tax (port of `calculators.states.ga`).

use std::path::Path;

use crate::common::Brackets;
use crate::federal::{Fed, Inp};
use crate::params::{self, get, load, num, status_direct, Fs, YearParams};
use crate::states::{dividend_adjustment, law_year_is_exact, unemployment_total, Interp};

struct Percent {
    rate: f64,
    floor: f64,
    ceiling: f64,
}

struct IraAddback {
    amount: f64,
    limit_joint: f64,
    limit_other: f64,
}

struct UnemploymentThresholds {
    georgia: [f64; 4],
    federal: [f64; 4],
}

struct EarningsCap {
    cap: f64,
}

enum Exemption {
    /// Through 1986.
    Early { exemption: f64, taxpayer: f64, hoh_dependent: f64 },
    /// No dependent exemption yet: the same amount for everyone.
    Uniform { exemption: f64, taxpayer: f64 },
    DependentOnly(f64),
    Married { exemption: f64, married: f64, dependent: f64 },
    Plain { exemption: f64, dependent: f64 },
}

enum Standard {
    Percent { single: Percent, married: Percent },
    Fixed { single: f64, married: f64, aged: f64 },
}

enum ChildCare {
    None,
    Expenses { cap_per_dependent: f64, cap_min: f64, cap_max: f64, rate: f64 },
    FederalShare(f64),
}

enum LowIncome {
    None,
    Flat { per_taxpayer: f64, threshold: f64 },
    PerExemption(Interp),
}

enum Schedule {
    Flat(f64),
    Tables { single: Brackets, married: Brackets },
}

pub struct GaParams {
    year: i64,
    ira: Option<IraAddback>,
    two_earner: Option<(f64, f64)>,
    unemployment: Option<UnemploymentThresholds>,
    retirement_max: f64,
    retirement_max_62_to_64: Option<f64>,
    retirement_earned_limit: Option<f64>,
    earnings_cap: Option<EarningsCap>,
    exemption: Exemption,
    standard: Standard,
    schedule: Schedule,
    child_care: ChildCare,
    low_income: LowIncome,
    low_income_refundable: bool,
    /// From 2026 a nonrefundable credit per child under 6.
    under_six_credit: Option<f64>,
    dividend_adjustment: f64,
}

impl GaParams {
    pub fn load(root: &Path, year: i64) -> Result<Self, String> {
        let doc = load(root, "states/ga/income_tax.yaml")?;
        law_year_is_exact(root, &doc, year)?;
        let pre87 = load(root, "national/pre1987.yaml")?;
        let p = YearParams::new(&doc, year);
        let opt = |key: &str| -> Result<Option<f64>, String> { Ok(if p.in_effect(key)? { Some(p.f(key)?) } else { None }) };
        let early = (1982..=1986).contains(&year);

        let ira = if early {
            let v = p.value("ira_addback", &[])?;
            Some(IraAddback { amount: num(get(v, "amount")?)?, limit_joint: num(get(v, "limit_joint")?)?, limit_other: num(get(v, "limit_other")?)? })
        } else {
            None
        };
        let two_earner = if early {
            let q = YearParams::new(&pre87, year);
            Some((q.f("two_earner_deduction_rate")?, q.f("two_earner_deduction_cap")?))
        } else {
            None
        };
        let unemployment = if early {
            let t = p.value("unemployment_threshold", &[])?;
            Some(UnemploymentThresholds { georgia: status_direct(get(t, "georgia")?)?, federal: status_direct(get(t, "federal")?)? })
        } else {
            None
        };
        let earnings_cap = match opt("retirement_exclusion_earnings_cap")? {
            Some(cap) => Some(EarningsCap { cap }),
            None => None,
        };
        let exemption = if year <= 1986 {
            Exemption::Early {
                exemption: p.f("exemption")?,
                taxpayer: p.f("taxpayer_exemption")?,
                hoh_dependent: p.f("head_of_household_dependent_exemption")?,
            }
        } else if !p.in_effect("dependent_exemption")? {
            let exemption = p.f("exemption")?;
            Exemption::Uniform { exemption, taxpayer: opt("taxpayer_exemption")?.unwrap_or(exemption) }
        } else if !p.in_effect("exemption")? {
            Exemption::DependentOnly(p.f("dependent_exemption")?)
        } else if p.in_effect("married_exemption")? {
            Exemption::Married { exemption: p.f("exemption")?, married: p.f("married_exemption")?, dependent: p.f("dependent_exemption")? }
        } else {
            Exemption::Plain { exemption: p.f("exemption")?, dependent: p.f("dependent_exemption")? }
        };
        let standard = if p.in_effect("standard_deduction_percent")? {
            let v = p.value("standard_deduction_percent", &[])?;
            let read = |key: &str| -> Result<Percent, String> {
                let s = get(v, key)?;
                Ok(Percent { rate: num(get(s, "rate")?)?, floor: num(get(s, "floor")?)?, ceiling: num(get(s, "ceiling")?)? })
            };
            Standard::Percent { single: read("single")?, married: read("married")? }
        } else {
            Standard::Fixed {
                single: p.num_at("standard_deduction", &["single"])?,
                married: p.num_at("standard_deduction", &["married"])?,
                aged: p.f("aged_standard_deduction")?,
            }
        };
        let schedule = if p.in_effect("flat_rate")? {
            Schedule::Flat(p.f("flat_rate")?)
        } else {
            let t = p.value("brackets", &[])?;
            Schedule::Tables {
                single: Brackets::new(&params::brackets(get(t, "single")?)?),
                married: Brackets::new(&params::brackets(get(t, "married")?)?),
            }
        };
        let child_care = if p.in_effect("child_care_credit_expenses")? {
            let cc = p.value("child_care_credit_expenses", &[])?;
            ChildCare::Expenses {
                cap_per_dependent: num(get(cc, "cap_per_dependent")?)?,
                cap_min: num(get(cc, "cap_min")?)?,
                cap_max: num(get(cc, "cap_max")?)?,
                rate: num(get(cc, "rate")?)?,
            }
        } else if p.in_effect("child_care_credit_federal_share")? {
            ChildCare::FederalShare(p.f("child_care_credit_federal_share")?)
        } else {
            ChildCare::None
        };
        let low_income = if p.in_effect("low_income_credit_flat")? {
            let l = p.value("low_income_credit_flat", &[])?;
            LowIncome::Flat { per_taxpayer: num(get(l, "per_taxpayer")?)?, threshold: num(get(l, "agi_threshold_per_taxpayer")?)? }
        } else if p.in_effect("low_income_credit_per_exemption")? {
            LowIncome::PerExemption(Interp::new(&params::brackets(p.value("low_income_credit_per_exemption", &[])?)?))
        } else {
            LowIncome::None
        };
        Ok(Self {
            year,
            under_six_credit: if p.in_effect("child_tax_credit_under_six")? { Some(p.f("child_tax_credit_under_six")?) } else { None },
            ira,
            two_earner,
            unemployment,
            retirement_max: p.f("retirement_exclusion_max")?,
            retirement_max_62_to_64: if p.in_effect("retirement_exclusion_max_age_62_to_64")? { Some(p.f("retirement_exclusion_max_age_62_to_64")?) } else { None },
            retirement_earned_limit: opt("retirement_exclusion_earned_limit")?,
            earnings_cap,
            exemption,
            standard,
            schedule,
            child_care,
            low_income,
            low_income_refundable: p.value("low_income_credit_refundable", &[])?.as_bool().ok_or("expected a boolean")?,
            dividend_adjustment: dividend_adjustment(root)?,
        })
    }
}

pub fn tax(i: &Inp, f: &Fed, p: &GaParams) -> f64 {
    let y = p.year;
    let fs = i.fs();
    let sep = i.sepret();
    let taxpayers = i.taxpayer_count();
    let aged = i.aged_count();
    let wages = i.pwages + i.swages;

    // ---- AGI ----
    let mut agi = f.agi;
    // 2020: unemployment compensation excluded federally is taxable.
    if y == 2020 {
        agi = agi + unemployment_total(i) - f.taxable_unemployment;
    }
    // 1982-1986: Georgia kept 1981 federal law, adding back the two-earner deduction,
    // part of the IRA deduction and federally excluded unemployment compensation.
    if let (Some((rate, cap)), Some(ira), Some(t)) = (p.two_earner, &p.ira, &p.unemployment) {
        let twoded = (rate * i.pwages.min(i.swages).max(0.0)).max(0.0).min(cap);
        agi += twoded;
        // IRA contributions are not an input, so a return with little earned income adds back up to the full amount.
        let earnings = wages + (i.psemp + i.ssemp + i.otherprop.max(0.0)).max(0.0);
        let iramax = if fs == Fs::Joint { ira.limit_joint } else { ira.limit_other };
        let iralim = earnings.min(iramax);
        agi += (ira.amount - iralim).max(0.0).min(ira.amount);
        let ui_total = unemployment_total(i);
        let stutx = (0.5 * (ui_total + agi - t.georgia[fs as usize]).max(0.0)).min(ui_total);
        let fdutx = (0.5 * (ui_total + agi - t.federal[fs as usize]).max(0.0)).min(ui_total);
        agi += stutx - fdutx;
    }

    // Retirement income exclusion for taxpayers 65 or older.
    let rtmax = p.retirement_max;
    let mut rtexc = f.agi.min(rtmax * aged).max(0.0);
    if let Some(limit) = p.retirement_earned_limit {
        if f.earned_income > limit * taxpayers {
            rtexc = 0.0;
        }
    }
    let mut eligible = aged > 0.0;
    if let Some(c) = &p.earnings_cap {
        // From 1989 the exclusion is for taxpayers 62 or older: up to the cap of the taxpayer's wages plus retirement and
        // investment income, split between spouses.
        let head_62 = i.page >= 62.0;
        let spouse_62 = i.sage >= 62.0;
        eligible = head_62 || spouse_62;
        let business = (i.pbusinc + i.pprofinc + i.scorp + i.sbusinc + i.sprofinc).max(0.0);
        let gains = (i.stcg + i.ltcg).max(0.0);
        let other = i.dividends + p.dividend_adjustment + i.intrec + business + gains;
        let one = |wages: f64| wages.min(c.cap) + i.pensions + other / taxpayers.max(1.0);
        let both = |wages: f64| wages.min(c.cap) + (i.pensions + other) / 2.0;
        let share = |mine: bool, other_eligible: bool, wages: f64| {
            if mine && !other_eligible { one(wages) } else if mine { both(wages) } else { 0.0 }
        };
        let limit = |age: f64| match p.retirement_max_62_to_64 {
            Some(low) if age < 65.0 => low,
            _ => rtmax,
        };
        let head = share(head_62, spouse_62, i.pwages);
        let spouse = share(spouse_62, head_62, i.swages);
        rtexc = (head.min(limit(i.page)) + spouse.min(limit(i.sage))).max(0.0);
    }
    agi -= if eligible { rtexc } else { 0.0 };
    // Social Security benefits are exempt from 1988.
    if y >= 1988 {
        agi -= f.taxable_social_security;
    }

    // ---- exemptions ----
    let exemp = match &p.exemption {
        Exemption::Early { exemption, taxpayer, hoh_dependent } => {
            taxpayers * taxpayer
                + (i.depx + aged) * exemption
                + if fs == Fs::Hoh && i.depx > 0.0 { *hoh_dependent } else { 0.0 }
        }
        Exemption::Uniform { exemption, taxpayer } => taxpayers * taxpayer + i.depx * exemption,
        Exemption::DependentOnly(dependent) => i.depx * dependent,
        Exemption::Married { exemption, married, dependent } => {
            (if fs == Fs::Single || fs == Fs::Hoh { exemption * taxpayers } else { married * taxpayers }) + i.depx * dependent
        }
        Exemption::Plain { exemption, dependent } => exemption * taxpayers + i.depx * dependent,
    };

    // ---- standard deduction ----
    let is_married = fs == Fs::Joint || fs == Fs::Separate;
    let stded = match &p.standard {
        Standard::Percent { single, married } => {
            if is_married {
                (married.rate * agi).max(married.floor / sep).min(married.ceiling / sep)
            } else {
                (single.rate * agi).max(single.floor).min(single.ceiling)
            }
        }
        Standard::Fixed { single, married, aged: aged_std } => {
            (if is_married { married / sep } else { *single }) + aged_std * aged
        }
    };

    // ---- itemized deduction ---- Federal itemized deductions (zero when not itemizing) less the state tax deduction.
    let mut xitded = if y <= 1986 {
        (f.pre87.deduc - f.ded) * (f.pre87.itemizes as u8 as f64)
    } else {
        (f.itemized_deduction - f.ded) * (f.itemizes as u8 as f64)
    };
    if y == 1999 && !f.forced_itemize {
        xitded = 0.0;
    }
    let deduc = xitded.max(stded);
    let taxinc = (agi - deduc - exemp).max(0.0);

    // ---- tax ----
    let mut statax = match &p.schedule {
        Schedule::Flat(rate) => rate * taxinc,
        Schedule::Tables { single, married } => {
            if fs != Fs::Single {
                married.tax(taxinc * sep) / sep
            } else {
                single.tax(taxinc)
            }
        }
    };

    // ---- credits ----
    let mut chcr = match &p.child_care {
        ChildCare::None => 0.0,
        ChildCare::Expenses { cap_per_dependent, cap_min, cap_max, rate } => {
            let chmax = (i.depx * cap_per_dependent).max(*cap_min).min(*cap_max);
            let chexp = i.childcare.min(chmax).min(wages.max(0.0));
            chexp * rate
        }
        ChildCare::FederalShare(rate) => rate * f.federal_chcr.max(0.0).min(f.tax_before_credits.max(0.0)),
    };
    if let Some(per_child) = p.under_six_credit {
        // After the other nonrefundable credits.
        let after_other = (statax - chcr).max(0.0);
        chcr += after_other.min(per_child * i.dep6);
    }
    let mut ycred = match &p.low_income {
        LowIncome::Flat { per_taxpayer, threshold } => {
            // Heads of household count as two taxpayers.
            let txp = if fs == Fs::Single || fs == Fs::Separate { 1.0 } else { 2.0 };
            (per_taxpayer * txp - (f.agi - threshold * txp)).max(0.0).min(per_taxpayer * txp)
        }
        LowIncome::PerExemption(table) => table.eval(f.agi) * (taxpayers + i.depx + aged),
        LowIncome::None => 0.0,
    };
    if i.is_dependent_filer() {
        ycred = 0.0;
    }
    statax = if p.low_income_refundable {
        (statax - chcr).max(0.0) - ycred
    } else {
        (statax - chcr - ycred).max(0.0)
    };
    statax * 1.0
}
