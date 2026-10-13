//! Oregon income tax (port of `calculators.states.or_`).

use std::path::Path;

use crate::common::Brackets;
use crate::federal::{Fed, Inp};
use crate::params::{self, get, load, num, resolve_year, status_direct, Fs, YearParams};
use crate::states::{
    dividend_adjustment, dividend_exclusion_addback, federal_exemption_count, household_income, law_year_is_exact,
    pre1987_gross_itemized, unemployment_total, Interp, Tiers,
};

struct Unemployment {
    threshold: [f64; 4],
    share: f64,
}

enum Schedule {
    Split(Brackets),
    ByStatus { single: Brackets, married: Brackets },
}

struct MinimumTax {
    table: Brackets,
    preference: f64,
    agi: f64,
    agi_preference: f64,
}

struct ItemizedLimit {
    threshold: f64,
    index: f64,
    status_factor: Option<[f64; 4]>,
    share: f64,
    rate: f64,
}

struct StandardShare {
    rate: f64,
    low: f64,
    high: f64,
}

struct AgedStandard {
    addition: [f64; 4],
    floor: f64,
    earned_addition: Option<f64>,
}

struct TwoEarner {
    rate: f64,
    share: f64,
}

struct ChildCareRate {
    table: Interp,
    per_child: f64,
}

struct ExemptionCredit {
    amount: f64,
    income_limit: Option<f64>,
    phaseout: Option<ExemptionPhaseout>,
}

struct ExemptionPhaseout {
    minimum: f64,
    step: f64,
    rate: f64,
    hoh_factor: f64,
    married_factor: f64,
    base: f64,
}

struct Retirement {
    rate: f64,
    base: f64,
    income_start: f64,
    agi_limit: f64,
}

struct WorkingFamily {
    index: f64,
    loss_limit: f64,
    earned_min: f64,
    investment_max: f64,
    /// One interpolation table per household size 1-8.
    tables: Vec<Interp>,
}

struct DependentCare {
    uppers: Tiers,
    shares: Vec<f64>,
    last_upper: f64,
    per_child: f64,
    poverty: (f64, f64),
    agi_limit: (f64, f64),
}

struct Kids {
    amount: f64,
    child_limit: f64,
    start: f64,
    width: f64,
}

pub struct OrParams {
    year: i64,
    unemployment: Option<Unemployment>,
    interest_dividend_exclusion: Option<f64>,
    rebate: Option<(f64, f64)>,
    fed_limit: f64,
    fed_limit_phaseout: Vec<(f64, f64)>,
    standard_share: Option<StandardShare>,
    standard: [f64; 4],
    standard_index: f64,
    aged_standard: Option<AgedStandard>,
    salt_cap: Option<f64>,
    itemized_limit: Option<ItemizedLimit>,
    charity_nonitemizer: Option<f64>,
    exemption_amount: Option<f64>,
    schedule: Schedule,
    minimum_tax: Option<MinimumTax>,
    two_earner: Option<TwoEarner>,
    child_care_share: Option<f64>,
    child_care_rate: Option<ChildCareRate>,
    elderly_share: Option<f64>,
    exemption_credit: Option<ExemptionCredit>,
    retirement: Option<Retirement>,
    eitc: Option<(f64, Option<f64>)>,
    working_family: Option<WorkingFamily>,
    dependent_care: Option<DependentCare>,
    multiplier: f64,
    kicker: Option<f64>,
    kids: Option<Kids>,
    dividend_adjustment: f64,
}

impl OrParams {
    pub fn load(root: &Path, year: i64) -> Result<Self, String> {
        let doc = load(root, "states/or/income_tax.yaml")?;
        law_year_is_exact(root, &doc, year)?;
        let capital_gains = load(root, "national/capital_gains.yaml")?;
        let p = YearParams::new(&doc, year);
        let opt = |key: &str| -> Result<Option<f64>, String> { Ok(if p.in_effect(key)? { Some(p.f(key)?) } else { None }) };
        let pair = |v: &serde_yaml::Value| -> Result<(f64, f64), String> {
            let s = v.as_sequence().ok_or("expected a pair")?;
            Ok((num(&s[0])?, num(&s[1])?))
        };

        let unemployment = match p.opt("unemployment_inclusion")? {
            Some(u) => Some(Unemployment { threshold: status_direct(get(u, "threshold")?)?, share: num(get(u, "taxable_share")?)? }),
            None => None,
        };
        let schedule = {
            let s = p.value("brackets", &[])?;
            if s.get("split").is_some() {
                Schedule::Split(Brackets::new(&params::brackets(get(s, "split")?)?))
            } else {
                Schedule::ByStatus {
                    single: Brackets::new(&params::brackets(get(s, "single")?)?),
                    married: Brackets::new(&params::brackets(get(s, "married")?)?),
                }
            }
        };
        let minimum_tax = if p.in_effect("minimum_tax_brackets")? {
            Some(MinimumTax {
                table: Brackets::new(&params::brackets(p.value("minimum_tax_brackets", &[])?)?),
                preference: p.f("minimum_tax_preference_threshold")?,
                agi: p.f("minimum_tax_agi_threshold")?,
                agi_preference: p.f("minimum_tax_agi_preference_threshold")?,
            })
        } else {
            None
        };
        let itemized_limit = if year >= 1991 && p.in_effect("itemized_limit_threshold")? {
            Some(ItemizedLimit {
                threshold: p.f("itemized_limit_threshold")?,
                index: p.f("itemized_limit_index")?,
                status_factor: if p.in_effect("itemized_limit_status_factor")? {
                    Some(status_direct(p.value("itemized_limit_status_factor", &[])?)?)
                } else {
                    None
                },
                share: p.f("itemized_limit_share")?,
                rate: p.f("itemized_limit_rate")?,
            })
        } else {
            None
        };
        let standard_share = match p.opt("standard_deduction_share")? {
            Some(c) => Some(StandardShare { rate: num(get(c, "rate")?)?, low: num(get(c, "low")?)?, high: num(get(c, "high")?)? }),
            None => None,
        };
        let aged_standard = if p.in_effect("standard_deduction_aged_addition")? {
            Some(AgedStandard {
                addition: status_direct(p.value("standard_deduction_aged_addition", &[])?)?,
                floor: p.f("dependent_standard_deduction_floor")?,
                earned_addition: opt("dependent_standard_deduction_earned_addition")?,
            })
        } else {
            None
        };
        let two_earner = if p.in_effect("two_earner_credit_rate")? {
            Some(TwoEarner { rate: p.f("two_earner_credit_rate")?, share: p.f("child_care_federal_share")? })
        } else {
            None
        };
        let exemption_credit = if p.in_effect("exemption_credit")? {
            Some(ExemptionCredit {
                amount: p.f("exemption_credit")?,
                income_limit: opt("exemption_credit_income_limit")?,
                phaseout: if p.in_effect("exemption_credit_minimum")? {
                    let factors = p.value("exemption_credit_phaseout_factor", &[])?;
                    Some(ExemptionPhaseout {
                        minimum: p.f("exemption_credit_minimum")?,
                        step: p.f("exemption_credit_phaseout_step")?,
                        rate: p.f("exemption_credit_phaseout_rate")?,
                        hoh_factor: num(get(factors, "head_of_household")?)?,
                        married_factor: num(get(factors, "married")?)?,
                        base: p.f("itemized_limit_threshold")? * p.f("itemized_limit_index")?,
                    })
                } else {
                    None
                },
            })
        } else {
            None
        };
        let retirement = match p.opt("retirement_income_credit")? {
            Some(r) => Some(Retirement {
                rate: num(get(r, "rate")?)?,
                base: num(get(r, "base_per_taxpayer")?)?,
                income_start: num(get(r, "income_start_per_taxpayer")?)?,
                agi_limit: num(get(r, "agi_limit_per_taxpayer")?)?,
            }),
            None => None,
        };
        let eitc = if p.in_effect("eitc_rate")? { Some((p.f("eitc_rate")?, opt("eitc_rate_young_child")?)) } else { None };
        let working_family = if p.in_effect("working_family_index")? {
            let index = p.f("working_family_index")?;
            let shares = params::matrix(&serde_yaml::Value::Sequence(vec![p.value("working_family_shares", &[])?.clone()]))?.remove(0);
            let thresholds = params::matrix(p.value("working_family_thresholds", &[])?)?;
            let tables = thresholds
                .iter()
                .map(|row| {
                    let mut rows: Vec<Vec<f64>> = row.iter().zip(&shares).map(|(t, s)| vec![t * index, *s]).collect();
                    rows.push(vec![1.0e20, *shares.last().unwrap()]);
                    Interp::new(&rows)
                })
                .collect();
            Some(WorkingFamily {
                index,
                loss_limit: num(resolve_year(get(&capital_gains, "net_capital_loss_limit")?, year)?)?,
                earned_min: p.f("working_family_earned_min")?,
                investment_max: p.f("working_family_investment_max")?,
                tables,
            })
        } else {
            None
        };
        let dependent_care = if p.in_effect("dependent_care_shares")? {
            let rows = params::brackets(p.value("dependent_care_shares", &[])?)?;
            Some(DependentCare {
                uppers: Tiers::new(rows.iter().map(|r| r[0]).collect(), false),
                shares: rows.iter().map(|r| r[1]).collect(),
                last_upper: rows.last().unwrap()[0],
                per_child: p.f("dependent_care_expense_per_child")?,
                poverty: pair(p.value("poverty_line", &[])?)?,
                agi_limit: pair(p.value("dependent_care_agi_limit", &[])?)?,
            })
        } else {
            None
        };
        let kids = match p.opt("kids_credit")? {
            Some(k) => Some(Kids {
                amount: num(get(k, "amount")?)?,
                child_limit: num(get(k, "child_limit")?)?,
                start: num(get(k, "reduction_start")?)?,
                width: num(get(k, "reduction_width")?)?,
            }),
            None => None,
        };
        let phaseout: Vec<(f64, f64)> = params::brackets(p.value("federal_tax_limit_phaseout", &[])?)?.into_iter().map(|r| (r[0], r[1])).collect();
        let _ = working_family.as_ref().map(|w| w.index);
        Ok(Self {
            year,
            unemployment,
            interest_dividend_exclusion: opt("interest_dividend_exclusion")?,
            rebate: match p.opt("federal_rebate_subtraction")? {
                Some(r) => Some((num(get(r, "taxpayer")?)?, num(get(r, "dependent")?)?)),
                None => None,
            },
            fed_limit: p.f("federal_tax_limit")?,
            fed_limit_phaseout: phaseout,
            standard_share,
            standard: if p.in_effect("standard_deduction")? { status_direct(p.value("standard_deduction", &[])?)? } else { [0.0; 4] },
            standard_index: if p.in_effect("standard_deduction")? { p.f("standard_deduction_index")? } else { 1.0 },
            aged_standard,
            salt_cap: opt("salt_cap")?,
            itemized_limit,
            charity_nonitemizer: opt("charity_nonitemizer")?,
            exemption_amount: opt("exemption_amount")?,
            schedule,
            minimum_tax,
            child_care_share: if two_earner.is_none() { opt("child_care_federal_share")? } else { None },
            two_earner,
            child_care_rate: if p.in_effect("child_care_rate")? {
                Some(ChildCareRate {
                    table: Interp::new(&params::brackets(p.value("child_care_rate", &[])?)?),
                    per_child: p.f("child_care_expense_per_child")?,
                })
            } else {
                None
            },
            elderly_share: opt("elderly_credit_share")?,
            exemption_credit,
            retirement,
            eitc,
            working_family,
            dependent_care,
            multiplier: p.f("tax_multiplier")?,
            kicker: if year == 2019 || p.in_effect("kicker")? { opt("kicker")? } else { None },
            kids,
            dividend_adjustment: dividend_adjustment(root)?,
        })
    }
}

pub fn tax(i: &Inp, f: &Fed, p: &OrParams) -> f64 {
    let y = p.year;
    let fs = i.fs();
    let is_joint = fs == Fs::Joint;
    let is_sep = fs == Fs::Separate;
    let is_hoh = fs == Fs::Hoh;
    let single_like = fs == Fs::Single || is_sep;
    let sep = i.sepret();
    let txp = i.taxpayer_count();
    let aged = i.aged_count();
    let depx = i.depx;
    let fed_agi = f.agi;
    let exemps = federal_exemption_count(y, i);
    let salt = f.ded;
    let ui = unemployment_total(i);
    let household = household_income(i, p.dividend_adjustment);

    // ---- AGI ----
    let mut agi = fed_agi;
    if let Some(amount) = p.interest_dividend_exclusion {
        let divall = i.dividends + p.dividend_adjustment - dividend_exclusion_addback(y, i, f, p.dividend_adjustment);
        let exclusion = (divall + i.intrec).min(amount * txp);
        agi = agi + f.pre87.twoded - exclusion.max(0.0);
    }
    if let Some(u) = &p.unemployment {
        let untax = (u.share * (agi - u.threshold[fs as usize]).max(0.0)).min(ui);
        agi = agi - f.taxable_unemployment + untax;
    }
    if y >= 1984 {
        agi -= f.taxable_social_security;
    }
    let agi = agi.max(0.0);

    // ---- federal values ----
    let (deducp, taxbc, fed_credit) = if y <= 1986 {
        (pre1987_gross_itemized(i, f), f.pre87.taxbc, f.pre87.credit)
    } else {
        (f.itemized_deduction, f.regular_tax, f.nonrefundable_credits)
    };
    let fed_ccc = f.federal_chcr;

    // ---- federal tax subtraction, limited by AGI ----
    let mut fedtax = (taxbc - fed_credit).max(0.0);
    if y == 1997 && !f.forced_itemize {
        fedtax = taxbc.max(0.0);
    }
    if let Some((taxpayer, dependent)) = p.rebate {
        fedtax = (fedtax - taxpayer * txp - dependent * depx).max(0.0);
    }
    fedtax = (fedtax - f.making_work_pay).max(0.0);
    fedtax = (fedtax - f.cares).max(0.0);
    if y == 2021 {
        fedtax = (fedtax - f.actc).max(0.0);
    }
    let units = if is_joint || is_hoh { 2.0 } else { 1.0 };
    let fedlim = p
        .fed_limit_phaseout
        .iter()
        .find(|(bound, _)| agi < bound * units)
        .map_or(0.0, |(_, divisor)| p.fed_limit / sep / divisor);
    let fedtax = fedtax.max(0.0).min(fedlim);

    // ---- standard deduction ----
    let mut stded = match &p.standard_share {
        Some(c) => (c.rate * agi).max(c.low / sep).min(c.high / sep),
        None => p.standard[fs as usize] * p.standard_index,
    };
    if let Some(a) = &p.aged_standard {
        stded += aged * a.addition[fs as usize];
        // Returns with no taxpayer (dependent filers).
        let dependent_std = match a.earned_addition {
            Some(addition) => a.floor.max(f.earned_income + addition),
            None => a.floor,
        };
        if txp < 1.0 {
            stded = dependent_std;
        }
    }

    // ---- itemized deductions ----
    let mut tx = if y <= 2017 { salt } else { 0.0 };
    let mut xtot = 0.0;
    if y >= 1991 {
        let xlin8 = match p.salt_cap {
            Some(cap) => (cap / sep).min(i.proptax + i.otheritem),
            None => tx + i.proptax + i.otheritem,
        };
        xtot = xlin8 + i.mortgage + i.charity_cash;
        if let Some(l) = &p.itemized_limit {
            let phas92 = match &l.status_factor {
                None => l.threshold * l.index / sep,
                Some(factor) => l.index * l.threshold * factor[fs as usize],
            };
            let over = fed_agi - phas92;
            let xconst = (l.share * xtot).min(l.rate * over);
            if xtot > 0.0 && over > 0.0 {
                tx = (xtot - xconst) / xtot * tx;
            }
        }
    }
    let mut xitded = if y <= 2017 {
        let limited = (deducp - tx).max(0.0);
        if y >= 1992 && aged > 0.0 { deducp.max(0.0) } else { limited }
    } else {
        let mut xitded = xtot;
        if let Some(cap) = p.charity_nonitemizer {
            xitded -= if f.itemizes { 0.0 } else { i.charity_cash.min(cap) };
        }
        xitded
    };
    if !f.forced_itemize {
        xitded = 0.0;
    }
    let deduc = stded.max(xitded);
    let exemp = p.exemption_amount.map_or(0.0, |amount| exemps * amount);
    let taxinc = (agi - deduc - exemp - fedtax).max(0.0);

    // ---- tax ----
    let mut statax = match &p.schedule {
        Schedule::Split(b) => {
            let num = if single_like { 1.0 } else { 2.0 };
            num * b.tax(taxinc / num)
        }
        Schedule::ByStatus { single, married } => if single_like { single.tax(taxinc) } else { married.tax(taxinc) },
    };
    if let Some(m) = &p.minimum_tax {
        let prefs = f.pre87.pref;
        let qualifies = prefs > m.preference / sep || (agi > m.agi / sep && prefs > m.agi_preference / sep);
        statax += if qualifies { m.table.tax(prefs) } else { 0.0 };
    }
    statax = statax.max(0.0);
    let taxbc_state = statax;

    // ---- nonrefundable credits ----
    let mut ecred = 0.0;
    let mut chcr = 0.0;
    if let Some(t) = &p.two_earner {
        if y <= 1978 {
            ecred = t.rate * f.pre87.twoded;
            chcr = t.share * fed_ccc;
        } else {
            let taxmax = (f.fiitax - f.pre87.credit).max(0.0);
            ecred = (t.rate * taxmax).min(t.rate * f.pre87.twoded);
            chcr = (t.share * taxmax).min(t.share * fed_ccc);
        }
    } else if let Some(share) = p.child_care_share {
        chcr = share * fed_ccc;
    }
    if let Some(share) = p.elderly_share {
        ecred = share * f.federal_elder;
    }
    if let Some(c) = &p.child_care_rate {
        let children = depx.min(2.0) * c.per_child;
        let expenses = f.earned_income.min(i.childcare).min(children);
        let rate = c.table.eval(f.taxable_income.max(0.0));
        chcr = if fed_ccc > 0.0 { rate * expenses } else { 0.0 };
    }
    let mut gcred = 0.0;
    if let Some(c) = &p.exemption_credit {
        gcred = c.amount * exemps;
        if let Some(limit) = c.income_limit {
            let units_credit = if is_joint || is_hoh { 2.0 } else { 1.0 };
            gcred = if fed_agi <= limit * units_credit { gcred } else { 0.0 };
        }
        if let Some(ph) = &c.phaseout {
            let phasa = if is_hoh {
                ph.hoh_factor * ph.base
            } else if is_joint || is_sep {
                ph.married_factor * ph.base / sep
            } else {
                ph.base
            };
            let steps = ((fed_agi - phasa) / (ph.step / sep) + 1.0).floor();
            let reduced = (ph.minimum * exemps).max(gcred * (1.0 - steps * ph.rate));
            if fed_agi > phasa {
                gcred = reduced;
            }
        }
    }
    // Retirement income credit for taxpayers 65 or older (1991+).
    let mut rcred = 0.0;
    if let Some(r) = &p.retirement {
        let room = ((r.base * txp - i.gssi).max(0.0) - (household - r.income_start * txp).max(0.0)).max(0.0);
        if aged > 0.0 && agi <= r.agi_limit * txp {
            rcred = r.rate * i.pensions.min(room);
        }
    }
    let earncr = match p.eitc {
        Some((rate, young)) => {
            let rate = match young {
                Some(young_rate) if i.children_under_3 > 0.0 => young_rate,
                _ => rate,
            };
            rate * f.eitc
        }
        None => 0.0,
    };

    // ---- working family child care credit 1997-2015 ----
    let numhh = exemps.clamp(1.0, 8.0).floor();
    let mut famcr = 0.0;
    if let Some(w) = &p.working_family {
        let capgn = (i.stcg + i.ltcg).max(-w.loss_limit / sep);
        let investment = i.dividends + p.dividend_adjustment + i.intrec + capgn.max(0.0);
        let eligible = !is_sep && f.earned_income >= w.earned_min && investment < w.investment_max;
        let share_by_size = w.tables[(numhh as usize).clamp(1, w.tables.len()) - 1].eval(fed_agi);
        famcr = if eligible { i.childcare * share_by_size } else { 0.0 };
    }
    let mut credit = ecred + chcr + gcred + rcred;
    if (1997..=2005).contains(&y) {
        credit += earncr;
        if y <= 2002 {
            credit += famcr;
        }
    }
    let mut statax = (statax - credit).max(0.0);
    if y >= 2003 {
        statax -= famcr;
    }
    if y >= 2006 {
        statax -= earncr;
    }

    // ---- working family household and dependent care credit 2016 on ----
    if let Some(d) = &p.dependent_care {
        let num = exemps.floor();
        let children = i.dep13.min(2.0).floor();
        let mut expens = i.childcare.min(d.per_child * children).min(f.earned_income);
        if is_joint {
            expens = expens.min(i.pwages).min(i.swages).max(0.0);
        }
        let pov = d.poverty.0 + d.poverty.1 * (num - 1.0);
        let base = agi.max(0.0);
        let share_of_poverty = d.shares[d.uppers.index(base / pov)];
        let in_range = base > 0.0 && base <= d.last_upper * pov && base < d.agi_limit.0 + d.agi_limit.1 * (numhh - 2.0);
        if numhh > 1.0 && num > 1.0 && in_range {
            statax -= share_of_poverty * expens;
        }
    }
    if statax > 0.0 {
        statax *= p.multiplier;
    }
    if let Some(kicker) = p.kicker {
        statax -= kicker * taxbc_state;
    }
    if let Some(k) = &p.kids {
        let reduction = ((agi - k.start) / k.width).clamp(0.0, 1.0);
        statax -= k.amount * i.dep6.min(k.child_limit) * (1.0 - reduction);
    }
    statax
}
