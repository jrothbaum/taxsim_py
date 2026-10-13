//! North Carolina income tax (port of `calculators.states.nc`).

use std::path::Path;

use crate::common::Brackets;
use crate::federal::{Fed, Inp};
use crate::params::{self, get, load, num, status_direct, Fs, YearParams};
use crate::states::{
    dividend_adjustment, dividend_exclusion_addback, federal_exemption_count, law_year_is_exact, unemployment_total, Interp,
};

/// Everything the 1977-1988 calculation reads.
struct Early {
    brackets: Brackets,
    charity_limit: f64,
    child_care: Option<(Option<f64>, Option<f64>)>,
    std_cap: f64,
    std_rate: f64,
    xmp: f64,
    xmp2: f64,
    xmpc: f64,
    interest_exclusion: Option<f64>,
    low_income: Option<Vec<(f64, f64)>>,
}

struct Tables {
    single: Brackets,
    hoh: Brackets,
    married: Brackets,
}

struct Surtax {
    single: Vec<(f64, f64)>,
    hoh: Vec<(f64, f64)>,
    married: Vec<(f64, f64)>,
}

struct Std {
    single: f64,
    hoh: f64,
    married: f64,
    aged: Option<(f64, f64)>,
    dependent: Option<(f64, f64)>,
}

struct Worksheet {
    addition_a: f64,
    addition_b: f64,
    federal_amount: f64,
    phaseout: [f64; 4],
    partial: Option<([f64; 4], f64, f64)>,
}

enum Exemption {
    None,
    Flat(f64),
    Worksheet(Worksheet),
}

enum ChildDeduction {
    None,
    Table { joint: Interp, hoh: Interp, single: Interp },
    Steps { joint: Brackets, hoh: Brackets, single: Brackets },
}

enum ChildCareCredit {
    None,
    Flat { per_child: f64, rate: f64 },
    ByAgi { per_child: f64, married: Interp, single: Interp, hoh: Interp },
}

struct ChildCredit {
    amount: f64,
    limit: [f64; 4],
    low: Option<(f64, [f64; 4], [f64; 4])>,
}

pub struct NcParams {
    year: i64,
    retirement: Option<f64>,
    early: Option<Early>,
    std: Option<Std>,
    mortgage_cap: Option<f64>,
    exemption: Exemption,
    business_income: Option<f64>,
    charity_addback: Option<f64>,
    child_deduction: ChildDeduction,
    tables: Option<Tables>,
    flat_rate: f64,
    surtax: Option<Surtax>,
    child_care: ChildCareCredit,
    child_credit: Option<ChildCredit>,
    charity_credit: Option<(f64, f64)>,
    eitc_rate: Option<f64>,
    dividend_adjustment: f64,
}

fn pairs(v: &serde_yaml::Value) -> Result<Vec<(f64, f64)>, String> {
    Ok(params::brackets(v)?.into_iter().map(|r| (r[0], r[1])).collect())
}

impl NcParams {
    pub fn load(root: &Path, year: i64) -> Result<Self, String> {
        let doc = load(root, "states/nc/income_tax.yaml")?;
        law_year_is_exact(root, &doc, year)?;
        let p = YearParams::new(&doc, year);
        let opt = |key: &str| -> Result<Option<f64>, String> { Ok(if p.in_effect(key)? { Some(p.f(key)?) } else { None }) };
        let interp = |v: &serde_yaml::Value| -> Result<Interp, String> { Ok(Interp::new(&params::brackets(v)?)) };

        let early = if year <= 1988 {
            let cc = match p.opt("child_care_deduction")? {
                Some(c) => Some((c.get("cap").map(num).transpose()?, c.get("per_child").map(num).transpose()?)),
                None => None,
            };
            Some(Early {
                brackets: Brackets::new(&params::brackets(p.value("brackets", &[])?)?),
                charity_limit: p.f("charity_agi_limit")?,
                child_care: cc,
                std_cap: p.f("standard_deduction_cap")?,
                std_rate: p.f("standard_deduction_rate")?,
                xmp: p.f("exemption_taxpayer")?,
                xmp2: p.f("exemption_additional")?,
                xmpc: p.f("exemption_dependent")?,
                interest_exclusion: opt("interest_exclusion")?,
                low_income: match p.opt("low_income_credit")? {
                    Some(v) => Some(pairs(v)?),
                    None => None,
                },
            })
        } else {
            None
        };
        let std = if year >= 1989 {
            Some(Std {
                single: p.num_at("standard_deduction", &["single"])?,
                hoh: p.num_at("standard_deduction", &["head_of_household"])?,
                married: p.num_at("standard_deduction", &["married"])?,
                aged: match p.opt("standard_deduction_aged_addition")? {
                    Some(a) => Some((num(get(a, "single")?)?, num(get(a, "married")?)?)),
                    None => None,
                },
                dependent: if p.in_effect("dependent_standard_deduction_floor")? {
                    Some((p.f("dependent_standard_deduction_floor")?, p.f("dependent_standard_deduction_earned_addition")?))
                } else {
                    None
                },
            })
        } else {
            None
        };
        let exemption = if year < 1989 {
            Exemption::None
        } else if p.in_effect("exemption_amount")? {
            Exemption::Flat(p.f("exemption_amount")?)
        } else if p.in_effect("exemption_addition_a")? {
            let partial = if p.in_effect("exemption_partial_phaseout_start")? {
                Some((
                    status_direct(p.value("exemption_partial_phaseout_start", &[])?)?,
                    num(get(&doc, "exemption_partial_phaseout_width")?)?,
                    p.f("exemption_partial_share")?,
                ))
            } else {
                None
            };
            Exemption::Worksheet(Worksheet {
                addition_a: p.f("exemption_addition_a")?,
                addition_b: p.f("exemption_addition_b")?,
                federal_amount: p.f("federal_exemption_amount")?,
                phaseout: status_direct(get(&doc, "exemption_phaseout_agi")?)?,
                partial,
            })
        } else {
            Exemption::None
        };
        let child_deduction = if p.in_effect("child_deduction")? {
            let t = p.value("child_deduction", &[])?;
            ChildDeduction::Table { joint: interp(get(t, "married_joint")?)?, hoh: interp(get(t, "head_of_household")?)?, single: interp(get(t, "single")?)? }
        } else if p.in_effect("child_deduction_steps")? {
            let t = p.value("child_deduction_steps", &[])?;
            let mk = |key: &str| -> Result<Brackets, String> { Ok(Brackets::new(&params::brackets(get(t, key)?)?)) };
            ChildDeduction::Steps { joint: mk("married_joint")?, hoh: mk("head_of_household")?, single: mk("single")? }
        } else {
            ChildDeduction::None
        };
        let (tables, surtax) = if year >= 1989 && p.in_effect("brackets")? {
            let t = p.value("brackets", &[])?;
            let mk = |key: &str| -> Result<Brackets, String> { Ok(Brackets::new(&params::brackets(get(t, key)?)?)) };
            let surtax = match p.opt("surtax")? {
                Some(s) => Some(Surtax { single: pairs(get(s, "single")?)?, hoh: pairs(get(s, "head_of_household")?)?, married: pairs(get(s, "married")?)? }),
                None => None,
            };
            (Some(Tables { single: mk("single")?, hoh: mk("head_of_household")?, married: mk("married")? }), surtax)
        } else {
            (None, None)
        };
        let child_care = if p.in_effect("child_care_expense_per_child")? {
            let per_child = p.f("child_care_expense_per_child")?;
            if p.in_effect("child_care_rate")? {
                ChildCareCredit::Flat { per_child, rate: p.f("child_care_rate")? }
            } else {
                let r = p.value("child_care_rate_by_agi", &[])?;
                ChildCareCredit::ByAgi { per_child, married: interp(get(r, "married")?)?, single: interp(get(r, "single")?)?, hoh: interp(get(r, "head_of_household")?)? }
            }
        } else {
            ChildCareCredit::None
        };
        let child_credit = if p.in_effect("child_credit")? {
            let low = match p.opt("child_credit_low_income")? {
                Some(l) => Some((num(get(l, "amount")?)?, status_direct(get(l, "low_agi")?)?, status_direct(get(l, "high_agi")?)?)),
                None => None,
            };
            Some(ChildCredit { amount: p.f("child_credit")?, limit: status_direct(get(&doc, "child_credit_agi_limit")?)?, low })
        } else {
            None
        };
        Ok(Self {
            year,
            retirement: opt("retirement_deduction_per_taxpayer")?,
            early,
            std,
            mortgage_cap: opt("itemized_mortgage_property_tax_cap")?,
            exemption,
            business_income: opt("business_income_deduction")?,
            charity_addback: opt("charity_nonitemizer_addback")?,
            child_deduction,
            tables,
            flat_rate: if p.in_effect("flat_rate")? { p.f("flat_rate")? } else { 0.0 },
            surtax,
            child_care,
            child_credit,
            charity_credit: match opt("charity_credit_rate")? {
                Some(rate) => Some((rate, num(get(&doc, "charity_credit_floor_share")?)?)),
                None => None,
            },
            eitc_rate: opt("eitc_rate")?,
            dividend_adjustment: dividend_adjustment(root)?,
        })
    }
}

fn step_down(value: f64, rows: &[(f64, f64)]) -> f64 {
    for (upper, amount) in rows {
        if value <= *upper {
            return *amount;
        }
    }
    0.0
}

fn multiplier(taxinc: f64, rows: &[(f64, f64)], divisor: f64) -> f64 {
    let mut out = 1.0;
    for (lower, factor) in rows {
        if taxinc > lower / divisor {
            out = *factor;
        }
    }
    out
}

pub fn tax(i: &Inp, f: &Fed, p: &NcParams) -> f64 {
    let y = p.year;
    let fs = i.fs();
    let is_single = fs == Fs::Single;
    let is_joint = fs == Fs::Joint;
    let is_hoh = fs == Fs::Hoh;
    let sep = i.sepret();
    let txp = i.taxpayer_count();
    let aged = i.aged_count();
    let depx = i.depx;
    let fed_agi = f.agi;
    let ss_taxable = f.taxable_social_security;
    let nc_ui = unemployment_total(i);
    let salt = f.ded;

    // Retirement income deduction through 2013, per taxpayer (per taxpayer 65 or older when there is one).
    let pension_deduction = match p.retirement {
        Some(per_taxpayer) => {
            let pens = if i.pensions > 0.0 { if aged > 0.0 { aged } else { txp } } else { 0.0 };
            i.pensions.min(per_taxpayer * pens)
        }
        None => i.pensions.min(0.0),
    };

    // ---- North Carolina AGI (used through 1988) ----
    let mut agi = fed_agi;
    if y <= 1986 {
        let mut dividend_addback = dividend_exclusion_addback(y, i, f, p.dividend_adjustment);
        if y == 1981 {
            let dividends = i.dividends + p.dividend_adjustment;
            let divall = (dividends + i.intrec - f.pre87.divexc).max(0.0);
            dividend_addback = (dividends - divall).max(0.0);
        }
        let fullcg = i.stcg + i.ltcg;
        agi = agi + (fullcg - f.pre87.capgn).max(0.0) + dividend_addback + nc_ui - f.taxable_unemployment;
        if y >= 1983 {
            agi += f.pre87.twoded;
        }
    }
    if y >= 1984 {
        agi -= ss_taxable;
    }

    let mut statax;
    let ch;
    let mut early_split = (0.0, 0.0);
    if let Some(e) = &p.early {
        let look = |income: f64| e.brackets.tax(income.max(0.0));
        let ag = agi.max(0.0);
        let charity = i.charity_cash.min(e.charity_limit * ag);
        let mut xitded = charity + i.proptax + i.mortgage;
        ch = if depx >= 2.0 { 2.0 } else if depx > 0.0 { 1.0 } else { 0.0 };
        if let Some((cap, per_child)) = e.child_care {
            xitded += match cap {
                Some(cap) => i.childcare.min(cap),
                None => i.childcare.min(ch * per_child.unwrap_or(0.0)),
            };
        }
        // Joint returns: each spouse files separately on a share of AGI.
        let pw = i.pwages.max(0.0);
        let sw = i.swages.max(0.0);
        let mut yh = agi.min(pw.max(sw) + (agi - pw - sw) / 2.0);
        let mut yw = agi - yh;
        if let Some(amount) = e.interest_exclusion {
            let exclusion = amount.min(i.intrec / 2.0);
            yh = (yh - exclusion).max(0.0);
            yw = (yw - exclusion).max(0.0);
        }
        let joint_ch = (depx * 0.5 + 0.5).floor();
        let cw = depx - joint_ch;
        // Each spouse 65 or older gets an extra exemption, the husband's first.
        let oh = if aged >= 1.0 { 1.0 } else { 0.0 };
        let ow = if aged >= 2.0 { 1.0 } else { 0.0 };
        let eh = e.xmp2 + oh * e.xmp + joint_ch * e.xmpc;
        let ew = e.xmp + ow * e.xmp + cw * e.xmpc;
        let sth = (e.std_rate * yh).min(e.std_cap);
        let stw = (e.std_rate * yw).min(e.std_cap);
        let use_itemized = xitded > sth + stw;
        let dedh = if use_itemized { 0.5 * xitded } else { sth };
        let dedw = if use_itemized { 0.5 * xitded } else { stw };
        let taxyh = (yh - dedh - eh).max(0.0);
        let taxyw = (yw - dedw - ew).max(0.0);
        let mut staxh = look(taxyh);
        let mut staxw = look(taxyw);

        // Other returns.
        let agi_s = match e.interest_exclusion {
            Some(amount) => (agi - i.intrec.min(amount)).max(0.0),
            None => agi,
        };
        let stded = (e.std_rate * agi_s.max(0.0)).min(e.std_cap);
        let exemp = e.xmp + aged * e.xmp2 + depx * e.xmpc;
        let single_taxinc = (agi_s - stded.max(xitded) - exemp).max(0.0);
        let mut single_tax = look(single_taxinc);
        // Low income credit 1986-1988.
        if let Some(rows) = &e.low_income {
            single_tax = (single_tax - step_down(agi_s, rows)).max(0.0);
            staxh = (staxh - step_down(yh, rows)).max(0.0);
            staxw = (staxw - step_down(yw, rows)).max(0.0);
        }
        statax = if is_joint { staxh + staxw } else { single_tax };
        early_split = (staxh, staxw);
    } else {
        // ---- standard deduction ----
        let s = p.std.as_ref().expect("standard deduction parameters");
        let mut stded = if is_single { s.single } else if is_hoh { s.hoh } else { s.married / sep };
        if let Some((single_add, married_add)) = s.aged {
            stded += aged * if is_joint || fs == Fs::Separate { married_add } else { single_add };
        }
        if let Some((floor, addition)) = s.dependent {
            if i.is_dependent_filer() {
                stded = stded.min(floor.max(f.earned_income + addition));
            }
        }
        let fed_itemizes = f.itemizes;
        let fed_deduc = f.itemized_deduction;
        let fed_zbr = if fed_itemizes { 0.0 } else { f.standard_deduction };
        // ---- deduction difference from federal (through 2011) or deduction (2012+) ----
        let deduc = if y <= 2011 {
            if fed_itemizes { salt.min((fed_deduc - stded).max(0.0)) } else { (fed_zbr - stded).max(0.0) }
        } else if y <= 2013 {
            if fed_itemizes { fed_deduc - salt.min((fed_deduc - stded).max(0.0)) } else { stded }
        } else {
            let cap = p.mortgage_cap.unwrap_or(0.0);
            let xitded = cap.min(i.mortgage + i.proptax) + i.charity_cash;
            stded.max(xitded)
        };

        // ---- exemptions ----
        let exemps = federal_exemption_count(y, i);
        let exemp = match &p.exemption {
            Exemption::None => 0.0,
            Exemption::Flat(amount) => exemps * amount,
            Exemption::Worksheet(w) => {
                let amex = f.personal_exemptions;
                let phaseout = w.phaseout[fs as usize];
                let fedxf = exemps * w.federal_amount;
                let mut above = exemps * w.addition_b;
                if y != 2010 {
                    if fedxf > amex {
                        above = w.addition_b * amex / w.federal_amount;
                    }
                    if let Some((starts, width, share)) = &w.partial {
                        let fedphl = starts[fs as usize];
                        let partial = fed_agi > fedphl && fed_agi - fedphl <= width / sep;
                        if fedxf > amex && partial {
                            above = share * exemps * w.addition_b;
                        }
                    }
                }
                if fed_agi > phaseout { above } else { exemps * w.addition_a }
            }
        };
        let deduc_total = deduc + exemp;

        // ---- taxable income ----
        let fed_taxable = fed_agi - if fed_itemizes { fed_deduc } else { fed_zbr } - f.personal_exemptions;
        let mut taxinc;
        if y <= 2011 {
            taxinc = (fed_taxable + deduc_total - ss_taxable - pension_deduction).max(0.0);
        } else {
            let adjbus = match p.business_income {
                Some(amount) => (txp * amount).min((i.psemp + i.ssemp).max(0.0)),
                None => 0.0,
            };
            let mut nc_agi = fed_agi - ss_taxable - pension_deduction;
            if let Some(addback) = p.charity_addback {
                nc_agi += nc_ui - f.taxable_unemployment + if fed_itemizes { 0.0 } else { i.charity_cash.min(addback) };
            }
            taxinc = (nc_agi - adjbus - deduc_total).max(0.0);
        }
        let fed_agix = fed_agi.max(0.0);
        let per_child = match &p.child_deduction {
            ChildDeduction::Table { joint, hoh, single } => Some(if is_joint { joint.eval(fed_agix) } else if is_hoh { hoh.eval(fed_agix) } else { single.eval(fed_agix) }),
            ChildDeduction::Steps { joint, hoh, single } => Some(if is_joint { joint.rate(fed_agix) } else if is_hoh { hoh.rate(fed_agix) } else { single.rate(fed_agix) }),
            ChildDeduction::None => None,
        };
        if let Some(per_child) = per_child {
            taxinc = (taxinc - per_child * i.dep17).max(0.0);
        }

        // ---- tax ----
        statax = match &p.tables {
            None => p.flat_rate * taxinc,
            Some(t) => {
                let married_tax = t.married.tax(taxinc * sep) / sep;
                if is_single { t.single.tax(taxinc) } else if is_hoh { t.hoh.tax(taxinc) } else { married_tax }
            }
        };
        if let Some(s) = &p.surtax {
            statax *= if is_single {
                multiplier(taxinc, &s.single, 1.0)
            } else if is_hoh {
                multiplier(taxinc, &s.hoh, 1.0)
            } else {
                multiplier(taxinc, &s.married, sep)
            };
        }
        ch = if depx >= 2.0 { 2.0 } else if depx > 0.0 { 1.0 } else { 0.0 };
    }

    // ---- child care credit ----
    let chcr = match &p.child_care {
        ChildCareCredit::None => 0.0,
        ChildCareCredit::Flat { per_child, rate } => rate * (ch * per_child).min(i.childcare),
        ChildCareCredit::ByAgi { per_child, married, single, hoh } => {
            let eligible = (ch * per_child).min(i.childcare);
            eligible * if is_hoh { hoh.eval(fed_agi) } else if is_single { single.eval(fed_agi) } else { married.eval(fed_agi * sep) }
        }
    };
    statax = (statax - chcr).max(0.0);
    if y <= 1988 {
        // Joint returns: the credit comes off the larger spouse's tax.
        let (staxh, staxw) = early_split;
        let staxh2 = if staxh > staxw { (staxh - chcr).max(0.0) } else { staxh };
        let staxw2 = if staxw >= staxh { (staxw - chcr).max(0.0) } else { staxw };
        if is_joint {
            statax = staxh2 + staxw2;
        }
    }

    // ---- child tax credit 1995-2017 ----
    if let Some(c) = &p.child_credit {
        let children = if y >= 1998 { i.dep17 } else { depx };
        let chld = match &c.low {
            None => if fed_agi < c.limit[fs as usize] { children * c.amount } else { 0.0 },
            Some((low_amount, low, high)) => {
                if fed_agi <= low[fs as usize] {
                    children * low_amount
                } else if fed_agi <= high[fs as usize] {
                    children * c.amount
                } else {
                    0.0
                }
            }
        };
        statax = (statax - chld).max(0.0);
    }

    // ---- charitable credit for federal non-itemizers 1997-2013 ----
    if let Some((rate, floor_share)) = p.charity_credit {
        let contr = if f.itemizes { 0.0 } else { (i.charity_cash - floor_share * fed_agi).max(0.0) * rate };
        statax = (statax - contr).max(0.0);
    }

    // ---- earned income credit 2008-2013 (refundable) ----
    if let Some(rate) = p.eitc_rate {
        statax -= rate * f.eitc;
    }
    statax
}
