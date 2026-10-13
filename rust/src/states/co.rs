//! Colorado income tax (port of `calculators.states.co`).

use std::path::Path;

use crate::common::Brackets;
use crate::federal::{Fed, Inp};
use crate::params::{self, get, load, num, resolve_year, Fs, YearParams, STATUS_KEYS};
use crate::payroll::round_half_even;
use crate::states::{
    dividend_adjustment, dividend_exclusion_addback, household_income, law_year_is_exact, Interp,
};

struct PensionSubtraction {
    age_older: f64,
    age_younger: f64,
    cap_older: f64,
    cap_younger: f64,
    limit_joint: f64,
    limit_other: f64,
}

enum Pension {
    Subtraction(PensionSubtraction),
    Cap(f64),
}

struct Credit {
    maximum: f64,
    start_single: f64,
    start_other: f64,
    rate: f64,
    limit_single: f64,
    limit_other: f64,
}

struct PreCredits {
    property: Credit,
    rent_share: f64,
    addition: Option<(f64, f64)>,
    heating: Option<Credit>,
    heating_rent_share: f64,
}

struct PercentStandard {
    rate: f64,
    cap: f64,
    cap_separate: f64,
    allowance_separate_base: f64,
    allowance_separate_cap: f64,
    allowance_base: f64,
    allowance_per_exemption: f64,
    allowance_cap: f64,
    allowance_bonus: f64,
    bonus_income_floor: f64,
    bonus_income_per_exemption: f64,
    bonus_phaseout_rate: f64,
}

enum Standard {
    Percent(PercentStandard),
    Flat(f64),
}

struct Old {
    aif: f64,
    interest_exclusion: Option<(f64, f64)>,
    two_earner: Option<(f64, f64)>,
    standard: Standard,
    gas: (f64, f64),
    exemption: f64,
    table: Brackets,
    multiplier: f64,
    surcharge: (f64, f64),
    food: Option<(Interp, f64)>,
}

struct HighIncome {
    agi_threshold: f64,
    limit_joint: f64,
    limit_other: f64,
    itemized_only: bool,
    qbi_limit_joint: f64,
    qbi_limit_other: f64,
}

struct Amt {
    exemption: [f64; 4],
    threshold: [f64; 4],
    phaseout_rate: f64,
    rate: f64,
}

struct New {
    ui: Option<(f64, f64)>,
    high_income: Option<HighIncome>,
    marriage_penalty: Option<f64>,
    aged_marriage: Option<f64>,
    flat_rate: f64,
    amt: Amt,
}

enum Refund {
    Flat(f64),
    Steps(Brackets),
    Table(Interp),
}

enum Share {
    Table(Interp),
    Flat(f64),
}

struct FedCtc {
    amount_per_child: f64,
    max_refundable: f64,
    actc_rate: f64,
    earned_floor: f64,
}

struct Fac {
    amount: f64,
    older_share: f64,
    reduction_rate: f64,
    increment: f64,
    thresholds: [f64; 4],
}

struct Ctc {
    scales: [Brackets; 4],
    fed: Option<FedCtc>,
    fac: Option<Fac>,
}

pub struct CoParams {
    year: i64,
    pension: Pension,
    pre_credits: Option<PreCredits>,
    old: Option<Old>,
    new: Option<New>,
    refund: Refund,
    share: Share,
    share_nonzero: bool,
    dependent_offset: Option<f64>,
    eitc_share: f64,
    minimum_age: Option<f64>,
    ctc: Option<Ctc>,
    dividend_adjustment: f64,
}

impl CoParams {
    pub fn load(root: &Path, year: i64) -> Result<Self, String> {
        let doc = load(root, "states/co/income_tax.yaml")?;
        law_year_is_exact(root, &doc, year)?;
        let federal_tax = load(root, "national/income_tax.yaml")?;
        let pre87_doc = load(root, "national/pre1987.yaml")?;
        let amt_doc = load(root, "national/amt.yaml")?;
        let credits_doc = load(root, "national/credits.yaml")?;
        let p = YearParams::new(&doc, year);
        let opt = |key: &str| -> Result<Option<f64>, String> { Ok(if p.in_effect(key)? { Some(p.f(key)?) } else { None }) };

        let pension = if p.in_effect("pension_subtraction")? {
            let ps = p.value("pension_subtraction", &[])?;
            let limits = get(ps, "social_security_full_agi_limit")?;
            Pension::Subtraction(PensionSubtraction {
                age_older: num(get(ps, "age_older")?)?,
                age_younger: num(get(ps, "age_younger")?)?,
                cap_older: num(get(ps, "cap_older")?)?,
                cap_younger: num(get(ps, "cap_younger")?)?,
                limit_joint: num(get(limits, "joint")?)?,
                limit_other: num(get(limits, "other")?)?,
            })
        } else {
            Pension::Cap(p.f("pension_exclusion")?)
        };
        let credit = |name: &str, with_limit: bool| -> Result<Credit, String> {
            Ok(Credit {
                maximum: p.num_at(name, &["maximum"])?,
                start_single: p.num_at(name, &["phaseout_start", "single"])?,
                start_other: p.num_at(name, &["phaseout_start", "other"])?,
                rate: p.num_at(name, &["phaseout_rate"])?,
                limit_single: if with_limit { p.num_at(name, &["income_limit", "single"])? } else { 0.0 },
                limit_other: if with_limit { p.num_at(name, &["income_limit", "other"])? } else { 0.0 },
            })
        };
        let (pre_credits, old) = if year <= 1986 {
            let q = YearParams::new(&pre87_doc, year);
            let two_earner = if (1982..=1986).contains(&year) { Some((q.f("two_earner_deduction_rate")?, q.f("two_earner_deduction_cap")?)) } else { None };
            let standard = if p.in_effect("standard_deduction_percent")? {
                let sd = p.value("standard_deduction_percent", &[])?;
                let n = |key: &str| -> Result<f64, String> { num(get(sd, key)?) };
                Standard::Percent(PercentStandard {
                    rate: n("rate")?,
                    cap: n("cap")?,
                    cap_separate: n("cap_separate")?,
                    allowance_separate_base: n("allowance_separate_base")?,
                    allowance_separate_cap: n("allowance_separate_cap")?,
                    allowance_base: n("allowance_base")?,
                    allowance_per_exemption: n("allowance_per_exemption")?,
                    allowance_cap: n("allowance_cap")?,
                    allowance_bonus: n("allowance_bonus")?,
                    bonus_income_floor: n("bonus_income_floor")?,
                    bonus_income_per_exemption: n("bonus_income_per_exemption")?,
                    bonus_phaseout_rate: n("bonus_phaseout_rate")?,
                })
            } else {
                Standard::Flat(p.f("standard_deduction")?)
            };
            let gas = p.value("gasoline_tax_deduction", &[])?;
            let pex = p.value("personal_exemption", &[])?;
            let aif = p.f("indexing_factor")?;
            let surcharge = p.value("investment_income_surcharge", &[])?;
            let scaled: Vec<Vec<f64>> = params::brackets(p.value("brackets", &[])?)?.into_iter().map(|r| vec![r[0] * aif, r[1]]).collect();
            let food = if p.in_effect("food_credit")? {
                let table = params::brackets(p.value("food_credit", &[])?)?;
                let last = table[table.len() - 1][1];
                Some((Interp::new(&table), last))
            } else {
                None
            };
            let amount = num(get(pex, "amount")?)?;
            let indexed = get(pex, "indexed")?.as_bool().ok_or("expected a boolean")?;
            (
                Some(PreCredits {
                    property: credit("property_credit", true)?,
                    rent_share: num(get(&doc, "property_credit_rent_share")?)?,
                    addition: match p.opt("property_credit_addition")? {
                        Some(a) => Some((num(get(a, "rate")?)?, num(get(a, "rent_share")?)?)),
                        None => None,
                    },
                    heating: if year >= 1979 { Some(credit("heating_credit", false)?) } else { None },
                    heating_rent_share: if year >= 1979 { num(get(&doc, "heating_credit_rent_share")?)? } else { 0.0 },
                }),
                Some(Old {
                    aif,
                    interest_exclusion: if p.in_effect("interest_exclusion_per_taxpayer")? {
                        Some((p.f("interest_exclusion_per_taxpayer")?, p.f("dividend_exclusion_per_taxpayer")?))
                    } else {
                        None
                    },
                    two_earner,
                    standard,
                    gas: (num(get(gas, "per_taxpayer")?)?, num(get(gas, "per_dependent")?)?),
                    exemption: if indexed { amount * aif } else { amount },
                    table: Brackets::new(&scaled),
                    multiplier: p.f("tax_multiplier")?,
                    surcharge: (num(get(surcharge, "threshold_per_taxpayer")?)?, num(get(surcharge, "rate")?)?),
                    food,
                }),
            )
        } else {
            (None, None)
        };
        let new = if year >= 1987 {
            let ui = if year == 2020 {
                let u = YearParams::new(&federal_tax, 2020).value("unemployment_exclusion", &[])?;
                Some((num(get(u, "per_spouse")?)?, num(get(u, "agi_limit")?)?))
            } else {
                None
            };
            let by_status = |name: &str| -> Result<[f64; 4], String> {
                let node = get(&amt_doc, name)?;
                let mut out = [0.0; 4];
                for (k, key) in STATUS_KEYS.iter().enumerate() {
                    out[k] = num(resolve_year(get(node, key)?, year)?)?;
                }
                Ok(out)
            };
            Some(New {
                ui,
                high_income: match p.opt("high_income_addback")? {
                    Some(h) => Some(HighIncome {
                        agi_threshold: num(get(h, "agi_threshold")?)?,
                        limit_joint: num(get(h, "limit_joint")?)?,
                        limit_other: num(get(h, "limit_other")?)?,
                        itemized_only: get(h, "itemized_only")?.as_bool().ok_or("expected a boolean")?,
                        qbi_limit_joint: num(get(h, "qbi_agi_limit_joint")?)?,
                        qbi_limit_other: num(get(h, "qbi_agi_limit_other")?)?,
                    }),
                    None => None,
                },
                marriage_penalty: opt("marriage_penalty_subtraction")?,
                aged_marriage: opt("aged_marriage_subtraction")?,
                flat_rate: p.f("flat_rate")?,
                amt: Amt {
                    exemption: by_status("exemption")?,
                    threshold: by_status("exemption_phaseout_threshold")?,
                    phaseout_rate: num(resolve_year(get(&amt_doc, "exemption_phaseout_rate")?, year)?)?,
                    rate: p.f("amt_rate")?,
                },
            })
        } else {
            None
        };
        let refund_v = p.value("sales_tax_refund", &[])?;
        let refund = if !refund_v.is_sequence() {
            Refund::Flat(num(refund_v)?)
        } else if year >= 2022 {
            Refund::Steps(Brackets::new(&params::brackets(refund_v)?))
        } else {
            Refund::Table(Interp::new(&params::brackets(refund_v)?))
        };
        let share_v = p.value("child_care_credit_federal_share", &[])?;
        let (share, share_nonzero) = if share_v.is_sequence() {
            (Share::Table(Interp::new(&params::brackets(share_v)?)), true)
        } else {
            let s = num(share_v)?;
            (Share::Flat(s), s != 0.0)
        };
        let ctc = if p.in_effect("child_tax_credit_amount")? {
            let scales = p.value("child_tax_credit_amount", &[])?;
            let mk = |key: &str| -> Result<Brackets, String> { Ok(Brackets::new(&params::brackets(get(scales, key)?)?)) };
            let fed = if year <= 2023 {
                let fp = YearParams::new(get(&credits_doc, "child_tax_credit")?, year);
                Some(FedCtc {
                    amount_per_child: fp.f("amount_per_child")?,
                    max_refundable: fp.f("actc_max_refundable_per_child")?,
                    actc_rate: fp.f("actc_rate")?,
                    earned_floor: fp.f("actc_earned_income_floor")?,
                })
            } else {
                None
            };
            let fac = match p.opt("family_affordability_credit")? {
                Some(c) => {
                    let t = get(c, "reduction_threshold")?;
                    Some(Fac {
                        amount: num(get(c, "amount")?)?,
                        older_share: num(get(c, "older_child_share")?)?,
                        reduction_rate: num(get(c, "reduction_rate")?)?,
                        increment: num(get(c, "reduction_increment")?)?,
                        thresholds: [num(get(t, "single")?)?, num(get(t, "married_joint")?)?, num(get(t, "married_separate")?)?, num(get(t, "head_of_household")?)?],
                    })
                }
                None => None,
            };
            Some(Ctc { scales: [mk("single")?, mk("married_joint")?, mk("married_separate")?, mk("head_of_household")?], fed, fac })
        } else {
            None
        };
        Ok(Self {
            year,
            pension,
            pre_credits,
            old,
            new,
            refund,
            share,
            share_nonzero,
            dependent_offset: opt("child_care_credit_dependent_offset")?,
            eitc_share: p.f("eitc_federal_share")?,
            minimum_age: opt("eitc_minimum_age")?,
            ctc,
            dividend_adjustment: dividend_adjustment(root)?,
        })
    }
}

fn credit_by_group(c: &Credit, single_like: bool) -> (f64, f64) {
    if single_like { (c.start_single, c.limit_single) } else { (c.start_other, c.limit_other) }
}

pub fn tax(i: &Inp, f: &Fed, p: &CoParams) -> f64 {
    let y = p.year;
    let fs = i.fs();
    let is_joint = fs == Fs::Joint;
    let sep = i.sepret();
    let txp = i.taxpayer_count();
    let aged = i.aged_count();
    let depx = i.depx;
    let exemps = txp + depx + aged;
    let salt = f.ded;
    let household = household_income(i, p.dividend_adjustment);

    // ---- pension and Social Security exclusion ----
    let pension_exclusion = match &p.pension {
        Pension::Subtraction(ps) => {
            let limit = if is_joint { ps.limit_joint } else { ps.limit_other };
            // Each taxpayer takes an equal share of the pension and Social Security inputs.
            let people = txp.max(1.0);
            let pension_each = i.pensions / people;
            let ss_each = f.taxable_social_security / people;
            let subtraction = |age: f64, present: bool| -> f64 {
                let older = present && age >= ps.age_older;
                let middle = present && !older && age >= ps.age_younger;
                let social_security = if older {
                    ss_each
                } else if middle {
                    if ss_each > ps.cap_younger && f.agi <= limit { ss_each } else { ss_each.min(ps.cap_younger) }
                } else {
                    0.0
                };
                let cap = if older { ps.cap_older } else { ps.cap_younger };
                let pension = if older || middle { (cap - social_security).max(0.0).min(pension_each) } else { 0.0 };
                pension.max(0.0) + social_security
            };
            subtraction(i.page, true) + subtraction(i.sage, txp > 1.0)
        }
        Pension::Cap(cap) => {
            if aged > 0.0 { (i.pensions + f.taxable_social_security).min(cap * txp).max(0.0) } else { 0.0 }
        }
    };

    let mut propcr = 0.0;
    let mut fuelcr = 0.0;
    let mut foodcr = 0.0;
    let statax_before_credits;
    let co_agi;
    let taxinc;
    if let (Some(old), Some(pc)) = (&p.old, &p.pre_credits) {
        // ---- property tax and heating credits for taxpayers 65 or older ----
        let single_like = matches!(fs, Fs::Single | Fs::Hoh);
        let income = household * if sep == 2.0 { 2.0 } else { 1.0 };
        let (start, limit) = credit_by_group(&pc.property, single_like);
        let eligible = aged > 0.0 && income <= limit;
        let mut prop = (pc.property.maximum - (income - start).max(0.0) * pc.property.rate).max(0.0);
        prop = prop.min(i.proptax + pc.rent_share * i.rentpaid);
        if let Some((rate, rent_share)) = pc.addition {
            prop += rate * (i.proptax + rent_share * i.rentpaid);
        }
        let fuel = match &pc.heating {
            Some(h) => {
                let (hstart, _) = credit_by_group(h, single_like);
                ((h.maximum - (income - hstart) * h.rate).max(0.0)).min(pc.heating_rent_share * i.rentpaid)
            }
            None => 0.0,
        };
        propcr = if eligible { prop } else { 0.0 };
        fuelcr = if eligible { fuel } else { 0.0 };

        // ---- Colorado's own taxable income ----
        let div_addback = if y >= 1980 { dividend_exclusion_addback(y, i, f, p.dividend_adjustment) } else { 0.0 };
        let mut agi_1 = f.agi + div_addback;
        if let Some((rate, cap)) = old.two_earner {
            if is_joint {
                agi_1 += (rate * i.pwages.min(i.swages).max(0.0)).max(0.0).min(cap);
            }
        }
        if let Some((interest, dividend)) = old.interest_exclusion {
            agi_1 -= i.intrec.max(0.0).min(interest * txp) + (i.dividends + p.dividend_adjustment).max(0.0).min(dividend * txp);
        }
        co_agi = agi_1 - pension_exclusion;
        let co_ag = co_agi.max(0.0);
        // Federal tax after credits is deducted, scaled by Colorado AGI over federal AGI.
        let fedded = (f.pre87.taxbc - f.pre87.credit - f.pre87.earncr).max(0.0);
        let share = if co_agi > 0.0 && f.agi > 0.0 { co_agi / f.agi } else { 1.0 };
        let co_fedded = (fedded * share).min(fedded).max(0.0);

        let aif = old.aif;
        let stded = match &old.standard {
            Standard::Percent(s) => {
                let is_sep = fs == Fs::Separate;
                let stded_std = if is_sep { (s.cap_separate * aif).min(s.rate * co_ag) } else { (s.rate * co_ag).min(s.cap * aif / sep) };
                let allow_sep = aif * s.allowance_separate_cap.min(s.allowance_separate_base + exemps * s.allowance_per_exemption);
                let mut sub = exemps * s.allowance_per_exemption;
                sub += (s.bonus_phaseout_rate * (co_ag - (s.bonus_income_floor + exemps * s.bonus_income_per_exemption))).max(0.0);
                sub = (s.allowance_bonus - sub).max(0.0);
                let allow_std = (s.allowance_cap * aif).min(s.allowance_base + s.allowance_per_exemption * exemps + sub);
                if is_sep { stded_std.max(allow_sep) } else { stded_std.max(allow_std) }
            }
            Standard::Flat(amount) => amount * aif / sep,
        };
        let itemizing = if f.pre87.itemizes { 1.0 } else { 0.0 };
        let xitded = itemizing * (f.pre87.deduc - salt) + old.gas.0 * txp + old.gas.1 * depx;
        let deduc = xitded.max(stded);
        let exemp = old.exemption * exemps;
        taxinc = (co_agi - deduc - exemp - co_fedded).max(0.0);

        let mut statax = old.table.tax(taxinc) * old.multiplier;
        let (threshold, rate) = old.surcharge;
        statax += rate * (i.dividends + i.intrec - threshold * txp).max(0.0);
        if let Some((table, last)) = &old.food {
            let foody = co_ag / (txp + depx);
            let base_amt = round_half_even(table.eval(foody) * aif, 0);
            foodcr = (txp + depx) * base_amt;
            if y >= 1978 && fs == Fs::Separate {
                foodcr = last * (txp + depx);
            }
        }
        statax_before_credits = statax;
    } else {
        // ---- 1987+: flat rate on federal taxable income ----
        let new = p.new.as_ref().expect("1987+ parameters");
        co_agi = f.agi;
        let mut t = f.taxable_income;
        if let Some((per_spouse, agi_limit)) = new.ui {
            let excl_spouse = i.sui.min(per_spouse);
            let excl_primary = (i.ui - i.sui).max(0.0).min(per_spouse);
            let excluded = if f.agi - excl_spouse - excl_primary < agi_limit { excl_spouse + excl_primary } else { 0.0 };
            t += excluded;
        }
        t = t.max(0.0);
        // 1992+: the state income tax deducted federally is added back when itemizing.
        if y >= 1992 && f.itemizes {
            t += salt.max(0.0).min(f.itemized_deduction);
        }
        if let Some(h) = &new.high_income {
            let limit = if is_joint { h.limit_joint } else { h.limit_other };
            let claimed = if f.itemizes {
                f.itemized_deduction
            } else if h.itemized_only {
                0.0
            } else {
                f.standard_deduction
            };
            t += if f.agi > h.agi_threshold { (claimed - limit).max(0.0) } else { 0.0 };
            let qbi_limit = if is_joint { h.qbi_limit_joint } else { h.qbi_limit_other };
            t += if f.agi > qbi_limit { f.qbi_deduction } else { 0.0 };
        }
        // The pension exclusion comes before the 2000-2002 marriage penalty subtraction.
        t = (t - pension_exclusion).max(0.0);
        if let Some(xmar) = new.marriage_penalty {
            if is_joint {
                let zbr = f.standard_deduction;
                let sub_not_itemized = (t - (xmar - zbr)).max(0.0);
                let sub_itemized = if f.itemized_deduction > zbr { (t - (xmar - f.itemized_deduction).max(0.0)).max(0.0) } else { t };
                t = if !f.itemizes { sub_not_itemized } else { sub_itemized };
            }
        }
        if let Some(amount) = new.aged_marriage {
            if is_joint {
                t = (t - amount * aged).max(0.0);
            }
        }
        taxinc = t;
        let mut statax = taxinc * new.flat_rate;
        // Minimum tax: a flat rate on the federal AMT base.
        let amt_income = f.agi - if f.itemizes { i.mortgage } else { 0.0 };
        let exemption = new.amt.exemption[fs as usize];
        let threshold = new.amt.threshold[fs as usize];
        let after = (exemption - new.amt.phaseout_rate * (amt_income - threshold).max(0.0)).max(0.0);
        let altax = new.amt.rate * (amt_income - after).max(0.0);
        statax += (altax - statax).max(0.0);
        statax_before_credits = statax;
    }
    let _ = taxinc;

    // ---- sales tax refund, on modified AGI including all Social Security ----
    let coagi = f.agi - f.taxable_social_security + i.gssi;
    let salesrefund = match &p.refund {
        Refund::Flat(amount) => amount * txp,
        Refund::Steps(b) => b.rate(coagi) * txp,
        Refund::Table(t) => t.eval(coagi) * txp,
    };

    // ---- child care credit: a share of the federal credit up to tax ----
    let child_fed = f.federal_chcr.min(f.regular_tax).max(0.0);
    let rate = match &p.share {
        Share::Table(t) => t.eval(f.agi.max(0.0)),
        Share::Flat(s) => *s,
    };
    let chcr = if let Some(offset) = p.dependent_offset {
        (rate * child_fed - offset * depx).max(0.0)
    } else if p.share_nonzero {
        rate * child_fed
    } else {
        0.0
    };

    // ---- earned income credit: a share of the federal credit ----
    let earncr = if let Some(minimum_age) = p.minimum_age {
        let older = i.page.max(i.sage);
        let too_young = f.num_children == 0.0 && older > 0.0 && older < minimum_age;
        let eitc_co = if too_young { 0.0 } else { f.eitc_before_age_test.max(0.0) };
        p.eitc_share * eitc_co
    } else if p.eitc_share != 0.0 {
        p.eitc_share * f.eitc.max(0.0)
    } else {
        0.0
    };

    // ---- refundable child tax credit for children under six ----
    let mut co_ctc = 0.0;
    if let Some(c) = &p.ctc {
        let ctc_rate = c.scales[fs as usize].rate(co_agi);
        co_ctc = if let Some(fed) = &c.fed {
            // DR 0104CN worksheet: the federal credit recomputed for children under six only.
            let maximum = fed.amount_per_child * i.dep6;
            let available_tax = (f.tax_before_credits - f.ccc - f.federal_elder).max(0.0);
            let nonrefundable = maximum.min(available_tax);
            let refundable = (fed.max_refundable * i.dep6)
                .min(maximum - nonrefundable)
                .min(fed.actc_rate * (f.earned_income.max(0.0) - fed.earned_floor).max(0.0))
                .max(0.0);
            (ctc_rate / fed.amount_per_child) * (nonrefundable + refundable)
        } else {
            ctc_rate * i.dep6
        };
        if let Some(fac) = &c.fac {
            let threshold = fac.thresholds[fs as usize];
            let increments = ((f.agi - threshold).max(0.0) / fac.increment).ceil();
            let remaining = 1.0 - (increments * fac.reduction_rate).min(1.0);
            let children = i.dep6 + fac.older_share * (i.dep17 - i.dep6).max(0.0);
            co_ctc += fac.amount * children * remaining;
        }
    }

    // The pre-1987 nonrefundable credits are limited to tax; the others are refundable.
    let statax = (statax_before_credits - foodcr - propcr - fuelcr).max(0.0);
    statax - salesrefund - chcr - earncr - co_ctc
}
