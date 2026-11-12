//! Delaware personal income tax (port of `calculators.states.de`).

use std::path::Path;

use crate::common::Brackets;
use crate::federal::{Fed, Inp};
use crate::params::{self, get, load, num, status_direct, Fs, YearParams};
use crate::states::{dividend_adjustment, law_year_is_exact, unemployment_total};

struct LowIncome {
    exclusion: f64,
    earned_limit: f64,
    agi_limit: f64,
}

enum Standard {
    Percent { rate: f64, cap_per_taxpayer: f64 },
    Fixed { single_or_hoh: f64, married: f64 },
    PerTaxpayer(f64),
}

enum Threshold {
    Flat(f64),
    ByStatus([f64; 4]),
}

struct Limitation {
    rate: f64,
    cap_rate: f64,
    reduction_share: f64,
    threshold: Threshold,
    index: f64,
}

enum Eitc {
    None,
    /// 2006-2020: a share of the federal credit.
    NonRefundable(f64),
    /// 2021 on: the refundable share applies when it exceeds the tax.
    Either { refundable: f64, nonrefundable: f64 },
}

pub struct DeParams {
    year: i64,
    table: Brackets,
    pension_young_per_taxpayer: f64,
    pension_aged: f64,
    low_income: Option<LowIncome>,
    standard: Standard,
    aged_standard: f64,
    /// Standard deduction per taxpayer on a split return (0 when the year has none).
    std_each: f64,
    itemized_multiplier: Option<f64>,
    state_tax_cap: Option<f64>,
    limitation: Option<Limitation>,
    personal_exemption: Option<f64>,
    federal_tax_exemption: Option<f64>,
    chcr_share: f64,
    chcr_cap: Option<f64>,
    pecred: Option<f64>,
    eitc: Eitc,
    dividend_adjustment: f64,
}

impl DeParams {
    pub fn load(root: &Path, year: i64) -> Result<Self, String> {
        let doc = load(root, "states/de/income_tax.yaml")?;
        law_year_is_exact(root, &doc, year)?;
        let p = YearParams::new(&doc, year);
        let opt = |key: &str| -> Result<Option<f64>, String> { Ok(if p.in_effect(key)? { Some(p.f(key)?) } else { None }) };
        let standard = if p.in_effect("standard_deduction_percent")? {
            let sd = p.value("standard_deduction_percent", &[])?;
            Standard::Percent { rate: num(get(sd, "rate")?)?, cap_per_taxpayer: num(get(sd, "cap_per_taxpayer")?)? }
        } else if p.in_effect("standard_deduction")? {
            let sd = p.value("standard_deduction", &[])?;
            Standard::Fixed { single_or_hoh: num(get(sd, "single_or_hoh")?)?, married: num(get(sd, "married")?)? }
        } else {
            Standard::PerTaxpayer(p.f("standard_deduction_per_taxpayer")?)
        };
        let limitation = match p.opt("itemized_limitation")? {
            Some(l) => {
                let t = get(l, "threshold")?;
                Some(Limitation {
                    rate: num(get(l, "rate")?)?,
                    cap_rate: num(get(l, "cap_rate")?)?,
                    reduction_share: num(get(l, "reduction_share")?)?,
                    threshold: if t.is_mapping() { Threshold::ByStatus(status_direct(t)?) } else { Threshold::Flat(num(t)?) },
                    index: p.f("itemized_limitation_index")?,
                })
            }
            None => None,
        };
        let low_income = match p.opt("aged_low_income_exclusion")? {
            Some(e) => Some(LowIncome {
                exclusion: num(get(e, "exclusion")?)?,
                earned_limit: num(get(e, "earned_limit")?)?,
                agi_limit: num(get(e, "agi_limit")?)?,
            }),
            None => None,
        };
        let eitc = if p.in_effect("eitc_refundable_share")? {
            Eitc::Either { refundable: p.f("eitc_refundable_share")?, nonrefundable: p.f("eitc_federal_share")? }
        } else if p.in_effect("eitc_federal_share")? {
            Eitc::NonRefundable(p.f("eitc_federal_share")?)
        } else {
            Eitc::None
        };
        Ok(Self {
            year,
            table: Brackets::new(&params::brackets(p.value("brackets", &[])?)?),
            pension_young_per_taxpayer: num(get(&doc, "pension_exclusion_young_per_taxpayer")?)?,
            pension_aged: p.f("pension_exclusion_aged")?,
            low_income,
            standard,
            aged_standard: if year >= 1987 { p.f("aged_standard_deduction")? } else { 0.0 },
            itemized_multiplier: if year > 1986 && year <= 2017 { opt("itemized_deduction_multiplier")? } else { None },
            state_tax_cap: if year > 2017 { opt("state_tax_deduction_cap")? } else { None },
            limitation,
            personal_exemption: opt("personal_exemption")?,
            federal_tax_exemption: if p.in_effect("personal_exemption")? { opt("federal_tax_exemption_per_taxpayer")? } else { None },
            chcr_share: p.f("child_care_credit_federal_share")?,
            chcr_cap: opt("child_care_credit_cap")?,
            pecred: opt("personal_exemption_credit")?,
            std_each: opt("standard_deduction_per_taxpayer")?.unwrap_or(0.0),
            eitc,
            dividend_adjustment: dividend_adjustment(root)?,
        })
    }
}

pub fn tax(i: &Inp, f: &Fed, p: &DeParams) -> f64 {
    let y = p.year;
    let fs = i.fs();
    let sep = i.sepret();
    let taxpayers = i.taxpayer_count();
    let aged = i.aged_count();

    // Federal exemptions, floored; 1989 adds the aged.
    let exemps_raw = if y <= 1986 {
        taxpayers + i.depx + aged
    } else if i.is_dependent_filer() {
        0.0
    } else {
        taxpayers + i.depx
    };
    let mut de_num = exemps_raw.floor();
    if y == 1989 {
        de_num += aged;
    }

    // ---- AGI ---- federal AGI less taxable Social Security (2021 also excludes unemployment compensation).
    let mut agi = f.agi - f.taxable_social_security;
    if y == 2021 {
        agi -= unemployment_total(i);
    }
    // Pension exclusion.
    let pensions = i.pensions;
    let is_joint = fs == Fs::Joint;
    // TAXSIM assumes a joint spouse with no age is 40.
    let sage = if is_joint && i.sage == 0.0 { 40.0 } else { i.sage };
    let unified = y >= 1996;
    let agi_pre = agi;
    let (mut retdedt, mut retdeds) = (0.0, 0.0);
    if unified {
        // Taxpayers 60 or older exclude up to $12,500 of pensions, others $2,000; pensions go to
        // the spouse aged 60 or over (half each when both or neither are).
        let (h_old, w_old) = (i.page >= 60.0, sage >= 60.0);
        let pent = if !is_joint || (h_old && !w_old) { pensions } else if !h_old && w_old { 0.0 } else { pensions / 2.0 };
        let pens = if is_joint { pensions - pent } else { 0.0 };
        let earned = |w: f64, a: f64, b: f64, c: f64| w + 0.9235 * (a.max(0.0) + b.max(0.0) + c.max(0.0));
        let totn_h = earned(i.pwages.max(0.0), i.psemp, i.pbusinc, i.pprofinc);
        let totn_w = earned(i.swages.max(0.0), i.ssemp, i.sbusinc, i.sprofinc);
        let agilow = agi_pre < 10000.0 * taxpayers;
        retdedt = if h_old {
            pent.min(12500.0) + if totn_h < 2500.0 && agilow { 2000.0 } else { 0.0 }
        } else {
            pent.min(2000.0)
        };
        let retdeds_old = pens.min(12500.0);
        retdeds = if is_joint { if w_old { retdeds_old } else { pens.min(2000.0) } } else { 0.0 };
        retdeds += if is_joint && w_old && totn_w < 2500.0 && agilow { 2000.0 } else { 0.0 };
        agi = (agi_pre - retdedt - retdeds).max(0.0);
    } else {
        let investment = i.intrec + i.dividends + p.dividend_adjustment + (i.stcg + i.ltcg).max(0.0);
        let penexc = (if aged < 1.0 {
            (p.pension_young_per_taxpayer * taxpayers).min(pensions)
        } else if aged == 1.0 {
            p.pension_aged.min(pensions + 0.5 * investment)
        } else {
            (2.0 * p.pension_aged).min(pensions + investment)
        })
        .max(0.0);
        agi -= penexc;
    }
    if let (Some(e), true) = (&p.low_income, y < 2000) {
        let low = aged > 0.0 && f.earned_income < e.earned_limit * aged && agi <= e.agi_limit * aged;
        if low {
            agi -= e.exclusion * aged;
        }
    }

    // ---- standard deduction ----
    let mut stded = match &p.standard {
        Standard::Percent { rate, cap_per_taxpayer } => (cap_per_taxpayer * taxpayers / sep).min(rate * agi.max(0.0)),
        Standard::Fixed { single_or_hoh, married } => {
            if fs == Fs::Single || fs == Fs::Hoh { *single_or_hoh } else { married / sep }
        }
        Standard::PerTaxpayer(amount) => amount * taxpayers,
    };
    if y >= 1987 {
        stded += p.aged_standard * aged;
    }

    // ---- itemized deduction ---- Through 1986 the itemized inputs.
    let salt = f.ded;
    let raw_itemized = if y <= 1986 { i.proptax + i.otheritem + i.mortgage } else { f.salt_capped + i.mortgage };
    let xitded_base = if y <= 1986 {
        raw_itemized
    } else if y <= 2017 {
        let mut b = (raw_itemized - salt).max(0.0);
        if let Some(m) = p.itemized_multiplier {
            b *= m;
        }
        b
    } else if y >= 2019 {
        // Property tax up to $10,000, mortgage interest and charity; no state income tax and no
        // other itemized deductions.
        i.proptax.min(10000.0) + i.mortgage + i.charity_cash
    } else if let Some(cap) = p.state_tax_cap {
        // The federal itemized total less the state income tax inside its capped state and local part.
        let sttax = (cap / sep).min(i.proptax + salt);
        let room = sttax - i.proptax;
        f.itemized_before_limit - salt.min(room)
    } else {
        raw_itemized
    };
    let mut xitded = match &p.limitation {
        Some(l) => {
            let phas92 = match &l.threshold {
                Threshold::ByStatus(by_status) => by_status[fs as usize] * l.index,
                Threshold::Flat(v) => v * l.index / sep,
            };
            let reduce = if agi > phas92 { (l.cap_rate * xitded_base).min(l.rate * (agi - phas92)) } else { 0.0 };
            (xitded_base - reduce * l.reduction_share).max(0.0)
        }
        None => xitded_base,
    };
    // A forced standard deduction zeroes itemized deductions in 1999 only.
    if y == 1999 && !f.forced_itemize {
        xitded = 0.0;
    }
    let deduc = stded.max(xitded);

    // ---- exemption (through 1995) ----
    let exemp = match p.personal_exemption {
        Some(amount) => {
            let mut e = de_num * amount;
            if let Some(per_taxpayer) = p.federal_tax_exemption {
                // Plus federal income tax up to an amount per taxpayer.
                e += f.fiitax.max(0.0).min(per_taxpayer * taxpayers);
            }
            e
        }
        None => 0.0,
    };
    let taxinc = (agi - deduc - exemp).max(0.0);

    // ---- joint returns: the lower of the joint tax and the tax on each earner's share ----
    let is_joint_relief = is_joint && agi > 0.0;
    let (taxinh, taxinw);
    if unified {
        // TAXSIM's `splitagi`: each spouse's own income, with the rest shared equally.
        let ui_total = i.ui.max(i.pui + i.sui);
        let pui_part = ui_total - i.sui;
        let both_55 = i.page >= 55.0;
        let wife_55 = sage >= 55.0;
        let pension_ss = pensions + f.taxable_social_security;
        let pent_s = if both_55 && !wife_55 { pension_ss } else if !both_55 && wife_55 { 0.0 } else { pension_ss / 2.0 };
        let pens_s = pension_ss - pent_s;
        let seih = i.psemp + i.pbusinc + i.pprofinc;
        let seis = i.ssemp + i.sbusinc + i.sprofinc;
        let secah = f.payroll.setax_primary / 2.0;
        let secas = f.payroll.setax_secondary / 2.0;
        let tinc = i.pwages + seih + pui_part - retdedt + pent_s;
        let sinc = i.swages + seis + i.sui - retdeds + pens_s;
        let cinc = 0.5 * (agi_pre - tinc - sinc + secah + secas);
        let agit = tinc + cinc - secah;
        let agis = sinc + cinc - secas;
        let aged_h = if i.page > 0.0 { i.page >= 65.0 } else { aged >= 1.0 };
        let aged_w = if i.page > 0.0 { sage >= 65.0 } else { aged >= 2.0 };
        let std_h = agit.min(p.std_each + p.aged_standard * aged_h as u8 as f64);
        let std_w = agis.min(p.std_each + p.aged_standard * aged_w as u8 as f64);
        let itemizing_split = xitded > stded - p.aged_standard * aged;
        let safe_agi = if agi != 0.0 { agi } else { 1.0 };
        let deduch = if itemizing_split { xitded * agit / safe_agi } else { std_h };
        let deducw = if itemizing_split { xitded * agis / safe_agi } else { std_w };
        taxinh = (agit - deduch - 0.5 * exemp).max(0.0);
        taxinw = (agis - deducw - 0.5 * exemp).max(0.0);
    } else {
        let wages = i.pwages + i.swages;
        let higher = i.pwages.max(i.swages) + (agi - wages) / 2.0;
        let lower = agi - higher;
        let dedh = if agi != 0.0 { deduc * higher / agi } else { 0.0 };
        let dedw = deduc - dedh;
        taxinh = (higher - dedh - 0.5 * exemp).max(0.0);
        taxinw = (lower - dedw - 0.5 * exemp).max(0.0);
    }

    let stat = p.table.tax(taxinc);
    let stat_h = p.table.tax(taxinh);
    let stat_w = p.table.tax(taxinw);
    let mut statax = if is_joint_relief { stat.min(stat_h + stat_w) } else { stat };

    // ---- child/dependent care credit ----
    let mut chcr = f.federal_chcr.max(0.0) * p.chcr_share;
    if let Some(cap) = p.chcr_cap {
        chcr = chcr.min(cap);
    }
    // ---- personal exemption credit (1996+) ----
    if unified {
        // Combined separate returns: each spouse's tax is cut by that spouse's personal credits,
        // dependents going first to the husband until his tax is used up.
        let unit = p.pecred.unwrap_or(0.0);
        let h_aged60 = i.page >= 60.0 || aged >= 1.0;
        let w_aged60 = i.sage >= 60.0 || aged >= 2.0;
        let credit_total = unit * (taxpayers + i.depx) + unit * (h_aged60 as u8 as f64) + unit * (w_aged60 as u8 as f64);
        let sepcmb = is_joint_relief && stat >= stat_h + stat_w;
        let cred_h = unit + unit * (h_aged60 as u8 as f64);
        let cred_w = unit + unit * (w_aged60 as u8 as f64);
        let dep_max_h = (((stat_h - cred_h) / unit) as i64) as f64;
        let dep_h = i.depx.min(dep_max_h);
        let dep_w = i.depx - dep_h;
        let tax_h = (stat_h - cred_h - dep_h * unit).max(0.0);
        let tax_w = (stat_w - cred_w - dep_w * unit).max(0.0);
        let joint_after = (stat - credit_total).max(0.0);
        let combined = if sepcmb { joint_after.min(tax_h + tax_w) } else { joint_after };
        statax = if is_joint_relief { combined } else { joint_after };
        let chcr_limited = if sepcmb { chcr.min(tax_h).min(tax_w) } else { chcr };
        statax = (statax - chcr_limited).max(0.0);
    } else {
        let pecred = p.pecred.map_or(0.0, |per_unit| {
            let aged_60 = (i.page >= 60.0) as u8 as f64 + (i.sage >= 60.0) as u8 as f64;
            (de_num + aged_60) * per_unit
        });
        statax = (statax - (chcr + pecred)).max(0.0);
    }

    // ---- EITC (2006+) ----
    match &p.eitc {
        Eitc::Either { refundable, nonrefundable } => {
            let refundable_amt = refundable * f.eitc.max(0.0);
            let nonrefundable_amt = nonrefundable * f.eitc.max(0.0);
            let use_refundable = refundable_amt > statax;
            let earncr = if use_refundable { refundable_amt } else { nonrefundable_amt };
            statax = if use_refundable { statax - earncr } else { (statax - earncr).max(0.0) };
        }
        Eitc::NonRefundable(share) => {
            statax = (statax - share * f.eitc.max(0.0)).max(0.0);
        }
        Eitc::None => {}
    }
    statax * 1.0
}
