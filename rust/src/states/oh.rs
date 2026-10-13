//! Ohio individual income tax (port of `calculators.states.oh`).

use std::path::Path;

use crate::common::Brackets;
use crate::federal::{Fed, Inp};
use crate::params::{self, get, load, num, Fs, YearParams};
use crate::states::{law_year_is_exact, Interp, Tiers};

/// How the joint filing credit share is looked up.
enum JointShare {
    /// Through 1983: interpolated on taxable income.
    Interpolated(Interp),
    /// From 1984: the first tier above taxable income (plus business income).
    Tiered { tiers: Tiers, shares: Vec<f64> },
}

struct JointCredit {
    share: JointShare,
    cap: Option<f64>,
}

enum ChildCare {
    None,
    Capped { agi_limit: f64, rate: f64, cap: f64 },
    TwoRate { agi_limit: f64, low_agi: f64, low_rate: f64, rate: f64 },
}

struct Exemptions {
    amount: f64,
    dependent: Option<f64>,
    low_income: Option<(f64, f64)>,
    low_income_agi: f64,
    middle_income_agi: f64,
    high_income_limit: Option<f64>,
}

struct Retirement {
    table: Interp,
    cap: Option<f64>,
}

struct Business {
    share: f64,
    cap: f64,
    brackets: Brackets,
}

struct Eitc {
    rate: f64,
    income_limit: Option<f64>,
    tax_share_limit: f64,
}

pub struct OhParams {
    year: i64,
    table: Brackets,
    zero_bracket: Option<f64>,
    no_tax_threshold: Option<f64>,
    pension_exclusion: Option<f64>,
    business: Option<Business>,
    exemptions: Exemptions,
    child_care: ChildCare,
    senior_credit: f64,
    senior_income_limit: f64,
    retirement: Option<Retirement>,
    exemption_credit: Option<f64>,
    exemption_credit_income_limit: Option<f64>,
    exemption_deduction_alternative: Option<f64>,
    joint: JointCredit,
    joint_min_spouse_income: f64,
    joint_min_earned: Option<f64>,
    eitc: Option<Eitc>,
}

impl OhParams {
    pub fn load(root: &Path, year: i64) -> Result<Self, String> {
        let doc = load(root, "states/oh/income_tax.yaml")?;
        law_year_is_exact(root, &doc, year)?;
        let p = YearParams::new(&doc, year);
        let opt = |key: &str| -> Result<Option<f64>, String> { Ok(if p.in_effect(key)? { Some(p.f(key)?) } else { None }) };

        let inflation = p.f("bracket_inflation")?;
        let scaled: Vec<Vec<f64>> =
            params::brackets(p.value("brackets", &[])?)?.into_iter().map(|r| vec![r[0] * inflation, r[1]]).collect();

        let business = if p.in_effect("business_deduction_share")? {
            Some(Business {
                share: p.f("business_deduction_share")?,
                cap: p.f("business_deduction_cap")?,
                brackets: Brackets::new(&params::brackets(p.value("business_brackets", &[])?)?),
            })
        } else {
            None
        };
        let exemptions = Exemptions {
            amount: p.f("exemption")?,
            dependent: opt("dependent_exemption")?,
            low_income: if p.in_effect("exemption_low_income")? {
                Some((p.f("exemption_low_income")?, p.f("exemption_middle_income")?))
            } else {
                None
            },
            low_income_agi: num(get(&doc, "exemption_low_income_agi")?)?,
            middle_income_agi: num(get(&doc, "exemption_middle_income_agi")?)?,
            high_income_limit: opt("high_income_limit")?,
        };
        let child_care = match p.opt("child_care_credit")? {
            None => ChildCare::None,
            Some(c) if c.get("cap").is_some() => {
                ChildCare::Capped { agi_limit: num(get(c, "agi_limit")?)?, rate: num(get(c, "rate")?)?, cap: num(get(c, "cap")?)? }
            }
            Some(c) => ChildCare::TwoRate {
                agi_limit: num(get(c, "agi_limit")?)?,
                low_agi: num(get(c, "low_agi")?)?,
                low_rate: num(get(c, "low_rate")?)?,
                rate: num(get(c, "rate")?)?,
            },
        };
        let retirement = if p.in_effect("retirement_income_credit")? {
            Some(Retirement {
                table: Interp::new(&params::brackets(p.value("retirement_income_credit", &[])?)?),
                cap: opt("retirement_income_credit_cap")?,
            })
        } else {
            None
        };
        let share_rows = params::brackets(p.value("joint_credit_share", &[])?)?;
        let share = if year <= 1983 {
            JointShare::Interpolated(Interp::new(&share_rows))
        } else {
            JointShare::Tiered {
                tiers: Tiers::new(share_rows.iter().map(|r| r[0]).collect(), true),
                shares: share_rows.iter().map(|r| r[1]).collect(),
            }
        };
        let eitc = match opt("eitc_rate")? {
            Some(rate) => Some(Eitc {
                rate,
                income_limit: opt("eitc_income_limit")?,
                tax_share_limit: num(get(&doc, "eitc_tax_share_limit")?)?,
            }),
            None => None,
        };
        Ok(Self {
            year,
            table: Brackets::new(&scaled),
            zero_bracket: opt("zero_bracket")?,
            no_tax_threshold: opt("no_tax_threshold")?,
            pension_exclusion: opt("pension_exclusion")?,
            business,
            exemptions,
            child_care,
            senior_credit: p.f("senior_credit")?,
            senior_income_limit: if p.in_effect("retirement_income_credit")? { p.f("senior_credit_taxable_income_limit")? } else { 0.0 },
            retirement,
            exemption_credit: opt("exemption_credit")?,
            exemption_credit_income_limit: opt("exemption_credit_income_limit")?,
            exemption_deduction_alternative: opt("exemption_deduction_alternative")?,
            joint: JointCredit { share, cap: opt("joint_credit_cap")? },
            joint_min_spouse_income: num(get(&doc, "joint_credit_min_spouse_income")?)?,
            joint_min_earned: opt("joint_credit_min_earned")?,
            eitc,
        })
    }
}

/// Tax after the joint filing credit.
fn joint_credit(p: &OhParams, statax: f64, taxinc: f64, businc: f64, eligible: bool) -> f64 {
    let share = match &p.joint.share {
        JointShare::Interpolated(table) => table.eval(taxinc),
        JointShare::Tiered { tiers, shares } => shares[tiers.index(taxinc + businc)],
    };
    let mut jcred = (share * statax).max(0.0);
    if let Some(cap) = p.joint.cap {
        jcred = jcred.min(cap);
    }
    if eligible { (statax - jcred).max(0.0) } else { statax }
}

pub fn tax(i: &Inp, f: &Fed, p: &OhParams) -> f64 {
    let y = p.year;
    let is_joint = i.fs() == Fs::Joint;
    let sep = i.sepret();
    let taxpayers = i.taxpayer_count();
    let aged = i.aged_count();
    let depx = i.depx;
    let exemptions = taxpayers + depx;

    // ---- AGI and the business income deduction ----
    let mut agi = f.agi;
    if let Some(exclusion) = p.pension_exclusion {
        agi -= if aged > 0.0 { i.pensions.min(exclusion) } else { 0.0 };
    }
    if y >= 1984 {
        agi -= f.taxable_social_security;
    }
    let (mut businc, mut statb) = (0.0, 0.0);
    if let Some(b) = &p.business {
        let business = (i.psemp + i.ssemp).max(0.0);
        let busded = (b.share * business).min(b.cap / sep);
        agi -= busded;
        businc = (business - busded).max(0.0);
        statb = b.brackets.tax(businc);
    }

    // ---- exemptions ----
    let e = &p.exemptions;
    let mut exemp = match e.dependent {
        Some(dependent) => e.amount * taxpayers + dependent * depx,
        None => {
            let mut amount = e.amount;
            if let Some((low, middle)) = e.low_income {
                amount = if agi <= e.low_income_agi {
                    low
                } else if agi <= e.middle_income_agi {
                    middle
                } else {
                    amount
                };
                if let Some(limit) = e.high_income_limit {
                    if agi > limit {
                        amount = 0.0;
                    }
                }
            }
            amount * exemptions
        }
    };
    if i.is_dependent_filer() {
        exemp = 0.0;
    }
    let taxinc = (agi - businc - exemp).max(0.0);

    // ---- child care credit (share of the federal credit before its tax limit) ----
    let chcrbc = f.ccc_uncapped;
    let mut regcr = match &p.child_care {
        ChildCare::None => 0.0,
        ChildCare::Capped { agi_limit, rate, cap } => {
            if agi < *agi_limit { (rate * chcrbc).min(*cap) } else { 0.0 }
        }
        ChildCare::TwoRate { agi_limit, low_agi, low_rate, rate } => {
            if agi < *agi_limit {
                if agi < *low_agi { low_rate * chcrbc } else { rate * chcrbc }
            } else {
                0.0
            }
        }
    };
    // Senior citizen credit, and from 1983 a retirement income credit by pensions.
    let senior = match &p.retirement {
        None => p.senior_credit,
        Some(r) => {
            let mut retcr = r.table.eval(i.pensions);
            if let Some(cap) = r.cap {
                retcr = retcr.min(cap);
            }
            if taxinc <= p.senior_income_limit { p.senior_credit + retcr } else { 0.0 }
        }
    };
    regcr += if aged > 0.0 { senior } else { 0.0 };

    let mut excred = 0.0;
    if let Some(credit) = p.exemption_credit {
        excred = credit * exemptions;
        if let Some(limit) = p.exemption_credit_income_limit {
            excred = if taxinc < limit { excred } else { 0.0 };
        }
    }

    // ---- joint filing credit eligibility: each spouse's income share ----
    let wages = i.pwages.max(0.0) + i.swages.max(0.0);
    let adjust = (if y >= 1987 {
        f.se_adjustment
    } else if y >= 1982 {
        f.pre87.twoded
    } else {
        0.0
    }) - i.nonprop;
    let xlind = wages + i.psemp + i.ssemp + i.pensions + f.taxable_unemployment - adjust;
    let hagi = i.pwages.max(0.0) + 0.5 * (xlind - wages);
    let wagi = xlind - hagi;
    let mut joint_eligible = hagi >= p.joint_min_spouse_income && wagi >= p.joint_min_spouse_income;
    if let Some(minimum) = p.joint_min_earned {
        joint_eligible = joint_eligible && f.earned_income >= minimum;
    }
    if y <= 1988 {
        // TAXSIM does not test the filing status through 1988; the statutory rule limits it to joint returns.
        joint_eligible = joint_eligible && is_joint;
    }

    let mut statax;
    if y <= 1982 {
        statax = (p.table.tax(taxinc) - regcr).max(0.0);
        statax = joint_credit(p, statax, taxinc, businc, joint_eligible);
    } else if let Some(alternative) = p.exemption_deduction_alternative {
        // Method 1 (a deduction per exemption, then the joint credit) unless method 2
        // (a credit per exemption) is lower; the exemption credit applies once.
        let dedy = alternative * exemptions;
        let mut stax1 = p.table.tax((taxinc - dedy).max(0.0)) - regcr;
        let mut stax2 = p.table.tax(taxinc) - regcr - p.exemption_credit.unwrap_or(0.0) * exemptions;
        if y != 1983 {
            stax1 = stax1.max(0.0);
            stax2 = stax2.max(0.0);
        }
        let method1 = joint_credit(p, stax1, taxinc, businc, joint_eligible);
        let method2 = joint_credit(p, stax2, taxinc, businc, joint_eligible);
        let final1 = (method1 - excred).max(0.0);
        statax = final1.min(method2);
    } else {
        statax = p.table.tax(taxinc);
        if let Some(zero) = p.zero_bracket {
            if taxinc <= zero {
                statax = 0.0;
            }
        }
        statax += statb;
        statax = (statax - regcr).max(0.0);
        statax = (statax - excred).max(0.0);
        let mut earncr = 0.0;
        if let Some(c) = &p.eitc {
            earncr = c.rate * f.eitc;
            if let Some(limit) = c.income_limit {
                if taxinc + businc > limit {
                    earncr = earncr.min(c.tax_share_limit * statax);
                }
            }
        }
        let mut joint_ok = is_joint && joint_eligible;
        if let Some(limit) = p.exemptions.high_income_limit {
            joint_ok = joint_ok && (taxinc + businc) <= limit;
        }
        statax = joint_credit(p, statax, taxinc, businc, joint_ok);
        statax = (statax - earncr).max(0.0);
    }

    if let Some(threshold) = p.no_tax_threshold {
        if taxinc <= threshold {
            statax = 0.0;
        }
    }
    statax * 1.0
}
