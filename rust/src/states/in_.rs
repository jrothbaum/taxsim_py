//! Indiana income tax (port of `calculators.states.in_`).

use std::path::Path;

use crate::federal::{read_eitc, EitcRow, Fed, Inp};
use crate::params::{self, get, load, num, Fs, YearParams};
use crate::states::{dividend_adjustment, household_income, law_year_is_exact, unemployment_total, Interp};

struct Eitc2022 {
    /// The federal schedule for single filers by number of children (0 to 2).
    single: [Option<EitcRow>; 4],
}

struct EitcSchedule {
    max_credit: [f64; 3],
    phaseout_start: [f64; 3],
    phase_in_rate: [f64; 3],
    phaseout_rate: [f64; 3],
}

enum Eitc {
    None,
    Own { rate: f64, income_cap: f64, earnings_share: f64 },
    Federal { rate: f64, floor: f64, decoupled_2022: Option<Eitc2022>, schedule: Option<EitcSchedule> },
}

struct PropertyCredit {
    share_table: Interp,
    rent_share: f64,
    cap: f64,
    flat_amount: f64,
    flat_limit: f64,
}

struct ElderlyCredit {
    table: Interp,
    rows: Vec<(f64, f64)>,
    second_aged: f64,
}

struct ExemptionAddition {
    floor: f64,
    maximum: f64,
}

pub struct InParams {
    year: i64,
    rate: f64,
    tax_share: Option<f64>,
    dividend_exclusion: Option<f64>,
    ui_addback: Option<f64>,
    ui_threshold_single: f64,
    ui_threshold_joint: f64,
    renter_cap: Option<f64>,
    homeowner_cap: Option<f64>,
    earned_income_deduction: Option<(f64, f64)>,
    exemption_per_taxpayer: f64,
    exemption_per_dependent: Option<f64>,
    exemption_income_addition: Option<ExemptionAddition>,
    aged_low_income: Option<(f64, f64)>,
    dependent_exemption_addition: f64,
    eitc: Eitc,
    property: Option<PropertyCredit>,
    elderly: Option<ElderlyCredit>,
    elderly_agi_limit: f64,
    refund_credit: Option<f64>,
    dividend_adjustment: f64,
}

impl InParams {
    pub fn load(root: &Path, year: i64) -> Result<Self, String> {
        let doc = load(root, "states/in/income_tax.yaml")?;
        law_year_is_exact(root, &doc, year)?;
        let p = YearParams::new(&doc, year);
        let opt = |key: &str| -> Result<Option<f64>, String> { Ok(if p.in_effect(key)? { Some(p.f(key)?) } else { None }) };
        let rows = |v: &serde_yaml::Value| params::brackets(v);

        let ui = get(&doc, "ui_exclusion_threshold")?;
        let eitc = if p.in_effect("eitc_own")? {
            let own = p.value("eitc_own", &[])?;
            Eitc::Own {
                rate: num(get(own, "rate")?)?,
                income_cap: num(get(own, "income_cap")?)?,
                earnings_share: num(get(own, "earnings_share")?)?,
            }
        } else if p.in_effect("eitc_federal_share")? {
            let decoupled_2022 = if year == 2022 { Some(Eitc2022 { single: read_eitc(root, 2022)?[Fs::Single as usize] }) } else { None };
            let schedule = if year < 2022 && p.in_effect("eitc_schedule")? {
                let s = p.value("eitc_schedule", &[])?;
                let rates = get(&doc, "eitc_schedule_rates")?;
                let three = |v: &serde_yaml::Value| -> Result<[f64; 3], String> {
                    let l = v.as_sequence().ok_or("expected three values")?;
                    Ok([num(&l[0])?, num(&l[1])?, num(&l[2])?])
                };
                Some(EitcSchedule {
                    max_credit: three(get(s, "max_credit")?)?,
                    phaseout_start: three(get(s, "phaseout_start")?)?,
                    phase_in_rate: three(get(rates, "phase_in_rate")?)?,
                    phaseout_rate: three(get(rates, "phaseout_rate")?)?,
                })
            } else {
                None
            };
            Eitc::Federal { rate: p.f("eitc_federal_share")?, floor: p.f("eitc_federal_floor")?, decoupled_2022, schedule }
        } else {
            Eitc::None
        };
        let (property, elderly) = if p.in_effect("property_credit")? {
            let pc = p.value("property_credit", &[])?;
            let flat = p.value("elderly_credit_flat", &[])?;
            (
                Some(PropertyCredit {
                    share_table: Interp::new(&rows(get(pc, "share_table")?)?),
                    rent_share: num(get(pc, "rent_share")?)?,
                    cap: num(get(pc, "cap")?)?,
                    flat_amount: num(get(flat, "amount")?)?,
                    flat_limit: num(get(flat, "household_income_limit")?)?,
                }),
                None,
            )
        } else {
            let table = rows(p.value("elderly_credit_table", &[])?)?;
            (
                None,
                Some(ElderlyCredit {
                    table: Interp::new(&table),
                    rows: table.iter().map(|r| (r[0], r[1])).collect(),
                    second_aged: p.f("elderly_credit_second_aged")?,
                }),
            )
        };
        let addition = match p.opt("exemption_income_addition")? {
            Some(a) => Some(ExemptionAddition { floor: num(get(a, "floor")?)?, maximum: num(get(a, "maximum")?)? }),
            None => None,
        };
        Ok(Self {
            year,
            rate: p.f("rate")?,
            tax_share: opt("tax_share")?,
            dividend_exclusion: opt("dividend_exclusion_per_taxpayer")?,
            ui_addback: opt("ui_addback_per_taxpayer")?,
            ui_threshold_single: num(get(ui, "single")?)?,
            ui_threshold_joint: num(get(ui, "married_joint")?)?,
            renter_cap: opt("renter_deduction_cap")?,
            homeowner_cap: opt("homeowner_property_tax_deduction_cap")?,
            earned_income_deduction: match p.opt("earned_income_deduction")? {
                Some(d) => Some((num(get(d, "income_limit")?)?, num(get(d, "earnings_share")?)?)),
                None => None,
            },
            exemption_per_taxpayer: p.f("exemption_per_taxpayer")?,
            exemption_per_dependent: opt("exemption_per_dependent")?,
            exemption_income_addition: addition,
            aged_low_income: match p.opt("aged_low_income_exemption")? {
                Some(a) => Some((num(get(a, "amount")?)?, num(get(a, "agi_limit")?)?)),
                None => None,
            },
            dependent_exemption_addition: if year >= 1997 { p.f("dependent_exemption_addition")? } else { 0.0 },
            eitc,
            property,
            elderly,
            elderly_agi_limit: num(get(&doc, "elderly_credit_agi_limit")?)?,
            refund_credit: opt("automatic_taxpayer_refund_credit")?,
            dividend_adjustment: dividend_adjustment(root)?,
        })
    }
}

pub fn tax(i: &Inp, f: &Fed, p: &InParams) -> f64 {
    let y = p.year;
    let fs = i.fs();
    let sep = i.sepret();
    let taxpayers = i.taxpayer_count();
    let aged = i.aged_count();
    let hh = household_income(i, p.dividend_adjustment);
    let ui_total = unemployment_total(i);

    // ---- AGI ---- Social Security benefits are exempt.
    let mut agi = f.agi.max(0.0) - f.taxable_social_security;
    if let Some(per_taxpayer) = p.dividend_exclusion {
        let divexc = (i.dividends + i.intrec).min(f.pre87.divexc);
        let own = i.dividends.min(per_taxpayer * taxpayers);
        agi += divexc - own;
    }
    let untax = f.taxable_unemployment;
    if y == 2020 {
        agi = agi + ui_total - untax;
    }
    if let Some(cap) = p.ui_addback {
        agi += ui_total.min(cap * taxpayers);
    }
    // Indiana's own UI exclusion is zero when no UI is present.
    let threshold = if fs == Fs::Joint { p.ui_threshold_joint } else { p.ui_threshold_single };
    let unded = if y <= 2008 {
        let xlin6 = untax.min(0.5 * (f.agi - threshold).max(0.0));
        untax - xlin6
    } else {
        let xlin7 = 0.5 * (f.agi + ui_total - untax - threshold).max(0.0);
        (ui_total - xlin7).max(0.0)
    };
    agi -= unded;

    // ---- deductions ----
    let dedr = p.renter_cap.map_or(0.0, |cap| i.rentpaid.min(cap) / sep);
    let dedown = match p.homeowner_cap {
        Some(cap) => {
            let d = i.proptax.min(cap);
            if sep == 2.0 { d / sep } else { d }
        }
        None => 0.0,
    };
    let dedei = match p.earned_income_deduction {
        Some((limit, share)) => {
            let wages_plus_se = i.pwages + i.swages + i.psemp + i.ssemp;
            let eligible = i.depx > 0.0 && agi < limit && share * agi.max(0.0) < wages_plus_se;
            if eligible { limit - agi.max(0.0) } else { 0.0 }
        }
        None => 0.0,
    };
    let deduc = dedr + dedown + dedei;

    // ---- exemptions ----
    let per_taxpayer = p.exemption_per_taxpayer;
    let exemps_count = if y <= 1986 {
        taxpayers + i.depx + aged
    } else if i.is_dependent_filer() {
        0.0
    } else {
        taxpayers + i.depx
    };
    let mut exemp = if let Some(per_dependent) = p.exemption_per_dependent {
        (taxpayers + aged) * per_taxpayer + i.depx * per_dependent
    } else if let Some(a) = &p.exemption_income_addition {
        let base = (exemps_count + aged) * per_taxpayer;
        let xtra1 = (agi / 3.0 - a.floor).max(0.0).min(a.maximum);
        let xtra2 = (agi * 2.0 / 3.0 - a.floor).max(0.0).min(a.maximum);
        let xtra = if fs == Fs::Joint { xtra1 + xtra2 } else { (agi - a.floor).max(0.0).min(a.maximum) };
        base + xtra
    } else if y <= 1986 {
        exemps_count * per_taxpayer
    } else {
        // A dependent filer claims one exemption.
        let mut e = (i.is_dependent_filer() as u8 as f64 + exemps_count + aged) * per_taxpayer;
        if let Some((amount, agi_limit)) = p.aged_low_income {
            e += if f.agi < agi_limit { amount * aged } else { 0.0 };
        }
        e
    };
    if y >= 1997 {
        exemp += p.dependent_exemption_addition * i.depx;
    }

    let taxinc = (agi - deduc - exemp).max(0.0);
    let mut statax = taxinc * p.rate;
    if let Some(share) = p.tax_share {
        statax *= share;
    }

    // ---- credits ----
    let earncr = match &p.eitc {
        Eitc::None => 0.0,
        Eitc::Own { rate, income_cap, earnings_share } => {
            // Tested against federal total income.
            let total_income = f.agi + 0.5 * f.payroll.setax;
            let eligible = i.depx > 0.0
                && (f.earned_income >= earnings_share * total_income || total_income < 1.0)
                && total_income < *income_cap;
            if eligible { rate * (income_cap - total_income.max(0.0)) } else { 0.0 }
        }
        Eitc::Federal { rate, floor, decoupled_2022, schedule } => {
            let fed_eitc = f.eitc;
            let mut earncr = if fed_eitc >= *floor { rate * fed_eitc } else { 0.0 };
            if y >= 2022 {
                earncr = rate * fed_eitc;
                if let Some(d) = decoupled_2022 {
                    // 2022 is computed on its own formula: at most two children and no higher phase-out start for joint returns.
                    let children = i.dep18.max(0.0).min(2.0) as usize;
                    let decoupled = match d.single[children] {
                        Some(r) => {
                            let phase_in = (f.earned_income * r.rate_in).max(0.0).min(r.max_credit);
                            let reduction = ((f.earned_income.max(f.agi) - r.phaseout_start) * r.rate_out).max(0.0);
                            phase_in.min((r.max_credit - reduction).max(0.0)).max(0.0)
                        }
                        None => 0.0,
                    };
                    earncr = if fed_eitc > 0.0 { rate * decoupled } else { 0.0 };
                }
            } else if let Some(s) = schedule {
                let k = i.dep18.min(2.0);
                let idx = if k == 0.0 { 0 } else if k == 1.0 { 1 } else { 2 };
                let (crm, ym, rtbs, rtlw) = (s.max_credit[idx], s.phaseout_start[idx], s.phase_in_rate[idx], s.phaseout_rate[idx]);
                let earny = f.earned_income;
                // Uses federal AGI, not Indiana AGI.
                let modagi = f.agi.max(0.0);
                let mut eic11 = (rtbs * earny).min(crm);
                let over = modagi > ym || earny > ym;
                let eicpo = rtlw * modagi.max(earny).max(ym) - rtlw * ym;
                if over {
                    eic11 = eic11.min((crm - eicpo).max(0.0));
                }
                earncr = earncr.min(eic11);
            }
            earncr
        }
    };

    // Property tax credit (through 1980) and elderly credit, taxpayers 65 or older.
    let fed_agi = f.agi;
    let (pcred, mut ecred) = match (&p.property, &p.elderly) {
        (Some(pc), _) => {
            let ptax = i.proptax + pc.rent_share * i.rentpaid;
            let pcred = if aged > 0.0 { (pc.share_table.eval(hh) * ptax).max(0.0).min(pc.cap) } else { 0.0 };
            (pcred, if hh < pc.flat_limit { pc.flat_amount } else { 0.0 })
        }
        (None, Some(e)) => {
            // From 2022 the statute's step schedule, not TAXSIM's interpolation.
            let base_credit = if y >= 2022 {
                let mut base = 0.0;
                for (limit, amount) in e.rows.iter().rev() {
                    base = if fed_agi < *limit { *amount } else { base };
                }
                base
            } else {
                e.table.eval(fed_agi)
            };
            (0.0, base_credit + if aged > 1.0 && fs == Fs::Joint { e.second_aged } else { 0.0 })
        }
        (None, None) => (0.0, 0.0),
    };
    if !(aged > 0.0 && fed_agi < p.elderly_agi_limit) {
        ecred = 0.0;
    }
    statax -= pcred + ecred + earncr;
    if let Some(amount) = p.refund_credit {
        let refund = amount * taxpayers;
        statax -= if statax > 0.0 { refund } else { 0.0 };
    }
    statax * 1.0
}
