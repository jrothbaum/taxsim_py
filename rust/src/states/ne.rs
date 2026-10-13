//! Nebraska income tax (port of `calculators.states.ne`).

use std::path::Path;

use crate::common::Brackets;
use crate::federal::{Fed, Inp};
use crate::params::{self, get, load, num, status_direct, Fs, YearParams};
use crate::states::{federal_exemption_count, law_year_is_exact};

struct Additional {
    rates: [f64; 3],
    /// Bounds by table (single, joint, head of household), already inflated.
    bounds: [[f64; 4]; 3],
    min_taxable_income: f64,
    agi_share: f64,
}

struct Std2018 {
    single: f64,
    married: f64,
    hoh: f64,
    aged_single: f64,
    aged_married: f64,
    aged_hoh: f64,
    salt_cap: f64,
}

struct Phaseout {
    rate: f64,
    override_married: Option<f64>,
    override_single: Option<f64>,
}

struct PersonalCreditPhaseout {
    threshold: [f64; 4],
    amount: f64,
    step: f64,
}

struct ChildCareRefundable {
    agi_limit: f64,
    full_share_agi: f64,
    share_step: f64,
}

struct ChildTaxCredit {
    amount: Brackets,
    poverty_first: f64,
    poverty_additional: f64,
}

/// Tables by filing status, indexed by `Fs`; separate returns use the joint table from 1993.
struct Tables {
    by_status: [Brackets; 4],
}

pub struct NeParams {
    year: i64,
    federal_tax_share: Option<f64>,
    tables: Option<Tables>,
    zero_bracket: Option<[f64; 4]>,
    ss_limit_joint: f64,
    ss_limit_single: f64,
    ss_above: f64,
    threshold_base: Option<f64>,
    inflation: f64,
    status_multiplier: [f64; 4],
    std2018: Option<Std2018>,
    phaseout: Option<Phaseout>,
    property_cap: Option<f64>,
    exemption: f64,
    additional: Option<Additional>,
    personal_credit: f64,
    personal_phaseout: Option<PersonalCreditPhaseout>,
    elderly_share: f64,
    child_care_share: f64,
    child_care_refundable: Option<ChildCareRefundable>,
    minimum_tax_share: f64,
    eitc_match_rate: f64,
    child_tax_credit: Option<ChildTaxCredit>,
}

impl NeParams {
    pub fn load(root: &Path, year: i64) -> Result<Self, String> {
        let doc = load(root, "states/ne/income_tax.yaml")?;
        law_year_is_exact(root, &doc, year)?;
        let p = YearParams::new(&doc, year);
        let opt = |key: &str| -> Result<Option<f64>, String> { Ok(if p.in_effect(key)? { Some(p.f(key)?) } else { None }) };

        let federal_tax_share = if year <= 1986 { Some(p.f("federal_tax_share")?) } else { None };
        let inflation = if year >= 1993 { p.f("bracket_inflation")? } else { 1.0 };
        let mut tables = None;
        let mut zero_bracket = None;
        if year > 1986 {
            let t = p.value("brackets", &[])?;
            let read = |key: &str| -> Result<Option<Vec<Vec<f64>>>, String> { t.get(key).map(params::brackets).transpose() };
            let scale = |rows: Vec<Vec<f64>>| -> Vec<Vec<f64>> { rows.into_iter().map(|r| vec![r[0] * inflation, r[1]]).collect() };
            let single = read("single")?.ok_or("single table")?;
            let joint = read("married_joint")?.ok_or("joint table")?;
            let hoh = read("head_of_household")?.ok_or("hoh table")?;
            let separate = read("married_separate")?;
            let build = |rows: Vec<Vec<f64>>| if year <= 1992 { Brackets::new(&rows) } else { Brackets::new(&scale(rows)) };
            // Order of `Fs`: single, joint, separate, head of household.
            let separate_table = match (year <= 1992, separate) {
                (true, Some(s)) => build(s),
                _ => build(joint.clone()),
            };
            tables = Some(Tables { by_status: [build(single), build(joint), separate_table, build(hoh)] });
            if year <= 1992 && p.in_effect("zero_bracket")? {
                zero_bracket = Some(status_direct(p.value("zero_bracket", &[])?)?);
            }
        }
        let threshold_base = if year <= 2017 { opt("itemized_limit_threshold_base")? } else { None };
        let inflation_itemized = if year <= 2017 && year >= 1992 { p.f("itemized_limit_inflation")? } else { 1.0 };
        let std2018 = if year > 2017 {
            let std = get(&doc, "standard_deduction")?;
            let aged = get(&doc, "standard_deduction_aged_addition")?;
            let at = |table: &serde_yaml::Value, key: &str| -> Result<f64, String> {
                num(params::resolve_year(get(table, key)?, year)?)
            };
            Some(Std2018 {
                single: at(std, "single")?,
                married: at(std, "married")?,
                hoh: at(std, "head_of_household")?,
                aged_single: at(aged, "single")?,
                aged_married: at(aged, "married")?,
                aged_hoh: at(aged, "head_of_household")?,
                salt_cap: p.f("salt_cap")?,
            })
        } else {
            None
        };
        let phaseout = if p.in_effect("standard_deduction_phaseout_rate")? {
            Some(Phaseout {
                rate: p.f("standard_deduction_phaseout_rate")?,
                override_married: opt("standard_deduction_override_married")?,
                override_single: opt("standard_deduction_override_single")?,
            })
        } else {
            None
        };
        let additional = if year > 1992 && p.in_effect("additional_tax_rates")? {
            let r = p.value("additional_tax_rates", &[])?.as_sequence().ok_or("rates must be a list")?;
            let bound_factor = if year >= 2013 { p.f("itemized_limit_inflation")? } else { 1.0 };
            let bounds_for = |table: &str| -> Result<[f64; 4], String> {
                let b = p.value("additional_tax_bounds", &[table])?.as_sequence().ok_or("bounds must be a list")?;
                Ok([num(&b[0])? * bound_factor, num(&b[1])? * bound_factor, num(&b[2])? * bound_factor, num(&b[3])? * bound_factor])
            };
            Some(Additional {
                rates: [num(&r[0])?, num(&r[1])?, num(&r[2])?],
                bounds: [bounds_for("single")?, bounds_for("married_joint")?, bounds_for("head_of_household")?],
                min_taxable_income: p.f("additional_tax_min_taxable_income")?,
                agi_share: num(get(&doc, "additional_tax_agi_share")?)?,
            })
        } else {
            None
        };
        let personal_phaseout = if year > 1992 && p.in_effect("personal_credit_phaseout_threshold")? {
            Some(PersonalCreditPhaseout {
                threshold: status_direct(p.value("personal_credit_phaseout_threshold", &[])?)?,
                amount: p.f("personal_credit_phaseout_amount")?,
                step: p.f("personal_credit_phaseout_step")?,
            })
        } else {
            None
        };
        let child_care_refundable = match p.opt("child_care_refundable")? {
            Some(r) => Some(ChildCareRefundable {
                agi_limit: num(get(r, "agi_limit")?)?,
                full_share_agi: num(get(r, "full_share_agi")?)?,
                share_step: num(get(r, "share_step")?)?,
            }),
            None => None,
        };
        let child_tax_credit = match p.opt("child_tax_credit")? {
            Some(c) => Some(ChildTaxCredit {
                amount: Brackets::new(&params::brackets(get(c, "amount")?)?),
                poverty_first: num(get(c, "poverty_first_person")?)?,
                poverty_additional: num(get(c, "poverty_additional_person")?)?,
            }),
            None => None,
        };
        Ok(Self {
            year,
            federal_tax_share,
            tables,
            zero_bracket,
            ss_limit_joint: if year >= 2015 { p.num_at("social_security_exclusion_agi_limit", &["married_joint"])? } else { 0.0 },
            ss_limit_single: if year >= 2015 { p.num_at("social_security_exclusion_agi_limit", &["single"])? } else { 0.0 },
            ss_above: if year >= 2015 { p.f("social_security_exclusion_share_above_limit")? } else { 0.0 },
            threshold_base,
            inflation: inflation_itemized,
            status_multiplier: if (2013..=2017).contains(&year) {
                status_direct(get(&doc, "itemized_limit_status_multiplier")?)?
            } else {
                [1.0; 4]
            },
            std2018,
            phaseout,
            property_cap: if year > 1986 { opt("standard_deduction_property_tax_cap")? } else { None },
            exemption: if year > 1986 && year <= 1992 { p.f("exemption")? } else { 0.0 },
            additional,
            personal_credit: p.f("personal_credit")?,
            personal_phaseout,
            elderly_share: if year >= 1987 { p.f("elderly_credit_share")? } else { 0.0 },
            child_care_share: p.f("child_care_credit_share")?,
            child_care_refundable,
            minimum_tax_share: p.f("minimum_tax_share")?,
            eitc_match_rate: p.f("eitc_match_rate")?,
            child_tax_credit,
        })
    }
}

/// High-income additional tax on federal AGI, and its amount at the top bound.
fn additional_tax(dagi: f64, bounds: &[f64; 4], rates: &[f64; 3]) -> (f64, f64) {
    let [s1, s2, s3, s4] = *bounds;
    let [r2, r3, r4] = *rates;
    let schedule = Brackets::new(&[vec![0.0, 0.0], vec![s1, r2], vec![s2, r3], vec![s3, r4]]);
    let capped = (s4 - s3) * r4 + (s3 - s2) * r3 + (s2 - s1) * r2;
    (schedule.tax(dagi), capped)
}

pub fn tax(i: &Inp, f: &Fed, p: &NeParams) -> f64 {
    let y = p.year;
    let fs = i.fs();
    let is_single = fs == Fs::Single;
    let is_joint = fs == Fs::Joint;
    let is_sep = fs == Fs::Separate;
    let sep = i.sepret();
    let txp = i.taxpayer_count();
    let exemps = txp + i.depx;
    let fed_agi = f.agi;
    let mut ne_agi = fed_agi;
    if y >= 2015 {
        // Taxable Social Security is subtracted in full up to an AGI limit.
        let limit = if is_joint { p.ss_limit_joint } else { p.ss_limit_single };
        ne_agi -= if fed_agi <= limit { f.taxable_social_security } else { p.ss_above * f.taxable_social_security };
    }
    let salt_ded = f.ded;
    let itemizes = f.itemizes;

    let phas92 = match p.threshold_base {
        Some(base) if y <= 2012 => Some(base * p.inflation / sep),
        Some(base) => Some(base * p.inflation * p.status_multiplier[fs as usize]),
        None => None,
    };

    let (taxbc, almtax, fed_ccc) = if y <= 1986 { (f.pre87.taxbc, f.pre87.almtax, f.federal_chcr) } else { (f.regular_tax, f.amt, f.federal_chcr) };
    let mut statax;
    if y <= 1986 {
        statax = p.federal_tax_share.unwrap_or(0.0) * f.pre87.pretax.max(0.0);
    } else {
        let tables = p.tables.as_ref().expect("brackets from 1987");
        // ---- deductions ----
        let fed_itemized = if itemizes { f.itemized_deduction } else { 0.0 };
        let fed_std = if itemizes { 0.0 } else { f.standard_deduction };
        let itemizes_f = itemizes as u8 as f64;
        let (xitded, mut stded);
        if y <= 2017 {
            xitded = (fed_itemized - salt_ded).abs() * itemizes_f;
            stded = fed_std;
        } else {
            let c = p.std2018.as_ref().expect("2018 standard deduction");
            let stit = (c.salt_cap / sep).min(salt_ded);
            xitded = (fed_itemized - stit).abs() * itemizes_f;
            let (base, aged_add) = match fs {
                Fs::Single => (c.single, c.aged_single),
                Fs::Hoh => (c.hoh, c.aged_hoh),
                _ => (c.married, c.aged_married),
            };
            stded = base / sep + aged_add * i.aged_count();
        }
        if let Some(ph) = &p.phaseout {
            let reduction = ph.rate * (fed_agi - phas92.unwrap_or(0.0)).max(0.0);
            stded = (fed_std - reduction).max(0.0);
            if let Some(married) = ph.override_married {
                if is_joint || is_sep {
                    stded = (married / sep - reduction).max(0.0);
                }
            }
            if let Some(single) = ph.override_single {
                if is_single {
                    stded = (single - reduction).max(0.0);
                }
            }
        }
        if let Some(cap) = p.property_cap {
            if i.proptax > 0.0 {
                stded = fed_std - i.proptax.min(cap * txp);
            }
        }
        let deduc = if y == 1987 { (fed_itemized - salt_ded - stded).max(0.0) } else { xitded.max(stded) };
        let exemp = if y <= 1992 { federal_exemption_count(y, i) * p.exemption } else { 0.0 };
        let taxinc = (ne_agi - exemp - deduc).max(0.0);

        // ---- tax ----
        if y <= 1992 {
            let mut income = taxinc;
            if let Some(zero) = &p.zero_bracket {
                income = (taxinc - zero[fs as usize]).max(0.0);
            }
            statax = tables.by_status[fs as usize].tax(income);
        } else {
            let taxy = if is_sep { 2.0 * taxinc } else { taxinc };
            let dagi = if is_sep { 2.0 * fed_agi } else { fed_agi };
            let table = &tables.by_status[fs as usize];
            statax = table.tax(taxy) / sep;
            let rt = table.rate(taxy);
            if let Some(a) = &p.additional {
                let table_index = match fs {
                    Fs::Single => 0,
                    Fs::Hoh => 2,
                    _ => 1,
                };
                let bounds = &a.bounds[table_index];
                let (surtax, cap_amount) = additional_tax(dagi, bounds, &a.rates);
                let top = bounds[3];
                let limited = if dagi <= top { surtax } else { cap_amount };
                let added = if is_single { limited } else { limited / sep };
                let high = dagi > phas92.unwrap_or(0.0);
                let big = taxinc >= a.min_taxable_income;
                if high && big {
                    statax += added;
                } else if high && taxinc < a.agi_share * (fed_agi - phas92.unwrap_or(0.0)) {
                    statax = rt * taxinc;
                }
            }
        }
    }

    // ---- credits ----
    let stxcr = if y <= 1992 {
        p.personal_credit * exemps
    } else {
        let xcred = match &p.personal_phaseout {
            Some(ph) => {
                let agdiff = (fed_agi - ph.threshold[fs as usize]).max(0.0);
                (p.personal_credit - ph.amount * agdiff / (ph.step / sep)).max(0.0)
            }
            None => p.personal_credit.max(0.0),
        };
        xcred * federal_exemption_count(y, i)
    };
    let mut credit = stxcr;
    if y >= 1987 {
        credit += p.elderly_share * f.federal_elder;
    }
    let chcr = p.child_care_share * fed_ccc;
    let mut chcref = 0.0;
    let mut refundable_zone = false;
    if let Some(r) = &p.child_care_refundable {
        refundable_zone = fed_agi <= r.agi_limit;
        let share = 1.0 - r.share_step * (fed_agi - r.full_share_agi).max(0.0);
        chcref = if refundable_zone { share * fed_ccc } else { 0.0 };
    }
    credit += if refundable_zone { 0.0 } else { chcr };
    statax += p.minimum_tax_share * almtax;
    statax = (statax - credit).max(0.0);
    statax = (taxbc + almtax).min(statax);
    let earncr = p.eitc_match_rate * f.eitc;
    // Any income when child care expenses are paid, otherwise income at or below the federal poverty guideline.
    let ctc = match &p.child_tax_credit {
        Some(c) => {
            let household = txp + i.depx;
            let poverty = c.poverty_first + c.poverty_additional * (household - 1.0);
            let eligible = i.childcare > 0.0 || f.agi <= poverty;
            if eligible { c.amount.rate(f.agi) * i.dep6 } else { 0.0 }
        }
        None => 0.0,
    };
    (statax - chcref - earncr - ctc) * 1.0
}
