//! Oklahoma income tax (port of `calculators.states.ok`).

use std::path::Path;

use crate::common::Brackets;
use crate::federal::{read_eitc, EitcRow, Fed, Inp};
use crate::params::{self, get, load, num, status_direct, Fs, YearParams};
use crate::states::{
    dividend_exclusion_addback, dividend_adjustment, household_income, law_year_is_exact, pre1987_gross_itemized, unemployment_total,
};

struct Tables {
    single: Brackets,
    married: Brackets,
    hoh: Option<Brackets>,
}

enum PensionLimit {
    None,
    ByHousehold { one: f64, two: f64 },
    PerAged(f64),
}

enum Standard {
    Share { rate: f64, minimum: f64, maximum: f64 },
    Flat { single: f64, married: f64 },
    Per { hoh: f64, per_taxpayer: f64 },
}

struct FedTaxCap {
    threshold: f64,
    rate: f64,
    cap: f64,
}

enum SalesLimit {
    None,
    Flat(f64),
    Split { alone: f64, with_dependents: f64 },
}

struct LawEitc {
    rows: [[Option<EitcRow>; 4]; 4],
    dylim: f64,
    minimum_age: Option<f64>,
    aged_age: f64,
    year: i64,
}

pub struct OkParams {
    year: i64,
    tables: Tables,
    federal_tables: Option<Tables>,
    federal_tax_cap: Option<FedTaxCap>,
    pension_amount: f64,
    pension_limit: PensionLimit,
    exemption_amount: f64,
    aged_exemption_limit: Option<[f64; 4]>,
    standard: Standard,
    salt_cap: Option<f64>,
    itemized_cap: Option<f64>,
    child_care_rate: f64,
    child_tax_rate: f64,
    child_credit_agi_limit: Option<f64>,
    eitc_rate: Option<f64>,
    eitc_law: Option<LawEitc>,
    eitc_refundable: bool,
    property_limit: f64,
    property_share: f64,
    property_max: f64,
    sales_credit: f64,
    sales_limit: SalesLimit,
    dividend_adjustment: f64,
}

fn read_tables(v: &serde_yaml::Value) -> Result<Tables, String> {
    Ok(Tables {
        single: Brackets::new(&params::brackets(get(v, "single")?)?),
        married: Brackets::new(&params::brackets(get(v, "married")?)?),
        hoh: v.get("head_of_household").map(|h| params::brackets(h).map(|r| Brackets::new(&r))).transpose()?,
    })
}

impl OkParams {
    pub fn load(root: &Path, year: i64) -> Result<Self, String> {
        let doc = load(root, "states/ok/income_tax.yaml")?;
        law_year_is_exact(root, &doc, year)?;
        let eitc_misc = load(root, "national/eitc_misc.yaml")?;
        let p = YearParams::new(&doc, year);
        let opt = |key: &str| -> Result<Option<f64>, String> { Ok(if p.in_effect(key)? { Some(p.f(key)?) } else { None }) };

        let pension_limit = if p.in_effect("pension_exclusion_agi_limit")? {
            let l = p.value("pension_exclusion_agi_limit", &[])?;
            if l.is_mapping() {
                PensionLimit::ByHousehold { one: num(get(l, "one")?)?, two: num(get(l, "two")?)? }
            } else {
                PensionLimit::PerAged(num(l)?)
            }
        } else {
            PensionLimit::None
        };
        let standard = if p.in_effect("standard_deduction_share")? {
            let c = p.value("standard_deduction_share", &[])?;
            Standard::Share { rate: num(get(c, "rate")?)?, minimum: num(get(c, "minimum")?)?, maximum: num(get(c, "maximum")?)? }
        } else if p.in_effect("standard_deduction_flat")? {
            let a = p.value("standard_deduction_flat", &[])?;
            Standard::Flat { single: num(get(a, "single")?)?, married: num(get(a, "married")?)? }
        } else {
            Standard::Per { hoh: p.f("standard_deduction_head_of_household")?, per_taxpayer: p.f("standard_deduction_per_taxpayer")? }
        };
        let federal_tax_cap = match p.opt("federal_tax_deduction_cap")? {
            Some(c) => Some(FedTaxCap { threshold: num(get(c, "threshold")?)?, rate: num(get(c, "rate")?)?, cap: num(get(c, "cap")?)? }),
            None => None,
        };
        let eitc_law = match opt("eitc_federal_law_year")? {
            Some(law) => {
                let law = law as i64;
                Some(LawEitc {
                    rows: read_eitc(root, law)?,
                    dylim: num(params::resolve_year(get(&eitc_misc, "dylim")?, law)?)?,
                    minimum_age: if law >= 1994 {
                        Some(num(params::resolve_year(get(&eitc_misc, "childless_minimum_age")?, law)?)?)
                    } else {
                        None
                    },
                    aged_age: num(get(&eitc_misc, "childless_aged_age")?)?,
                    year: law,
                })
            }
            None => None,
        };
        let sales_limit = if p.in_effect("sales_tax_credit_income_limit")? {
            let l = p.value("sales_tax_credit_income_limit", &[])?;
            if l.is_mapping() {
                SalesLimit::Split { alone: num(get(l, "alone")?)?, with_dependents: num(get(l, "with_dependents")?)? }
            } else {
                SalesLimit::Flat(num(l)?)
            }
        } else {
            SalesLimit::None
        };
        Ok(Self {
            year,
            tables: read_tables(p.value("brackets", &[])?)?,
            federal_tables: if p.in_effect("federal_tax_brackets")? { Some(read_tables(p.value("federal_tax_brackets", &[])?)?) } else { None },
            federal_tax_cap,
            pension_amount: p.f("pension_exclusion")?,
            pension_limit,
            exemption_amount: p.f("exemption_amount")?,
            aged_exemption_limit: if p.in_effect("aged_exemption_agi_limit")? {
                Some(status_direct(p.value("aged_exemption_agi_limit", &[])?)?)
            } else {
                None
            },
            standard,
            salt_cap: opt("salt_cap")?,
            itemized_cap: opt("itemized_cap")?,
            child_care_rate: num(get(&doc, "child_care_credit_rate")?)?,
            child_tax_rate: num(get(&doc, "child_tax_credit_rate")?)?,
            child_credit_agi_limit: opt("child_credit_agi_limit")?,
            eitc_rate: opt("eitc_rate")?,
            eitc_law,
            eitc_refundable: if p.in_effect("eitc_refundable")? { p.value("eitc_refundable", &[])?.as_bool().ok_or("expected a boolean")? } else { true },
            property_limit: p.f("property_credit_income_limit")?,
            property_share: num(get(&doc, "property_credit_income_share")?)?,
            property_max: num(get(&doc, "property_credit_max")?)?,
            sales_credit: num(get(&doc, "sales_tax_credit")?)?,
            sales_limit,
            dividend_adjustment: dividend_adjustment(root)?,
        })
    }
}

fn by_table(t: &Tables, income: f64, single_like: bool) -> f64 {
    if single_like { t.single.tax(income) } else { t.married.tax(income) }
}

/// The federal EITC under another law year (the credit is decoupled from federal law).
fn law_eitc(i: &Inp, f: &Fed, l: &LawEitc) -> f64 {
    let fs = i.fs();
    let nkids = i.dep18.max(0.0).min(3.0);
    let childless = nkids == 0.0;
    let dependent = i.is_dependent_filer();
    let filer_eligible = !dependent && !(l.year != 2021 && childless && i.aged_count() >= i.taxpayer_count());
    let older = i.page.max(i.sage);
    let younger = i.page.min(i.sage);
    let age_eligible = match l.minimum_age {
        None => true,
        Some(min_age) => {
            let too_young = |age: f64| age > 0.0 && age < min_age;
            let ineligible = if l.year == 2021 {
                too_young(older) || too_young(younger)
            } else {
                too_young(older) || (older > l.aged_age && too_young(younger))
            };
            !(childless && ineligible)
        }
    };
    if !(filer_eligible && age_eligible) {
        return 0.0;
    }
    let disqy = (i.stcg + i.ltcg).max(0.0) + i.dividends + i.intrec + i.otherprop.max(0.0);
    let credit = match l.rows[fs as usize][nkids as usize] {
        Some(r) => {
            let phase_in = (f.earned_income * r.rate_in).max(0.0).min(r.max_credit);
            let reduction = ((f.earned_income.max(f.agi) - r.phaseout_start) * r.rate_out).max(0.0);
            phase_in.min((r.max_credit - reduction).max(0.0)).max(0.0)
        }
        None => 0.0,
    };
    (credit - (disqy - l.dylim).max(0.0)).max(0.0)
}

pub fn tax(i: &Inp, f: &Fed, p: &OkParams) -> f64 {
    let y = p.year;
    let fs = i.fs();
    let is_joint = fs == Fs::Joint;
    let is_sep = fs == Fs::Separate;
    let is_hoh = fs == Fs::Hoh;
    let single_like = fs == Fs::Single || is_sep;
    let sep = i.sepret();
    let txp = i.taxpayer_count();
    let aged = i.aged_count();
    let dependent_filer = i.is_dependent_filer();
    let depx = i.depx;
    let fed_agi = f.agi;
    let hh = household_income(i, p.dividend_adjustment);
    let ok_ui = unemployment_total(i);

    // ---- AGI ----
    let mut agi = fed_agi;
    if y <= 1986 {
        agi += dividend_exclusion_addback(y, i, f, p.dividend_adjustment);
    }
    if y >= 1985 {
        agi -= f.taxable_social_security;
    }
    // Retirement income exclusion.
    let pensions = i.pensions;
    let amount = p.pension_amount;
    let penmax = match &p.pension_limit {
        PensionLimit::None => amount * txp,
        PensionLimit::ByHousehold { one, two } => {
            let under = if txp == 1.0 { agi <= *one } else if txp == 2.0 { agi <= *two } else { false };
            if under { amount * aged } else { 0.0 }
        }
        PensionLimit::PerAged(limit) => if agi <= limit * aged { amount * aged } else { 0.0 },
    };
    if pensions > 0.0 {
        agi = (agi - penmax.min(pensions)).max(0.0);
    }
    if y == 2009 {
        // Unemployment compensation is taxed in full.
        agi = agi - f.taxable_unemployment + ok_ui;
    }

    let mut exemp = (txp + depx) * p.exemption_amount;
    exemp += match &p.aged_exemption_limit {
        None => aged * p.exemption_amount,
        Some(limit) => if fed_agi <= limit[fs as usize] { aged * p.exemption_amount } else { 0.0 },
    };
    if dependent_filer {
        exemp = 0.0;
    }

    // ---- federal values ----
    let (fed_itemizes, xitded_federal, taxbc) = if y <= 1986 {
        let itemizes = f.pre87.itemizes;
        (itemizes, if itemizes { pre1987_gross_itemized(i, f) } else { 0.0 }, f.pre87.taxbc)
    } else {
        (f.itemizes, if f.itemizes { f.itemized_deduction } else { 0.0 }, f.regular_tax)
    };
    let fed_ccc = f.federal_chcr;

    // ---- standard deduction ----
    let stded = match &p.standard {
        Standard::Share { rate, minimum, maximum } => (rate * agi).max(minimum / sep).min(maximum / sep),
        Standard::Flat { single, married } => if single_like { *single } else { *married },
        Standard::Per { hoh, per_taxpayer } => if is_hoh { *hoh } else { per_taxpayer * txp },
    };

    let mut perc2 = 1.0;
    let mut statax;
    if y <= 2005 {
        // ---- federal tax deducted ----
        let fedtax = match &p.federal_tax_cap {
            Some(c) => {
                let five = (f.fiitax - c.threshold).max(0.0);
                (if five > 0.0 { c.threshold + c.rate * five } else { taxbc.max(0.0) }).min(c.cap)
            }
            None => f.fiitax.max(0.0),
        };
        let deduc = xitded_federal.max(stded);
        let taxya = (agi - (deduc + exemp)).max(0.0);
        perc2 = if fed_agi > 0.0 { (agi / fed_agi).max(0.0).min(1.0) } else { 1.0 };
        let taxyb = (taxya - perc2 * fedtax).max(0.0);
        statax = match &p.federal_tables {
            None => {
                if is_joint {
                    p.tables.married.tax(taxyb)
                } else if is_hoh {
                    p.tables.hoh.as_ref().map_or(0.0, |h| h.tax(taxyb))
                } else {
                    p.tables.single.tax(taxyb)
                }
            }
            Some(ft) => {
                let stax1 = by_table(&p.tables, taxya, single_like);
                let stax2 = by_table(ft, taxyb, single_like);
                stax1.min(stax2)
            }
        };
    } else {
        let mut xitded = f.itemized_deduction;
        let deduc;
        if y <= 2015 {
            deduc = stded.max(xitded_federal);
        } else {
            let deducp = f.itemized_before_limit;
            let salt = f.ded;
            if y <= 2017 {
                // State income tax added back, net of its share of the federal limit.
                if salt > 0.0 {
                    xitded = (xitded - salt * xitded / deducp).max(0.0);
                }
            } else {
                let other = i.proptax + i.otheritem;
                let sttax = (p.salt_cap.unwrap_or(0.0) / sep).min(other + salt);
                let stt = salt.min(sttax - other);
                let charity = i.charity_cash;
                let cap = p.itemized_cap.unwrap_or(0.0);
                xitded = if deducp - stt - charity > cap { cap + charity } else { deducp - stt };
            }
            deduc = if fed_itemizes && deducp > 0.0 { xitded } else { stded };
        }
        let taxinc = if fed_agi > 0.0 { (agi - deduc - exemp).max(0.0) } else { 0.0 };
        statax = by_table(&p.tables, taxinc, single_like);
    }

    // ---- credits ----
    let chcr = match p.child_credit_agi_limit {
        None => p.child_care_rate * perc2 * fed_ccc.min(taxbc.max(0.0)),
        Some(limit) => {
            let perc = (agi / fed_agi).min(1.0);
            let chcare = fed_ccc.min(taxbc.max(0.0));
            let room = (taxbc - fed_ccc).max(0.0) + f.actc;
            let federal_credit = if y == 2021 { f.odc + f.actc } else { f.odc };
            let chtax = if y == 2021 { federal_credit.min(room) } else { (federal_credit + f.actc).min(room) };
            let credit = (p.child_care_rate * chcare).max(p.child_tax_rate * chtax) * perc;
            let eligible = fed_agi <= limit && fed_agi > 0.0 && agi >= 0.0;
            if eligible { credit } else { 0.0 }
        }
    };
    let earncr = match p.eitc_rate {
        Some(rate) => {
            let eitc_base = match &p.eitc_law {
                Some(l) => law_eitc(i, f, l),
                None => f.eitc,
            };
            if fed_agi > 0.0 { rate * eitc_base * agi / fed_agi } else { 0.0 }
        }
        None => 0.0,
    };

    // Property tax credit for taxpayers 65 or older.
    let hhy_prop = hh + f.eitc;
    let pcred = if aged > 0.0 && hhy_prop < p.property_limit {
        (i.proptax - p.property_share * hhy_prop).max(0.0).min(p.property_max)
    } else {
        0.0
    };
    let mut scred = match &p.sales_limit {
        SalesLimit::None => 0.0,
        SalesLimit::Flat(limit) => if hh < *limit { p.sales_credit * (txp + depx) } else { 0.0 },
        SalesLimit::Split { alone, with_dependents } => {
            let hhy = hh + f.eitc;
            let with_dep = depx > 0.0 || aged > 0.0;
            let limit = if with_dep { *with_dependents } else { *alone };
            let amount = if with_dep { p.sales_credit * (txp + depx) } else { p.sales_credit * txp };
            if hhy <= limit { amount } else { 0.0 }
        }
    };
    if dependent_filer {
        scred = 0.0;
    }
    statax = if p.eitc_refundable {
        (statax - chcr).max(0.0) - pcred - scred - earncr
    } else {
        (statax - chcr - earncr).max(0.0) - pcred - scred
    };
    statax * 1.0
}
