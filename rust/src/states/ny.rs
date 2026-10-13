//! New York income tax (port of `calculators.states.ny`).

use std::path::Path;

use serde_yaml::Value;

use crate::common::Brackets;
use crate::federal::{read_eitc, EitcRow, Fed, Inp};
use crate::params::{self, get, load, num, resolve_year, status_direct, Fs, YearParams};
use crate::payroll::round_half_even;
use crate::states::{
    dividend_adjustment, federal_exemption_count, household_income, law_year_is_exact, unemployment_total, Interp, Tiers,
};

/// Rate-schedule class by filing status: single (also separate), head of household, married.
fn class(fs: Fs) -> usize {
    match fs {
        Fs::Single | Fs::Separate => 0,
        Fs::Hoh => 1,
        Fs::Joint => 2,
    }
}

enum Schedule {
    One(Brackets),
    ByClass([Brackets; 3]),
}

struct ClassSupplemental {
    starts: Vec<f64>,
    base: Brackets,
    incremental: Brackets,
}

struct Supplemental {
    classes: [ClassSupplemental; 3],
    min_agi: f64,
    phase_in_length: f64,
    top_agi: f64,
    top_rate: f64,
}

struct TierClass {
    uppers: Tiers,
    rates: Vec<f64>,
    additions: Vec<f64>,
    starts: Vec<f64>,
    tops: Tiers,
    top_additions: Vec<f64>,
}

struct RecaptureTiers {
    base_agi: f64,
    phase: f64,
    decimals: i32,
    top_agi: f64,
    top_rate: f64,
    classes: [TierClass; 3],
}

struct Bounds {
    inflation: f64,
    rates: [f64; 4],
    additions: [f64; 7],
    base_agi: f64,
    phase: f64,
    married_tier1: f64,
    married_tier2: f64,
    married_top: f64,
    single_tier: f64,
    single_top: f64,
    hoh_tier: f64,
    hoh_top: f64,
}

struct TopBracket {
    flat_rate: f64,
    top_rate: f64,
    top_flat_rate: f64,
    top_agi: f64,
    bracket_top: [f64; 3],
    bracket_benefit: [f64; 3],
}

struct Millionaire {
    flat_rate: f64,
    middle_rate: f64,
    top_rate: f64,
    middle_agi: f64,
    top_agi: f64,
    second_start: f64,
    bracket_top: [f64; 3],
    benefit: [f64; 3],
    upper_benefit: [f64; 3],
}

enum Recapture {
    None,
    Supplemental(Supplemental),
    Tiers(RecaptureTiers),
    Bounds(Bounds),
    TopBracket { start: f64, span: f64, c: TopBracket },
    Millionaire { start: f64, span: f64, c: Millionaire },
    Simple { start: f64, span: f64 },
}

struct Family {
    low_income_limit: f64,
    high_income_limit: f64,
    low_table: Interp,
    high_table: Interp,
}

struct RealProperty {
    rent_share: f64,
    rent_cap: f64,
    property_tax_cap: f64,
    tiers: Vec<[f64; 4]>,
}

struct Unearned {
    rate: f64,
    phase_in_start: f64,
    phase_in_range: f64,
}

enum NoTax {
    PerTaxpayer(f64),
    Early { single: f64, other: f64 },
    Scalar(f64),
    ByStatus([f64; 4]),
}

enum Household {
    None,
    Table(Interp),
    Dict { single: Interp, married: Interp, additional: Interp },
}

enum Care {
    Flat(f64),
    ByAgi(Interp),
    Taper { full_limit: f64, full_share: f64, taper_end: f64, taper_share: f64, taper_slope: f64, taper_start: f64, floor_share: f64 },
    Pieces(Vec<[f64; 3]>),
}

enum Maximum {
    Scalar(f64),
    Table(Interp),
}

enum PropertyCredit {
    None,
    Homeowner { income_limit: f64, maximum: Maximum, aged_maximum: Interp, threshold: Interp, homeowner_share: f64, renter_share: f64 },
    Step { income_limit: f64, maximum: f64, step: f64, low: f64, high: f64, threshold: Interp },
    Standard { income_limit: f64, maximum: Maximum, aged_maximum: Interp, threshold: Interp, aged_threshold: Interp },
}

struct EitcLaw {
    rows: [[Option<EitcRow>; 4]; 4],
    dylim: f64,
    minimum_age: f64,
}

struct Eitc2020 {
    current: EitcLaw,
    prior: Option<(EitcLaw, f64, f64)>,
    aged_age: f64,
}

enum Empire {
    None,
    Under4 { under_4: f64, amount: f64, per_1000: f64, threshold: [f64; 4] },
    Share { limit_single: f64, limit_married: f64, minimum: f64, share: f64 },
    PerChild {
        limit_single: f64,
        limit_married: f64,
        per_child: f64,
        phaseout_rate: f64,
        floor: f64,
        refundable_rate: f64,
        payroll_rate: f64,
        many_children: f64,
        minimum: f64,
        share: f64,
        excludes_under_4: bool,
        wage_base: f64,
    },
}

pub struct NyParams {
    year: i64,
    loss_limit: f64,
    charity_addback: Option<f64>,
    pension_exclusion: f64,
    capital_gains_addback: f64,
    stds: f64,
    stdm: f64,
    stdh: f64,
    standard_share: Option<(f64, f64)>,
    standard_dependent: Option<f64>,
    salt_cap: Option<f64>,
    limit_thresholds: Option<[f64; 3]>,
    limit_max_share: f64,
    limit_rate: f64,
    reduction_share: Option<f64>,
    reduction_range: f64,
    reduction_upper_start: f64,
    reduction_upper_end: f64,
    reduction_threshold: [f64; 4],
    reduction_top_factor: f64,
    reduction_start: f64,
    adjustment_threshold: [f64; 4],
    adjustment_rate: f64,
    adjustment_top_rate: f64,
    exemption_per_person: f64,
    family: Option<Family>,
    family_low_share: f64,
    family_rates: Brackets,
    real_property: Option<RealProperty>,
    schedule: Schedule,
    recapture: Recapture,
    maximum_tax: Option<(Brackets, f64)>,
    unearned: Option<Unearned>,
    no_tax: NoTax,
    no_tax_dependent: f64,
    household: Household,
    household_addition: f64,
    care: Care,
    property_credit: PropertyCredit,
    property_share: f64,
    property_rent_share: f64,
    property_rent_limit: f64,
    eitc_rate: f64,
    eitc2020: Option<Eitc2020>,
    empire: Empire,
    family_relief: Option<(f64, f64, f64)>,
    dividend_adjustment: f64,
}

fn pairs(v: &Value) -> Result<Interp, String> {
    Ok(Interp::new(&params::brackets(v)?))
}

fn three(v: &Value) -> Result<[f64; 3], String> {
    Ok([num(get(v, "single")?)?, num(get(v, "head_of_household")?)?, num(get(v, "married")?)?])
}

fn tier_class(v: &Value) -> Result<TierClass, String> {
    let tiers = params::matrix(get(v, "tiers")?)?;
    let tops = params::matrix(get(v, "top_additions")?)?;
    Ok(TierClass {
        uppers: Tiers::new(tiers.iter().map(|t| t[0]).collect(), false),
        rates: tiers.iter().map(|t| t[1]).collect(),
        additions: tiers.iter().map(|t| t[2]).collect(),
        starts: tiers.iter().map(|t| t[3]).collect(),
        tops: Tiers::new(tops.iter().map(|t| t[0]).collect(), false),
        top_additions: tops.iter().map(|t| t[1]).collect(),
    })
}

fn eitc_law(root: &Path, law: i64) -> Result<EitcLaw, String> {
    let misc = load(root, "national/eitc_misc.yaml")?;
    Ok(EitcLaw {
        rows: read_eitc(root, law)?,
        dylim: num(resolve_year(get(&misc, "dylim")?, law)?)?,
        minimum_age: if law >= 1994 { num(resolve_year(get(&misc, "childless_minimum_age")?, law)?)? } else { 0.0 },
    })
}

impl NyParams {
    pub fn load(root: &Path, year: i64) -> Result<Self, String> {
        let doc = load(root, "states/ny/income_tax.yaml")?;
        law_year_is_exact(root, &doc, year)?;
        let capital_gains = load(root, "national/capital_gains.yaml")?;
        let misc = load(root, "national/eitc_misc.yaml")?;
        let p = YearParams::new(&doc, year);
        let opt = |key: &str| -> Result<Option<f64>, String> { Ok(if p.in_effect(key)? { Some(p.f(key)?) } else { None }) };

        let tables = p.value("brackets", &[])?;
        let schedule = if tables.is_sequence() {
            Schedule::One(Brackets::new(&params::brackets(tables)?))
        } else {
            let factor = p.f("bracket_inflation")?;
            let scale = |key: &str| -> Result<Brackets, String> {
                let rows: Vec<Vec<f64>> = params::brackets(get(tables, key)?)?.into_iter().map(|r| vec![r[0] * factor, r[1]]).collect();
                Ok(Brackets::new(&rows))
            };
            Schedule::ByClass([scale("single")?, scale("head_of_household")?, scale("married")?])
        };
        let recapture = if p.in_effect("supplemental_tax")? {
            let d = p.value("supplemental_tax", &[])?;
            let mk = |key: &str| -> Result<ClassSupplemental, String> {
                let rows = params::brackets(get(tables, key)?)?;
                let c = get(d, key)?;
                Ok(ClassSupplemental {
                    starts: rows.iter().map(|r| r[0]).collect(),
                    base: Brackets::new(&params::brackets(get(c, "base")?)?),
                    incremental: Brackets::new(&params::brackets(get(c, "incremental")?)?),
                })
            };
            Recapture::Supplemental(Supplemental {
                classes: [mk("single")?, mk("head_of_household")?, mk("married")?],
                min_agi: num(get(d, "min_agi")?)?,
                phase_in_length: num(get(d, "phase_in_length")?)?,
                top_agi: num(get(d, "top_agi")?)?,
                top_rate: num(get(d, "top_rate")?)?,
            })
        } else if p.in_effect("recapture_tiers")? {
            let c = p.value("recapture_tiers", &[])?;
            Recapture::Tiers(RecaptureTiers {
                base_agi: num(get(c, "base_agi")?)?,
                phase: num(get(c, "phase")?)?,
                decimals: num(get(c, "ratio_decimals")?)? as i32,
                top_agi: num(get(c, "top_agi")?)?,
                top_rate: num(get(c, "top_rate")?)?,
                classes: [tier_class(get(c, "single")?)?, tier_class(get(c, "head_of_household")?)?, tier_class(get(c, "married")?)?],
            })
        } else if p.in_effect("recapture_bounds")? {
            let b = p.value("recapture_bounds", &[])?;
            let r: Vec<f64> = p.value("recapture_rates", &[])?.as_sequence().ok_or("rates")?.iter().map(num).collect::<Result<_, _>>()?;
            let a: Vec<f64> = p.value("recapture_additions", &[])?.as_sequence().ok_or("additions")?.iter().map(num).collect::<Result<_, _>>()?;
            let inflation = p.f("recapture_inflation")?;
            Recapture::Bounds(Bounds {
                inflation,
                rates: [r[0], r[1], r[2], r[3]],
                additions: [a[0], a[1], a[2], a[3], a[4], a[5], a[6]],
                base_agi: num(get(b, "base_agi")?)?,
                phase: num(get(b, "phase")?)?,
                married_tier1: num(get(b, "married_tier1_taxinc")?)?,
                married_tier2: num(get(b, "married_tier2_taxinc")?)?,
                married_top: num(get(b, "married_top_agi")?)?,
                single_tier: num(get(b, "single_tier_taxinc")?)?,
                single_top: num(get(b, "single_top_agi")?)?,
                hoh_tier: num(get(b, "hoh_tier_taxinc")?)?,
                hoh_top: num(get(b, "hoh_top_agi")?)?,
            })
        } else if !p.in_effect("recapture_start")? {
            Recapture::None
        } else {
            let start = p.f("recapture_start")?;
            let span = p.f("recapture_range")?;
            if p.in_effect("recapture_top_bracket")? {
                let c = p.value("recapture_top_bracket", &[])?;
                Recapture::TopBracket {
                    start,
                    span,
                    c: TopBracket {
                        flat_rate: num(get(c, "flat_rate")?)?,
                        top_rate: num(get(c, "top_rate")?)?,
                        top_flat_rate: num(get(c, "top_flat_rate")?)?,
                        top_agi: num(get(c, "top_agi")?)?,
                        bracket_top: three(get(c, "bracket_top")?)?,
                        bracket_benefit: three(get(c, "bracket_benefit")?)?,
                    },
                }
            } else if p.in_effect("recapture_millionaire")? {
                let c = p.value("recapture_millionaire", &[])?;
                Recapture::Millionaire {
                    start,
                    span,
                    c: Millionaire {
                        flat_rate: num(get(c, "flat_rate")?)?,
                        middle_rate: num(get(c, "middle_rate")?)?,
                        top_rate: num(get(c, "top_rate")?)?,
                        middle_agi: num(get(c, "middle_agi")?)?,
                        top_agi: num(get(c, "top_agi")?)?,
                        second_start: num(get(c, "second_start")?)?,
                        bracket_top: three(get(c, "bracket_top")?)?,
                        benefit: three(get(c, "benefit")?)?,
                        upper_benefit: three(get(c, "upper_benefit")?)?,
                    },
                }
            } else {
                Recapture::Simple { start, span }
            }
        };
        let family = match p.opt("family_adjustment")? {
            Some(f) => Some(Family {
                low_income_limit: num(get(f, "low_income_limit")?)?,
                high_income_limit: num(get(f, "high_income_limit")?)?,
                low_table: pairs(get(f, "low_table")?)?,
                high_table: pairs(get(f, "high_table")?)?,
            }),
            None => None,
        };
        let real_property = match p.opt("real_property_deduction")? {
            Some(r) => Some(RealProperty {
                rent_share: num(get(r, "rent_share")?)?,
                rent_cap: num(get(r, "rent_cap")?)?,
                property_tax_cap: num(get(r, "property_tax_cap")?)?,
                tiers: params::matrix(get(r, "tiers")?)?.into_iter().map(|t| [t[0], t[1], t[2], t[3]]).collect(),
            }),
            None => None,
        };
        let maximum_tax = if p.in_effect("maximum_tax_savings")? {
            Some((Brackets::new(&params::brackets(p.value("maximum_tax_savings", &[])?)?), num(get(&doc, "maximum_tax_property_income_share")?)?))
        } else {
            None
        };
        let unearned = match p.opt("unearned_income_tax")? {
            Some(u) => Some(Unearned {
                rate: num(get(u, "rate")?)?,
                phase_in_start: num(get(u, "phase_in_start")?)?,
                phase_in_range: num(get(u, "phase_in_range")?)?,
            }),
            None => None,
        };
        let limits = p.value("no_tax_threshold", &[])?;
        let no_tax = if year <= 1985 {
            NoTax::PerTaxpayer(num(get(limits, "per_taxpayer")?)?)
        } else if year == 1986 {
            NoTax::Early { single: num(get(limits, "single")?)?, other: num(get(limits, "other")?)? }
        } else if limits.is_mapping() {
            NoTax::ByStatus(status_direct(limits)?)
        } else {
            NoTax::Scalar(num(limits)?)
        };
        let household = if p.in_effect("household_credit")? {
            let h = p.value("household_credit", &[])?;
            if h.is_sequence() {
                Household::Table(pairs(h)?)
            } else {
                Household::Dict { single: pairs(get(h, "single")?)?, married: pairs(get(h, "married")?)?, additional: pairs(get(h, "per_additional_person")?)? }
            }
        } else {
            Household::None
        };
        let care_v = p.value("child_care_share", &[])?;
        let care = if !care_v.is_mapping() {
            Care::Flat(num(care_v)?)
        } else if care_v.get("by_agi").is_some() {
            Care::ByAgi(pairs(get(care_v, "by_agi")?)?)
        } else if care_v.get("pieces").is_none() {
            Care::Taper {
                full_limit: num(get(care_v, "full_limit")?)?,
                full_share: num(get(care_v, "full_share")?)?,
                taper_end: num(get(care_v, "taper_end")?)?,
                taper_share: num(get(care_v, "taper_share")?)?,
                taper_slope: num(get(care_v, "taper_slope")?)?,
                taper_start: num(get(care_v, "taper_start")?)?,
                floor_share: num(get(care_v, "floor_share")?)?,
            }
        } else {
            Care::Pieces(params::matrix(get(care_v, "pieces")?)?.into_iter().map(|r| [r[0], r[1], r[2]]).collect())
        };
        let maximum = |v: &Value| -> Result<Maximum, String> { if v.is_sequence() { Ok(Maximum::Table(pairs(v)?)) } else { Ok(Maximum::Scalar(num(v)?)) } };
        let property_credit = if p.in_effect("property_credit")? {
            let c = p.value("property_credit", &[])?;
            if c.get("homeowner_share").is_some() {
                PropertyCredit::Homeowner {
                    income_limit: num(get(c, "income_limit")?)?,
                    maximum: maximum(get(c, "maximum")?)?,
                    aged_maximum: pairs(get(c, "aged_maximum")?)?,
                    threshold: pairs(get(c, "threshold")?)?,
                    homeowner_share: num(get(c, "homeowner_share")?)?,
                    renter_share: num(get(c, "renter_share")?)?,
                }
            } else if c.get("aged_maximum_step").is_some() {
                PropertyCredit::Step {
                    income_limit: num(get(c, "income_limit")?)?,
                    maximum: num(get(c, "maximum")?)?,
                    step: num(get(c, "aged_maximum_step")?)?,
                    low: num(get(c, "aged_maximum_low_income")?)?,
                    high: num(get(c, "aged_maximum_high_income")?)?,
                    threshold: pairs(get(c, "threshold")?)?,
                }
            } else {
                PropertyCredit::Standard {
                    income_limit: num(get(c, "income_limit")?)?,
                    maximum: maximum(get(c, "maximum")?)?,
                    aged_maximum: pairs(get(c, "aged_maximum")?)?,
                    threshold: pairs(get(c, "threshold")?)?,
                    aged_threshold: pairs(get(c, "aged_threshold")?)?,
                }
            }
        } else {
            PropertyCredit::None
        };
        let eitc2020 = if year >= 2020 {
            let prior = match p.opt("eitc_childless_prior_schedule")? {
                Some(s) => Some((eitc_law(root, year - 1)?, num(get(s, "agi_factor")?)?, num(get(s, "credit_factor")?)?)),
                None => None,
            };
            Some(Eitc2020 { current: eitc_law(root, year)?, prior, aged_age: num(get(&misc, "childless_aged_age")?)? })
        } else {
            None
        };
        let empire = if p.in_effect("empire_child_credit")? {
            let e = p.value("empire_child_credit", &[])?;
            let limit = |which: &str| -> Result<f64, String> { Ok(num(get(get(e, "income_limit")?, which)?)?) };
            if e.get("under_4_amount").is_some() {
                Empire::Under4 {
                    under_4: num(get(e, "under_4_amount")?)?,
                    amount: num(get(e, "amount")?)?,
                    per_1000: num(get(e, "phaseout_per_1000")?)?,
                    threshold: status_direct(get(e, "threshold")?)?,
                }
            } else if e.get("income_limit").is_some() && e.get("per_child").is_none() {
                Empire::Share { limit_single: limit("single")?, limit_married: limit("married")?, minimum: num(get(e, "minimum_per_child")?)?, share: num(get(e, "federal_share")?)? }
            } else if e.get("per_child").is_some() {
                Empire::PerChild {
                    limit_single: limit("single")?,
                    limit_married: limit("married")?,
                    per_child: num(get(e, "per_child")?)?,
                    phaseout_rate: num(get(e, "phaseout_rate")?)?,
                    floor: num(get(e, "earned_income_floor")?)?,
                    refundable_rate: num(get(e, "refundable_rate")?)?,
                    payroll_rate: num(get(e, "payroll_rate")?)?,
                    many_children: num(get(e, "many_children")?)?,
                    minimum: num(get(e, "minimum_per_child")?)?,
                    share: num(get(e, "federal_share")?)?,
                    excludes_under_4: p.value("empire_child_credit_excludes_under_4", &[])?.as_bool().ok_or("expected a boolean")?,
                    wage_base: p.f("payroll_wage_base")?,
                }
            } else {
                Empire::None
            }
        } else {
            Empire::None
        };
        Ok(Self {
            year,
            loss_limit: if year >= 1987 { num(resolve_year(get(&capital_gains, "net_capital_loss_limit")?, year)?)? } else { 0.0 },
                    charity_addback: opt("charity_nonitemizer_addback")?,
            pension_exclusion: num(get(&doc, "pension_exclusion_per_aged_taxpayer")?)?,
            capital_gains_addback: p.f("capital_gains_addback")?,
            stds: p.num_at("standard_deduction", &["single"])?,
            stdm: p.num_at("standard_deduction", &["married"])?,
            stdh: p.num_at("standard_deduction", &["head_of_household"])?,
            standard_share: if p.in_effect("standard_deduction_share")? { Some((p.f("standard_deduction_share")?, p.f("standard_deduction_cap")?)) } else { None },
            standard_dependent: opt("standard_deduction_dependent")?,
            salt_cap: if year >= 2018 { opt("salt_cap")? } else { None },
            limit_thresholds: if year >= 2018 {
                Some([
                    p.num_at("itemized_limit_threshold", &["single"])?,
                    p.num_at("itemized_limit_threshold", &["head_of_household"])?,
                    p.num_at("itemized_limit_threshold", &["married"])?,
                ])
            } else {
                None
            },
            limit_max_share: if year >= 2018 { p.f("itemized_limit_max_share")? } else { 0.0 },
            limit_rate: if year >= 2018 { p.f("itemized_limit_rate")? } else { 0.0 },
            reduction_share: opt("itemized_reduction_share")?,
            reduction_range: num(get(&doc, "itemized_reduction_range")?)?,
            reduction_upper_start: num(get(&doc, "itemized_reduction_upper_start")?)?,
            reduction_upper_end: num(get(&doc, "itemized_reduction_upper_end")?)?,
            reduction_threshold: status_direct(get(&doc, "itemized_reduction_threshold")?)?,
            reduction_top_factor: num(get(&doc, "itemized_reduction_top_factor")?)?,
            reduction_start: num(get(&doc, "itemized_reduction_start")?)?,
            adjustment_threshold: status_direct(get(&doc, "itemized_adjustment_threshold")?)?,
            adjustment_rate: num(get(&doc, "itemized_adjustment_rate")?)?,
            adjustment_top_rate: num(get(&doc, "itemized_adjustment_top_rate")?)?,
            exemption_per_person: p.f("exemption_per_person")?,
            family,
            family_low_share: num(get(&doc, "family_adjustment_low_share")?)?,
            family_rates: Brackets::new(&params::brackets(get(&doc, "family_adjustment_rates")?)?),
            real_property,
            schedule,
            recapture,
            maximum_tax,
            unearned,
            no_tax,
            no_tax_dependent: if year >= 1987 { p.f("no_tax_dependent")? } else { 0.0 },
            household,
            household_addition: opt("household_credit_addition")?.unwrap_or(0.0),
            care,
            property_credit,
            property_share: p.f("property_tax_share")?,
            property_rent_share: num(get(&doc, "property_credit_rent_share")?)?,
            property_rent_limit: num(get(&doc, "property_credit_rent_limit")?)?,
            eitc_rate: if (1994..=2019).contains(&year) || year >= 2020 { p.f("eitc_match_rate")? } else { 0.0 },
            eitc2020,
            empire,
            family_relief: match p.opt("family_tax_relief")? {
                Some(r) => Some((num(get(r, "amount")?)?, num(get(r, "income_min")?)?, num(get(r, "income_max")?)?)),
                None => None,
            },
            dividend_adjustment: dividend_adjustment(root)?,
        })
    }
}

fn blend(statax: f64, target: f64, ratio: f64, addition: f64) -> f64 {
    statax + ratio * (target - statax - addition) + addition
}

fn federal_eitc(law: &EitcLaw, earned: f64, agi: f64, disqy: f64, fs: Fs, nkids: usize) -> f64 {
    let credit = match law.rows[fs as usize][nkids] {
        Some(r) => {
            let phase_in = (earned * r.rate_in).max(0.0).min(r.max_credit);
            let phaseout_income = earned.max(agi);
            let reduction = ((phaseout_income - r.phaseout_start) * r.rate_out).max(0.0);
            phase_in.min((r.max_credit - reduction).max(0.0)).max(0.0)
        }
        None => 0.0,
    };
    (credit - (disqy - law.dylim).max(0.0)).max(0.0)
}

fn eitc_for(e: &Eitc2020, law: &EitcLaw, age_law: i64, i: &Inp, f: &Fed, fs: Fs, agi_value: f64, disqy: f64, nkids: usize) -> f64 {
    let credit = federal_eitc(law, f.earned_income, agi_value, disqy, fs, nkids);
    let childless = nkids == 0;
    let txp = i.taxpayer_count();
    let filer_ok = !i.is_dependent_filer() && (age_law == 2021 || !(childless && i.aged_count() >= txp));
    let age_ok = if age_law < 1994 {
        true
    } else {
        // The minimum age comes from the year whose rules govern eligibility.
        let min_age = law.minimum_age;
        let older = i.page.max(i.sage);
        let younger = i.page.min(i.sage);
        let too_young = |age: f64| age > 0.0 && age < min_age;
        let ineligible = if age_law == 2021 { too_young(older) || too_young(younger) } else { too_young(older) || (older > e.aged_age && too_young(younger)) };
        !(childless && ineligible)
    };
    if filer_ok && age_ok { credit } else { 0.0 }
}

pub fn tax(i: &Inp, f: &Fed, p: &NyParams) -> f64 {
    let y = p.year;
    let fs = i.fs();
    let is_single = fs == Fs::Single;
    let is_joint = fs == Fs::Joint;
    let is_sep = fs == Fs::Separate;
    let is_hoh = fs == Fs::Hoh;
    let sep = i.sepret();
    let txp = i.taxpayer_count();
    let aged = i.aged_count();
    let dependent_filer = i.is_dependent_filer();
    let depx = i.depx;
    let exemps = federal_exemption_count(y, i);
    let salt_ded = f.ded;
    let fullcg = i.stcg + i.ltcg;
    let hy = household_income(i, p.dividend_adjustment);
    let ny_ui = unemployment_total(i);
    let ny_dividends = i.dividends + p.dividend_adjustment;
    let taxbca = f.regular_tax + f.amt;

    // ---- federal values ----
    let (deducp, fed_itemizes, fed_deduc, capgn);
    if y <= 1986 {
        deducp = i.proptax + i.otheritem + i.mortgage + salt_ded;
        let fed_zbr = f.pre87.zbr;
        fed_itemizes = if y <= 1981 { deducp > fed_zbr } else { f.forced_itemize };
        fed_deduc = if fed_itemizes { deducp } else { 0.0 };
        capgn = f.pre87.capgn;
    } else {
        fed_itemizes = f.itemizes;
        let agix = f.agi.max(0.0);
        let alim50 = if y == 2020 || y == 2021 { 1.0e20 } else { 0.5 * agix };
        let fed_char = (if f.agi < 0.0 { 0.0 } else { i.charity_cash.min(alim50) }).max(0.0);
        deducp = if y <= 2017 { (i.proptax + i.otheritem + salt_ded).max(0.0) + i.mortgage + fed_char } else { f.itemized_deduction };
        fed_deduc = if fed_itemizes { f.itemized_deduction } else { 0.0 };
        capgn = fullcg.max(-p.loss_limit / sep);
    }
    let mut fed_agi = f.agi;
    let mut charity_addback = 0.0;
    if let Some(amount) = p.charity_addback {
        let limit = if y == 2020 { amount / sep } else { amount * txp };
        charity_addback = if fed_itemizes || i.charity_cash <= 0.0 { 0.0 } else { limit.min(i.charity_cash) };
        if y == 2020 {
            fed_agi -= if fed_itemizes { 0.0 } else { (amount / sep).min(i.charity_cash) };
        }
    }

    // ---- New York AGI ----
    let mut agi = fed_agi + charity_addback;
    if y == 2020 {
        agi += ny_ui - f.taxable_unemployment;
    }
    let agieic = agi;
    agi -= i.pensions.min(p.pension_exclusion * aged);
    agi += p.capital_gains_addback * fullcg.max(0.0);
    if y >= 1984 {
        agi -= f.taxable_social_security;
    }

    // ---- standard deduction ----
    let stds = p.stds;
    let stdm = p.stdm;
    let stdh = if y <= 1986 { stds } else { p.stdh };
    let mut stded = if let Some((share, cap)) = p.standard_share {
        if is_single { (share * agi).max(stds).min(cap) } else { (share * agi).max(stdm).min(cap) / sep }
    } else if is_single || is_sep {
        stds
    } else if is_hoh {
        stdh
    } else {
        stdm
    };
    if let Some(amount) = p.standard_dependent {
        if dependent_filer {
            stded = amount;
        }
    }

    // ---- itemized deductions ----
    let mut xitded;
    if y <= 1990 {
        xitded = if fed_itemizes && deducp > 0.0 { (fed_deduc - salt_ded).max(0.0) } else { 0.0 };
    } else if y <= 2017 {
        xitded = if fed_itemizes && deducp > 0.0 { ((deducp - salt_ded) * fed_deduc / deducp).max(0.0) } else { 0.0 };
    } else {
        let sttax = match p.salt_cap {
            Some(cap) => (cap / sep).min(i.proptax + salt_ded + i.otheritem),
            None => f.salt_capped,
        };
        xitded = deducp + i.proptax + i.otheritem - sttax;
        let t = p.limit_thresholds.expect("itemized limit thresholds");
        let phase = if is_single { t[0] } else if is_hoh { t[1] } else { t[2] / sep };
        let dedphs = (p.limit_max_share * xitded).min(p.limit_rate * (fed_agi - phase));
        xitded -= if fed_agi > phase { dedphs } else { 0.0 };
    }
    let rng = p.reduction_range;
    if let Some(share) = p.reduction_share {
        let threshold = p.reduction_threshold[fs as usize];
        let xover = if agi > p.reduction_start && agi <= p.reduction_upper_start {
            (agi - threshold).max(0.0).min(rng) / rng
        } else if agi > p.reduction_upper_start && agi <= p.reduction_upper_end {
            (agi - p.reduction_upper_start) / rng
        } else if agi > p.reduction_upper_end {
            p.reduction_top_factor
        } else {
            0.0
        };
        xitded = (xitded - xitded * xover * share).max(0.0);
    }
    let phasit = p.adjustment_threshold[fs as usize];
    let adjust = if agi > p.reduction_start && agi <= p.reduction_upper_start {
        p.adjustment_rate * xitded * (rng.min(agi - phasit) / rng).max(0.0)
    } else if agi > p.reduction_upper_start && agi <= p.reduction_upper_end {
        p.adjustment_rate * xitded * (agi - p.reduction_upper_start) / rng
    } else if agi > p.reduction_upper_end {
        p.adjustment_top_rate * xitded
    } else {
        0.0
    };
    if agi > p.reduction_start {
        xitded = (xitded - adjust).max(0.0);
    }
    xitded = xitded.max(0.0);
    let deduc = stded.max(xitded);

    // ---- exemptions and taxable income ----
    let exemp = (if y <= 1987 { exemps } else { depx }) * p.exemption_per_person;
    let mut taxinc = (agi - deduc - exemp).max(0.0);
    let mut taxy = taxinc;
    let mut famded = 0.0;
    if let Some(fam) = &p.family {
        taxy = if is_sep { 2.0 * taxinc } else { taxinc };
        let low = (p.family_low_share * taxy - fam.low_table.eval(agi)).max(0.0);
        let high = fam.high_table.eval(agi);
        famded = if taxy <= fam.low_income_limit { low } else if taxy <= fam.high_income_limit { high } else { 0.0 };
        if !(is_joint || is_sep) {
            famded = 0.0;
        }
        taxinc = (taxinc - famded).max(0.0);
    }
    // 1979: taxpayers 65 or older deduct part of their property tax or rent.
    let mut reald = 0.0;
    if let Some(r) = &p.real_property {
        let renttx = if i.rentpaid <= r.rent_cap { r.rent_share * i.rentpaid } else { 0.0 };
        let home = if i.proptax <= r.property_tax_cap { i.proptax } else { 0.0 };
        let ptax79 = renttx.max(home);
        for t in &r.tiers {
            if hy >= t[0] && hy < t[1] && ptax79 > t[2] * hy {
                reald = t[3];
            }
        }
        if !(aged > 0.0) {
            reald = 0.0;
        }
        taxinc = (taxinc - reald).max(0.0);
    }

    // ---- tax ----
    let (table_tax, rt) = match &p.schedule {
        Schedule::One(b) => (b.tax(taxinc), b.rate(taxinc)),
        Schedule::ByClass(b) => (b[class(fs)].tax(taxinc), b[class(fs)].rate(taxinc)),
    };
    let mut statax = recapture(p, table_tax, taxinc, agi, rt, fs);
    if famded > 0.0 {
        statax += p.family_rates.tax(famded);
    }
    if let Some((savings, share)) = &p.maximum_tax {
        let business = i.psemp + i.ssemp + i.pwages.min(0.0) + i.swages.min(0.0);
        let psinc = (i.pwages.max(0.0) + i.swages.max(0.0) + business + i.pensions + share * i.otherprop).max(0.0);
        let percen = if agi > 0.0 { 1.0f64.min(psinc / agi) } else { 0.0 };
        let pstinc = (percen * taxy - f.pre87.pref.max(0.0)).max(0.0);
        statax -= savings.tax(pstinc);
    }
    if let Some(u) = &p.unearned {
        let unearned = (i.dividends + i.intrec + fullcg.max(0.0)).max(0.0);
        let share = ((agi - u.phase_in_start / sep) / u.phase_in_range).clamp(0.0, 1.0);
        statax += u.rate * share * unearned;
    }

    // ---- filing thresholds ----
    let no_tax = match &p.no_tax {
        NoTax::PerTaxpayer(per) => agi <= per * txp * sep || agi <= exemp,
        NoTax::Early { single, other } => (if is_single { agi <= *single } else { agi <= *other }) || agi <= exemp,
        NoTax::Scalar(v) => agi <= if dependent_filer { p.no_tax_dependent } else { *v },
        NoTax::ByStatus(v) => agi <= if dependent_filer { p.no_tax_dependent } else { v[fs as usize] },
    };
    let taxbc = if no_tax { 0.0 } else { statax };
    statax = taxbc;

    // ---- credits ----
    let mut hcred = match &p.household {
        Household::None => 0.0,
        Household::Table(t) => (t.eval(fed_agi) + p.household_addition) / sep,
        Household::Dict { single, married, additional } => {
            let extra_people = exemps - 1.0;
            if is_single { single.eval(fed_agi) } else { (married.eval(fed_agi) + extra_people * additional.eval(fed_agi)) / sep }
        }
    };
    if dependent_filer {
        hcred = 0.0;
    }
    statax = (statax - hcred).max(0.0);

    let chcrbc = if y >= 1987 { f.ccc_uncapped } else { f.federal_chcr };
    let _chcr = match &p.care {
        Care::Flat(share) => {
            let c = statax.min(share * chcrbc);
            statax = (statax - c).max(0.0);
            c
        }
        care => {
            let share = match care {
                Care::ByAgi(t) => t.eval(agi),
                Care::Taper { full_limit, full_share, taper_end, taper_share, taper_slope, taper_start, floor_share } => {
                    if agi <= *full_limit {
                        *full_share
                    } else if agi < *taper_end {
                        taper_share - taper_slope * (agi - taper_start)
                    } else {
                        *floor_share
                    }
                }
                Care::Pieces(pieces) => {
                    let mut share = 0.0;
                    for [start, value, slope] in pieces {
                        if agi >= *start {
                            share = value - slope * (agi - start);
                        }
                    }
                    share
                }
                Care::Flat(_) => 0.0,
            };
            let c = chcrbc * share;
            statax -= c;
            c
        }
    };

    // Real property tax credit (refundable).
    let rentpaid = i.rentpaid;
    let ptax = p.property_share * i.proptax + p.property_rent_share * rentpaid;
    let is_aged = aged > 0.0;
    let maximum_at = |m: &Maximum| match m {
        Maximum::Scalar(v) => *v,
        Maximum::Table(t) => t.eval(hy),
    };
    let mut pcred = match &p.property_credit {
        PropertyCredit::None => 0.0,
        PropertyCredit::Homeowner { income_limit, maximum, aged_maximum, threshold, homeowner_share, renter_share } => {
            let pmax = if is_aged { aged_maximum.eval(hy) } else { maximum_at(maximum) };
            let mut base = (ptax - hy * threshold.eval(hy)).max(0.0);
            if i.proptax > 0.0 {
                base = (homeowner_share * base).min(pmax);
            }
            if rentpaid > 0.0 {
                base = (renter_share * base).min(pmax);
            }
            if hy <= *income_limit { base } else { 0.0 }
        }
        PropertyCredit::Step { income_limit, maximum, step, low, high, threshold } => {
            let pmax = if is_aged { if hy <= *step { *low } else { *high } } else { *maximum };
            if hy <= *income_limit { (ptax - hy * threshold.eval(hy)).max(0.0).min(pmax) } else { 0.0 }
        }
        PropertyCredit::Standard { income_limit, maximum, aged_maximum, threshold, aged_threshold } => {
            let pmax = if is_aged { aged_maximum.eval(hy) } else { maximum_at(maximum) };
            let share = if is_aged { aged_threshold.eval(hy) } else { threshold.eval(hy) };
            let mut pc = if hy <= *income_limit { (ptax - hy * share).max(0.0).min(pmax) } else { 0.0 };
            if p.real_property.is_some() && is_aged && reald > 0.0 {
                pc = 0.0;
            }
            pc
        }
    };
    if p.property_rent_share * rentpaid > p.property_rent_limit {
        pcred = 0.0;
    }

    // ---- earned income credit ----
    let mut earncr = 0.0;
    if (1994..=1995).contains(&y) {
        earncr = p.eitc_rate * f.eitc;
    } else if (1996..=2019).contains(&y) {
        earncr = (p.eitc_rate * f.eitc - hcred.min(taxbc)).max(0.0);
    } else if y >= 2020 {
        let e = p.eitc2020.as_ref().expect("2020 earned income credit parameters");
        let nkids = i.dep18.clamp(0.0, 3.0) as usize;
        let disqy = capgn.max(0.0) + ny_dividends + i.intrec + i.otherprop.max(0.0);
        let mut eitc = eitc_for(e, &e.current, y, i, f, fs, agieic, disqy, nkids);
        if let Some((prior, agi_factor, credit_factor)) = &e.prior {
            let aged_single_childless = is_single && i.dep17 < 1.0 && aged >= txp;
            let childless_single = is_single && i.dep17 < 1.0 && aged < txp;
            if childless_single {
                // The prior year's schedule, with this year's eligibility rules.
                let shifted = EitcLaw { rows: prior.rows, dylim: prior.dylim, minimum_age: e.current.minimum_age };
                eitc = credit_factor * eitc_for(e, &shifted, y, i, f, fs, agieic * agi_factor, disqy, nkids);
            } else if aged_single_childless {
                eitc = 0.0;
            }
        }
        earncr = (p.eitc_rate * eitc - hcred.min(taxbc)).max(0.0);
    }

    // ---- Empire State child credit ----
    let eschcr = match &p.empire {
        Empire::None => 0.0,
        Empire::Under4 { under_4, amount, per_1000, threshold } => {
            let young = i.children_under_4.min(i.dep17);
            let base = under_4 * young + amount * (i.dep17 - young);
            let thr = threshold[fs as usize];
            let reduction = per_1000 * ((f.agi - thr).max(0.0) / 1000.0).floor();
            (base - reduction).max(0.0)
        }
        Empire::Share { limit_single, limit_married, minimum, share } => {
            let cphase = if is_single || is_hoh { *limit_single } else { limit_married / sep };
            if fed_agi <= cphase { (minimum * i.dep17).max(share * (f.actc + f.odc)) } else { 0.0 }
        }
        Empire::PerChild { limit_single, limit_married, per_child, phaseout_rate, floor, refundable_rate, payroll_rate, many_children, minimum, share, excludes_under_4, wage_base } => {
            let cphase = if is_single || is_hoh { *limit_single } else { limit_married / sep };
            let mut fagi = f.agi;
            let mut dep17 = i.dep17;
            let mut dep18 = i.dep18;
            let mut kids = depx;
            if *excludes_under_4 {
                let young = i.children_under_4;
                dep17 -= young;
                dep18 -= young;
                kids = depx - young;
            }
            if y == 2020 {
                fagi += ny_ui - f.taxable_unemployment;
            }
            let precrd = (per_child * dep18 - phaseout_rate * (fagi - cphase).max(0.0)).max(0.0);
            let ctcred = precrd.min((taxbca - f.ccc - f.federal_elder).max(0.0));
            let earned = f.earned_income;
            let fift = refundable_rate * (earned - floor).max(0.0);
            let remaining = precrd - ctcred;
            let ssmtax = payroll_rate * wage_base.min(earned.max(0.0));
            let mut chcr1 = remaining.min(fift);
            if depx > many_children - 1.0 && fift < remaining {
                chcr1 = remaining.min(fift.max((ssmtax - f.eitc).max(0.0)));
            }
            let base = chcr1 + ctcred;
            let per_child_credit = share * base / kids.max(1.0);
            // The $100-per-child minimum applies at every income in the dated statutory years.
            let floor_applies = fed_agi > cphase || y >= 2022;
            let credit = if floor_applies { dep17 * minimum.max(per_child_credit) } else { dep17 * per_child_credit };
            if kids > 0.0 && precrd > 0.0 && dep17 > 0.0 { credit } else { 0.0 }
        }
    };

    statax = statax - earncr - eschcr - pcred;
    if let Some((amount, income_min, income_max)) = p.family_relief {
        if statax >= 0.0 && agi >= income_min && agi <= income_max && i.dep17 > 0.0 {
            statax -= amount;
        }
    }
    statax
}

/// High-income recapture of the benefit of lower brackets.
fn recapture(p: &NyParams, statax: f64, taxinc: f64, agi: f64, rt: f64, fs: Fs) -> f64 {
    let cls = class(fs);
    let is_single_class = matches!(fs, Fs::Single | Fs::Separate);
    let is_hoh = fs == Fs::Hoh;
    let is_joint = fs == Fs::Joint;
    match &p.recapture {
        Recapture::None => statax,
        Recapture::Supplemental(d) => {
            let c = &d.classes[cls];
            let previous = c.starts[c.starts.partition_point(|s| *s <= taxinc).saturating_sub(1)];
            let applicable = (agi - previous.max(d.min_agi)).max(0.0);
            let fraction = (applicable / d.phase_in_length).min(1.0);
            let supplemental = c.base.rate(taxinc) + fraction * c.incremental.rate(taxinc);
            let high = taxinc * d.top_rate - statax;
            statax + if agi > d.top_agi { high } else { supplemental }
        }
        Recapture::Tiers(c) => {
            let ratio = |start: f64| round_half_even((agi - start) / c.phase, c.decimals);
            let worksheet = |rate: f64, addition: f64, start: f64| -> f64 {
                let flat = taxinc * rate;
                let partial = statax + addition + ratio(start) * (flat - statax - addition);
                if agi >= start + c.phase { flat } else { partial }
            };
            let k = &c.classes[cls];
            let idx = k.uppers.index(taxinc);
            let tiered = worksheet(k.rates[idx], k.additions[idx], k.starts[idx]);
            let top = worksheet(c.top_rate, k.top_additions[k.tops.index(taxinc)], c.top_agi);
            if agi <= c.base_agi { statax } else if agi > c.top_agi { top } else { tiered }
        }
        Recapture::Bounds(b) => {
            let a = b.inflation;
            let r = b.rates;
            let add = b.additions;
            let base = b.base_agi * a;
            let phase = b.phase;
            let ratio_from = |start: f64| phase.min(agi - start) / phase;
            if is_joint {
                let t1 = b.married_tier1 * a;
                let t2 = b.married_tier2 * a;
                let top = b.married_top * a;
                let ws1 = if agi >= base + phase { taxinc * r[0] } else { statax + ratio_from(base) * (taxinc * r[0] - statax) };
                let ws2 = if agi >= t1 + phase { taxinc * r[1] } else { blend(statax, taxinc * r[1], ratio_from(t1), add[0]) };
                let ws3 = if agi >= t2 + phase { taxinc * r[2] } else { blend(statax, taxinc * r[2], ratio_from(t2), add[1]) };
                let r4 = ratio_from(top);
                let ws4_partial = if taxinc <= t1 {
                    blend(statax, taxinc * r[3], r4, add[0])
                } else if taxinc > t1 && taxinc < t2 {
                    blend(statax, taxinc * r[3], r4, add[1])
                } else if taxinc > t2 {
                    blend(statax, taxinc * r[3], r4, add[2])
                } else {
                    statax
                };
                let ws4 = if agi >= top + phase { taxinc * r[3] } else { ws4_partial };
                if agi > base && agi <= top && taxinc <= t1 {
                    ws1
                } else if agi > t1 && agi <= top && taxinc > t1 && taxinc <= t2 {
                    ws2
                } else if agi > t2 && agi <= top && taxinc > t2 {
                    ws3
                } else if agi > top {
                    ws4
                } else {
                    statax
                }
            } else if is_single_class {
                let st = b.single_tier * a;
                let stop = b.single_top * a;
                let ws5 = if agi >= base + phase { taxinc * r[1] } else { statax + ratio_from(base) * (taxinc * r[1] - statax) };
                let ws6 = if agi >= st + phase { taxinc * r[2] } else { blend(statax, taxinc * r[2], ratio_from(st), add[3]) };
                let r7 = ratio_from(stop);
                let ws7_partial = if taxinc <= st { blend(statax, taxinc * r[3], r7, add[3]) } else { blend(statax, taxinc * r[3], r7, add[4]) };
                let ws7 = if agi >= stop + phase { taxinc * r[3] } else { ws7_partial };
                if agi > base && agi <= stop && taxinc <= st {
                    ws5
                } else if agi > st && agi <= stop && taxinc > st {
                    ws6
                } else if agi > stop {
                    ws7
                } else {
                    statax
                }
            } else {
                let ht = b.hoh_tier * a;
                let htop = b.hoh_top * a;
                let ws8 = if agi >= base + phase { taxinc * r[1] } else { statax + ratio_from(base) * (taxinc * r[1] - statax) };
                let ws9 = if agi >= ht + phase { taxinc * r[2] } else { blend(statax, taxinc * r[2], ratio_from(ht), add[5]) };
                let r10 = ratio_from(htop);
                let partial = if taxinc <= ht { blend(statax, taxinc * r[3], r10, add[5]) } else { blend(statax, taxinc * r[3], r10, add[6]) };
                let ws10 = if agi >= htop + phase { taxinc * r[3] } else { partial };
                if agi > base && agi <= htop && taxinc <= ht {
                    ws8
                } else if agi > ht && agi <= htop && taxinc > ht {
                    ws9
                } else if agi > htop {
                    ws10
                } else {
                    statax
                }
            }
        }
        Recapture::TopBracket { start, span, c } => {
            let top = c.bracket_top[cls];
            let benefit = c.bracket_benefit[cls];
            let ratio = (agi - start) / span;
            let within = (is_single_class && taxinc <= top) || (is_hoh && taxinc <= top) || is_joint;
            let above = !is_joint && taxinc > top;
            let mut low = statax + if within { (taxinc * c.flat_rate - statax) * ratio } else { 0.0 };
            low += if above { benefit * ratio } else { 0.0 };
            let high_base = if taxinc <= top { taxinc * c.flat_rate } else { statax };
            let ratio2 = span.min(agi - (start + span)) / span;
            let high = if taxinc > top { benefit + high_base + (taxinc * c.top_rate - high_base - benefit) * ratio2 } else { high_base };
            if agi > *start && agi <= start + span {
                low
            } else if agi > start + span && agi <= c.top_agi {
                high
            } else if agi > c.top_agi {
                taxinc * c.top_flat_rate
            } else {
                statax
            }
        }
        Recapture::Millionaire { start, span, c } => {
            let top = c.bracket_top[cls];
            let benefit = c.benefit[cls];
            let upper = c.upper_benefit[cls];
            let ratio1 = span.min(agi - start) / span;
            let ratio2 = span.min(agi - c.second_start) / span;
            let ratio3 = span.min(agi - c.middle_agi) / span;
            // Above the bracket top, single and head-of-household filers phase in from the bracket top.
            let ratio3_upper = if is_joint { ratio3 } else { span.min(agi - top) / span };
            let middle = if taxinc <= top { taxinc * c.flat_rate } else { blend(statax, taxinc * c.middle_rate, ratio2, benefit) };
            let upper_band = if taxinc <= top { blend(statax, taxinc * c.top_rate, ratio3, benefit) } else { blend(statax, taxinc * c.top_rate, ratio3_upper, upper) };
            if agi > *start && agi <= start + span {
                statax + ratio1 * (taxinc * c.flat_rate - statax)
            } else if agi > start + span && agi <= c.middle_agi {
                middle
            } else if agi > c.middle_agi && agi <= c.top_agi {
                upper_band
            } else if agi > c.top_agi {
                taxinc * c.top_rate
            } else {
                statax
            }
        }
        Recapture::Simple { start, span } => {
            let ratio = span.min(agi - start) / span;
            if agi > *start && agi <= start + span {
                statax + (taxinc * rt - statax) * ratio
            } else if agi > start + span {
                taxinc * rt
            } else {
                statax
            }
        }
    }
}
