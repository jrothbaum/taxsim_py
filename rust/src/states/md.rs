//! Maryland income tax (port of `calculators.states.md`).

use std::path::Path;

use crate::common::Brackets;
use crate::federal::{Fed, Inp};
use crate::params::{self, get, load, num, status_direct, Fs, YearParams};
use crate::states::{federal_exemption_count, law_year_is_exact, pre1987_gross_itemized};

struct Tier {
    low: f64,
    high: f64,
    value: f64,
}

enum Schedule {
    One(Brackets),
    Groups { single_or_separate: Brackets, joint_or_hoh: Brackets },
}

enum Standard {
    CapPerExemption(f64),
    PerExemption { floor: f64, ceiling: f64, aged: Option<f64> },
    Limits { single: (f64, f64), joint: (f64, f64) },
    Flat([f64; 4]),
}

enum Threshold {
    One(f64),
    ByStatus([f64; 4]),
}

struct CapitalGainsCapped {
    share: f64,
    cap_joint: f64,
    cap_other: f64,
    threshold_joint: f64,
    threshold_other: f64,
    reduction_rate: f64,
}

struct Phaseout {
    floor: f64,
    single: Vec<Tier>,
    joint: Vec<Tier>,
}

struct CareYear {
    unit: f64,
    units: f64,
    start: f64,
    step: f64,
    limit: f64,
}

enum CareRate {
    None,
    Y2000 { unit: f64, units: f64, start: f64, step: f64 },
    Y2001 { unit: f64, units: f64, start: f64, step_numerator: f64, step_divisor: f64 },
    Y2019 { joint: CareYear, other: CareYear },
}

struct Senior {
    single: f64,
    hoh: f64,
    joint_one: f64,
    joint_two: f64,
    limit_other: f64,
    limit_joint_or_hoh: f64,
}

struct Ctc {
    amount: f64,
    agi_cap: f64,
    phaseout: Option<(f64, f64, f64)>,
}

struct Refundable {
    rate: f64,
    childless_floor: Option<f64>,
}

pub struct MdParams {
    year: i64,
    max_children: f64,
    care_subtraction_per_child: f64,
    pension_exclusion: f64,
    preference: Option<(f64, f64)>,
    interest_exclusion: Option<f64>,
    two_earner: f64,
    two_earner_high: Option<(f64, f64)>,
    cg_share: Option<f64>,
    cg_capped: Option<CapitalGainsCapped>,
    standard_rate: f64,
    standard: Standard,
    itemized_threshold: Option<(Threshold, f64)>,
    itemized_phaseout: Option<(f64, [f64; 4])>,
    personal_exemption: f64,
    aged_exemption: Option<f64>,
    exemption_phaseout: Option<Phaseout>,
    schedule: Schedule,
    surtax: Option<(f64, f64)>,
    eitc_share: Option<f64>,
    refundable: Option<Refundable>,
    care: CareRate,
    poverty: Option<(f64, f64, f64)>,
    senior: Option<Senior>,
    ctc: Option<Ctc>,
}

impl MdParams {
    pub fn load(root: &Path, year: i64) -> Result<Self, String> {
        let doc = load(root, "states/md/income_tax.yaml")?;
        law_year_is_exact(root, &doc, year)?;
        let p = YearParams::new(&doc, year);
        let opt = |key: &str| -> Result<Option<f64>, String> { Ok(if p.in_effect(key)? { Some(p.f(key)?) } else { None }) };
        let pair = |v: &serde_yaml::Value, a: &str, b: &str| -> Result<(f64, f64), String> { Ok((num(get(v, a)?)?, num(get(v, b)?)?)) };
        let tiers = |v: &serde_yaml::Value| -> Result<Vec<Tier>, String> {
            Ok(params::matrix(v)?.into_iter().map(|r| Tier { low: r[0], high: r[1], value: r[2] }).collect())
        };

        let b = p.value("brackets", &[])?;
        let schedule = if b.is_mapping() {
            Schedule::Groups {
                single_or_separate: Brackets::new(&params::brackets(get(b, "single_or_separate")?)?),
                joint_or_hoh: Brackets::new(&params::brackets(get(b, "joint_or_hoh")?)?),
            }
        } else {
            Schedule::One(Brackets::new(&params::brackets(b)?))
        };
        let standard = if p.in_effect("standard_deduction_cap_per_exemption")? {
            Standard::CapPerExemption(p.f("standard_deduction_cap_per_exemption")?)
        } else if p.in_effect("standard_deduction_limits_per_exemption")? {
            let l = p.value("standard_deduction_limits_per_exemption", &[])?;
            Standard::PerExemption { floor: num(get(l, "floor")?)?, ceiling: num(get(l, "ceiling")?)?, aged: opt("aged_standard_deduction")? }
        } else if p.in_effect("standard_deduction_limits")? {
            let l = p.value("standard_deduction_limits", &[])?;
            Standard::Limits {
                single: pair(get(l, "single_or_separate")?, "floor", "ceiling")?,
                joint: pair(get(l, "joint_or_hoh")?, "floor", "ceiling")?,
            }
        } else {
            Standard::Flat(status_direct(p.value("standard_deduction_flat", &[])?)?)
        };
        let itemized_threshold = if (1991..=2017).contains(&year) {
            let t = p.value("itemized_limitation_threshold", &[])?;
            let index = p.f("itemized_limitation_index")?;
            Some((
                if t.is_mapping() {
                    let raw = status_direct(t)?;
                    Threshold::ByStatus([raw[0] * index, raw[1] * index, raw[2] * index, raw[3] * index])
                } else {
                    Threshold::One(num(t)? * index)
                },
                index,
            ))
        } else {
            None
        };
        let itemized_phaseout = match p.opt("itemized_deduction_phaseout")? {
            Some(ph) => {
                // The rate sits beside the four filing-status thresholds.
                let thresholds = [num(get(ph, "single")?)?, num(get(ph, "married_joint")?)?, num(get(ph, "married_separate")?)?, num(get(ph, "head_of_household")?)?];
                Some((num(get(ph, "rate")?)?, thresholds))
            }
            None => None,
        };
        let exemption_phaseout = if year >= 2008 && p.in_effect("aged_exemption")? {
            let t = p.value("exemption_phaseout", &[])?;
            Some(Phaseout { floor: num(get(&doc, "exemption_phaseout_agi_floor")?)?, single: tiers(get(t, "single_or_separate")?)?, joint: tiers(get(t, "joint_or_hoh")?)? })
        } else {
            None
        };
        let care = if year == 2000 {
            let c = p.value("child_care_credit_rate", &[])?;
            CareRate::Y2000 { unit: num(get(c, "unit")?)?, units: num(get(c, "units")?)?, start: num(get(c, "start")?)?, step: num(get(c, "step")?)? }
        } else if (2001..=2018).contains(&year) {
            let c = p.value("child_care_credit_rate", &[])?;
            CareRate::Y2001 {
                unit: num(get(c, "unit")?)?,
                units: num(get(c, "units")?)?,
                start: num(get(c, "start")?)?,
                step_numerator: num(get(c, "step_numerator")?)?,
                step_divisor: num(get(c, "step_divisor")?)?,
            }
        } else if year >= 2019 {
            let rates = p.value("child_care_credit_rate", &[])?;
            let read = |c: &serde_yaml::Value| -> Result<CareYear, String> {
                Ok(CareYear {
                    unit: num(get(c, "unit")?)?,
                    units: num(get(c, "units")?)?,
                    start: num(get(c, "start")?)?,
                    step: num(get(c, "step")?)?,
                    limit: num(get(c, "refundable_agi_limit")?)?,
                })
            };
            CareRate::Y2019 { joint: read(get(rates, "married_joint")?)?, other: read(get(rates, "other")?)? }
        } else {
            CareRate::None
        };
        let senior = match p.opt("senior_tax_credit")? {
            Some(s) => Some(Senior {
                single: num(get(s, "single")?)?,
                hoh: num(get(s, "head_of_household")?)?,
                joint_one: num(get(s, "joint_one")?)?,
                joint_two: num(get(s, "joint_two")?)?,
                limit_other: num(get(s, "limit_other")?)?,
                limit_joint_or_hoh: num(get(s, "limit_joint_or_hoh")?)?,
            }),
            None => None,
        };
        let ctc = match p.opt("child_tax_credit")? {
            Some(c) => Some(Ctc {
                amount: num(get(c, "amount")?)?,
                agi_cap: num(get(c, "agi_cap")?)?,
                phaseout: match p.opt("child_tax_credit_phaseout")? {
                    Some(po) => Some((num(get(po, "threshold")?)?, num(get(po, "increment")?)?, num(get(po, "rate")?)?)),
                    None => None,
                },
            }),
            None => None,
        };
        let refundable = if p.in_effect("refundable_eitc_rate")? {
            Some(Refundable { rate: p.f("refundable_eitc_rate")?, childless_floor: if year >= 2020 { Some(p.f("refundable_eitc_childless_floor")?) } else { None } })
        } else {
            None
        };
        Ok(Self {
            year,
            max_children: num(get(&doc, "child_care_subtraction_max_children")?)?,
            care_subtraction_per_child: p.f("child_care_subtraction_per_child")?,
            pension_exclusion: p.f("pension_exclusion")?,
            preference: match opt("preference_addback_share")? {
                Some(share) => Some((share, p.f("preference_exclusion_per_taxpayer")?)),
                None => None,
            },
            interest_exclusion: opt("interest_exclusion_per_taxpayer")?,
            two_earner: if year > 1991 { p.f("two_earner_subtraction")? } else { 0.0 },
            two_earner_high: if year > 1991 {
                match p.opt("two_earner_subtraction_high_income")? {
                    Some(h) => Some((num(get(h, "agi_limit")?)?, num(get(h, "amount")?)?)),
                    None => None,
                }
            } else {
                None
            },
            cg_share: opt("capital_gains_exclusion_share")?,
            cg_capped: match p.opt("capital_gains_exclusion_capped")? {
                Some(cg) if !p.in_effect("capital_gains_exclusion_share")? => {
                    let cap = get(cg, "cap")?;
                    let threshold = get(cg, "income_threshold")?;
                    Some(CapitalGainsCapped {
                        share: num(get(cg, "share")?)?,
                        cap_joint: num(get(cap, "married_joint")?)?,
                        cap_other: num(get(cap, "other")?)?,
                        threshold_joint: num(get(threshold, "married_joint")?)?,
                        threshold_other: num(get(threshold, "other")?)?,
                        reduction_rate: num(get(cg, "reduction_rate")?)?,
                    })
                }
                _ => None,
            },
            standard_rate: p.f("standard_deduction_rate")?,
            standard,
            itemized_threshold,
            itemized_phaseout,
            personal_exemption: p.f("personal_exemption")?,
            aged_exemption: opt("aged_exemption")?,
            exemption_phaseout,
            schedule,
            surtax: match p.opt("capital_gains_surtax")? {
                Some(s) => Some((num(get(s, "threshold")?)?, num(get(s, "rate")?)?)),
                None => None,
            },
            eitc_share: opt("eitc_nonrefundable_share")?,
            refundable,
            care,
            poverty: if p.in_effect("poverty_credit_income_base")? {
                Some((p.f("poverty_credit_income_base")?, p.f("poverty_credit_income_per_additional_exemption")?, p.f("poverty_credit_rate")?))
            } else {
                None
            },
            senior,
            ctc,
        })
    }
}

fn tiered_replace(income: f64, tiers: &[Tier]) -> Option<f64> {
    let mut out = None;
    for t in tiers {
        if income > t.low && income <= t.high {
            out = Some(t.value);
        }
    }
    out
}

pub fn tax(i: &Inp, f: &Fed, p: &MdParams) -> f64 {
    let y = p.year;
    let fs = i.fs();
    let is_joint = fs == Fs::Joint;
    let is_hoh = fs == Fs::Hoh;
    let is_sep = fs == Fs::Separate;
    let single_or_separate = fs == Fs::Single || is_sep;
    let sep = i.sepret();
    let txp = i.taxpayer_count();
    let txpded = txp + if is_hoh { 1.0 } else { 0.0 };
    let aged = i.aged_count();
    let salt = f.ded;

    // ---- AGI ----
    let chnum = i.depx.clamp(0.0, p.max_children);
    let chexp = if y <= 1977 { 0.0 } else { (p.care_subtraction_per_child * chnum).min(i.childcare) };
    // Pension exclusion per taxpayer 65 or older, less Social Security benefits.
    let penexc = i.pensions.max(0.0).min((p.pension_exclusion * aged - i.gssi).max(0.0));
    let mut md_agi = f.agi - chexp - penexc;
    if y >= 1985 {
        md_agi -= f.taxable_social_security;
    }
    if y <= 1986 {
        let base = if y == 1981 { i.dividends + i.intrec } else { i.dividends };
        md_agi += base.min(f.pre87.divexc);
    }
    if let Some((share, exclusion)) = p.preference {
        md_agi += share * (f.pre87.pref - exclusion * txp).max(0.0);
    }
    if let Some(amount) = p.interest_exclusion {
        md_agi -= i.intrec.min(amount * txp).max(0.0);
    }
    if (1982..=1986).contains(&y) {
        md_agi += f.pre87.twoded;
    }

    // ---- Maryland's own two-earner subtraction (1992+, joint returns only) ----
    let wages_total = i.pwages + i.swages;
    let agih = i.pwages + 0.5 * (f.agi - wages_total);
    let agiw = i.swages + 0.5 * (f.agi - wages_total);
    let ss_subtracted = if y >= 1985 { f.taxable_social_security } else { 0.0 };
    let shared = chexp + ss_subtracted;
    let one_aged_pension = aged == 1.0 && penexc > 0.0;
    let twoh = (if one_aged_pension { agih - 0.5 * shared } else { agih - 0.5 * (shared + penexc) }).max(0.0);
    let twow = (if one_aged_pension { agiw - 0.5 * shared - penexc } else { agiw - 0.5 * (shared + penexc) }).max(0.0);
    let mut twoear = if y <= 1991 {
        0.0
    } else if let Some((limit, amount)) = p.two_earner_high {
        (if f.agi <= limit { p.two_earner } else { amount }).min(twoh).min(twow)
    } else {
        p.two_earner.min(twoh).min(twow)
    };
    if !(is_joint && f.agi > 0.0 && agih * agiw > 0.0) {
        twoear = 0.0;
    }
    md_agi = (md_agi - twoear).max(0.0);

    // ---- capital gains (discontinued 1992) ----
    let capgn = i.stcg + i.ltcg;
    if let Some(share) = p.cg_share {
        if capgn > 0.0 {
            md_agi -= share * capgn;
        }
    } else if let Some(c) = &p.cg_capped {
        let cap = if is_joint { c.cap_joint } else { c.cap_other };
        let threshold = if is_joint { c.threshold_joint } else { c.threshold_other };
        let astep = (c.share * capgn).min(cap);
        let bstep = ((f.agi - capgn) - threshold).max(0.0) * c.reduction_rate;
        let capded = (astep - bstep).max(0.0);
        if capgn > 0.0 {
            md_agi -= capded;
        }
    }

    // ---- standard deduction ----
    let agi_pos = md_agi.max(0.0);
    let stded = match &p.standard {
        Standard::CapPerExemption(cap) => (txpded * cap).min(p.standard_rate * agi_pos),
        Standard::PerExemption { floor, ceiling, aged: aged_amount } => {
            let base = (p.standard_rate * md_agi).max(txpded * floor).min(txpded * ceiling);
            base + aged_amount.map_or(0.0, |amount| amount * aged)
        }
        Standard::Limits { single, joint } => {
            let (floor, ceiling) = if is_joint || is_hoh { *joint } else { *single };
            (p.standard_rate * md_agi).max(floor).min(ceiling)
        }
        Standard::Flat(amounts) => amounts[fs as usize],
    };

    // ---- itemized deduction ----
    let xitded = if y <= 1986 {
        (pre1987_gross_itemized(i, f) - salt).max(f.pre87.zbr)
    } else {
        let smg = f.salt_capped + i.mortgage;
        let gate = f.itemizes && smg > 0.0;
        let mut base = (smg - salt).max(0.0);
        if let Some((threshold, _)) = &p.itemized_threshold {
            let phas92 = match threshold {
                Threshold::ByStatus(by_status) => by_status[fs as usize],
                Threshold::One(amount) => amount / sep,
            };
            let high = (f.itemized_deduction - salt * f.itemized_deduction / smg.max(1e-9)).max(0.0);
            if md_agi > phas92 && smg > 0.0 {
                base = high;
            }
        }
        if let Some((rate, thresholds)) = &p.itemized_phaseout {
            base = (base - rate * (f.agi - thresholds[fs as usize]).max(0.0)).max(0.0);
        }
        if gate { base } else { 0.0 }
    };
    let deduc = if xitded >= stded { xitded } else { stded };

    // ---- exemption ----
    let exemps_count = federal_exemption_count(y, i);
    let xmp = p.personal_exemption;
    let mut exemp = match p.aged_exemption {
        None => exemps_count * xmp,
        Some(aged_amount) => {
            let blage = aged_amount * aged;
            let base_exemp = txp + i.depx;
            let mut exemp = blage + base_exemp * xmp;
            if let Some(ph) = &p.exemption_phaseout {
                let replaced = if single_or_separate { tiered_replace(f.agi, &ph.single) } else { tiered_replace(f.agi, &ph.joint) };
                if let Some(replaced) = replaced {
                    if f.agi > ph.floor {
                        exemp = if y >= 2012 && replaced == 0.0 { 0.0 } else { blage + base_exemp * replaced };
                    }
                }
            }
            exemp
        }
    };
    // A dependent filer gets no exemptions from 2008.
    if p.aged_exemption.is_some() && y >= 2008 && i.is_dependent_filer() {
        exemp = 0.0;
    }
    let taxinc = (md_agi - deduc - exemp).max(0.0);

    // ---- tax ----
    let mut statax = match &p.schedule {
        Schedule::One(b) => b.tax(taxinc),
        Schedule::Groups { single_or_separate: s, joint_or_hoh: j } => if single_or_separate { s.tax(taxinc) } else { j.tax(taxinc) },
    };
    if let Some((threshold, rate)) = p.surtax {
        if md_agi > threshold {
            statax += rate * (i.stcg + i.ltcg).max(0.0);
        }
    }
    let taxbc = statax;

    // ---- credits ----
    let mut earncr = 0.0;
    if let Some(share) = p.eitc_share {
        earncr = share * f.eitc;
        if y >= 2022 && i.depx < 1.0 && !is_joint {
            earncr = f.eitc;
        }
    }
    earncr = taxbc.min(earncr);
    let pretax = (taxbc - earncr).max(0.0);
    let mut md_statax = pretax;

    // Refundable EITC.
    let mut refcr = 0.0;
    if let Some(r) = &p.refundable {
        let base_refcr = (r.rate * f.eitc - taxbc).max(0.0);
        if y <= 2008 {
            refcr = if i.depx > 0.0 { base_refcr } else { 0.0 };
        } else if y <= 2019 {
            refcr = base_refcr;
        } else {
            // From 2022 married childless filers use the refundable share like parents.
            let parent_like = i.depx > 0.0 || (y >= 2022 && is_joint);
            refcr = if parent_like { base_refcr } else { 0.0 };
            let wl2 = r.childless_floor.unwrap_or(0.0).min(f.eitc);
            let childless_gate = pretax < 1.0 && !parent_like;
            let childless_refcr = if taxbc < wl2 { wl2 - taxbc } else { 0.0 };
            if childless_gate {
                refcr = childless_refcr;
            }
        }
    }

    // Nonrefundable Child/Dependent Care Credit.
    let phased = |unit: f64, units: f64, start: f64, step: f64| unit * (units - ((f.agi - start) / step).max(0.0)).max(0.0);
    let (chr_, fagim) = match &p.care {
        CareRate::None => (0.0, 0.0),
        CareRate::Y2000 { unit, units, start, step } => (phased(*unit, *units, start / sep, step / sep), 0.0),
        CareRate::Y2001 { unit, units, start, step_numerator, step_divisor } => {
            (phased(*unit, *units, start / sep, step_numerator / (step_divisor * sep)), 0.0)
        }
        CareRate::Y2019 { joint, other } => {
            let c = if is_joint { joint } else { other };
            (phased(c.unit, c.units, c.start, c.step), c.limit)
        }
    };
    let comnew52 = if y <= 1979 { f.regular_tax } else if y <= 1986 { f.fiitax } else { f.regular_tax };
    let chcr = chr_ * f.federal_chcr.min(comnew52.max(0.0));
    md_statax = (md_statax - chcr).max(0.0);

    // Poverty Level Credit (1997+, nonrefundable).
    let mut ptcr = 0.0;
    if let Some((base, per_additional, rate)) = p.poverty {
        let xlin3 = base + per_additional * (exemps_count - 1.0);
        let xlin4 = f.agi.max(f.earned_income);
        if xlin3 >= xlin4 && !i.is_dependent_filer() {
            ptcr = rate * f.earned_income;
        }
    }
    md_statax = (md_statax - ptcr).max(0.0);

    // Senior tax credit (2022+): nonrefundable, for returns with a taxpayer 65 or older below the AGI limit.
    let mut senior = 0.0;
    if let Some(s) = &p.senior {
        let limit = if is_joint || is_hoh { s.limit_joint_or_hoh } else { s.limit_other };
        let amount = if is_joint {
            if aged >= 2.0 { s.joint_two } else if aged == 1.0 { s.joint_one } else { 0.0 }
        } else if is_hoh {
            if aged > 0.0 { s.hoh } else { 0.0 }
        } else if aged > 0.0 {
            s.single
        } else {
            0.0
        };
        senior = if f.agi < limit { amount.min(md_statax.max(0.0)) } else { 0.0 };
        md_statax -= senior;
    }
    md_statax -= refcr;

    // 2019+ refundable portion of the Child/Dependent Care Credit.
    if y >= 2019 {
        let chref = if f.agi <= fagim { (chcr - taxbc).max(0.0) } else { 0.0 };
        md_statax -= chref;
    }
    if let Some(c) = &p.ctc {
        let mut ctc = if f.agi <= c.agi_cap { c.amount * i.dep6 } else { 0.0 };
        if let Some((threshold, increment, rate)) = c.phaseout {
            let steps = ((f.agi - threshold).max(0.0) / increment).ceil();
            ctc = (c.amount * i.dep6 - rate * steps).max(0.0);
        }
        md_statax -= ctc;
    }
    let _ = senior;
    md_statax
}
