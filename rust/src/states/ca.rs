//! California income tax (port of `calculators.states.ca`).

use std::path::Path;

use crate::common::Brackets;
use crate::federal::{Fed, Inp};
use crate::params::{self, get, load, num, resolve_year, status_direct, Fs, YearParams, STATUS_KEYS};
use crate::states::{dividend_adjustment, household_income, law_year_is_exact, Interp};

struct SeHoliday {
    threshold: f64,
    rate: f64,
    flat: f64,
}

enum ExemptionCredit {
    Amounts { per_taxpayer: f64, per_dependent: f64 },
    Personal { phase_start: [f64; 4], step: f64, per_person: f64, per_dependent: f64, per_credit: f64 },
    Base { base: f64, index: f64, per_dependent: Option<f64> },
}

struct Phaseout {
    per_credit: f64,
    step: f64,
    upper: Option<f64>,
    upper_index: Option<f64>,
}

struct Limit {
    rate: f64,
    floor_index: f64,
    floor_single_or_hoh: f64,
    floor_other: f64,
    upper_hoh: f64,
    upper_other: f64,
    agi_share: f64,
}

enum LowIncome {
    None,
    Flat { base: f64, agi_floor: f64, rate: f64, limit: f64 },
    Share { index: f64, rows: Vec<(f64, f64)> },
}

enum Care {
    Expenses { rate: f64, cap_per_child: f64, cap_total: f64 },
    Dict { threshold: f64, above: f64, below: f64 },
    Rows(Vec<(f64, f64)>),
    Flat(f64),
}

enum Renter {
    None,
    PerTaxpayer { per_taxpayer: f64, agi_limit: f64, index: f64 },
    Banded { single: f64, other: f64, limit_single: f64, limit_other: f64, band_single: f64, band_other: f64, index: f64 },
    Flat { single: f64, other: f64 },
}

struct Caleitc {
    max_credit: [f64; 4],
    max_earned: [f64; 4],
    second: Option<([f64; 4], [f64; 4], [f64; 4])>,
}

struct Schedule {
    phase_in: [f64; 4],
    amount: [f64; 4],
    final_start: [f64; 4],
    final_end: f64,
}

enum Eitc {
    Caleitc(Caleitc),
    Schedule(Schedule),
}

struct Young {
    amount: f64,
    threshold: f64,
    rate: f64,
    loss_threshold: Option<f64>,
}

pub struct CaParams {
    year: i64,
    aif: f64,
    capital: Option<(f64, f64)>,
    aged_exclusion: Option<(f64, f64)>,
    se_holiday: Option<SeHoliday>,
    stded_per_taxpayer: f64,
    phaseout_threshold: [f64; 4],
    phaseout_index: Option<f64>,
    itemized_limit: Option<(f64, f64)>,
    table_single: Brackets,
    table_hoh: Brackets,
    exemption: ExemptionCredit,
    phaseout: Option<Phaseout>,
    limit: Option<Limit>,
    low_income: LowIncome,
    care: Care,
    refundable_care: bool,
    amt_rate: Option<f64>,
    amt_exemption: [f64; 4],
    amt_phaseout_start: [f64; 4],
    amt_phaseout_rate: f64,
    renter: Renter,
    mental_health: Option<(f64, f64)>,
    eitc: Option<Eitc>,
    minimum_age: Option<f64>,
    disqualified: f64,
    young: Option<Young>,
    dividend_adjustment: f64,
}

/// Convert upper-bound brackets to start-rate brackets.
fn start_rate(rows: &[Vec<f64>], scale: f64) -> Brackets {
    let mut out = vec![vec![0.0, rows[0][1]]];
    for k in 1..rows.len() {
        let prev = rows[k - 1][0];
        out.push(vec![if prev < 1.0e19 { prev * scale } else { prev }, rows[k][1]]);
    }
    Brackets::new(&out)
}

fn four(v: &serde_yaml::Value) -> Result<[f64; 4], String> {
    let s = v.as_sequence().ok_or("expected four values")?;
    Ok([num(&s[0])?, num(&s[1])?, num(&s[2])?, num(&s[3])?])
}

impl CaParams {
    pub fn load(root: &Path, year: i64) -> Result<Self, String> {
        let doc = load(root, "states/ca/income_tax.yaml")?;
        law_year_is_exact(root, &doc, year)?;
        let amt_doc = load(root, "national/amt.yaml")?;
        let p = YearParams::new(&doc, year);
        let opt = |key: &str| -> Result<Option<f64>, String> { Ok(if p.in_effect(key)? { Some(p.f(key)?) } else { None }) };
        let aif = p.f("indexing_factor")?;
        let scale = p.f("bracket_inflation")?;

        let tables = p.value("brackets", &[])?;
        let (single_rows, hoh_rows) = (params::brackets(get(tables, "single")?)?, params::brackets(get(tables, "head_of_household")?)?);
        let (table_single, table_hoh) = if year <= 1981 {
            // Bounds are rounded to $10, halves away from zero.
            let round = |rows: &[Vec<f64>]| -> Vec<Vec<f64>> { rows.iter().map(|r| vec![(r[0] * scale / 10.0 + 0.5).floor() * 10.0, r[1]]).collect() };
            (start_rate(&round(&single_rows), 1.0), start_rate(&round(&hoh_rows), 1.0))
        } else {
            (start_rate(&single_rows, scale), start_rate(&hoh_rows, scale))
        };
        let phaseout_threshold = status_direct(get(&doc, "phaseout_threshold")?)?;

        let exemption = if p.in_effect("exemption_credit_amounts")? {
            let c = p.value("exemption_credit_amounts", &[])?;
            let indexed = get(c, "indexed")?.as_bool().ok_or("expected a boolean")?;
            let (pt, pd) = (num(get(c, "per_taxpayer")?)?, num(get(c, "per_dependent")?)?);
            if indexed {
                ExemptionCredit::Amounts { per_taxpayer: (pt * aif + 0.5).floor(), per_dependent: (pd * aif + 0.5).floor() }
            } else {
                ExemptionCredit::Amounts { per_taxpayer: pt, per_dependent: pd }
            }
        } else if p.in_effect("exemption_credit_personal")? {
            let ph = get(&doc, "exemption_credit_phaseout")?;
            ExemptionCredit::Personal {
                phase_start: status_direct(p.value("exemption_credit_phaseout_start", &[])?)?,
                step: num(get(ph, "step")?)?,
                per_person: p.f("exemption_credit_personal")?,
                per_dependent: p.f("exemption_credit_per_dependent")?,
                per_credit: num(get(ph, "per_credit")?)?,
            }
        } else if !p.in_effect("exemption_credit_per_dependent")? {
            ExemptionCredit::Base { base: num(get(&doc, "exemption_credit_base")?)?, index: p.f("exemption_credit_index")?, per_dependent: None }
        } else {
            ExemptionCredit::Base {
                base: num(get(&doc, "exemption_credit_base")?)?,
                index: p.f("exemption_credit_index")?,
                per_dependent: Some(p.f("exemption_credit_per_dependent")?),
            }
        };
        let phaseout = if year >= 1991 && !p.in_effect("exemption_credit_personal")? {
            let ph = get(&doc, "exemption_credit_phaseout")?;
            Some(Phaseout {
                per_credit: num(get(ph, "per_credit")?)?,
                step: num(get(ph, "step")?)?,
                upper: opt("exemption_credit_phaseout_upper")?,
                upper_index: if year <= 1998 { Some(p.f("exemption_credit_upper_index")?) } else { None },
            })
        } else {
            None
        };
        let limit = match (&phaseout, p.opt("exemption_credit_limit")?) {
            (Some(_), Some(l)) => Some(Limit {
                rate: num(get(l, "rate")?)?,
                floor_index: num(get(l, "floor_index")?)?,
                floor_single_or_hoh: num(get(l, "floor_single_or_hoh")?)?,
                floor_other: num(get(l, "floor_other")?)?,
                upper_hoh: num(get(l, "upper_hoh")?)?,
                upper_other: num(get(l, "upper_other")?)?,
                agi_share: num(get(l, "agi_share")?)?,
            }),
            _ => None,
        };
        let low_income = if p.in_effect("low_income_credit_flat")? {
            let l = p.value("low_income_credit_flat", &[])?;
            LowIncome::Flat {
                base: num(get(l, "base")?)?,
                agi_floor: num(get(l, "agi_floor")?)?,
                rate: num(get(l, "reduction_rate")?)?,
                limit: num(get(l, "household_income_limit")?)?,
            }
        } else if p.in_effect("low_income_credit_share")? {
            LowIncome::Share {
                index: p.f("low_income_credit_index")?,
                rows: params::brackets(p.value("low_income_credit_share", &[])?)?.into_iter().map(|r| (r[0], r[1])).collect(),
            }
        } else {
            LowIncome::None
        };
        let care = if p.in_effect("child_care_credit_expenses")? {
            let c = p.value("child_care_credit_expenses", &[])?;
            Care::Expenses { rate: num(get(c, "rate")?)?, cap_per_child: num(get(c, "cap_per_child")?)?, cap_total: num(get(c, "cap_total")?)? }
        } else {
            let share = p.value("child_care_credit_federal_share", &[])?;
            if share.is_mapping() {
                Care::Dict { threshold: num(get(share, "threshold")?)?, above: num(get(share, "above")?)?, below: num(get(share, "below")?)? }
            } else if share.is_sequence() {
                Care::Rows(params::brackets(share)?.into_iter().map(|r| (r[0], r[1])).collect())
            } else {
                Care::Flat(num(share)?)
            }
        };
        let amt_exemption = if p.in_effect("amt_exemption")? {
            status_direct(p.value("amt_exemption", &[])?)?
        } else if year >= 1987 {
            let node = get(&amt_doc, "exemption")?;
            let mut out = [0.0; 4];
            for (k, key) in STATUS_KEYS.iter().enumerate() {
                out[k] = num(resolve_year(get(node, key)?, year)?)?;
            }
            out
        } else {
            [0.0; 4]
        };
        let renter = if p.in_effect("renter_credit")? {
            let rc = p.value("renter_credit", &[])?;
            if rc.get("per_taxpayer").is_some() {
                Renter::PerTaxpayer {
                    per_taxpayer: num(get(rc, "per_taxpayer")?)?,
                    agi_limit: num(get(rc, "agi_limit_per_taxpayer")?)?,
                    index: p.f("renter_credit_index")?,
                }
            } else if rc.get("agi_limit").is_some() {
                let limit = get(rc, "agi_limit")?;
                let band = get(rc, "half_band")?;
                Renter::Banded {
                    single: num(get(rc, "single")?)?,
                    other: num(get(rc, "other")?)?,
                    limit_single: num(get(limit, "single")?)?,
                    limit_other: num(get(limit, "other")?)?,
                    band_single: num(get(band, "single")?)?,
                    band_other: num(get(band, "other")?)?,
                    index: p.f("renter_credit_index")?,
                }
            } else {
                Renter::Flat { single: num(get(rc, "single")?)?, other: num(get(rc, "other")?)? }
            }
        } else {
            Renter::None
        };
        let eitc = if year >= 2015 {
            Some(if p.in_effect("caleitc")? {
                let ce = p.value("caleitc", &[])?;
                Eitc::Caleitc(Caleitc {
                    max_credit: four(get(ce, "max_credit")?)?,
                    max_earned: four(get(ce, "max_earned")?)?,
                    second: if ce.get("second_phaseout_start").is_some() {
                        Some((four(get(ce, "second_phaseout_start")?)?, four(get(ce, "credit_at_second_phaseout")?)?, four(get(ce, "income_limit")?)?))
                    } else {
                        None
                    },
                })
            } else {
                let ce = p.value("caleitc_schedule", &[])?;
                let factor = num(get(ce, "adjustment_factor")?)?;
                let rates = four(get(ce, "phase_in_rate")?)?;
                Eitc::Schedule(Schedule {
                    phase_in: [rates[0] * factor, rates[1] * factor, rates[2] * factor, rates[3] * factor],
                    amount: four(get(ce, "earned_income_amount")?)?,
                    final_start: four(get(ce, "final_start")?)?,
                    final_end: num(get(ce, "final_end")?)?,
                })
            })
        } else {
            None
        };
        let young = if year >= 2015 {
            match p.opt("young_child_credit")? {
                Some(yc) => Some(Young {
                    amount: num(get(yc, "amount")?)?,
                    threshold: num(get(yc, "earnings_threshold")?)?,
                    rate: num(get(yc, "phaseout_rate")?)?,
                    loss_threshold: if year >= 2022 { Some(num(get(yc, "loss_threshold")?)?) } else { None },
                }),
                None => None,
            }
        } else {
            None
        };
        Ok(Self {
            year,
            aif,
            capital: if year <= 1986 { Some((p.f("capital_gains_inclusion")?, p.f("capital_loss_limit")?)) } else { None },
            aged_exclusion: if year <= 1986 {
                match p.opt("aged_exclusion")? {
                    Some(e) => Some((num(get(e, "amount")?)?, num(get(e, "income_limit")?)?)),
                    None => None,
                }
            } else {
                None
            },
            se_holiday: match p.opt("se_deduction_holiday_addback")? {
                Some(h) if year > 1986 => Some(SeHoliday { threshold: num(get(h, "threshold")?)?, rate: num(get(h, "rate")?)?, flat: num(get(h, "flat")?)? }),
                _ => None,
            },
            stded_per_taxpayer: num(p.value("standard_deduction_per_taxpayer", &[])?)?,
            phaseout_threshold,
            phaseout_index: if year >= 1991 { opt("phaseout_index")? } else { None },
            itemized_limit: match p.opt("itemized_limitation")? {
                Some(l) => Some((num(get(l, "rate")?)?, num(get(l, "cap_rate")?)?)),
                None => None,
            },
            table_single,
            table_hoh,
            exemption,
            phaseout,
            limit,
            low_income,
            care,
            refundable_care: p.value("child_care_credit_refundable", &[])?.as_bool().ok_or("expected a boolean")?,
            amt_rate: if year >= 1987 { Some(p.f("amt_rate")?) } else { None },
            amt_exemption,
            amt_phaseout_start: if year >= 1987 { status_direct(p.value("amt_phaseout_start", &[])?)? } else { [0.0; 4] },
            amt_phaseout_rate: if year >= 1987 { num(get(&doc, "amt_exemption_phaseout_rate")?)? } else { 0.0 },
            renter,
            mental_health: match p.opt("mental_health_tax")? {
                Some(m) => Some((num(get(m, "rate")?)?, num(get(m, "threshold")?)?)),
                None => None,
            },
            eitc,
            minimum_age: opt("eitc_childless_minimum_age")?,
            disqualified: if year >= 2015 { p.f("eitc_disqualified_income_limit")? } else { 0.0 },
            young,
            dividend_adjustment: dividend_adjustment(root)?,
        })
    }
}

pub fn tax(i: &Inp, f: &Fed, p: &CaParams) -> f64 {
    let y = p.year;
    let fs = i.fs();
    let is_hoh = fs == Fs::Hoh;
    let is_single = fs == Fs::Single;
    let is_sep = fs == Fs::Separate;
    let sep = i.sepret();
    let ajnt = if fs == Fs::Joint { 2.0 } else { 1.0 };
    let txp_raw = i.taxpayer_count();
    let ca_txp = if is_hoh { 2.0 } else { txp_raw };
    let aged = i.aged_count();
    let depx = i.depx;
    let salt = f.ded;
    let setax = f.payroll.setax;
    let hy = household_income(i, p.dividend_adjustment);
    let aif = p.aif;
    let wages = i.pwages + i.swages;

    // ---- AGI ----
    let ca_agi = if y <= 1986 {
        let (inclusion, loss_limit) = p.capital.expect("pre-1987 capital gains parameters");
        let mut cg = i.stcg + inclusion * i.ltcg;
        if cg < 0.0 {
            cg = -cg.abs().min(loss_limit / sep);
        }
        let gross_se = i.psemp + i.ssemp;
        let mut totinc = wages + i.intrec + (i.dividends + p.dividend_adjustment) + cg + gross_se + i.pensions + i.otherprop + i.nonprop;
        if let Some((amount, income_limit)) = p.aged_exclusion {
            let limit = income_limit * aged / sep;
            let oldex = (amount * aged - 0.5 * (totinc - limit).max(0.0)).max(0.0);
            totinc -= oldex;
        }
        totinc.max(0.0)
    } else {
        // Federal AGI less taxable unemployment compensation and Social Security.
        let mut subtra = f.taxable_unemployment + f.taxable_social_security;
        if let Some(h) = &p.se_holiday {
            subtra -= if setax <= h.threshold { (h.rate - 0.5) * setax } else { h.flat };
        }
        f.agi - subtra
    };

    // ---- standard deduction ----
    let ca_stded = if y <= 1978 { p.stded_per_taxpayer * ca_txp } else { p.stded_per_taxpayer * aif * ca_txp };

    // ---- itemized deduction ----
    let mut ca_reduce = 0.0;
    let mut xitded;
    if y <= 1986 {
        xitded = if f.pre87.itemizes { f.pre87.deduc - salt } else { 0.0 };
    } else {
        let mut base = (f.salt_capped - salt + i.mortgage).max(0.0);
        if y >= 2018 {
            // California never adopted the federal SALT cap.
            base = (i.proptax + i.otheritem + i.mortgage).max(0.0);
        }
        if let (Some((rate, cap_rate)), Some(index)) = (p.itemized_limit, p.phaseout_index) {
            let threshold = p.phaseout_threshold[fs as usize] * index;
            ca_reduce = if f.agi > threshold { (cap_rate * base).min(rate * (f.agi - threshold)) } else { 0.0 };
        }
        xitded = (base - ca_reduce).max(0.0);
        if y == 1999 && !f.forced_itemize {
            xitded = 0.0;
        }
    }
    let deduc = if (1982..=1986).contains(&y) {
        if ca_stded > xitded { 0.0 } else { xitded - ca_stded }
    } else {
        ca_stded.max(xitded)
    };
    let taxinc = (ca_agi - deduc).max(0.0);

    // ---- tax: joint returns use the single schedule on half their income, doubled ----
    let stat_single = p.table_single.tax(taxinc / ajnt) * ajnt;
    let stat_hoh = p.table_hoh.tax(taxinc);
    let mut statax = if is_hoh { stat_hoh } else { stat_single };

    // ---- exemption credits ----
    let dep_reduced = if y <= 1986 && is_hoh { (depx - 1.0).max(0.0) } else { depx };
    let mut excrd = match &p.exemption {
        ExemptionCredit::Amounts { per_taxpayer, per_dependent } => per_taxpayer * ca_txp + per_dependent * dep_reduced,
        ExemptionCredit::Personal { phase_start, step, per_person, per_dependent, per_credit } => {
            let steps = ((f.agi - phase_start[fs as usize]).max(0.0) / (step / sep)).ceil();
            let reduction = steps * per_credit;
            (txp_raw + aged) * (per_person - reduction).max(0.0) + dep_reduced * (per_dependent - reduction).max(0.0)
        }
        ExemptionCredit::Base { base, index, per_dependent: None } => base * index * (txp_raw + aged + dep_reduced),
        ExemptionCredit::Base { base, index, per_dependent: Some(per) } => base * index * (txp_raw + aged) + dep_reduced * per,
    };
    if let Some(ph) = &p.phaseout {
        let index = p.phaseout_index.unwrap_or(1.0);
        let phaded2 = p.phaseout_threshold[fs as usize] * index;
        let excrd_over_agi = match ph.upper {
            Some(upper) => {
                let excess_over = (f.agi - phaded2).max(0.0);
                let reduction_full = (dep_reduced + ca_txp + aged) * ph.per_credit * (f.agi - phaded2) / ph.step / sep;
                let upper_index = ph.upper_index.unwrap_or(1.0);
                if excess_over > upper * upper_index / sep { 0.0 } else { (excrd - reduction_full).max(0.0) }
            }
            None => {
                let num = 1.0 + ((f.agi - phaded2) / (ph.step / sep)).floor();
                (excrd - (dep_reduced + ca_txp + aged) * ph.per_credit * num).max(0.0)
            }
        };
        if f.agi > phaded2 {
            excrd = excrd_over_agi;
        }
        if let (Some(lim), Some(upper_index)) = (&p.limit, ph.upper_index) {
            let exc = if matches!(fs, Fs::Single | Fs::Hoh) { lim.floor_single_or_hoh * lim.floor_index } else { lim.floor_other * lim.floor_index * sep };
            let excess_thr = if is_hoh { lim.upper_hoh * upper_index } else { lim.upper_other * upper_index * sep };
            let under_std = ca_stded > xitded;
            let alt_a = statax - lim.rate * (f.agi - exc).min(0.0);
            let alt_b = statax - lim.rate * ((lim.agi_share * f.agi.max(0.0)).min(0.0) + i.otheritem + i.mortgage + taxinc - exc);
            let capped = if under_std { alt_a } else { alt_b };
            if ca_agi > phaded2 && ca_agi < excess_thr {
                excrd = capped.min(excrd);
            }
        }
    }
    // Dependent filers get no exemption credit.
    if i.is_dependent_filer() {
        excrd = 0.0;
    }
    let mut noncr = excrd;

    // ---- low-income credit ----
    let mut lowcr = match &p.low_income {
        LowIncome::None => 0.0,
        LowIncome::Flat { base, agi_floor, rate, limit } => {
            let base_amt = base * aif;
            if hy <= limit * ca_txp { (base_amt - (ca_agi - agi_floor) * rate).max(0.0).round() } else { 0.0 }
        }
        LowIncome::Share { index, rows } => {
            let div = if matches!(fs, Fs::Single | Fs::Separate) { 2.0 } else { 1.0 };
            let scaled: Vec<Vec<f64>> = rows.iter().map(|(lo, rate)| vec![lo * index / div, *rate]).collect();
            statax * Interp::new(&scaled).eval(ca_agi)
        }
    };
    if i.is_dependent_filer() {
        lowcr = 0.0;
    }
    noncr += lowcr;

    // ---- child and dependent care credit ----
    let chcr = match &p.care {
        Care::Expenses { rate, cap_per_child, cap_total } => {
            let chmax = (cap_per_child * depx).min(*cap_total);
            let chwage = wages.max(0.0);
            rate * chmax.min(chwage).min(i.childcare)
        }
        care => {
            let federal = if y >= 2000 { f.ccc_uncapped } else { f.federal_chcr };
            let rate = match care {
                Care::Dict { threshold, above, below } => if ca_agi >= *threshold { *above } else { *below },
                Care::Rows(rows) => rows.iter().find(|(upper, _)| f.agi <= *upper).map_or(0.0, |(_, share)| *share),
                Care::Flat(share) => *share,
                Care::Expenses { .. } => 0.0,
            };
            rate * federal
        }
    };
    statax = (statax - noncr).max(0.0);

    // ---- alternative minimum tax (1987+) ----
    let mut amt = 0.0;
    if let Some(amt_rate) = p.amt_rate {
        let itemizing = xitded > ca_stded;
        let addprf = if itemizing { i.proptax + i.otheritem } else { ca_stded };
        let alminy = (addprf + taxinc - ca_reduce).max(0.0);
        let phaout = p.amt_phaseout_rate * (alminy - p.amt_phaseout_start[fs as usize]).max(0.0);
        let exclnt = (p.amt_exemption[fs as usize] - phaout).max(0.0);
        let alminc = (alminy - exclnt).max(0.0);
        amt = (alminc * amt_rate - statax).max(0.0);
    }
    statax += amt;

    // ---- renter's credit, for renters paying no property tax ----
    let renter = i.proptax < 1.0 && i.rentpaid > 0.0;
    let mut rcred = match &p.renter {
        Renter::None => 0.0,
        Renter::PerTaxpayer { per_taxpayer, agi_limit, index } => {
            let units = if is_single || is_sep { 1.0 } else { 2.0 };
            if ca_agi <= agi_limit * units * index { per_taxpayer * units } else { 0.0 }
        }
        Renter::Banded { single, other, limit_single, limit_other, band_single, band_other, index } => {
            let use_single = is_single || is_sep;
            let limit = (if use_single { *limit_single } else { *limit_other }) * index;
            let band = (if use_single { *band_single } else { *band_other }) * index;
            let amount = if use_single { *single } else { *other };
            if ca_agi <= limit {
                amount
            } else if ca_agi <= limit + band {
                0.5 * amount
            } else {
                0.0
            }
        }
        Renter::Flat { single, other } => {
            if y <= 1978 { *single } else if is_single { *single } else { other / sep }
        }
    };
    if !renter {
        rcred = 0.0;
    }
    statax = (statax - rcred).max(0.0);

    statax = if p.refundable_care { statax - chcr } else { (statax - chcr).max(0.0) };
    if let Some((rate, threshold)) = p.mental_health {
        statax += rate * (taxinc - threshold).max(0.0);
    }

    // ---- California EITC and Young Child Tax Credit ----
    let mut earncr = 0.0;
    let mut young_credit = 0.0;
    if let Some(eitc) = &p.eitc {
        let ieic = i.dep18.clamp(0.0, 3.0) as usize;
        let earned = if y <= 2016 { wages } else { f.earned_income };
        earncr = match eitc {
            Eitc::Caleitc(ce) => {
                let cr = ce.max_credit[ieic];
                let am = ce.max_earned[ieic];
                let posagi = f.agi.max(0.0);
                let base1 = if earned < 0.5 * am { earned } else { (am - earned).max(0.0) };
                let mut credit = if earned > 0.0 { base1 * cr / (0.5 * am) } else { 0.0 };
                let base_agi = (am - posagi).max(0.0);
                if posagi >= 0.5 * am {
                    credit = credit.min(base_agi * cr / (0.5 * am));
                }
                if let Some((start, at_second, income_limit)) = &ce.second {
                    let ym1 = start[ieic];
                    let em1 = at_second[ieic];
                    let limit = income_limit[ieic];
                    let tgbeta = em1 / (limit - ym1);
                    let base2 = (limit - posagi.max(earned)).max(0.0);
                    let earncr2 = if earned > 0.0 { base2 * tgbeta } else { 0.0 };
                    if earned > ym1 || posagi > ym1 {
                        credit = earncr2;
                    }
                }
                credit
            }
            Eitc::Schedule(s) => {
                let rate_in = s.phase_in[ieic];
                let amount = s.amount[ieic];
                let final_start = s.final_start[ieic];
                let maximum = amount * rate_in;
                let first_range = (maximum - final_start) / rate_in;
                let second_start = amount + first_range;
                let at = |income: f64| -> f64 {
                    let after_first = income.min(amount) * rate_in - (income - amount).max(0.0).min(first_range) * rate_in;
                    let second_share = ((income - second_start) / (s.final_end - second_start)).min(1.0);
                    if income > second_start { after_first * (1.0 - second_share) } else { after_first }
                };
                let earned_now = f.earned_income;
                let credit = at(earned_now).min(at(earned_now.max(f.agi)));
                if f.agi < s.final_end { credit.max(0.0) } else { 0.0 }
            }
        };
        if let Some(minimum_age) = p.minimum_age {
            let older = i.page.max(i.sage);
            if f.num_children == 0.0 && older > 0.0 && older < minimum_age {
                earncr = 0.0;
            }
        }
        let disqy = (i.stcg + i.ltcg).max(0.0) + i.dividends + p.dividend_adjustment + i.intrec + i.otherprop.max(0.0);
        if disqy >= p.disqualified {
            earncr = 0.0;
        }
        if sep == 2.0 || i.is_dependent_filer() {
            earncr = 0.0;
        }
        // Young Child Tax Credit: requires a state EITC and an EITC-qualifying child.
        if let Some(yc) = &p.young {
            let mut gate = earncr > 0.0 && i.dep18 > 0.0;
            if let (Some(loss_threshold), Some(Eitc::Schedule(s))) = (yc.loss_threshold, &p.eitc) {
                let small_losses = f.agi < s.final_end && f.earned_income <= loss_threshold;
                gate = gate || (i.dep18 > 0.0 && small_losses);
            }
            gate = gate && i.dep6 > 0.0;
            let young_count = i.dep6.clamp(0.0, 1.0);
            let young_low = yc.amount * young_count;
            let young_high = (yc.amount - yc.rate * (earned - yc.threshold)).max(0.0);
            let young = if earned <= yc.threshold { young_low } else { young_high };
            young_credit = if gate { young } else { 0.0 };
        }
    }
    statax - earncr - young_credit
}
