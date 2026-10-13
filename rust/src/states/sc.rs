//! South Carolina income tax (port of `calculators.states.sc`).

use std::path::Path;

use crate::common::Brackets;
use crate::federal::{pre1987, Fed, Inp, Pre87Params};
use crate::params::{self, get, load, num, resolve_year, status_direct, Fs, YearParams};
use crate::states::{
    dividend_adjustment, dividend_exclusion_addback, federal_exemption_count, law_year_is_exact, pre1987_gross_itemized, unemployment_total,
};

struct EarlyLaw {
    index: f64,
    deduction_rate: f64,
    deduction_cap_per_taxpayer: f64,
    federal_tax_cap: f64,
    gasoline: Option<f64>,
    charity_limit_share: f64,
    child_care_base: f64,
    child_care_per_dependent: f64,
    child_care_max_dependents: f64,
    child_care_phaseout_agi: f64,
    child_care_phaseout_rate: f64,
    exemption_amount: f64,
    table: Brackets,
    food_credit: Option<f64>,
    aged_no_tax: Option<(f64, f64)>,
    /// The federal tax figured under 1980 law, for 1981-1984.
    law_1980: Option<Pre87Params>,
}

struct ItemizedLimit {
    threshold: f64,
    index: f64,
    status_factor: Option<[f64; 4]>,
}

enum Schedule {
    /// 1985-1986: separate tables with an exemption amount subtracted first.
    ByStatus { single: Brackets, married: Brackets, exemption_single: f64, exemption_married: f64 },
    Scaled(Brackets),
}

struct Retirement {
    young: f64,
    aged: Option<f64>,
    aged_flat: Option<f64>,
}

struct TwoEarner {
    maximum: f64,
    rate: f64,
}

pub struct ScParams {
    year: i64,
    early: Option<EarlyLaw>,
    long_term_gain_share: f64,
    interest_dividend_exclusion: Option<f64>,
    charity_nonitemizer_addback: Option<f64>,
    retirement: Option<Retirement>,
    itemized_limit: Option<ItemizedLimit>,
    salt_cap: Option<f64>,
    capital_gain_deduction: Option<f64>,
    married_addback: Option<f64>,
    standard_increase: Option<[f64; 4]>,
    dependent_exemption: Option<f64>,
    schedule: Option<Schedule>,
    federal_exemption_amount: f64,
    child_care_rate: f64,
    child_care_expense: f64,
    child_care_cap: Option<f64>,
    two_earner: Option<TwoEarner>,
    eitc_rate: Option<f64>,
    dividend_adjustment: f64,
}

impl ScParams {
    pub fn load(root: &Path, year: i64) -> Result<Self, String> {
        let doc = load(root, "states/sc/income_tax.yaml")?;
        law_year_is_exact(root, &doc, year)?;
        let fed_exemption = load(root, "national/personal_exemption.yaml")?;
        let p = YearParams::new(&doc, year);
        let opt = |key: &str| -> Result<Option<f64>, String> { Ok(if p.in_effect(key)? { Some(p.f(key)?) } else { None }) };

        let early = if year <= 1984 {
            let index = p.f("bracket_index")?;
            let share = p.value("standard_deduction_share", &[])?;
            let cd = p.value("child_care_deduction", &[])?;
            let table_rows: Vec<Vec<f64>> =
                params::brackets(p.value("brackets", &[])?)?.into_iter().map(|r| vec![r[0] * index, r[1]]).collect();
            let food_credit = opt("food_credit")?;
            Some(EarlyLaw {
                index,
                deduction_rate: num(get(share, "rate")?)?,
                deduction_cap_per_taxpayer: num(get(share, "cap_per_taxpayer")?)?,
                federal_tax_cap: p.f("federal_tax_deduction_cap")?,
                gasoline: opt("gasoline_deduction")?,
                charity_limit_share: p.f("charity_limit_share")?,
                child_care_base: num(get(cd, "base")?)?,
                child_care_per_dependent: num(get(cd, "per_dependent")?)?,
                child_care_max_dependents: num(get(cd, "max_dependents")?)?,
                child_care_phaseout_agi: num(get(cd, "phaseout_agi")?)?,
                child_care_phaseout_rate: num(get(cd, "phaseout_rate")?)?,
                exemption_amount: p.f("exemption_amount")?,
                table: Brackets::new(&table_rows),
                food_credit,
                aged_no_tax: if food_credit.is_none() {
                    let l = p.value("aged_no_tax_limit", &[])?;
                    Some((num(get(l, "alone")?)?, num(get(l, "with_dependents")?)?))
                } else {
                    None
                },
                law_1980: if (1981..=1984).contains(&year) { Some(Pre87Params::load(root, 1980)?) } else { None },
            })
        } else {
            None
        };

        let retirement = if p.in_effect("retirement_deduction_per_taxpayer")? {
            Some(Retirement {
                young: p.f("retirement_deduction_per_taxpayer")?,
                aged: opt("retirement_deduction_aged_per_taxpayer")?,
                aged_flat: opt("aged_deduction_per_taxpayer")?,
            })
        } else {
            None
        };
        let itemized_limit = if year > 1984 && year <= 2017 && p.in_effect("itemized_limit_threshold")? {
            Some(ItemizedLimit {
                threshold: p.f("itemized_limit_threshold")?,
                index: p.f("itemized_limit_index")?,
                status_factor: if p.in_effect("itemized_limit_status_factor")? {
                    Some(status_direct(p.value("itemized_limit_status_factor", &[])?)?)
                } else {
                    None
                },
            })
        } else {
            None
        };
        let schedule = if year > 1984 {
            let b = p.value("brackets", &[])?;
            Some(if b.is_mapping() {
                let amounts = p.value("exemption_amount", &[])?;
                Schedule::ByStatus {
                    single: Brackets::new(&params::brackets(get(b, "single")?)?),
                    married: Brackets::new(&params::brackets(get(b, "married")?)?),
                    exemption_single: num(get(amounts, "single")?)?,
                    exemption_married: num(get(amounts, "married")?)?,
                }
            } else {
                let index = p.f("bracket_index")?;
                let rows: Vec<Vec<f64>> = params::brackets(b)?.into_iter().map(|r| vec![r[0] * index, r[1]]).collect();
                Schedule::Scaled(Brackets::new(&rows))
            })
        } else {
            None
        };
        Ok(Self {
            year,
            early,
            long_term_gain_share: if year <= 1984 { p.f("long_term_gain_share")? } else { 0.0 },
            interest_dividend_exclusion: if year <= 1984 { opt("interest_dividend_exclusion")? } else { None },
            charity_nonitemizer_addback: opt("charity_nonitemizer_addback")?,
            retirement,
            itemized_limit,
            salt_cap: if year > 2017 { opt("salt_cap")? } else { None },
            capital_gain_deduction: if year > 1984 { opt("capital_gain_deduction")? } else { None },
            married_addback: if year > 1984 { opt("married_standard_deduction_addback")? } else { None },
            standard_increase: if year > 1984 && p.in_effect("federal_standard_deduction_increase_addback")? {
                Some(status_direct(p.value("federal_standard_deduction_increase_addback", &[])?)?)
            } else {
                None
            },
            dependent_exemption: if year > 1984 { opt("dependent_exemption")? } else { None },
            schedule,
            federal_exemption_amount: if (1987..=2017).contains(&year) {
                num(resolve_year(get(&fed_exemption, "amount")?, year)?)?
            } else {
                0.0
            },
            child_care_rate: num(get(&doc, "child_care_rate")?)?,
            child_care_expense: if year > 1984 { p.f("child_care_expense_per_child")? } else { 0.0 },
            child_care_cap: if year > 1984 { opt("child_care_cap_per_child")? } else { None },
            two_earner: if year > 1984 {
                match opt("two_earner_earnings_max")? {
                    Some(maximum) => Some(TwoEarner { maximum, rate: num(get(&doc, "two_earner_credit_rate")?)? }),
                    None => None,
                }
            } else {
                None
            },
            eitc_rate: if year > 1984 { opt("eitc_rate")? } else { None },
            dividend_adjustment: dividend_adjustment(root)?,
        })
    }
}

pub fn tax(i: &Inp, f: &Fed, p: &ScParams) -> f64 {
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
    let fed_agi = f.agi;
    let sc_ui = unemployment_total(i);
    let mut fed_itemizes = f.itemizes;

    // ---- AGI ----
    let mut agi;
    if y <= 1984 {
        agi = fed_agi + dividend_exclusion_addback(y, i, f, p.dividend_adjustment) + (sc_ui - f.taxable_unemployment).max(0.0);
        let cg = p.long_term_gain_share * i.ltcg + i.stcg;
        agi = agi - f.pre87.capgn.max(0.0) + cg.max(0.0);
        if let Some(exclusion) = p.interest_dividend_exclusion {
            agi -= (i.dividends + p.dividend_adjustment + i.intrec).max(0.0).min(exclusion * txp);
        }
    } else if y <= 1986 {
        // Federal taxable income is net of the zero bracket before 1987.
        fed_itemizes = f.pre87.itemizes;
        agi = (f.taxable_income - f.pre87.zbr).max(0.0);
    } else {
        agi = f.taxable_income;
    }
    if y == 2009 || y == 2020 {
        // Unemployment compensation is taxed in full.
        agi = agi - f.taxable_unemployment + sc_ui;
    }
    if let Some(amount) = p.charity_nonitemizer_addback {
        let limit = if y == 2020 { amount / sep } else { amount * txp };
        agi += if fed_itemizes { 0.0 } else { i.charity_cash.min(limit) };
    }
    // Retirement income deduction.
    let pensions = i.pensions;
    if let Some(r) = &p.retirement {
        let mut retded = pensions.max(0.0).min(r.young * txp);
        if let Some(aged_cap) = r.aged {
            let aged_ded = if is_joint && aged == 1.0 {
                pensions.max(0.0).min(aged_cap + r.young)
            } else {
                pensions.max(0.0).min(aged_cap * txp)
            };
            if aged > 0.0 {
                retded = aged_ded;
            }
        }
        if let Some(per_taxpayer) = r.aged_flat {
            if aged > 0.0 {
                retded = per_taxpayer * aged;
            }
        }
        agi -= retded;
    }
    if y >= 1984 {
        agi -= f.taxable_social_security;
    }

    if let Some(e) = &p.early {
        let ag = agi.max(0.0);
        let stded = (e.deduction_rate * ag * e.index).min(e.index * e.deduction_cap_per_taxpayer * txp);
        let gross = pre1987_gross_itemized(i, f);
        let mut xitded = (gross - f.ded).max(0.0);
        // TAXSIM's 1980-law rerun does not reach the state's copy of the federal tax.
        let fedtax = match &e.law_1980 {
            Some(law) => pre1987(i, f.ded, f.forced_itemize, law).fiitax,
            None => f.fiitax,
        };
        xitded = (xitded + fedtax.max(0.0).min(e.federal_tax_cap * txp)).max(0.0);
        if let Some(gas) = e.gasoline {
            xitded += gas * txp;
        }
        let contr = i.charity_cash;
        let clim = e.charity_limit_share * ag;
        if contr > clim {
            xitded = (xitded - (contr - clim)).max(0.0);
        }
        let mut chcr = i.childcare.min(depx.min(e.child_care_max_dependents) * e.child_care_per_dependent + e.child_care_base);
        chcr = (chcr - e.child_care_phaseout_rate * (agi - e.child_care_phaseout_agi).max(0.0)).max(0.0);
        let deduc = stded.max(xitded + chcr);
        let exemps = federal_exemption_count(y, i);
        let exemp = exemps * e.index * e.exemption_amount + if is_hoh { e.index * e.exemption_amount } else { 0.0 };
        let taxinc = (agi - deduc - exemp).max(0.0);
        let mut statax = e.table.tax(taxinc);
        if let Some(food) = e.food_credit {
            statax -= (depx + txp) * food;
        } else if let Some((alone, with_dependents)) = e.aged_no_tax {
            let limit = if depx > 0.0 { with_dependents } else { alone };
            // TAXSIM returns before the worksheet for these returns.
            if aged > 0.0 && agi <= limit {
                statax = 0.0;
            }
        }
        return statax * 1.0;
    }

    // ---- state income tax added back for federal itemizers ----
    let salt = f.ded;
    let itemized = f.itemized_deduction;
    let deducp = f.itemized_before_limit;
    let mut tx;
    if y <= 2017 {
        tx = salt;
        if let Some(l) = &p.itemized_limit {
            let phas92 = match &l.status_factor {
                None => l.threshold * l.index / sep,
                Some(factor) => l.index * l.threshold * factor[fs as usize],
            };
            let dedphs = deducp - itemized;
            tx = if fed_agi > phas92 && deducp > 0.0 { salt - dedphs * salt / deducp } else { salt };
            // The federal zero bracket is 0 for itemizers.
            tx = tx.min(itemized);
        }
    } else {
        let cap = match p.salt_cap {
            Some(cap) => cap / sep,
            None => f.salt_capped,
        };
        let room = (cap - i.proptax - i.otheritem).max(0.0);
        tx = salt.min(itemized.max(0.0).min(room));
    }
    if !fed_itemizes {
        tx = 0.0;
    }

    // ---- long-term capital gain deduction ----
    let dedgan = p.capital_gain_deduction.map_or(0.0, |rate| rate * i.ltcg.max(0.0));

    let mut add = 0.0;
    if let Some(amount) = p.married_addback {
        let zbr = f.standard_deduction;
        let added = (zbr.min((fed_agi - f.personal_exemptions).max(0.0)) - amount).max(0.0) / sep;
        add = if !fed_itemizes && (is_joint || is_sep) { added } else { 0.0 };
    }

    // ---- federal itemized and exemption phase-out adjustment (itemizers) ----
    let mut adjust = 0.0;
    if (1987..=2017).contains(&y) {
        let exemps = federal_exemption_count(y, i);
        let unreduced = deducp + exemps * p.federal_exemption_amount;
        let reduced = itemized + f.personal_exemptions;
        adjust = if fed_itemizes { (unreduced - reduced).max(0.0) } else { 0.0 };
    }
    if let Some(increase) = &p.standard_increase {
        add += (if fed_itemizes { 0.0 } else { increase[fs as usize] }) + f.senior_deduction;
    }
    let exemp = p.dependent_exemption.map_or(0.0, |amount| amount * (depx + i.dep6));
    let taxinc = (agi + tx - dedgan + add - exemp - adjust).max(0.0);

    // ---- tax ----
    let mut statax = match p.schedule.as_ref().expect("schedule from 1985") {
        Schedule::ByStatus { single, married, exemption_single, exemption_married } => {
            let single_income = (taxinc - exemption_single).max(0.0);
            let married_income = (taxinc - exemption_married / sep).max(0.0);
            if is_single { single.tax(single_income) } else { married.tax(married_income) }
        }
        Schedule::Scaled(table) => table.tax(taxinc),
    };

    // ---- credits (nonrefundable) ----
    let nccp = depx.min(2.0).floor();
    let expenses = (p.child_care_expense * nccp).min(i.childcare);
    let mut chcr = p.child_care_rate * expenses;
    if let Some(cap) = p.child_care_cap {
        chcr = (cap * nccp).min(chcr);
    }
    if is_sep {
        chcr = 0.0;
    }
    let twocrd = match &p.two_earner {
        Some(t) => {
            let busnes = (i.psemp + i.ssemp).max(0.0) - 0.5 * f.payroll.setax;
            let mut husb = i.pwages.max(0.0) + 0.5 * busnes;
            let mut wife = i.swages.max(0.0) + 0.5 * busnes;
            if y >= 2018 {
                husb += i.pbusinc + i.pprofinc;
                wife += i.sbusinc + i.sprofinc;
            }
            let earnls = husb.min(wife).max(0.0).min(t.maximum);
            if is_joint { t.rate * earnls } else { 0.0 }
        }
        None => 0.0,
    };
    let earncr = p.eitc_rate.map_or(0.0, |rate| rate * f.eitc);
    statax = (statax - chcr - twocrd - earncr).max(0.0);
    statax * 1.0
}
