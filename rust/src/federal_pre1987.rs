//! Federal individual income tax for 1977 through 1986 (port of
//! `taxsim_py.calculators.federal_pre1987`, statutory mode). The payroll tax is
//! added by `Federal::run`.

use std::path::Path;

use crate::common::Brackets;
use crate::federal::{clip, Fed, Inp, Pre87};
use crate::params::{self, brackets, get, load, num, status_at, status_direct, Fs, YearParams};

/// The alternative computation of tax on capital gains (`acgtax`).
enum AltGains {
    None,
    /// 1977-1978.
    Threshold { threshold: f64, subd_rate: f64, capded_rate: f64 },
    /// 1981.
    Share { excluded_share: f64, rate: f64 },
}

struct MaxTax {
    floor: [f64; 4],
    accumulated: [f64; 4],
}

enum CareRate {
    Flat(f64),
    Phased { max_rate: f64, agi_floor: f64, agi_step: f64, min_rate: f64 },
}

struct GeneralCredit {
    per_exemption: f64,
    maximum: f64,
    rate: f64,
}

struct Elderly {
    base_one: f64,
    base_two: f64,
    threshold_one: f64,
    threshold_two: f64,
    agi_share: f64,
    rate: f64,
}

enum MinTax {
    /// 1977-1978: none reaches the tax.
    None,
    /// 1979-1981: three tiers.
    Tiers3 { thresholds: [f64; 3], rates: [f64; 3] },
    /// 1982: two tiers and the excess itemized deduction preference.
    Tiers2 { thresholds: [f64; 2], rates: [f64; 2], agi_floor_share: f64 },
    /// 1983-1986: a flat rate above an offset.
    Flat { rate: f64, offset_single_hoh: f64, offset_joint_sep: f64 },
}

pub struct Pre87Params {
    year: i64,
    div_adjustment: f64,
    div_exclusion: [f64; 4],
    brackets: [Brackets; 4],
    capital_gains_exclusion: f64,
    long_term_loss_share: f64,
    loss_limit: f64,
    two_earner: Option<(f64, f64)>,
    unemployment_threshold: Option<[f64; 4]>,
    /// From 1984: the base amounts and the share of benefits taxed.
    social_security: Option<([f64; 4], f64)>,
    zbr: [f64; 4],
    exemption_amount: f64,
    rate_reduction: f64,
    alt_gains: AltGains,
    max_tax: Option<MaxTax>,
    /// Itemized-deduction preference share of AGI and its cap (1977-1978, 1982).
    excess_pref: (f64, f64),
    care_expense_cap: f64,
    care_rate: CareRate,
    general_credit: Option<GeneralCredit>,
    elderly: Elderly,
    min_tax: MinTax,
    eitc: (f64, f64, f64, f64),
}

impl Pre87Params {
    pub fn load(root: &Path, year: i64) -> Result<Self, String> {
        if !(1977..=1986).contains(&year) {
            return Err(format!("law year {year} is not supported by the compiled calculator"));
        }
        let doc = load(root, "national/pre1987.yaml")?;
        let ss_doc = load(root, "national/social_security.yaml")?;
        let adj_doc = load(root, "national/state_adjustments.yaml")?;
        let p = YearParams::new(&doc, year);

        let mut table = Vec::new();
        for key in params::STATUS_KEYS {
            table.push(Brackets::new(&brackets(params::resolve_year(get(get(&doc, "brackets")?, key)?, year)?)?));
        }
        let table: [Brackets; 4] = table.try_into().map_err(|_| "brackets")?;

        let alt_gains = match p.opt("alternative_capital_gains")? {
            None => AltGains::None,
            Some(a) if a.get("threshold").is_some() => AltGains::Threshold {
                threshold: num(get(a, "threshold")?)?,
                subd_rate: num(get(a, "subd_rate")?)?,
                capded_rate: num(get(a, "capded_rate")?)?,
            },
            Some(a) => AltGains::Share { excluded_share: num(get(a, "excluded_share")?)?, rate: num(get(a, "rate")?)? },
        };
        let max_tax = if p.in_effect("max_tax_earned_income_floor")? {
            Some(MaxTax {
                floor: status_direct(p.value("max_tax_earned_income_floor", &[])?)?,
                accumulated: status_direct(p.value("max_tax_earned_income_accumulated", &[])?)?,
            })
        } else {
            None
        };
        let excess = get(&doc, "excess_itemized_preference")?;
        let care_rate = match p.value("child_care_credit_rate", &[])? {
            v if v.is_mapping() => CareRate::Phased {
                max_rate: num(get(v, "max_rate")?)?,
                agi_floor: num(get(v, "agi_floor")?)?,
                agi_step: num(get(v, "agi_step")?)?,
                min_rate: num(get(v, "min_rate")?)?,
            },
            v => CareRate::Flat(num(v)?),
        };
        let general_credit = match p.opt("general_tax_credit")? {
            Some(g) => Some(GeneralCredit {
                per_exemption: num(get(g, "per_exemption")?)?,
                maximum: num(get(g, "maximum")?)?,
                rate: num(get(g, "taxable_income_rate")?)?,
            }),
            None => None,
        };
        let eld = YearParams::new(get(&doc, "elderly_credit")?, year);
        let (base, threshold) = (eld.value("base", &[])?, eld.value("agi_threshold", &[])?);
        let elderly = Elderly {
            base_one: num(get(base, "one_aged")?)?,
            base_two: num(get(base, "two_aged")?)?,
            threshold_one: num(get(threshold, "one_aged")?)?,
            threshold_two: num(get(threshold, "two_aged")?)?,
            agi_share: eld.f("agi_share")?,
            rate: eld.f("rate")?,
        };
        let min_tax = if year <= 1978 {
            MinTax::None
        } else if year <= 1981 {
            MinTax::Tiers3 {
                thresholds: [p.f("minimum_tax_tier1_threshold")?, p.f("minimum_tax_tier2_threshold")?, p.f("minimum_tax_tier3_threshold")?],
                rates: [p.f("minimum_tax_tier1_rate")?, p.f("minimum_tax_tier2_rate")?, p.f("minimum_tax_tier3_rate")?],
            }
        } else if year == 1982 {
            MinTax::Tiers2 {
                thresholds: [p.f("minimum_tax_tier1_threshold")?, p.f("minimum_tax_tier2_threshold")?],
                rates: [p.f("minimum_tax_tier1_rate")?, p.f("minimum_tax_tier2_rate")?],
                agi_floor_share: num(get(excess, "agi_floor_share")?)?,
            }
        } else {
            MinTax::Flat {
                rate: p.f("minimum_tax_flat_rate")?,
                offset_single_hoh: p.f("minimum_tax_offset_single_hoh")?,
                offset_joint_sep: p.f("minimum_tax_offset_joint_sep")?,
            }
        };
        let dividend_exclusion = get(&doc, "dividend_exclusion")?;
        Ok(Self {
            year,
            div_adjustment: num(get(&adj_doc, "household_income_dividend_adjustment")?)?,
            div_exclusion: status_at(dividend_exclusion, year)?,
            brackets: table,
            capital_gains_exclusion: p.f("capital_gains_exclusion_rate")?,
            long_term_loss_share: p.f("long_term_loss_share")?,
            loss_limit: p.f("net_capital_loss_limit")?,
            two_earner: if p.in_effect("two_earner_deduction_rate")? {
                Some((p.f("two_earner_deduction_rate")?, p.f("two_earner_deduction_cap")?))
            } else {
                None
            },
            unemployment_threshold: match p.opt("unemployment_exclusion_threshold")? {
                Some(t) => Some(status_direct(t)?),
                None => None,
            },
            social_security: if year >= 1984 {
                Some((status_direct(get(&ss_doc, "base_amount")?)?, num(get(&ss_doc, "first_tier_rate")?)?))
            } else {
                None
            },
            zbr: status_at(get(&doc, "standard_deduction")?, year)?,
            exemption_amount: p.f("personal_exemption_amount")?,
            rate_reduction: if p.in_effect("rate_reduction_credit_multiplier")? { p.f("rate_reduction_credit_multiplier")? } else { 1.0 },
            alt_gains,
            max_tax,
            excess_pref: (num(get(excess, "agi_floor_share")?)?, num(get(excess, "cap_share")?)?),
            care_expense_cap: p.f("child_care_credit_expense_cap")?,
            care_rate,
            general_credit,
            elderly,
            min_tax,
            eitc: (p.f("eitc_rate_in")?, p.f("eitc_max_credit")?, p.f("eitc_phaseout_start")?, p.f("eitc_rate_out")?),
        })
    }
}

/// Federal income tax for one household under 1977-1986 law.
pub fn pre1987(i: &Inp, ded: f64, forced_itemize: bool, p: &Pre87Params) -> Fed {
    let year = p.year;
    let fs = i.fs();
    let fsi = fs as usize;
    let sepret = i.sepret();
    let dependent = i.is_dependent_filer();
    let wages = i.pwages + i.swages;
    let ui_combined = i.ui.max(i.pui + i.sui);

    let gross_se = i.psemp + i.ssemp + i.pbusinc + i.pprofinc + i.sbusinc + i.sprofinc;
    let earned = (wages + gross_se).max(0.0);

    // Dividend exclusion (`divall`); 1981 also excludes interest.
    let div_f = i.dividends + p.div_adjustment;
    let divexc = p.div_exclusion[fsi];
    let (divall, ints) = if year == 1981 { ((div_f + i.intrec - divexc).max(0.0), 0.0) } else { ((div_f - divexc).max(0.0), i.intrec) };

    // Capital gains: part of a net long-term gain is excluded (`capded`).
    let fullcg = i.stcg + i.ltcg;
    let capded = (p.capital_gains_exclusion * i.ltcg.min(fullcg)).max(0.0);
    let net_loss = if i.ltcg >= 0.0 {
        fullcg
    } else if i.stcg >= 0.0 {
        p.long_term_loss_share * fullcg
    } else {
        i.stcg + p.long_term_loss_share * i.ltcg
    };
    let mut capgn = if fullcg > 0.0 { fullcg - capded } else { net_loss.max(-p.loss_limit / sepret) };

    // Two-earner deduction: a share of the lower earner's wages, capped.
    let twoded = match p.two_earner {
        Some((rate, cap)) => {
            let wife = i.pwages.min(i.swages).max(0.0);
            if fs == Fs::Joint { clip(rate * wife, 0.0, cap) } else { 0.0 }
        }
        None => 0.0,
    };

    let ti = divall + wages + ints + gross_se + i.pensions + i.otherprop + i.scorp;
    // A net capital loss cannot exceed other income.
    capgn = capgn.max(-ti.max(0.0));
    let agi_pre_ui = ti - twoded + i.nonprop + capgn;
    let unearned = ti - earned + capgn;

    // Unemployment compensation: half the excess over a threshold is taxable, up to the benefit.
    let untax = match &p.unemployment_threshold {
        None => 0.0,
        Some(t) => (0.5 * (ui_combined + agi_pre_ui - t[fsi]).max(0.0)).min(ui_combined),
    };
    let mut agi = agi_pre_ui + untax;
    // From 1984, half of Social Security benefits above the base amount.
    let taxable_ss = match &p.social_security {
        Some((base, rate)) => {
            let benefits = i.gssi.max(0.0);
            let over_base = (agi + 0.5 * benefits + twoded - base[fsi]).max(0.0);
            if benefits > 0.0 { rate * benefits.min(over_base) } else { 0.0 }
        }
        None => 0.0,
    };
    agi += taxable_ss;

    // Itemized deduction and the zero-bracket amount.
    let zbr = p.zbr[fsi];
    let deduc = i.proptax + i.otheritem + i.mortgage + ded;
    let itemizes = if year <= 1981 { deduc > zbr } else { forced_itemize };
    let excess = if itemizes { deduc - zbr } else { 0.0 };
    let exemps_count = i.taxpayer_count() + i.depx + i.aged_count();
    let amex = p.exemption_amount * exemps_count;
    let add105 = if dependent && unearned >= 1000.0 && itemizes { (zbr - earned.max(deduc)).max(0.0) } else { 0.0 };
    let taxy = agi - excess - amex + add105;
    let taxable_for_bracket = if year <= 1978 { (taxy - zbr).max(0.0) } else { taxy.max(0.0) };
    // `taxinc` and `taxy` stay unclipped for the alternative and minimum taxes.
    let taxinc_unclamped = taxy - zbr;
    let table = &p.brackets[fsi];
    let rrc = p.rate_reduction;
    let regtax_raw = table.tax(taxable_for_bracket);
    let regtax = if rrc != 1.0 { rrc * regtax_raw } else { regtax_raw };

    // ---- alternative computation of tax on capital gains -------------------------
    let acgtax = match &p.alt_gains {
        AltGains::Threshold { threshold, subd_rate, capded_rate } => {
            let cg1 = (taxinc_unclamped - capded).max(0.0);
            let cgtx1 = table.tax(cg1);
            let subd = (threshold / sepret).max(0.0);
            let gated = capded * 2.0 > threshold / sepret;
            let further_gated = gated && subd < capded * 2.0;
            let cgtx2 = if further_gated { subd_rate * subd } else { capded_rate * capded };
            let cg3 = taxinc_unclamped.max(capded);
            let cgtx3 = if further_gated { table.tax(cg3) - table.tax(cg1 + subd * 0.5) } else { 0.0 };
            if fullcg > 0.0 { cgtx1 + cgtx2 + cgtx3 } else { -1.0 }
        }
        AltGains::Share { excluded_share, rate } => {
            let cglong = i.ltcg.min(fullcg).max(0.0);
            let cg1 = (taxy - excluded_share * cglong).max(0.0);
            let cgtx1_raw = table.tax(cg1);
            if cglong > 0.0 { rrc * cgtx1_raw + rate * cglong } else { -1.0 }
        }
        AltGains::None => -1.0,
    };

    // ---- maximum tax on earned income ---------------------------------------------
    let mut psinc = 0.0;
    let mut pref = 0.0;
    let etax = match &p.max_tax {
        Some(m) => {
            psinc = if agi != 0.0 { clip(earned + i.pensions, 0.0, agi) } else { earned + i.pensions };
            let eratio = if agi == 0.0 { 1.0 } else { (psinc / agi).min(1.0) };
            let eti = if year <= 1978 {
                // Itemizers add back excluded gains and excess itemized deductions.
                let exded_pref = (deduc - p.excess_pref.0 * agi).max(0.0).min(p.excess_pref.1 * agi);
                pref = if itemizes { (capded + 0.0 + exded_pref).max(0.0) } else { 0.0 };
                taxinc_unclamped * eratio - pref
            } else {
                taxy * eratio
            };
            let etop = eti - m.floor[fsi];
            let partax = table.tax(eti);
            let etax_raw = if rrc != 1.0 {
                rrc * (regtax_raw - partax + m.accumulated[fsi]) + 0.5 * etop
            } else {
                regtax - partax + m.accumulated[fsi] + 0.5 * etop
            };
            let eligible = sepret == 1.0 && agi > 0.0 && psinc > 0.0 && etop > 0.0;
            if eligible { etax_raw } else { -1.0 }
        }
        None => -1.0,
    };

    // ---- combine alternatives: taxbc = min(regular, acgtax, etax) ---------------
    let mut taxbc = if acgtax > 0.0 { acgtax.min(regtax) } else { regtax };
    if etax > 0.0 && etax < taxbc {
        taxbc = etax;
    }

    // ---- credits -----------------------------------------------------------------------
    let chmax = p.care_expense_cap * clip(i.dep13, 0.0, 2.0);
    let chwage = wages.max(0.0);
    let child_expense = chmax.min(chwage).min(i.childcare);
    let ccc_rate = match &p.care_rate {
        CareRate::Flat(rate) => *rate,
        CareRate::Phased { max_rate, agi_floor, agi_step, min_rate } => {
            (max_rate - ((agi - agi_floor) / agi_step).max(0.0)).max(*min_rate)
        }
    };
    let chcr = child_expense * ccc_rate;
    let gencr = match &p.general_credit {
        Some(g) => (g.per_exemption * exemps_count).max((g.rate * taxable_for_bracket).min(g.maximum)),
        None => 0.0,
    };
    // Credit for the elderly.
    let e = &p.elderly;
    let aged = i.aged_count();
    let one_aged = aged == 1.0 && fs != Fs::Separate;
    let base_amount = if one_aged { e.base_one } else { e.base_two / sepret };
    let single = fs != Fs::Joint && fs != Fs::Separate;
    let agi_threshold = if single { e.threshold_one } else { e.threshold_two / sepret };
    let reduction = e.agi_share * (agi - agi_threshold).max(0.0) + (i.gssi - taxable_ss).max(0.0);
    let elderly = if aged > 0.0 { e.rate * (base_amount - reduction).max(0.0) } else { 0.0 };
    let credit = (chcr + gencr + elderly).min(taxbc).max(0.0);
    let taxaft = taxbc - credit;

    // ---- minimum tax: layered as a floor over taxaft ----------------------------------
    let (mintax_raw, alminy) = match &p.min_tax {
        MinTax::None => (-1.0, 0.0),
        MinTax::Tiers3 { thresholds, rates } => {
            let alminy = taxinc_unclamped + capded;
            let tax = rates[0] * (alminy - thresholds[0] / sepret).max(0.0)
                + rates[1] * (alminy - thresholds[1] / sepret).max(0.0)
                + rates[2] * (alminy - thresholds[2] / sepret).max(0.0);
            (tax, alminy)
        }
        MinTax::Tiers2 { thresholds, rates, agi_floor_share } => {
            // The excess-itemized-deduction preference leaves state income tax out of both the deductions and AGI.
            let prfded = (deduc - ded).max(0.0);
            let prfddy = (agi - ded).max(0.0);
            let exded = if itemizes { (prfded - agi_floor_share * prfddy).max(0.0) } else { 0.0 };
            let alminy = agi - excess - zbr - amex + capded + exded;
            let tax = rates[0] * (alminy - thresholds[0] / sepret).max(0.0) + rates[1] * (alminy - thresholds[1] / sepret).max(0.0);
            (tax, alminy)
        }
        MinTax::Flat { rate, offset_single_hoh, offset_joint_sep } => {
            let offset = if fs == Fs::Single || fs == Fs::Hoh { *offset_single_hoh } else { offset_joint_sep / sepret };
            // Mortgage interest on a housing loan reduces the base for itemizers.
            let pref_1983 = if fullcg < 0.0 { fullcg - capgn } else { capded };
            let housing = if itemizes { i.mortgage } else { 0.0 };
            let alminy = (agi + pref_1983 - housing).max(0.0);
            (rate * (alminy - offset).max(0.0), alminy)
        }
    };
    let tax_after_mintax = if mintax_raw > 0.0 { taxaft.max(mintax_raw) } else { taxaft };

    // ---- earned income credit: its own flat early schedule -------------------------
    let (rate_in, max_credit, phaseout_start, rate_out) = p.eitc;
    let phase_in = clip(earned * rate_in, 0.0, max_credit);
    let reduction_eitc = ((earned.max(agi) - phaseout_start) * rate_out).max(0.0);
    let earncr_raw = phase_in.min((max_credit - reduction_eitc).max(0.0)).max(0.0);
    let earncr = if fs == Fs::Separate || i.dep18 == 0.0 || dependent { 0.0 } else { earncr_raw };

    let fiitax = tax_after_mintax - earncr;

    // ---- figures the state calculators read ---------------------------------------------
    let no_benefit = taxinc_unclamped <= 0.0 && agi - amex - zbr <= 0.0;
    let reported_itemizes = itemizes && !no_benefit;
    let almtax = tax_after_mintax - taxaft;
    let pref_out = if year <= 1978 {
        pref
    } else if year <= 1982 {
        0.0
    } else if fullcg < 0.0 {
        fullcg - capgn
    } else {
        capded
    };
    let eitc_offset = earncr.min(tax_after_mintax.max(0.0));
    let pretax = regtax.max(taxbc).max(almtax).max(tax_after_mintax - earncr + eitc_offset + credit);
    let _ = psinc;

    Fed {
        itemizes: false,
        pre87: Pre87 {
            credit,
            taxbc,
            chcr,
            earncr,
            almtax,
            pretax,
            capgn,
            capded,
            pref: pref_out,
            twoded,
            zbr,
            amex,
            deduc: if reported_itemizes { deduc } else { 0.0 },
            gencr,
            alminy,
            taxinc: taxinc_unclamped.max(0.0),
            divexc,
            itemizes: reported_itemizes,
        },
        agi,
        taxable_income: taxable_for_bracket,
        taxable_unemployment: untax,
        taxable_social_security: taxable_ss,
        earned_income: earned,
        regular_tax: regtax,
        fiitax: fiitax,
        ..Fed::default()
    }
}
