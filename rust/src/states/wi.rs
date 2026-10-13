//! Wisconsin income tax (port of `calculators.states.wi`).

use std::path::Path;

use crate::common::Brackets;
use crate::federal::{Fed, Inp};
use crate::params::{self, get, load, num, resolve_year, status_direct, Fs, YearParams};
use crate::states::{
    dividend_adjustment, household_income, law_year_is_exact, pre1987_gross_itemized, unemployment_total, Interp, Tiers,
};

type Rows = Vec<[f64; 4]>;

struct CapitalLossReduction {
    range: f64,
    limit: f64,
    share: f64,
}

struct Low {
    single_0: Vec<Vec<f64>>,
    single_1: Vec<Vec<f64>>,
    married_0: Vec<Vec<f64>>,
    married_1: Vec<Vec<f64>>,
    married_2: Vec<Vec<f64>>,
}

struct Std {
    index: f64,
    single: Rows,
    hoh: Rows,
    joint: Rows,
    separate: Rows,
}

enum ExemptionCredit {
    None,
    Early { taxpayer: f64, aged: f64, dependent: f64, hoh: f64 },
    Mid { aged: f64, dependent: f64 },
    Limited { aged: f64, dependent: f64, phaseout_rate: f64, width: f64, income_limit: [f64; 4] },
}

enum School {
    None,
    Flat { renter: f64, rate: f64, minimum: f64, maximum: f64 },
    Max { max: f64, renter_rate: f64, renter_divisor: f64, owner_rate: f64, owner_divisor: f64 },
}

struct Working {
    full_income: f64,
    limit_joint: f64,
    limit_other: f64,
    intercept_joint: f64,
    intercept_other: f64,
    slope: f64,
}

struct Own {
    phaseout_start: f64,
    /// Rate, maximum and phaseout rate for one, two and three or more dependents.
    kinds: [(f64, f64, f64); 3],
}

enum Eitc {
    None,
    Own(Own),
    ByChildren([f64; 3]),
    Flat(f64),
}

/// Only the 1987-2018 minimum tax changes the tax; the earlier preference and federal-share
/// versions are computed but never added to it.
enum Minimum {
    None,
    Amt { single: f64, married: f64, phaseout_single: f64, phaseout_married: f64, phaseout_rate: f64, rate: f64 },
}

struct Homestead {
    income_limit: f64,
    tax_cap: f64,
    rent_share: f64,
    income_floor: f64,
    income_rate: f64,
    small_limit: f64,
    small_rate: f64,
    rate: f64,
    dependent_deduction: f64,
}

struct Retirement {
    min_age: f64,
    single: f64,
    joint: f64,
}

enum Schedule {
    Early(Brackets),
    Late { single: Brackets, joint: Brackets, separate: Brackets },
}

pub struct WiParams {
    year: i64,
    ui_subtraction: Option<(f64, f64)>,
    unemployment_share: f64,
    unemployment_base: [f64; 4],
    loss_reduction: Option<CapitalLossReduction>,
    loss_limit: Option<(f64, f64)>,
    gain_addback: Option<f64>,
    gain_exclusion: f64,
    child_care_subtraction: Option<f64>,
    dependent_addition: Option<Interp>,
    standard_share: Option<(f64, f64)>,
    std: Option<Std>,
    dependent_floor: f64,
    dependent_earned_addition: f64,
    low: Option<Low>,
    exemption: Option<(f64, f64)>,
    schedule: Schedule,
    tax_reduction: Option<(f64, f64)>,
    surtax: Option<f64>,
    exemption_credit: ExemptionCredit,
    itemized_credit_rate: Option<f64>,
    school: School,
    child_care_share: Option<f64>,
    working: Option<Working>,
    eitc: Eitc,
    minimum: Minimum,
    married_couple: Option<(f64, f64)>,
    homestead: Homestead,
    retirement: Option<Retirement>,
    dividend_adjustment: f64,
}

fn rows_of(v: &serde_yaml::Value) -> Result<Rows, String> {
    params::matrix(v)?.into_iter().map(|r| Ok([r[0], r[1], r[2], r[3]])).collect()
}

fn scale(rows: &[Vec<f64>], factor: f64) -> Brackets {
    let scaled: Vec<Vec<f64>> = rows.iter().map(|r| vec![r[0] * factor, r[1]]).collect();
    Brackets::new(&scaled)
}

impl WiParams {
    pub fn load(root: &Path, year: i64) -> Result<Self, String> {
        let doc = load(root, "states/wi/income_tax.yaml")?;
        law_year_is_exact(root, &doc, year)?;
        let capital_gains = load(root, "national/capital_gains.yaml")?;
        let p = YearParams::new(&doc, year);
        let opt = |key: &str| -> Result<Option<f64>, String> { Ok(if p.in_effect(key)? { Some(p.f(key)?) } else { None }) };

        let ui_subtraction = match p.opt("unemployment_subtraction_base")? {
            Some(b) => Some((num(get(b, "married_joint")?)?, num(get(b, "other")?)?)),
            None => None,
        };
        let loss_reduction = match p.opt("capital_loss_reduction")? {
            Some(c) => Some(CapitalLossReduction {
                range: num(get(c, "range_per_taxpayer")?)?,
                limit: num(get(c, "limit_per_taxpayer")?)?,
                share: num(get(c, "share")?)?,
            }),
            None => None,
        };
        let loss_limit = if loss_reduction.is_none() && p.in_effect("capital_loss_limit")? {
            Some((num(resolve_year(get(&capital_gains, "net_capital_loss_limit")?, year)?)?, p.f("capital_loss_limit")?))
        } else {
            None
        };
        let gain_addback = opt("capital_gain_exclusion_addback")?;
        let std = if p.in_effect("standard_deduction")? && !p.in_effect("standard_deduction_share")? {
            let t = p.value("standard_deduction", &[])?;
            let mut hoh = rows_of(get(t, "head_of_household")?)?;
            let mut separate = rows_of(get(t, "married_separate")?)?;
            // TAXSIM measures these tiers from an earlier start.
            if (2000..=2015).contains(&year) {
                hoh[2][3] = hoh[1][0];
            }
            if (1988..=1998).contains(&year) {
                separate[1][3] = separate[0][0];
            }
            Some(Std {
                index: p.f("standard_deduction_index")?,
                single: rows_of(get(t, "single")?)?,
                hoh,
                joint: rows_of(get(t, "married_joint")?)?,
                separate,
            })
        } else {
            None
        };
        let low = match p.opt("low_income_deduction")? {
            Some(t) => Some(Low {
                single_0: params::matrix(get(t, "single_0")?)?,
                single_1: params::matrix(get(t, "single_1")?)?,
                married_0: params::matrix(get(t, "married_0")?)?,
                married_1: params::matrix(get(t, "married_1")?)?,
                married_2: params::matrix(get(t, "married_2")?)?,
            }),
            None => None,
        };
        let schedules = p.value("brackets", &[])?;
        let index = p.f("bracket_index")?;
        let schedule = if year <= 1985 {
            Schedule::Early(scale(&params::brackets(get(schedules, "all")?)?, index))
        } else {
            let single_index = if p.in_effect("single_bracket_index")? { p.f("single_bracket_index")? } else { index };
            let joint_index = single_index;
            let joint_rows = params::brackets(get(schedules, "married_joint")?)?;
            let separate_v = get(schedules, "married_separate")?;
            let separate_rows = if separate_v.is_null() {
                joint_rows.iter().map(|b| vec![b[0] / 2.0, b[1]]).collect()
            } else {
                params::brackets(separate_v)?
            };
            Schedule::Late {
                single: scale(&params::brackets(get(schedules, "single")?)?, single_index),
                joint: scale(&joint_rows, joint_index),
                separate: scale(&separate_rows, joint_index),
            }
        };
        let exemption_credit = match p.opt("exemption_credit")? {
            None => ExemptionCredit::None,
            Some(c) => {
                if c.get("taxpayer").is_some() {
                    ExemptionCredit::Early {
                        taxpayer: num(get(c, "taxpayer")?)?,
                        aged: num(get(c, "aged")?)?,
                        dependent: num(get(c, "dependent")?)?,
                        hoh: num(get(c, "head_of_household")?)?,
                    }
                } else if c.get("income_limit").is_none() {
                    ExemptionCredit::Mid { aged: num(get(c, "aged")?)?, dependent: num(get(c, "dependent")?)? }
                } else {
                    ExemptionCredit::Limited {
                        aged: num(get(c, "aged")?)?,
                        dependent: num(get(c, "dependent")?)?,
                        phaseout_rate: num(get(c, "phaseout_rate")?)?,
                        width: num(get(c, "width")?)?,
                        income_limit: status_direct(get(c, "income_limit")?)?,
                    }
                }
            }
        };
        let school = if p.in_effect("school_property_credit_flat")? {
            let c = p.value("school_property_credit_flat", &[])?;
            School::Flat {
                renter: num(get(c, "renter")?)?,
                rate: num(get(c, "rate")?)?,
                minimum: num(get(c, "minimum")?)?,
                maximum: num(get(c, "maximum")?)?,
            }
        } else if p.in_effect("school_property_credit_max")? {
            School::Max {
                max: p.f("school_property_credit_max")?,
                renter_rate: p.f("school_property_credit_renter_rate")?,
                renter_divisor: num(get(&doc, "school_property_credit_renter_divisor")?)?,
                owner_rate: p.f("school_property_credit_owner_rate")?,
                owner_divisor: num(get(&doc, "school_property_credit_owner_divisor")?)?,
            }
        } else {
            School::None
        };
        let working = match p.opt("working_families_credit")? {
            Some(c) => {
                let limit = get(c, "income_limit")?;
                let intercept = get(c, "intercept")?;
                Some(Working {
                    full_income: num(get(c, "full_income_per_taxpayer")?)?,
                    limit_joint: num(get(limit, "married_joint")?)?,
                    limit_other: num(get(limit, "other")?)?,
                    intercept_joint: num(get(intercept, "married_joint")?)?,
                    intercept_other: num(get(intercept, "other")?)?,
                    slope: num(get(c, "slope")?)?,
                })
            }
            None => None,
        };
        let shares = p.opt("eitc_share")?;
        let eitc = if p.in_effect("eitc_own_schedule")? {
            let c = p.value("eitc_own_schedule", &[])?;
            let kind = |key: &str| -> Result<(f64, f64, f64), String> {
                let k = get(c, key)?;
                Ok((num(get(k, "rate")?)?, num(get(k, "maximum")?)?, num(get(k, "phaseout_rate")?)?))
            };
            Eitc::Own(Own { phaseout_start: num(get(c, "phaseout_start")?)?, kinds: [kind("one")?, kind("two")?, kind("three")?] })
        } else {
            match shares {
                Some(s) if s.is_mapping() => Eitc::ByChildren([num(get(s, "one")?)?, num(get(s, "two")?)?, num(get(s, "three")?)?]),
                Some(s) => Eitc::Flat(num(s)?),
                None => Eitc::None,
            }
        };
        let minimum = if p.in_effect("minimum_tax_preferences")? || p.in_effect("minimum_tax_federal_share")? {
            Minimum::None
        } else if p.in_effect("amt_exemption_single")? {
            Minimum::Amt {
                single: p.f("amt_exemption_single")?,
                married: p.f("amt_exemption_married")?,
                phaseout_single: p.f("amt_phaseout_single")?,
                phaseout_married: p.f("amt_phaseout_married")?,
                phaseout_rate: num(get(&doc, "amt_phaseout_rate")?)?,
                rate: num(get(&doc, "amt_rate")?)?,
            }
        } else {
            Minimum::None
        };
        let h = p.value("homestead_credit", &[])?;
        let hn = |key: &str| -> Result<f64, String> { num(get(h, key)?) };
        let homestead = Homestead {
            income_limit: hn("income_limit")?,
            tax_cap: hn("tax_cap")?,
            rent_share: hn("rent_share")?,
            income_floor: hn("income_floor")?,
            income_rate: hn("income_rate")?,
            small_limit: hn("small_limit")?,
            small_rate: hn("small_rate")?,
            rate: hn("rate")?,
            dependent_deduction: hn("dependent_deduction")?,
        };
        let d = get(&doc, "dependent_standard_deduction")?;
        Ok(Self {
            year,
            ui_subtraction,
            unemployment_share: num(get(&doc, "unemployment_share")?)?,
            unemployment_base: status_direct(get(&doc, "unemployment_base")?)?,
            loss_reduction,
            loss_limit,
            gain_addback,
            gain_exclusion: opt("capital_gain_exclusion")?.unwrap_or(0.0),
            child_care_subtraction: opt("child_care_subtraction_per_child")?,
            dependent_addition: match p.opt("dependent_addition")? {
                Some(t) => Some(Interp::new(&params::brackets(t)?)),
                None => None,
            },
            standard_share: match p.opt("standard_deduction_share")? {
                Some(c) => Some((num(get(c, "rate")?)?, num(get(c, "cap")?)?)),
                None => None,
            },
            std,
            dependent_floor: num(get(d, "floor")?)?,
            dependent_earned_addition: num(get(d, "earned_addition")?)?,
            low,
            exemption: if p.in_effect("exemption")? { Some((p.f("exemption")?, p.f("exemption_aged")?)) } else { None },
            schedule,
            tax_reduction: match p.opt("tax_reduction")? {
                Some(r) => Some((num(get(r, "rate")?)?, num(get(r, "cap")?)?)),
                None => None,
            },
            surtax: opt("surtax")?,
            exemption_credit,
            itemized_credit_rate: opt("itemized_credit_rate")?,
            school,
            child_care_share: opt("child_care_credit_share")?,
            working,
            eitc,
            minimum,
            married_couple: match p.opt("married_couple_credit")? {
                Some(c) => Some((num(get(c, "rate")?)?, num(get(c, "cap")?)?)),
                None => None,
            },
            homestead,
            retirement: match p.opt("retirement_exclusion")? {
                Some(r) => Some(Retirement { min_age: num(get(r, "min_age")?)?, single: num(get(r, "single")?)?, joint: num(get(r, "joint")?)? }),
                None => None,
            },
            dividend_adjustment: dividend_adjustment(root)?,
        })
    }
}

/// First row whose AGI bound is at least AGI: max(0, amount - rate * (AGI - start)).
fn standard(rows: &Rows, agi: f64, index: f64) -> f64 {
    let uppers: Vec<f64> = rows.iter().map(|r| if r[0] >= 1.0e19 { r[0] } else { r[0] * index }).collect();
    let k = Tiers::new(uppers, false).index(agi);
    let r = rows[k];
    (r[1] * index - r[2] * (agi - r[3] * index)).max(0.0)
}

/// TAXSIM `witab`: the amount for the first row whose bound is at least AGI, by number of dependents.
fn low_income(rows: &[Vec<f64>], agi: f64, depx: f64, depadd: f64) -> f64 {
    let width = rows[0].len() - 1;
    let uppers: Vec<f64> = rows.iter().map(|r| r[0]).collect();
    let row = &rows[Tiers::new(uppers, false).index(agi)];
    let mut k = width - 1;
    for count in 0..width - 1 {
        if depx == count as f64 {
            k = count;
            break;
        }
    }
    row[1 + k] + (depx - (width as f64 - 1.0)).max(0.0) * depadd
}

pub fn tax(i: &Inp, f: &Fed, p: &WiParams) -> f64 {
    let y = p.year;
    let fs = i.fs();
    let is_joint = fs == Fs::Joint;
    let is_sep = fs == Fs::Separate;
    let is_single = fs == Fs::Single;
    let is_hoh = fs == Fs::Hoh;
    let single_like = is_single || is_hoh;
    let sep = i.sepret();
    let txp = i.taxpayer_count();
    let aged = i.aged_count();
    let depx = i.depx;
    let dependent_filer = i.is_dependent_filer();
    let fed_agi = f.agi;
    let ss_taxable = f.taxable_social_security;
    let ui = unemployment_total(i);
    let (stcg, ltcg) = (i.stcg, i.ltcg);
    let fullcg = stcg + ltcg;
    let hy = household_income(i, p.dividend_adjustment);
    let salt = f.ded;

    // ---- Wisconsin AGI ----
    let mut agi = fed_agi;
    if y == 2020 {
        agi += ui - f.taxable_unemployment;
    }
    if let Some((joint, other)) = p.ui_subtraction {
        let base = if is_joint { joint } else { other };
        let half = p.unemployment_share * (fed_agi - (base + ss_taxable)).max(0.0);
        agi -= (ui - ui.min(half)).max(0.0);
    }
    // Capital gains and losses.
    if let Some(c) = &p.loss_reduction {
        let loss = if stcg < 0.0 { ltcg } else { fullcg };
        let limited = loss.max(-c.limit * txp);
        let change = (limited - c.share * loss).min(0.0);
        let in_range = loss > -c.range * txp && loss < 0.0;
        if fullcg <= 0.0 && in_range {
            agi += change;
        }
    } else if let Some((limit, cap)) = p.loss_limit {
        let caplss = fullcg.max(-limit / sep).abs();
        let closswi = caplss.min(cap).min(agi.max(0.0));
        if fullcg <= 0.0 {
            agi += caplss - closswi;
        }
    }
    if let Some(addback) = p.gain_addback {
        if fullcg > 0.0 {
            agi += addback * f.pre87.capded;
        }
    } else if y >= 1987 {
        let excluded = fullcg.min(ltcg).max(0.0);
        if fullcg > 0.0 {
            agi -= p.gain_exclusion * excluded;
        }
    }
    if (1982..=1986).contains(&y) {
        agi += f.pre87.twoded;
    }
    if (1986..=2007).contains(&y) {
        if i.gssi > 0.0 {
            agi -= (ss_taxable - i.gssi / 2.0).max(0.0);
        }
    } else if y >= 2008 {
        agi -= ss_taxable;
    }
    // Unemployment compensation up to half of federal AGI above a base.
    let ui_base = p.unemployment_base[fs as usize];
    let ui_taxable = ui.min(p.unemployment_share * (fed_agi - ui_base - ss_taxable).max(0.0));
    if y != 2020 && ui > 0.0 {
        agi -= ui - ui_taxable;
    }
    // Child and dependent care expenses (2011 on, with a federal credit).
    if let Some(per_child) = p.child_care_subtraction {
        let children = (if i.dep13 > 0.0 { i.dep13 } else { depx }).min(2.0);
        let mut child = i.childcare.min(per_child * children);
        if is_joint {
            child = child.min(i.pwages).min(i.swages).max(0.0);
        }
        if f.federal_chcr > 0.0 {
            agi -= child;
        }
    }

    // ---- standard deduction ----
    let depadd = p.dependent_addition.as_ref().map_or(0.0, |t| t.eval(agi));
    let mut stded = if let Some((rate, cap)) = p.standard_share {
        (rate * agi).max(0.0).min(cap) + depadd
    } else if let Some(s) = &p.std {
        let rows = if is_single {
            &s.single
        } else if is_hoh {
            &s.hoh
        } else if is_joint {
            &s.joint
        } else {
            &s.separate
        };
        standard(rows, agi, s.index)
    } else {
        0.0
    };
    if dependent_filer {
        stded = stded.min(p.dependent_floor.max(f.earned_income + p.dependent_earned_addition));
    }
    if let Some(l) = &p.low {
        let rows = if single_like && aged == 0.0 {
            &l.single_0
        } else if single_like {
            &l.single_1
        } else if aged == 0.0 {
            &l.married_0
        } else if aged == 1.0 {
            &l.married_1
        } else {
            &l.married_2
        };
        stded = stded.max(low_income(rows, agi, depx, depadd));
    }

    // ---- itemized deductions (a credit from 1986) ----
    let contr = i.charity_cash;
    let inrst = i.mortgage;
    let mut xitded = if y <= 1985 {
        let gross = pre1987_gross_itemized(i, f);
        if y >= 1979 { gross - salt - i.proptax } else { gross }
    } else {
        contr + inrst
    };
    xitded = xitded.max(0.0);

    let mut taxinc = if y <= 1985 { (agi - xitded.max(stded)).max(0.0) } else { (agi - stded).max(0.0) };
    let mut exemp = 0.0;
    if let Some((amount, aged_amount)) = p.exemption {
        exemp = if dependent_filer { 0.0 } else { amount * (depx + txp) + aged_amount * aged };
    }
    taxinc = (taxinc - exemp).max(0.0);

    // ---- tax ----
    let mut statax = match &p.schedule {
        Schedule::Early(table) => {
            // Joint returns: the lesser of income splitting and the spouses' shares.
            if is_joint {
                let yh = i.pwages.max(i.swages) + (taxinc - i.pwages - i.swages) / 2.0;
                let yw = taxinc - yh;
                let split = 2.0 * table.tax(taxinc / 2.0);
                let spouses = table.tax(yh.max(0.0)) + table.tax(yw.max(0.0));
                split.min(spouses)
            } else {
                table.tax(taxinc)
            }
        }
        Schedule::Late { single, joint, separate } => {
            if single_like {
                single.tax(taxinc)
            } else if is_joint {
                joint.tax(taxinc)
            } else {
                separate.tax(taxinc)
            }
        }
    };
    if let Some((rate, cap)) = p.tax_reduction {
        statax -= (rate * statax).min(cap);
    }
    if let Some(surtax) = p.surtax {
        statax *= surtax;
    }

    // ---- credits ----
    let exempc = match &p.exemption_credit {
        ExemptionCredit::None => 0.0,
        ExemptionCredit::Early { taxpayer, aged: a, dependent, hoh } => {
            taxpayer * txp + a * aged + dependent * depx + if is_hoh { *hoh } else { 0.0 }
        }
        ExemptionCredit::Mid { aged: a, dependent } => a * aged + dependent * depx,
        ExemptionCredit::Limited { aged: a, dependent, phaseout_rate, width, income_limit } => {
            let limit = income_limit[fs as usize];
            let reduction = phaseout_rate * (hy - limit);
            let per_return = if is_joint { a * aged } else { *a };
            let aged_credit = if hy <= limit {
                per_return
            } else if hy <= limit + width {
                per_return - reduction
            } else {
                0.0
            };
            dependent * depx + if aged > 0.0 { aged_credit } else { 0.0 }
        }
    };
    let xcred = p.itemized_credit_rate.map_or(0.0, |rate| rate * (xitded - stded).max(0.0));
    let renter = i.proptax < 1.0 && i.rentpaid > 0.0;
    let spcred = match &p.school {
        School::None => 0.0,
        School::Flat { renter: renter_amount, rate, minimum, maximum } => {
            if i.rentpaid > 0.0 { *renter_amount } else { (rate * i.proptax).max(*minimum).min(*maximum) }
        }
        School::Max { max, renter_rate, renter_divisor, owner_rate, owner_divisor } => {
            let cmax = max / sep;
            let renter_base = if renter { i.rentpaid } else { 0.0 };
            if i.rentpaid > 0.0 {
                (renter_rate * renter_base / renter_divisor).min(cmax)
            } else {
                (owner_rate * i.proptax / owner_divisor).min(cmax)
            }
        }
    };
    let chcr = p.child_care_share.map_or(0.0, |share| share * f.federal_chcr);
    let mut wcred = 0.0;
    if let Some(w) = &p.working {
        let limit = if is_joint { w.limit_joint } else { w.limit_other };
        let intercept = if is_joint { w.intercept_joint } else { w.intercept_other };
        let share = intercept - w.slope * agi;
        wcred = if agi <= w.full_income * txp {
            statax
        } else if agi <= limit {
            share * (statax - exempc - xcred - spcred).max(0.0)
        } else {
            0.0
        };
        if dependent_filer {
            wcred = 0.0;
        }
    }
    let eitc = if y <= 1986 { f.pre87.earncr } else { f.eitc };
    let earncr = match &p.eitc {
        Eitc::None => 0.0,
        Eitc::Own(o) => {
            let earned = f.earned_income;
            let income = agi.max(earned);
            let kind = if depx == 1.0 { Some(0) } else if depx == 2.0 { Some(1) } else if depx >= 3.0 { Some(2) } else { None };
            match kind {
                None => 0.0,
                Some(k) => {
                    let (rate, maximum, phaseout_rate) = o.kinds[k];
                    let mut credit = (rate * earned).min(maximum).max(0.0);
                    if income > o.phaseout_start {
                        credit = (credit - phaseout_rate * (income - o.phaseout_start)).max(0.0);
                    }
                    if is_sep || dependent_filer { 0.0 } else { credit }
                }
            }
        }
        Eitc::ByChildren(shares) => {
            let share = if depx == 1.0 { shares[0] } else if depx == 2.0 { shares[1] } else if depx >= 3.0 { shares[2] } else { 0.0 };
            share * eitc
        }
        Eitc::Flat(share) => share * eitc,
    };
    let credit = xcred + exempc + earncr + chcr + spcred + wcred;
    statax = if y <= 1988 { (statax - credit).max(0.0) } else { (statax - exempc - xcred - spcred - wcred).max(0.0) };

    // ---- minimum tax ----
    match &p.minimum {
        Minimum::None => {}
        Minimum::Amt { single, married, phaseout_single, phaseout_married, phaseout_rate, rate } => {
            let mut alminy = f.amt_income - ss_taxable;
            if ui > 0.0 {
                alminy -= ui - ui_taxable;
            }
            let gains = if stcg >= 0.0 { ltcg.max(0.0) } else { fullcg.max(0.0) };
            alminy += p.gain_exclusion * gains;
            let exempt_single = (single - phaseout_rate * (alminy - phaseout_single).max(0.0)).max(0.0);
            // TAXSIM gives separate returns the married exemption halved.
            let married_exemption = married / sep;
            let exempt_married = (married_exemption - phaseout_rate * (alminy - phaseout_married / sep).max(0.0)).max(0.0);
            alminy -= if single_like { exempt_single } else { exempt_married };
            let amt = (rate * alminy - statax).max(0.0);
            statax += amt;
        }
    }

    // ---- married couple credit ----
    if let Some((rate, cap)) = p.married_couple {
        let business = 0.5 * (i.psemp + i.ssemp);
        let earh = (i.pwages + i.pbusinc + i.pprofinc + business).max(0.0);
        let earw = (i.swages + i.sbusinc + i.sprofinc + business).max(0.0);
        let twocrd = if is_joint { rate * earh.min(earw).min(cap) } else { 0.0 };
        statax = (statax - twocrd).max(0.0);
    }

    // ---- homestead credit (refundable) ----
    let c = &p.homestead;
    let hymod = (hy - c.dependent_deduction * depx).max(0.0);
    let income_test = if y <= 1988 { hy } else { hymod };
    let reduce_on = if y >= 1989 { hymod } else { hy };
    let ptax = (i.proptax + c.rent_share * i.rentpaid).min(c.tax_cap);
    let tablea = if reduce_on > c.income_floor { (reduce_on - c.income_floor) * c.income_rate } else { 0.0 };
    let tableb = (ptax - tablea).max(0.0);
    let mut pcred = if tableb <= 0.0 {
        0.0
    } else if tableb <= c.small_limit {
        c.small_rate * tableb
    } else {
        c.rate * tableb
    };
    if !(income_test <= c.income_limit) {
        pcred = 0.0;
    }
    statax -= pcred;
    if y >= 1989 {
        statax -= earncr;
    }
    if let Some(r) = &p.retirement {
        let elderly = (if i.page >= r.min_age { 1.0 } else { 0.0 }) + (if is_joint && i.sage >= r.min_age { 1.0 } else { 0.0 });
        let cap = if elderly >= 2.0 && is_joint { r.joint } else { r.single * elderly };
        let line16 = i.pensions.max(0.0).min(cap);
        let reduced_agi = (agi - line16).max(0.0);
        let s = p.std.as_ref().expect("standard deduction tables");
        let rows = if is_single {
            &s.single
        } else if is_hoh {
            &s.hoh
        } else if is_joint {
            &s.joint
        } else {
            &s.separate
        };
        let reduced_std = standard(rows, reduced_agi, 1.0);
        let alt_income = (reduced_agi - reduced_std - exemp).max(0.0);
        if let Schedule::Late { single, joint, separate } = &p.schedule {
            let alt_tax = if single_like { single.tax(alt_income) } else if is_joint { joint.tax(alt_income) } else { separate.tax(alt_income) };
            if line16 > 0.0 {
                statax = statax.min(alt_tax);
            }
        }
    }
    statax
}
