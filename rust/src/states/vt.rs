//! Vermont income tax (port of `calculators.states.vt`).

use std::path::Path;

use crate::common::Brackets;
use crate::federal::{Fed, Inp};
use crate::params::{self, get, load, num, Fs, YearParams};
use crate::states::{dividend_adjustment, federal_exemption_count, household_income, law_year_is_exact, unemployment_total, Interp, Socsec};

struct Tables {
    single: Brackets,
    hoh: Brackets,
    married: Brackets,
}

struct Post2017 {
    ss_start_joint: f64,
    ss_start_other: f64,
    ss_range: f64,
    standard_single: f64,
    standard_hoh: f64,
    standard_joint: f64,
    aged_addition: f64,
    exemption_amount: f64,
}

struct Cgx {
    share: f64,
    cap: Option<f64>,
    floor: Option<f64>,
}

struct PropertyRebate {
    rent_share: f64,
    max: f64,
    table: Interp,
    income_limit: Option<f64>,
}

struct RenterRebate {
    rent_share: f64,
    table: Interp,
    investment_threshold: f64,
    aged_cap: Option<(f64, f64)>,
    income_limit: f64,
    socsec: Socsec,
}

struct ChildTaxCredit {
    amount: f64,
    reduction_start: f64,
    reduction_amount: f64,
    reduction_increment: f64,
    age_limit: f64,
}

pub struct VtParams {
    year: i64,
    federal_share: Option<f64>,
    federal_brackets: Option<Brackets>,
    cgx: Cgx,
    itemized_addback_multiple: Option<f64>,
    post2017: Option<Post2017>,
    tables: Option<Tables>,
    minimum_tax: Option<(f64, f64)>,
    low_income: Option<(Interp, Option<f64>, f64)>,
    child_care_rate: Option<f64>,
    child_care_refundable: Option<(f64, f64, f64)>,
    charity_credit: Option<(f64, f64)>,
    eitc_rate: Option<f64>,
    eitc_childless_rate: Option<f64>,
    telephone: Option<(f64, f64, Option<f64>)>,
    property_rebate: Option<PropertyRebate>,
    renter_rebate: Option<RenterRebate>,
    child_care_full_refundable_rate: Option<f64>,
    child_tax_credit: Option<ChildTaxCredit>,
    dividend_adjustment: f64,
}

impl VtParams {
    pub fn load(root: &Path, year: i64) -> Result<Self, String> {
        let doc = load(root, "states/vt/income_tax.yaml")?;
        law_year_is_exact(root, &doc, year)?;
        let p = YearParams::new(&doc, year);
        let opt = |key: &str| -> Result<Option<f64>, String> { Ok(if p.in_effect(key)? { Some(p.f(key)?) } else { None }) };
        let federal_share = opt("federal_tax_share")?;
        let federal_brackets = if federal_share.is_some() && p.in_effect("federal_tax_brackets")? {
            Some(Brackets::new(&params::brackets(p.value("federal_tax_brackets", &[])?)?))
        } else {
            None
        };
        let mut tables = None;
        let mut post2017 = None;
        if federal_share.is_none() {
            let index = p.f("bracket_index")?;
            let t = p.value("brackets", &[])?;
            let scaled = |key: &str| -> Result<Brackets, String> {
                let rows: Vec<Vec<f64>> = params::brackets(get(t, key)?)?.into_iter().map(|r| vec![r[0] * index, r[1]]).collect();
                Ok(Brackets::new(&rows))
            };
            tables = Some(Tables { single: scaled("single")?, hoh: scaled("head_of_household")?, married: scaled("married")? });
            if year > 2017 {
                let starts = p.value("social_security_exemption_start", &[])?;
                let amounts = p.value("standard_deduction", &[])?;
                post2017 = Some(Post2017 {
                    ss_start_joint: num(get(starts, "married_joint")?)?,
                    ss_start_other: num(get(starts, "other")?)?,
                    ss_range: num(get(&doc, "social_security_exemption_range")?)?,
                    standard_single: num(get(amounts, "single")?)?,
                    standard_hoh: num(get(amounts, "head_of_household")?)?,
                    standard_joint: num(get(amounts, "married_joint")?)?,
                    aged_addition: p.f("standard_deduction_aged_addition")?,
                    exemption_amount: p.f("exemption_amount")?,
                });
            }
        }
        let low_income = if p.in_effect("low_income_credit_share")? {
            let rows = Interp::new(&params::brackets(p.value("low_income_credit_share", &[])?)?);
            if p.in_effect("low_income_credit_aged_income")? {
                Some((rows, Some(p.f("low_income_credit_aged_income")?), 0.0))
            } else {
                Some((rows, None, num(get(&doc, "low_income_credit_income")?)?))
            }
        } else {
            None
        };
        let child_care_refundable = match p.opt("child_care_refundable_rate")? {
            Some(rate) => {
                let limits = p.value("child_care_refundable_agi", &[])?;
                Some((rate.as_f64().ok_or("expected a number")?, num(get(limits, "married_joint")?)?, num(get(limits, "other")?)?))
            }
            None => None,
        };
        let charity_credit = match p.opt("charity_credit")? {
            Some(c) => Some((num(get(c, "rate")?)?, num(get(c, "cap")?)?)),
            None => None,
        };
        let telephone = match opt("telephone_credit")? {
            Some(credit) => Some((credit, p.f("telephone_credit_aged_income_limit")?, opt("telephone_credit_income_limit")?)),
            None => None,
        };
        let (property_rebate, renter_rebate) = if p.in_effect("property_rebate")? {
            let c = p.value("property_rebate", &[])?;
            (
                Some(PropertyRebate {
                    rent_share: num(get(c, "rent_share")?)?,
                    max: num(get(c, "max")?)?,
                    table: Interp::new(&params::brackets(get(c, "table")?)?),
                    income_limit: c.get("income_limit").map(num).transpose()?,
                }),
                None,
            )
        } else {
            (
                None,
                Some(RenterRebate {
                    rent_share: num(get(&doc, "renter_rebate_rent_share")?)?,
                    table: Interp::new(&params::brackets(p.value("renter_rebate_table", &[])?)?),
                    investment_threshold: num(get(&doc, "renter_income_investment_threshold")?)?,
                    aged_cap: match p.opt("renter_rebate_aged_cap")? {
                        Some(c) => Some((num(get(c, "max")?)?, num(get(c, "full_limit")?)?)),
                        None => None,
                    },
                    income_limit: num(get(&doc, "renter_rebate_income_limit")?)?,
                    socsec: Socsec::load(root, year)?,
                }),
            )
        };
        let child_tax_credit = match p.opt("child_tax_credit")? {
            Some(c) => Some(ChildTaxCredit {
                amount: num(get(c, "amount")?)?,
                reduction_start: num(get(c, "reduction_start")?)?,
                reduction_amount: num(get(c, "reduction_amount")?)?,
                reduction_increment: num(get(c, "reduction_increment")?)?,
                age_limit: num(get(c, "age_limit")?)?,
            }),
            None => None,
        };
        Ok(Self {
            year,
            federal_share,
            federal_brackets,
            cgx: Cgx {
                share: if p.in_effect("capital_gain_exclusion_share")? { p.f("capital_gain_exclusion_share")? } else { 0.0 },
                cap: opt("capital_gain_exclusion_cap")?,
                floor: opt("capital_gain_exclusion_floor")?,
            },
            itemized_addback_multiple: opt("itemized_addback_multiple")?,
            post2017,
            tables,
            minimum_tax: match opt("minimum_tax_share")? {
                Some(share) => Some((share, num(get(&doc, "minimum_tax_agi")?)?)),
                None => None,
            },
            low_income,
            child_care_rate: opt("child_care_rate")?,
            child_care_refundable,
            charity_credit,
            eitc_rate: opt("eitc_rate")?,
            eitc_childless_rate: opt("eitc_childless_rate")?,
            telephone,
            property_rebate,
            renter_rebate,
            child_care_full_refundable_rate: opt("child_care_full_refundable_rate")?,
            child_tax_credit,
            dividend_adjustment: dividend_adjustment(root)?,
        })
    }
}

pub fn tax(i: &Inp, f: &Fed, p: &VtParams) -> f64 {
    let y = p.year;
    let fs = i.fs();
    let is_single = fs == Fs::Single;
    let is_joint = fs == Fs::Joint;
    let is_hoh = fs == Fs::Hoh;
    let sep = i.sepret();
    let fed_agi = f.agi;
    let fti = f.taxable_income;
    let aged = i.aged_count();
    let dependent_filer = i.is_dependent_filer();
    let hh = household_income(i, p.dividend_adjustment);
    let vt_ui = unemployment_total(i);
    let vt_schede = i.otherprop + if y >= 1987 { i.scorp } else { 0.0 };
    let setax = f.payroll.setax;

    let (regtax, almtax) = if y <= 1986 { (f.regular_tax, f.pre87.almtax) } else { (f.schedule_tax, f.amt) };
    let fed_ccc = f.federal_chcr;
    // Positive federal net capital gain; losses do not matter here.
    let capgn = (i.stcg + i.ltcg).max(0.0);
    let share = p.cgx.share;

    // ---- taxable income and tax ----
    let taxinc;
    let mut statax;
    if let Some(federal_share) = p.federal_share {
        let mut t = regtax + almtax - fed_ccc;
        if y >= 1987 {
            t -= f.federal_elder;
        }
        taxinc = t.max(0.0);
        statax = match &p.federal_brackets {
            Some(b) => b.tax(taxinc),
            None => federal_share * taxinc,
        };
    } else {
        let tables = p.tables.as_ref().expect("brackets exist when there is no federal tax share");
        let mut t;
        if y <= 2017 {
            t = fti;
            if (2002..=2007).contains(&y) {
                t = (t - share * capgn).max(0.0);
            } else if y == 2008 || y == 2009 {
                t = (t - share * capgn.min(fti)).max(0.0);
            } else if let Some(cap) = p.cgx.cap {
                t = (t - (share * fti).min(capgn.min(cap))).max(0.0);
            } else if let Some(floor) = p.cgx.floor {
                let dgain = capgn.min(floor).max(share * capgn);
                t = (t - (share * fti).min(dgain)).max(0.0);
            }
            if let Some(multiple) = p.itemized_addback_multiple {
                let itemized = f.itemized_deduction;
                let salt = f.ded;
                let addd50 = itemized.min(salt);
                let addit = ((itemized - (i.charity_cash + salt)).max(0.0) - multiple * f.standard_deduction).max(0.0);
                t += if f.itemizes { addd50 + addit } else { 0.0 };
            }
        } else {
            let c = p.post2017.as_ref().expect("post-2017 parameters");
            let floor = p.cgx.floor.unwrap_or(0.0);
            let dedg = (share * fti).min(capgn.min(floor).max(share * capgn));
            // Taxable Social Security is exempt, phased out above an AGI start.
            let ss = f.taxable_social_security;
            let phb = if is_joint { c.ss_start_joint } else { c.ss_start_other };
            let exssb = ss - ((fed_agi - phb).max(0.0) / c.ss_range).min(1.0) * ss;
            let agi = fed_agi - dedg - exssb;
            let exemps = federal_exemption_count(y, i);
            let mut stded = if is_hoh {
                c.standard_hoh
            } else if is_joint {
                c.standard_joint
            } else {
                c.standard_single
            };
            stded += c.aged_addition * aged;
            t = (agi - exemps * c.exemption_amount - stded).max(0.0);
        }
        taxinc = t;
        statax = if is_single {
            tables.single.tax(taxinc)
        } else if is_hoh {
            tables.hoh.tax(taxinc)
        } else {
            tables.married.tax(taxinc * sep) / sep
        };
        if let Some((share, agi_threshold)) = p.minimum_tax {
            if fed_agi > agi_threshold {
                statax = statax.max(share * fed_agi);
            }
        }
    }

    // ---- nonrefundable credits ----
    let lowcr = match &p.low_income {
        Some((rows, Some(aged_income), _)) => {
            if aged > 0.0 && hh < *aged_income { statax * rows.eval(statax) } else { 0.0 }
        }
        Some((rows, None, income)) => if hh <= *income { statax * rows.eval(statax) } else { 0.0 },
        None => 0.0,
    };
    // TAXSIM pays the student credit to every filer; the statutory mode omits it.
    let studcr = 0.0;
    let mut chcr = p.child_care_rate.map_or(0.0, |rate| rate * fed_ccc);
    let mut chcref = 0.0;
    if let Some((rate, limit_joint, limit_other)) = p.child_care_refundable {
        let low = fed_agi <= if is_joint { limit_joint } else { limit_other };
        chcref = if low { rate * fed_ccc } else { 0.0 };
        if chcref > 0.0 {
            chcr = 0.0;
        }
    }
    let contr = p.charity_credit.map_or(0.0, |(rate, cap)| rate * i.charity_cash.min(cap));
    statax = (statax - lowcr - studcr - chcr - contr).max(0.0);

    // ---- refundable credits ----
    let mut earncr = p.eitc_rate.map_or(0.0, |rate| rate * f.eitc);
    if let Some(rate) = p.eitc_childless_rate {
        if i.dep17 < 1.0 {
            earncr = rate * f.eitc;
        }
    }
    let telcr = match p.telephone {
        Some((credit, aged_limit, young_limit)) => {
            let aged_tel = if hh < aged_limit { credit } else { 0.0 };
            let young_tel = match young_limit {
                Some(limit) if hh < limit => credit,
                _ => 0.0,
            };
            if dependent_filer {
                0.0
            } else if aged > 0.0 {
                aged_tel
            } else {
                young_tel
            }
        }
        None => 0.0,
    };

    // Homeowner/renter rebate (refundable).
    let rentpaid = i.rentpaid;
    let pcred = if let Some(c) = &p.property_rebate {
        let ptax = i.proptax + c.rent_share * rentpaid;
        let mut pcred = (ptax - c.table.eval(hh) * hh).max(0.0).min(c.max);
        if let Some(limit) = c.income_limit {
            if hh >= limit {
                pcred = 0.0;
            }
        }
        if aged > 0.0 { pcred } else { 0.0 }
    } else {
        let c = p.renter_rebate.as_ref().expect("renter rebate parameters");
        let investment = i.dividends + i.intrec;
        let socsec = c.socsec.total(y, i, setax, f.payroll.addmed);
        let vthy = i.pwages.max(0.0)
            + i.swages.max(0.0)
            + investment
            + vt_ui
            + (i.psemp + i.ssemp).max(0.0)
            + (i.stcg + i.ltcg).max(0.0)
            + i.pensions
            + i.gssi
            + vt_schede.max(0.0)
            - socsec
            + (investment - c.investment_threshold).max(0.0);
        let ptax = c.rent_share * rentpaid;
        let excess = ptax - c.table.eval(vthy) * vthy;
        let mut pcred = match c.aged_cap {
            Some((cap, full)) => {
                let amount = if vthy <= full { excess.max(0.0).min(cap) } else { cap - 0.5 * (vthy - full) };
                if aged > 0.0 { amount } else { 0.0 }
            }
            None => excess.max(0.0),
        };
        if vthy > c.income_limit {
            pcred = 0.0;
        }
        pcred
    };
    if let Some(rate) = p.child_care_full_refundable_rate {
        chcref = rate * fed_ccc;
    }
    let ctc = match &p.child_tax_credit {
        Some(c) => {
            let excess = (fed_agi - c.reduction_start).max(0.0);
            let reduction = c.reduction_amount * (excess / c.reduction_increment).ceil();
            // Counts without ages fall back to the under-6 count.
            let young = if c.age_limit >= 7.0 { i.children_under_7.max(i.dep6) } else { i.dep6 };
            (c.amount * young - reduction).max(0.0)
        }
        None => 0.0,
    };
    statax = statax - pcred - earncr - telcr - chcref - ctc;
    statax * 1.0
}
