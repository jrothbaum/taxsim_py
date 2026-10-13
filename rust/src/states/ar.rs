//! Arkansas income tax (port of `calculators.states.ar`).

use std::path::Path;

use crate::common::Brackets;
use crate::federal::{Fed, Inp};
use crate::params::{self, get, load, num, resolve_year, status_direct, Fs, YearParams, STATUS_KEYS};
use crate::states::{dividend_adjustment, higher_earner_share, law_year_is_exact};

/// Convert `[upper bound, rate %]` rows to `[start, rate]` brackets.
fn upper_bound_brackets(rows: &[Vec<f64>]) -> Brackets {
    let mut out = vec![vec![0.0, rows[0][1] / 100.0]];
    for k in 1..rows.len() {
        out.push(vec![rows[k - 1][0], rows[k][1] / 100.0]);
    }
    Brackets::new(&out)
}

/// One row of the 1991-2021 low-income formula table.
struct FormulaRow {
    offset: f64,
    base: f64,
    rate: f64,
    upper: f64,
    exclusive: bool,
}

struct FormulaGroup {
    status: usize,
    dep_tier: u8,
    lower0: f64,
    upper_last: f64,
    rows: Vec<FormulaRow>,
}

struct Steps {
    status: usize,
    dep_tier: u8,
    starts: Vec<f64>,
    amounts: Vec<f64>,
}

struct Schedule {
    start: f64,
    end: f64,
    offset: f64,
    multiplier: f64,
}

struct LowIncomeSchedule {
    step: f64,
    single: Schedule,
    married_no_dependents: Schedule,
    richer_up_to_one: Schedule,
    richer_two: Schedule,
}

enum Table {
    Standard { other: Brackets, separate: Brackets, itemized: Brackets },
    Brackets(Brackets),
    Rates(Vec<[f64; 3]>),
}

struct StandardPercent {
    rate: f64,
    cap_per_taxpayer: f64,
}

struct ItemizedLimit {
    rate: f64,
    cap_rate: f64,
    index: f64,
    threshold_by_status: Option<[f64; 4]>,
    threshold: f64,
}

struct Working {
    rate: f64,
    cap: f64,
    floor: f64,
}

struct CgCredit {
    rate: f64,
    threshold: f64,
    maximum: f64,
}

struct Relief {
    maximum: f64,
    start: f64,
    reduction: f64,
    increment: f64,
}

struct Qualified {
    amount: f64,
    reduction: f64,
    start: f64,
    increment: f64,
}

pub struct ArParams {
    year: i64,
    loss_limit: f64,
    cg_rate: f64,
    cg_cap: Option<f64>,
    pension: Option<(f64, String)>,
    standard_percent: Option<StandardPercent>,
    standard: f64,
    itemized_limit: Option<ItemizedLimit>,
    table: Table,
    surcharge: Option<f64>,
    working: Option<Working>,
    low_income_schedule: Option<LowIncomeSchedule>,
    formula: Vec<FormulaGroup>,
    steps: Vec<Steps>,
    cg_credit: Option<CgCredit>,
    personal_credit: f64,
    dependent_credit: Option<f64>,
    relief: Option<[Relief; 4]>,
    qualified: Option<Qualified>,
    child_rate: f64,
    child_cap: Option<f64>,
    dividend_adjustment: f64,
}

fn dep_tier_matches(tier: u8, depx: f64) -> bool {
    match tier {
        0 => true,
        1 => depx <= 1.0,
        _ => depx >= 2.0,
    }
}

fn read_csv(root: &Path, file: &str) -> Result<Vec<Vec<String>>, String> {
    let path = root.join("states/ar").join(file);
    let text = std::fs::read_to_string(&path).map_err(|e| format!("{}: {e}", path.display()))?;
    Ok(text.lines().skip(1).filter(|l| !l.trim().is_empty()).map(|l| l.split(',').map(|s| s.to_string()).collect()).collect())
}

fn status_index(name: &str) -> Result<usize, String> {
    STATUS_KEYS.iter().position(|k| *k == name).ok_or_else(|| format!("unknown filing status {name}"))
}

impl ArParams {
    pub fn load(root: &Path, year: i64) -> Result<Self, String> {
        let doc = load(root, "states/ar/income_tax.yaml")?;
        law_year_is_exact(root, &doc, year)?;
        let capital_gains = load(root, "national/capital_gains.yaml")?;
        let adjustments = load(root, "national/state_adjustments.yaml")?;
        let p = YearParams::new(&doc, year);
        let opt = |key: &str| -> Result<Option<f64>, String> { Ok(if p.in_effect(key)? { Some(p.f(key)?) } else { None }) };

        let table = if p.in_effect("standard_deduction_tables")? {
            let t = p.value("standard_deduction_tables", &[])?;
            Table::Standard {
                other: upper_bound_brackets(&params::brackets(get(t, "other")?)?),
                separate: upper_bound_brackets(&params::brackets(get(t, "separate")?)?),
                itemized: Brackets::new(&params::brackets(p.value("brackets", &[])?)?),
            }
        } else if p.in_effect("brackets")? {
            Table::Brackets(Brackets::new(&params::brackets(p.value("brackets", &[])?)?))
        } else {
            Table::Rates(params::matrix(p.value("rate_table", &[])?)?.into_iter().map(|r| [r[0], r[1], r[2]]).collect())
        };
        let itemized_limit = if (1991..=2017).contains(&year) && p.in_effect("itemized_limitation")? {
            let lim = p.value("itemized_limitation", &[])?;
            let index = num(resolve_year(get(&adjustments, "itemized_phaseout_inflation")?, year)?)?;
            let threshold = get(lim, "threshold")?;
            let by_status = if threshold.is_mapping() {
                let raw = status_direct(threshold)?;
                Some([raw[0] * index, raw[1] * index, raw[2] * index, raw[3] * index])
            } else {
                None
            };
            Some(ItemizedLimit {
                rate: num(get(lim, "rate")?)?,
                cap_rate: num(get(lim, "cap_rate")?)?,
                index,
                threshold_by_status: by_status,
                threshold: if threshold.is_mapping() { 0.0 } else { num(threshold)? },
            })
        } else {
            None
        };
        let schedule = |v: &serde_yaml::Value| -> Result<Schedule, String> {
            Ok(Schedule { start: num(get(v, "start")?)?, end: num(get(v, "end")?)?, offset: num(get(v, "offset")?)?, multiplier: num(get(v, "multiplier")?)? })
        };
        let low_income_schedule = match p.opt("low_income_schedule")? {
            Some(s) => Some(LowIncomeSchedule {
                step: num(get(s, "step")?)?,
                single: schedule(get(s, "single")?)?,
                married_no_dependents: schedule(get(s, "married_no_dependents")?)?,
                richer_up_to_one: schedule(get(s, "married_or_hoh_up_to_one_dependent")?)?,
                richer_two: schedule(get(s, "married_or_hoh_two_dependents")?)?,
            }),
            None => None,
        };

        // Low-income tables: the formula table for 1991-2021 (when the year has rows) or the step table from 2022.
        let mut formula = Vec::new();
        let mut steps: Vec<Steps> = Vec::new();
        if low_income_schedule.is_none() {
            if year >= 2022 {
                for line in read_csv(root, "low_income_step_table.csv")? {
                    if line[0].parse::<i64>().ok() != Some(year) {
                        continue;
                    }
                    let status = status_index(&line[1])?;
                    let dep_tier: u8 = line[2].parse().map_err(|_| "bad dep_tier")?;
                    let threshold: f64 = line[3].parse().map_err(|_| "bad threshold")?;
                    let amount: f64 = if line[4].trim().is_empty() { 1e18 } else { line[4].parse().map_err(|_| "bad amount")? };
                    match steps.iter_mut().find(|s| s.status == status && s.dep_tier == dep_tier) {
                        Some(s) => {
                            s.starts.push(threshold);
                            s.amounts.push(amount);
                        }
                        None => steps.push(Steps { status, dep_tier, starts: vec![threshold], amounts: vec![amount] }),
                    }
                }
            } else {
                let mut raw: Vec<(usize, u8, i64, FormulaRow, f64)> = Vec::new();
                for line in read_csv(root, "low_income_formula_table.csv")? {
                    if line[0].parse::<i64>().ok() != Some(year) {
                        continue;
                    }
                    let f = |k: usize| -> Result<f64, String> { line[k].parse::<f64>().map_err(|_| format!("bad number {}", line[k])) };
                    raw.push((
                        status_index(&line[1])?,
                        line[2].parse().map_err(|_| "bad dep_tier")?,
                        line[3].parse().map_err(|_| "bad tier_order")?,
                        FormulaRow { offset: f(6)?, base: f(7)?, rate: f(8)?, upper: f(5)?, exclusive: line[9].trim() == "1" },
                        f(4)?,
                    ));
                }
                // Statuses in the order single, married joint, head of household; tiers ascending.
                for status in [0usize, 1, 3] {
                    let mut tiers: Vec<u8> = raw.iter().filter(|r| r.0 == status).map(|r| r.1).collect();
                    tiers.sort();
                    tiers.dedup();
                    for tier in tiers {
                        let mut rows: Vec<&(usize, u8, i64, FormulaRow, f64)> = raw.iter().filter(|r| r.0 == status && r.1 == tier).collect();
                        rows.sort_by_key(|r| r.2);
                        let lower0 = rows[0].4;
                        let upper_last = rows[rows.len() - 1].3.upper;
                        formula.push(FormulaGroup {
                            status,
                            dep_tier: tier,
                            lower0,
                            upper_last,
                            rows: rows
                                .iter()
                                .map(|r| FormulaRow { offset: r.3.offset, base: r.3.base, rate: r.3.rate, upper: r.3.upper, exclusive: r.3.exclusive })
                                .collect(),
                        });
                    }
                }
            }
        }
        let relief = match p.opt("inflationary_relief_credit")? {
            Some(r) => {
                let read = |key: &str| -> Result<Relief, String> {
                    let v = get(r, key)?;
                    Ok(Relief {
                        maximum: num(get(v, "maximum")?)?,
                        start: num(get(v, "start")?)?,
                        reduction: num(get(v, "reduction")?)?,
                        increment: num(get(v, "increment")?)?,
                    })
                };
                Some([read("single")?, read("married_joint")?, read("married_separate")?, read("head_of_household")?])
            }
            None => None,
        };
        let pension = if year >= 1983 {
            Some((p.f("pension_exclusion_amount")?, p.value("pension_exclusion_basis", &[])?.as_str().ok_or("basis must be text")?.to_string()))
        } else {
            None
        };
        Ok(Self {
            year,
            loss_limit: if year >= 1987 { num(resolve_year(get(&capital_gains, "net_capital_loss_limit")?, year)?)? } else { 0.0 },
            cg_rate: if year >= 1999 { 1.0 - p.f("capital_gains_exclusion")? } else { 1.0 },
            cg_cap: if year >= 1999 { opt("capital_gains_cap")? } else { None },
            pension,
            standard_percent: match p.opt("standard_deduction_percent")? {
                Some(s) => Some(StandardPercent { rate: num(get(s, "rate")?)?, cap_per_taxpayer: num(get(s, "cap_per_taxpayer")?)? }),
                None => None,
            },
            standard: if p.in_effect("standard_deduction_percent")? { 0.0 } else { p.f("standard_deduction")? },
            itemized_limit,
            table,
            surcharge: opt("surcharge")?,
            working: match p.opt("working_taxpayer_credit")? {
                Some(w) => Some(Working { rate: num(get(w, "rate")?)?, cap: num(get(w, "cap")?)?, floor: num(get(w, "floor")?)? }),
                None => None,
            },
            low_income_schedule,
            formula,
            steps,
            cg_credit: match p.opt("capital_gains_credit")? {
                Some(c) => Some(CgCredit { rate: num(get(c, "rate")?)?, threshold: num(get(c, "threshold")?)?, maximum: num(get(c, "maximum")?)? }),
                None => None,
            },
            personal_credit: p.f("personal_credit")?,
            dependent_credit: opt("dependent_credit")?,
            relief,
            qualified: match p.opt("qualified_individual_credit")? {
                Some(q) => Some(Qualified {
                    amount: num(get(q, "amount")?)?,
                    reduction: num(get(q, "reduction")?)?,
                    start: num(get(q, "start")?)?,
                    increment: num(get(q, "increment")?)?,
                }),
                None => None,
            },
            child_rate: p.f("child_care_credit_federal_share")?,
            child_cap: opt("child_care_credit_cap_per_dependent")?,
            dividend_adjustment: dividend_adjustment(root)?,
        })
    }
}

/// Arkansas's rate-table lookup: the first row whose threshold exceeds income, else the last row.
fn rate_lookup(rows: &[[f64; 3]], income: f64) -> f64 {
    let last = rows[rows.len() - 1];
    for r in &rows[..rows.len() - 1] {
        if income < r[0] {
            return (r[1] / 100.0) * income - r[2];
        }
    }
    (last[1] / 100.0) * income - last[2]
}

fn override_of(c: &Schedule, step: f64, agi: f64) -> Option<f64> {
    if agi < c.start {
        Some(0.0)
    } else if agi <= c.end {
        Some(((agi - c.offset) / step).floor() * c.multiplier)
    } else {
        None
    }
}

pub fn tax(i: &Inp, f: &Fed, p: &ArParams) -> f64 {
    let y = p.year;
    let fs = i.fs();
    let is_joint = fs == Fs::Joint;
    let is_hoh = fs == Fs::Hoh;
    let sep = i.sepret();
    let txp = i.taxpayer_count();
    let aged = i.aged_count();
    let depx = i.depx;
    let wages = i.pwages + i.swages;
    let business = i.pbusinc + i.pprofinc + i.sbusinc + i.sprofinc + i.scorp;
    let schede = i.otherprop + if y >= 1987 { i.scorp } else { 0.0 };

    // ---- capital gains ----
    let fullcg = i.stcg + i.ltcg;
    let federal_capgn = if y <= 1986 { f.pre87.capgn } else { fullcg.max(-p.loss_limit / sep) };
    let mut capgn = if y <= 1990 {
        if federal_capgn < 0.0 { fullcg } else { federal_capgn }
    } else if y <= 1998 {
        federal_capgn
    } else {
        let partial = if i.stcg < 0.0 { p.cg_rate * fullcg } else { p.cg_rate * i.ltcg + i.stcg };
        if federal_capgn > 0.0 { partial } else { federal_capgn }
    };
    if y > 1998 {
        if let Some(cap) = p.cg_cap {
            capgn = capgn.min(cap);
        }
    }

    // ---- AGI ----
    let mut agi = wages + (i.dividends + p.dividend_adjustment) + i.intrec + capgn + i.psemp + i.ssemp + i.pensions + schede + business;
    if let Some((amount, basis)) = &p.pension {
        let count = match basis.as_str() {
            "return" => 1.0,
            "taxpayer" => txp,
            _ => aged,
        };
        agi -= i.pensions.min(amount * count);
    }
    if (2018..=2019).contains(&y) {
        agi += i.ui;
    }

    // ---- standard deduction ----
    let stded = match &p.standard_percent {
        Some(s) => (s.rate * agi).max(0.0).min(s.cap_per_taxpayer * txp / sep),
        None => p.standard * txp,
    };

    // ---- itemized deduction ----
    let base = i.proptax + i.otheritem + i.mortgage;
    let mut xitded = match &p.itemized_limit {
        Some(l) => {
            let phas = match &l.threshold_by_status {
                Some(by_status) => by_status[fs as usize],
                None => l.threshold * l.index / sep,
            };
            let reduce = if agi > phas { (l.cap_rate * base).min(l.rate * (agi - phas)) } else { 0.0 };
            (base - reduce).max(0.0)
        }
        None if (1991..=2017).contains(&y) => base.max(0.0),
        None => base,
    };
    if !f.forced_itemize {
        xitded = 0.0;
    }
    let deduc = stded.max(xitded);
    let taxinc = (agi - deduc).max(0.0);

    // ---- joint returns: each earner's share of AGI and deductions ----
    let agi_h = higher_earner_share(i, agi);
    let agi_w = agi - agi_h;
    let xitdh = if agi != 0.0 { xitded * agi_h / agi } else { 0.0 };
    let xitdw = xitded - xitdh;
    let dedh = (0.5 * stded).max(xitdh);
    let dedw = (0.5 * stded).max(xitdw);
    let taxinh = (agi_h - dedh).max(0.0);
    let taxinw = (agi_w - dedw).max(0.0);
    let joint_split = is_joint && agi > 0.0;

    // ---- tax ----
    let mut statax = match &p.table {
        Table::Standard { other, separate, itemized } => {
            let prefers_itemized = f.forced_itemize || stded < xitded;
            let mut stat_std = if fs == Fs::Separate { separate.tax(agi) } else { other.tax(agi) };
            if is_joint {
                let method_a = 2.0 * other.tax(agi / 2.0);
                let method_b = other.tax(agi_h) + other.tax(agi - agi_h);
                stat_std = method_a.min(method_b);
            }
            let mut stat_item = itemized.tax(taxinc);
            if joint_split {
                stat_item = stat_item.min(itemized.tax(taxinh) + itemized.tax(taxinw));
            }
            if prefers_itemized { stat_item } else { stat_std }
        }
        Table::Brackets(b) => {
            let stat = b.tax(taxinc);
            if joint_split { stat.min(b.tax(taxinh) + b.tax(taxinw)) } else { stat }
        }
        Table::Rates(rows) => {
            let stat = rate_lookup(rows, taxinc);
            if joint_split { stat.min(rate_lookup(rows, taxinh) + rate_lookup(rows, taxinw)) } else { stat }
        }
    };
    if let Some(s) = p.surcharge {
        statax *= 1.0 + s;
    }
    if let Some(w) = &p.working {
        // All self-employment income goes to the higher-wage spouse.
        let self_employment = i.psemp + i.ssemp;
        let winc85 = i.pwages.max(i.swages) + self_employment;
        let winc86 = i.pwages.min(i.swages);
        let work85 = if winc85 > w.floor { w.cap.min(w.rate * winc85) } else { 0.0 };
        let work86 = if winc86 > w.floor && i.pwages > 0.0 && i.swages > 0.0 { w.cap.min(w.rate * winc86) } else { 0.0 };
        let winc_single = wages + self_employment;
        let work_single = if winc_single > w.floor { w.cap.min(w.rate * winc_single) } else { 0.0 };
        let work = if i.pwages > 0.0 && i.swages > 0.0 { work85 + work86 } else { work_single };
        statax = (statax - work).max(0.0);
    }

    // ---- low-income tax tables (not for separate returns) ----
    let eligible = fs != Fs::Separate;
    if let Some(s) = &p.low_income_schedule {
        let richer = is_joint || is_hoh;
        let ov = if fs == Fs::Single && override_of(&s.single, s.step, agi).is_some() {
            override_of(&s.single, s.step, agi)
        } else if is_joint && depx < 1.0 && override_of(&s.married_no_dependents, s.step, agi).is_some() {
            override_of(&s.married_no_dependents, s.step, agi)
        } else if richer && depx <= 1.0 && override_of(&s.richer_up_to_one, s.step, agi).is_some() {
            override_of(&s.richer_up_to_one, s.step, agi)
        } else if richer && depx >= 2.0 && override_of(&s.richer_two, s.step, agi).is_some() {
            override_of(&s.richer_two, s.step, agi)
        } else {
            None
        };
        if let Some(v) = ov {
            statax = v;
        }
    } else if y >= 2022 {
        // Dated statutory tables: the tax is the lesser of the step table and the main schedule.
        let mut lookup = None;
        for group in &p.steps {
            if group.status == fs as usize && dep_tier_matches(group.dep_tier, depx) {
                let idx = group.starts.partition_point(|s| *s <= agi).saturating_sub(1);
                lookup = Some(group.amounts[idx]);
            }
        }
        if let (true, Some(v)) = (eligible, lookup) {
            statax = statax.min(v);
        }
    } else if !p.formula.is_empty() && eligible {
        let ge_lower = y >= 2017;
        let mut applies = false;
        let mut value = 0.0;
        for g in &p.formula {
            if g.status != fs as usize || !dep_tier_matches(g.dep_tier, depx) {
                continue;
            }
            let lower_test = if ge_lower { agi >= g.lower0 } else { agi > g.lower0 };
            let in_range = lower_test && agi <= g.upper_last;
            applies = applies || agi <= g.upper_last;
            let mut tier_val = if agi <= g.lower0 { Some(0.0) } else { None };
            for r in &g.rows {
                let cond = if r.exclusive { agi < r.upper } else { agi <= r.upper };
                if cond && tier_val.is_none() {
                    tier_val = Some(r.base + r.rate * (agi - r.offset));
                }
            }
            if in_range {
                value = tier_val.unwrap_or(0.0);
            }
        }
        if applies {
            statax = value;
        }
    }
    if let Some(c) = &p.cg_credit {
        statax -= c.rate * if taxinc > c.threshold { (taxinc - c.threshold).min(c.maximum).min(capgn.max(0.0)) } else { 0.0 };
    }

    // ---- credits ----
    let gcred = match p.dependent_credit {
        Some(dependent) => p.personal_credit * (txp + aged) + dependent * depx,
        None => {
            let pcr = p.personal_credit;
            let mut g = pcr * (txp + aged + depx);
            if is_hoh {
                g += pcr;
            }
            // Taxpayers 65 or older without pension income get one more credit.
            if i.pensions == 0.0 && aged > 0.0 {
                g += pcr;
            }
            g
        }
    };
    let mut extra = 0.0;
    if let Some(r) = &p.relief {
        let c = &r[fs as usize];
        extra += (c.maximum - c.reduction * ((agi - c.start).max(0.0) / c.increment).floor()).max(0.0);
    }
    if let Some(q) = &p.qualified {
        let people = txp;
        extra += (q.amount * people - q.reduction * people * ((taxinc - q.start).max(0.0) / q.increment).ceil()).max(0.0);
    }
    let mut chcr = (p.child_rate * f.federal_chcr.max(0.0)).max(0.0);
    if let Some(cap) = p.child_cap {
        chcr = chcr.min(cap * depx.clamp(0.0, 2.0));
    }
    (statax - (chcr + gcred + extra)).max(0.0)
}
