//! Connecticut income tax (port of `calculators.states.ct`).

use std::path::Path;

use crate::common::Brackets;
use crate::federal::{Fed, Inp};
use crate::params::{self, get, load, num, resolve_year, Fs, YearParams, STATUS_KEYS};
use crate::states::{dividend_adjustment, household_income, law_year_is_exact, Interp, Tiers};

struct Exemption {
    amount: f64,
    start: f64,
}

struct Exemptions {
    single: Exemption,
    joint: Exemption,
    hoh: Exemption,
    separate: Option<Exemption>,
}

struct Pension {
    joint: Vec<(f64, f64)>,
    non_joint: Vec<(f64, f64)>,
}

enum PensionDeduction {
    None,
    Rates(Pension),
    Share { share: f64, limit_single: f64, limit_joint: f64 },
}

struct Step {
    start: f64,
    amount: f64,
    increment: f64,
    maximum: f64,
}

type StepTable = [Step; 4];

struct AddBack {
    threshold: f64,
    denom: f64,
    per_1000: f64,
    cap: f64,
}

struct Recapture {
    rate: f64,
    threshold: f64,
    max: f64,
}

enum Extra {
    Steps { add_back: StepTable, recapture: Vec<StepTable> },
    Legacy { add_back: [AddBack; 4], single: Recapture, hoh: Recapture },
    None,
}

enum Income {
    /// Before 1991: tax on capital gains, dividends and interest only.
    Investment { rate: f64, exclusion: Option<f64>, rates: Vec<(f64, f64)> },
    Flat { rate: f64, cg: Option<(f64, f64, f64, Vec<(f64, f64)>)>, credit: Credit },
    Brackets { single: Brackets, hoh: Brackets, joint: Brackets, extra: Extra, credit: Credit },
}

struct Credit {
    early: bool,
    year_2022: bool,
    rates: Vec<f64>,
    interp_single: Interp,
    interp_hoh: Interp,
    interp_joint: Interp,
    tiers_single: Tiers,
    tiers_hoh: Tiers,
    tiers_joint: Tiers,
}

struct Amt {
    separate_addback_start: f64,
    separate_addback_end: f64,
    separate_addback_max: f64,
    separate_addback_rate: f64,
    exemption_married: f64,
    exemption_other: f64,
    phaseout_married: f64,
    phaseout_other: f64,
    phaseout_rate: f64,
    rate_breakpoint: f64,
    tax_at_breakpoint: f64,
    offset: f64,
    rate_low: f64,
    rate_high: f64,
    tentative_share: f64,
    preference_share: f64,
    fed_sep_cap: f64,
    fed_sep_threshold: f64,
    fed_sep_rate: f64,
    tmt: Option<Tmt>,
}

struct Tmt {
    exemption: [f64; 4],
    threshold: [f64; 4],
    phaseout_rate: f64,
    rate_bp: f64,
    rate_lo: f64,
    rate_hi: f64,
    rate_0_ceiling: [f64; 4],
    rate_15_ceiling: [f64; 4],
    cg_rate_0: f64,
    cg_rate_15: f64,
    top_addition: f64,
}

struct Property {
    cap: f64,
    start_single: f64,
    start_hoh: f64,
    start_joint: f64,
    width: f64,
    rate: f64,
    rows: Interp,
    requires: bool,
}

pub struct CtParams {
    year: i64,
    loss_limit: f64,
    pension: PensionDeduction,
    ss_limit_single: f64,
    ss_limit_joint: f64,
    ss_base: [f64; 3],
    ss_share: f64,
    exemption_per_person: Option<f64>,
    exemptions: Option<Exemptions>,
    income: Income,
    amt: Option<Amt>,
    property: Option<Property>,
    eitc: Option<(f64, f64)>,
    dividend_adjustment: f64,
}

fn rows_pairs(v: &serde_yaml::Value) -> Result<Vec<(f64, f64)>, String> {
    Ok(params::brackets(v)?.into_iter().map(|r| (r[0], r[1])).collect())
}

fn step_value(income: f64, rows: &[(f64, f64)]) -> f64 {
    let mut out = rows[0].1;
    for (threshold, value) in &rows[1..] {
        if income > *threshold {
            out = *value;
        }
    }
    out
}

fn flat_rate_step(income: f64, rows: &[(f64, f64)]) -> f64 {
    for (threshold, rate) in &rows[..rows.len() - 1] {
        if income <= *threshold {
            return *rate;
        }
    }
    rows[rows.len() - 1].1
}

fn step_table(v: &serde_yaml::Value) -> Result<StepTable, String> {
    let read = |key: &str| -> Result<Step, String> {
        let s = get(v, key)?.as_sequence().ok_or("expected a list")?;
        Ok(Step { start: num(&s[0])?, amount: num(&s[1])?, increment: num(&s[2])?, maximum: num(&s[3])? })
    };
    Ok([read("single")?, read("married_joint")?, read("married_separate")?, read("head_of_household")?])
}

fn stepped(table: &StepTable, fs: Fs, agi: f64) -> f64 {
    let s = &table[fs as usize];
    let steps = ((agi - s.start).max(0.0) / s.increment).ceil();
    s.maximum.min(steps * s.amount)
}

impl CtParams {
    pub fn load(root: &Path, year: i64) -> Result<Self, String> {
        let doc = load(root, "states/ct/income_tax.yaml")?;
        law_year_is_exact(root, &doc, year)?;
        let amt_doc = load(root, "national/amt.yaml")?;
        let cg_doc = load(root, "national/capital_gains.yaml")?;
        let p = YearParams::new(&doc, year);
        let opt = |key: &str| -> Result<Option<f64>, String> { Ok(if p.in_effect(key)? { Some(p.f(key)?) } else { None }) };
        let list = |v: &serde_yaml::Value| -> Result<Vec<f64>, String> { v.as_sequence().ok_or("expected a list")?.iter().map(num).collect() };

        let pension = if year >= 2019 {
            if p.in_effect("pension_deduction_rates")? {
                let r = p.value("pension_deduction_rates", &[])?;
                PensionDeduction::Rates(Pension { joint: rows_pairs(get(r, "joint")?)?, non_joint: rows_pairs(get(r, "non_joint")?)? })
            } else {
                PensionDeduction::Share {
                    share: p.f("pension_deduction_share")?,
                    limit_single: num(get(get(&doc, "pension_deduction_agi_limit")?, "single")?)?,
                    limit_joint: num(get(get(&doc, "pension_deduction_agi_limit")?, "joint")?)?,
                }
            }
        } else {
            PensionDeduction::None
        };
        let ss_base_v = get(&doc, "social_security_base_amount")?;
        let exemption = |status: &str| -> Result<Exemption, String> {
            let terms = p.value("exemption", &[status])?;
            Ok(Exemption { amount: num(get(terms, "amount")?)?, start: num(get(terms, "phaseout_start")?)? })
        };
        let (exemption_per_person, exemptions) = if p.in_effect("exemption_per_person")? {
            (Some(p.f("exemption_per_person")?), None)
        } else {
            (
                None,
                Some(Exemptions {
                    single: exemption("single")?,
                    joint: exemption("married_joint")?,
                    hoh: exemption("head_of_household")?,
                    separate: if year >= 2000 { Some(exemption("married_separate")?) } else { None },
                }),
            )
        };
        let credit = || -> Result<Credit, String> {
            let rates = list(p.value("personal_credit_rates", &[])?)?;
            let single = list(p.value("personal_credit_thresholds_single", &[])?)?;
            let hoh = list(p.value("personal_credit_thresholds_head_of_household", &[])?)?;
            let joint = list(p.value("personal_credit_thresholds_joint", &[])?)?;
            let zip = |t: &[f64]| -> Vec<Vec<f64>> { t.iter().zip(&rates).map(|(a, b)| vec![*a, *b]).collect() };
            Ok(Credit {
                early: year <= 1994,
                year_2022: year >= 2022,
                interp_single: Interp::new(&zip(&single)),
                interp_hoh: Interp::new(&zip(&hoh)),
                interp_joint: Interp::new(&zip(&joint)),
                tiers_single: Tiers::new(single.clone(), false),
                tiers_hoh: Tiers::new(hoh.clone(), false),
                tiers_joint: Tiers::new(joint.clone(), false),
                rates,
            })
        };
        let income = if p.in_effect("capital_gains_rate")? {
            Income::Investment {
                rate: p.f("capital_gains_rate")?,
                exclusion: opt("capital_gains_exclusion")?,
                rates: rows_pairs(p.value("investment_income_rates", &[])?)?,
            }
        } else if p.in_effect("flat_rate")? {
            let cg = match p.opt("capital_gains_tax")? {
                Some(c) => Some((
                    num(get(c, "rate")?)?,
                    num(get(c, "exemption_per_unit")?)?,
                    num(get(c, "agi_cap_share")?)?,
                    rows_pairs(p.value("investment_income_rates", &[])?)?,
                )),
                None => None,
            };
            Income::Flat { rate: p.f("flat_rate")?, cg, credit: credit()? }
        } else {
            let b = p.value("brackets", &[])?;
            let mk = |key: &str| -> Result<Brackets, String> { Ok(Brackets::new(&params::brackets(get(b, key)?)?)) };
            let extra = if p.in_effect("add_back_steps")? {
                let recapture_v = p.value("recapture_steps", &[])?;
                let mut tiers = Vec::new();
                for (_, tier) in recapture_v.as_mapping().ok_or("recapture_steps must be a mapping")? {
                    let in_effect = match tier.get("in_effect") {
                        Some(v) => v.as_bool().ok_or("expected a boolean")?,
                        None => true,
                    };
                    if in_effect {
                        tiers.push(step_table(tier)?);
                    }
                }
                Extra::Steps { add_back: step_table(p.value("add_back_steps", &[])?)?, recapture: tiers }
            } else if p.in_effect("add_back")? {
                let ab = p.value("add_back", &[])?;
                let read = |key: &str| -> Result<AddBack, String> {
                    let a = get(ab, key)?;
                    Ok(AddBack {
                        threshold: num(get(a, "threshold")?)?,
                        denom: num(get(a, "denom")?)?,
                        per_1000: num(get(a, "per_1000")?)?,
                        cap: num(get(a, "cap")?)?,
                    })
                };
                let rc = p.value("recapture", &[])?;
                let rec = |key: &str| -> Result<Recapture, String> {
                    let r = get(rc, key)?;
                    Ok(Recapture { rate: num(get(r, "rate")?)?, threshold: num(get(r, "agi_threshold")?)?, max: num(get(r, "max")?)? })
                };
                Extra::Legacy {
                    add_back: [read("single")?, read("married_joint")?, read("married_separate")?, read("head_of_household")?],
                    single: rec("single")?,
                    hoh: rec("head_of_household")?,
                }
            } else {
                Extra::None
            };
            Income::Brackets { single: mk("single")?, hoh: mk("head_of_household")?, joint: mk("joint")?, extra, credit: credit()? }
        };
        let amt = if year >= 1993 {
            let a = get(&doc, "amt")?;
            let n = |key: &str| -> Result<f64, String> { num(get(a, key)?) };
            let by_status = |doc: &serde_yaml::Value, name: &str| -> Result<[f64; 4], String> {
                let node = get(doc, name)?;
                let mut out = [0.0; 4];
                for (k, key) in STATUS_KEYS.iter().enumerate() {
                    out[k] = num(resolve_year(get(node, key)?, year)?)?;
                }
                Ok(out)
            };
            let fed = YearParams::new(&amt_doc, year);
            let cg = YearParams::new(&cg_doc, year);
            let tmt = if year >= 2013 {
                Some(Tmt {
                    exemption: by_status(&amt_doc, "exemption")?,
                    threshold: by_status(&amt_doc, "exemption_phaseout_threshold")?,
                    phaseout_rate: fed.f("exemption_phaseout_rate")?,
                    rate_bp: fed.f("rate_breakpoint")?,
                    rate_lo: fed.f("rate_below_breakpoint")?,
                    rate_hi: fed.f("rate_above_breakpoint")?,
                    rate_0_ceiling: by_status(&cg_doc, "rate_0_ceiling")?,
                    rate_15_ceiling: by_status(&amt_doc, "cg_rate_15_ceiling")?,
                    cg_rate_0: cg.f("rate_0")?,
                    cg_rate_15: cg.f("rate_15")?,
                    top_addition: fed.f("gains_top_rate_addition")?,
                })
            } else {
                None
            };
            Some(Amt {
                separate_addback_start: n("separate_addback_start")?,
                separate_addback_end: n("separate_addback_end")?,
                separate_addback_max: n("separate_addback_max")?,
                separate_addback_rate: n("separate_addback_rate")?,
                exemption_married: n("exemption_married")?,
                exemption_other: n("exemption_other")?,
                phaseout_married: n("exemption_phaseout_married")?,
                phaseout_other: n("exemption_phaseout_other")?,
                phaseout_rate: n("exemption_phaseout_rate")?,
                rate_breakpoint: n("rate_breakpoint")?,
                tax_at_breakpoint: n("tax_at_breakpoint")?,
                offset: num(resolve_year(get(a, "breakpoint_offset")?, year)?)?,
                rate_low: n("rate_low")?,
                rate_high: n("rate_high")?,
                tentative_share: p.num_at("amt", &["tentative_tax_share"])?,
                preference_share: if year > 1993 { p.num_at("amt", &["preference_income_share"])? } else { 0.0 },
                fed_sep_cap: num(resolve_year(get(get(&amt_doc, "exemption")?, "married_separate")?, year)?)?,
                fed_sep_threshold: fed.f("separate_return_addback_threshold")?,
                fed_sep_rate: fed.f("separate_return_addback_rate")?,
                tmt,
            })
        } else {
            None
        };
        let property = if p.in_effect("property_credit_cap")? {
            let steps = list(get(&doc, "property_credit_phaseout_steps")?)?;
            let width = num(get(&doc, "property_credit_phaseout_step_width")?)?;
            let mut rows: Vec<Vec<f64>> = steps.iter().enumerate().map(|(k, s)| vec![width * k as f64, *s]).collect();
            let final_row = list(get(&doc, "property_credit_phaseout_final")?)?;
            rows.push(final_row);
            let (start_single, start_hoh, start_joint) = if year >= 1997 {
                (
                    p.num_at("property_credit_phaseout_start", &["single"])?,
                    p.num_at("property_credit_phaseout_start", &["head_of_household"])?,
                    p.num_at("property_credit_phaseout_start", &["joint"])?,
                )
            } else {
                (0.0, 0.0, 0.0)
            };
            Some(Property {
                cap: p.f("property_credit_cap")?,
                start_single,
                start_hoh,
                start_joint,
                width,
                rate: num(get(&doc, "property_credit_phaseout_rate")?)?,
                rows: Interp::new(&rows),
                requires: p.value("property_credit_requires_dependent_or_elderly", &[])?.as_bool().ok_or("expected a boolean")?,
            })
        } else {
            None
        };
        Ok(Self {
            year,
            loss_limit: if year >= 1987 { num(resolve_year(get(&cg_doc, "net_capital_loss_limit")?, year)?)? } else { 0.0 },
            pension,
            ss_limit_single: if year >= 1985 { p.num_at("social_security_agi_limit", &["single"])? } else { 0.0 },
            ss_limit_joint: if year >= 1985 { p.num_at("social_security_agi_limit", &["joint"])? } else { 0.0 },
            ss_base: [num(get(ss_base_v, "joint")?)?, num(get(ss_base_v, "married_separate")?)?, num(get(ss_base_v, "single")?)?],
            ss_share: num(get(&doc, "social_security_partial_share")?)?,
            exemption_per_person,
            exemptions,
            income,
            amt,
            property,
            eitc: if p.in_effect("eitc_federal_share")? { Some((p.f("eitc_federal_share")?, p.f("eitc_child_bonus")?)) } else { None },
            dividend_adjustment: dividend_adjustment(root)?,
        })
    }
}

fn exemption_amount(e: &Exemption, year: i64, agi: f64) -> f64 {
    if year >= 2022 {
        // $1,000 less for each $1,000 (or part) of AGI over the start.
        let steps = ((agi - e.start).max(0.0) / 1000.0).ceil();
        (e.amount - 1000.0 * steps).max(0.0).min(e.amount)
    } else {
        (e.amount - (agi - e.start)).max(0.0).min(e.amount)
    }
}

fn personal_credit(c: &Credit, fs: Fs, agi: f64, sep: f64) -> f64 {
    let is_married = matches!(fs, Fs::Joint | Fs::Separate);
    if c.early {
        match fs {
            Fs::Hoh => c.interp_hoh.eval(agi),
            Fs::Joint => c.interp_joint.eval(agi),
            _ => c.interp_single.eval(agi),
        }
    } else if c.year_2022 {
        // The rate of the first threshold reached, not interpolated.
        match fs {
            Fs::Hoh => c.rates[c.tiers_hoh.index(agi)],
            _ if is_married => c.rates[c.tiers_joint.index(agi * sep)],
            _ => c.rates[c.tiers_single.index(agi)],
        }
    } else {
        match fs {
            Fs::Hoh => c.interp_hoh.eval(agi),
            _ if is_married => c.interp_joint.eval(agi * sep),
            _ => c.interp_single.eval(agi),
        }
    }
}

pub fn tax(i: &Inp, f: &Fed, p: &CtParams) -> f64 {
    let y = p.year;
    let fs = i.fs();
    let is_joint = fs == Fs::Joint;
    let is_hoh = fs == Fs::Hoh;
    let is_sep = fs == Fs::Separate;
    let sep = i.sepret();
    let txp = i.taxpayer_count();
    let aged = i.aged_count();

    // ---- AGI: federal AGI less the pension deduction (2019+) and exempt Social Security benefits (1985+) ----
    let mut agi = f.agi;
    match &p.pension {
        PensionDeduction::Rates(r) => {
            let rate = if is_joint { step_value(agi, &r.joint) } else { step_value(agi, &r.non_joint) };
            agi -= rate * i.pensions;
        }
        PensionDeduction::Share { share, limit_single, limit_joint } => {
            let limit = if is_joint { *limit_joint } else { *limit_single };
            agi -= (1.0 - (agi - limit) / limit).clamp(0.0, 1.0) * share * i.pensions;
        }
        PensionDeduction::None => {}
    }
    if y >= 1985 {
        let ssbmax = if is_joint || is_hoh { p.ss_limit_joint } else { p.ss_limit_single };
        let excl = if is_joint { p.ss_base[0] } else if is_sep { p.ss_base[1] } else { p.ss_base[2] };
        let taxable_ss = f.taxable_social_security;
        let benefits = i.gssi;
        let partial = if y <= 1999 {
            let adjustments = f.se_adjustment - i.nonprop;
            let hh = household_income(i, p.dividend_adjustment);
            let kept = 0.5 * (0.5 * (hh - 0.5 * benefits - adjustments - excl).max(0.0)).min(0.5 * benefits);
            (taxable_ss - kept).max(0.0)
        } else if y >= 2022 {
            // A share of the lesser of gross benefits and combined income above the federal base amount (IRC section 86).
            let combined_income = agi - taxable_ss + 0.5 * benefits;
            let combined_excess = (combined_income - excl).max(0.0);
            (taxable_ss - p.ss_share * benefits.min(combined_excess)).max(0.0)
        } else if excl > 0.0 {
            taxable_ss - p.ss_share * benefits.min(excl)
        } else {
            0.0
        };
        if taxable_ss > 0.0 {
            agi -= if agi < ssbmax { taxable_ss } else { partial };
        }
    }
    let ct_agi = agi;

    // ---- exemption ----
    let exemp = if let Some(per_person) = p.exemption_per_person {
        per_person * (txp + aged)
    } else {
        let e = p.exemptions.as_ref().expect("exemptions");
        let chosen = if is_joint {
            &e.joint
        } else if is_hoh {
            &e.hoh
        } else if let (true, Some(sepx)) = (is_sep, &e.separate) {
            sepx
        } else {
            &e.single
        };
        exemption_amount(chosen, y, ct_agi)
    };

    // ---- income tax ----
    let fullcg = i.stcg + i.ltcg;
    let mut gain = if y <= 1986 { f.pre87.capgn } else { fullcg.max(-p.loss_limit / sep) };
    let divint = i.dividends + i.intrec;
    let mut statax;
    match &p.income {
        Income::Investment { rate, exclusion, rates } => {
            if let Some(share) = exclusion {
                gain = (gain - share * i.ltcg.min(gain).max(0.0)).max(0.0);
            }
            let cgtax = (rate * (gain - exemp)).max(0.0);
            let rate_expr = flat_rate_step(ct_agi, rates);
            let divtax = rate_expr * if y < 1983 { i.dividends } else { divint };
            statax = cgtax + divtax;
        }
        Income::Flat { rate, cg, credit } => {
            let taxinc = (ct_agi - exemp).max(0.0);
            let xtax = taxinc * rate;
            let (mut divtax, mut cgtax) = (0.0, 0.0);
            if let Some((cg_rate, per_unit, cap_share, rates)) = cg {
                divtax = flat_rate_step(ct_agi, rates) * divint;
                cgtax = (cap_share * ct_agi).min((gain - per_unit * (txp + aged)) * cg_rate).max(0.0);
            }
            let credp = personal_credit(credit, fs, ct_agi, sep);
            statax = xtax * (1.0 - credp);
            if cg.is_some() {
                statax += cgtax + divtax;
            }
        }
        Income::Brackets { single, hoh, joint, extra, credit } => {
            let taxinc = (ct_agi - exemp).max(0.0);
            let mut xtax = if is_hoh { hoh.tax(taxinc) } else if is_joint { joint.tax(taxinc) } else { single.tax(taxinc) };
            match extra {
                Extra::Steps { add_back, recapture } => {
                    xtax += stepped(add_back, fs, ct_agi);
                    for tier in recapture {
                        xtax += stepped(tier, fs, ct_agi);
                    }
                }
                Extra::Legacy { add_back, single: rs, hoh: rh } => {
                    let a = &add_back[fs as usize];
                    let stax = a.cap.min(a.per_1000 * (ct_agi - a.threshold).max(0.0) / a.denom);
                    let rtax_single = (rs.rate * (ct_agi - rs.threshold).max(0.0)).min(rs.max);
                    let rtax_hoh = (rh.rate * (ct_agi - rh.threshold).max(0.0)).min(rh.max);
                    let rtax_joint = (rs.rate * (ct_agi - 2.0 * rs.threshold).max(0.0)).min(2.0 * rs.max);
                    let rtax = if is_hoh { rtax_hoh } else if is_joint { rtax_joint } else { rtax_single };
                    xtax += stax + rtax;
                }
                Extra::None => {}
            }
            let credp = personal_credit(credit, fs, ct_agi, sep);
            statax = xtax * (1.0 - credp);
        }
    }

    // ---- minimum tax (1993+), only when there is federal AMT ----
    if let Some(a) = &p.amt {
        let xprefs_base = f.agi - if f.itemizes { i.mortgage } else { 0.0 };
        let sep_addback = a.fed_sep_cap.min(a.fed_sep_rate * (xprefs_base - a.fed_sep_threshold).max(0.0));
        let xprefs_raw = if is_sep { xprefs_base + sep_addback } else { xprefs_base };
        let xprefs = if is_sep && xprefs_raw > a.separate_addback_start {
            if xprefs_raw > a.separate_addback_end {
                xprefs_raw + a.separate_addback_max
            } else {
                xprefs_raw + a.separate_addback_rate * (xprefs_raw - a.separate_addback_start)
            }
        } else {
            xprefs_raw
        };
        let married = is_joint || is_sep;
        let xemp_base = if married { a.exemption_married / sep } else { a.exemption_other };
        let ln9_thr = if married { a.phaseout_married / sep } else { a.phaseout_other };
        let xln9 = (xprefs - ln9_thr).max(0.0) * a.phaseout_rate;
        let xemp = (xemp_base - xln9).max(0.0);
        let xln11 = (xprefs - xemp).max(0.0);
        let ln11_bp = a.rate_breakpoint / sep;
        let offset = a.offset / sep;
        let xln12_hi = a.tax_at_breakpoint / sep + (xln11 - ln11_bp).max(0.0) * a.rate_high - offset;
        let xln12_lo = xln11 * a.rate_low;
        let xln12 = if xln11 > ln11_bp { xln12_hi } else { xln12_lo };
        let (amt_final, amcred_final);
        if y <= 1993 {
            amt_final = (xln12 * a.tentative_share - statax).max(0.0);
            amcred_final = 0.0;
        } else if y <= 2012 {
            amt_final = ((xln12 * a.tentative_share).min(xprefs * a.preference_share) - statax).max(0.0);
            amcred_final = 0.0;
        } else {
            let t = a.tmt.as_ref().expect("federal tentative minimum tax parameters");
            let tmt = federal_tmt(a, t, i, f, fs, sep);
            let amt1 = a.tentative_share * tmt;
            let amt2 = a.preference_share * xprefs_raw;
            let amtfin = amt1.min(amt2);
            amt_final = (statax - amtfin).max(0.0);
            amcred_final = (xln12 * a.tentative_share).min(amt_final);
        }
        let gate = xln11 > 0.0 && f.amt > 0.0;
        let amcred = if gate { amcred_final } else { 0.0 };
        let amt = if gate { amt_final } else { 0.0 };
        statax = (statax - amcred).max(0.0);
        statax = (statax + amt).max(0.0);
    }

    // ---- property tax credit (1996+) ----
    let mut pcred = 0.0;
    if let Some(pc) = &p.property {
        pcred = pc.cap.min(i.proptax);
        if y >= 1997 {
            let phse = if fs == Fs::Single { pc.start_single } else if is_hoh { pc.start_hoh } else { pc.start_joint / sep };
            let agix = ct_agi.max(0.0);
            if y >= 2022 {
                let steps = ((agix - phse).max(0.0) / pc.width).ceil();
                let pct = (pc.rate * steps).clamp(0.0, 1.0);
                pcred *= 1.0 - pct;
            } else {
                let pct = pc.rows.eval(agix - phse);
                pcred -= pct * pcred;
            }
        }
        if pc.requires && i.depx < 1.0 && aged < 1.0 {
            pcred = 0.0;
        }
        pcred = pcred.max(0.0);
    }
    statax = (statax - pcred).max(0.0);

    // ---- earned income credit (2011+) ----
    let mut earncr = 0.0;
    if let Some((share, bonus)) = p.eitc {
        let eitc_fed = f.eitc.max(0.0);
        earncr = share * eitc_fed;
        if bonus != 0.0 && eitc_fed > 0.0 && i.dep18 > 0.0 {
            earncr += bonus;
        }
    }
    statax - earncr
}

/// Reconstruct the federal tentative minimum tax (2013 on).
fn federal_tmt(a: &Amt, t: &Tmt, i: &Inp, f: &Fed, fs: Fs, sepret: f64) -> f64 {
    let fsi = fs as usize;
    let amt_income = f.agi - if f.itemizes { i.mortgage } else { 0.0 };
    // Separate returns add back part of AMT income, up to the separate exemption.
    let sep_addback = t.exemption[2].min(a.fed_sep_rate * (amt_income - a.fed_sep_threshold).max(0.0));
    let tmt_income = if sepret == 2.0 { amt_income + sep_addback } else { amt_income };
    let exemption_after = (t.exemption[fsi] - t.phaseout_rate * (tmt_income - t.threshold[fsi]).max(0.0)).max(0.0);
    let amt_base = (tmt_income - exemption_after).max(0.0);
    let breakpoint_per_return = t.rate_bp / sepret;
    let backout = (t.rate_hi - t.rate_lo) * t.rate_bp / sepret;
    let ltg_capped = f.ltg.min(amt_base);
    let regular_ordinary = (f.taxable_income - f.ltg).max(0.0);
    let ordinary_amt_base = (amt_base - ltg_capped).max(0.0);
    let tentative_ordinary =
        if ordinary_amt_base <= breakpoint_per_return { ordinary_amt_base * t.rate_lo } else { ordinary_amt_base * t.rate_hi - backout };
    let zero_room = (t.rate_0_ceiling[fsi] - regular_ordinary).max(0.0);
    let zero_amount = zero_room.min(ltg_capped);
    let remaining = ltg_capped - zero_amount;
    let fifteen_room = (t.rate_15_ceiling[fsi] - regular_ordinary - zero_room).max(0.0);
    let top_slice = (remaining - fifteen_room).max(0.0);
    tentative_ordinary + t.cg_rate_0 * zero_amount + t.cg_rate_15 * remaining + t.top_addition * top_slice
}
