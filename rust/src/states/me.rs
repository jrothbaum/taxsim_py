//! Maine income tax (port of `calculators.states.me`).

use std::path::Path;

use crate::common::Brackets;
use crate::federal::{Fed, Inp};
use crate::params::{self, get, load, num, resolve_year, status_direct, Fs, YearParams};
use crate::states::{
    dividend_adjustment, federal_exemption_count, household_income, law_year_is_exact, pre1987_gross_itemized, unemployment_total,
    Interp,
};

struct PensionPhaseout {
    start: [f64; 4],
    width: [f64; 4],
}

struct Percent {
    rate: f64,
    single: (f64, f64),
    married: (f64, f64),
    separate: Option<(f64, f64)>,
}

struct MarriedOverride {
    amount: f64,
    aged_addition: f64,
}

struct SingleAndHoh {
    single: f64,
    hoh: f64,
    aged: f64,
}

struct Nonconforming {
    single: f64,
    joint: f64,
    hoh: f64,
    aged_single: f64,
    aged_married: f64,
}

struct Proportional {
    hoh: (f64, f64),
    per_taxpayer: (f64, f64),
}

struct DepExemption {
    amount: f64,
    young_multiplier: f64,
    starts: DepStarts,
    increment: f64,
    step: f64,
}

enum DepStarts {
    ByStatus { joint: f64, hoh: f64, separate: f64, single: f64 },
    Simple { joint: f64, other: f64 },
}

struct Deduction88 {
    pct: f64,
    floor: f64,
    caps: [f64; 4],
    single: Interp,
    hoh: Interp,
    married: Interp,
}

struct Tiers88 {
    one: Vec<(f64, f64, f64)>,
    two: Vec<(f64, f64, f64)>,
}

struct OldPropertyCredit {
    rent_share: f64,
    cap: f64,
    limit_one: f64,
    limit_two: f64,
    reduction: Option<(f64, f64)>,
    tiers: Option<Tiers88>,
}

struct Ptfc2013 {
    ceiling: f64,
    cap: f64,
    elderly_cap: f64,
    income_share: f64,
    benefit_share: f64,
}

struct PtfcCaps {
    base_single: f64,
    base_two: f64,
    base_more: f64,
    income_single: f64,
    income_two: f64,
    income_more: f64,
    cap: f64,
    elderly_cap: f64,
    income_share: f64,
    benefit_share: Option<f64>,
}

enum Ptfc {
    None,
    Y2013(Ptfc2013),
    Caps(PtfcCaps),
}

struct PtfcSchedule {
    single: f64,
    hoh_one_child: f64,
    joint_or_multi: f64,
    senior: f64,
    cap: f64,
    cap_senior: f64,
    income_rate: f64,
    rent_share: f64,
}

struct StfcOld {
    threshold: [f64; 3],
    amount: [f64; 4],
    step: f64,
}

struct StfcRow {
    base: f64,
    start: f64,
    increment: f64,
    step: f64,
}

struct StfcSchedule {
    rows: [StfcRow; 4],
    extra_joint: Vec<(f64, f64)>,
    extra_hoh: Vec<(f64, f64)>,
}

enum Stfc {
    None,
    Old(StfcOld),
    Schedule(StfcSchedule),
}

pub struct MeParams {
    year: i64,
    divisor: [f64; 3],
    dividend_exclusion: Option<f64>,
    pension: Option<(f64, Option<PensionPhaseout>)>,
    percent: Option<Percent>,
    married_override: Option<MarriedOverride>,
    single_and_hoh: Option<SingleAndHoh>,
    nonconforming: Option<Nonconforming>,
    itemized_cap: Option<f64>,
    proportional: Option<Proportional>,
    phaseout_start: Option<([f64; 4], [f64; 4], f64)>,
    personal_exemption: f64,
    exemption_phaseout: Option<([f64; 4], f64)>,
    table: Brackets,
    aif: f64,
    low_income_limit: Option<f64>,
    minimum_rate: Option<f64>,
    minimum_share: Option<f64>,
    child_rate: f64,
    refundable_cap: f64,
    dependent_per_child: Option<f64>,
    dependent_exemption: Option<DepExemption>,
    elderly_share: Option<f64>,
    credit_per_exemption: Option<f64>,
    rent_or_property: Option<(f64, f64)>,
    deduction_credits: Option<Deduction88>,
    old_property: Option<OldPropertyCredit>,
    eitc_rate: Option<f64>,
    eitc_childless: Option<f64>,
    eitc_rates_by_children: Option<(f64, f64)>,
    eitc_refundable: bool,
    ptfc_rent_share: f64,
    ptfc: Ptfc,
    ptfc_schedule: Option<PtfcSchedule>,
    stfc: Stfc,
    dividend_adjustment: f64,
}

impl MeParams {
    pub fn load(root: &Path, year: i64) -> Result<Self, String> {
        let doc = load(root, "states/me/income_tax.yaml")?;
        law_year_is_exact(root, &doc, year)?;
        let p = YearParams::new(&doc, year);
        let opt = |key: &str| -> Result<Option<f64>, String> { Ok(if p.in_effect(key)? { Some(p.f(key)?) } else { None }) };
        let pair = |v: &serde_yaml::Value, a: &str, b: &str| -> Result<(f64, f64), String> { Ok((num(get(v, a)?)?, num(get(v, b)?)?)) };
        let triples = |v: &serde_yaml::Value| -> Result<Vec<(f64, f64, f64)>, String> {
            Ok(params::matrix(v)?.into_iter().map(|r| (r[0], r[1], r[2])).collect())
        };
        let pairs = |v: &serde_yaml::Value| -> Result<Vec<(f64, f64)>, String> {
            Ok(params::matrix(v)?.into_iter().map(|r| (r[0], r[1])).collect())
        };

        let d = p.value("bracket_income_divisor", &[])?;
        let pension = match p.opt("pension_deduction_per_taxpayer")? {
            Some(amount) => Some((
                num(amount)?,
                match p.opt("pension_deduction_phaseout")? {
                    Some(po) => Some(PensionPhaseout { start: status_direct(get(po, "start")?)?, width: status_direct(get(po, "width")?)? }),
                    None => None,
                },
            )),
            None => None,
        };
        let percent = match p.opt("standard_deduction_percent")? {
            Some(sd) => Some(Percent {
                rate: num(get(sd, "rate")?)?,
                single: pair(get(sd, "single")?, "floor", "ceiling")?,
                married: pair(get(sd, "married")?, "floor", "ceiling")?,
                separate: match sd.get("separate") {
                    Some(s) => Some(pair(s, "floor", "ceiling")?),
                    None => None,
                },
            }),
            None => None,
        };
        let married_override = if percent.is_none() && year != 1988 && p.in_effect("standard_deduction_married_override")? {
            Some(MarriedOverride { amount: p.f("standard_deduction_married_override")?, aged_addition: p.f("standard_deduction_aged_addition")? })
        } else {
            None
        };
        let single_and_hoh = match p.opt("standard_deduction_single_and_hoh")? {
            Some(s) if percent.is_none() && year != 1988 => {
                Some(SingleAndHoh { single: num(get(s, "single")?)?, hoh: num(get(s, "head_of_household")?)?, aged: num(get(s, "aged")?)? })
            }
            _ => None,
        };
        let nonconforming = match p.opt("standard_deduction_nonconforming")? {
            Some(s) => Some(Nonconforming {
                single: num(get(s, "single")?)?,
                joint: num(get(s, "married_joint")?)?,
                hoh: num(get(s, "head_of_household")?)?,
                aged_single: num(get(s, "aged_single")?)?,
                aged_married: num(get(s, "aged_married")?)?,
            }),
            None => None,
        };
        let proportional = match p.opt("deduction_phaseout_proportional")? {
            Some(dp) => Some(Proportional {
                hoh: pair(get(dp, "head_of_household")?, "phased", "threshold")?,
                per_taxpayer: pair(get(dp, "per_taxpayer")?, "phased", "threshold")?,
            }),
            None => None,
        };
        let phaseout_start = if p.in_effect("deduction_phaseout_start")? {
            Some((
                status_direct(p.value("deduction_phaseout_start", &[])?)?,
                status_direct(get(&doc, "deduction_phaseout_range")?)?,
                p.f("phaseout_index")?,
            ))
        } else {
            None
        };
        let exemption_phaseout = if year >= 2018 {
            let start = status_direct(p.value("exemption_phaseout_start", &[])?)?;
            Some((
                [start[0] * p.f("phaseout_index")?, start[1] * p.f("phaseout_index")?, start[2] * p.f("phaseout_index")?, start[3] * p.f("phaseout_index")?],
                num(get(&doc, "exemption_phaseout_range")?)?,
            ))
        } else {
            None
        };
        let dependent_exemption = match p.opt("dependent_exemption_credit")? {
            Some(dep) if !p.in_effect("dependent_credit_per_child")? => {
                let starts = if dep.get("start_joint").is_some() && dep.get("start_hoh").is_some() {
                    DepStarts::ByStatus {
                        joint: num(get(dep, "start_joint")?)?,
                        hoh: num(get(dep, "start_hoh")?)?,
                        separate: num(get(dep, "start_separate")?)?,
                        single: num(get(dep, "start_single")?)?,
                    }
                } else {
                    DepStarts::Simple { joint: num(get(dep, "start_joint")?)?, other: num(get(dep, "start_other")?)? }
                };
                Some(DepExemption {
                    amount: num(get(dep, "amount")?)?,
                    young_multiplier: num(get(dep, "young_multiplier")?)?,
                    starts,
                    increment: num(get(dep, "increment")?)?,
                    step: num(get(dep, "step")?)?,
                })
            }
            _ => None,
        };
        let deduction_credits = match p.opt("deduction_credits")? {
            Some(c) => {
                let caps = get(c, "std_deduction_cap")?;
                let table = get(c, "exemption_table")?;
                Some(Deduction88 {
                    pct: num(get(c, "earned_income_pct")?)?,
                    floor: num(get(c, "earned_income_floor")?)?,
                    caps: [num(get(caps, "single")?)?, num(get(caps, "married_joint")?)?, num(get(caps, "married_separate")?)?, num(get(caps, "head_of_household")?)?],
                    single: Interp::new(&params::brackets(get(table, "single")?)?),
                    hoh: Interp::new(&params::brackets(get(table, "head_of_household")?)?),
                    married: Interp::new(&params::brackets(get(table, "married")?)?),
                })
            }
            None => None,
        };
        let old_property = if year <= 1988 {
            let rent_share = num(get(&doc, "property_tax_credit_rent_share")?)?;
            let cap = num(get(&doc, "property_tax_credit_cap")?)?;
            if year <= 1987 {
                let limits = get(&doc, "property_tax_credit_income_limit")?;
                Some(OldPropertyCredit {
                    rent_share,
                    cap,
                    limit_one: num(resolve_year(get(limits, "one_taxpayer")?, year)?)?,
                    limit_two: num(resolve_year(get(limits, "two_taxpayers")?, year)?)?,
                    reduction: match p.opt("property_tax_credit_reduction")? {
                        Some(r) => Some(pair(r, "rate", "income_floor")?),
                        None => None,
                    },
                    tiers: None,
                })
            } else {
                let t = p.value("property_tax_credit_tiers", &[])?;
                Some(OldPropertyCredit {
                    rent_share,
                    cap,
                    limit_one: 0.0,
                    limit_two: 0.0,
                    reduction: None,
                    tiers: Some(Tiers88 { one: triples(get(t, "one_taxpayer")?)?, two: triples(get(t, "two_taxpayers")?)? }),
                })
            }
        } else {
            None
        };
        let ptfc = if p.in_effect("property_tax_fairness_credit")? {
            let pf = p.value("property_tax_fairness_credit", &[])?;
            if pf.get("income_ceiling").is_some() {
                Ptfc::Y2013(Ptfc2013 {
                    ceiling: num(get(pf, "income_ceiling")?)?,
                    cap: num(get(pf, "cap")?)?,
                    elderly_cap: num(get(pf, "elderly_cap")?)?,
                    income_share: num(get(pf, "income_share")?)?,
                    benefit_share: num(get(pf, "benefit_share")?)?,
                })
            } else {
                let bc = get(pf, "base_cap")?;
                let ic = get(pf, "income_cap")?;
                Ptfc::Caps(PtfcCaps {
                    base_single: num(get(bc, "single")?)?,
                    base_two: num(get(bc, "two")?)?,
                    base_more: num(get(bc, "more")?)?,
                    income_single: num(get(ic, "single")?)?,
                    income_two: num(get(ic, "two")?)?,
                    income_more: num(get(ic, "more")?)?,
                    cap: num(get(pf, "cap")?)?,
                    elderly_cap: num(get(pf, "elderly_cap")?)?,
                    income_share: num(get(pf, "income_share")?)?,
                    benefit_share: match pf.get("benefit_share") {
                        Some(v) => Some(num(v)?),
                        None => None,
                    },
                })
            }
        } else {
            Ptfc::None
        };
        let ptfc_schedule = match p.opt("property_tax_fairness_credit_schedule")? {
            Some(d) => Some(PtfcSchedule {
                single: num(get(d, "single")?)?,
                hoh_one_child: num(get(d, "hoh_one_child")?)?,
                joint_or_multi: num(get(d, "joint_or_multi")?)?,
                senior: num(get(d, "senior")?)?,
                cap: num(get(d, "cap")?)?,
                cap_senior: num(get(d, "cap_senior")?)?,
                income_rate: num(get(d, "income_rate")?)?,
                rent_share: num(get(d, "rent_share")?)?,
            }),
            None => None,
        };
        let stfc = if p.in_effect("sales_tax_fairness_credit")? {
            let sc = p.value("sales_tax_fairness_credit", &[])?;
            let t = get(sc, "threshold")?;
            let amount = get(sc, "amount")?.as_sequence().ok_or("expected a list")?;
            Stfc::Old(StfcOld {
                threshold: [num(get(t, "single")?)?, num(get(t, "head_of_household")?)?, num(get(t, "married_joint")?)?],
                amount: [num(&amount[0])?, num(&amount[1])?, num(&amount[2])?, num(&amount[3])?],
                step: num(get(&doc, "stfc_phaseout_step")?)?,
            })
        } else if p.in_effect("sales_tax_fairness_credit_schedule")? {
            let d = p.value("sales_tax_fairness_credit_schedule", &[])?;
            let row = |key: &str| -> Result<StfcRow, String> {
                let r = get(d, key)?;
                Ok(StfcRow { base: num(get(r, "base")?)?, start: num(get(r, "start")?)?, increment: num(get(r, "increment")?)?, step: num(get(r, "step")?)? })
            };
            let additional = get(d, "additional")?;
            Stfc::Schedule(StfcSchedule {
                rows: [row("single")?, row("married_joint")?, row("married_separate")?, row("head_of_household")?],
                extra_joint: pairs(get(additional, "joint")?)?,
                extra_hoh: pairs(get(additional, "head_of_household")?)?,
            })
        } else {
            Stfc::None
        };
        let rates_by_children = match p.opt("eitc_rates_by_children")? {
            Some(r) => Some((num(get(r, "with_child")?)?, num(get(r, "childless")?)?)),
            None => None,
        };
        let eitc_rate = opt("eitc_rate")?;
        Ok(Self {
            year,
            divisor: [num(get(d, "married_joint")?)?, num(get(d, "head_of_household")?)?, num(get(d, "other")?)?],
            dividend_exclusion: opt("dividend_exclusion_per_taxpayer")?,
            pension,
            percent,
            married_override,
            single_and_hoh,
            nonconforming,
            itemized_cap: opt("itemized_deduction_cap")?,
            proportional,
            phaseout_start,
            personal_exemption: if year <= 2012 || year >= 2018 { p.f("personal_exemption")? } else { 0.0 },
            exemption_phaseout,
            table: Brackets::new(&params::brackets(p.value("brackets", &[])?)?),
            aif: p.f("bracket_inflation")?,
            low_income_limit: opt("low_income_taxable_income_limit")?,
            minimum_rate: opt("minimum_tax_rate")?,
            minimum_share: if p.in_effect("minimum_tax_rate")? { None } else { opt("minimum_tax_share_of_federal_amt")? },
            child_rate: p.f("child_care_credit_rate")?,
            refundable_cap: num(get(&doc, "child_care_credit_refundable_cap")?)?,
            dependent_per_child: opt("dependent_credit_per_child")?,
            dependent_exemption,
            elderly_share: opt("elderly_credit_share")?,
            credit_per_exemption: opt("credit_per_exemption")?,
            rent_or_property: match p.opt("rent_or_property_tax_credit")? {
                Some(r) => Some(pair(r, "rent_cap", "property_tax_cap")?),
                None => None,
            },
            deduction_credits,
            old_property,
            eitc_rate,
            eitc_childless: opt("eitc_rate_childless")?,
            eitc_rates_by_children: rates_by_children,
            eitc_refundable: if eitc_rate.is_some() { p.value("eitc_refundable", &[])?.as_bool().ok_or("expected a boolean")? } else { false },
            ptfc_rent_share: if p.in_effect("property_tax_fairness_credit")? { p.f("ptfc_rent_share")? } else { 0.0 },
            ptfc,
            ptfc_schedule,
            stfc,
            dividend_adjustment: dividend_adjustment(root)?,
        })
    }
}

pub fn tax(i: &Inp, f: &Fed, p: &MeParams) -> f64 {
    let y = p.year;
    let fs = i.fs();
    let is_joint = fs == Fs::Joint;
    let is_hoh = fs == Fs::Hoh;
    let is_sep = fs == Fs::Separate;
    let is_single = fs == Fs::Single;
    let sep = i.sepret();
    let me_txp = if is_joint { 2.0 } else { 1.0 };
    let me_texp = if is_joint { p.divisor[0] } else if is_hoh { p.divisor[1] } else { p.divisor[2] };
    let aged = i.aged_count();
    let depx = i.depx;
    let setax = f.payroll.setax;
    let salt = f.ded;
    let total_income = f.agi + 0.5 * setax;
    let hy = household_income(i, p.dividend_adjustment);

    // ---- AGI ----
    let mut me_agi = f.agi;
    if let Some(per_taxpayer) = p.dividend_exclusion {
        let divexc = (i.dividends + i.intrec).min(f.pre87.divexc);
        let own_cap = i.dividends.min(per_taxpayer * me_txp);
        me_agi += divexc - own_cap;
    }
    if y >= 1984 {
        me_agi -= f.taxable_social_security;
    }
    if let Some((per_taxpayer, phaseout)) = &p.pension {
        let mut pension_deduction = i.pensions.min((per_taxpayer * me_txp - i.gssi).max(0.0));
        if let Some(po) = phaseout {
            let share = ((f.agi - po.start[fs as usize]).max(0.0) / po.width[fs as usize]).min(1.0);
            pension_deduction *= 1.0 - share;
        }
        me_agi -= pension_deduction;
    }

    // ---- standard deduction ----
    let clip = |value: f64, (floor, ceiling): (f64, f64)| value.max(floor).min(ceiling);
    let mut stded;
    if let Some(s) = &p.percent {
        let single_group = is_single || is_hoh;
        let single_d = clip(s.rate * me_agi, s.single);
        stded = match s.separate {
            Some(separate) => {
                if single_group {
                    single_d
                } else if is_joint {
                    clip(s.rate * me_agi, s.married)
                } else {
                    clip(s.rate * me_agi, separate)
                }
            }
            None => {
                if single_group {
                    single_d
                } else {
                    clip(s.rate * me_agi, (s.married.0 / sep, s.married.1 / sep))
                }
            }
        };
    } else if y == 1988 {
        stded = 0.0;
    } else {
        stded = if !f.itemizes { f.standard_deduction } else { 0.0 };
        if y == 2023 {
            // TAXSIM's 2023 aged addition is $100 below the statutory value used by the federal port.
            let stale = if is_single || is_hoh { 100.0 * aged } else { 0.0 };
            stded = (stded - stale).max(0.0);
        }
        if let Some(o) = &p.married_override {
            if is_joint || is_sep {
                stded = o.amount / sep + o.aged_addition * aged;
            }
        }
        if let Some(sh) = &p.single_and_hoh {
            let olds = sh.aged * aged;
            if is_hoh {
                stded = sh.hoh + olds;
            } else if is_single {
                stded = sh.single + olds;
            }
        }
    }
    if let Some(n) = &p.nonconforming {
        let base = if is_single || is_sep { n.single } else if is_hoh { n.hoh } else { n.joint };
        let aged_amount = if is_single || is_hoh { n.aged_single } else { n.aged_married };
        stded = if f.itemizes { 0.0 } else { base + aged_amount * aged };
    }

    // ---- itemized deduction ---- (when the federal return itemizes a positive gross total)
    let (smg, itemizes_local, local) = if y <= 1986 {
        (pre1987_gross_itemized(i, f), f.pre87.itemizes, f.pre87.deduc)
    } else {
        (f.salt_capped + i.mortgage, f.itemizes, f.itemized_deduction)
    };
    let gate = itemizes_local && smg > 0.0;
    let mut xitded = match p.itemized_cap {
        None => (local - salt).max(0.0),
        Some(cap) => {
            let statit = salt - salt * (smg - local) / smg.max(1e-9);
            cap.min((local - statit).max(0.0))
        }
    };
    if !gate {
        xitded = 0.0;
    }
    let mut me_deduc = if stded >= xitded { stded } else { xitded };
    if let Some(pr) = &p.proportional {
        let (phased, thrsh) = if is_hoh { pr.hoh } else { (pr.per_taxpayer.0 * me_txp, pr.per_taxpayer.1 * me_txp) };
        if me_agi > phased {
            me_deduc = (me_deduc - me_deduc * (me_agi - phased) / thrsh).max(0.0);
        }
    }
    if let Some((starts, ranges, index)) = &p.phaseout_start {
        let phased = starts[fs as usize] * index;
        let xl4 = ranges[fs as usize];
        if me_agi >= phased {
            me_deduc *= 1.0 - ((me_agi - phased) / xl4).clamp(0.0, 1.0);
        }
    }

    // ---- exemptions ----
    let exemps_count = federal_exemption_count(y, i);
    let exemp = if y <= 2012 {
        exemps_count * p.personal_exemption
    } else if y <= 2017 {
        f.personal_exemptions
    } else {
        let mut e = p.personal_exemption * me_txp;
        if let Some((starts, range)) = &p.exemption_phaseout {
            let phasex = starts[fs as usize];
            if me_agi > phasex {
                e *= 1.0 - ((me_agi - phasex) / (range / sep)).clamp(0.0, 1.0);
            }
        }
        if i.is_dependent_filer() { 0.0 } else { e }
    };
    let me_taxinc = (me_agi - me_deduc - exemp).max(0.0);

    // ---- tax ----
    let tinc = me_taxinc / me_texp;
    let mut statax = p.table.tax(tinc / p.aif) * p.aif * me_texp;
    if let Some(ceiling) = p.low_income_limit {
        if me_taxinc <= ceiling {
            statax = 0.0;
        }
    }

    // ---- minimum tax ----
    let comnew28 = if y <= 1979 { f.regular_tax } else if y <= 1990 { f.fiitax } else { f.regular_tax };
    let comnew69 = f.agi - if f.itemizes { i.mortgage } else { 0.0 };
    let amt = f.amt;
    let mut txm = 0.0;
    if let Some(rate) = p.minimum_rate {
        txm = if amt > 0.0 { (rate * comnew69 - statax).max(0.0) } else { 0.0 };
    } else if let Some(share) = p.minimum_share {
        txm = if amt > 0.0 { share * (amt - comnew28).max(0.0) } else { 0.0 };
    }
    statax += txm;

    // ---- credits ---- A share of the federal child care credit, limited by federal tax before credits.
    let comnew52 = if y <= 1979 { f.regular_tax } else if y <= 1986 { f.fiitax } else { f.regular_tax };
    let chcr = p.child_rate * f.federal_chcr.min(comnew52.max(0.0));
    let chcrr = p.refundable_cap.min(chcr);
    let chcrn = chcr - chcrr;

    let mut depcrd = 0.0;
    let mut dep_refundable = 0.0;
    if let Some(per_child) = p.dependent_per_child {
        depcrd = (per_child * i.dep17).min(statax);
    } else if let Some(d) = &p.dependent_exemption {
        let start = match &d.starts {
            DepStarts::ByStatus { joint, hoh, separate, single } => {
                if is_joint { *joint } else if is_hoh { *hoh } else if is_sep { *separate } else { *single }
            }
            DepStarts::Simple { joint, other } => if is_joint { *joint } else { *other },
        };
        let steps = ((me_agi - start).max(0.0) / d.increment).ceil();
        // A dependent under 6 counts `young_multiplier` times.
        let dependents = depx + (d.young_multiplier - 1.0) * i.dep6.min(depx);
        let statutory = (d.amount * dependents - d.step * steps).max(0.0);
        depcrd = statutory.min(statax);
        if y >= 2024 {
            // Refundable from 2024: applied with the other refundable credits below.
            dep_refundable = statutory;
            depcrd = 0.0;
        }
    }
    let eldcr = p.elderly_share.map_or(0.0, |share| share * f.federal_elder);
    let mut credit = chcrn + depcrd + eldcr;
    if let Some(per_exemption) = p.credit_per_exemption {
        credit += per_exemption * (i.taxpayer_count() + depx + aged);
    }
    if let Some((cap_r, cap_p)) = p.rent_or_property {
        credit += 0.0f64.max(i.rentpaid.min(cap_r)).max(cap_p.min(i.proptax));
    }
    if let Some(c) = &p.deduction_credits {
        let std_cap = if is_single { c.caps[0] } else if is_joint { c.caps[1] } else if is_hoh { c.caps[3] } else { c.caps[2] };
        credit += (c.pct * f.earned_income).max(c.floor).min(std_cap);
        credit += if matches!(fs, Fs::Single | Fs::Separate) {
            c.single.eval(me_agi)
        } else if is_hoh {
            c.hoh.eval(me_agi)
        } else {
            c.married.eval(me_agi)
        };
    }
    statax = (statax - credit).max(0.0);

    // ---- property tax credit (1977-1988), for taxpayers 65 or older ----
    if let Some(o) = &p.old_property {
        let aged_any = aged.clamp(0.0, 1.0);
        let ptax = i.proptax.max(o.rent_share * i.rentpaid);
        let mut pcred;
        if y <= 1987 {
            let limit = if me_txp == 2.0 { o.limit_two } else { o.limit_one };
            pcred = match o.reduction {
                Some((rate, floor)) => (ptax - rate * (hy - floor).max(0.0)).max(0.0).min(o.cap),
                None => ptax.max(0.0).min(o.cap),
            };
            pcred = if hy <= limit { pcred * aged_any } else { 0.0 };
        } else {
            let t = o.tiers.as_ref().expect("1988 tiers");
            let lookup = |rows: &[(f64, f64, f64)]| -> f64 {
                for (upper, max_credit, share) in rows {
                    if hy <= *upper {
                        return max_credit.min(share * ptax);
                    }
                }
                0.0
            };
            pcred = if me_txp == 2.0 { lookup(&t.two) } else { lookup(&t.one) };
        }
        statax = (statax - pcred * aged_any).max(0.0);
    }

    // ---- EITC ----
    let mut earncr = 0.0;
    if let Some(rate) = p.eitc_rate {
        earncr = rate * f.eitc;
        if let Some(childless) = p.eitc_childless {
            let federal = f.eitc_before_age_test;
            earncr = if i.dep18 < 1.0 { childless * federal } else { rate * federal };
        }
    }
    if let Some((with_child, childless)) = p.eitc_rates_by_children {
        // Childless filers from age 18 (not the federal 25).
        let older = i.page.max(i.sage);
        let childless_ok = older == 0.0 || older >= 18.0;
        earncr = if i.dep18 > 0.0 {
            with_child * f.eitc
        } else if childless_ok {
            childless * f.eitc_before_age_test
        } else {
            0.0
        };
    }
    if p.eitc_rate.is_some() && !p.eitc_refundable {
        statax = (statax - earncr).max(0.0);
    }

    // ---- property tax fairness credit (2013+, refundable) ----
    let ti = (total_income + i.gssi).max(0.0);
    let aged_any = aged > 0.0;
    let nexem = exemps_count.floor();
    let mut ptfc = match &p.ptfc {
        Ptfc::None => 0.0,
        Ptfc::Y2013(pf) => {
            let agix = me_agi.max(0.0);
            let bagix = pf.income_share * agix;
            let base = i.proptax + p.ptfc_rent_share * i.rentpaid;
            let cap = if aged_any { pf.elderly_cap } else { pf.cap };
            let eligible = nexem > 0.0 && agix <= pf.ceiling && base > bagix;
            if eligible { (cap / sep).min(pf.benefit_share * (base - bagix)) } else { 0.0 }
        }
        Ptfc::Caps(pf) => {
            let base_raw = i.proptax + p.ptfc_rent_share * i.rentpaid;
            // Through 2017 the caps for two or more exemptions are halved on separate returns.
            let div = if y <= 2017 { sep } else { 1.0 };
            let base = if is_single {
                pf.base_single.min(base_raw)
            } else if nexem <= 2.0 {
                (pf.base_two / div).min(base_raw)
            } else {
                (pf.base_more / div).min(base_raw)
            };
            let single_income_cap = if y <= 2017 { pf.income_single / div } else { pf.income_single };
            let tix = if is_single {
                single_income_cap.min(ti)
            } else if nexem <= 2.0 {
                (pf.income_two / div).min(ti)
            } else {
                (pf.income_more / div).min(ti)
            };
            let cap_out = if aged_any { pf.elderly_cap } else { pf.cap };
            let eligible = nexem > 0.0;
            let excess = (base - pf.income_share * tix).max(0.0);
            match pf.benefit_share {
                Some(share) => if eligible { (cap_out / sep).min(share * excess) } else { 0.0 },
                None => if eligible { cap_out.min(excess) } else { 0.0 },
            }
        }
    };
    if y >= 2017 && is_sep {
        ptfc = 0.0;
    }
    if let Some(d) = &p.ptfc_schedule {
        let kids = i.dep17;
        let mut base = if is_single {
            d.single
        } else if (is_joint && kids < 1.0) || (is_hoh && kids <= 1.0) {
            d.hoh_one_child
        } else {
            d.joint_or_multi
        };
        let older = i.page.max(i.sage);
        let senior = older >= 65.0 && d.senior > 0.0;
        if senior {
            base = d.senior;
        }
        let countable = i.proptax + d.rent_share * i.rentpaid;
        let capgn = i.stcg + i.ltcg;
        let fair_income = f.agi + setax * 0.5 + i.gssi - f.taxable_social_security - capgn.min(0.0);
        let mut c = (base.min(countable) - d.income_rate * fair_income).max(0.0);
        c = c.min(if senior { d.cap_senior } else { d.cap });
        ptfc = if is_sep { 0.0 } else { c };
    }

    // ---- sales tax fairness credit (2016+, refundable) ----
    let mut stfc = 0.0;
    if !matches!(p.stfc, Stfc::None) {
        let capgn = i.stcg + i.ltcg;
        let mut tis = f.agi + setax * 0.5 + i.gssi - f.taxable_social_security - capgn.min(0.0);
        if y == 2020 {
            tis += unemployment_total(i) - f.taxable_unemployment;
        }
        let eligible = !is_sep && !i.is_dependent_filer();
        match &p.stfc {
            Stfc::Old(s) => {
                let nexem_capped = nexem.clamp(0.0, 4.0);
                let threshold = if is_single { s.threshold[0] } else if is_hoh { s.threshold[1] } else { s.threshold[2] };
                let credit_amt = (1..=4).find(|n| nexem_capped == *n as f64).map_or(0.0, |n| s.amount[n - 1]);
                if nexem > 0.0 && eligible {
                    stfc = (credit_amt - ((tis - threshold) / s.step).max(0.0)).max(0.0);
                }
            }
            Stfc::Schedule(d) => {
                let children = i.dep17;
                let mut extra_joint = 0.0;
                for (threshold, amount) in &d.extra_joint {
                    if children >= *threshold {
                        extra_joint = *amount;
                    }
                }
                let mut extra_hoh = 0.0;
                for (threshold, amount) in &d.extra_hoh {
                    if children >= *threshold {
                        extra_hoh = *amount;
                    }
                }
                let row = &d.rows[fs as usize];
                let extra = match fs {
                    Fs::Joint => extra_joint,
                    Fs::Hoh => extra_hoh,
                    _ => 0.0,
                };
                let mut credit = 0.0;
                if row.increment != 0.0 {
                    let reduction = ((tis - row.start).max(0.0) / row.increment).ceil() * row.step;
                    credit = (row.base + extra - reduction).max(0.0);
                }
                stfc = if eligible { credit } else { 0.0 };
            }
            Stfc::None => {}
        }
    }
    statax = statax - chcrr - ptfc - stfc - dep_refundable;
    if p.eitc_rate.is_some() && p.eitc_refundable {
        statax -= earncr;
    }
    statax
}
