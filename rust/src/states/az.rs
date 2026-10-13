//! Arizona income tax (port of `calculators.states.az`).

use std::path::Path;

use crate::common::Brackets;
use crate::federal::{Fed, Inp};
use crate::params::{self, get, load, num, resolve_year, status_direct, Fs, YearParams};
use crate::payroll::round_half_even;
use crate::states::{
    dividend_adjustment, dividend_exclusion_addback, household_income, law_year_is_exact, Interp,
};

struct Trapezoid {
    rate_in: f64,
    max_credit: f64,
    phaseout_start: f64,
    rate_out: f64,
}

struct PercentStandard {
    rate: f64,
    cap_per_exemption: f64,
}

enum Standard {
    Percent(PercentStandard),
    PerExemption(f64),
    Multiple { amount: f64, joint: f64, hoh: f64, other: f64 },
}

struct ItemizedLimit {
    rate: f64,
    cap_rate: f64,
    reduction_share: f64,
    threshold_by_status: Option<[f64; 4]>,
    threshold: f64,
}

enum Schedule {
    One(Brackets),
    ByRich { single: Brackets, rich: Brackets },
}

struct FamilyCredit {
    per_exemption: f64,
    cap_single: f64,
    cap_rich: f64,
    limit_single: f64,
    limit_rich: f64,
    joint_by_dependents: Option<[f64; 4]>,
    hoh_by_dependents: Option<[f64; 5]>,
}

struct DependentCredit {
    per_child: f64,
    per_other: f64,
    phaseout_start: f64,
    reduction_per_1000: f64,
}

struct Excise {
    agi_limit: f64,
    amount: f64,
    cap: f64,
}

struct Renter {
    rate: f64,
    cap: f64,
    limit: Option<(f64, f64)>,
}

pub struct AzParams {
    year: i64,
    two_earner: Option<(f64, f64)>,
    eitc_pre87: Option<Trapezoid>,
    indexing_factor: Option<f64>,
    personal_exemption: Option<f64>,
    married_with_dependents: Option<f64>,
    dependent_exemption: Option<f64>,
    backout: Option<f64>,
    aged_exemption: f64,
    standard: Standard,
    child_care_deduction: Option<(f64, f64)>,
    itemized_limit: Option<ItemizedLimit>,
    schedule: Schedule,
    bracket_inflation: f64,
    family: Option<FamilyCredit>,
    dependent_credit: Option<DependentCredit>,
    excise: Option<Excise>,
    property_family_addition: f64,
    property_rows: Vec<Vec<f64>>,
    rent_factor: f64,
    renter: Option<Renter>,
    dividend_adjustment: f64,
}

impl AzParams {
    pub fn load(root: &Path, year: i64) -> Result<Self, String> {
        let doc = load(root, "states/az/income_tax.yaml")?;
        law_year_is_exact(root, &doc, year)?;
        let pre87_doc = load(root, "national/pre1987.yaml")?;
        let adjustments = load(root, "national/state_adjustments.yaml")?;
        let p = YearParams::new(&doc, year);
        let p87 = YearParams::new(&pre87_doc, year);
        let opt = |key: &str| -> Result<Option<f64>, String> { Ok(if p.in_effect(key)? { Some(p.f(key)?) } else { None }) };

        let tables = p.value("brackets", &[])?;
        let schedule = if tables.is_mapping() {
            Schedule::ByRich {
                single: Brackets::new(&params::brackets(get(tables, "single")?)?),
                rich: Brackets::new(&params::brackets(get(tables, "joint_or_hoh")?)?),
            }
        } else {
            Schedule::One(Brackets::new(&params::brackets(tables)?))
        };
        let standard = if p.in_effect("standard_deduction_percent")? {
            let sd = p.value("standard_deduction_percent", &[])?;
            Standard::Percent(PercentStandard { rate: num(get(sd, "rate")?)?, cap_per_exemption: num(get(sd, "cap_per_exemption")?)? })
        } else if p.in_effect("standard_deduction_per_exemption")? {
            Standard::PerExemption(p.f("standard_deduction_per_exemption")?)
        } else {
            let m = get(&doc, "standard_deduction_multiple")?;
            Standard::Multiple {
                amount: p.f("standard_deduction")?,
                joint: num(get(m, "married_joint")?)?,
                hoh: num(get(m, "head_of_household")?)?,
                other: num(get(m, "other")?)?,
            }
        };
        let itemized_limit = if p.in_effect("child_care_deduction")? || !p.in_effect("itemized_limitation")? {
            None
        } else {
            let lim = p.value("itemized_limitation", &[])?;
            let index = num(resolve_year(get(&adjustments, "itemized_phaseout_inflation")?, year)?)?;
            let threshold = get(lim, "threshold")?;
            let by_status = if threshold.is_mapping() {
                let raw = status_direct(threshold)?;
                Some([raw[0] * index, raw[1] * index, raw[2] * index, raw[3] * index])
            } else {
                None
            };
            Some(ItemizedLimit {
                rate: num(get(lim, "rate")?)?,
                cap_rate: num(get(lim, "cap_rate")?)?,
                reduction_share: num(get(lim, "reduction_share")?)?,
                threshold_by_status: by_status,
                threshold: if threshold.is_mapping() { 0.0 } else { num(threshold)? * index },
            })
        };
        let family = match p.opt("family_income_credit")? {
            Some(fic) => {
                let limits = get(fic, "agi_limit")?;
                let cap = get(fic, "cap")?;
                let list = |key: &str| -> Result<Vec<f64>, String> {
                    get(limits, key)?.as_sequence().ok_or("expected a list")?.iter().map(num).collect()
                };
                let (joint, hoh) = if limits.get("married_joint").is_some() {
                    let j = list("married_joint")?;
                    let h = list("head_of_household")?;
                    (Some([j[0], j[1], j[2], j[3]]), Some([h[0], h[1], h[2], h[3], h[4]]))
                } else {
                    (None, None)
                };
                Some(FamilyCredit {
                    per_exemption: num(get(fic, "per_exemption")?)?,
                    cap_single: num(get(cap, "single")?)?,
                    cap_rich: num(get(cap, "joint_or_hoh")?)?,
                    limit_single: num(get(limits, "single")?)?,
                    limit_rich: match limits.get("joint_or_hoh") {
                        Some(v) => num(v)?,
                        None => 0.0,
                    },
                    joint_by_dependents: joint,
                    hoh_by_dependents: hoh,
                })
            }
            None => None,
        };
        let dependent_credit = match p.opt("dependent_tax_credit")? {
            Some(d) => Some(DependentCredit {
                per_child: num(get(d, "per_child")?)?,
                per_other: num(get(d, "per_other_dependent")?)?,
                phaseout_start: num(get(d, "phaseout_start")?)?,
                reduction_per_1000: num(get(d, "reduction_per_1000")?)?,
            }),
            None => None,
        };
        let excise = match p.opt("excise_credit")? {
            Some(e) => Some(Excise { agi_limit: num(get(e, "agi_limit")?)?, amount: num(get(e, "amount")?)?, cap: num(get(e, "cap")?)? }),
            None => None,
        };
        let renter = if p.in_effect("renter_credit_rate")? {
            Some(Renter {
                rate: p.f("renter_credit_rate")?,
                cap: p.f("renter_credit_cap")?,
                limit: match p.opt("renter_credit_limit")? {
                    Some(l) => Some((num(get(l, "agi_limit")?)?, num(get(l, "max")?)?)),
                    None => None,
                },
            })
        } else {
            None
        };
        let thresholds: Vec<f64> = get(&doc, "property_tax_credit_thresholds")?.as_sequence().ok_or("expected a list")?.iter().map(num).collect::<Result<_, _>>()?;
        let mut amounts: Vec<f64> = p.value("property_tax_credit_amounts", &[])?.as_sequence().ok_or("expected a list")?.iter().map(num).collect::<Result<_, _>>()?;
        if p.in_effect("indexing_factor")? {
            let aif = p.f("indexing_factor")?;
            amounts = amounts.iter().map(|a| aif * a).collect();
        }
        let addition = num(get(&doc, "property_tax_credit_family_addition")?)?;
        let property_rows: Vec<Vec<f64>> = thresholds.iter().zip(&amounts).map(|(t, a)| vec![*t, *a]).collect();
        let _ = &property_rows;
        let (indexing_factor, personal_exemption) = (opt("indexing_factor")?, opt("personal_exemption")?);
        Ok(Self {
            year,
            two_earner: if (1982..=1986).contains(&year) {
                Some((p87.f("two_earner_deduction_rate")?, p87.f("two_earner_deduction_cap")?))
            } else {
                None
            },
            eitc_pre87: if year <= 1986 {
                Some(Trapezoid {
                    rate_in: p87.f("eitc_rate_in")?,
                    max_credit: p87.f("eitc_max_credit")?,
                    phaseout_start: p87.f("eitc_phaseout_start")?,
                    rate_out: p87.f("eitc_rate_out")?,
                })
            } else {
                None
            },
            indexing_factor,
            personal_exemption,
            married_with_dependents: opt("personal_exemption_married_with_dependents")?,
            dependent_exemption: opt("dependent_exemption")?,
            backout: opt("head_of_household_exemption_backout")?,
            aged_exemption: if year >= 1990 { p.f("aged_exemption")? } else { 0.0 },
            standard,
            child_care_deduction: match p.opt("child_care_deduction")? {
                Some(c) => Some((num(get(c, "income_limit")?)?, num(get(c, "cap")?)?)),
                None => None,
            },
            itemized_limit,
            schedule,
            bracket_inflation: p.f("bracket_inflation")?,
            family,
            dependent_credit,
            excise,
            property_family_addition: addition,
            property_rows,
            rent_factor: num(get(&doc, "property_tax_credit_rent_factor")?)?,
            renter,
            dividend_adjustment: dividend_adjustment(root)?,
        })
    }
}

fn property_rows_with_sentinel(rows: &[Vec<f64>], shift: f64) -> Vec<Vec<f64>> {
    let mut out: Vec<Vec<f64>> = rows.iter().map(|r| vec![r[0] + shift, r[1]]).collect();
    out.push(vec![1.0e20, 0.0]);
    out
}

pub fn tax(i: &Inp, f: &Fed, p: &AzParams) -> f64 {
    let y = p.year;
    let fs = i.fs();
    let is_joint = fs == Fs::Joint;
    let is_hoh = fs == Fs::Hoh;
    let is_sep = fs == Fs::Separate;
    let rich = is_joint || is_hoh;
    let sep = i.sepret();
    let txp_raw = i.taxpayer_count();
    // Heads of household count as two taxpayers for exemptions, the standard deduction and the family income credit.
    let txp = txp_raw + if is_hoh { 1.0 } else { 0.0 };
    let nchild = if p.dependent_exemption.is_some() { i.depx } else { 0.0 };
    let aged = i.aged_count();
    let household = household_income(i, p.dividend_adjustment);
    let aif_index = p.indexing_factor;

    // ---- AGI ----
    let div_addback = if y <= 1980 { dividend_exclusion_addback(y, i, f, p.dividend_adjustment) } else { 0.0 };
    let twoded_addback = match p.two_earner {
        Some((rate, cap)) if is_joint => (rate * i.pwages.min(i.swages).max(0.0)).max(0.0).min(cap),
        _ => 0.0,
    };
    let exempt_ss = if y >= 1984 { f.taxable_social_security } else { 0.0 };
    // 2025 on: Arizona subtracts the federal senior deduction from its income.
    let agi_1 = f.agi + div_addback + twoded_addback - exempt_ss - if y >= 2025 { f.senior_deduction } else { 0.0 };
    // Through 1989 federal income tax, before the EITC and nonrefundable credits, is subtracted from AGI.
    let earncr = if let Some(t) = &p.eitc_pre87 {
        let earned = (i.pwages + i.swages + i.psemp.max(0.0) + i.ssemp.max(0.0)).max(0.0);
        let phase_in = (earned * t.rate_in).max(0.0).min(t.max_credit);
        let reduction = ((earned.max(f.agi) - t.phaseout_start) * t.rate_out).max(0.0);
        let raw = phase_in.min((t.max_credit - reduction).max(0.0)).max(0.0);
        if is_sep || i.dep18 == 0.0 { 0.0 } else { raw }
    } else if y <= 1989 {
        f.eitc
    } else {
        0.0
    };
    let az_agi = if y <= 1989 {
        let credit = if y <= 1986 { f.pre87.credit } else { f.nonrefundable_credits };
        agi_1 - (f.fiitax + earncr + credit).max(0.0)
    } else {
        agi_1
    };

    // ---- exemptions ----
    let aged_amount;
    let mut exemp;
    if y <= 1989 {
        let pe = p.personal_exemption.unwrap_or(0.0);
        let de = p.dependent_exemption.unwrap_or(0.0);
        let aif = aif_index.unwrap_or(1.0);
        exemp = (txp * pe + nchild * de) * aif;
        if let Some(backout) = p.backout {
            exemp -= if is_hoh { nchild.clamp(0.0, 1.0) * backout * aif } else { 0.0 };
        }
        aged_amount = pe * aif;
    } else if let Some(mut pe) = p.personal_exemption {
        if let Some(married) = p.married_with_dependents {
            if matches!(fs, Fs::Joint | Fs::Separate) && nchild > 0.0 {
                pe = married;
            }
        }
        exemp = txp * pe + nchild * p.dependent_exemption.unwrap_or(0.0);
        aged_amount = p.aged_exemption;
    } else {
        exemp = 0.0;
        aged_amount = p.aged_exemption;
    }
    exemp += aged_amount * aged;

    // ---- standard deduction ----
    let stded = match &p.standard {
        Standard::Percent(s) => {
            let aif = aif_index.unwrap_or(1.0);
            // TAXSIM rounds the index on the rate to tenths through 1983 and hundredths in 1984-1985.
            let fctr = if y <= 1983 {
                round_half_even(aif * 10.0, 0) / 10.0
            } else if y == 1984 || y == 1985 {
                round_half_even(aif * 100.0, 0) / 100.0
            } else {
                aif
            };
            (s.rate * fctr * az_agi).max(0.0).min(s.cap_per_exemption * txp * aif)
        }
        Standard::PerExemption(amount) => txp * amount,
        Standard::Multiple { amount, joint, hoh, other } => {
            (if is_joint { *joint } else if is_hoh { *hoh } else { *other }) * amount
        }
    };

    // ---- itemized deduction ----
    let raw_itemized = i.proptax + i.otheritem + i.mortgage;
    let xitded = if let Some((limit, cap)) = p.child_care_deduction {
        let childcare_ded = if household < limit / sep { i.childcare.max(0.0).min(cap) } else { 0.0 };
        // TAXSIM counts other taxes twice.
        (raw_itemized + i.otheritem + childcare_ded).max(0.0)
    } else {
        let base = f.salt_capped + i.mortgage;
        match &p.itemized_limit {
            Some(l) => {
                let phas = match &l.threshold_by_status {
                    Some(by_status) => by_status[fs as usize],
                    None => l.threshold / sep,
                };
                let reduce = if az_agi > phas { (l.cap_rate * base).min(l.rate * (az_agi - phas)) } else { 0.0 };
                (base - l.reduction_share * reduce).max(0.0)
            }
            None => base,
        }
    };
    let deduc = stded.max(xitded);
    let taxinc = (az_agi - deduc - exemp).max(0.0);

    // ---- tax ----
    let aif = p.bracket_inflation;
    let regtax = match (&p.schedule, y) {
        (Schedule::One(b), _) if y <= 1989 => aif * b.tax(taxinc / aif),
        (Schedule::One(b), _) => {
            let taxy = if rich { taxinc / 2.0 } else { taxinc };
            let stat = aif * b.tax(taxy / aif);
            if rich { stat * 2.0 } else { stat }
        }
        (Schedule::ByRich { single, rich: rich_table }, _) => aif * if rich { rich_table.tax(taxinc / aif) } else { single.tax(taxinc / aif) },
    };

    // ---- credits ----
    let mut famcr = 0.0;
    if let Some(c) = &p.family {
        let mut fagi = if rich { c.limit_rich } else { c.limit_single };
        let famlim = if rich { c.cap_rich } else { c.cap_single };
        if let (Some(j), Some(h)) = (&c.joint_by_dependents, &c.hoh_by_dependents) {
            let depx = i.depx;
            let joint_fagi = if depx <= 1.0 { j[0] } else if depx == 2.0 { j[1] } else if depx == 3.0 { j[2] } else { j[3] };
            let hoh_fagi = if depx <= 1.0 {
                h[0]
            } else if depx == 2.0 {
                h[1]
            } else if depx == 3.0 {
                h[2]
            } else if depx == 4.0 {
                h[3]
            } else {
                h[4]
            };
            fagi = if is_joint { joint_fagi } else if is_hoh { hoh_fagi } else { fagi };
        }
        famcr = if az_agi <= fagi { famlim.min((i.depx + txp_raw) * c.per_exemption) } else { 0.0 };
    }
    let mut ctc = 0.0;
    if let Some(d) = &p.dependent_credit {
        let raw = d.per_child * i.dep17 + d.per_other * (i.depx - i.dep17).max(0.0);
        let phase = d.phaseout_start * txp_raw;
        ctc = (raw - ((az_agi - phase).max(0.0) / 1000.0) * d.reduction_per_1000).max(0.0);
    }
    let after_famcr = (regtax - famcr - ctc).max(0.0);

    let excise = match &p.excise {
        Some(e) => {
            let numb = if is_hoh { 2.0 } else { txp_raw };
            if f.agi <= e.agi_limit * numb && !i.is_dependent_filer() {
                (e.amount * (txp_raw + i.depx)).min(e.cap)
            } else {
                0.0
            }
        }
        None => 0.0,
    };

    // Refundable property tax credit (taxpayers 65 or older with pension or Social Security income) or renter credit, whichever is larger.
    let more = fs != Fs::Single || i.depx > 0.0;
    let shift = if more { p.property_family_addition } else { 0.0 };
    let pensions = i.pensions + i.gssi;
    let mut property_credit = if aged > 0.0 && pensions > 0.0 {
        // The statute's income test excludes Social Security, which TAXSIM's household income includes.
        let shifted = Interp::new(&property_rows_with_sentinel(&p.property_rows, shift));
        shifted.eval(household - i.gssi)
    } else {
        0.0
    };
    // ARS 43-1072(B): at most the property tax paid (renters: rent times the landlord property-tax factor).
    property_credit = property_credit.min(i.proptax + p.rent_factor * i.rentpaid);
    let renter_credit = match &p.renter {
        Some(r) => {
            let credit = (r.rate * i.rentpaid).max(0.0).min(r.cap);
            match r.limit {
                Some((agi_limit, max)) => if az_agi <= agi_limit { credit.min(max) } else { 0.0 },
                None => credit,
            }
        }
        None => 0.0,
    };
    let az_credit = property_credit.max(renter_credit);
    after_famcr - excise - az_credit
}
