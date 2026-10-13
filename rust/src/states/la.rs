//! Louisiana income tax (port of `calculators.states.la`).

use std::path::Path;

use crate::common::Brackets;
use crate::federal::{Fed, Inp};
use crate::params::{self, get, load, num, Fs, YearParams};
use crate::states::law_year_is_exact;

struct ExemptIncomeTax {
    floor: f64,
    rate_low: f64,
    break_point: f64,
    base_high: f64,
    rate_high: f64,
}

/// Louisiana's rate schedule: flat, one table, or a table for each status.
enum Schedule {
    Flat(f64),
    One(Brackets),
    ByStatus { single: Brackets, married: Brackets, hoh: Brackets, first_single_rate: f64 },
}

enum HohReduction {
    /// No dependent reduction (before 1977's reduction applies).
    None,
    FirstAdditional { first: f64, additional: f64 },
    FirstThree { first_three: f64, fourth: f64, additional: f64 },
}

struct DependentReduction {
    amount: f64,
    credit_rate: f64,
    hoh: HohReduction,
}

struct ChildCare {
    tiers: [(f64, f64); 3],
    top_rate: f64,
    top_cap: f64,
}

struct FederalCredit {
    share: f64,
    cap: Option<f64>,
}

enum Combined {
    /// From 2025: a standard deduction by status.
    Standard { single: f64, hoh: f64, joint: f64 },
    /// Before: a combined deduction and exemption, possibly with an additional exemption credit.
    Combined { one: f64, two: f64, additional: Option<f64> },
}

struct Excess {
    share: f64,
    property_tax_addition: Option<f64>,
    /// Standard deduction by status from the federal income tax, 1987 on.
    federal_standard: Option<(f64, f64, f64)>,
}

pub struct LaParams {
    year: i64,
    federal_tax_deduction: bool,
    aged_pension_exemption: Option<f64>,
    exempt: ExemptIncomeTax,
    excess: Option<Excess>,
    combined: Combined,
    schedule: Schedule,
    dependent: Option<DependentReduction>,
    education_credit: Option<f64>,
    federal_credit: Option<FederalCredit>,
    child_care: Option<ChildCare>,
    eitc_share: Option<f64>,
}

impl LaParams {
    pub fn load(root: &Path, year: i64) -> Result<Self, String> {
        let doc = load(root, "states/la/income_tax.yaml")?;
        law_year_is_exact(root, &doc, year)?;
        let inc = load(root, "national/income_tax.yaml")?;
        let p = YearParams::new(&doc, year);
        let opt = |key: &str| -> Result<Option<f64>, String> { Ok(if p.in_effect(key)? { Some(p.f(key)?) } else { None }) };

        let e = get(&doc, "exempt_income_tax")?;
        let exempt = ExemptIncomeTax {
            floor: num(get(e, "floor")?)?,
            rate_low: num(get(e, "rate_low")?)?,
            break_point: num(get(e, "break")?)?,
            base_high: num(get(e, "base_high")?)?,
            rate_high: num(get(e, "rate_high")?)?,
        };
        let excess = match opt("excess_itemized_share")? {
            Some(share) => Some(Excess {
                share,
                property_tax_addition: opt("excess_itemized_property_tax_addition")?,
                federal_standard: if year >= 1987 {
                    let std = YearParams::new(get(&inc, "standard_deduction")?, year);
                    Some((std.num_at("single", &[])?, std.num_at("married_joint", &[])?, std.num_at("head_of_household", &[])?))
                } else {
                    None
                },
            }),
            None => None,
        };
        let combined = if p.in_effect("standard_deduction")? {
            let sd = p.value("standard_deduction", &[])?;
            Combined::Standard {
                single: num(get(sd, "single")?)?,
                hoh: num(get(sd, "head_of_household")?)?,
                joint: num(get(sd, "married_joint")?)?,
            }
        } else {
            let c = p.value("combined_deduction_exemption", &[])?;
            Combined::Combined { one: num(get(c, "one")?)?, two: num(get(c, "two")?)?, additional: opt("additional_exemption")? }
        };
        let dependent = if p.in_effect("dependent_tax_reduction_amount")? {
            let hoh = p.value("dependent_credit_hoh", &[])?;
            Some(DependentReduction {
                amount: p.f("dependent_tax_reduction_amount")?,
                credit_rate: num(get(&doc, "dependent_credit_rate")?)?,
                hoh: if hoh.get("first").is_some() {
                    HohReduction::FirstAdditional { first: num(get(hoh, "first")?)?, additional: num(get(hoh, "additional")?)? }
                } else {
                    HohReduction::FirstThree {
                        first_three: num(get(hoh, "first_three")?)?,
                        fourth: num(get(hoh, "fourth")?)?,
                        additional: num(get(hoh, "additional")?)?,
                    }
                },
            })
        } else {
            None
        };
        let schedule = if !p.in_effect("brackets")? {
            Schedule::Flat(p.f("flat_rate")?)
        } else {
            let b = p.value("brackets", &[])?;
            if b.is_sequence() {
                Schedule::One(Brackets::new(&params::brackets(b)?))
            } else {
                let single = params::brackets(get(b, "single")?)?;
                Schedule::ByStatus {
                    first_single_rate: single[0][1],
                    single: Brackets::new(&single),
                    married: Brackets::new(&params::brackets(get(b, "married_joint")?)?),
                    hoh: Brackets::new(&params::brackets(get(b, "head_of_household")?)?),
                }
            }
        };
        let child_care = match p.opt("child_care_credit")? {
            Some(c) => {
                let t = get(c, "tiers")?.as_sequence().ok_or("tiers must be a list")?;
                let tier = |k: usize| -> Result<(f64, f64), String> {
                    let row = t[k].as_sequence().ok_or("tier must be a list")?;
                    Ok((num(&row[0])?, num(&row[1])?))
                };
                Some(ChildCare {
                    tiers: [tier(0)?, tier(1)?, tier(2)?],
                    top_rate: num(get(c, "top_rate")?)?,
                    top_cap: num(get(c, "top_cap")?)?,
                })
            }
            None => None,
        };
        Ok(Self {
            year,
            federal_tax_deduction: p.value("federal_tax_deduction", &[])?.as_bool().ok_or("expected a boolean")?,
            aged_pension_exemption: opt("aged_pension_exemption")?,
            exempt,
            excess,
            combined,
            schedule,
            dependent,
            education_credit: opt("education_credit_per_dependent")?,
            federal_credit: match opt("federal_credit_share")? {
                Some(share) => Some(FederalCredit { share, cap: opt("federal_credit_cap")? }),
                None => None,
            },
            child_care,
            eitc_share: opt("eitc_federal_share")?,
        })
    }
}

pub fn tax(i: &Inp, f: &Fed, p: &LaParams) -> f64 {
    let y = p.year;
    let fs = i.fs();
    let sep = i.sepret();
    let la_txp = if fs == Fs::Joint || fs == Fs::Hoh { 2.0 } else { 1.0 };
    let is_single_or_sep = fs == Fs::Single || fs == Fs::Separate;
    let aged = i.aged_count();

    // ---- AGI ---- less exempt retirement income and Social Security, net of the federal tax attributable to them.
    let penexc = match p.aged_pension_exemption {
        Some(amount) => i.pensions.max(0.0).min(amount * aged),
        None => 0.0,
    };
    let ssi = if y >= 1985 { f.taxable_social_security } else { 0.0 };
    let subtr = penexc + ssi;
    let fedtax = f.fiitax + if y >= 2020 { f.cares } else { 0.0 };
    let e = &p.exempt;
    let ftadd1 = if subtr <= e.break_point {
        e.rate_low * (subtr - e.floor).max(0.0)
    } else {
        e.base_high + e.rate_high * (subtr - e.break_point)
    };
    let ftadd2 = fedtax * (1.0f64).min(subtr / f.agi);
    let mut ftadd = if f.agi > 0.0 && fedtax > 0.0 && subtr > 0.0 { ftadd1.min(ftadd2) } else { 0.0 };
    if !p.federal_tax_deduction {
        ftadd = 0.0;
    }
    let la_agi = f.agi - (subtr - ftadd);

    // ---- "excess federal itemized deductions" ----
    let deduc = match &p.excess {
        None => 0.0,
        Some(x) => {
            let (fedbas, itemized_local) = match x.federal_standard {
                Some((single, married, hoh)) => {
                    let basis = match fs {
                        Fs::Single => single,
                        Fs::Hoh => hoh,
                        _ => married / sep,
                    };
                    (basis, f.itemized_deduction)
                }
                None => (f.pre87.zbr, f.pre87.deduc),
            };
            let excess = (itemized_local - fedbas).max(0.0);
            let itemizing = if y <= 1986 { f.pre87.itemizes } else { f.itemizes };
            let real = match x.property_tax_addition {
                Some(cap) => {
                    let pded = if i.proptax > 0.0 { (cap * i.taxpayer_count()).min(i.proptax) } else { 0.0 };
                    x.share * (f.itemized_deduction - (fedbas + pded)).max(0.0)
                }
                None => x.share * excess,
            };
            if itemizing { real } else { 0.0 }
        }
    };

    // ---- federal income tax deduction ----
    // Through 1979 regular tax before credits; 1980-1986 federal income tax before the EITC;
    // from 1987 regular tax less nonrefundable credits, plus AMT.
    let la_fedtax = if !p.federal_tax_deduction {
        0.0
    } else if y <= 1979 {
        f.regular_tax.max(0.0)
    } else if y <= 1986 {
        (f.fiitax + f.pre87.earncr).max(0.0)
    } else {
        (f.regular_tax - f.nonrefundable_credits).max(0.0) + f.amt.max(0.0)
    };
    let mut la_taxinc = (la_agi - deduc - la_fedtax).max(0.0);

    // ---- combined standard deduction and exemption ----
    let mut exemption_credit = 0.0;
    match &p.combined {
        Combined::Standard { single, hoh, joint } => {
            let stxmp = if fs == Fs::Single || fs == Fs::Separate {
                *single
            } else if fs == Fs::Hoh {
                *hoh
            } else {
                *joint
            };
            la_taxinc = (la_taxinc - stxmp).max(0.0);
        }
        Combined::Combined { one, two, additional } => {
            let mut stxmp = if la_txp == 2.0 { *two } else { *one };
            match additional {
                Some(extra) => {
                    stxmp += extra * (i.depx + aged);
                    // Exemptions reduce tax at the lowest rate instead of income.
                    if let Schedule::ByStatus { first_single_rate, .. } = &p.schedule {
                        exemption_credit = first_single_rate * stxmp;
                    }
                }
                None => la_taxinc = (la_taxinc - stxmp).max(0.0),
            }
        }
    }

    // ---- tax ----
    let dep = p.dependent.as_ref();
    let xmpd = dep.map_or(0.0, |d| d.amount);
    // Dependents and taxpayers 65 or older each count toward the reduction.
    let dependents = if dep.is_some() { i.depx + aged } else { 0.0 };
    let mut statax = match &p.schedule {
        Schedule::Flat(rate) => rate * la_taxinc,
        Schedule::One(table) => table.tax(la_taxinc),
        Schedule::ByStatus { single, married, hoh, .. } => {
            let credit_rate = dep.map_or(0.0, |d| d.credit_rate);
            let tax_single = (single.tax(la_taxinc) - credit_rate * xmpd * dependents).max(0.0);
            let tax_married = (married.tax(la_taxinc) - credit_rate * xmpd * dependents).max(0.0);
            let tax_hoh = match dep {
                None => hoh.tax(la_taxinc),
                Some(d) => match &d.hoh {
                    HohReduction::FirstAdditional { first, additional } => (hoh.tax(la_taxinc)
                        - xmpd * (first * dependents.max(0.0).min(1.0) + additional * (dependents - 1.0).max(0.0)))
                    .max(0.0),
                    HohReduction::FirstThree { first_three, fourth, additional } => (hoh.tax(la_taxinc)
                        - xmpd
                            * (first_three * dependents.max(0.0).min(3.0)
                                + fourth * (dependents - 4.0).max(0.0).min(1.0)
                                + additional * (dependents - 5.0).max(0.0)))
                    .max(0.0),
                    HohReduction::None => hoh.tax(la_taxinc),
                },
            };
            let mut s = if is_single_or_sep {
                tax_single
            } else if fs == Fs::Joint {
                tax_married
            } else {
                tax_hoh
            };
            if matches!(&p.combined, Combined::Combined { additional: Some(_), .. }) {
                s = (s - exemption_credit).max(0.0);
            }
            s
        }
    };

    // ---- credits ----
    let edcr = match p.education_credit {
        Some(amount) => if i.depx >= 1.0 { amount * i.depx } else { 0.0 },
        None => 0.0,
    };
    let fedcr = match &p.federal_credit {
        Some(c) => {
            // From 2022 Louisiana has no credit for the federal elderly credit.
            let elder = if y >= 2022 { 0.0 } else { f.federal_elder };
            let credit = c.share * (f.federal_chcr + elder);
            match c.cap {
                Some(cap) => credit.max(0.0).min(cap),
                None => credit,
            }
        }
        None => 0.0,
    };
    let (mut chcr, mut chcref) = (0.0, 0.0);
    if let Some(c) = &p.child_care {
        let base = la_agi.max(0.0);
        let [(ceiling1, rate1), (ceiling2, rate2), (ceiling3, rate3)] = c.tiers;
        chcref = if base <= ceiling1 { rate1 * f.ccc } else { 0.0 };
        chcr = if base > ceiling1 && base <= ceiling2 {
            rate2 * f.ccc
        } else if base > ceiling2 && base <= ceiling3 {
            rate3 * f.ccc
        } else if base > ceiling3 {
            (c.top_rate * f.ccc).min(c.top_cap)
        } else {
            0.0
        };
    }
    let credit = fedcr + edcr + chcr;
    statax = (statax - credit).max(0.0);
    let earncr = p.eitc_share.map_or(0.0, |share| share * f.eitc);
    statax = statax - earncr - chcref;
    statax * 1.0
}
