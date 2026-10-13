//! Federal individual income tax for 1960 through 1976 (port of
//! `taxsim_py.calculators.federal_law60`, statutory mode). The payroll tax is
//! added by `Federal::run`.

use std::path::Path;

use crate::common::Brackets;
use crate::federal::{clip, Fed, Inp, Pre87};
use crate::params::{self, brackets, get, load, num, status_at, status_direct, Fs, YearParams};

/// How the zero-bracket amount (the standard deduction) is set.
enum Zbr {
    /// 1960-1963: a share of AGI up to a ceiling.
    Share { pct: f64, ceiling: f64 },
    /// 1964-1970: also at least a base plus an amount per exemption.
    PerExemption { pct: f64, ceiling: f64, base: f64, amount: f64, low_income: Option<LowIncome> },
    /// 1971-1974: one ceiling and floor for every filing status.
    FloorCeiling { pct: f64, ceiling: f64, floor: f64 },
    /// 1975-1976: separate ceilings and floors for joint and other returns.
    Joint { pct: f64, ceiling_other: f64, floor_other: f64, ceiling_joint: f64, floor_joint: f64 },
}

struct LowIncome {
    base: f64,
    per_exemption: f64,
    phaseout_rate: f64,
    income_base: f64,
    income_per_exemption: f64,
}

struct AltGains {
    threshold: f64,
    flat_component: f64,
    cap_rate: f64,
}

struct MaxTax {
    floor: [f64; 4],
    accumulated: [f64; 4],
    etop_multiplier: f64,
}

struct GeneralCredit {
    per_exemption: f64,
    cap: f64,
    rate: f64,
}

enum AddMin {
    Flat(f64),
    PctFloor(f64),
}

pub struct Law60Params {
    year: i64,
    brackets: [Brackets; 4],
    div_exclusion: [f64; 4],
    capital_gains_exclusion: f64,
    loss_limit: f64,
    loss_limit_separate: f64,
    exemption_amount: f64,
    zbr: Zbr,
    alt_gains: Option<AltGains>,
    max_tax: Option<MaxTax>,
    /// Itemized-deduction preference share of AGI, its cap, and the preference exemption.
    excess_pref: (f64, f64, f64),
    surtax_multiplier: Option<f64>,
    care_credit: Option<(f64, f64)>,
    exemption_credit: Option<f64>,
    general_credit: Option<GeneralCredit>,
    addmin_rate: f64,
    addmin: AddMin,
    eitc: Option<(f64, f64, f64, f64)>,
}

/// Separate returns use the joint table at double income, which equals halving every threshold.
fn halve_thresholds(rows: &[Vec<f64>]) -> Vec<Vec<f64>> {
    rows.iter().map(|r| vec![if r[0] < 1.0e28 { r[0] / 2.0 } else { r[0] }, r[1]]).collect()
}

impl Law60Params {
    pub fn load(root: &Path, year: i64) -> Result<Self, String> {
        if !(1960..=1976).contains(&year) {
            return Err(format!("law year {year} is not supported by the compiled calculator"));
        }
        let doc = load(root, "national/law60.yaml")?;
        let p = YearParams::new(&doc, year);

        let table = |key: &str| -> Result<Vec<Vec<f64>>, String> {
            brackets(params::resolve_year(get(get(&doc, "brackets")?, key)?, year)?)
        };
        let (single, joint, hoh) = (table("single")?, table("married_joint")?, table("head_of_household")?);
        // Separate returns use the single table through 1970 and the joint table at `sepret=2` from 1971.
        let separate = if year <= 1970 { single.clone() } else { halve_thresholds(&joint) };
        // Order of `STATUS_KEYS`: single, joint, separate, head of household.
        let brackets = [Brackets::new(&single), Brackets::new(&joint), Brackets::new(&separate), Brackets::new(&hoh)];

        let zbr = if year <= 1963 {
            Zbr::Share { pct: p.f("standard_deduction_pct")?, ceiling: p.f("standard_deduction_ceiling_other")? }
        } else if year <= 1970 {
            let low_income = match p.opt("low_income_allowance")? {
                Some(l) => Some(LowIncome {
                    base: num(get(l, "base")?)?,
                    per_exemption: num(get(l, "per_exemption")?)?,
                    phaseout_rate: num(get(l, "phaseout_rate")?)?,
                    income_base: num(get(l, "income_base")?)?,
                    income_per_exemption: num(get(l, "income_per_exemption")?)?,
                }),
                None => None,
            };
            Zbr::PerExemption {
                pct: p.f("standard_deduction_pct")?,
                ceiling: p.f("standard_deduction_ceiling_other")?,
                base: p.f("standard_deduction_per_exemption_base")?,
                amount: p.f("standard_deduction_per_exemption_amount")?,
                low_income,
            }
        } else if year <= 1974 {
            Zbr::FloorCeiling {
                pct: p.f("standard_deduction_pct")?,
                ceiling: p.f("standard_deduction_ceiling_other")?,
                floor: p.f("standard_deduction_floor_other")?,
            }
        } else {
            Zbr::Joint {
                pct: p.f("standard_deduction_pct")?,
                ceiling_other: p.f("standard_deduction_ceiling_other")?,
                floor_other: p.f("standard_deduction_floor_other")?,
                ceiling_joint: p.f("standard_deduction_ceiling_joint")?,
                floor_joint: p.f("standard_deduction_floor_joint")?,
            }
        };
        let alt_gains = if p.in_effect("alt_capital_gains_threshold")? {
            Some(AltGains {
                threshold: p.f("alt_capital_gains_threshold")?,
                flat_component: p.f("alt_capital_gains_flat_component")?,
                cap_rate: p.f("alt_capital_gains_cap_rate")?,
            })
        } else {
            None
        };
        let max_tax = if p.in_effect("max_tax_earned_income_floor")? {
            Some(MaxTax {
                floor: status_direct(p.value("max_tax_earned_income_floor", &[])?)?,
                accumulated: status_direct(p.value("max_tax_earned_income_accumulated", &[])?)?,
                etop_multiplier: p.f("max_tax_earned_income_etop_multiplier")?,
            })
        } else {
            None
        };
        let excess = get(&doc, "excess_itemized_preference")?;
        let general_credit = match p.opt("general_tax_credit")? {
            Some(g) => Some(GeneralCredit {
                per_exemption: num(get(g, "per_exemption")?)?,
                cap: num(get(g, "cap")?)?,
                rate: num(get(g, "rate")?)?,
            }),
            None => None,
        };
        Ok(Self {
            year,
            brackets,
            div_exclusion: status_at(get(&doc, "dividend_exclusion")?, year)?,
            capital_gains_exclusion: p.f("capital_gains_exclusion_rate")?,
            loss_limit: p.f("net_capital_loss_limit")?,
            loss_limit_separate: p.f("net_capital_loss_limit_married_separate")?,
            exemption_amount: p.f("personal_exemption_amount")?,
            zbr,
            alt_gains,
            max_tax,
            excess_pref: (
                num(get(excess, "agi_floor_share")?)?,
                num(get(excess, "cap_share")?)?,
                if p.in_effect("max_tax_earned_income_floor")? { p.f("preference_exemption")? } else { 0.0 },
            ),
            surtax_multiplier: if p.in_effect("surtax_multiplier")? { Some(p.f("surtax_multiplier")?) } else { None },
            care_credit: if p.in_effect("child_care_credit_flat_rate")? {
                Some((p.f("child_care_credit_expense_cap")?, p.f("child_care_credit_flat_rate")?))
            } else {
                None
            },
            exemption_credit: if p.in_effect("personal_exemption_credit_amount")? {
                Some(p.f("personal_exemption_credit_amount")?)
            } else {
                None
            },
            general_credit,
            addmin_rate: p.f("addmin_rate")?,
            addmin: if p.in_effect("addmin_offset_flat")? {
                AddMin::Flat(p.f("addmin_offset_flat")?)
            } else {
                AddMin::PctFloor(p.f("addmin_offset_pct_floor")?)
            },
            eitc: if p.in_effect("eitc_rate_in")? {
                Some((p.f("eitc_rate_in")?, p.f("eitc_max_credit")?, p.f("eitc_phaseout_start")?, p.f("eitc_rate_out")?))
            } else {
                None
            },
        })
    }
}

/// Federal income tax for one household under 1960-1976 law.
pub fn law60(i: &Inp, p: &Law60Params) -> Fed {
    let year = p.year;
    let fs = i.fs();
    let fsi = fs as usize;
    let sepret = i.sepret();
    let dependent = i.is_dependent_filer();
    let wages = i.pwages + i.swages;

    let se_income = i.psemp + i.ssemp + i.pbusinc + i.pprofinc + i.sbusinc + i.sprofinc;
    let earned = wages + se_income.max(0.0);

    let divall = (i.dividends - p.div_exclusion[fsi]).max(0.0);
    // Part of the long-term gain is excluded, on the net gain when a short-term loss offsets it.
    let fullcg = i.stcg + i.ltcg;
    let capded = if fullcg > 0.0 && i.ltcg > 0.0 {
        p.capital_gains_exclusion * if i.stcg < 0.0 { fullcg } else { i.ltcg }
    } else {
        0.0
    };
    let loss_limit = if fs == Fs::Separate { p.loss_limit_separate } else { p.loss_limit };
    let capgn = if fullcg > 0.0 { fullcg - capded } else { fullcg.max(-loss_limit) };

    let ti = divall + wages + i.intrec + se_income + capgn + i.pensions + i.otherprop + i.scorp;
    let agi = ti + i.nonprop;

    let deduc = i.proptax + i.otheritem + i.mortgage + if year <= 1975 { i.childcare } else { 0.0 };
    let agix = agi.max(0.0);
    let exemps_count = i.taxpayer_count() + i.depx + i.aged_count();
    let zbr = match &p.zbr {
        Zbr::Share { pct, ceiling } => (ceiling / sepret).min(pct * agix),
        Zbr::PerExemption { pct, ceiling, base, amount, low_income } => {
            let mut zbr = (ceiling / sepret).min(pct * agix).max((ceiling / sepret).min(base / sepret + amount * exemps_count));
            if let Some(l) = low_income {
                let zbradd = l.base - l.per_exemption * exemps_count;
                let zbrpo = (l.phaseout_rate * (agi - (l.income_base + l.income_per_exemption * exemps_count))).max(0.0);
                zbr += (zbradd - zbrpo).max(0.0);
            }
            zbr
        }
        Zbr::FloorCeiling { pct, ceiling, floor } => (ceiling / sepret).min(pct * agix).max(floor / sepret),
        Zbr::Joint { pct, ceiling_other, floor_other, ceiling_joint, floor_joint } => {
            // Separate returns get the joint ceiling and floor halved.
            if fs == Fs::Joint || fs == Fs::Separate {
                (ceiling_joint / sepret).min(pct * agix).max(floor_joint / sepret)
            } else {
                (ceiling_other / sepret).min(pct * agix).max(floor_other / sepret)
            }
        }
    };
    let itemizes = deduc > zbr;
    let amex = p.exemption_amount * exemps_count;
    let taxable_income = (agi - if itemizes { deduc } else { zbr } - amex).max(0.0);

    let table = &p.brackets[fsi];
    let regtax = table.tax(taxable_income);

    // ---- alternative tax on capital gains ---------------------------------------
    let cg1 = (taxable_income - capded).max(0.0);
    let cgtx1 = table.tax(cg1);
    let min_gain = i.ltcg.min(fullcg);
    let (cgtx2, cgtx3) = match &p.alt_gains {
        Some(a) => {
            let gate = min_gain > a.threshold / sepret;
            let xl32 = table.tax(taxable_income.max(capded));
            let xl33 = table.tax((taxable_income - capded + a.threshold / 2.0 / sepret).max(0.0));
            let raw_diff = (xl32 - xl33).max(0.0);
            let gated = raw_diff.min(a.cap_rate * (min_gain - a.threshold / sepret).max(0.0));
            (if gate { gated } else { 0.5 * capded }, if gate { a.flat_component / sepret } else { 0.0 })
        }
        None => (0.5 * capded, 0.0),
    };
    let acgtax = if fullcg > 0.0 { cgtx1 + cgtx2 + cgtx3 } else { -1.0 };

    // ---- maximum tax on earned income -----------------------------------------------
    let etax = match &p.max_tax {
        Some(m) => {
            let psinc = if agi > 0.0 { clip(earned + i.pensions, 0.0, agi) } else { earned + i.pensions };
            let eratio = if agi == 0.0 { 1.0 } else { (psinc / agi).min(1.0) };
            let (floor_share, cap_share, preference_exemption) = p.excess_pref;
            let exded = if itemizes && deduc - floor_share * agi > 0.0 {
                (deduc - floor_share * agi).min(cap_share * agi)
            } else {
                0.0
            };
            let preference_ui = i.ui.max(i.pui + i.sui);
            let pref = (capded + preference_ui + exded).max(0.0);
            let eti = taxable_income * eratio - (pref - preference_exemption).max(0.0);
            let etop = eti - m.floor[fsi];
            let partax = table.tax(eti);
            let etax_raw = regtax - partax + m.accumulated[fsi] + m.etop_multiplier * etop;
            let eligible = sepret == 1.0 && agi >= 0.0 && etop > 0.0 && (agi == 0.0 || psinc > 0.0);
            if eligible { etax_raw } else { -1.0 }
        }
        None => -1.0,
    };

    // ---- combine alternatives: when both save tax, their savings stack -------------
    let acgsav = if acgtax > 0.0 { (regtax - acgtax).max(0.0) } else { 0.0 };
    let mut altax = regtax - acgsav;
    altax = if etax > 0.0 && etax < regtax && acgsav > 0.0 {
        etax - acgsav
    } else if etax > 0.0 && etax < altax {
        etax
    } else {
        altax
    };
    if let Some(multiplier) = p.surtax_multiplier {
        altax *= multiplier;
    }

    // ---- credits -------------------------------------------------------------------------
    let chcr = match p.care_credit {
        Some((cap, rate)) => {
            let chmax = cap * clip(i.dep13, 0.0, 2.0);
            chmax.min(wages.max(0.0)).min(i.childcare) * rate
        }
        None => 0.0,
    };
    let gencr = if let Some(amount) = p.exemption_credit {
        amount * exemps_count
    } else if let Some(g) = &p.general_credit {
        (g.per_exemption * exemps_count).max(clip(g.rate * taxable_income, 0.0, g.cap))
    } else {
        0.0
    };
    let credit_raw = chcr + gencr;
    let credm = (chcr - altax).max(0.0);
    let credit = credit_raw.max(0.0).min(altax);
    let taxaft = (altax - credit).max(0.0);

    // ---- add-on minimum tax, added to tax rather than a floor ------------------------
    let addmin = match p.addmin {
        AddMin::Flat(offset_flat) => {
            let offset = offset_flat / sepret + taxaft - credit;
            // Before 1976 `gencr` is subtracted again although `credit` already reduced `taxaft`.
            (((capgn - offset) * p.addmin_rate) - credm - gencr).max(0.0)
        }
        AddMin::PctFloor(offset_floor) => {
            let offset = (0.5 * (taxaft - credit)).max(offset_floor / sepret);
            (((capgn - offset) * p.addmin_rate) - credm).max(0.0)
        }
    };
    // The minimum tax is added from 1969 only.
    let tax_after_addmin = if year >= 1969 { taxaft + addmin } else { taxaft };

    // ---- earned income credit; eligibility keys off `depx`, not `dep18` ----------
    let earncr = match p.eitc {
        Some((rate_in, max_credit, phaseout_start, rate_out)) => {
            let phase_in = clip(earned * rate_in, 0.0, max_credit);
            let reduction = ((earned.max(agi) - phaseout_start) * rate_out).max(0.0);
            let raw = phase_in.min((max_credit - reduction).max(0.0)).max(0.0);
            if fs == Fs::Separate || i.depx == 0.0 || dependent { 0.0 } else { raw }
        }
        None => 0.0,
    };

    Fed {
        itemizes: false,
        pre87: Pre87 {
            credit,
            taxbc: altax,
            chcr,
            earncr,
            zbr,
            amex,
            deduc: if itemizes { deduc } else { 0.0 },
            gencr,
            taxinc: taxable_income,
            itemizes,
            ..Pre87::default()
        },
        agi,
        taxable_income,
        taxable_unemployment: 0.0,
        earned_income: earned,
        regular_tax: regtax,
        fiitax: tax_after_addmin - earncr,
        ..Fed::default()
    }
}
