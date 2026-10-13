//! Montana income tax (port of `calculators.states.mt`).

use std::path::Path;

use crate::common::Brackets;
use crate::federal::{Fed, Inp};
use crate::params::{self, get, load, num, resolve_year, status_direct, Fs, YearParams};
use crate::states::{dividend_adjustment, household_income, law_year_is_exact, unemployment_total, Interp};

struct Retirement {
    maximum: f64,
    per_taxpayer: f64,
    income_start: f64,
    phaseout_rate: f64,
    inflation: f64,
    old_age: Option<f64>,
}

struct ItemizedLimit {
    rate: f64,
    max_share: f64,
    fraction: f64,
    base: f64,
    inflation: f64,
    status_multiplier: [f64; 4],
}

struct ChildCare {
    caps: [f64; 3],
    phaseout_rate: f64,
    phaseout_start: f64,
}

struct TwoRate {
    /// By filing status in `Fs` order: threshold, low rate, high rate.
    rows: [(f64, f64, f64); 4],
    gains_low: f64,
    gains_high: f64,
}

struct Elderly {
    rent_share: f64,
    multiplier: Interp,
    maximum: f64,
    income_exclusion: Option<f64>,
    income_share: Option<Interp>,
    income_limit: Option<f64>,
}

enum Table {
    Brackets(Brackets),
    TwoRate(TwoRate),
}

pub struct MtParams {
    year: i64,
    loss_limit: f64,
    cg_exclusion_rate: f64,
    aged_interest_exclusion: Option<f64>,
    retirement: Option<Retirement>,
    social_security: Option<SocialSecurity>,
    pct: f64,
    cap: f64,
    floor: f64,
    hoh_taxpayers: f64,
    conforms: bool,
    federal_tax_deduction: bool,
    federal_tax_cap: Option<f64>,
    federal_tax_rebate: Option<(f64, f64)>,
    itemized_limit: Option<ItemizedLimit>,
    child_care: Option<ChildCare>,
    exemption: f64,
    table: Table,
    split_other_share: f64,
    split_itemized_share: f64,
    surtax: f64,
    cg_credit_rate: f64,
    elderly: Option<Elderly>,
    homeowner_credit: Option<f64>,
    eitc_match_rate: f64,
    dividend_adjustment: f64,
}

struct SocialSecurity {
    base_single: f64,
    base_married: f64,
    second_single: f64,
    second_married: f64,
    rate_1: f64,
    rate_2: f64,
}

impl MtParams {
    pub fn load(root: &Path, year: i64) -> Result<Self, String> {
        let doc = load(root, "states/mt/income_tax.yaml")?;
        law_year_is_exact(root, &doc, year)?;
        let capital_gains = load(root, "national/capital_gains.yaml")?;
        let p = YearParams::new(&doc, year);
        let opt = |key: &str| -> Result<Option<f64>, String> { Ok(if p.in_effect(key)? { Some(p.f(key)?) } else { None }) };

        let retirement = if year >= 1991 {
            Some(Retirement {
                maximum: p.f("retirement_exclusion_max")?,
                per_taxpayer: p.f("retirement_exclusion_per_taxpayer")?,
                income_start: p.f("retirement_exclusion_income_start")?,
                phaseout_rate: p.f("retirement_exclusion_phaseout_rate")?,
                inflation: p.f("retirement_exclusion_inflation")?,
                old_age: opt("old_age_subtraction")?,
            })
        } else {
            None
        };
        let social_security = if year >= 1984 {
            let base = get(&doc, "social_security_base")?;
            let second = get(&doc, "social_security_second_base")?;
            Some(SocialSecurity {
                base_single: num(get(base, "single")?)?,
                base_married: num(get(base, "married")?)?,
                second_single: num(get(second, "single")?)?,
                second_married: num(get(second, "married")?)?,
                rate_1: num(get(&doc, "social_security_rate_1")?)?,
                rate_2: num(get(&doc, "social_security_rate_2")?)?,
            })
        } else {
            None
        };
        let itemized_limit = if p.in_effect("itemized_limit_rate")? {
            Some(ItemizedLimit {
                rate: p.f("itemized_limit_rate")?,
                max_share: p.f("itemized_limit_max_share")?,
                fraction: p.f("itemized_limit_fraction")?,
                base: p.f("itemized_limit_threshold_base")?,
                inflation: p.f("itemized_limit_inflation")?,
                status_multiplier: if year >= 2013 { status_direct(get(&doc, "itemized_limit_status_multiplier")?)? } else { [1.0; 4] },
            })
        } else {
            None
        };
        let child_care = if p.in_effect("child_care_expense_cap")? {
            let caps = p.value("child_care_expense_cap", &[])?.as_sequence().ok_or("caps must be a list")?;
            Some(ChildCare {
                caps: [num(&caps[0])?, num(&caps[1])?, num(&caps[2])?],
                phaseout_rate: p.f("child_care_phaseout_rate")?,
                phaseout_start: p.f("child_care_phaseout_start")?,
            })
        } else {
            None
        };
        let table = if p.in_effect("two_rate_schedule")? {
            let d = p.value("two_rate_schedule", &[])?;
            let row = |key: &str| -> Result<(f64, f64, f64), String> {
                let r = get(d, key)?;
                Ok((num(get(r, "threshold")?)?, num(get(r, "rate_low")?)?, num(get(r, "rate_high")?)?))
            };
            Table::TwoRate(TwoRate {
                rows: [row("single")?, row("married_joint")?, row("married_separate")?, row("head_of_household")?],
                gains_low: num(get(d, "gains_rate_low")?)?,
                gains_high: num(get(d, "gains_rate_high")?)?,
            })
        } else {
            let factor = p.f("bracket_inflation")?;
            let scaled: Vec<Vec<f64>> = params::brackets(p.value("brackets", &[])?)?.into_iter().map(|r| vec![r[0] * factor, r[1]]).collect();
            Table::Brackets(Brackets::new(&scaled))
        };
        let elderly = if year >= 1981 {
            Some(Elderly {
                rent_share: num(get(&doc, "elderly_credit_rent_share")?)?,
                multiplier: Interp::new(&params::brackets(get(&doc, "elderly_credit_multiplier")?)?),
                maximum: p.f("elderly_credit_max")?,
                income_exclusion: if year > 1982 { Some(p.f("elderly_credit_income_exclusion")?) } else { None },
                income_share: if year > 1982 { Some(Interp::new(&params::brackets(get(&doc, "elderly_credit_income_share")?)?)) } else { None },
                income_limit: if year > 1982 { opt("elderly_credit_income_limit")? } else { None },
            })
        } else {
            None
        };
        Ok(Self {
            year,
            loss_limit: if year >= 1987 { num(resolve_year(get(&capital_gains, "net_capital_loss_limit")?, year)?)? } else { 0.0 },
            cg_exclusion_rate: p.f("capital_gains_exclusion_rate")?,
            aged_interest_exclusion: opt("aged_interest_exclusion")?,
            retirement,
            social_security,
            pct: p.f("standard_deduction_pct")?,
            cap: p.f("standard_deduction_cap")?,
            floor: p.f("standard_deduction_floor")?,
            hoh_taxpayers: num(get(&doc, "head_of_household_standard_deduction_taxpayers")?)?,
            conforms: p.value("conforms_to_federal_standard_deduction", &[])?.as_bool().ok_or("expected a boolean")?,
            federal_tax_deduction: p.value("federal_tax_deduction", &[])?.as_bool().ok_or("expected a boolean")?,
            federal_tax_cap: if year >= 2005 { Some(p.f("federal_tax_deduction_cap_per_taxpayer")?) } else { None },
            federal_tax_rebate: match p.opt("federal_tax_rebate")? {
                Some(r) => Some((num(get(r, "per_taxpayer")?)?, num(get(r, "per_dependent")?)?)),
                None => None,
            },
            itemized_limit,
            child_care,
            exemption: p.f("exemption")?,
            table,
            split_other_share: p.f("joint_split_other_income_share")?,
            split_itemized_share: p.f("joint_split_itemized_share")?,
            surtax: p.f("surtax")?,
            cg_credit_rate: p.f("capital_gains_credit_rate")?,
            elderly,
            homeowner_credit: opt("homeowner_credit")?,
            eitc_match_rate: p.f("eitc_match_rate")?,
            dividend_adjustment: dividend_adjustment(root)?,
        })
    }

    fn standard(&self, income: f64, taxpayers: f64) -> f64 {
        (self.floor * taxpayers).max((self.cap * taxpayers).min(self.pct * income.max(0.0)))
    }
}

pub fn tax(i: &Inp, f: &Fed, p: &MtParams) -> f64 {
    let y = p.year;
    let fs = i.fs();
    let is_joint = fs == Fs::Joint;
    let is_sep = fs == Fs::Separate;
    let is_hoh = fs == Fs::Hoh;
    let sep = i.sepret();
    let ntp = i.taxpayer_count();
    let aged = i.aged_count();
    let depx = i.depx;
    let salt_ded = f.ded;
    let fed_agi = f.agi;
    let ui = unemployment_total(i);
    let household = household_income(i, p.dividend_adjustment);
    let half_setax = 0.5 * f.payroll.setax;

    let fullcg = i.stcg + i.ltcg;
    let capgn = if y >= 1987 { fullcg.max(-p.loss_limit / sep) } else { fullcg };

    // ---- Montana AGI ---- `subtr` collects the subtractions for the Social Security worksheet.
    let mut agi = fed_agi;
    if y == 2020 {
        agi += ui - f.taxable_unemployment;
    }
    let cg_exclusion = p.cg_exclusion_rate * capgn.max(0.0);
    agi -= cg_exclusion;
    // Only the unemployment compensation in federal AGI is subtracted (all of it in 2020).
    let ui_subtraction = if y != 2020 { f.taxable_unemployment } else { ui };
    let mut subtr = cg_exclusion + ui_subtraction;
    if let Some(amount) = p.aged_interest_exclusion {
        let excint = i.intrec.min(amount * aged);
        agi -= excint;
        subtr += excint;
    }
    if let Some(r) = &p.retirement {
        let index = r.inflation;
        let pensions = i.pensions;
        let retmax = (r.maximum * if y >= 2010 { aged * index } else { 1.0 }).min(pensions);
        let delta = r.per_taxpayer * ntp * index;
        let agiret = r.income_start * index;
        let agi_each = fed_agi / aged;
        let mut retexc = aged * ((delta.min(pensions / aged) - r.phaseout_rate * (agi_each - agiret).max(0.0)).max(0.0));
        retexc = if agi_each < agiret {
            retmax
        } else if agi_each < agiret + delta {
            (retmax - r.phaseout_rate * (fed_agi - agiret)).max(0.0)
        } else {
            retexc
        };
        retexc = if aged > 0.0 { retexc } else { 0.0 };
        if let Some(old_age) = r.old_age {
            retexc = old_age * aged;
        }
        agi = (agi - retexc).max(0.0);
        subtr += retexc;
    }
    if y >= 1984 {
        agi -= ui_subtraction;
    }
    // Social Security on Montana's worksheet in place of the federal amount.
    let mut ssagi = 0.0;
    if let Some(s) = &p.social_security {
        let gssi = i.gssi;
        let provisional = 0.5 * gssi + fed_agi - f.taxable_social_security;
        let xl8 = subtr + half_setax;
        let single_class = matches!(fs, Fs::Single | Fs::Hoh);
        let base_1 = if single_class { s.base_single } else { s.base_married / sep };
        let base_2 = if single_class { s.second_single } else { s.second_married / sep };
        let xl11 = (provisional - xl8 - base_1).max(0.0);
        let worksheet = ((s.rate_1 * gssi).min(s.rate_1 * xl11.min(base_2)) + s.rate_2 * (xl11 - base_2).max(0.0)).min(s.rate_2 * gssi);
        ssagi = if gssi > 0.0 && xl8 <= provisional { worksheet } else { 0.0 };
    }
    agi = agi - f.taxable_social_security + ssagi;

    // ---- standard deduction ----
    let std_taxpayers = if is_hoh { p.hoh_taxpayers } else { ntp };
    let mut stded = p.standard(agi, std_taxpayers);
    if p.conforms {
        stded = f.standard_deduction;
    }

    // ---- itemized deductions ----
    let agix = fed_agi.max(0.0);
    let alim50 = if y == 2020 || y == 2021 { 1.0e20 } else { 0.5 * agix };
    let fed_char = (if fed_agi < 0.0 { 0.0 } else { i.charity_cash.min(alim50) }).max(0.0);
    let deducp = if y <= 1986 {
        i.proptax + i.otheritem + i.mortgage + salt_ded
    } else if y <= 2017 {
        (i.proptax + i.otheritem + salt_ded).max(0.0) + i.mortgage + fed_char
    } else {
        f.itemized_deduction
    };
    let mut fed_tax = f.fiitax;
    if let Some((per_taxpayer, per_dependent)) = p.federal_tax_rebate {
        fed_tax -= per_taxpayer * ntp + per_dependent * depx;
    }
    fed_tax = fed_tax.max(0.0);
    if let Some(cap) = p.federal_tax_cap {
        fed_tax = fed_tax.min(cap * ntp);
    }
    if !p.federal_tax_deduction {
        fed_tax = 0.0;
    }
    let mut xitded = (deducp - salt_ded).max(0.0) + fed_tax;
    if let Some(l) = &p.itemized_limit {
        let threshold = if y >= 2013 { l.base * l.inflation * l.status_multiplier[fs as usize] } else { l.base * l.inflation / sep };
        let reduce = (l.max_share * (deducp - salt_ded)).min(l.rate * (agi - threshold)) * l.fraction;
        xitded -= if agi > threshold { reduce } else { 0.0 };
    }
    let chexp = match &p.child_care {
        Some(c) => {
            let ich = depx.clamp(1.0, 3.0).floor();
            let care_cap = if ich >= 3.0 { c.caps[2] } else if ich >= 2.0 { c.caps[1] } else { c.caps[0] };
            (i.childcare.min(care_cap) - c.phaseout_rate * (agi - c.phaseout_start).max(0.0)).max(0.0)
        }
        None => 0.0,
    };
    xitded += if depx > 0.0 { chexp } else { 0.0 };
    if y == 1999 && !f.forced_itemize {
        xitded = 0.0;
    }
    let deduc = stded.max(xitded);

    // ---- exemptions and tax ----
    let xmp = p.exemption;
    let exemp = (ntp + depx + aged) * xmp;
    let schedule_1a = if y >= 2025 { f.senior_deduction } else { 0.0 };
    let taxinc = (agi - deduc - exemp - schedule_1a).max(0.0);
    let mut statax = match &p.table {
        Table::TwoRate(t) => {
            let gains = (i.ltcg + i.stcg).max(0.0);
            let gains = i.ltcg.max(0.0).min(gains);
            let gains_in = gains.min(taxinc);
            let ordinary = (taxinc - gains_in).max(0.0);
            let (threshold, low, high) = t.rows[fs as usize];
            let ordinary_tax = low * ordinary.min(threshold) + high * (ordinary - threshold).max(0.0);
            let room = (threshold - ordinary).max(0.0);
            ordinary_tax + t.gains_low * gains_in.min(room) + t.gains_high * (gains_in - room).max(0.0)
        }
        Table::Brackets(b) => {
            // Married couples may instead be taxed as two returns.
            let mut tax = b.tax(taxinc);
            let wages_total = i.pwages.max(0.0) + i.swages.max(0.0);
            let agih = i.pwages.max(i.swages) + p.split_other_share * (agi - wages_total);
            let agiw = agi - agih;
            let stdh = p.standard(agih, 1.0);
            let stdw = p.standard(agiw, 1.0);
            let split_stded = if y >= 1981 { stdh + stdw } else { stded };
            let xitd_each = if xitded > 0.0 && agi > 0.0 { p.split_itemized_share * xitded } else { 0.0 };
            let use_itemized = xitded > split_stded;
            let dedh = if use_itemized { xitd_each } else { stdh };
            let dedw = if use_itemized { xitd_each } else { stdw };
            let exempw = if aged > 0.0 { 2.0 * xmp } else { xmp };
            let exemph = exemp - exempw;
            let split_tax = b.tax((agih - dedh - exemph).max(0.0)) + b.tax((agiw - dedw - exempw).max(0.0));
            if is_joint {
                tax = tax.min(split_tax);
            }
            tax
        }
    };
    statax *= p.surtax;

    // ---- credits ----
    let cgcred = p.cg_credit_rate * capgn.max(0.0);
    statax = (statax - cgcred).max(0.0);
    if let Some(e) = &p.elderly {
        let hy = household;
        let rent_share = e.rent_share * i.rentpaid;
        let ptax = if y == 1981 { i.proptax.max(rent_share) } else { i.proptax + rent_share };
        let multiplier = e.multiplier.eval(hy);
        let mut pcred;
        if y <= 1982 {
            pcred = (ptax - hy * multiplier).max(0.0).min(e.maximum);
        } else {
            let hy1 = (hy - e.income_exclusion.unwrap_or(0.0)).max(0.0);
            let hynet = e.income_share.as_ref().map_or(0.0, |t| t.eval(hy1)) * hy1;
            pcred = (ptax - hynet).max(0.0).min(e.maximum);
            if let Some(limit) = e.income_limit {
                if hy >= limit {
                    pcred = 0.0;
                }
            }
            if y >= 1999 {
                pcred *= multiplier;
            }
        }
        if !(aged > 0.0) {
            pcred = 0.0;
        }
        statax -= pcred;
    }
    if let Some(amount) = p.homeowner_credit {
        statax -= if i.proptax > 0.0 { amount } else { 0.0 };
    }
    statax -= p.eitc_match_rate * f.eitc;
    let _ = is_sep;
    statax
}
