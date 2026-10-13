//! Missouri income tax (port of `calculators.states.mo`).

use std::path::Path;

use crate::common::Brackets;
use crate::federal::{Fed, Inp};
use crate::params::{self, get, load, num, status_direct, Fs, YearParams};
use crate::states::{dividend_adjustment, household_income, law_year_is_exact, Socsec};

struct LowIncome {
    amount: f64,
    limit: f64,
}

struct ItemizedLimit {
    rate: f64,
    max_share: f64,
    base: f64,
    inflation: f64,
    status_multiplier: [f64; 4],
}

enum SeDeduction {
    Cap(f64),
    Holiday { threshold: f64, lower_rate: f64, upper_rate: f64, amount: f64 },
    Rate(f64),
}

struct Owner {
    income_limit: f64,
    maximum: f64,
    steps: f64,
}

struct Schedule {
    base: f64,
    step: f64,
    steps: f64,
}

struct Wftc {
    matching: f64,
    investment_limit: f64,
}

pub struct MoParams {
    year: i64,
    split_share: f64,
    exemption_hoh: f64,
    exemption_per_taxpayer: f64,
    exemption_per_dependent: f64,
    low_income: Option<LowIncome>,
    phase_single: f64,
    phase_joint: f64,
    ss_share: Option<f64>,
    pension_limit_single: f64,
    pension_limit_married: f64,
    pension_flat: Option<f64>,
    pension_maximum: Option<f64>,
    payroll_cap: f64,
    socsec: Socsec,
    se_deduction: SeDeduction,
    itemized_limit: Option<ItemizedLimit>,
    fed_tax_cap: Option<f64>,
    fed_tax_share: Option<Vec<(f64, f64)>>,
    capital_gains_subtraction: Option<f64>,
    inflation: f64,
    table: Brackets,
    minimum_taxable_income: f64,
    married_subtraction: f64,
    married_owner_subtraction: Option<f64>,
    schedule: Schedule,
    income_limit: f64,
    maximum: f64,
    owner: Option<Owner>,
    rate_step: f64,
    rent_share: f64,
    wftc: Option<Wftc>,
    dividend_adjustment: f64,
}

impl MoParams {
    pub fn load(root: &Path, year: i64) -> Result<Self, String> {
        let doc = load(root, "states/mo/income_tax.yaml")?;
        law_year_is_exact(root, &doc, year)?;
        let p = YearParams::new(&doc, year);
        let opt = |key: &str| -> Result<Option<f64>, String> { Ok(if p.in_effect(key)? { Some(p.f(key)?) } else { None }) };

        let low_income = match p.opt("low_income_exemption")? {
            Some(l) => Some(LowIncome { amount: num(get(l, "amount")?)?, limit: num(get(l, "income_limit")?)? }),
            None => None,
        };
        let phases = get(&doc, "social_security_exemption_phaseout_start")?;
        let limits = get(&doc, "pension_exemption_income_limit")?;
        let se_deduction = if p.in_effect("self_employment_tax_deduction_cap")? {
            SeDeduction::Cap(p.f("self_employment_tax_deduction_cap")?)
        } else if p.in_effect("self_employment_tax_deduction_holiday")? {
            let h = p.value("self_employment_tax_deduction_holiday", &[])?;
            SeDeduction::Holiday {
                threshold: num(get(h, "threshold")?)?,
                lower_rate: num(get(h, "lower_rate")?)?,
                upper_rate: num(get(h, "upper_rate")?)?,
                amount: num(get(h, "amount")?)?,
            }
        } else {
            SeDeduction::Rate(p.f("self_employment_tax_deduction_rate")?)
        };
        let itemized_limit = if p.in_effect("itemized_limit_rate")? {
            Some(ItemizedLimit {
                rate: p.f("itemized_limit_rate")?,
                max_share: p.f("itemized_limit_max_share")?,
                base: p.f("itemized_limit_threshold_base")?,
                inflation: p.f("itemized_limit_inflation")?,
                status_multiplier: if year >= 2013 { status_direct(get(&doc, "itemized_limit_status_multiplier")?)? } else { [1.0; 4] },
            })
        } else {
            None
        };
        let schedule_v = p.value("property_credit_schedule", &[])?;
        let owner = match p.opt("property_credit_owner")? {
            Some(o) => Some(Owner {
                income_limit: num(get(o, "income_limit")?)?,
                maximum: num(get(o, "maximum")?)?,
                steps: num(get(o, "steps")?)?,
            }),
            None => None,
        };
        let inflation = p.f("bracket_inflation")?;
        let scaled: Vec<Vec<f64>> = params::brackets(p.value("brackets", &[])?)?.into_iter().map(|r| vec![r[0] * inflation, r[1]]).collect();
        Ok(Self {
            year,
            split_share: p.f("joint_split_other_income_share")?,
            exemption_hoh: p.f("exemption_head_of_household")?,
            exemption_per_taxpayer: p.f("exemption_per_taxpayer")?,
            exemption_per_dependent: p.f("exemption_per_dependent")?,
            low_income,
            phase_single: num(get(phases, "single")?)?,
            phase_joint: num(get(phases, "married_joint")?)?,
            ss_share: if year >= 2007 { Some(p.f("social_security_exemption_share")?) } else { None },
            pension_limit_single: num(get(limits, "single")?)?,
            pension_limit_married: num(get(limits, "married")?)?,
            pension_flat: if year >= 1990 && year <= 2013 { Some(p.f("pension_exemption_flat")?) } else { None },
            pension_maximum: if year >= 2007 { Some(p.f("pension_exemption_maximum")?) } else { None },
            payroll_cap: p.f("payroll_tax_deduction_cap")?,
            socsec: Socsec::load(root, year)?,
            se_deduction,
            itemized_limit,
            fed_tax_cap: opt("federal_tax_deduction_cap_per_taxpayer")?,
            fed_tax_share: if p.in_effect("federal_tax_deduction_share")? {
                Some(params::brackets(p.value("federal_tax_deduction_share", &[])?)?.into_iter().map(|r| (r[0], r[1])).collect())
            } else {
                None
            },
            capital_gains_subtraction: opt("capital_gains_subtraction")?,
            inflation,
            table: Brackets::new(&scaled),
            minimum_taxable_income: p.f("minimum_taxable_income")?,
            married_subtraction: p.f("property_credit_married_subtraction")?,
            married_owner_subtraction: if year > 2007 { Some(p.f("property_credit_married_owner_subtraction")?) } else { None },
            schedule: Schedule {
                base: num(get(schedule_v, "base")?)?,
                step: num(get(schedule_v, "step")?)?,
                steps: num(get(schedule_v, "steps")?)?,
            },
            income_limit: p.f("property_credit_income_limit")?,
            maximum: p.f("property_credit_maximum")?,
            owner,
            rate_step: num(get(&doc, "property_credit_rate_step")?)?,
            rent_share: num(get(&doc, "property_credit_rent_share")?)?,
            wftc: match p.opt("working_family_credit")? {
                Some(c) => Some(Wftc { matching: num(get(c, "match")?)?, investment_limit: num(get(c, "investment_income_limit")?)? }),
                None => None,
            },
            dividend_adjustment: dividend_adjustment(root)?,
        })
    }
}

pub fn tax(i: &Inp, f: &Fed, p: &MoParams) -> f64 {
    let y = p.year;
    let fs = i.fs();
    let is_joint = fs == Fs::Joint;
    let is_sep = fs == Fs::Separate;
    let is_hoh = fs == Fs::Hoh;
    let sep = i.sepret();
    let txp = i.taxpayer_count();
    let aged = i.aged_count();
    let dependent_filer = i.is_dependent_filer();
    let depx = i.depx;
    let salt_ded = f.ded;
    let setax = f.payroll.setax;
    let agi = f.agi;
    let hh = household_income(i, p.dividend_adjustment);
    let wages_total = i.pwages.max(0.0) + i.swages.max(0.0);
    let agih = i.pwages.max(i.swages) + p.split_share * (agi - wages_total);
    let agiw = agi - agih;

    // ---- federal return values as Missouri reads them ----
    let (fed_itemizes, fed_zbr, deducp, dedphs);
    if y <= 1986 {
        deducp = i.proptax + i.otheritem + i.mortgage + salt_ded;
        fed_zbr = f.pre87.zbr;
        fed_itemizes = if y <= 1981 { deducp > fed_zbr } else { f.forced_itemize };
        dedphs = 0.0;
    } else {
        fed_itemizes = f.itemizes;
        fed_zbr = if fed_itemizes { 0.0 } else { f.standard_deduction };
        let agix = agi.max(0.0);
        let alim50 = if y == 2020 || y == 2021 { 1.0e20 } else { 0.5 * agix };
        let fed_char = (if agi < 0.0 { 0.0 } else { i.charity_cash.min(alim50) }).max(0.0);
        deducp = if y <= 2017 {
            (i.proptax + i.otheritem + salt_ded).max(0.0) + i.mortgage + fed_char
        } else {
            f.itemized_deduction
        };
        dedphs = if (1991..=2017).contains(&y) { deducp - f.itemized_deduction } else { 0.0 };
    }

    // ---- exemptions ----
    let mut exemp = (if is_hoh { p.exemption_hoh } else { p.exemption_per_taxpayer * txp }) + depx * p.exemption_per_dependent;
    if let Some(l) = &p.low_income {
        let joint_bonus = (if agih < l.limit { l.amount } else { 0.0 }) + (if agiw < l.limit { l.amount } else { 0.0 });
        exemp += if dependent_filer {
            0.0
        } else if is_joint {
            joint_bonus
        } else if agi < l.limit {
            l.amount
        } else {
            0.0
        };
    }
    // Social Security exemption (2007+), reduced by AGI over the phaseout start.
    let phase = if is_joint { p.phase_joint } else { p.phase_single };
    let mut ssaxmp = 0.0;
    if let Some(share) = p.ss_share {
        ssaxmp = (share * f.taxable_social_security - (agi - phase).max(0.0)).max(0.0);
        exemp += ssaxmp;
    }
    // Public pension exemption for taxpayers 65 or older (1990+).
    if y >= 1990 {
        let magi = agi - f.taxable_social_security;
        let exc = (magi - phase).max(0.0);
        let pension = i.pensions;
        let slim = if is_joint || is_sep { p.pension_limit_married / sep } else { p.pension_limit_single };
        let scred = if y <= 1998 {
            if magi <= slim { p.pension_flat.unwrap_or(0.0).min(pension) } else { 0.0 }
        } else if y <= 2006 {
            let cap = p.pension_flat.unwrap_or(0.0);
            if magi - slim <= cap * txp { cap.min(pension) } else { 0.0 }
        } else {
            let ssmax = p.pension_maximum.unwrap_or(0.0);
            let share = p.ss_share.unwrap_or(0.0);
            let allowed = if y <= 2013 {
                let cap = p.pension_flat.unwrap_or(0.0);
                let limited = ssmax.min(share * pension);
                if y <= 2008 {
                    let nontaxable = i.gssi - f.taxable_social_security;
                    (limited - nontaxable).max(0.0).max(cap.min(pension))
                } else {
                    (limited.max(cap.min(pension)) - ssaxmp).max(0.0)
                }
            } else {
                (ssmax.min(pension) - ssaxmp).max(0.0)
            };
            (allowed - exc).max(0.0)
        };
        exemp += if aged > 0.0 { scred } else { 0.0 };
    }
    if dependent_filer {
        exemp = 0.0;
    }

    // ---- itemized deductions (only when itemizing federally) ----
    let socsec = p.socsec.total(y, i, setax, f.payroll.addmed);
    let mut xitded = (deducp + socsec.min(p.payroll_cap * txp) - salt_ded).max(0.0);
    let septx = match &p.se_deduction {
        SeDeduction::Cap(cap) => (cap * txp).min(setax),
        SeDeduction::Holiday { threshold, lower_rate, upper_rate, amount } => {
            if setax <= *threshold { lower_rate * setax } else { upper_rate * setax + amount }
        }
        SeDeduction::Rate(rate) => rate * setax,
    };
    xitded = (xitded - septx).max(0.0);
    let mut stax = salt_ded;
    if let Some(l) = &p.itemized_limit {
        let threshold = if y >= 2013 { l.base * l.inflation * l.status_multiplier[fs as usize] } else { l.base * l.inflation / sep };
        let xtot = salt_ded + i.proptax + i.mortgage + i.charity_cash;
        let over = agi - threshold;
        let xconst = (l.max_share * xtot).min(l.rate * over);
        stax = if xtot > 0.0 && over > 0.0 { (xtot - xconst) / xtot * salt_ded } else { salt_ded };
    }
    xitded = (xitded - dedphs + (salt_ded - stax)).max(0.0);
    if y == 1999 && !f.forced_itemize {
        xitded = 0.0;
    }
    let deduc = if fed_itemizes { fed_zbr.max(xitded) } else { fed_zbr };

    // ---- deduction for federal income tax ----
    let mut fedtax = if y <= 1986 {
        f.pre87.taxbc - f.federal_chcr - f.pre87.earncr
    } else {
        let ccc_used = if y == 2021 { 0.0 } else { f.federal_chcr };
        let ctc_used = if y == 2021 { f.actc } else if y >= 1998 { f.odc } else { 0.0 };
        // Regular tax less the child care, elderly, earned income, child and Making Work Pay credits.
        f.regular_tax - ccc_used - f.federal_elder - f.eitc - ctc_used - f.making_work_pay
    };
    fedtax = fedtax.max(0.0);
    if let Some(cap) = p.fed_tax_cap {
        fedtax = fedtax.min(cap * txp);
    }
    if let Some(rows) = &p.fed_tax_share {
        let mut share = 0.0;
        for (upper, value) in rows.iter().rev() {
            share = if agi <= *upper { *value } else { share };
        }
        fedtax *= share;
    }
    let cg_subtraction = p.capital_gains_subtraction.map_or(0.0, |rate| rate * (i.stcg + i.ltcg).max(0.0));
    let taxinc = (agi - deduc - fedtax - exemp - cg_subtraction).max(0.0);

    let taxh = taxinc * agih / agi;
    let taxw = taxinc * agiw / agi;
    let mut statax = p.table.tax(taxinc);
    let split_tax = p.table.tax(taxh) + p.table.tax(taxw);
    if is_joint && agi > 0.0 {
        statax = statax.min(split_tax);
    }
    if taxinc < p.minimum_taxable_income * p.inflation {
        statax = 0.0;
    }

    // ---- property tax credit for taxpayers 65 or older ----
    let (proptax, rentpaid) = (i.proptax, i.rentpaid);
    let mut married_sub = p.married_subtraction;
    if y > 2007 {
        married_sub = (if proptax > 0.0 { p.married_owner_subtraction.unwrap_or(0.0) } else { 0.0 })
            + (if rentpaid > 0.0 { p.married_subtraction } else { 0.0 });
    }
    let hy1 = if is_joint { (hh - married_sub).max(0.0) } else { hh };
    let (base, step) = (p.schedule.base, p.schedule.step);
    let (mut income_limit, mut maximum, mut steps) = (p.income_limit, p.maximum, p.schedule.steps);
    if let Some(o) = &p.owner {
        if proptax > 0.0 {
            income_limit = o.income_limit;
            maximum = o.maximum;
            steps = o.steps;
        }
    }
    // The last step reached sets the income percentage.
    let reached = ((hy1 - base) / step).floor().min(steps - 1.0);
    let ptax = proptax + p.rent_share * rentpaid;
    // RSMo 135.030: tax paid up to the maximum less the income percentage.
    let paid = ptax.min(maximum);
    let stepped = (paid - hy1 * p.rate_step * (reached.max(0.0) + 1.0)).max(0.0);
    let mut pcred = if aged > 0.0 && hy1 < income_limit && ptax > 0.0 {
        if hy1 <= base { paid } else { stepped }
    } else {
        0.0
    };
    if y <= 1978 {
        pcred = pcred.min(statax);
    }
    statax -= pcred;
    if let Some(c) = &p.wftc {
        let investment = i.dividends + i.intrec + (i.stcg + i.ltcg).max(0.0) + i.otherprop.max(0.0);
        let eligible = investment <= c.investment_limit && !dependent_filer;
        let wftc = if eligible { (c.matching * f.eitc).min(statax.max(0.0)) } else { 0.0 };
        statax -= wftc;
    }
    statax * 1.0
}
