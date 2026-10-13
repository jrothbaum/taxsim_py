//! Mississippi individual income tax (port of `calculators.states.ms`).

use std::path::Path;

use crate::common::Brackets;
use crate::federal::{Fed, Inp};
use crate::params::{self, get, load, num, status_at, status_direct, Fs, YearParams};
use crate::states::{
    dividend_adjustment, dividend_exclusion_addback, law_year_is_exact, pre1987_gross_itemized, unemployment_total,
};

struct Holiday {
    threshold: f64,
    lower_rate: f64,
    upper_rate: f64,
    amount: f64,
}

struct ItemizedLimit {
    base: f64,
    inflation: f64,
    status_multiplier: [f64; 4],
}

pub struct MsParams {
    year: i64,
    table: Brackets,
    addback_holiday: Option<Holiday>,
    addback_rate: f64,
    deduction_rate: f64,
    charity_nonitemizer_addback: Option<f64>,
    exemption: [f64; 4],
    dependent_exemption: f64,
    aged_exemption: f64,
    /// A share of AGI up to a cap per taxpayer (through 1979), else a fixed amount by status.
    standard_pct: Option<(f64, f64)>,
    standard: Option<[f64; 4]>,
    charity_agi_limit: Option<f64>,
    itemized_limit: Option<ItemizedLimit>,
    split_share: f64,
    retirement_per_taxpayer: Option<f64>,
    dividend_adjustment: f64,
}

impl MsParams {
    pub fn load(root: &Path, year: i64) -> Result<Self, String> {
        let doc = load(root, "states/ms/income_tax.yaml")?;
        law_year_is_exact(root, &doc, year)?;
        let p = YearParams::new(&doc, year);
        let opt = |key: &str| -> Result<Option<f64>, String> { Ok(if p.in_effect(key)? { Some(p.f(key)?) } else { None }) };
        let pct = opt("standard_deduction_pct")?;
        let itemized_limit = match opt("itemized_limit_threshold_base")? {
            Some(base) => Some(ItemizedLimit {
                base,
                inflation: p.f("itemized_limit_inflation")?,
                status_multiplier: if year >= 2013 { status_direct(get(&doc, "itemized_limit_status_multiplier")?)? } else { [1.0; 4] },
            }),
            None => None,
        };
        Ok(Self {
            year,
            table: Brackets::new(&params::brackets(p.value("brackets", &[])?)?),
            addback_holiday: match p.opt("self_employment_tax_addback_holiday")? {
                Some(h) => Some(Holiday {
                    threshold: num(get(h, "threshold")?)?,
                    lower_rate: num(get(h, "lower_rate")?)?,
                    upper_rate: num(get(h, "upper_rate")?)?,
                    amount: num(get(h, "amount")?)?,
                }),
                None => None,
            },
            addback_rate: if p.in_effect("self_employment_tax_addback_holiday")? { 0.0 } else { p.f("self_employment_tax_addback_rate")? },
            deduction_rate: p.f("self_employment_tax_deduction_rate")?,
            charity_nonitemizer_addback: opt("charity_nonitemizer_addback")?,
            exemption: status_at(get(&doc, "exemption")?, year)?,
            dependent_exemption: p.f("dependent_exemption")?,
            aged_exemption: p.f("aged_exemption")?,
            standard_pct: match pct {
                Some(pct) => Some((pct, p.f("standard_deduction_cap_per_taxpayer")?)),
                None => None,
            },
            standard: if pct.is_none() { Some(status_at(get(&doc, "standard_deduction")?, year)?) } else { None },
            charity_agi_limit: opt("charity_agi_limit")?,
            itemized_limit,
            split_share: p.f("joint_split_other_income_share")?,
            retirement_per_taxpayer: opt("retirement_exclusion_per_taxpayer")?,
            dividend_adjustment: dividend_adjustment(root)?,
        })
    }
}

pub fn tax(i: &Inp, f: &Fed, p: &MsParams) -> f64 {
    let y = p.year;
    let is_joint = i.fs() == Fs::Joint;
    let sep = i.sepret();
    let txp = i.taxpayer_count();
    let salt_ded = f.ded;
    let setax = f.payroll.setax;
    let fed_itemizes = f.itemizes;
    let ms_divexc = dividend_exclusion_addback(y, i, f, p.dividend_adjustment);

    let agix = f.agi.max(0.0);
    let alim50 = if y == 2020 || y == 2021 { 1.0e20 } else { 0.5 * agix };
    let fed_char = (if f.agi < 0.0 { 0.0 } else { i.charity_cash.min(alim50) }).max(0.0);

    let mut fed_agi = f.agi;
    if y == 2020 {
        let addback = p.charity_nonitemizer_addback.unwrap_or(0.0);
        fed_agi -= if fed_itemizes { 0.0 } else { (addback / sep).min(i.charity_cash) };
    }

    // ---- Mississippi AGI ----
    let halfse = match &p.addback_holiday {
        Some(h) => {
            if setax <= h.threshold { h.lower_rate * setax } else { h.upper_rate * setax + h.amount }
        }
        None => p.addback_rate * setax,
    };
    let mut agi = fed_agi + ms_divexc + halfse - p.deduction_rate * setax;
    // Retirement income is exempt, up to a cap per taxpayer through 1993.
    agi -= match p.retirement_per_taxpayer {
        Some(cap) => i.pensions.min(cap * txp),
        None => i.pensions,
    };
    if y <= 1979 {
        agi -= i.nonprop;
    }
    if (1982..=1986).contains(&y) {
        agi += f.pre87.twoded;
    }
    // Social Security is exempt from 1984; before, all benefits are income.
    if y >= 1984 {
        agi -= f.taxable_social_security;
    } else {
        agi = agi + i.gssi - f.taxable_social_security;
    }
    if y == 2020 {
        let addback = p.charity_nonitemizer_addback.unwrap_or(0.0);
        agi = agi + unemployment_total(i) - f.taxable_unemployment + if fed_itemizes { 0.0 } else { addback.min(i.charity_cash) };
    }

    // ---- exemptions ----
    let exemp = (p.exemption[i.fs() as usize] + i.depx * p.dependent_exemption + i.aged_count() * p.aged_exemption) / sep;

    // ---- standard deduction ----
    let stded = match (&p.standard_pct, &p.standard) {
        (Some((pct, cap)), _) => (cap * txp).min(pct * agi.max(0.0)),
        (None, Some(by_status)) => by_status[i.fs() as usize] / sep,
        _ => 0.0,
    };

    // ---- itemized deductions (federal total before the high-income limit) ----
    let deducp = if y <= 1986 {
        pre1987_gross_itemized(i, f)
    } else if y <= 2017 {
        (i.proptax + i.otheritem + salt_ded).max(0.0) + i.mortgage + fed_char
    } else {
        f.itemized_deduction
    };
    let mut xitded = if y < 1991 {
        deducp
    } else {
        let mut x = (deducp - salt_ded).max(0.0);
        if let Some(l) = &p.itemized_limit {
            let threshold = if y >= 2013 {
                l.base * l.inflation * l.status_multiplier[i.fs() as usize]
            } else {
                l.base * l.inflation / sep
            };
            let dedphs = deducp - f.itemized_deduction;
            let limited = (deducp - dedphs - (salt_ded - salt_ded * dedphs / deducp)).max(0.0);
            if agi > threshold && deducp > 0.0 {
                x = limited;
            }
        }
        x
    };
    if let Some(limit) = p.charity_agi_limit {
        let contributions = (i.charity_cash - limit * agi.max(0.0)).max(0.0);
        xitded = (xitded - contributions).max(0.0);
    }
    if y == 1999 && !f.forced_itemize {
        xitded = 0.0;
    }

    let deduc = stded.max(xitded);
    let taxinc = (agi - deduc - exemp).max(0.0);
    let mut statax = p.table.tax(taxinc);

    // ---- married couples: tax as separate returns if lower ----
    let wages_total = i.pwages.max(0.0) + i.swages.max(0.0);
    let agih = agi.min(i.pwages.max(i.swages) + p.split_share * (agi - wages_total));
    let agiw = agi - agih;
    let exemph = exemp * agih / agi;
    let exempw = exemp - exemph;
    let taxyh1 = (agih - exemph).max(0.0);
    let taxyw1 = (agiw - exempw).max(0.0);
    let deducw = taxyw1.min(deduc * agiw / agi);
    let deduch = deduc - deducw;
    let split_tax = p.table.tax((taxyh1 - deduch).max(0.0)) + p.table.tax((taxyw1 - deducw).max(0.0));
    if is_joint && agi > 0.0 {
        statax = statax.min(split_tax);
    }
    statax * 1.0
}
