//! Idaho income tax (port of `calculators.states.id`).

use std::path::Path;

use crate::common::Brackets;
use crate::federal::{Fed, Inp};
use crate::params::{self, get, load, num, status_direct, Fs, YearParams};
use crate::states::{dividend_adjustment, household_income, law_year_is_exact, unemployment_total};

/// The federal itemized-deduction limitation threshold used from 1991 to 2017.
enum Threshold {
    Flat(f64),
    ByStatus([f64; 4]),
}

struct ItemizedLimit {
    threshold: Threshold,
    index: f64,
}

struct PercentDeduction {
    rate: f64,
    single_floor: f64,
    single_ceiling: f64,
    married_floor: f64,
    married_ceiling: f64,
}

struct FixedDeduction {
    single: f64,
    head_of_household: f64,
    married_joint: f64,
    married_separate: f64,
}

struct BuildingFund {
    amount: f64,
    single_or_hoh: f64,
    married_joint: f64,
    married_separate: f64,
}

pub struct IdParams {
    year: i64,
    table: Brackets,
    inflation: f64,
    child_care_per_dependent: f64,
    itemized_limit: Option<ItemizedLimit>,
    percent: Option<PercentDeduction>,
    separate_addition: Option<f64>,
    fixed: Option<FixedDeduction>,
    property_tax_addition: Option<f64>,
    separate_aged_reduction: Option<f64>,
    /// Aged standard deduction `[first, step]` by status (1987).
    aged_standard: Option<[(f64, f64); 4]>,
    federal_aged_single: f64,
    federal_aged_joint: f64,
    dependent_minimum: f64,
    dependent_addition: f64,
    child_tax_credit: Option<f64>,
    grocery_credit: f64,
    grocery_bonus: Option<(f64, f64)>,
    grocery_aged: f64,
    grocery_excludes_separate: bool,
    building_fund: Option<BuildingFund>,
    building_fund_limit_aged: f64,
    dividend_adjustment: f64,
}

impl IdParams {
    pub fn load(root: &Path, year: i64) -> Result<Self, String> {
        let doc = load(root, "states/id/income_tax.yaml")?;
        law_year_is_exact(root, &doc, year)?;
        let inc = load(root, "national/income_tax.yaml")?;
        let p = YearParams::new(&doc, year);
        let opt = |key: &str| -> Result<Option<f64>, String> { Ok(if p.in_effect(key)? { Some(p.f(key)?) } else { None }) };

        let itemized_limit = if (1991..=2017).contains(&year) {
            let t = p.value("itemized_limitation_threshold", &[])?;
            Some(ItemizedLimit {
                threshold: if t.is_mapping() { Threshold::ByStatus(status_direct(t)?) } else { Threshold::Flat(num(t)?) },
                index: p.f("itemized_limitation_index")?,
            })
        } else {
            None
        };
        let percent = match p.opt("standard_deduction_percent")? {
            Some(sd) => Some(PercentDeduction {
                rate: num(get(sd, "rate")?)?,
                single_floor: num(get(get(sd, "single")?, "floor")?)?,
                single_ceiling: num(get(get(sd, "single")?, "ceiling")?)?,
                married_floor: num(get(get(sd, "married")?, "floor")?)?,
                married_ceiling: num(get(get(sd, "married")?, "ceiling")?)?,
            }),
            None => None,
        };
        let fixed = if percent.is_none() && year >= 1993 {
            Some(FixedDeduction {
                single: p.num_at("standard_deduction", &["single"])?,
                head_of_household: p.num_at("standard_deduction", &["head_of_household"])?,
                married_joint: p.num_at("standard_deduction", &["married_joint"])?,
                married_separate: if year >= 1999 { p.num_at("standard_deduction", &["married_separate"])? } else { 0.0 },
            })
        } else {
            None
        };
        let aged_standard = match p.opt("aged_standard_deduction")? {
            Some(t) => {
                let pair = |key: &str| -> Result<(f64, f64), String> {
                    let v = get(t, key)?.as_sequence().ok_or("expected [first, step]")?;
                    Ok((num(&v[0])?, num(&v[1])?))
                };
                Some([pair("single")?, pair("married_joint")?, pair("married_separate")?, pair("head_of_household")?])
            }
            None => None,
        };
        let federal_aged = YearParams::new(get(&inc, "aged_standard_deduction")?, year);
        let building_fund = match p.opt("building_fund_tax")? {
            Some(b) => {
                let limits = get(b, "limit")?;
                Some(BuildingFund {
                    amount: num(get(b, "amount")?)?,
                    single_or_hoh: num(get(limits, "single_or_hoh")?)?,
                    married_joint: num(get(limits, "married_joint")?)?,
                    married_separate: num(get(limits, "married_separate")?)?,
                })
            }
            None => None,
        };
        let bonus = match p.opt("grocery_credit_low_income_bonus")? {
            Some(b) => Some((num(get(b, "amount")?)?, num(get(b, "taxable_income_limit")?)?)),
            None => None,
        };
        Ok(Self {
            year,
            table: Brackets::new(&params::brackets(p.value("brackets", &[])?)?),
            inflation: p.f("bracket_inflation")?,
            child_care_per_dependent: if year >= 1978 { p.f("child_care_deduction_per_dependent")? } else { 0.0 },
            itemized_limit,
            percent,
            separate_addition: if year > 1986 && year <= 1992 { opt("standard_deduction_separate_addition")? } else { None },
            fixed,
            property_tax_addition: if year >= 1993 { opt("standard_deduction_property_tax_addition")? } else { None },
            separate_aged_reduction: opt("separate_aged_reduction")?,
            aged_standard,
            federal_aged_single: if year >= 1993 && aged_standard.is_none() { federal_aged.num_at("single", &[])? } else { 0.0 },
            federal_aged_joint: if year >= 1993 && aged_standard.is_none() { federal_aged.num_at("married_joint", &[])? } else { 0.0 },
            dependent_minimum: if year >= 1987 { p.f("dependent_standard_deduction_minimum")? } else { 0.0 },
            dependent_addition: num(get(&doc, "dependent_standard_deduction_earned_addition")?)?,
            child_tax_credit: opt("child_tax_credit_per_child")?,
            grocery_credit: p.f("grocery_credit")?,
            grocery_bonus: bonus,
            grocery_aged: p.f("grocery_credit_aged")?,
            grocery_excludes_separate: p.value("grocery_credit_excludes_separate_filers", &[])?.as_bool().ok_or("expected a boolean")?,
            building_fund,
            building_fund_limit_aged: num(get(&doc, "building_fund_limit_aged")?)?,
            dividend_adjustment: dividend_adjustment(root)?,
        })
    }
}

pub fn tax(i: &Inp, f: &Fed, p: &IdParams) -> f64 {
    let y = p.year;
    let fs = i.fs();
    let sep = i.sepret();
    let taxpayers = i.taxpayer_count();
    let aged = i.aged_count();
    let salt = f.ded;
    let household = household_income(i, p.dividend_adjustment);

    // ---- AGI ---- 1977 uses federal AGI unadjusted.
    let mut agi = f.agi;
    if y >= 1978 {
        let cap = p.child_care_per_dependent * i.depx.min(2.0);
        agi -= i.childcare.min(f.earned_income).min(cap);
        if y >= 1984 {
            agi -= f.taxable_social_security;
        }
    }
    if y == 2020 {
        agi = agi + unemployment_total(i) - f.taxable_unemployment;
    }

    // ---- itemized deduction ---- Federal itemized deductions less the state tax share.
    let (raw_itemized, itemized_deduction) = if y <= 1986 {
        let raw = i.proptax + i.otheritem + i.mortgage + salt;
        (raw, raw)
    } else {
        (f.salt_capped + i.mortgage, f.itemized_deduction)
    };
    let mut xitded = (raw_itemized - salt).max(0.0);
    if let Some(l) = &p.itemized_limit {
        let phas92 = match &l.threshold {
            Threshold::ByStatus(by_status) => by_status[fs as usize] * l.index,
            Threshold::Flat(v) => v * l.index / sep,
        };
        let salt_share = salt / raw_itemized.max(1e-9);
        if agi > phas92 && raw_itemized > 0.0 {
            xitded = (itemized_deduction * (1.0 - salt_share)).max(0.0);
        }
    }

    // ---- standard deduction ----
    let mut stded = if let Some(d) = &p.percent {
        let value = d.rate * agi;
        if fs == Fs::Single || fs == Fs::Hoh {
            value.max(d.single_floor).min(d.single_ceiling)
        } else {
            value.max(d.married_floor).min(d.married_ceiling)
        }
    } else if y <= 1986 {
        // The federal zero bracket amount.
        f.pre87.zbr
    } else if y <= 1992 {
        // The federal standard deduction, 0 when the federal return itemizes.
        let mut s = if f.itemizes { 0.0 } else { f.standard_deduction };
        if let (Some(addition), true) = (p.separate_addition, fs == Fs::Separate) {
            s += addition;
        }
        s
    } else {
        let d = p.fixed.as_ref().expect("fixed standard deduction from 1993");
        let mut s = if y <= 1998 {
            match fs {
                Fs::Single => d.single,
                Fs::Hoh => d.head_of_household,
                _ => d.married_joint / sep,
            }
        } else {
            match fs {
                Fs::Single => d.single,
                Fs::Hoh => d.head_of_household,
                Fs::Joint => d.married_joint,
                Fs::Separate => d.married_separate / sep,
            }
        };
        if let Some(addition) = p.property_tax_addition {
            s += (addition * taxpayers).min(i.proptax);
        }
        s
    };
    let xitded_detail = xitded;
    let _ = xitded_detail;
    if y == 1999 && !f.forced_itemize {
        xitded = 0.0;
    }

    // Taxpayers 65 or older, and dependent filers.
    if let (Some(reduction), true) = (p.separate_aged_reduction, fs == Fs::Separate) {
        stded -= reduction * aged;
    }
    if let Some(table) = &p.aged_standard {
        let (first, step) = table[fs as usize];
        if aged >= 1.0 {
            stded = first + step * (aged - 1.0);
        }
    } else if y >= 1993 {
        let single_like = fs == Fs::Single || fs == Fs::Hoh;
        stded += (if single_like { p.federal_aged_single } else { p.federal_aged_joint }) * aged;
    }
    if y >= 1987 && i.is_dependent_filer() {
        let limit = p.dependent_minimum.max(f.earned_income + p.dependent_addition);
        stded = stded.min(limit);
    }
    let deduc = xitded.max(stded);

    // ---- exemption ---- The federal exemption amount.
    let exemp = if y <= 1986 { f.pre87.amex } else { f.personal_exemptions };
    let mut taxinc = (agi - deduc - exemp).max(0.0);
    if y >= 2018 {
        taxinc = (taxinc - f.qbi_deduction).max(0.0);
    }
    if y >= 2025 {
        taxinc = (taxinc - f.senior_deduction).max(0.0);
    }

    // ---- tax ----
    let txp = if fs == Fs::Joint || fs == Fs::Hoh { 2.0 } else { 1.0 };
    let tinc = taxinc / txp;
    let stat = p.table.tax(tinc / p.inflation) * p.inflation;
    let mut statax = stat * txp;

    // ---- credits ----
    let ctcred = match p.child_tax_credit {
        Some(per_child) => (per_child * i.dep17).min(statax),
        None => 0.0,
    };
    let depx_plus_texp = taxpayers + i.depx;
    let mut grcred = p.grocery_credit * depx_plus_texp;
    if let Some((amount, limit)) = p.grocery_bonus {
        grcred += if taxinc <= limit { amount * depx_plus_texp } else { 0.0 };
    }
    grcred += p.grocery_aged * aged;
    if p.grocery_excludes_separate && fs == Fs::Separate && aged < 1.0 {
        grcred = 0.0;
    }
    if i.is_dependent_filer() {
        grcred = 0.0;
    }
    statax -= ctcred + grcred;

    // ---- permanent building fund tax ----
    if let Some(b) = &p.building_fund {
        let mut limit = match fs {
            Fs::Single | Fs::Hoh => b.single_or_hoh,
            Fs::Joint => b.married_joint,
            Fs::Separate => b.married_separate,
        };
        limit += if fs != Fs::Separate { p.building_fund_limit_aged * aged } else { 0.0 };
        if household > limit {
            statax += b.amount;
        }
    }
    statax * 1.0
}
