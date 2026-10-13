//! Kentucky income tax (port of `calculators.states.ky`).

use std::path::Path;

use crate::common::Brackets;
use crate::federal::{Fed, Inp};
use crate::params::{self, get, load, num, Fs, YearParams};
use crate::states::{dividend_adjustment, higher_earner_share, law_year_is_exact, unemployment_total, Interp};

struct InterestExclusion {
    rate: f64,
    cap_per_taxpayer: f64,
}

struct ChildCareDeduction {
    caps: [f64; 3],
    phaseout_start: f64,
    income_ceiling: f64,
}

struct Retirement {
    share: f64,
    cap: f64,
}

struct ItemizedLimit {
    cap_rate: f64,
    rate: f64,
    reduction_share: f64,
}

struct Poverty {
    first_person: f64,
    additional_person: f64,
    steps: Vec<(f64, f64)>,
}

enum FamilyCredit {
    None,
    /// 2005-2021: interpolated tables by family size, indexed by year.
    Indexed { tables: [Interp; 4], index: [f64; 4] },
    /// 2022 on: income as a multiple of the poverty guideline, by step.
    Poverty(Poverty),
}

enum Schedule {
    Brackets(Brackets),
    Flat(f64),
}

pub struct KyParams {
    year: i64,
    schedule: Schedule,
    interest_exclusion: Option<InterestExclusion>,
    dividend_exclusion: Option<f64>,
    two_earner: Option<(f64, f64)>,
    retirement: Option<Retirement>,
    capital_gains_exclusion: Option<f64>,
    standard_deduction: f64,
    standard_per_taxpayer: bool,
    threshold_base: f64,
    threshold_index: Option<f64>,
    itemized_limit: Option<ItemizedLimit>,
    child_care_deduction: Option<ChildCareDeduction>,
    personal_per_person: f64,
    personal_per_aged: f64,
    child_care_credit_share: f64,
    low_income: Option<Vec<(f64, f64)>>,
    family: FamilyCredit,
    dividend_adjustment: f64,
}

impl KyParams {
    pub fn load(root: &Path, year: i64) -> Result<Self, String> {
        let doc = load(root, "states/ky/income_tax.yaml")?;
        law_year_is_exact(root, &doc, year)?;
        let pre87 = load(root, "national/pre1987.yaml")?;
        let p = YearParams::new(&doc, year);
        let opt = |key: &str| -> Result<Option<f64>, String> { Ok(if p.in_effect(key)? { Some(p.f(key)?) } else { None }) };

        let schedule = if p.in_effect("brackets")? {
            Schedule::Brackets(Brackets::new(&params::brackets(p.value("brackets", &[])?)?))
        } else {
            Schedule::Flat(p.f("flat_rate")?)
        };
        let two_earner = if (1982..=1986).contains(&year) {
            let q = YearParams::new(&pre87, year);
            Some((q.f("two_earner_deduction_rate")?, q.f("two_earner_deduction_cap")?))
        } else {
            None
        };
        let table_of = |key: &str| -> Result<Vec<Vec<f64>>, String> { params::brackets(get(get(&doc, "family_size_credit_table")?, key)?) };
        let family = if year >= 2005 && year < 1990 || year < 2005 {
            FamilyCredit::None
        } else if p.in_effect("family_size_credit_poverty_guideline")? {
            let g = p.value("family_size_credit_poverty_guideline", &[])?;
            FamilyCredit::Poverty(Poverty {
                first_person: num(get(g, "first_person")?)?,
                additional_person: num(get(g, "additional_person")?)?,
                steps: params::brackets(get(&doc, "family_size_credit_poverty_share")?)?.into_iter().map(|r| (r[0], r[1])).collect(),
            })
        } else {
            let index = p.value("family_size_credit_index", &[])?.as_sequence().ok_or("index must be a list")?;
            FamilyCredit::Indexed {
                tables: [
                    Interp::new(&table_of("one")?),
                    Interp::new(&table_of("two")?),
                    Interp::new(&table_of("three")?),
                    Interp::new(&table_of("four")?),
                ],
                index: [num(&index[0])?, num(&index[1])?, num(&index[2])?, num(&index[3])?],
            }
        };
        let (personal_per_person, personal_per_aged) = if year >= 1990 {
            let pc = p.value("personal_credit", &[])?;
            (num(get(pc, "per_person")?)?, num(get(pc, "per_aged")?)?)
        } else {
            (0.0, 0.0)
        };
        let itemized_limit = if year <= 2017 {
            Some(ItemizedLimit {
                cap_rate: num(get(&doc, "itemized_phaseout_cap_rate")?)?,
                rate: num(get(&doc, "itemized_phaseout_rate")?)?,
                reduction_share: if year >= 1991 { p.f("itemized_limitation_reduction_share")? } else { 1.0 },
            })
        } else {
            None
        };
        Ok(Self {
            year,
            schedule,
            interest_exclusion: if year <= 1989 {
                match p.opt("interest_exclusion")? {
                    Some(e) => Some(InterestExclusion { rate: num(get(e, "rate")?)?, cap_per_taxpayer: num(get(e, "cap_per_taxpayer")?)? }),
                    None => None,
                }
            } else {
                None
            },
            dividend_exclusion: opt("dividend_exclusion_per_taxpayer")?,
            two_earner,
            retirement: match opt("retirement_exclusion_share")? {
                Some(share) => Some(Retirement { share, cap: p.f("retirement_exclusion_cap")? }),
                None => None,
            },
            capital_gains_exclusion: opt("capital_gains_exclusion")?,
            standard_deduction: p.f("standard_deduction")?,
            standard_per_taxpayer: p.value("standard_deduction_per_taxpayer", &[])?.as_bool().ok_or("expected a boolean")?,
            threshold_base: num(get(&doc, "itemized_limitation_threshold")?)?,
            threshold_index: if (1992..=2017).contains(&year) { Some(p.f("itemized_limitation_index")?) } else { None },
            itemized_limit,
            child_care_deduction: match p.opt("child_care_deduction")? {
                Some(c) => {
                    let caps = get(c, "caps")?.as_sequence().ok_or("caps must be a list")?;
                    Some(ChildCareDeduction {
                        caps: [num(&caps[0])?, num(&caps[1])?, num(&caps[2])?],
                        phaseout_start: num(get(c, "phaseout_start")?)?,
                        income_ceiling: num(get(c, "income_ceiling")?)?,
                    })
                }
                None => None,
            },
            personal_per_person,
            personal_per_aged,
            child_care_credit_share: if year >= 1990 { p.f("child_care_credit_federal_share")? } else { 0.0 },
            low_income: if year >= 1990 && p.in_effect("low_income_credit")? {
                Some(params::brackets(p.value("low_income_credit", &[])?)?.into_iter().map(|r| (r[0], r[1])).collect())
            } else {
                None
            },
            family: if year >= 1990 && !p.in_effect("low_income_credit")? { family } else { FamilyCredit::None },
            dividend_adjustment: dividend_adjustment(root)?,
        })
    }
}

pub fn tax(i: &Inp, f: &Fed, p: &KyParams) -> f64 {
    let y = p.year;
    let fs = i.fs();
    let sep = i.sepret();
    let taxpayers = i.taxpayer_count();
    let aged = i.aged_count();
    let is_joint = fs == Fs::Joint;
    let setax = f.payroll.setax;
    let ky_ccc = f.ccc_uncapped;

    let phas92 = match p.threshold_index {
        Some(index) => p.threshold_base * index / sep,
        None => p.threshold_base / sep,
    };

    // Federal income tax, which can be negative, less self-employment tax and AMT, floored at 0 only at the end.
    let ky_fedtax = (f.fiitax - setax - f.amt.max(0.0)).max(0.0);

    // ---- AGI ----
    let mut agi;
    if y <= 1989 {
        agi = f.agi - ky_fedtax;
        if let Some(e) = &p.interest_exclusion {
            agi -= e.rate * i.intrec.max(0.0).min(e.cap_per_taxpayer * taxpayers);
        }
    } else {
        // 1990 adds back federal income tax even when negative.
        let subtra = if y != 1990 { 0.0 } else { f.fiitax };
        agi = f.agi - subtra;
    }
    let ui_total = unemployment_total(i);
    if y == 2009 || y == 2020 {
        agi = agi + ui_total - f.taxable_unemployment;
    }
    if y >= 1984 {
        agi -= f.taxable_social_security;
    }
    if let Some(per_taxpayer) = p.dividend_exclusion {
        agi -= (i.dividends + p.dividend_adjustment).max(0.0).min(per_taxpayer * taxpayers);
    }
    if let Some((rate, cap)) = p.two_earner {
        let lesser_wage = i.pwages.min(i.swages).max(0.0);
        agi += if is_joint { (rate * lesser_wage).max(0.0).min(cap) } else { 0.0 };
    }
    if let Some(r) = &p.retirement {
        // The statutory cap is per taxpayer, with joint pensions split evenly.
        agi -= (r.share * i.pensions).min(r.cap * taxpayers);
    }
    if let Some(rate) = p.capital_gains_exclusion {
        if i.stcg + i.ltcg > 0.0 {
            agi -= rate * i.ltcg.max(0.0);
        }
    }
    let ky_agi = agi;

    // ---- standard deduction ----
    let stded = p.standard_deduction * if p.standard_per_taxpayer { taxpayers } else { 1.0 };

    // ---- itemized deduction ----
    let salt = f.ded;
    let mut xitded;
    if y <= 2017 {
        let salt_plus_mortgage = if y <= 1986 {
            i.proptax + i.otheritem + i.mortgage + salt
        } else {
            f.salt_capped + i.mortgage
        };
        xitded = (salt_plus_mortgage - salt).max(0.0);
        let l = p.itemized_limit.as_ref().expect("itemized limit through 2017");
        let over_thr = ky_agi > phas92 && (1991..=2017).contains(&y);
        let mut reduce = (l.cap_rate * xitded).min(l.rate * (ky_agi - phas92).max(0.0));
        if y >= 1991 {
            reduce *= l.reduction_share;
        }
        if over_thr {
            xitded -= reduce;
        }
        if let Some(c) = &p.child_care_deduction {
            let ytest = ky_agi + ky_fedtax;
            let raw = f.federal_chcr;
            let mut child = if i.depx > 0.0 && i.depx < 2.0 {
                c.caps[0].min(raw)
            } else if i.depx > 1.0 && i.depx < 3.0 {
                c.caps[1].min(raw)
            } else if i.depx > 2.0 {
                c.caps[2].min(raw)
            } else {
                0.0
            };
            if ytest >= c.phaseout_start {
                child = (child - (ytest - c.phaseout_start) / 2.0).max(0.0);
            }
            if !(ytest <= c.income_ceiling) {
                child = 0.0;
            }
            xitded += child;
        }
    } else {
        xitded = i.mortgage;
    }
    if y == 1999 && !f.forced_itemize {
        xitded = 0.0;
    }
    let deduc = stded.max(xitded);
    let taxinc = (ky_agi - deduc).max(0.0);

    // ---- married filing combined: each spouse on their own share, with the full standard deduction each ----
    let mfc_years = y <= 2017 || y >= 2022;
    let is_mfc = is_joint && ky_agi > 0.0 && mfc_years;
    let agih = higher_earner_share(i, ky_agi);
    let agiw = ky_agi - agih;
    let xitdh = if ky_agi != 0.0 { xitded * agih / ky_agi } else { 0.0 };
    let xitdw = xitded - xitdh;
    let dedh = stded.max(xitdh);
    let dedw = stded.max(xitdw);
    let taxinh = if is_mfc { (agih - dedh).max(0.0) } else { 0.0 };
    let taxinw = if is_mfc { (agiw - dedw).max(0.0) } else { 0.0 };

    // ---- tax ----
    let mut statax = match &p.schedule {
        Schedule::Brackets(table) => {
            let statax = table.tax(taxinc);
            if is_mfc { statax.min(table.tax(taxinh) + table.tax(taxinw)) } else { statax }
        }
        Schedule::Flat(rate) => {
            let mut statax = rate * taxinc;
            if y >= 2022 && is_mfc {
                statax = rate * taxinc.min(taxinh + taxinw);
            }
            statax
        }
    };

    // ---- credits ----
    if y <= 1989 {
        statax = statax.max(0.0);
    } else {
        let gcred = p.personal_per_person * (taxpayers + i.depx) + p.personal_per_aged * aged;
        statax = (statax - gcred).max(0.0);
        let chcr = ky_ccc * p.child_care_credit_share;
        if let Some(rows) = &p.low_income {
            // The first bracket whose ceiling covers AGI applies.
            let mut rate_lc = 0.0;
            for (ceiling, rate) in rows.iter().rev() {
                rate_lc = if ky_agi <= *ceiling { *rate } else { rate_lc };
            }
            let lowcrd = statax * rate_lc;
            statax = (statax - chcr - lowcrd).max(0.0);
        } else if y >= 2005 {
            // TAXSIM counts a taxpayer 65 or older twice; the statute's family size does not.
            let aged_in_family = if y >= 2022 { 0.0 } else { aged };
            let num_family = (taxpayers + i.depx + aged_in_family).min(4.0);
            let modagi = ky_agi.max(f.agi);
            let perc = match &p.family {
                FamilyCredit::Poverty(g) => {
                    let poverty = g.first_person + g.additional_person * (num_family - 1.0);
                    let share = modagi / poverty;
                    let mut perc = 0.0;
                    for (limit, rate) in g.steps.iter().rev() {
                        perc = if share <= *limit { *rate } else { perc };
                    }
                    perc
                }
                FamilyCredit::Indexed { tables, index } => {
                    let n = num_family as usize;
                    if (1..=4).contains(&n) { tables[n - 1].eval(modagi / index[n - 1]) } else { 0.0 }
                }
                FamilyCredit::None => 0.0,
            };
            let famcr = statax * perc;
            statax = (statax - famcr - chcr).max(0.0);
        }
    }
    statax * 1.0
}
