//! Federal individual income tax for the TCJA-era law years (port of
//! `taxsim_py.calculators.federal`): AGI, deductions, regular tax, AMT,
//! credits and `fiitax`. Parameters come straight from the YAML files.

use std::path::Path;

use serde_yaml::Value;

use crate::common::Brackets;
use crate::params::{self, brackets, get, load, num, status_at, status_direct, Fs, YearParams};
use crate::payroll::{payroll, Payroll, PayrollInputs, PayrollParams};

/// One household's inputs, as Python's `with_input_defaults` leaves them.
#[derive(Clone, Copy, Default)]
pub struct Inp {
    pub state: i64,
    pub mstat: f64,
    pub depx: f64,
    pub dep6: f64,
    pub dep13: f64,
    pub dep17: f64,
    pub dep18: f64,
    pub page: f64,
    pub sage: f64,
    pub pwages: f64,
    pub swages: f64,
    pub dividends: f64,
    pub intrec: f64,
    pub stcg: f64,
    pub ltcg: f64,
    pub otherprop: f64,
    pub nonprop: f64,
    pub pensions: f64,
    pub gssi: f64,
    pub ui: f64,
    pub transfers: f64,
    pub proptax: f64,
    pub otheritem: f64,
    pub childcare: f64,
    pub mortgage: f64,
    pub scorp: f64,
    pub pbusinc: f64,
    pub pprofinc: f64,
    pub sbusinc: f64,
    pub sprofinc: f64,
    pub pui: f64,
    pub sui: f64,
    pub psemp: f64,
    pub ssemp: f64,
    pub charity_cash: f64,
    pub rentpaid: f64,
    pub children_under_3: f64,
    pub children_under_4: f64,
    pub children_under_7: f64,
}

impl Inp {
    pub fn fs(&self) -> Fs {
        let m = self.mstat;
        if (m == 1.0 || m == 3.0) && self.depx > 0.0 {
            Fs::Hoh
        } else if m == 1.0 || m == 3.0 || m == 8.0 || m == 33.0 {
            Fs::Single
        } else if m == 2.0 {
            Fs::Joint
        } else {
            Fs::Separate
        }
    }
    pub fn taxpayer_count(&self) -> f64 {
        if self.mstat == 2.0 { 2.0 } else if self.mstat == 8.0 { 0.0 } else { 1.0 }
    }
    pub fn is_dependent_filer(&self) -> bool {
        self.mstat == 8.0
    }
    pub fn aged_count(&self) -> f64 {
        (self.page >= 65.0) as u8 as f64 + (self.sage >= 65.0) as u8 as f64
    }
    pub fn sepret(&self) -> f64 {
        if self.fs() == Fs::Separate { 2.0 } else { 1.0 }
    }
}

struct Qbi {
    rate: f64,
    start_joint: f64,
    start_other: f64,
    start_sep: Option<f64>,
    end_joint: f64,
    end_other: f64,
    end_sep: Option<f64>,
}

struct Senior {
    amount: f64,
    rate: f64,
    threshold_joint: f64,
    threshold_other: f64,
}

struct SaltPhaseout {
    floor: f64,
    rate: f64,
    threshold: f64,
}

/// Unemployment compensation excluded from income.
enum UiExclusion {
    None,
    PerReturn(f64),
    PerSpouse { amount: f64, agi_limit: f64 },
}

struct SeHoliday {
    threshold: f64,
    flat_addon: f64,
    low_rate: f64,
}

/// The itemized-deduction limitation.
struct Pease {
    threshold: [f64; 4],
    reduction_rate: f64,
    cap_rate: f64,
    phasedown_share: f64,
}

struct PersonalExemption {
    amount: f64,
    phaseout: Option<ExemptionPhaseout>,
}

struct ExemptionPhaseout {
    threshold: [f64; 4],
    rate: f64,
    bracket_size: f64,
    /// Share of the phaseout in force (after 1996).
    phasedown_share: f64,
}

struct ExemptionSurtax {
    threshold: [f64; 4],
    rate: f64,
    cap_share: f64,
}

struct BubbleSurtax {
    threshold: [f64; 4],
    cap: [f64; 4],
    rate: f64,
}

struct TransitionRates {
    dividends_low: f64,
    gains_low: f64,
    dividends_high: f64,
    gains_high: f64,
}

pub struct Amt {
    exemption: [f64; 4],
    phaseout_threshold: [f64; 4],
    phaseout_rate: f64,
    breakpoint: f64,
    rate_below: f64,
    rate_above: f64,
    /// From 1990: the young filer cap and the separate-return addback.
    young: Option<AmtYoung>,
    /// From 1997: preferential rates on gains.
    gains: Option<AmtGains>,
}

struct AmtYoung {
    age: f64,
    addition: f64,
    addback_cap: f64,
    addback_threshold: f64,
    addback_rate: f64,
}

struct AmtGains {
    cg_rate_15_ceiling: [f64; 4],
    top_rate_addition: f64,
}

enum CareRate {
    OnePhase { top: f64, floor: f64, start: f64, step: f64 },
    TwoPhase {
        top: f64,
        mid: f64,
        first_start: f64,
        first_ceiling: f64,
        first_step: f64,
        second_start: f64,
        second_ceiling: f64,
        second_step: f64,
    },
}

struct ChildCare {
    max_persons: f64,
    max_expense: f64,
    refundable: bool,
    rate: CareRate,
}

struct EnhancedCtc {
    young_amount: f64,
    older_amount: f64,
    base_offset: f64,
    cap: [f64; 4],
    low_income_threshold: [f64; 4],
    low_income_rate: f64,
}

struct Actc {
    floor: f64,
    rate: f64,
    max_per_child: Option<f64>,
}

struct Ctc {
    amount_per_child: f64,
    phaseout_threshold: [f64; 4],
    rate_per_1000: f64,
    odc_amount: Option<f64>,
    enhanced: Option<EnhancedCtc>,
    /// `None` before 2001, when only the nonrefundable credit (and a small
    /// additional credit for three or more children) exists.
    actc: Option<Actc>,
    refundable_minimum_children: Option<f64>,
}

#[derive(Clone, Copy)]
pub struct EitcRow {
    pub rate_in: f64,
    pub max_credit: f64,
    pub phaseout_start: f64,
    pub rate_out: f64,
}

struct Elderly {
    rate: f64,
    agi_share: f64,
    base_one: f64,
    base_two: f64,
    threshold_one: f64,
    threshold_two: f64,
}

struct Niit {
    threshold: [f64; 4],
    expense_cap: f64,
    rate: f64,
}

struct SocialSecurity {
    base: [f64; 4],
    width: [f64; 4],
    first_rate: f64,
    second_rate: Option<f64>,
}

enum Rebate {
    None,
    /// 2020: each payment is reduced separately.
    Payments { phaseout_start: [f64; 4], rate: f64, payments: Vec<(f64, f64)> },
    /// 2021: one payment with a hard end to the phaseout.
    Single { phaseout_start: [f64; 4], phaseout_end: [f64; 4], per_adult: f64, per_dependent: f64 },
}

struct MakingWorkPay {
    max_credit: f64,
    phaseout_start: f64,
    income_ceiling: f64,
    rate: f64,
    phaseout_rate: f64,
    benefit_offset: f64,
}

struct Telephone {
    per_exemption: f64,
    max_exemptions: f64,
    base_exemptions: f64,
}

struct CapitalGains {
    rate_0_ceiling: [f64; 4],
    rate_15_ceiling: [f64; 4],
    /// 1987-1996 cap on the rate on gains.
    maximum_rate: Option<f64>,
    /// The 1987 transition ceiling (top of the 28% bracket).
    rate_28_ceiling: Option<[f64; 4]>,
    transition: Option<TransitionRates>,
    /// From 1997 (and 2001-2002 for the first two): the preferential rates.
    rate_0: f64,
    rate_15: f64,
    rate_20: f64,
}

pub struct FedParams {
    pub year: i64,
    pay: PayrollParams,
    brackets: [Brackets; 4],
    std_ded: [f64; 4],
    std_property_cap: Option<f64>,
    aged_std: [f64; 4],
    dep_min: f64,
    dep_add: f64,
    qbi: Option<Qbi>,
    senior: Option<Senior>,
    salt_cap: Option<f64>,
    salt_phaseout: Option<SaltPhaseout>,
    charity_share: f64,
    pease: Option<Pease>,
    nonitemizer_charity: Option<f64>,
    exemption: Option<PersonalExemption>,
    exemption_surtax: Option<ExemptionSurtax>,
    bubble_surtax: Option<BubbleSurtax>,
    ui: UiExclusion,
    se_holiday: Option<SeHoliday>,
    loss_limit: f64,
    cg: CapitalGains,
    amt: Amt,
    ccc: ChildCare,
    ctc: Ctc,
    eitc: [[Option<EitcRow>; 4]; 4],
    eitc_dylim: f64,
    eitc_loss_addback: f64,
    eitc_min_age: Option<f64>,
    eitc_aged_age: f64,
    elderly: Elderly,
    niit: Niit,
    ss: SocialSecurity,
    rebate: Rebate,
    making_work_pay: Option<MakingWorkPay>,
    telephone: Option<Telephone>,
    div_adjustment: f64,
}

pub fn read_eitc(root: &Path, year: i64) -> Result<[[Option<EitcRow>; 4]; 4], String> {
    let path = root.join("national/eitc.csv");
    let text = std::fs::read_to_string(&path).map_err(|e| format!("{}: {e}", path.display()))?;
    let mut rows = [[None; 4]; 4];
    for line in text.lines().skip(1) {
        let f: Vec<&str> = line.split(',').collect();
        if f.len() < 7 || f[0].parse::<i64>().ok() != Some(year) {
            continue;
        }
        let status = params::STATUS_KEYS.iter().position(|k| *k == f[2]).ok_or("unknown filing status in eitc.csv")?;
        let children: usize = f[1].parse().map_err(|_| "bad num_children in eitc.csv")?;
        let parse = |s: &str| s.parse::<f64>().map_err(|_| format!("bad number in eitc.csv: {s}"));
        rows[status][children] =
            Some(EitcRow { rate_in: parse(f[3])?, max_credit: parse(f[4])?, phaseout_start: parse(f[5])?, rate_out: parse(f[6])? });
    }
    Ok(rows)
}

impl FedParams {
    pub fn load(root: &Path, year: i64) -> Result<Self, String> {
        let inc_doc = load(root, "national/income_tax.yaml")?;
        let itm_doc = load(root, "national/itemized.yaml")?;
        let pe_doc = load(root, "national/personal_exemption.yaml")?;
        let cg_doc = load(root, "national/capital_gains.yaml")?;
        let amt_doc = load(root, "national/amt.yaml")?;
        let cr_doc = load(root, "national/credits.yaml")?;
        let em_doc = load(root, "national/eitc_misc.yaml")?;
        let niit_doc = load(root, "national/niit.yaml")?;
        let ss_doc = load(root, "national/social_security.yaml")?;
        let pt_doc = load(root, "national/payroll_tax.yaml")?;
        let adj_doc = load(root, "national/state_adjustments.yaml")?;

        if !(1987..=2025).contains(&year) {
            return Err(format!("law year {year} is not supported by the compiled calculator"));
        }
        let inc = YearParams::new(&inc_doc, year);
        let itm = YearParams::new(&itm_doc, year);
        let pe = YearParams::new(&pe_doc, year);
        let cg = YearParams::new(&cg_doc, year);
        let amt = YearParams::new(&amt_doc, year);
        let cr = YearParams::new(&cr_doc, year);
        let pt = YearParams::new(&pt_doc, year);

        let mut brackets_by_status: Vec<Brackets> = Vec::new();
        for key in params::STATUS_KEYS {
            let table = get(get(&inc_doc, "brackets")?, key)?;
            brackets_by_status.push(Brackets::new(&brackets(params::resolve_year(table, year)?)?));
        }
        let brackets_by_status: [Brackets; 4] = brackets_by_status.try_into().map_err(|_| "brackets")?;

        // Standard deduction: married separate uses half the joint amount.
        let std_table = get(&inc_doc, "standard_deduction")?;
        let std_at = |key: &str| -> Result<f64, String> { num(params::resolve_year(get(std_table, key)?, year)?) };
        let (single, joint, hoh) = (std_at("single")?, std_at("married_joint")?, std_at("head_of_household")?);
        let std_ded = [single, joint, joint / 2.0, hoh];
        let dep = YearParams::new(get(&inc_doc, "dependent_standard_deduction")?, year);

        let qbi = if inc.in_effect("qbi_deduction_rate")? {
            let start = inc.value("qbi_phase_in_start", &[])?;
            let end = inc.value("qbi_phase_in_end", &[])?;
            let opt = |v: &Value| v.get("married_separate").map(num).transpose();
            Some(Qbi {
                rate: inc.f("qbi_deduction_rate")?,
                start_joint: num(get(start, "married_joint")?)?,
                start_other: num(get(start, "unmarried")?)?,
                start_sep: opt(start)?,
                end_joint: num(get(end, "married_joint")?)?,
                end_other: num(get(end, "unmarried")?)?,
                end_sep: opt(end)?,
            })
        } else {
            None
        };
        let senior = match inc.opt("senior_deduction")? {
            Some(s) => Some(Senior {
                amount: num(get(s, "amount")?)?,
                rate: num(get(s, "phaseout_rate")?)?,
                threshold_joint: num(get(get(s, "phaseout_threshold")?, "joint")?)?,
                threshold_other: num(get(get(s, "phaseout_threshold")?, "other")?)?,
            }),
            None => None,
        };
        let salt_phaseout = match itm.opt("salt_phaseout")? {
            Some(s) => Some(SaltPhaseout {
                floor: num(get(s, "floor")?)?,
                rate: num(get(s, "rate")?)?,
                threshold: num(get(s, "threshold")?)?,
            }),
            None => None,
        };
        let pease = match itm.opt("pease_limitation_threshold")? {
            Some(threshold) => Some(Pease {
                threshold: status_direct(threshold)?,
                reduction_rate: itm.f("pease_reduction_rate")?,
                cap_rate: itm.f("pease_cap_rate")?,
                phasedown_share: itm.f("limitation_phasedown_share")?,
            }),
            None => None,
        };
        let ui = match inc.opt("unemployment_exclusion")? {
            Some(v) if v.get("per_return").is_some() => UiExclusion::PerReturn(num(get(v, "per_return")?)?),
            Some(v) if v.get("per_spouse").is_some() => {
                UiExclusion::PerSpouse { amount: num(get(v, "per_spouse")?)?, agi_limit: num(get(v, "agi_limit")?)? }
            }
            Some(_) => return Err("unrecognized unemployment_exclusion".into()),
            None => UiExclusion::None,
        };
        let se_holiday = match pt.opt("se_deduction_holiday")? {
            Some(h) => {
                let wage_base = pt.f("oasdi_wage_base")?;
                let share = num(get(h, "threshold_share")?)?;
                Some(SeHoliday {
                    threshold: share * wage_base,
                    flat_addon: share * num(get(h, "addon_rate")?)? * wage_base,
                    low_rate: num(get(h, "low_rate")?)?,
                })
            }
            None => None,
        };

        // Personal exemptions (through 2017) and their phaseout.
        let exemption = if pe.in_effect("amount")? {
            let phaseout = if pe.in_effect("high_income_phaseout_threshold")? {
                Some(ExemptionPhaseout {
                    threshold: status_direct(pe.value("high_income_phaseout_threshold", &[])?)?,
                    rate: pe.f("phaseout_rate")?,
                    bracket_size: pe.f("phaseout_bracket_size")?,
                    phasedown_share: if year > 1996 { itm.f("limitation_phasedown_share")? } else { 1.0 },
                })
            } else {
                None
            };
            Some(PersonalExemption { amount: pe.f("amount")?, phaseout })
        } else {
            None
        };
        let exemption_surtax = if pe.in_effect("exemption_surtax_threshold")? {
            Some(ExemptionSurtax {
                threshold: status_direct(pe.value("exemption_surtax_threshold", &[])?)?,
                rate: num(get(&inc_doc, "exemption_surtax_rate")?)?,
                cap_share: num(get(&inc_doc, "exemption_surtax_cap_share")?)?,
            })
        } else {
            None
        };
        let bubble_surtax = if inc.in_effect("bubble_surtax_threshold")? {
            Some(BubbleSurtax {
                threshold: status_direct(inc.value("bubble_surtax_threshold", &[])?)?,
                cap: status_direct(inc.value("bubble_surtax_cap", &[])?)?,
                rate: num(get(&inc_doc, "bubble_surtax_rate")?)?,
            })
        } else {
            None
        };

        // Capital gains: which parameters exist depends on the era.
        let preferential = year >= 1997;
        let cg_params = CapitalGains {
            rate_0_ceiling: status_at(get(&cg_doc, "rate_0_ceiling")?, year)?,
            rate_15_ceiling: status_at(get(&cg_doc, "rate_15_ceiling")?, year)?,
            maximum_rate: if cg.in_effect("maximum_rate")? { Some(cg.f("maximum_rate")?) } else { None },
            rate_28_ceiling: if year == 1987 { Some(status_direct(cg.value("rate_28_ceiling", &[])?)?) } else { None },
            transition: match cg.opt("transition_rates")? {
                Some(t) => Some(TransitionRates {
                    dividends_low: num(get(t, "dividends_low")?)?,
                    gains_low: num(get(t, "gains_low")?)?,
                    dividends_high: num(get(t, "dividends_high")?)?,
                    gains_high: num(get(t, "gains_high")?)?,
                }),
                None => None,
            },
            rate_0: if preferential || (2001..=2003).contains(&year) { cg.f("rate_0")? } else { 0.0 },
            rate_15: if preferential || (2001..=2003).contains(&year) { cg.f("rate_15")? } else { 0.0 },
            rate_20: if preferential { cg.f("rate_20")? } else { 0.0 },
        };

        let amt_ex = get(&amt_doc, "exemption")?;
        let amt_params = Amt {
            exemption: status_at(amt_ex, year)?,
            phaseout_threshold: status_at(get(&amt_doc, "exemption_phaseout_threshold")?, year)?,
            phaseout_rate: amt.f("exemption_phaseout_rate")?,
            breakpoint: amt.f("rate_breakpoint")?,
            rate_below: amt.f("rate_below_breakpoint")?,
            rate_above: amt.f("rate_above_breakpoint")?,
            young: if year >= 1990 {
                Some(AmtYoung {
                    age: amt.f("young_filer_age")?,
                    addition: amt.f("young_filer_exemption_addition")?,
                    addback_cap: num(params::resolve_year(get(amt_ex, "married_separate")?, year)?)?,
                    addback_threshold: amt.f("separate_return_addback_threshold")?,
                    addback_rate: amt.f("separate_return_addback_rate")?,
                })
            } else {
                None
            },
            gains: if year >= 1997 {
                Some(AmtGains {
                    cg_rate_15_ceiling: status_at(get(&amt_doc, "cg_rate_15_ceiling")?, year)?,
                    top_rate_addition: amt.f("gains_top_rate_addition")?,
                })
            } else {
                None
            },
        };

        let ccc_p = YearParams::new(get(&cr_doc, "child_care_credit")?, year);
        let rate = ccc_p.value("rate", &[])?;
        let care_rate = if rate.get("second_phase_start").is_some() {
            CareRate::TwoPhase {
                top: num(get(rate, "top_rate")?)?,
                mid: num(get(rate, "mid_rate")?)?,
                first_start: num(get(rate, "first_phase_start")?)?,
                first_ceiling: num(get(rate, "first_phase_ceiling")?)?,
                first_step: num(get(rate, "first_phase_step_amount")?)?,
                second_start: num(get(rate, "second_phase_start")?)?,
                second_ceiling: num(get(rate, "second_phase_ceiling")?)?,
                second_step: num(get(rate, "second_phase_step_amount")?)?,
            }
        } else {
            CareRate::OnePhase {
                top: num(get(rate, "top_rate")?)?,
                floor: num(get(rate, "floor_rate")?)?,
                start: num(get(rate, "phase_start")?)?,
                step: num(get(rate, "step_amount")?)?,
            }
        };
        let ccc = ChildCare {
            max_persons: ccc_p.f("max_qualifying_persons")?,
            max_expense: ccc_p.f("max_expense_per_person")?,
            refundable: ccc_p.value("refundable", &[])?.as_bool().ok_or("refundable must be a boolean")?,
            rate: care_rate,
        };

        let ctc_p = YearParams::new(get(&cr_doc, "child_tax_credit")?, year);
        let odc_p = YearParams::new(get(&cr_doc, "other_dependent_credit")?, year);
        let enhanced = match ctc_p.opt("enhanced")? {
            Some(e) => Some(EnhancedCtc {
                young_amount: num(get(e, "young_child_amount")?)?,
                older_amount: num(get(e, "older_child_amount")?)?,
                base_offset: num(get(e, "base_amount_offset")?)?,
                cap: status_direct(get(e, "enhanced_cap")?)?,
                low_income_threshold: status_direct(get(e, "low_income_phaseout_threshold")?)?,
                low_income_rate: num(get(e, "low_income_phaseout_rate")?)?,
            }),
            None => None,
        };
        let actc = if ctc_p.in_effect("actc_rate")? {
            Some(Actc {
                floor: ctc_p.f("actc_earned_income_floor")?,
                rate: ctc_p.f("actc_rate")?,
                max_per_child: if ctc_p.in_effect("actc_max_refundable_per_child")? {
                    Some(ctc_p.f("actc_max_refundable_per_child")?)
                } else {
                    None
                },
            })
        } else {
            None
        };
        let ctc = Ctc {
            amount_per_child: if ctc_p.in_effect("amount_per_child")? { ctc_p.f("amount_per_child")? } else { 0.0 },
            phaseout_threshold: status_direct(ctc_p.value("phaseout_threshold", &[])?)?,
            rate_per_1000: ctc_p.f("phaseout_rate_per_1000")?,
            odc_amount: if odc_p.in_effect("amount")? { Some(odc_p.f("amount")?) } else { None },
            enhanced,
            refundable_minimum_children: if actc.is_none() && ctc_p.in_effect("refundable_minimum_children")? {
                Some(ctc_p.f("refundable_minimum_children")?)
            } else {
                None
            },
            actc,
        };

        let rebate = match cr.opt("recovery_rebate")? {
            None => Rebate::None,
            Some(r) if r.get("payments").is_some() => Rebate::Payments {
                phaseout_start: status_direct(get(r, "phaseout_start")?)?,
                rate: num(get(r, "phaseout_rate")?)?,
                payments: get(r, "payments")?
                    .as_sequence()
                    .ok_or("payments must be a list")?
                    .iter()
                    .map(|p| Ok((num(get(p, "per_adult")?)?, num(get(p, "per_child")?)?)))
                    .collect::<Result<_, String>>()?,
            },
            Some(r) => Rebate::Single {
                phaseout_start: status_direct(get(r, "phaseout_start")?)?,
                phaseout_end: status_direct(get(r, "phaseout_end")?)?,
                per_adult: num(get(get(r, "payment")?, "per_adult")?)?,
                per_dependent: num(get(get(r, "payment")?, "per_dependent")?)?,
            },
        };
        let making_work_pay = match cr.opt("making_work_pay")? {
            Some(m) => Some(MakingWorkPay {
                max_credit: num(get(m, "max_credit")?)?,
                phaseout_start: num(get(m, "phaseout_start")?)?,
                income_ceiling: num(get(m, "income_ceiling")?)?,
                rate: num(get(m, "rate")?)?,
                phaseout_rate: num(get(m, "phaseout_rate")?)?,
                benefit_offset: num(get(m, "benefit_offset")?)?,
            }),
            None => None,
        };
        let telephone = match cr.opt("telephone_excise_credit")? {
            Some(t) => Some(Telephone {
                per_exemption: num(get(t, "per_exemption")?)?,
                max_exemptions: num(get(t, "max_exemptions")?)?,
                base_exemptions: num(get(t, "base_exemptions")?)?,
            }),
            None => None,
        };

        let em = YearParams::new(&em_doc, year);
        let elderly_doc = get(&inc_doc, "elderly_credit")?;
        let elderly = Elderly {
            rate: num(get(elderly_doc, "rate")?)?,
            agi_share: num(get(elderly_doc, "agi_share")?)?,
            base_one: num(get(get(elderly_doc, "base")?, "one_aged")?)?,
            base_two: num(get(get(elderly_doc, "base")?, "two_aged")?)?,
            threshold_one: num(get(get(elderly_doc, "agi_threshold")?, "one_aged")?)?,
            threshold_two: num(get(get(elderly_doc, "agi_threshold")?, "two_aged")?)?,
        };

        let niit_p = YearParams::new(&niit_doc, year);
        let niit = Niit {
            threshold: status_at(get(&niit_doc, "threshold")?, year)?,
            expense_cap: if niit_p.in_effect("expense_cap")? { niit_p.f("expense_cap")? } else { f64::INFINITY },
            rate: niit_p.f("rate")?,
        };

        let ss_p = YearParams::new(&ss_doc, year);
        let ss = SocialSecurity {
            base: status_direct(get(&ss_doc, "base_amount")?)?,
            width: status_direct(get(&ss_doc, "first_tier_width")?)?,
            first_rate: num(get(&ss_doc, "first_tier_rate")?)?,
            second_rate: if ss_p.in_effect("second_tier_rate")? { Some(ss_p.f("second_tier_rate")?) } else { None },
        };

        Ok(Self {
            year,
            pay: PayrollParams::load(&pt_doc, year)?,
            brackets: brackets_by_status,
            std_ded,
            std_property_cap: if inc.in_effect("standard_deduction_real_property_tax_cap")? {
                Some(inc.f("standard_deduction_real_property_tax_cap")?)
            } else {
                None
            },
            aged_std: status_at(get(&inc_doc, "aged_standard_deduction")?, year)?,
            dep_min: dep.f("minimum")?,
            dep_add: dep.f("earned_income_addition")?,
            qbi,
            senior,
            salt_cap: if itm.in_effect("salt_cap")? { Some(itm.f("salt_cap")?) } else { None },
            salt_phaseout,
            charity_share: itm.f("cash_charity_agi_limit_share")?,
            pease,
            nonitemizer_charity: if itm.in_effect("nonitemizer_charity_deduction")? {
                Some(itm.f("nonitemizer_charity_deduction")?)
            } else {
                None
            },
            exemption,
            exemption_surtax,
            bubble_surtax,
            ui,
            se_holiday,
            loss_limit: num(params::resolve_year(get(&cg_doc, "net_capital_loss_limit")?, year)?)?,
            cg: cg_params,
            amt: amt_params,
            ccc,
            ctc,
            eitc: read_eitc(root, year)?,
            eitc_dylim: if year >= 1996 { num(params::resolve_year(get(&em_doc, "dylim")?, year)?)? } else { 0.0 },
            eitc_loss_addback: if (1997..=2002).contains(&year) { em.f("loss_addback_share")? } else { 0.0 },
            eitc_min_age: if year >= 1994 {
                Some(num(params::resolve_year(get(&em_doc, "childless_minimum_age")?, year)?)?)
            } else {
                None
            },
            eitc_aged_age: num(get(&em_doc, "childless_aged_age")?)?,
            elderly,
            niit,
            ss,
            rebate,
            making_work_pay,
            telephone,
            div_adjustment: num(get(&adj_doc, "household_income_dividend_adjustment")?)?,
        })
    }
}

/// Figures only the pre-1987 federal laws produce, which state calculators of
/// those years read (`pre1987_*` and `credit` in the Python frame).
#[derive(Default, Clone, Copy)]
pub struct Pre87 {
    pub credit: f64,
    pub taxbc: f64,
    pub chcr: f64,
    pub earncr: f64,
    pub almtax: f64,
    pub pretax: f64,
    pub capgn: f64,
    pub capded: f64,
    pub pref: f64,
    pub twoded: f64,
    pub zbr: f64,
    pub amex: f64,
    pub deduc: f64,
    pub gencr: f64,
    pub alminy: f64,
    pub taxinc: f64,
    /// The federal dividend exclusion limit for the filing status (1977-1986).
    pub divexc: f64,
    /// Whether the return itemizes, as the state calculators read it.
    pub itemizes: bool,
}

/// Federal results the state calculators and the outputs read.
#[derive(Default, Clone, Copy)]
pub struct Fed {
    /// The forced itemizing choice (modern law); always false before 1987.
    pub itemizes: bool,
    /// The state income (or sales) tax deduction this pass used.
    pub ded: f64,
    /// The forced itemizing choice, in every era (`FORCE_ITEMIZE`).
    pub forced_itemize: bool,
    pub pre87: Pre87,
    // Modern-law (1987 on) figures states read; zero in earlier years.
    pub standard_deduction: f64,
    pub itemized_deduction: f64,
    pub itemized_before_limit: f64,
    pub deduction_phaseout: f64,
    pub salt_capped: f64,
    pub schedule_tax: f64,
    pub tax_before_credits: f64,
    pub amt: f64,
    pub amt_income: f64,
    pub nonrefundable_credits: f64,
    pub ccc: f64,
    pub ccc_uncapped: f64,
    pub federal_chcr: f64,
    pub odc: f64,
    pub actc: f64,
    pub eitc_before_age_test: f64,
    pub eitc_before_filer_test: f64,
    pub federal_elder: f64,
    pub personal_exemptions: f64,
    pub exemption_phaseout: f64,
    pub senior_deduction: f64,
    pub qbi_deduction: f64,
    pub se_adjustment: f64,
    pub ltg: f64,
    pub making_work_pay: f64,
    pub niit: f64,
    /// Net capital gain in AGI after the per-return loss limit (modern law).
    pub capgn: f64,
    pub agi: f64,
    pub taxable_income: f64,
    pub taxable_unemployment: f64,
    pub taxable_social_security: f64,
    pub earned_income: f64,
    pub regular_tax: f64,
    pub fiitax: f64,
    /// Recovery rebate credit (2020-2021).
    pub cares: f64,
    pub eitc: f64,
    /// Phaseout start, maximum credit and phaseout rate of the earned income credit schedule that applies.
    pub eitc_schedule: Option<(f64, f64, f64)>,
    pub num_children: f64,
    pub payroll: Payroll,
}

pub(crate) fn clip(x: f64, lo: f64, hi: f64) -> f64 {
    x.max(lo).min(hi)
}

/// Taxable Social Security benefits (`ssagi`).
fn taxable_social_security(p: &SocialSecurity, fs: usize, benefits: f64, provisional: f64) -> f64 {
    let over_base = (provisional - p.base[fs]).max(0.0);
    let taxable = match p.second_rate {
        None => p.first_rate * benefits.min(over_base),
        Some(second) => {
            let over_width = (over_base - p.width[fs]).max(0.0);
            let first_tier = (p.first_rate * over_base.min(p.width[fs])).min(p.first_rate * benefits);
            (second * benefits).min(first_tier + second * over_width)
        }
    };
    if benefits > 0.0 { taxable } else { 0.0 }
}

fn qbi_deduction(q: &Qbi, i: &Inp, fs: Fs, taxable: f64, setax: &Payroll, div_f: f64, capgn: f64) -> f64 {
    let (mut start, mut end) = if fs == Fs::Joint { (q.start_joint, q.end_joint) } else { (q.start_other, q.end_other) };
    if fs == Fs::Separate {
        if let Some(s) = q.start_sep {
            start = s;
        }
        if let Some(e) = q.end_sep {
            end = e;
        }
    }
    let qbi_income = i.pbusinc.max(0.0) + i.sbusinc.max(0.0);
    let professional = i.pprofinc.max(0.0) + i.sprofinc.max(0.0);
    let phased_share = 1.0 - ((taxable - start) / (end - start)).max(0.0).min(1.0);
    let sstb_income = i.scorp + if professional > 0.0 { (professional - 0.5 * setax.setax_sstb).max(0.0) } else { 0.0 };
    let sstb = if professional > 0.0 || i.scorp > 0.0 { q.rate * sstb_income * phased_share } else { 0.0 };
    let qbi = if qbi_income > 0.0 { q.rate * (qbi_income - 0.5 * setax.setax_qbi).max(0.0) } else { 0.0 };
    let cap = q.rate * (taxable - (capgn.max(0.0) + div_f)).max(0.0);
    (sstb + qbi).min(cap)
}

pub(crate) fn preferential_rate_tax(
    taxable: f64,
    ltg: f64,
    ceiling_0: f64,
    ceiling_15: f64,
    rate_15: f64,
    rate_20: f64,
    rate_0: f64,
) -> f64 {
    let ordinary = (taxable - ltg).max(0.0);
    let at_0 = clip((taxable.min(ceiling_0) - ordinary).max(0.0), 0.0, ltg);
    let remaining_0 = (ltg - at_0).max(0.0);
    let at_15 = clip((taxable.min(ceiling_15) - ordinary - at_0).max(0.0), 0.0, remaining_0);
    let at_20 = (ltg - at_0 - at_15).max(0.0);
    rate_0 * at_0 + rate_15 * at_15 + rate_20 * at_20
}

/// Federal income tax and the payroll figures for one household.
///
/// `ded` is the state income (or sales) tax deduction fed back from the state
/// calculation; `itemizes` is the forced itemizing choice.
pub fn federal(i: &Inp, ded: f64, itemizes: bool, p: &FedParams) -> Fed {
    let year = p.year;
    let fs = i.fs();
    let fsi = fs as usize;
    let sepret = i.sepret();
    let dependent = i.is_dependent_filer();
    let joint = fs == Fs::Joint;
    let wages = i.pwages + i.swages;

    // ---- payroll and income --------------------------------------------
    let (pay, wage_tax) = payroll(
        &PayrollInputs {
            pwages: i.pwages,
            swages: i.swages,
            psemp: i.psemp,
            ssemp: i.ssemp,
            pbusinc: i.pbusinc,
            pprofinc: i.pprofinc,
            sbusinc: i.sbusinc,
            sprofinc: i.sprofinc,
            fs,
        },
        &p.pay,
    );
    let gross_se = i.psemp + i.ssemp + i.pbusinc + i.pprofinc + i.sbusinc + i.sprofinc;
    let se_agi_deduction = match &p.se_holiday {
        Some(h) => {
            if pay.setax <= h.threshold { h.low_rate * pay.setax } else { 0.5 * pay.setax + h.flat_addon }
        }
        None => 0.5 * pay.setax,
    };
    let earned_income = (wages + gross_se - se_agi_deduction).max(0.0);
    // The deduction for self-employment tax begins in 1990.
    let se_adjustment = if year < 1990 { 0.0 } else { se_agi_deduction };

    let div_f = i.dividends + p.div_adjustment;
    let capgn = (i.stcg + i.ltcg).max(-p.loss_limit / sepret);
    let fullcg = i.stcg + i.ltcg;
    let net_ltcg = i.ltcg.min(fullcg).max(0.0);
    // Qualified dividends join the preferential-rate base from 2003.
    let ltg = if year >= 2003 { net_ltcg + div_f } else { net_ltcg };
    let schedule_e = i.otherprop + i.scorp;
    let income_before_ui = wages + i.intrec + gross_se - se_adjustment + capgn + div_f + i.pensions + schedule_e + i.nonprop;
    let ui_total = i.ui.max(i.pui + i.sui);
    let ui_in_income = match &p.ui {
        UiExclusion::PerReturn(amount) => (ui_total - amount).max(0.0),
        UiExclusion::PerSpouse { .. } => 0.0,
        UiExclusion::None => ui_total,
    };
    let income_before_ss = income_before_ui + ui_in_income;

    let benefits = i.gssi.max(0.0);
    // Tax-exempt interest is not read from the transfers input.
    let mut provisional = income_before_ss + 0.5 * benefits;
    if let UiExclusion::PerSpouse { .. } = p.ui {
        provisional += ui_total;
    }
    let taxable_ss = taxable_social_security(&p.ss, fsi, benefits, provisional);
    let agi_before_ui = income_before_ss + taxable_ss;
    let (taxable_ui, agi) = match &p.ui {
        UiExclusion::PerSpouse { amount, agi_limit } => {
            let exclusion = i.sui.min(*amount) + clip(ui_total - i.sui, 0.0, *amount);
            let taxable = if agi_before_ui < *agi_limit { (ui_total - exclusion).max(0.0) } else { ui_total };
            (taxable, agi_before_ui + taxable)
        }
        _ => (ui_in_income, agi_before_ui),
    };

    // ---- deductions -------------------------------------------------------
    let mut std_ded = p.std_ded[fsi];
    if let Some(cap) = p.std_property_cap {
        let num_filers = if joint { 2.0 } else { 1.0 };
        std_ded += i.proptax.max(0.0).min(cap * num_filers);
    }
    std_ded += p.aged_std[fsi] * i.aged_count();
    if dependent {
        let earnings = wages + (gross_se + i.scorp).max(0.0) + p.dep_add;
        std_ded = std_ded.min(p.dep_min.max(earnings));
    }

    let salt_uncapped = i.proptax + i.otheritem + ded;
    let salt_capped = match p.salt_cap {
        Some(cap) => {
            let mut cap_expr = cap / sepret;
            if let Some(ph) = &p.salt_phaseout {
                cap_expr = (ph.floor / sepret).max(cap_expr - ph.rate * (agi - ph.threshold / sepret).max(0.0));
            }
            salt_uncapped.max(0.0).min(cap_expr)
        }
        None => salt_uncapped.max(0.0),
    };
    let alim50 = p.charity_share * agi.max(0.0);
    let char_cash = alim50.min(i.charity_cash).max(0.0);
    let mut itemized_deduction = salt_capped + i.mortgage + char_cash;
    let itemized_before_limit = itemized_deduction;
    let mut deduction_phaseout = 0.0;
    if let Some(pz) = &p.pease {
        let dlim1 = pz.reduction_rate * (agi - pz.threshold[fsi]).max(0.0);
        let dlim2 = pz.cap_rate * itemized_deduction.max(0.0);
        deduction_phaseout = dlim1.min(dlim2) * pz.phasedown_share;
        itemized_deduction -= deduction_phaseout;
    }
    // Cash charity deduction for nonitemizers: per return in 2020, per taxpayer in 2021.
    let cas = match p.nonitemizer_charity {
        Some(charity) => {
            let num_filers = if joint { 2.0 } else { 1.0 };
            let limit = if year == 2020 { charity / sepret } else { charity * num_filers };
            limit.min(i.charity_cash)
        }
        None => 0.0,
    };

    let mut deduction = if itemizes { itemized_deduction } else { std_ded + cas };
    if dependent {
        let dependent_standard = if itemizes { 0.0 } else { std_ded };
        deduction = (if itemizes { itemized_deduction } else { 0.0 })
            .max(dependent_standard.min(p.dep_min.max(earned_income + p.dep_add)));
    }

    // Personal and dependent exemptions, with the high-income phaseout.
    let (amex, amex_base) = match &p.exemption {
        Some(pe) => {
            let count = 1.0 + i.depx + if joint { 1.0 } else { 0.0 };
            let base = pe.amount * count;
            let amount = match &pe.phaseout {
                None => base,
                Some(ph) => {
                    let ratio = ph.rate * (agi - ph.threshold[fsi]).max(0.0) / (ph.bracket_size / sepret);
                    if year <= 1996 {
                        (base * (1.0 - ratio)).max(0.0)
                    } else {
                        base * (1.0 - ratio.max(0.0).min(1.0) * ph.phasedown_share)
                    }
                }
            };
            (if dependent { 0.0 } else { amount }, base)
        }
        None => (0.0, 0.0),
    };
    let senior = match &p.senior {
        Some(s) => {
            let threshold = if joint { s.threshold_joint } else { s.threshold_other };
            let per_person = (s.amount - s.rate * (agi - threshold).max(0.0)).max(0.0);
            if fs == Fs::Separate || dependent { 0.0 } else { per_person * i.aged_count() }
        }
        None => 0.0,
    };
    let mut taxable_income = (agi - deduction - amex - senior).max(0.0);
    let qbi_ded = match &p.qbi {
        Some(q) => {
            let d = qbi_deduction(q, i, fs, taxable_income, &pay, div_f, capgn);
            taxable_income = (taxable_income - d).max(0.0);
            d
        }
        None => 0.0,
    };

    // ---- regular tax ------------------------------------------------------
    let ltg_capped = ltg.min(taxable_income);
    let ordinary_income = (taxable_income - ltg_capped).max(0.0);
    let table = &p.brackets[fsi];
    let tax_expr = table.tax(ordinary_income);
    let plain_ordinary_tax = table.tax(taxable_income);
    let cgp = &p.cg;
    let (ceiling_0, ceiling_15) = (cgp.rate_0_ceiling[fsi], cgp.rate_15_ceiling[fsi]);
    let preferential = if year == 1987 {
        // The 1987 transition caps gains above the 28% bracket ceiling.
        let ceiling_28 = cgp.rate_28_ceiling.map_or(0.0, |c| c[fsi]);
        let max_rate = cgp.maximum_rate.unwrap_or(0.0);
        let total = if taxable_income < ceiling_28 {
            plain_ordinary_tax
        } else if ordinary_income <= ceiling_28 {
            table.tax(ceiling_28) + max_rate * (taxable_income - ceiling_28)
        } else {
            tax_expr + max_rate * ltg_capped
        };
        total - tax_expr
    } else if year <= 1990 {
        // Capital gains are taxed as ordinary income in 1988-1990.
        plain_ordinary_tax - tax_expr
    } else if year <= 1996 {
        let max_rate = cgp.maximum_rate.unwrap_or(0.0);
        let taxin3 = ceiling_0.max(taxable_income - ltg_capped);
        if year <= 1993 {
            let excess = taxable_income - taxin3;
            let alt_tax = table.tax(taxin3) + max_rate * excess;
            let gated = ltg_capped > 0.0 && taxable_income > ceiling_15;
            if gated { alt_tax - tax_expr } else { plain_ordinary_tax - tax_expr }
        } else {
            let excess = (taxable_income - taxin3).max(0.0);
            let alt_tax = table.tax(taxin3) + max_rate * excess;
            if ltg_capped > 0.0 { alt_tax - tax_expr } else { 0.0 }
        }
    } else if year <= 2000 {
        preferential_rate_tax(taxable_income, ltg_capped, ceiling_0, ceiling_15, cgp.rate_15, cgp.rate_20, cgp.rate_0)
    } else if year <= 2002 {
        // The 2001-2002 low-rate room is not capped by available gains.
        let low_room = (ceiling_0 - ordinary_income).max(0.0);
        let above_room = (ltg_capped - low_room).max(0.0);
        cgp.rate_0 * low_room + cgp.rate_15 * above_room
    } else if let Some(t) = &cgp.transition {
        // Dividends use the new rates before gains consume the remaining room at the old ones.
        let low_room = (taxable_income.min(ceiling_0) - ordinary_income).max(0.0);
        let dividends_in_low = low_room.min(div_f);
        let sch5 = t.dividends_low * dividends_in_low;
        let sch10 = t.gains_low * (low_room - dividends_in_low);
        let above_room = (ltg_capped - low_room).max(0.0);
        let dividends_in_high = above_room.min(div_f - dividends_in_low);
        let sch15 = t.dividends_high * dividends_in_high;
        let sch20 = t.gains_high * (above_room - dividends_in_high);
        sch5 + sch10 + sch15 + sch20
    } else {
        preferential_rate_tax(taxable_income, ltg_capped, ceiling_0, ceiling_15, cgp.rate_15, cgp.rate_20, cgp.rate_0)
    };
    let mut regular_tax = plain_ordinary_tax.min(tax_expr + preferential);
    // The schedule tax is the regular tax before the 1988-1996 surtaxes (ordinary rates from 1991).
    let schedule_tax = if year <= 1990 { regular_tax } else { plain_ordinary_tax };
    if let Some(es) = &p.exemption_surtax {
        regular_tax += (es.rate * (taxable_income - es.threshold[fsi]).max(0.0)).min(es.cap_share * amex);
    }
    if let Some(b) = &p.bubble_surtax {
        regular_tax += (b.rate * (taxable_income - b.threshold[fsi]).max(0.0)).min(b.cap[fsi]);
    }
    let num_children = i.dep18.max(0.0).min(3.0);

    // ---- alternative minimum tax -------------------------------------------
    let a = &p.amt;
    let older = i.page.max(i.sage);
    let mut amt_income = agi - if itemizes { i.mortgage } else { 0.0 };
    let exemption_cap = match &a.young {
        Some(y) => {
            // The cap applies to children subject to the tax on unearned income.
            let young = (if older > 0.0 { older < y.age } else { dependent }) && dependent;
            if young { Some(earned_income + y.addition) } else { None }
        }
        None => None,
    };
    amt_income = amt_income.max(0.0);
    amt_income = (amt_income - qbi_ded).max(0.0);
    if let (Some(y), true) = (&a.young, sepret == 2.0) {
        amt_income += y.addback_cap.min(y.addback_rate * (amt_income - y.addback_threshold).max(0.0));
    }
    let mut exemption_after = (a.exemption[fsi] - a.phaseout_rate * (amt_income - a.phaseout_threshold[fsi]).max(0.0)).max(0.0);
    if let Some(cap) = exemption_cap {
        exemption_after = exemption_after.min(cap.max(0.0));
    }
    let amt_base = (amt_income - exemption_after).max(0.0);
    let breakpoint = a.breakpoint / sepret;
    let backout = (a.rate_above - a.rate_below) * a.breakpoint / sepret;
    let tentative = |base: f64| if base <= breakpoint { base * a.rate_below } else { base * a.rate_above - backout };
    let tentative_minimum_tax = match &a.gains {
        None => tentative(amt_base),
        Some(g) => {
            let ltg_amt = ltg.min(amt_base);
            let ordinary_amt = (amt_base - ltg_amt).max(0.0);
            let regular_ordinary = (taxable_income - ltg).max(0.0);
            let zero_room = (ceiling_0 - regular_ordinary).max(0.0);
            let zero_amount = zero_room.min(ltg_amt);
            let remaining_zero = ltg_amt - zero_amount;
            let fifteen_room = (g.cg_rate_15_ceiling[fsi] - regular_ordinary - zero_room).max(0.0);
            let top_slice = (remaining_zero - fifteen_room).max(0.0);
            let tentative_ltg = cgp.rate_0 * zero_amount + cgp.rate_15 * remaining_zero + g.top_rate_addition * top_slice;
            tentative(ordinary_amt) + tentative_ltg
        }
    };
    let amt_tax = (tentative_minimum_tax - regular_tax).max(0.0);
    let tax_before_credits = regular_tax + amt_tax;

    // ---- credits ------------------------------------------------------------
    let c = &p.ccc;
    let ccc_rate = match &c.rate {
        CareRate::OnePhase { top, floor, start, step } => (top - ((agi - start) / step).max(0.0) * 0.01).max(*floor),
        CareRate::TwoPhase { top, mid, first_start, first_ceiling, first_step, second_start, second_ceiling, second_step } => {
            if agi <= *first_start {
                *top
            } else if agi < *first_ceiling {
                top - ((agi - first_start) / first_step).max(0.0) * 0.01
            } else if agi <= *second_start {
                *mid
            } else if agi < *second_ceiling {
                mid - ((agi - second_start) / second_step).max(0.0) * 0.01
            } else {
                0.0
            }
        }
    };
    let num_qualifying = i.dep13.max(0.0).min(c.max_persons);
    let qualifying_expense = clip(i.childcare, 0.0, num_qualifying * c.max_expense);
    let earned_cap = if i.mstat == 2.0 { i.pwages.min(i.swages) } else { wages };
    let ccc_expense = qualifying_expense.min(earned_cap).max(0.0);
    let ccc_amount = ccc_rate * ccc_expense;
    let ccc = if c.refundable { ccc_amount.max(0.0) } else { ccc_amount.min(tax_before_credits.max(0.0)) };

    // Earned income credit.
    let row = p.eitc[fsi][num_children as usize];
    let phaseout_income = if year <= 2002 {
        // Capital and Schedule E losses (and from 1997 part of a business loss) do not lower the phaseout income.
        let business = gross_se + i.scorp;
        agi - capgn.min(0.0) - schedule_e.min(0.0) - p.eitc_loss_addback * business.min(0.0)
    } else {
        agi
    };
    let eitc_ordinary = match row {
        Some(r) => {
            let phase_in = clip(earned_income * r.rate_in, 0.0, r.max_credit);
            let phaseout = earned_income.max(phaseout_income);
            let reduction = ((phaseout - r.phaseout_start) * r.rate_out).max(0.0);
            let ceiling = (r.max_credit - reduction).max(0.0);
            phase_in.min(ceiling).max(0.0)
        }
        None => 0.0,
    };
    // The disqualified-investment-income test begins in 1996.
    let eitc_reduction = if year < 1996 {
        0.0
    } else {
        let disqy = capgn.max(0.0) + div_f + i.intrec + i.otherprop.max(0.0);
        (disqy - p.eitc_dylim).max(0.0)
    };
    let mut eitc = (eitc_ordinary - eitc_reduction).max(0.0);
    let eitc_before_filer_test = eitc;
    let childless = num_children == 0.0;
    if dependent || (year != 2021 && childless && i.aged_count() >= i.taxpayer_count()) {
        eitc = 0.0;
    }
    let eitc_before_age_test = eitc;
    if let Some(min_age) = p.eitc_min_age {
        let younger = i.page.min(i.sage);
        let too_young = |age: f64| age > 0.0 && age < min_age;
        let ineligible = if year == 2021 {
            too_young(older) || too_young(younger)
        } else {
            too_young(older) || (older > p.eitc_aged_age && too_young(younger))
        };
        if childless && ineligible {
            eitc = 0.0;
        }
    }

    // Credit for the elderly.
    let e = &p.elderly;
    let aged = i.aged_count();
    let one_aged = aged == 1.0 && sepret != 2.0;
    let single_threshold = fs != Fs::Joint && sepret != 2.0;
    let threshold = if single_threshold { e.threshold_one } else { e.threshold_two / sepret };
    let base = if one_aged { e.base_one } else { e.base_two / sepret };
    let excess_agi = (agi - threshold).max(0.0);
    let nontaxable = i.gssi - taxable_ss;
    let elderly_raw =
        if aged > 0.0 { e.rate * (base - (e.agi_share * excess_agi + nontaxable)).max(0.0) } else { 0.0 };

    // Child tax credit, other dependent credit and the refundable part.
    let t = &p.ctc;
    let ideps = i.dep17;
    let odc_base = match t.odc_amount {
        Some(amount) => amount * (i.depx - ideps).max(0.0),
        None => 0.0,
    };
    let ctc_before = match &t.enhanced {
        Some(en) => {
            let young_children = i.dep6.min(ideps);
            let ccrmax = young_children * en.young_amount + (ideps - young_children) * en.older_amount;
            let enhanced_amount = (ccrmax - ideps * en.base_offset).min(en.cap[fsi]);
            let low_income_reduction = (agi - en.low_income_threshold[fsi]).max(0.0) * en.low_income_rate;
            ccrmax - enhanced_amount.min(low_income_reduction)
        }
        None => t.amount_per_child * ideps,
    };
    let combined_base = ctc_before + odc_base;
    let reduction = ((agi - t.phaseout_threshold[fsi]).max(0.0) / 1000.0) * t.rate_per_1000;
    let combined_after = (combined_base - reduction).max(0.0);
    let (odc, actc) = if t.enhanced.is_some() {
        // Fully refundable.
        (0.0, combined_after)
    } else {
        let remaining_after_ccc = (tax_before_credits - ccc - elderly_raw).max(0.0);
        let nonrefundable = combined_after.min(remaining_after_ccc);
        let unused = combined_after - nonrefundable;
        let actc = match &t.actc {
            Some(ac) => {
                let formula = ac.rate * (earned_income.max(0.0) - ac.floor).max(0.0);
                let cap = match ac.max_per_child {
                    Some(max) => unused.min(max * ideps),
                    None => unused,
                };
                cap.min(formula).max(0.0)
            }
            None => match t.refundable_minimum_children {
                // With enough children the unused credit is refundable up to the
                // employee share of payroll tax beyond the earned income credit.
                Some(minimum) if ideps >= minimum => {
                    let limit = (0.5 * (wage_tax + pay.setax) - eitc).max(0.0);
                    unused.min(limit)
                }
                _ => 0.0,
            },
        };
        (nonrefundable, actc)
    };

    // ---- net tax --------------------------------------------------------------
    let n = &p.niit;
    let nii = (i.intrec + div_f + capgn + i.otherprop).max(0.0);
    let investment_share = if agi > 0.0 { clip(nii / agi, 0.0, 1.0) } else { 0.0 };
    let expense = clip(ded, 0.0, n.expense_cap) * investment_share;
    let net_after = (nii - expense).max(0.0);
    let excess = (agi - n.threshold[fsi]).max(0.0);
    let niit = if agi > n.threshold[fsi] { n.rate * net_after.min(excess) } else { 0.0 };
    let elderly_room = if year == 2021 { tax_before_credits } else { (tax_before_credits - ccc).max(0.0) };
    let elderly_credit = elderly_raw.min(elderly_room + niit);

    // Recovery rebate credits (economic impact payments); none for dependent filers.
    let adults = if joint { 2.0 } else { 1.0 };
    let cares = if dependent {
        0.0
    } else {
        match &p.rebate {
            Rebate::None => 0.0,
            Rebate::Payments { phaseout_start, rate, payments } => {
                let reduction = (rate * (agi - phaseout_start[fsi])).max(0.0);
                let mut total = 0.0;
                for (per_adult, per_child) in payments {
                    let base = per_adult * adults + per_child * i.dep13;
                    total += (base - reduction).max(0.0);
                }
                total
            }
            Rebate::Single { phaseout_start, phaseout_end, per_adult, per_dependent } => {
                let (start, end) = (phaseout_start[fsi], phaseout_end[fsi]);
                let base = per_adult * adults + per_dependent * i.depx;
                let payment = if agi >= end {
                    0.0
                } else if agi > start {
                    base * (end - agi) / (end - start)
                } else {
                    base
                };
                payment.max(0.0)
            }
        }
    };
    // Making Work Pay credit, per taxpayer.
    let making_work_pay = match &p.making_work_pay {
        Some(m) => {
            let max_credit = m.max_credit * adults;
            let phaseout_start = m.phaseout_start * adults;
            let ceiling = m.income_ceiling * adults;
            let phase_in = max_credit.min(m.rate * earned_income);
            let phaseout = m.phaseout_rate * (agi - phaseout_start).max(0.0);
            let credit = if earned_income > 0.0 && agi < ceiling && !dependent {
                (phase_in - phaseout).max(0.0)
            } else {
                0.0
            };
            if i.gssi > 0.0 {
                (credit - i.gssi.min(m.benefit_offset * adults)).max(0.0)
            } else {
                credit
            }
        }
        None => 0.0,
    };
    // Telephone excise tax refund.
    let telephone_excise_credit = match &p.telephone {
        Some(t) => {
            let exemptions = if dependent { 0.0 } else { 1.0 + i.depx + if joint { 1.0 } else { 0.0 } };
            if exemptions > 0.0 { t.per_exemption * (exemptions.min(t.max_exemptions) + t.base_exemptions) } else { 0.0 }
        }
        None => 0.0,
    };
    let nonrefundable_credits = if year < 1998 {
        ccc + elderly_credit
    } else if year == 2021 {
        elderly_credit
    } else {
        ccc + elderly_credit + odc
    };
    let fiitax = tax_before_credits - odc - actc - ccc - elderly_credit - eitc - cares - making_work_pay
        - telephone_excise_credit
        + niit;

    Fed {
        itemizes,
        ded,
        forced_itemize: itemizes,
        pre87: Pre87::default(),
        standard_deduction: std_ded,
        itemized_deduction,
        itemized_before_limit,
        deduction_phaseout,
        salt_capped,
        schedule_tax,
        tax_before_credits,
        amt: amt_tax,
        amt_income,
        nonrefundable_credits,
        ccc,
        ccc_uncapped: ccc_amount.max(0.0),
        federal_chcr: ccc,
        odc,
        actc,
        eitc_before_age_test,
        eitc_before_filer_test,
        federal_elder: elderly_raw,
        personal_exemptions: amex,
        exemption_phaseout: amex_base - amex,
        senior_deduction: senior,
        qbi_deduction: qbi_ded,
        se_adjustment,
        ltg,
        making_work_pay,
        niit,
        capgn,
        agi,
        taxable_income,
        taxable_unemployment: taxable_ui,
        taxable_social_security: taxable_ss,
        earned_income,
        regular_tax,
        fiitax,
        cares,
        eitc,
        eitc_schedule: row.map(|r| (r.phaseout_start, r.max_credit, r.rate_out)),
        num_children,
        payroll: pay,
    }
}


use crate::federal_law60::{law60, Law60Params};
pub use crate::federal_pre1987::{pre1987, Pre87Params};

enum Era {
    Law60(Law60Params),
    Pre87(Pre87Params),
    Modern(FedParams),
}

/// The federal calculator for one law year: the era's income tax plus payroll tax.
pub struct Federal {
    era: Era,
    pay: PayrollParams,
}

impl Federal {
    pub fn load(root: &Path, year: i64) -> Result<Self, String> {
        let era = if year >= 1987 {
            Era::Modern(FedParams::load(root, year)?)
        } else if year >= 1977 {
            Era::Pre87(Pre87Params::load(root, year)?)
        } else {
            Era::Law60(Law60Params::load(root, year)?)
        };
        let pt_doc = load(root, "national/payroll_tax.yaml")?;
        Ok(Self { era, pay: PayrollParams::load(&pt_doc, year)? })
    }

    /// Federal income tax and payroll tax. `ded` is the state tax deduction fed back
    /// from the state calculation and `itemizes` the forced itemizing choice.
    pub fn run(&self, i: &Inp, ded: f64, itemizes: bool) -> Fed {
        let mut fed = match &self.era {
            Era::Modern(p) => {
                let mut fed = federal(i, ded, itemizes, p);
                fed.ded = ded;
                return fed;
            }
            Era::Pre87(p) => pre1987(i, ded, itemizes, p),
            Era::Law60(p) => law60(i, p),
        };
        fed.ded = ded;
        fed.forced_itemize = itemizes;
        fed.federal_chcr = fed.pre87.chcr;
        let inputs = PayrollInputs {
            pwages: i.pwages,
            swages: i.swages,
            psemp: i.psemp,
            ssemp: i.ssemp,
            pbusinc: i.pbusinc,
            pprofinc: i.pprofinc,
            sbusinc: i.sbusinc,
            sprofinc: i.sprofinc,
            fs: i.fs(),
        };
        fed.payroll = payroll(&inputs, &self.pay).0;
        fed
    }
}
