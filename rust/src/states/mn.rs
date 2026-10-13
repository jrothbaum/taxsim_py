//! Minnesota income tax (port of `calculators.states.mn`).

use std::path::Path;

use serde_yaml::Value;

use crate::common::Brackets;
use crate::federal::{Fed, Inp};
use crate::params::{self, get, load, num, resolve_year, status_direct, Fs, YearParams};
use crate::states::{
    dividend_adjustment, federal_exemption_count, household_income, law_year_is_exact, unemployment_total, Interp,
};

fn scaled(rows: &[Vec<f64>], factor: f64) -> Brackets {
    Brackets::new(&rows.iter().map(|r| vec![r[0] * factor, r[1]]).collect::<Vec<_>>())
}

fn pairs(v: &Value) -> Result<Interp, String> {
    Ok(Interp::new(&params::brackets(v)?))
}

fn list(v: &Value) -> Result<Vec<f64>, String> {
    v.as_sequence().ok_or("expected a list")?.iter().map(num).collect()
}

/// Select a value by Minnesota filer class; married separate uses the married value.
fn by_filer(fs: Fs, single: f64, married: f64, hoh: f64) -> f64 {
    match fs {
        Fs::Single => single,
        Fs::Joint | Fs::Separate => married,
        Fs::Hoh => hoh,
    }
}

enum Tables {
    Flat(Brackets),
    Pair { single_a: Brackets, single_b: Brackets, joint_a: Brackets, joint_b: Brackets },
    Classes { single: Brackets, joint: Brackets, hoh: Brackets },
}

struct Phaseout22 {
    low_rate: f64,
    high_rate: f64,
    alternate_rate: f64,
    alternate_applies: bool,
    alternate_income: f64,
    step_fraction: f64,
    low: [f64; 4],
    high: [f64; 4],
    exemption_start: [f64; 4],
    exemption_step: [f64; 4],
}

struct Aged {
    first_step: Interp,
    first_step_top_base: f64,
    second_step_share: Option<f64>,
}

struct Ptc {
    income_limit: f64,
    threshold: Option<Interp>,
    threshold_with_dependents: Option<Interp>,
    threshold_no_dependents: Option<Interp>,
    copay: Option<Interp>,
    copay_aged: Option<Interp>,
    maximum_steps: Vec<(f64, f64)>,
    maximum_mid_start: f64,
    maximum_mid_end: f64,
    maximum_mid_base: f64,
    maximum_mid_rate: f64,
    maximum_top_base: f64,
    maximum_top_rate: f64,
    maximum_top_start: f64,
    maximum_flat: f64,
    maximum: f64,
    aged_income_reduction: f64,
    first_step_table_limit: f64,
    first_step: Option<Interp>,
    first_step_top_base: f64,
    first_step_top_start: f64,
    first_step_top_rate: f64,
    final_limits: Vec<[f64; 3]>,
    second_step_share: f64,
    aged: Option<Aged>,
    rent_share: f64,
}

struct Marriage {
    se_share: f64,
    se_tax_share: f64,
    standard_share: f64,
    lower_earner_deduction: f64,
    exemption_amount: f64,
    federal_joint_standard: f64,
    min_taxable: f64,
    min_earnings: f64,
    maximum: f64,
}

struct Homemaker {
    amount: f64,
    earnings_limit: f64,
    agi_limit: f64,
}

struct LowIncome {
    rate: f64,
    agi_limit: f64,
    thresholds: Vec<f64>,
}

struct Pieces {
    no_child: (f64, f64, f64, f64, f64, f64, f64),
    one: (Vec<[f64; 3]>, f64),
    two: (Vec<[f64; 3]>, f64),
}

struct Children {
    break_: [f64; 4],
    break_4_joint: Option<f64>,
    rate: [f64; 3],
    maximum: [f64; 2],
}

struct NoChild {
    phase_in_end: f64,
    phase_out_start: f64,
    phase_out_start_joint: Option<f64>,
    rate: f64,
    maximum: f64,
}

struct Kind {
    maximum: f64,
    phase_in_end: f64,
    phase_out_start: f64,
    phase_out_start_joint: f64,
    phase_in_rate: f64,
    phase_out_rate: f64,
}

struct Schedule {
    no_child: Kind,
    one: Kind,
    two: Kind,
    three: Option<Kind>,
}

enum Working {
    None,
    Statutory2022 { earnings_maximum: Vec<f64>, phase_in_rate: Vec<f64>, threshold_joint: Vec<f64>, threshold_other: Vec<f64>, phase_out_rate: Vec<f64> },
    Statutory { child_amount: f64, phase_in_rate: f64, phase_out_rate: f64, phase_in_end: f64, phase_out_joint: f64, phase_out_other: f64 },
    FederalShare(f64),
    Pieces(Pieces),
    Tables { no_child: NoChild, one: Children, two: Children },
    Schedule(Schedule),
}

struct WorkingCredit {
    kind: Working,
    min_federal_eitc: Option<f64>,
}

struct ChildCareExpense {
    cap: f64,
    rate_top: f64,
    rate_floor: f64,
    rate_start: f64,
    rate_step: f64,
    rate_step_decrement: f64,
}

struct Refund {
    owner_limit: f64,
    inflation: f64,
    clawback: Option<(f64, f64, f64, f64, f64, f64)>,
    max_table: Option<Interp>,
    threshold: Interp,
    copay: Interp,
    renter_limit: f64,
    renter_inflation: f64,
    renter_rent_share: f64,
    renter_max: Interp,
    renter_threshold: Interp,
    renter_copay: Interp,
    dependent_subtraction: Vec<f64>,
    aged_subtraction: Option<Vec<f64>>,
}

pub struct MnParams {
    year: i64,
    loss_limit: f64,
    tables: Tables,
    bracket_factor: f64,
    surtax: Option<f64>,
    charity_addback: Option<f64>,
    gains_addback_rate: Option<f64>,
    pension: Option<(f64, f64)>,
    federal_tax: Option<(f64, f64)>,
    phaseout_base: f64,
    phaseout_inflation: f64,
    phaseout_multiplier: [f64; 4],
    exemption_amount: f64,
    exemption_rate: f64,
    exemption_step: f64,
    itemized_rate: f64,
    itemized_max_share: f64,
    d22: Option<Phaseout22>,
    std_cap: f64,
    std_percent: f64,
    married_reduction: Option<f64>,
    property_cap: f64,
    std_by_status: Option<([f64; 4], [f64; 4])>,
    std_table: [f64; 3],
    std_aged: [f64; 2],
    dependent_floor: f64,
    dependent_addition: f64,
    salt_cap: f64,
    m1m: Option<(f64, f64)>,
    elderly: Option<([Vec<f64>; 3], f64)>,
    charity_subtraction: Option<(f64, f64)>,
    ss_subtraction: Option<(f64, f64, f64, f64, f64, f64)>,
    amt: Option<(f64, f64, [f64; 3], [f64; 3], f64)>,
    personal_credit: f64,
    personal_credit_aged: Option<(f64, f64)>,
    homemaker: Option<Homemaker>,
    elderly_credit_share: Option<f64>,
    low_income: Option<LowIncome>,
    marriage: Option<Marriage>,
    working: WorkingCredit,
    cc_limit: [f64; 2],
    cc_max: [f64; 2],
    cc_start: f64,
    cc_expense: Option<ChildCareExpense>,
    cc_share: f64,
    cc_phaseout_rate: f64,
    refund: Option<Refund>,
    ptc: Option<Ptc>,
    motor_fuels: Option<([f64; 3], f64)>,
    dividend_adjustment: f64,
}

fn kind(v: &Value, y: i64) -> Result<Kind, String> {
    let s = |name: &str| -> Result<f64, String> { num(resolve_year(get(v, name)?, y)?) };
    Ok(Kind {
        maximum: s("maximum")?,
        phase_in_end: s("phase_in_end")?,
        phase_out_start: s("phase_out_start")?,
        phase_out_start_joint: s("phase_out_start_joint")?,
        phase_in_rate: s("phase_in_rate")?,
        phase_out_rate: s("phase_out_rate")?,
    })
}

impl MnParams {
    pub fn load(root: &Path, year: i64) -> Result<Self, String> {
        let doc = load(root, "states/mn/income_tax.yaml")?;
        law_year_is_exact(root, &doc, year)?;
        let capital_gains = load(root, "national/capital_gains.yaml")?;
        let federal_tax = load(root, "national/income_tax.yaml")?;
        let p = YearParams::new(&doc, year);
        let y = year;
        let opt = |key: &str| -> Result<Option<f64>, String> { Ok(if p.in_effect(key)? { Some(p.f(key)?) } else { None }) };
        let sub = |section: &str, name: &str| -> Result<f64, String> { num(resolve_year(get(get(&doc, section)?, name)?, y)?) };

        let bt = p.value("brackets", &[])?;
        let factor = p.f("bracket_inflation")?;
        let tables = if y <= 1984 {
            Tables::Flat(scaled(&params::brackets(bt)?, factor))
        } else if y <= 1986 {
            let mk = |key: &str| -> Result<Brackets, String> { Ok(scaled(&params::brackets(get(bt, key)?)?, factor)) };
            Tables::Pair { single_a: mk("single_a")?, single_b: mk("single_b")?, joint_a: mk("joint_a")?, joint_b: mk("joint_b")? }
        } else {
            let mk = |key: &str| -> Result<Brackets, String> { Ok(scaled(&params::brackets(get(bt, key)?)?, factor)) };
            Tables::Classes { single: mk("single")?, joint: mk("joint")?, hoh: mk("head_of_household")? }
        };
        let phaseout22 = |year: i64| -> Result<Phaseout22, String> {
            let d = YearParams::new(&doc, year).value("phaseout_schedule", &[])?;
            Ok(Phaseout22 {
                low_rate: num(get(d, "low_rate")?)?,
                high_rate: num(get(d, "high_rate")?)?,
                alternate_rate: num(get(d, "alternate_rate")?)?,
                alternate_applies: get(d, "alternate_applies")?.as_bool().ok_or("expected a boolean")?,
                alternate_income: num(get(d, "alternate_income")?)?,
                step_fraction: num(get(d, "step_fraction")?)?,
                low: status_direct(get(d, "low")?)?,
                high: status_direct(get(d, "high")?)?,
                exemption_start: status_direct(get(d, "exemption_start")?)?,
                exemption_step: status_direct(get(d, "exemption_step")?)?,
            })
        };
        let std_table = if y >= 2018 && p.in_effect("standard_deduction_by_status")? {
            [0.0; 3]
        } else if y >= 1988 {
            [sub("standard_deduction_table", "single")?, sub("standard_deduction_table", "married_joint")?, sub("standard_deduction_table", "head_of_household")?]
        } else {
            [0.0; 3]
        };
        let std_aged = if y >= 1988 && !(y >= 2018 && p.in_effect("standard_deduction_by_status")?) {
            [sub("standard_deduction_aged_addition", "single")?, sub("standard_deduction_aged_addition", "married")?]
        } else {
            [0.0; 2]
        };
        let elderly = match p.opt("elderly_subtraction")? {
            Some(e) => Some((
                [list(get(e, "reduction_start")?)?, list(get(e, "amount")?)?, list(get(e, "income_limit")?)?],
                num(get(&doc, "elderly_subtraction_reduction_rate")?)?,
            )),
            None => None,
        };
        let ss_subtraction = if y >= 2017 {
            let t = get(&doc, "social_security_subtraction_threshold")?;
            let m = get(&doc, "social_security_subtraction_max")?;
            Some((
                num(resolve_year(get(t, "single")?, y)?)?,
                num(resolve_year(get(t, "married")?, y)?)?,
                num(resolve_year(get(m, "single")?, y)?)?,
                num(resolve_year(get(m, "married")?, y)?)?,
                num(get(&doc, "social_security_subtraction_benefit_share")?)?,
                num(get(&doc, "social_security_subtraction_phaseout_rate")?)?,
            ))
        } else {
            None
        };
        let amt = if y >= 2001 {
            let ph = get(&doc, "amt_phaseout_start")?;
            Some((
                p.f("amt_rate")?,
                p.f("amt_charity_floor")?,
                [sub("amt_exclusion", "single")?, sub("amt_exclusion", "married")?, sub("amt_exclusion", "head_of_household")?],
                [num(get(ph, "single")?)?, num(get(ph, "married")?)?, num(get(ph, "head_of_household")?)?],
                p.f("amt_phaseout_rate")?,
            ))
        } else {
            None
        };
        let homemaker = match p.opt("homemaker_credit")? {
            Some(h) => Some(Homemaker { amount: num(get(h, "amount")?)?, earnings_limit: num(get(h, "earnings_limit")?)?, agi_limit: num(get(h, "agi_limit")?)? }),
            None => None,
        };
        let low_income = if p.in_effect("low_income_credit_rate")? {
            Some(LowIncome { rate: p.f("low_income_credit_rate")?, agi_limit: p.f("low_income_credit_agi_limit")?, thresholds: list(p.value("low_income_credit_threshold", &[])?)? })
        } else {
            None
        };
        let marriage = if y >= 1999 {
            Some(Marriage {
                se_share: p.f("marriage_credit_self_employment_share")?,
                se_tax_share: p.f("marriage_credit_self_employment_tax_share")?,
                standard_share: if y <= 2017 { p.f("marriage_credit_standard_deduction_share")? } else { 0.0 },
                lower_earner_deduction: if y > 2017 { p.f("marriage_credit_lower_earner_deduction")? } else { 0.0 },
                exemption_amount: if y <= 2017 { p.f("exemption_amount")? } else { 0.0 },
                federal_joint_standard: if y <= 2017 { p.f("federal_joint_standard_deduction")? } else { 0.0 },
                min_taxable: p.f("marriage_credit_min_taxable_income")?,
                min_earnings: p.f("marriage_credit_min_earnings")?,
                maximum: p.f("marriage_credit_max")?,
            })
        } else {
            None
        };
        let working = if y >= 2022 {
            let c = get(&doc, "child_and_working_family_credit")?;
            if y == 2022 {
                let pre = get(c, "pre_cwfc")?;
                Working::Statutory2022 {
                    earnings_maximum: list(get(pre, "earnings_maximum")?)?,
                    phase_in_rate: list(get(pre, "phase_in_rate")?)?,
                    threshold_joint: list(get(pre, "phase_out_threshold_joint")?)?,
                    threshold_other: list(get(pre, "phase_out_threshold_other")?)?,
                    phase_out_rate: list(get(pre, "phase_out_rate")?)?,
                }
            } else {
                let d = resolve_year(get(c, "by_year")?, y)?;
                Working::Statutory {
                    child_amount: num(get(c, "child_amount")?)?,
                    phase_in_rate: num(get(c, "phase_in_rate")?)?,
                    phase_out_rate: num(get(c, "phase_out_rate")?)?,
                    phase_in_end: num(get(d, "phase_in_end")?)?,
                    phase_out_joint: num(get(d, "phase_out_joint")?)?,
                    phase_out_other: num(get(d, "phase_out_other")?)?,
                }
            }
        } else if y < 1991 {
            Working::None
        } else if p.in_effect("working_family_credit_federal_share")? {
            Working::FederalShare(p.f("working_family_credit_federal_share")?)
        } else if p.in_effect("working_family_credit_pieces")? {
            let c = p.value("working_family_credit_pieces", &[])?;
            let n0 = get(c, "no_child")?;
            let rows = |v: &Value| -> Result<Vec<[f64; 3]>, String> { Ok(params::matrix(get(v, "pieces")?)?.into_iter().map(|r| [r[0], r[1], r[2]]).collect()) };
            Working::Pieces(Pieces {
                no_child: (
                    num(get(n0, "phase_in_end")?)?,
                    num(get(n0, "phase_in_rate")?)?,
                    num(get(n0, "plateau_end")?)?,
                    num(get(n0, "maximum")?)?,
                    num(get(n0, "phase_out_end")?)?,
                    num(get(n0, "phase_out_rate")?)?,
                    num(get(n0, "agi_test")?)?,
                ),
                one: (rows(get(c, "one_child")?)?, num(get(get(c, "one_child")?, "agi_test")?)?),
                two: (rows(get(c, "two_or_more")?)?, num(get(get(c, "two_or_more")?, "agi_test")?)?),
            })
        } else if y <= 2013 {
            let use_joint = (2002..=2013).contains(&y);
            let c0 = get(&doc, "working_family_credit_no_child")?;
            let s0 = |name: &str| -> Result<f64, String> { num(resolve_year(get(c0, name)?, y)?) };
            let children = |key: &str| -> Result<Children, String> {
                let c = get(&doc, key)?;
                let s = |name: &str| -> Result<f64, String> { num(resolve_year(get(c, name)?, y)?) };
                Ok(Children {
                    break_: [s("break_1")?, s("break_2")?, s("break_3")?, s("break_4")?],
                    break_4_joint: if use_joint { Some(s("break_4_joint")?) } else { None },
                    rate: [s("rate_1")?, s("rate_2")?, s("rate_3")?],
                    maximum: [s("maximum_1")?, s("maximum_2")?],
                })
            };
            Working::Tables {
                no_child: NoChild {
                    phase_in_end: s0("phase_in_end")?,
                    phase_out_start: s0("phase_out_start")?,
                    phase_out_start_joint: if use_joint { Some(s0("phase_out_start_joint")?) } else { None },
                    rate: s0("rate")?,
                    maximum: s0("maximum")?,
                },
                one: children("working_family_credit_one_child")?,
                two: children("working_family_credit_two_children")?,
            }
        } else {
            let c = get(&doc, "working_family_credit_schedule")?;
            Working::Schedule(Schedule {
                no_child: kind(get(c, "no_child")?, y)?,
                one: kind(get(c, "one_child")?, y)?,
                two: kind(get(c, "two_children")?, y)?,
                three: if y >= 2019 { Some(kind(get(c, "three_children")?, y)?) } else { None },
            })
        };
        let cc_expense = match p.opt("child_care_expense_credit")? {
            Some(c) => Some(ChildCareExpense {
                cap: num(get(c, "expense_cap_per_child")?)?,
                rate_top: num(get(c, "rate_top")?)?,
                rate_floor: num(get(c, "rate_floor")?)?,
                rate_start: num(get(c, "rate_start")?)?,
                rate_step: num(get(c, "rate_step")?)?,
                rate_step_decrement: num(get(c, "rate_step_decrement")?)?,
            }),
            None => None,
        };
        let refund = if y >= 1989 {
            let clawback = match p.opt("property_refund_clawback")? {
                Some(c) => Some((
                    num(get(c, "maximum")?)?,
                    num(get(c, "clawback_start_1")?)?,
                    num(get(c, "clawback_start_2")?)?,
                    num(get(c, "clawback_end")?)?,
                    num(get(c, "clawback_rate_1")?)?,
                    num(get(c, "clawback_rate_2")?)?,
                )),
                None => None,
            };
            Some(Refund {
                owner_limit: p.f("property_refund_owner_income_limit")?,
                inflation: p.f("property_refund_inflation")?,
                max_table: if clawback.is_none() { Some(pairs(p.value("property_refund_max", &[])?)?) } else { None },
                clawback,
                threshold: pairs(p.value("property_refund_threshold", &[])?)?,
                copay: pairs(p.value("property_refund_copay", &[])?)?,
                renter_limit: p.f("property_refund_renter_income_limit")?,
                renter_inflation: p.f("property_refund_renter_inflation")?,
                renter_rent_share: p.f("property_refund_rent_share")?,
                renter_max: pairs(p.value("property_refund_renter_max", &[])?)?,
                renter_threshold: pairs(p.value("property_refund_renter_threshold", &[])?)?,
                renter_copay: pairs(get(&doc, "property_refund_renter_copay")?)?,
                dependent_subtraction: if y >= 1994 { list(p.value("property_refund_dependent_subtraction", &[])?)? } else { Vec::new() },
                aged_subtraction: if y >= 1994 && p.in_effect("property_refund_aged_subtraction")? {
                    Some(list(p.value("property_refund_aged_subtraction", &[])?)?)
                } else {
                    None
                },
            })
        } else {
            None
        };
        let ptc = if y < 1989 {
            let c = p.value("property_tax_credit", &[])?;
            let o = |name: &str| -> Result<f64, String> { Ok(match c.get(name) { Some(v) => num(v)?, None => 0.0 }) };
            let oi = |name: &str| -> Result<Option<Interp>, String> { Ok(match c.get(name) { Some(v) => Some(pairs(v)?), None => None }) };
            let aged = match c.get("aged") {
                Some(a) => Some(Aged {
                    first_step: pairs(get(a, "first_step")?)?,
                    first_step_top_base: num(get(a, "first_step_top_base")?)?,
                    second_step_share: match a.get("second_step_share") { Some(v) => Some(num(v)?), None => None },
                }),
                None => None,
            };
            Some(Ptc {
                income_limit: o("income_limit")?,
                threshold: oi("threshold")?,
                threshold_with_dependents: oi("threshold_with_dependents")?,
                threshold_no_dependents: oi("threshold_no_dependents")?,
                copay: oi("copay")?,
                copay_aged: oi("copay_aged")?,
                maximum_steps: match c.get("maximum_steps") { Some(v) => params::brackets(v)?.into_iter().map(|r| (r[0], r[1])).collect(), None => Vec::new() },
                maximum_mid_start: o("maximum_mid_start")?,
                maximum_mid_end: o("maximum_mid_end")?,
                maximum_mid_base: o("maximum_mid_base")?,
                maximum_mid_rate: o("maximum_mid_rate")?,
                maximum_top_base: o("maximum_top_base")?,
                maximum_top_rate: o("maximum_top_rate")?,
                maximum_top_start: o("maximum_top_start")?,
                maximum_flat: o("maximum_flat")?,
                maximum: o("maximum")?,
                aged_income_reduction: o("aged_income_reduction")?,
                first_step_table_limit: o("first_step_table_limit")?,
                first_step: oi("first_step")?,
                first_step_top_base: o("first_step_top_base")?,
                first_step_top_start: o("first_step_top_start")?,
                first_step_top_rate: o("first_step_top_rate")?,
                final_limits: match c.get("final_limits") { Some(v) => params::matrix(v)?.into_iter().map(|r| [r[0], r[1], r[2]]).collect(), None => Vec::new() },
                second_step_share: o("second_step_share")?,
                aged,
                rent_share: num(get(&doc, "property_credit_rent_share")?)?,
            })
        } else {
            None
        };
        let motor_fuels = match p.opt("motor_fuels_credit")? {
            Some(f) => {
                let l = get(f, "taxable_income_limit")?;
                Some(([num(get(l, "single")?)?, num(get(l, "married")?)?, num(get(l, "head_of_household")?)?], num(get(f, "amount")?)?))
            }
            None => None,
        };
        let cc_limit = get(&doc, "child_care_income_limit")?;
        let cc_max = get(&doc, "child_care_credit_max")?;
        let fed_year = YearParams::new(&federal_tax, y);
        Ok(Self {
            year,
            loss_limit: if y >= 1989 { num(resolve_year(get(&capital_gains, "net_capital_loss_limit")?, y)?)? } else { 0.0 },
            tables,
            bracket_factor: factor,
            surtax: opt("surtax")?,
            charity_addback: opt("charity_nonitemizer_addback")?,
            gains_addback_rate: opt("capital_gains_addback_rate")?,
            pension: if y <= 1986 { Some((p.f("pension_exclusion_income_threshold")?, p.f("pension_exclusion_max")?)) } else { None },
            federal_tax: if y <= 1986 { Some((p.f("federal_tax_se_share")?, p.f("federal_tax_se_cap")?)) } else { None },
                    phaseout_base: if y >= 2013 { p.f("phaseout_threshold_base")? } else { 0.0 },
            phaseout_inflation: if y >= 2013 { p.f("phaseout_inflation")? } else { 0.0 },
            phaseout_multiplier: if y >= 2011 { status_direct(get(&doc, "phaseout_multiplier")?)? } else { [0.0; 4] },
            exemption_amount: if y >= 2013 || (y >= 1987 && y <= 2017) { p.f("exemption_amount")? } else { 0.0 },
            exemption_rate: if y >= 2011 { p.f("exemption_phaseout_rate")? } else { 0.0 },
            exemption_step: if y >= 2011 { p.f("exemption_phaseout_step")? } else { 1.0 },
            itemized_rate: if y >= 2011 { p.f("itemized_phaseout_rate")? } else { 0.0 },
            itemized_max_share: if y >= 2011 { p.f("itemized_phaseout_max_share")? } else { 0.0 },
            d22: if y >= 2022 { Some(phaseout22(y)?) } else { None },
            std_cap: if y <= 1986 { p.f("standard_deduction_cap")? * p.f("standard_deduction_cap_inflation")? } else { 0.0 },
            std_percent: if y <= 1986 { p.f("standard_deduction_percent")? } else { 0.0 },
            married_reduction: if y > 1986 && y <= 2017 { opt("standard_deduction_married_reduction")? } else { None },
            property_cap: if y == 2008 || y == 2009 { fed_year.f("standard_deduction_real_property_tax_cap")? } else { 0.0 },
            std_by_status: if y >= 2018 && p.in_effect("standard_deduction_by_status")? {
                Some((status_direct(p.value("standard_deduction_by_status", &[])?)?, status_direct(p.value("standard_deduction_aged_addition_by_status", &[])?)?))
            } else {
                None
            },
            std_table,
            std_aged,
            dependent_floor: if y >= 2018 { p.f("dependent_standard_deduction_floor")? } else { 0.0 },
            dependent_addition: if y >= 2018 { p.f("dependent_standard_deduction_earned_addition")? } else { 0.0 },
            salt_cap: if y >= 2018 { p.f("salt_cap")? } else { 0.0 },
            m1m: if (2011..=2013).contains(&y) { Some((p.f("m1m_addback_threshold")?, p.f("m1m_exemption_phaseout_range")?)) } else { None },
            elderly,
            charity_subtraction: if y >= 1999 { Some((p.f("charity_subtraction_rate")?, p.f("charity_subtraction_floor")?)) } else { None },
            ss_subtraction,
            amt,
            personal_credit: if y <= 1986 { p.f("personal_credit")? } else { 0.0 },
            personal_credit_aged: match p.opt("personal_credit_aged")? {
                Some(a) => Some((num(get(a, "per_aged")?)?, num(get(a, "extra_aged")?)?)),
                None => None,
            },
            homemaker,
            elderly_credit_share: opt("elderly_credit_share")?,
            low_income,
            marriage,
            working: WorkingCredit { kind: working, min_federal_eitc: if y < 2022 && y >= 1991 { opt("working_family_credit_min_federal_eitc")? } else { None } },
            cc_limit: [num(resolve_year(get(cc_limit, "one")?, y)?)?, num(resolve_year(get(cc_limit, "two_or_more")?, y)?)?],
            cc_max: [num(resolve_year(get(cc_max, "one")?, y)?)?, num(resolve_year(get(cc_max, "two_or_more")?, y)?)?],
            cc_start: p.f("child_care_phaseout_start")?,
            cc_expense,
            cc_share: if p.in_effect("child_care_expense_credit")? { 0.0 } else { p.f("child_care_share_of_federal")? },
            cc_phaseout_rate: p.f("child_care_phaseout_rate")?,
            refund,
            ptc,
            motor_fuels,
            dividend_adjustment: dividend_adjustment(root)?,
        })
    }

    fn schedule_tax(&self, taxinc: f64, fedtax: f64, fs: Fs) -> f64 {
        let sep = if fs == Fs::Separate { 2.0 } else { 1.0 };
        let mut tax = match &self.tables {
            Tables::Flat(b) => b.tax(taxinc),
            Tables::Pair { single_a, single_b, joint_a, joint_b } => {
                let with_deduction = (taxinc - fedtax).max(0.0);
                let (ta, tb) = if fs == Fs::Joint {
                    (joint_a.tax(taxinc), joint_b.tax(with_deduction))
                } else {
                    (single_a.tax(taxinc), single_b.tax(with_deduction))
                };
                ta.min(tb)
            }
            Tables::Classes { single, joint, hoh } => {
                if self.year <= 1998 {
                    match fs {
                        Fs::Joint => joint.tax(taxinc),
                        Fs::Hoh => hoh.tax(taxinc),
                        _ => single.tax(taxinc),
                    }
                } else {
                    match fs {
                        Fs::Single => single.tax(taxinc),
                        Fs::Hoh => hoh.tax(taxinc),
                        _ => joint.tax(taxinc * sep) / sep,
                    }
                }
            }
        };
        if let Some(s) = self.surtax {
            tax *= s;
        }
        tax
    }
}

fn after_reduction(d: &Phaseout22, amount: f64, agi: f64, fs: Fs) -> f64 {
    let low = d.low[fs as usize];
    let high = d.high[fs as usize];
    let alternate = d.alternate_rate * amount;
    let reduction = if d.alternate_applies {
        let main = d.low_rate * (agi - low).max(0.0).min(high - low) + d.high_rate * (agi - high).max(0.0);
        if agi > d.alternate_income { alternate } else { alternate.min(main) }
    } else {
        alternate.min(d.low_rate * (agi - low).max(0.0))
    };
    (amount - reduction).max(0.0)
}

fn steps(income: f64, rows: &[(f64, f64)], otherwise: f64) -> f64 {
    for (upper, amount) in rows {
        if income < *upper {
            return *amount;
        }
    }
    otherwise
}

fn pieces(income: f64, rows: &[[f64; 3]]) -> f64 {
    let mut out = 0.0;
    for [start, value, slope] in rows {
        if income >= *start {
            out = value + slope * (income - start);
        }
    }
    out
}

fn property_credit_pre1989(c: &Ptc, hh: f64, y: i64, i: &Inp) -> f64 {
    let ptx = i.proptax + c.rent_share * i.rentpaid;
    let aged = i.aged_count() > 0.0;
    let ev = |t: &Option<Interp>, x: f64| t.as_ref().map_or(0.0, |t| t.eval(x));
    if y >= 1987 {
        let threshold = if c.threshold.is_some() {
            ev(&c.threshold, hh)
        } else if i.depx > 0.0 || aged {
            ev(&c.threshold_with_dependents, hh)
        } else {
            ev(&c.threshold_no_dependents, hh)
        };
        let copay = ev(&c.copay, hh);
        let excess = (ptx - threshold * hh).max(0.0);
        let otherwise = if hh < c.maximum_mid_end {
            c.maximum_mid_base - c.maximum_mid_rate * (hh - c.maximum_mid_start)
        } else {
            c.maximum_top_base - c.maximum_top_rate * (hh - c.maximum_top_start)
        };
        let maximum = steps(hh, &c.maximum_steps, otherwise);
        let credit = ((1.0 - copay) * excess).max(0.0).min(maximum);
        return if hh < c.income_limit { credit } else { 0.0 };
    }
    if y >= 1985 {
        let hh = if aged { (hh - c.aged_income_reduction).max(0.0) } else { hh };
        let threshold = ev(&c.threshold, hh);
        let excess = ptx - threshold * hh;
        let maximum = if hh < c.maximum_mid_start {
            c.maximum_flat
        } else if hh < c.maximum_mid_end {
            c.maximum_flat - c.maximum_mid_rate * (hh - c.maximum_mid_start)
        } else {
            c.maximum_top_base - c.maximum_top_rate * (hh - c.maximum_mid_end)
        };
        let credit = ((1.0 - ev(&c.copay, hh)) * excess).max(0.0).min(maximum);
        return if excess > 0.0 && hh < c.income_limit { credit } else { 0.0 };
    }
    if y >= 1983 {
        let threshold = ev(&c.threshold, hh);
        let excess = ptx - threshold * hh;
        let step1_cap = if hh < c.first_step_table_limit {
            ev(&c.first_step, hh)
        } else {
            c.first_step_top_base - c.first_step_top_rate * (hh - c.first_step_top_start)
        };
        let step1 = excess.min(step1_cap);
        let maximum = steps(hh, &c.maximum_steps, c.maximum_top_base - c.maximum_top_rate * (hh - c.maximum_top_start));
        let copay = if aged { ev(&c.copay_aged, hh) } else { ev(&c.copay, hh) };
        let mut credit = ((1.0 - copay) * (excess - step1).max(0.0)).max(0.0) + step1;
        credit = credit.min(maximum);
        for [lower, upper, share] in &c.final_limits {
            if hh >= *lower && hh < *upper {
                credit = credit.min(ptx - share * hh).max(0.0);
            }
        }
        let eligible = hh < c.income_limit && ptx > 0.0 && excess > 0.0;
        return if eligible { credit } else { 0.0 };
    }
    let threshold = ev(&c.threshold, hh);
    let excess = ptx - threshold * hh;
    let first_step = |table: Option<&Interp>, base: f64| -> f64 {
        if hh < c.first_step_table_limit {
            table.map_or(0.0, |t| t.eval(hh))
        } else {
            (base - c.first_step_top_rate * (hh - c.first_step_top_start)).max(0.0)
        }
    };
    let step1_cap = match (&c.aged, aged) {
        (Some(a), true) => first_step(Some(&a.first_step), a.first_step_top_base),
        _ => first_step(c.first_step.as_ref(), c.first_step_top_base),
    };
    let step1 = excess.min(step1_cap);
    let maximum = if y >= 1979 {
        steps(hh, &c.maximum_steps, (c.maximum_top_base - c.maximum_top_rate * (hh - c.maximum_top_start)).max(0.0))
    } else if hh < c.maximum_top_start {
        c.maximum
    } else {
        (c.maximum - c.maximum_top_rate * (hh - c.maximum_top_start)).max(0.0)
    };
    let second_step_share = match (&c.aged, aged) {
        (Some(a), true) => a.second_step_share.unwrap_or(c.second_step_share),
        _ => c.second_step_share,
    };
    let credit = (second_step_share * (excess - step1).max(0.0)).max(0.0) + step1;
    let credit = credit.min(maximum);
    if ptx > 0.0 && excess > 0.0 { credit } else { 0.0 }
}

fn property_refund_owner(r: &Refund, hhy: f64, i: &Inp) -> f64 {
    let aif2 = r.inflation;
    let income = hhy / aif2;
    let ptxo = i.proptax;
    let refund_max = match &r.clawback {
        Some((max, start_1, start_2, end, rate_1, rate_2)) => {
            let maximum = max * aif2;
            let raw = if hhy <= start_2 * aif2 {
                (hhy - start_1 * aif2) * rate_1
            } else if hhy <= end * aif2 {
                (hhy - start_2 * aif2) * rate_2
            } else {
                maximum
            };
            maximum - raw.min(maximum).max(0.0)
        }
        None => r.max_table.as_ref().map_or(0.0, |t| t.eval(income)) * aif2,
    };
    let threshold_pct = r.threshold.eval(income);
    let copay = r.copay.eval(income);
    let threshold = ptxo.min(threshold_pct * hhy / 100.0);
    let refund = refund_max.min((ptxo - threshold).max(0.0) * (1.0 - copay)).max(0.0);
    if ptxo > 0.0 && hhy <= r.owner_limit * aif2 { refund } else { 0.0 }
}

fn property_refund_renter(r: &Refund, hhy: f64, i: &Inp) -> f64 {
    let aifr = r.renter_inflation;
    let income = hhy / aifr;
    let ptxr = r.renter_rent_share * i.rentpaid;
    let refund_max = r.renter_max.eval(income) * aifr;
    let thres = ptxr.min(r.renter_threshold.eval(income) * hhy / 100.0);
    let copay = r.renter_copay.eval(income);
    refund_max.min((ptxr - thres).max(0.0) * (1.0 - copay)).max(0.0)
}

fn working_family_credit(p: &MnParams, base_earned: f64, base_agi: f64, fed_agi: f64, is_joint: bool, i: &Inp, f: &Fed) -> f64 {
    let y = p.year;
    let nkid = i.depx.floor();
    let eitc = f.eitc;
    let wmax = base_earned.max(base_agi);
    let two_base = |credit: &dyn Fn(f64) -> f64, agi_test: f64| -> f64 {
        if fed_agi <= agi_test { credit(base_earned) } else { credit(base_earned).min(credit(base_agi)) }
    };
    let mut work = match &p.working.kind {
        Working::None => return 0.0,
        Working::Statutory2022 { earnings_maximum, phase_in_rate, threshold_joint, threshold_other, phase_out_rate } => {
            let earned = f.earned_income;
            let agi = f.agi;
            let ages = (i.page >= 19.0 && i.page <= 64.0) || (i.sage >= 19.0 && i.sage <= 64.0) || (i.page == 0.0 && i.sage == 0.0);
            let eligible = (i.depx > 0.0 || ages) && i.fs() != Fs::Separate;
            let income = earned.max(agi);
            let count = i.dep18.clamp(0.0, 3.0) as usize;
            let credit = earned.max(0.0).min(earnings_maximum[count]) * phase_in_rate[count];
            let threshold = if is_joint { threshold_joint[count] } else { threshold_other[count] };
            let reduction = (income - threshold).max(0.0) * phase_out_rate[count];
            return if eligible { (credit - reduction).max(0.0) } else { 0.0 };
        }
        Working::Statutory { child_amount, phase_in_rate, phase_out_rate, phase_in_end, phase_out_joint, phase_out_other } => {
            let earned = f.earned_income;
            let ages = (i.page >= 19.0 && i.page <= 64.0) || (i.sage >= 19.0 && i.sage <= 64.0) || (i.page == 0.0 && i.sage == 0.0);
            let eligible = (i.depx > 0.0 || ages) && i.fs() != Fs::Separate;
            let income = earned.max(f.agi);
            let base = child_amount * i.dep17 + phase_in_rate * earned.max(0.0).min(*phase_in_end);
            let threshold = if is_joint { *phase_out_joint } else { *phase_out_other };
            let reduction = (income - threshold).max(0.0) * phase_out_rate;
            return if eligible { (base - reduction).max(0.0) } else { 0.0 };
        }
        Working::FederalShare(share) => return if eitc > 0.0 { share * eitc } else { 0.0 },
        Working::Pieces(pc) => {
            let (phase_in_end, phase_in_rate, plateau_end, maximum, phase_out_end, phase_out_rate, agi_test) = pc.no_child;
            let no_child = |base: f64| -> f64 {
                let phase_out = (maximum - phase_out_rate * (base - plateau_end)).max(0.0);
                if base <= phase_in_end {
                    phase_in_rate * base
                } else if base <= plateau_end {
                    maximum
                } else if base < phase_out_end {
                    phase_out
                } else {
                    0.0
                }
            };
            if nkid < 1.0 {
                two_base(&no_child, agi_test)
            } else if nkid == 1.0 {
                two_base(&|b| pieces(b, &pc.one.0), pc.one.1)
            } else {
                two_base(&|b| pieces(b, &pc.two.0), pc.two.1)
            }
        }
        Working::Tables { no_child, one, two, .. } => {
            let phase_out_0 = match no_child.phase_out_start_joint {
                Some(j) if is_joint => j,
                _ => no_child.phase_out_start,
            };
            let rate_0 = no_child.rate;
            let max_0 = no_child.maximum;
            let no_child_fn = |base: f64| -> f64 {
                if base <= no_child.phase_in_end {
                    rate_0 * base
                } else if base <= phase_out_0 {
                    phase_out_0.min(max_0)
                } else {
                    (max_0 - rate_0 * (base - phase_out_0)).max(0.0)
                }
            };
            let with_children = |c: &Children| -> (Box<dyn Fn(f64) -> f64>, f64) {
                let phase_out = match c.break_4_joint {
                    Some(j) if is_joint => j,
                    _ => c.break_[3],
                };
                let (b1, b2, b3) = (c.break_[0], c.break_[1], c.break_[2]);
                let (r1, r2, r3) = (c.rate[0], c.rate[1], c.rate[2]);
                let (m1, m2) = (c.maximum[0], c.maximum[1]);
                let credit = move |base: f64| -> f64 {
                    if base < b1 {
                        r1 * base
                    } else if base < b2 {
                        phase_out.min(m1)
                    } else if base < b3 {
                        r2 * (base - b2) + m1
                    } else if base < phase_out {
                        m2
                    } else {
                        (m2 - r3 * (base - phase_out)).max(0.0)
                    }
                };
                (Box::new(credit), phase_out)
            };
            let (one_fn, one_out) = with_children(one);
            let (two_fn, two_out) = with_children(two);
            if nkid < 1.0 {
                two_base(&no_child_fn, phase_out_0)
            } else if nkid == 1.0 {
                two_base(&*one_fn, one_out)
            } else {
                two_base(&*two_fn, two_out)
            }
        }
        Working::Schedule(s) => {
            let schedule = |k: &Kind, phase_out_start: f64| -> f64 {
                let credit = if base_earned <= k.phase_in_end { k.phase_in_rate * base_earned } else { k.maximum };
                (credit - k.phase_out_rate * (wmax - phase_out_start).max(0.0)).max(0.0)
            };
            let start = |k: &Kind| if is_joint { k.phase_out_start_joint } else { k.phase_out_start };
            if nkid < 1.0 {
                schedule(&s.no_child, start(&s.no_child))
            } else if nkid == 1.0 {
                schedule(&s.one, start(&s.one))
            } else if y >= 2019 && nkid == 2.0 {
                schedule(&s.two, start(&s.two))
            } else if y >= 2019 {
                let t = s.three.as_ref().expect("three children schedule");
                schedule(t, start(t))
            } else {
                schedule(&s.two, start(&s.two))
            }
        }
    };
    if let Some(min) = p.working.min_federal_eitc {
        if !(eitc >= min) {
            work = 0.0;
        }
    }
    work
}

pub fn tax(i: &Inp, f: &Fed, p: &MnParams) -> f64 {
    let y = p.year;
    let fs = i.fs();
    let is_joint = fs == Fs::Joint;
    let is_sep = fs == Fs::Separate;
    let sep = i.sepret();
    let txp = i.taxpayer_count();
    let aged = i.aged_count();
    let dependent_filer = i.is_dependent_filer();
    let depx = i.depx;
    let setax = f.payroll.setax;
    let hh = household_income(i, p.dividend_adjustment);
    let mn_business = i.pbusinc + i.pprofinc + i.sbusinc + i.sprofinc + i.scorp;
    let salt_ded = f.ded;
    let untax = f.taxable_unemployment;
    let ui_total = unemployment_total(i);
    let fullcg = i.stcg + i.ltcg;

    // ---- federal return values as Minnesota reads them ----
    let (fed_deduc, fed_itemizes, fed_zbr);
    if y <= 1986 {
        fed_deduc = i.proptax + i.otheritem + i.mortgage + salt_ded;
        fed_zbr = f.pre87.zbr;
        fed_itemizes = if y <= 1981 { fed_deduc > fed_zbr } else { f.forced_itemize };
    } else {
        fed_deduc = f.itemized_deduction;
        fed_itemizes = f.itemizes;
        fed_zbr = if fed_itemizes { 0.0 } else { f.standard_deduction };
    }
    let agix = f.agi.max(0.0);
    let alim50 = if y == 2020 || y == 2021 { 1.0e20 } else { 0.5 * agix };
    let cash = if f.agi < 0.0 { 0.0 } else { i.charity_cash.min(alim50) };
    let fed_char = alim50.min(cash).max(0.0);
    let mut fed_agi = f.agi;
    if y == 2020 {
        let amount = p.charity_addback.unwrap_or(0.0);
        fed_agi -= if fed_itemizes { 0.0 } else { (amount / sep).min(i.charity_cash) };
    }

    // ---- Minnesota AGI ----
    let mut agi = fed_agi;
    if y == 2009 {
        agi = agi - untax + ui_total;
    }
    if y == 2020 {
        let amount = p.charity_addback.unwrap_or(0.0);
        agi = agi + ui_total - untax + if fed_itemizes { 0.0 } else { amount.min(i.charity_cash) };
    }
    if let Some(rate) = p.gains_addback_rate {
        agi += rate * fullcg;
    }
    if (1979..=1986).contains(&y) {
        agi -= untax;
    }
    if (1982..=1985).contains(&y) {
        agi += f.pre87.twoded;
    }
    if let Some((threshold, maximum)) = p.pension {
        let over = (fed_agi - threshold).max(0.0);
        let mut penexc = i.pensions.min((maximum - over).max(0.0));
        if y >= 1985 && !(aged > 0.0) {
            penexc = 0.0;
        }
        agi -= penexc;
    }

    // ---- deduction for federal income tax (through 1986) ----
    let mut fedtax = 0.0;
    if let Some((seless, semax)) = p.federal_tax {
        fedtax = (f.fiitax - setax * seless).max(0.0);
        fedtax = (fedtax - ((1.0 - seless) * setax - semax).max(0.0)).max(0.0);
        if fed_agi > 0.0 && agi >= 0.0 {
            fedtax *= 1.0f64.min(agi / fed_agi);
        }
        if y <= 1984 {
            agi -= fedtax;
        }
    }

    // ---- exemptions ----
    let mult = p.phaseout_multiplier[fs as usize];
    let (ph13, phded) = if y >= 2013 {
        let aif91 = p.phaseout_inflation;
        (p.phaseout_base * aif91 * mult / sep, p.phaseout_base * aif91 / sep)
    } else {
        (0.0, 0.0)
    };
    let mut exemp = if y <= 1986 { 0.0 } else if y <= 2012 { f.personal_exemptions } else { 0.0 };
    if y >= 2013 {
        let count = if y <= 2018 { federal_exemption_count(y, i) } else { depx };
        exemp = count * p.exemption_amount;
        let excess = (fed_agi - ph13).max(0.0);
        let ratio = 1.0f64.min(p.exemption_rate * excess / (p.exemption_step / sep));
        exemp -= ratio * exemp;
        if let Some(d22) = &p.d22 {
            let steps = ((fed_agi - d22.exemption_start[fs as usize]).max(0.0) / d22.exemption_step[fs as usize]).ceil();
            exemp = (count * p.exemption_amount * (1.0 - d22.step_fraction * steps)).max(0.0);
        }
        if dependent_filer {
            exemp = 0.0;
        }
    }

    // ---- standard deduction ----
    let ag = agi.max(0.0);
    let mut stded;
    if y <= 1986 {
        let cap_expr = if y >= 1985 { p.std_cap / sep } else { p.std_cap };
        stded = cap_expr.min(p.std_percent * ag);
    } else if y <= 2017 {
        stded = fed_zbr;
        if let Some(reduction) = p.married_reduction {
            stded -= if is_joint || is_sep { reduction / sep } else { 0.0 };
        }
        if y == 2008 || y == 2009 {
            stded -= i.proptax.max(0.0).min(p.property_cap * txp);
        }
    } else if let Some((amounts, aged_amounts)) = &p.std_by_status {
        stded = amounts[fs as usize] + aged_amounts[fs as usize] * aged;
    } else {
        let [s, m, h] = p.std_table;
        stded = (by_filer(fs, s, m, h) + by_filer(fs, p.std_aged[0], p.std_aged[1], p.std_aged[0]) * aged) / sep;
    }
    if let Some(d22) = &p.d22 {
        stded = after_reduction(d22, stded, fed_agi, fs);
    }
    if y >= 2018 && dependent_filer {
        stded = stded.min(p.dependent_floor.max(f.earned_income + p.dependent_addition));
    }

    // ---- itemized deductions ----
    let mut xitded;
    if y <= 1986 {
        xitded = if fed_itemizes { (fed_deduc - salt_ded).max(0.0) } else { 0.0 };
    } else if y <= 2012 {
        xitded = if fed_itemizes { fed_deduc } else { 0.0 };
    } else if y <= 2017 {
        let deduc1 = fed_char + i.proptax + i.otheritem + i.mortgage;
        let dlim1 = p.itemized_rate * (fed_agi - ph13).max(0.0);
        let dlim2 = p.itemized_max_share * deduc1.max(0.0);
        xitded = (deduc1 - dlim1.min(dlim2)).max(0.0);
    } else if y <= 2019 {
        let salt = i.proptax + salt_ded + i.otheritem;
        let cap = p.salt_cap / sep;
        xitded = fed_deduc - cap.min(salt) + salt;
        let disalt = (xitded - stded).max(0.0).min(salt_ded + i.otheritem);
        let dedphs = (p.itemized_max_share * xitded).min(p.itemized_rate * (fed_agi - phded));
        let alostd = disalt + dedphs;
        let exces = (xitded - stded).max(0.0);
        let remaining = if alostd <= exces { (xitded - dedphs).max(0.0) } else { xitded - (exces - disalt).max(0.0) };
        let disagi = if fed_agi > phded { xitded - remaining } else { 0.0 };
        xitded -= disalt + disagi;
    } else {
        let sttax = (p.salt_cap / sep).min(i.proptax + salt_ded + i.otheritem);
        let stt = salt_ded.min(sttax - (i.proptax + i.otheritem));
        xitded = fed_deduc - stt;
        let dedphs = (p.itemized_max_share * fed_deduc).min(p.itemized_rate * (fed_agi - phded));
        xitded -= if fed_agi - phded > 0.0 { dedphs } else { 0.0 };
        if let Some(d22) = &p.d22 {
            xitded = after_reduction(d22, fed_deduc - stt, fed_agi, fs);
        }
    }
    let deduc = stded.max(xitded);
    let mut taxinc = agi - deduc - exemp;

    // ---- addition for state income tax deducted federally ----
    let add = if y <= 2017 {
        let a = if y >= 1988 {
            let [s, m, h] = p.std_table;
            let stnd = by_filer(fs, s, m, h) / sep;
            let oldd = by_filer(fs, p.std_aged[0], p.std_aged[1], p.std_aged[0]) * aged;
            (fed_deduc - stnd - oldd).max(0.0).min(salt_ded)
        } else if fed_deduc - salt_ded < fed_zbr {
            fed_deduc - fed_zbr
        } else {
            salt_ded
        };
        if fed_itemizes { a } else { 0.0 }
    } else {
        0.0
    };

    // Subtraction for the elderly, less nontaxable Social Security.
    let mut subrac = 0.0;
    if let Some((e, rate)) = &p.elderly {
        let class = if is_joint && aged >= 2.0 { 0 } else if is_joint { 1 } else if is_sep { 3 } else { 2 };
        let pick = |values: &Vec<f64>| values[class];
        let nontaxable = i.gssi - f.taxable_social_security;
        let yless = rate * (fed_agi - pick(&e[0])).max(0.0);
        let eldded = (pick(&e[1]) - nontaxable - yless).max(0.0);
        subrac = if aged > 0.0 && fed_agi <= pick(&e[2]) { eldded } else { 0.0 };
    }
    if let Some((rate, floor)) = p.charity_subtraction {
        if stded > xitded {
            subrac += rate * (cash - floor).max(0.0);
        }
    }
    if let Some((thr_single, thr_married, max_single, max_married, benefit_share, phaseout_rate)) = p.ss_subtraction {
        let ss_taxable = f.taxable_social_security;
        let provisional = fed_agi - ss_taxable + benefit_share * i.gssi;
        let single_class = matches!(fs, Fs::Single | Fs::Hoh);
        let threshold = if single_class { thr_single } else { thr_married / sep };
        let maximum = if single_class { max_single } else { max_married / sep };
        let allowed = (maximum - phaseout_rate * (provisional - threshold).max(0.0)).max(0.0);
        if ss_taxable > 0.0 {
            subrac += ss_taxable.min(allowed);
        }
    }
    taxinc = taxinc - subrac + add;

    if let Some((ph, m1m_range)) = p.m1m {
        let [s, m, h] = p.std_table;
        let stdmn = by_filer(fs, s, m, h) / sep;
        let xl9 = (p.itemized_max_share * fed_deduc.max(0.0)).min(p.itemized_rate * (fed_agi - ph / sep).max(0.0));
        let xl14 = (fed_deduc - stdmn).max(0.0);
        let xl15 = if add + xl9 <= xl14 { xl9 } else { (xl14 - add).max(0.0) };
        taxinc += if fed_itemizes && fed_agi > ph { xl15 } else { 0.0 };
        let phamex = ph * mult / sep;
        let over = fed_agi - phamex;
        let addxmp = if over <= m1m_range / sep {
            p.exemption_rate * over / (p.exemption_step / sep) * f.personal_exemptions
        } else {
            f.personal_exemptions
        };
        taxinc += if over > 0.0 { addxmp } else { 0.0 };
    }
    taxinc = taxinc.max(0.0);
    if y <= 1986 {
        taxinc = (agi - deduc - exemp).max(0.0);
    }
    let mut statax = p.schedule_tax(taxinc, fedtax, fs);

    // ---- alternative minimum tax ----
    if let Some((amt_rate, charity_floor, exclusion, phaseout_start, phaseout_rate)) = p.amt {
        let char_pref = (fed_char - charity_floor * fed_agi.max(0.0)).max(0.0);
        let alminy = fed_agi - char_pref;
        let excl = by_filer(fs, exclusion[0], exclusion[1], exclusion[2]) / sep;
        let phase = by_filer(fs, phaseout_start[0], phaseout_start[1], phaseout_start[2]) / sep;
        let alminc = (alminy - (excl - phaseout_rate * (alminy - phase).max(0.0)).max(0.0)).max(0.0);
        statax += (amt_rate * alminc - statax).max(0.0);
    }

    // ---- nonrefundable credits ----
    let mut credit = 0.0;
    if let Some((per_aged, extra_aged)) = p.personal_credit_aged {
        credit = p.personal_credit * (txp + depx) + per_aged * aged + extra_aged * (aged - txp).max(0.0);
    } else if y <= 1986 {
        credit = (txp + depx + aged) * p.personal_credit;
    }
    if let Some(h) = &p.homemaker {
        let ok = !dependent_filer && depx >= 1.0 && i.pwages <= h.earnings_limit && i.swages <= h.earnings_limit && !is_sep && fed_agi <= h.agi_limit / sep;
        credit += if ok { h.amount } else { 0.0 };
    }
    if let Some(share) = p.elderly_credit_share {
        credit += share * f.federal_elder;
    }
    if let Some(l) = &p.low_income {
        let npop = (txp + depx).min(6.0).floor();
        let ylow = if npop >= 1.0 { l.thresholds[npop as usize - 1] } else { 0.0 };
        let ytest = (l.rate * (hh - ylow)).max(0.0);
        let crlow = (statax - credit - ytest).max(0.0);
        credit += if fed_agi <= l.agi_limit && !dependent_filer { crlow } else { 0.0 };
    }
    if let Some(m) = &p.marriage {
        let mut d17 = i.psemp + i.ssemp + i.pwages.min(0.0) + i.swages.min(0.0);
        let mut other_income = 0.0;
        if y >= 2000 {
            // From 2000 business income, pensions and taxable Social Security count too.
            d17 += mn_business;
            other_income = i.pensions + f.taxable_social_security;
        }
        let earn = i.pwages.min(i.swages) + m.se_share * (other_income + d17.max(0.0) - m.se_tax_share * setax);
        let xl10 = if y <= 2017 { m.exemption_amount + m.standard_share * m.federal_joint_standard } else { m.lower_earner_deduction };
        let xl11 = (earn - xl10).max(0.0);
        let x1 = p.schedule_tax(xl11, fedtax, Fs::Single);
        let x2 = p.schedule_tax((taxinc - xl11).max(0.0), fedtax, Fs::Single);
        let statm = if taxinc < m.min_taxable || earn < m.min_earnings { 0.0 } else { statax };
        let crmar = m.maximum.min((statm - (x1 + x2)).max(0.0));
        credit += if is_joint { crmar } else { 0.0 };
    }

    // ---- working family credit ----
    let base_agi = fed_agi + if y == 2020 { ui_total - untax } else { 0.0 };
    let mut work = working_family_credit(p, f.earned_income, base_agi, fed_agi, is_joint, i, f);
    if dependent_filer {
        work = 0.0;
    }

    // ---- child and dependent care credit ----
    let deps = depx.min(2.0).floor();
    let cymax = if deps >= 2.0 { p.cc_limit[1] } else { p.cc_limit[0] };
    let cmax = if deps >= 2.0 { p.cc_max[1] } else { p.cc_max[0] };
    let cded = p.cc_start;
    let cost = if let Some(c13) = &p.cc_expense {
        let ncccr = if i.dep13 > 0.0 { i.dep13.min(2.0).floor() } else { deps };
        let mut child = i.childcare.min(c13.cap * ncccr);
        if is_joint {
            child = child.min(i.pwages).min(i.swages).max(0.0);
        }
        let chr_rate = c13.rate_floor.max(c13.rate_top - c13.rate_step_decrement * ((agi - c13.rate_start) / c13.rate_step).max(0.0));
        chr_rate * child
    } else if y <= 1986 {
        p.cc_share * f.federal_chcr
    } else {
        p.cc_share * f.ccc_uncapped
    };
    let phaseout_rate = p.cc_phaseout_rate;
    let (mut chcr, eligible);
    if y >= 2017 {
        let over = (fed_agi - cded).max(0.0);
        chcr = if over <= 0.0 { cost } else { cost.min((cmax - phaseout_rate * over).max(0.0)) };
        eligible = fed_agi <= cymax;
    } else {
        let over = (hh - cded).max(0.0);
        let capped = cost.min(cmax);
        let per_dependent = if y >= 1983 { deps } else { 1.0 };
        chcr = (capped - phaseout_rate * per_dependent * over).max(0.0);
        eligible = hh <= cymax;
    }
    if !(deps > 0.0 && eligible) {
        chcr = 0.0;
    }

    // ---- property tax refund ----
    let mut pcred;
    if let Some(r) = &p.refund {
        let capgn = fullcg.max(-p.loss_limit / sep);
        let refund_income = fed_agi + i.gssi - f.taxable_social_security;
        let mut hhy = (if capgn < 0.0 { refund_income - capgn } else { refund_income }).max(0.0);
        if y >= 1994 {
            let kid = depx.min(5.0).floor() as usize;
            let by_kid = |amounts: &Vec<f64>| amounts.get(kid).copied().unwrap_or(0.0);
            let dep_sub = by_kid(&r.dependent_subtraction);
            let aged_sub = match &r.aged_subtraction {
                Some(a) => by_kid(a),
                None => dep_sub + p.exemption_amount,
            };
            hhy = (hhy - if aged > 0.0 { aged_sub } else { dep_sub }).max(0.0);
        }
        let renter = i.rentpaid > 0.0 && hhy <= r.renter_limit * r.renter_inflation;
        let renter_refund = if renter { property_refund_renter(r, hhy, i) } else { 0.0 };
        // A household that rented and owned claims both (Form M1PR).
        pcred = renter_refund + property_refund_owner(r, hhy, i);
    } else {
        pcred = p.ptc.as_ref().map_or(0.0, |c| property_credit_pre1989(c, hh, y, i));
    }
    if dependent_filer {
        pcred = 0.0;
    }
    pcred = pcred.max(0.0);

    // ---- 2009 motor fuels credit ----
    let mut crfuel = 0.0;
    if let Some((limits, amount)) = p.motor_fuels {
        let limit = by_filer(fs, limits[0], limits[1], limits[2]) / sep;
        crfuel = if taxinc <= limit { amount / sep } else { 0.0 };
    }
    let _ = p.bracket_factor;
    (statax - credit).max(0.0) - chcr - work - pcred - crfuel
}
