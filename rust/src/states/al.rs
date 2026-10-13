//! Alabama individual income tax (port of `calculators.states.al`).

use std::path::Path;

use crate::common::Brackets;
use crate::federal::{Fed, Inp};
use crate::params::{self, get, load, num, Fs, YearParams, STATUS_KEYS};
use crate::states::{dividend_adjustment, dividend_exclusion_addback, law_year_is_exact};

struct SeHoliday {
    threshold: f64,
    rate: f64,
    flat: f64,
}

struct Step {
    maximum: f64,
    minimum: f64,
    reduction: f64,
    threshold: f64,
    increment: f64,
}

struct Phasedown {
    income_floor: f64,
    income_ceiling: f64,
    min_per_taxpayer: f64,
    max_single: f64,
    max_hoh: f64,
    max_married: f64,
}

enum StandardDeduction {
    Percent { rate: f64, cap_per_taxpayer: f64 },
    Steps([Step; 4]),
    Phasedown(Phasedown),
}

enum DependentExemption {
    Flat(f64),
    ByAgi { high: f64, mid: f64, low: f64, breakpoints: (f64, f64) },
}

pub struct AlParams {
    year: i64,
    table: Brackets,
    se_holiday: Option<SeHoliday>,
    retirement_cap: Option<f64>,
    standard: StandardDeduction,
    exemption_joint_or_hoh: f64,
    exemption_other: f64,
    dependent_exemption: DependentExemption,
    dividend_adjustment: f64,
}

impl AlParams {
    pub fn load(root: &Path, year: i64) -> Result<Self, String> {
        let doc = load(root, "states/al/income_tax.yaml")?;
        law_year_is_exact(root, &doc, year)?;
        let p = YearParams::new(&doc, year);
        let standard = if p.in_effect("standard_deduction_percent")? {
            let sd = p.value("standard_deduction_percent", &[])?;
            StandardDeduction::Percent { rate: num(get(sd, "rate")?)?, cap_per_taxpayer: num(get(sd, "cap_per_taxpayer")?)? }
        } else if p.in_effect("standard_deduction_steps")? {
            let steps = p.value("standard_deduction_steps", &[])?;
            let read = |key: &str| -> Result<Step, String> {
                let t = get(steps, key)?;
                Ok(Step {
                    maximum: num(get(t, "maximum")?)?,
                    minimum: num(get(t, "minimum")?)?,
                    reduction: num(get(t, "reduction")?)?,
                    threshold: num(get(t, "threshold")?)?,
                    increment: num(get(t, "increment")?)?,
                })
            };
            StandardDeduction::Steps([read(STATUS_KEYS[0])?, read(STATUS_KEYS[1])?, read(STATUS_KEYS[2])?, read(STATUS_KEYS[3])?])
        } else {
            let sd = p.value("standard_deduction_phasedown", &[])?;
            let max = get(sd, "max")?;
            StandardDeduction::Phasedown(Phasedown {
                income_floor: num(get(sd, "income_floor")?)?,
                income_ceiling: num(get(sd, "income_ceiling")?)?,
                min_per_taxpayer: num(get(sd, "min_per_taxpayer")?)?,
                max_single: num(get(max, "single")?)?,
                max_hoh: num(get(max, "head_of_household")?)?,
                max_married: num(get(max, "married")?)?,
            })
        };
        let dependent_exemption = if p.in_effect("dependent_exemption_flat")? {
            DependentExemption::Flat(p.f("dependent_exemption_flat")?)
        } else {
            let d = p.value("dependent_exemption_by_agi", &[])?;
            let b = get(d, "breakpoints")?.as_sequence().ok_or("breakpoints must be a list")?;
            DependentExemption::ByAgi {
                high: num(get(d, "high")?)?,
                mid: num(get(d, "mid")?)?,
                low: num(get(d, "low")?)?,
                breakpoints: (num(&b[0])?, num(&b[1])?),
            }
        };
        let exemption = get(&doc, "personal_exemption")?;
        Ok(Self {
            year,
            table: Brackets::new(&params::brackets(p.value("brackets", &[])?)?),
            se_holiday: match p.opt("se_deduction_holiday")? {
                Some(h) => Some(SeHoliday {
                    threshold: num(get(h, "threshold")?)?,
                    rate: num(get(h, "rate")?)?,
                    flat: num(get(h, "flat")?)?,
                }),
                None => None,
            },
            retirement_cap: if p.in_effect("retirement_exemption_cap")? { Some(p.f("retirement_exemption_cap")?) } else { None },
            standard,
            exemption_joint_or_hoh: num(get(exemption, "joint_or_hoh")?)?,
            exemption_other: num(get(exemption, "other")?)?,
            dependent_exemption,
            dividend_adjustment: dividend_adjustment(root)?,
        })
    }
}

pub fn tax(i: &Inp, f: &Fed, p: &AlParams) -> f64 {
    let y = p.year;
    let fs = i.fs();
    let sep = i.sepret();
    let taxpayers = i.taxpayer_count();
    let setax = f.payroll.setax;

    // ---- AGI ----
    // The federal deduction for half of self-employment tax is added back.
    let setax_addback = match &p.se_holiday {
        Some(h) => {
            if setax <= h.threshold { h.rate * setax } else { 0.5 * setax + h.flat }
        }
        None => 0.5 * setax,
    };
    // The federal dividend exclusion (through 1986) is added back.
    let dividend_addback = if y <= 1986 { dividend_exclusion_addback(y, i, f, p.dividend_adjustment) } else { 0.0 };
    // All capital gains are taxable and all losses deductible: when federal AGI holds a capped net loss,
    // the uncapped loss replaces it.
    let capped_capital_gain = if y <= 1986 { f.pre87.capgn } else { f.capgn };
    let raw_capital_gain = i.stcg + i.ltcg;
    let capital_loss_excess = if capped_capital_gain < 0.0 { raw_capital_gain - capped_capital_gain } else { 0.0 };
    // The federal two-earner deduction (1982-1986) is added back.
    let twoded_addback = if (1982..=1986).contains(&y) { f.pre87.twoded } else { 0.0 };
    let mut agi = f.agi + dividend_addback + twoded_addback + setax_addback + capital_loss_excess
        - f.taxable_unemployment
        // Social Security benefits are exempt from 1984.
        - if y >= 1984 { f.taxable_social_security } else { 0.0 };
    if let Some(cap) = p.retirement_cap {
        agi -= i.pensions.min(cap * i.aged_count());
    }

    // ---- standard vs. itemized deduction ----
    let stded = match &p.standard {
        StandardDeduction::Percent { rate, cap_per_taxpayer } => (rate * agi).max(0.0).min(cap_per_taxpayer * taxpayers),
        StandardDeduction::Steps(steps) => {
            let s = &steps[fs as usize];
            let value = s.maximum - s.reduction * ((agi - s.threshold).max(0.0) / s.increment).floor();
            value.max(s.minimum).min(s.maximum)
        }
        StandardDeduction::Phasedown(d) => {
            let agimin = d.income_floor / sep;
            let agimax = d.income_ceiling / sep;
            let stmin = d.min_per_taxpayer * taxpayers;
            let stmax = match fs {
                Fs::Single => d.max_single,
                Fs::Hoh => d.max_hoh,
                _ => d.max_married / sep,
            };
            let excess = (agi - agimin).max(0.0);
            let tga = (stmax - stmin) / (agimax - agimin);
            stmax - excess.min(agimax - agimin) * tga
        }
    };
    // Itemized deductions: mortgage interest, plus from 1982 property and other taxes, half of wage FICA and self-employment tax.
    let xitded_base = if y < 1982 { i.mortgage } else { i.mortgage + i.proptax + i.otheritem };
    let xitded = if y >= 1982 {
        // Additional Medicare tax is employee-only and already in `fica` once.
        let fica_addback = 0.5 * (f.payroll.fica - setax - f.payroll.addmed) + f.payroll.addmed;
        (xitded_base + fica_addback + setax).max(0.0)
    } else {
        xitded_base
    };
    let deduc_before_fedtax = stded.max(xitded);

    // ---- federal income tax deduction ----
    let fedtax = if y <= 1999 {
        f.fiitax.max(0.0)
    } else if y <= 2008 {
        (f.tax_before_credits - f.nonrefundable_credits).max(0.0)
    } else {
        (f.tax_before_credits - f.nonrefundable_credits - f.eitc - f.actc + f.niit).max(0.0)
    };
    let deduc = deduc_before_fedtax + fedtax;

    // ---- exemptions ----
    let base = if fs == Fs::Joint || fs == Fs::Hoh { p.exemption_joint_or_hoh } else { p.exemption_other };
    let dep_exemption = match &p.dependent_exemption {
        DependentExemption::Flat(amount) => amount * i.depx,
        DependentExemption::ByAgi { high, mid, low, breakpoints: (bp1, bp2) } => {
            if y >= 2022 {
                let step = if agi >= *bp2 {
                    *low
                } else if agi >= *bp1 {
                    *mid
                } else {
                    *high
                };
                step * i.depx
            } else {
                let tga = (high - mid) / bp1;
                let tgb = (mid - low) / (bp2 - bp1);
                let excesa = agi.max(0.0).min(*bp1);
                let excesb = (agi - bp1).max(0.0).min(bp2 - bp1);
                (high - excesa * tga - excesb * tgb) * i.depx
            }
        }
    };
    let exemp = base + dep_exemption;
    let taxinc = (agi - deduc - exemp).max(0.0);

    // ---- bracket tax: joint returns split income (tax on half, doubled) ----
    let regtax = if y <= 1981 {
        p.table.tax(taxinc)
    } else {
        let taxy = if fs == Fs::Joint { taxinc / 2.0 } else { taxinc };
        let stat = p.table.tax(taxy);
        if fs == Fs::Joint { stat * 2.0 } else { stat }
    };
    regtax.max(0.0) * 1.0
}
