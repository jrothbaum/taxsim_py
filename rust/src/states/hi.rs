//! Hawaii income tax (port of `calculators.states.hi`).

use std::path::Path;

use crate::common::Brackets;
use crate::federal::{Fed, Inp};
use crate::params::{self, get, load, num, Fs, YearParams};
use crate::states::{law_year_is_exact, unemployment_total, Interp};

struct Phaseout {
    base: f64,
    step: f64,
    multiplier_hoh: f64,
    multiplier_married: f64,
    share_per_step: f64,
}

struct ItemizedCap {
    threshold_hoh: f64,
    threshold_per_taxpayer: f64,
    amount_hoh: f64,
    amount_per_taxpayer: f64,
}

struct SaltLimit {
    hoh: f64,
    single_or_separate: f64,
    joint: f64,
}

struct Limitation {
    threshold: f64,
    reduction_share: f64,
}

struct GeneralCredit {
    max: f64,
    slope: f64,
    limit: f64,
}

enum General {
    Phaseout { hoh: GeneralCredit, joint: GeneralCredit, other: GeneralCredit },
    PerExemption(f64),
}

enum Excise {
    None,
    Table(Interp),
    FoodExcise { single: Interp, other: Interp },
}

struct ChildCare {
    min_pct: f64,
    max_pct: f64,
    start: f64,
    step: f64,
}

pub struct HiParams {
    year: i64,
    single: Brackets,
    hoh: Brackets,
    capital_gains_floor_per_taxpayer: f64,
    capital_gains_floor_hoh: f64,
    capital_gains_rate: f64,
    personal_exemption: f64,
    phaseout: Option<Phaseout>,
    standard: [f64; 4],
    standard_agi_share: Option<f64>,
    salt_limit: Option<SaltLimit>,
    limitation: Option<Limitation>,
    phaseout_rate: f64,
    cap_rate: f64,
    itemized_cap: Option<ItemizedCap>,
    child_cap_per_dependent: f64,
    child_max_dependents: f64,
    child_rate: ChildCare,
    general: General,
    renter_per_exemption: f64,
    renter_min_rent: f64,
    renter_agi_limit: f64,
    excise: Excise,
    food_credit: Option<f64>,
    low_income: Option<([f64; 3], [f64; 3])>,
    eitc: Option<(f64, bool)>,
}

impl HiParams {
    pub fn load(root: &Path, year: i64) -> Result<Self, String> {
        let doc = load(root, "states/hi/income_tax.yaml")?;
        law_year_is_exact(root, &doc, year)?;
        let p = YearParams::new(&doc, year);
        let opt = |key: &str| -> Result<Option<f64>, String> { Ok(if p.in_effect(key)? { Some(p.f(key)?) } else { None }) };
        let b = p.value("brackets", &[])?;
        let phaseout = match p.opt("personal_exemption_phaseout")? {
            Some(ph) => Some(Phaseout {
                base: num(get(ph, "base")?)?,
                step: num(get(ph, "step")?)?,
                multiplier_hoh: num(get(ph, "multiplier_hoh")?)?,
                multiplier_married: num(get(ph, "multiplier_married")?)?,
                share_per_step: num(get(ph, "share_per_step")?)?,
            }),
            None => None,
        };
        let standard = [
            p.num_at("standard_deduction", &["single"])?,
            p.num_at("standard_deduction", &["married_joint"])?,
            p.num_at("standard_deduction", &["married_separate"])?,
            p.num_at("standard_deduction", &["head_of_household"])?,
        ];
        let salt_limit = match p.opt("state_tax_deduction_agi_limit")? {
            Some(l) => Some(SaltLimit {
                hoh: num(get(l, "head_of_household")?)?,
                single_or_separate: num(get(l, "single_or_separate")?)?,
                joint: num(get(l, "married_joint")?)?,
            }),
            None => None,
        };
        let limitation = match p.opt("itemized_limitation")? {
            Some(l) => Some(Limitation { threshold: num(get(l, "threshold")?)?, reduction_share: num(get(l, "reduction_share")?)? }),
            None => None,
        };
        let itemized_cap = match p.opt("itemized_cap")? {
            Some(c) => Some(ItemizedCap {
                threshold_hoh: num(get(c, "threshold_hoh")?)?,
                threshold_per_taxpayer: num(get(c, "threshold_per_taxpayer")?)?,
                amount_hoh: num(get(c, "amount_hoh")?)?,
                amount_per_taxpayer: num(get(c, "amount_per_taxpayer")?)?,
            }),
            None => None,
        };
        let cr = p.value("child_care_credit_rate", &[])?;
        let general = match p.opt("general_credit_phaseout")? {
            Some(g) => {
                let read = |key: &str| -> Result<GeneralCredit, String> {
                    let c = get(g, key)?;
                    Ok(GeneralCredit { max: num(get(c, "max")?)?, slope: num(get(c, "slope")?)?, limit: num(get(c, "limit")?)? })
                };
                General::Phaseout { hoh: read("head_of_household")?, joint: read("married_joint")?, other: read("other")? }
            }
            None => General::PerExemption(p.f("general_credit_per_exemption")?),
        };
        let excise = if p.in_effect("excise_credit")? {
            Excise::Table(Interp::new(&params::brackets(p.value("excise_credit", &[])?)?))
        } else if p.in_effect("food_excise_credit")? {
            let t = p.value("food_excise_credit", &[])?;
            Excise::FoodExcise {
                single: Interp::new(&params::brackets(get(t, "single")?)?),
                other: Interp::new(&params::brackets(get(t, "other")?)?),
            }
        } else {
            Excise::None
        };
        let low_income = match p.opt("low_income_credit")? {
            Some(l) => {
                let three = |v: &serde_yaml::Value| -> Result<[f64; 3], String> {
                    let s = v.as_sequence().ok_or("expected three values")?;
                    Ok([num(&s[0])?, num(&s[1])?, num(&s[2])?])
                };
                Some((three(get(l, "amounts")?)?, three(get(l, "ceilings")?)?))
            }
            None => None,
        };
        let eitc = if p.in_effect("eitc_federal_share")? {
            Some((p.f("eitc_federal_share")?, p.value("eitc_refundable", &[])?.as_bool().ok_or("expected a boolean")?))
        } else {
            None
        };
        Ok(Self {
            year,
            single: Brackets::new(&params::brackets(get(b, "single")?)?),
            hoh: Brackets::new(&params::brackets(get(b, "head_of_household")?)?),
            capital_gains_floor_per_taxpayer: num(get(&doc, "capital_gains_floor_per_taxpayer")?)?,
            capital_gains_floor_hoh: num(get(&doc, "capital_gains_floor_hoh")?)?,
            capital_gains_rate: num(get(&doc, "capital_gains_rate")?)?,
            personal_exemption: p.f("personal_exemption")?,
            phaseout,
            standard,
            standard_agi_share: opt("standard_deduction_agi_share")?,
            salt_limit,
            limitation,
            phaseout_rate: num(get(&doc, "itemized_phaseout_rate")?)?,
            cap_rate: num(get(&doc, "itemized_phaseout_cap_rate")?)?,
            itemized_cap,
            child_cap_per_dependent: p.f("child_care_credit_cap_per_dependent")?,
            child_max_dependents: num(get(&doc, "child_care_credit_max_dependents")?)?,
            child_rate: ChildCare {
                min_pct: num(get(cr, "min_pct")?)?,
                max_pct: num(get(cr, "max_pct")?)?,
                start: num(get(cr, "start")?)?,
                step: num(get(cr, "step")?)?,
            },
            general,
            renter_per_exemption: p.f("renter_credit_per_exemption")?,
            renter_min_rent: num(get(&doc, "renter_credit_min_rent")?)?,
            renter_agi_limit: p.f("renter_credit_agi_limit")?,
            excise,
            food_credit: opt("food_credit")?,
            low_income,
            eitc,
        })
    }
}

pub fn tax(i: &Inp, f: &Fed, p: &HiParams) -> f64 {
    let y = p.year;
    let fs = i.fs();
    let is_hoh = fs == Fs::Hoh;
    let is_joint = fs == Fs::Joint;
    let sep = i.sepret();
    let txp = i.taxpayer_count();
    let aged = i.aged_count();

    // ---- AGI ---- Social Security benefits are exempt.
    let mut hi_agi = f.agi - f.taxable_social_security;
    if y == 2020 || y == 2021 {
        hi_agi = hi_agi + unemployment_total(i) - f.taxable_unemployment;
    }
    // TAXSIM's pension subtraction reads an input slot that is never set; Hawaii exempts pensions.
    hi_agi -= i.pensions.max(0.0);

    // ---- exemptions ----
    let exema = (txp + i.depx + aged) * p.personal_exemption;
    let mut hi_exemp = exema;
    if let Some(ph) = &p.phaseout {
        let phex = if fs == Fs::Single {
            ph.base
        } else if is_hoh {
            ph.multiplier_hoh * ph.base
        } else {
            ph.multiplier_married * ph.base / sep
        };
        let ln6 = (1.0 + (hi_agi - phex) / (ph.step / sep)).floor();
        let phased = exema - exema * ph.share_per_step * ln6;
        if hi_agi > phex {
            hi_exemp = phased;
        }
    }
    if i.is_dependent_filer() {
        hi_exemp = 0.0;
    }

    // ---- standard deduction ----
    let status_std = p.standard[fs as usize];
    let mut hi_stded = status_std;
    if let Some(share) = p.standard_agi_share {
        hi_stded = (share * hi_agi).max(0.0).min(hi_stded);
    }

    // ---- itemized deduction ---- Before 1987 rebuilt from the inputs.
    let raw_itemized = if y <= 1986 { i.proptax + i.otheritem + i.mortgage + f.ded } else { f.salt_capped + i.mortgage };
    let mut xitded = raw_itemized;
    if let Some(l) = &p.salt_limit {
        let salt_thr = if is_hoh {
            l.hoh
        } else if fs == Fs::Single || sep == 2.0 {
            l.single_or_separate
        } else {
            l.joint
        };
        if f.agi > salt_thr {
            xitded = (xitded - f.ded).max(0.0);
        }
    }
    if let Some(l) = &p.limitation {
        let threshold = l.threshold / sep;
        let reduce = if hi_agi > threshold { (p.cap_rate * xitded).min(p.phaseout_rate * (hi_agi - threshold)) } else { 0.0 };
        xitded -= reduce * l.reduction_share;
    }
    if let Some(c) = &p.itemized_cap {
        let phas92 = if is_hoh { c.threshold_hoh } else { c.threshold_per_taxpayer * txp };
        let over_thr = f.agi > phas92;
        let cap_other = c.amount_per_taxpayer * txp;
        if over_thr && is_hoh {
            xitded = c.amount_hoh.min(xitded);
        } else if over_thr {
            xitded = cap_other.min(xitded);
        }
    }
    if (1982..=1986).contains(&y) {
        xitded = (raw_itemized - status_std).max(0.0);
        hi_stded = (hi_stded - status_std).max(0.0);
    }
    if y == 1999 && !f.forced_itemize {
        xitded = 0.0;
    }
    let deduc = xitded.max(hi_stded);
    let taxinc = (hi_agi - hi_exemp - deduc).max(0.0);

    // ---- tax ---- Heads of household have their own schedule; joint returns use the single schedule on half their income, doubled.
    let stat = |income: f64| -> f64 {
        let taxy = if is_joint { income / 2.0 } else { income };
        let mut stat_single = p.single.tax(taxy);
        if is_joint {
            stat_single *= 2.0;
        }
        if is_hoh { p.hoh.tax(income) } else { stat_single }
    };
    let mut statax = stat(taxinc);

    // ---- capital gains alternative tax ----
    let capgn = if y <= 1986 { f.pre87.capgn } else { f.capgn };
    let has_gain = capgn.max(0.0) > 0.0;
    let brack = if is_hoh { p.capital_gains_floor_hoh } else { p.capital_gains_floor_per_taxpayer * txp };
    let taxyng = brack.max((taxinc - capgn.max(0.0)).max(0.0));
    let taxycg = (taxinc - taxyng).max(0.0);
    let statng = stat(taxyng);
    let statcg = taxycg * p.capital_gains_rate;
    if has_gain {
        statax = statax.min(statng + statcg);
    }

    // ---- credits ----
    let mut child = i.childcare.min(p.child_cap_per_dependent * i.depx.min(p.child_max_dependents));
    if is_joint {
        child = child.min(i.pwages).min(i.swages);
    }
    let c = &p.child_rate;
    let chr_rate = 0.01 * c.min_pct.max(c.max_pct - ((hi_agi - c.start) / c.step).max(0.0));
    let hi_chcr = chr_rate * child;

    let general_credit = |g: &GeneralCredit| if f.agi < g.limit { g.max - g.slope * f.agi / g.limit } else { 0.0 };
    let hi_gencr = match &p.general {
        General::Phaseout { hoh, joint, other } => {
            if is_hoh {
                general_credit(hoh)
            } else if is_joint {
                general_credit(joint)
            } else {
                general_credit(other)
            }
        }
        General::PerExemption(per_exemption) => per_exemption * (txp + i.depx),
    };

    let aged_factor = aged.max(1.0);
    let rcred = if i.rentpaid >= p.renter_min_rent && hi_agi < p.renter_agi_limit {
        p.renter_per_exemption * (txp + i.depx + aged)
    } else {
        0.0
    };
    let hi_rcred = rcred * aged_factor;

    let mut hi_exc = match &p.excise {
        Excise::Table(t) => t.eval(hi_agi) * txp * aged_factor,
        Excise::FoodExcise { single, other } => {
            let fedagi = f.agi.max(0.0);
            let non_single = (i.depx + txp) * other.eval(fedagi);
            let single_amount = (i.depx + txp) * single.eval(fedagi);
            if fs == Fs::Single { single_amount } else { non_single }
        }
        Excise::None => 0.0,
    };
    let mut hi_foodcr = p.food_credit.map_or(0.0, |amount| amount * (txp + i.depx));
    let hi_lowcr = match &p.low_income {
        Some((amounts, ceilings)) => {
            let units = txp + i.depx;
            if hi_agi < ceilings[0] {
                units * amounts[0]
            } else if hi_agi < ceilings[1] {
                units * amounts[1]
            } else if hi_agi <= ceilings[2] {
                units * amounts[2]
            } else {
                0.0
            }
        }
        None => 0.0,
    };
    // Dependent filers get no excise or food credit.
    if i.is_dependent_filer() {
        hi_exc = 0.0;
        hi_foodcr = 0.0;
    }
    let hi_credit = hi_gencr + hi_rcred + hi_exc + hi_foodcr + hi_chcr + hi_lowcr;
    statax -= hi_credit;

    // ---- earned income credit ----
    let hi_earncr = match p.eitc {
        Some((share, refundable)) => {
            let raw = share * f.eitc.max(0.0);
            if refundable { raw } else { raw.min(statax.max(0.0)) }
        }
        None => 0.0,
    };
    statax -= hi_earncr;
    statax * 1.0
}
