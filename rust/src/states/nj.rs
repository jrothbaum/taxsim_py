//! New Jersey gross income tax (port of `calculators.states.nj`).

use std::path::Path;

use crate::common::Brackets;
use crate::federal::{Fed, Inp};
use crate::params::{self, get, load, num, resolve_year, Fs, YearParams};
use crate::payroll::round_half_even;
use crate::states::{dividend_adjustment, federal_exemption_count, household_income, law_year_is_exact, Interp};

enum Schedule {
    One(Brackets),
    ByStatus { single: Brackets, married: Brackets },
}

impl Schedule {
    fn tax(&self, income: f64, uses_married: bool) -> f64 {
        match self {
            Self::One(b) => b.tax(income),
            Self::ByStatus { single, married } => if uses_married { married.tax(income) } else { single.tax(income) },
        }
    }
}

enum NoTax {
    Flat(f64),
    ByStatus { single: f64, other: f64 },
    PerTaxpayer(f64),
}

struct Floor {
    table: Interp,
    share: f64,
    floor_share: f64,
}

struct Aged {
    income_share: f64,
    tiers: [f64; 3],
    maximum: f64,
    owner_minimum: f64,
    owner_middle_other: f64,
    owner_top: f64,
    renter_minimum: f64,
    renter_middle_flat: Option<f64>,
    renter_middle_other: f64,
    renter_top: f64,
}

struct Homestead {
    amount: f64,
    income_limit: f64,
    renter: f64,
    renter_income_limit: f64,
    aged: Aged,
}

struct Childless {
    minimum_age: f64,
    maximum_age_applies: bool,
}

pub struct NjParams {
    year: i64,
    loss_limit: f64,
    schedule: Schedule,
    pension_exclusion: [f64; 4],
    pension_phaseout: Option<[Vec<(f64, f64)>; 4]>,
    pension_household_limit: Option<f64>,
    retirement_earnings_limit: f64,
    no_tax: NoTax,
    hoh_no_tax_taxpayers: f64,
    exemption_per_taxpayer: f64,
    exemption_per_dependent: f64,
    floor: Option<Floor>,
    excess_credit_rate: f64,
    rent_share: f64,
    renter_credit: Option<(f64, f64)>,
    low_income_credit: Option<(f64, f64)>,
    homestead: Option<Homestead>,
    deduction_cap: Option<(f64, f64, f64)>,
    eitc_match_rate: f64,
    childless: Option<Childless>,
    eitc_income_limit: Option<f64>,
    child_tax_credit: Option<Brackets>,
    dividend_adjustment: f64,
}

impl NjParams {
    pub fn load(root: &Path, year: i64) -> Result<Self, String> {
        let doc = load(root, "states/nj/income_tax.yaml")?;
        law_year_is_exact(root, &doc, year)?;
        let capital_gains = load(root, "national/capital_gains.yaml")?;
        let p = YearParams::new(&doc, year);
        let opt = |key: &str| -> Result<Option<f64>, String> { Ok(if p.in_effect(key)? { Some(p.f(key)?) } else { None }) };

        let tables = p.value("brackets", &[])?;
        let schedule = if tables.is_mapping() {
            Schedule::ByStatus {
                single: Brackets::new(&params::brackets(get(tables, "single")?)?),
                married: Brackets::new(&params::brackets(get(tables, "married")?)?),
            }
        } else {
            Schedule::One(Brackets::new(&params::brackets(tables)?))
        };
        let pension_exclusion = {
            let v = get(&doc, "pension_exclusion")?;
            let mut out = [0.0; 4];
            for (k, key) in ["single", "married_joint", "married_separate", "head_of_household"].iter().enumerate() {
                out[k] = num(resolve_year(get(v, key)?, year)?)?;
            }
            out
        };
        let pension_phaseout = if p.in_effect("pension_exclusion_phaseout")? {
            let v = p.value("pension_exclusion_phaseout", &[])?;
            let rows = |key: &str| -> Result<Vec<(f64, f64)>, String> {
                Ok(params::brackets(get(v, key)?)?.into_iter().map(|r| (r[0], r[1])).collect())
            };
            Some([rows("single")?, rows("married_joint")?, rows("married_separate")?, rows("head_of_household")?])
        } else {
            None
        };
        let limits = p.value("no_tax_threshold", &[])?;
        let no_tax = if !limits.is_mapping() {
            NoTax::Flat(num(limits)?)
        } else if limits.get("single").is_some() {
            NoTax::ByStatus { single: num(get(limits, "single")?)?, other: num(get(limits, "other")?)? }
        } else {
            NoTax::PerTaxpayer(num(get(limits, "per_taxpayer")?)?)
        };
        let floor = if p.in_effect("property_tax_floor")? {
            let rd = p.value("rent_deduction", &[])?;
            Some(Floor {
                table: Interp::new(&params::brackets(p.value("property_tax_floor", &[])?)?),
                share: num(get(rd, "share")?)?,
                floor_share: num(get(rd, "floor_share")?)?,
            })
        } else {
            None
        };
        let renter_credit = if p.in_effect("renter_credit")? {
            let rc = p.value("renter_credit", &[])?;
            Some((num(get(rc, "amount")?)?, num(get(rc, "aged_addition")?)?))
        } else {
            None
        };
        let low_income_credit = if renter_credit.is_none() && p.in_effect("low_income_property_credit")? {
            let lc = p.value("low_income_property_credit", &[])?;
            Some((num(get(lc, "homeowner")?)?, num(get(lc, "renter")?)?))
        } else {
            None
        };
        let homestead = if p.in_effect("homestead_rebate")? {
            let a = YearParams::new(get(&doc, "homestead_rebate_aged")?, year);
            let tiers = a.value("tier_limits", &[])?.as_sequence().ok_or("tier_limits must be a list")?;
            Some(Homestead {
                amount: p.f("homestead_rebate")?,
                income_limit: p.f("homestead_rebate_income_limit")?,
                renter: p.f("homestead_rebate_renter")?,
                renter_income_limit: p.f("homestead_rebate_renter_income_limit")?,
                aged: Aged {
                    income_share: a.f("income_share")?,
                    tiers: [num(&tiers[0])?, num(&tiers[1])?, num(&tiers[2])?],
                    maximum: a.f("maximum")?,
                    owner_minimum: a.f("owner_minimum")?,
                    owner_middle_other: a.f("owner_middle_other")?,
                    owner_top: a.f("owner_top")?,
                    renter_minimum: a.f("renter_minimum")?,
                    renter_middle_flat: if year >= 2001 { Some(a.f("renter_middle_flat")?) } else { None },
                    renter_middle_other: a.f("renter_middle_other")?,
                    renter_top: a.f("renter_top")?,
                },
            })
        } else {
            None
        };
        Ok(Self {
            year,
            loss_limit: if year >= 1987 { num(resolve_year(get(&capital_gains, "net_capital_loss_limit")?, year)?)? } else { 0.0 },
            schedule,
            pension_exclusion,
            pension_phaseout,
            pension_household_limit: opt("pension_exclusion_household_income_limit")?,
            retirement_earnings_limit: num(get(&doc, "other_retirement_exclusion_earnings_limit")?)?,
            no_tax,
            hoh_no_tax_taxpayers: num(get(&doc, "head_of_household_no_tax_taxpayers")?)?,
            exemption_per_taxpayer: p.f("exemption_per_taxpayer")?,
            exemption_per_dependent: p.f("exemption_per_dependent")?,
            floor,
            excess_credit_rate: num(get(&doc, "property_tax_excess_credit_rate")?)?,
            rent_share: num(get(&doc, "property_tax_rent_share")?)?,
            renter_credit,
            low_income_credit,
            homestead,
            deduction_cap: if year >= 1996 {
                Some((p.f("property_tax_deduction_cap")?, p.f("property_tax_deduction_share")?, p.f("property_tax_credit")?))
            } else {
                None
            },
            eitc_match_rate: p.f("eitc_match_rate")?,
            childless: if year >= 2020 {
                Some(Childless {
                    minimum_age: num(resolve_year(get(&doc, "eitc_childless_minimum_age")?, year)?)?,
                    maximum_age_applies: resolve_year(get(&doc, "eitc_childless_maximum_age_applies")?, year)?
                        .as_bool()
                        .ok_or("expected a boolean")?,
                })
            } else {
                None
            },
            eitc_income_limit: opt("eitc_income_limit")?,
            child_tax_credit: if p.in_effect("child_tax_credit")? {
                Some(Brackets::new(&params::brackets(p.value("child_tax_credit", &[])?)?))
            } else {
                None
            },
            dividend_adjustment: dividend_adjustment(root)?,
        })
    }
}

/// Homestead property tax rebate (1990-2008), refundable.
fn homestead_rebate(h: &Homestead, y: i64, agi: f64, ptax: f64, i: &Inp, married: bool, sep: f64, aged: f64) -> f64 {
    let a = &h.aged;
    let [low, middle, top] = a.tiers;
    let reb = (ptax - a.income_share * agi).max(0.0);
    let between = |minimum: f64| minimum.max(a.maximum.min(reb));
    let owner = if agi <= low {
        between(a.owner_minimum)
    } else if agi <= middle {
        if married { between(a.owner_minimum) } else { a.owner_middle_other }
    } else if agi <= top {
        a.owner_top
    } else {
        0.0
    };
    let renter_middle_married = if y >= 2001 { a.renter_middle_flat.unwrap_or(0.0) } else { between(a.renter_minimum) };
    let renter = if agi <= low {
        between(a.renter_minimum)
    } else if agi <= middle {
        if married { renter_middle_married } else { a.renter_middle_other }
    } else if agi <= top {
        a.renter_top
    } else {
        0.0
    };
    let aged_rebate = if i.proptax > 0.0 {
        owner
    } else if i.rentpaid > 0.0 {
        renter
    } else {
        0.0
    };
    let mut under65 = 0.0;
    if i.proptax > 0.0 && agi < h.income_limit {
        under65 = h.amount;
    }
    if i.rentpaid > 0.0 && agi < h.renter_income_limit {
        under65 = h.renter;
    }
    (if aged > 0.0 { aged_rebate } else { under65 }) / sep
}

pub fn tax(i: &Inp, f: &Fed, p: &NjParams) -> f64 {
    let y = p.year;
    let fs = i.fs();
    let is_joint = fs == Fs::Joint;
    let is_hoh = fs == Fs::Hoh;
    let uses_married = is_joint || is_hoh;
    let sep = i.sepret();
    let txp = i.taxpayer_count();
    let aged = i.aged_count();
    let depx = i.depx;
    let proptax = i.proptax;
    let dividends = i.dividends + p.dividend_adjustment;
    let schede = i.otherprop + if y >= 1987 { i.scorp } else { 0.0 };
    let household = household_income(i, p.dividend_adjustment);

    let fullcg = i.stcg + i.ltcg;
    let capgn = if y >= 1987 { fullcg.max(-p.loss_limit / sep) } else { f.pre87.capgn };

    // ---- gross income ----
    let wages = i.pwages.max(0.0) + i.swages.max(0.0);
    let semp = i.psemp + i.ssemp + i.pwages.min(0.0) + i.swages.min(0.0);
    let business = semp + i.pbusinc + i.pprofinc + i.sbusinc + i.sprofinc;
    let mut agi = wages + dividends + i.intrec + business.max(0.0) + capgn.max(0.0) + schede.max(0.0) + i.pensions;
    if y < 1992 {
        agi += f.taxable_unemployment + f.taxable_social_security;
    }
    // Pension exclusion for taxpayers 65 or older; unused exclusion covers other income when earnings are small.
    let mut deduct = p.pension_exclusion[fs as usize];
    if let Some(rows) = &p.pension_phaseout {
        let share = rows[fs as usize].iter().find(|(limit, _)| household <= *limit).map_or(0.0, |(_, s)| *s);
        deduct *= share;
    } else if let Some(limit) = p.pension_household_limit {
        deduct = if household <= limit { deduct } else { 0.0 };
    }
    let pensions = i.pensions;
    let mut excluded = agi - pensions.max(0.0).min(deduct);
    let unused = (deduct - pensions).max(0.0);
    let earnings = wages + i.psemp + i.ssemp;
    if earnings <= p.retirement_earnings_limit {
        excluded -= unused.min(excluded.max(0.0));
    }
    if aged > 0.0 {
        agi = excluded;
    }
    // Gross income counts the full net gain, never a net loss; TAXSIM swaps the federal gain for the raw one.
    let agi = agi - capgn.max(0.0) + fullcg.max(0.0);

    // ---- no-tax status ----
    let no_tax = match p.no_tax {
        NoTax::Flat(limit) => {
            let threshold = limit / sep;
            if (1994..=1996).contains(&y) { agi < threshold } else { agi <= threshold }
        }
        NoTax::ByStatus { single, other } => if fs == Fs::Single { agi <= single } else { agi <= other / sep },
        NoTax::PerTaxpayer(per) => agi <= per * if is_hoh { p.hoh_no_tax_taxpayers } else { txp },
    };

    // ---- taxable income and tax ----
    let exemp = (txp + aged) * p.exemption_per_taxpayer + depx * p.exemption_per_dependent;
    let mut taxinc = (agi - exemp).max(0.0);
    let flat_threshold = match p.no_tax {
        NoTax::Flat(limit) => limit,
        _ => 0.0,
    };
    let rentpaid = i.rentpaid;
    let mut rescr = 0.0;
    if let Some(fl) = &p.floor {
        let floor = fl.table.eval(taxinc) / sep;
        let pded = if proptax > 0.0 { floor.max(proptax) } else { 0.0 };
        let rded = if proptax < 1.0 && rentpaid > 0.0 { (fl.floor_share * floor).max(fl.share * rentpaid) } else { 0.0 };
        if agi > flat_threshold / sep {
            rescr = p.excess_credit_rate * (pded + rded - taxinc).max(0.0);
            taxinc = (taxinc - pded - rded).max(0.0);
        }
    }
    let mut statax = p.schedule.tax(taxinc, uses_married);
    if no_tax {
        statax = 0.0;
        taxinc = 0.0;
    }

    // ---- property tax credits and rebates ----
    let ptax = proptax.max(0.0) + if rentpaid > 0.0 && federal_exemption_count(y, i) > 0.0 { p.rent_share * rentpaid } else { 0.0 };
    let mut pcred = 0.0;
    if let Some((amount, aged_addition)) = p.renter_credit {
        pcred = if rentpaid > 0.0 { amount + if aged > 0.0 { aged_addition } else { 0.0 } } else { 0.0 };
    } else if let Some((homeowner, renter)) = p.low_income_credit {
        let low = agi <= flat_threshold / sep;
        pcred = if low && proptax > 0.0 {
            homeowner
        } else if low && rentpaid > 0.0 {
            renter
        } else {
            0.0
        };
    }
    let mut rebate = 0.0;
    if let Some(h) = &p.homestead {
        rebate = homestead_rebate(h, y, agi, ptax, i, uses_married, sep, aged);
        if y >= 2004 && !(rentpaid > 0.0) {
            rebate = 0.0;
        }
    }
    if let Some((cap, share, credit)) = p.deduction_cap {
        let pded = (cap / sep).min(share * ptax);
        let income_pr = (agi - exemp - pded).max(0.0);
        let statpr = p.schedule.tax(income_pr, uses_married);
        let credit_amount = credit / sep;
        let eligible = ptax > 2.0;
        let take_deduction = eligible && statax - statpr >= credit_amount;
        if eligible && !take_deduction && proptax + rentpaid > 0.0 && taxinc > 0.0 {
            pcred = credit_amount;
        }
        if take_deduction {
            statax = statpr;
            taxinc = income_pr;
        }
    }
    statax = (statax - rescr - pcred).max(0.0) - rebate;

    // ---- earned income credit ----
    let mut earncr = p.eitc_match_rate * f.eitc;
    if let Some(c) = &p.childless {
        // A childless filer meeting every federal rule but the age test gets the flat amount (NJ-1040 line 58).
        let older = i.page.max(i.sage);
        let childless = f.num_children == 0.0;
        let too_young = older > 0.0 && older < c.minimum_age;
        let too_old = c.maximum_age_applies && older >= 65.0;
        let before_test = if y >= 2021 { f.eitc_before_filer_test } else { f.eitc_before_age_test };
        let max_credit = f.eitc_schedule.map_or(0.0, |s| s.1);
        let flat = round_half_even(p.eitc_match_rate * max_credit, 0);
        earncr = if childless && !i.is_dependent_filer() && !too_young && !too_old && before_test > 0.0 && f.eitc <= 0.0 {
            flat
        } else if childless && (too_young || too_old) {
            0.0
        } else {
            earncr
        };
    }
    if let Some(limit) = p.eitc_income_limit {
        earncr = if agi <= limit && depx > 0.0 { earncr } else { 0.0 };
    }
    statax -= earncr;
    if let Some(ctc) = &p.child_tax_credit {
        statax -= ctc.rate(taxinc - 0.005) * i.dep6;
    }
    statax * 1.0
}
