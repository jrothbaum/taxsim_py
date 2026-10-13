//! District of Columbia income tax (port of `calculators.states.dc`).

use std::path::Path;

use crate::common::Brackets;
use crate::federal::{Fed, Inp};
use crate::params::{self, get, load, num, status_at, Fs, YearParams};
use crate::states::{
    dividend_adjustment, dividend_exclusion_addback, higher_earner_share, household_income, law_year_is_exact, Tiers,
};

struct Percent {
    rate: f64,
    cap: f64,
}

enum Standard {
    Percent(Percent),
    ByStatus { joint: f64, hoh: f64, single: f64 },
    Flat { amount: f64, property_addition: Option<f64> },
    Federal { single: f64, joint: f64, hoh: f64 },
}

struct Federal2018 {
    aged: [f64; 4],
    minimum: f64,
    earned_addition: f64,
}

struct ByIncome {
    cap: f64,
    income_limit: f64,
    aged_base_rate: f64,
    aged_rate_income: f64,
    base_pct: f64,
    divisor: f64,
    low_income_limit: f64,
    low_income_share: f64,
    share: f64,
}

struct Schedule {
    max: f64,
    rent_share: f64,
    elderly_age: f64,
    nonelderly: Tiers,
    nonelderly_rates: Vec<f64>,
    nonelderly_limit: f64,
    elderly: Tiers,
    elderly_rates: Vec<f64>,
    elderly_limit: f64,
}

struct Rates {
    low_income: f64,
    low_rate: f64,
    rate: f64,
    middle_income: f64,
    high_rate: f64,
    income_limit: f64,
    aged_income_limit: f64,
    max: f64,
}

enum Property {
    ByIncome(ByIncome),
    Schedule(Schedule),
    Rates(Rates),
}

struct LowIncome {
    joint: Vec<f64>,
    single: Vec<f64>,
    hoh: Vec<f64>,
    per_dependent: f64,
}

struct Childless {
    max_credit: f64,
    max_earned: f64,
    phaseout_start: f64,
    phase_in_rate: f64,
    disqualified: f64,
    phaseout_rate: f64,
    minimum_age: f64,
    maximum_age: f64,
}

pub struct DcParams {
    year: i64,
    standard: Standard,
    federal_2018: Option<Federal2018>,
    state_tax_cap: Option<f64>,
    reduction_threshold: f64,
    reduction_rate: f64,
    exemption: f64,
    phaseout: Option<(f64, f64, f64, f64)>,
    brackets: Brackets,
    chcr_share: Option<f64>,
    rent_share: f64,
    property: Property,
    low_income: Option<LowIncome>,
    eitc_share: Option<f64>,
    childless: Option<Childless>,
    percent_year: bool,
    two_earner: Option<(f64, f64)>,
    dividend_adjustment: f64,
}

impl DcParams {
    pub fn load(root: &Path, year: i64) -> Result<Self, String> {
        let doc = load(root, "states/dc/income_tax.yaml")?;
        law_year_is_exact(root, &doc, year)?;
        let federal = load(root, "national/income_tax.yaml")?;
        let pre87 = load(root, "national/pre1987.yaml")?;
        let p = YearParams::new(&doc, year);
        let fed = YearParams::new(&federal, year);
        let opt = |key: &str| -> Result<Option<f64>, String> { Ok(if p.in_effect(key)? { Some(p.f(key)?) } else { None }) };
        let list = |v: &serde_yaml::Value| -> Result<Vec<f64>, String> { v.as_sequence().ok_or("expected a list")?.iter().map(num).collect() };

        let standard = if p.in_effect("standard_deduction_percent")? {
            let sd = p.value("standard_deduction_percent", &[])?;
            Standard::Percent(Percent { rate: num(get(sd, "rate")?)?, cap: num(get(sd, "cap")?)? })
        } else if p.in_effect("standard_deduction")? {
            let sd = p.value("standard_deduction", &[])?;
            if sd.is_mapping() {
                Standard::ByStatus { joint: num(get(sd, "married_joint")?)?, hoh: num(get(sd, "head_of_household")?)?, single: num(get(sd, "single")?)? }
            } else {
                Standard::Flat { amount: num(sd)?, property_addition: opt("standard_deduction_property_tax_addition")? }
            }
        } else {
            let mut single = fed.num_at("standard_deduction", &["single"])?;
            let mut joint = fed.num_at("standard_deduction", &["married_joint"])?;
            let mut hoh = fed.num_at("standard_deduction", &["head_of_household"])?;
            if let Some(n) = p.opt("standard_deduction_nonconforming")? {
                single = num(get(n, "single")?)?;
                joint = num(get(n, "married_joint")?)?;
                hoh = num(get(n, "head_of_household")?)?;
            }
            Standard::Federal { single, joint, hoh }
        };
        let federal_2018 = if year >= 2018 {
            let dep = YearParams::new(get(&federal, "dependent_standard_deduction")?, year);
            Some(Federal2018 {
                aged: status_at(get(&federal, "aged_standard_deduction")?, year)?,
                minimum: dep.f("minimum")?,
                earned_addition: num(dep.value("earned_income_addition", &[])?)?,
            })
        } else {
            None
        };
        let reduction = get(&doc, "itemized_reduction")?;
        let property = if p.in_effect("property_credit_by_income")? {
            let c = p.value("property_credit_by_income", &[])?;
            let n = |key: &str| -> Result<f64, String> { num(get(c, key)?) };
            Property::ByIncome(ByIncome {
                cap: n("cap")?,
                income_limit: n("income_limit")?,
                aged_base_rate: n("aged_base_rate")?,
                aged_rate_income: n("aged_rate_income")?,
                base_pct: n("base_pct")?,
                divisor: n("divisor")?,
                low_income_limit: n("low_income_limit")?,
                low_income_share: n("low_income_share")?,
                share: n("share")?,
            })
        } else if p.in_effect("property_credit_schedule")? {
            let s = p.value("property_credit_schedule", &[])?;
            let nonelderly = list(get(s, "nonelderly_upper_bounds")?)?;
            let elderly = list(get(s, "elderly_upper_bounds")?)?;
            Property::Schedule(Schedule {
                max: num(get(s, "max")?)?,
                rent_share: num(get(s, "rent_share")?)?,
                elderly_age: num(get(s, "elderly_age")?)?,
                nonelderly_limit: nonelderly[nonelderly.len() - 2],
                nonelderly: Tiers::new(nonelderly, false),
                nonelderly_rates: list(get(s, "nonelderly_rates")?)?,
                elderly_limit: elderly[elderly.len() - 2],
                elderly: Tiers::new(elderly, false),
                elderly_rates: list(get(s, "elderly_rates")?)?,
            })
        } else {
            let c = get(&doc, "property_credit_rates")?;
            let l = p.value("property_credit_limits", &[])?;
            let n = |key: &str| -> Result<f64, String> { num(get(c, key)?) };
            Property::Rates(Rates {
                low_income: n("low_income")?,
                low_rate: n("low_rate")?,
                rate: n("rate")?,
                middle_income: n("middle_income")?,
                high_rate: n("high_rate")?,
                income_limit: num(get(l, "income_limit")?)?,
                aged_income_limit: num(get(l, "aged_income_limit")?)?,
                max: num(get(l, "max")?)?,
            })
        };
        let low_income = if p.in_effect("low_income_credit_per_dependent")? {
            Some(LowIncome {
                joint: list(p.value("low_income_credit_joint_by_aged", &[])?)?,
                single: list(p.value("low_income_credit_single_by_aged", &[])?)?,
                hoh: list(p.value("low_income_credit_hoh_by_aged", &[])?)?,
                per_dependent: p.f("low_income_credit_per_dependent")?,
            })
        } else {
            None
        };
        let childless = if p.in_effect("eitc_federal_share")? && p.in_effect("eitc_childless")? {
            let c = p.value("eitc_childless", &[])?;
            let limits = get(&doc, "eitc_childless_age_limits")?;
            Some(Childless {
                max_credit: num(get(c, "max_credit")?)?,
                max_earned: num(get(c, "max_earned")?)?,
                phaseout_start: num(get(c, "phaseout_start")?)?,
                phase_in_rate: num(get(c, "phase_in_rate")?)?,
                disqualified: num(get(c, "disqualified_income_limit")?)?,
                phaseout_rate: num(get(&doc, "eitc_childless_phaseout_rate")?)?,
                minimum_age: num(get(limits, "minimum_age")?)?,
                maximum_age: num(get(limits, "maximum_age")?)?,
            })
        } else {
            None
        };
        Ok(Self {
            year,
            standard,
            federal_2018,
            state_tax_cap: opt("state_tax_deduction_cap")?,
            reduction_threshold: num(get(reduction, "threshold")?)?,
            reduction_rate: num(get(reduction, "rate")?)?,
            exemption: p.f("personal_exemption")?,
            phaseout: match p.opt("exemption_phaseout")? {
                Some(ep) => Some((num(get(ep, "floor")?)?, num(get(ep, "ceiling")?)?, num(get(ep, "band")?)?, num(get(ep, "share_per_band")?)?)),
                None => None,
            },
            brackets: Brackets::new(&params::brackets(p.value("brackets", &[])?)?),
            chcr_share: opt("child_care_credit_federal_share")?,
            rent_share: p.f("property_credit_rent_share")?,
            property,
            low_income,
            eitc_share: opt("eitc_federal_share")?,
            childless,
            percent_year: year <= 1981,
            two_earner: if (1982..=1986).contains(&year) {
                let q = YearParams::new(&pre87, year);
                Some((q.f("two_earner_deduction_rate")?, q.f("two_earner_deduction_cap")?))
            } else {
                None
            },
            dividend_adjustment: dividend_adjustment(root)?,
        })
    }
}

pub fn tax(i: &Inp, f: &Fed, p: &DcParams) -> f64 {
    let y = p.year;
    let fs = i.fs();
    let is_joint = fs == Fs::Joint;
    let is_hoh = fs == Fs::Hoh;
    let sep = i.sepret();
    let txp = i.taxpayer_count();
    let aged = i.aged_count();
    let salt = f.ded;
    let household = household_income(i, p.dividend_adjustment);
    let dep_filer = i.is_dependent_filer();

    // ---- AGI ----
    let mut dc_agi = if p.percent_year {
        // Other non-property income is not DC income before 1982.
        f.agi + dividend_exclusion_addback(y, i, f, p.dividend_adjustment) - f.taxable_unemployment - i.nonprop
    } else {
        let mut a = f.agi;
        if let Some((rate, cap)) = p.two_earner {
            a += (rate * i.pwages.min(i.swages).max(0.0)).max(0.0).min(cap);
        }
        // Social Security benefits are exempt from 1984.
        if y >= 1984 {
            a -= f.taxable_social_security;
        }
        a
    };
    // Unemployment compensation is excluded in every year.
    dc_agi -= crate::states::unemployment_total(i);

    // ---- standard deduction ----
    let mut dc_stded = match &p.standard {
        Standard::Percent(s) => (s.rate * dc_agi).max(0.0).min(s.cap / sep),
        Standard::ByStatus { joint, hoh, single } => if is_joint { *joint } else if is_hoh { *hoh } else { *single },
        Standard::Flat { amount, property_addition } => match property_addition {
            Some(add) => amount / sep + i.proptax.min(txp * add),
            None => amount / sep,
        },
        Standard::Federal { single, joint, hoh } => {
            if fs == Fs::Single { *single } else if is_hoh { *hoh } else { joint / sep }
        }
    };
    if let Some(g) = &p.federal_2018 {
        let earnkd = i.pwages + i.swages + (i.psemp + i.ssemp).max(0.0) + g.earned_addition;
        let std = dc_stded + g.aged[fs as usize] * aged;
        dc_stded = if dep_filer { std.min(g.minimum.max(earnkd)) } else { std };
    }
    let mut dc_deduc = dc_stded;

    // ---- itemized deduction ----
    let (raw, gate) = if y <= 1986 { (f.pre87.deduc, f.pre87.itemizes) } else { (f.salt_capped + i.mortgage, f.itemizes) };
    let mut xitded = 0.0;
    if y <= 1981 {
        xitded = raw;
    } else if y <= 2017 {
        let salt_share = salt / raw.max(1e-9);
        xitded = (f.itemized_deduction * (1.0 - salt_share)).max(0.0);
    }
    if (1982..=1986).contains(&y) {
        xitded = (raw - salt).max(0.0);
    }
    if let Some(cap) = p.state_tax_cap {
        let sttax = (cap / sep).min(i.proptax + salt + i.otheritem);
        let room = sttax - (i.proptax + i.otheritem);
        xitded = (raw - salt.min(room)).max(0.0);
    }
    let excess = (dc_agi - p.reduction_threshold / sep).max(0.0);
    xitded = (xitded - p.reduction_rate * excess).max(0.0);
    if !gate {
        xitded = 0.0;
    }
    if y == 1999 && !f.forced_itemize {
        xitded = 0.0;
    }
    if gate {
        dc_deduc = xitded;
    }

    // ---- exemption ----
    let num_exemptions = txp + i.depx + aged;
    let mut dc_exemp = num_exemptions * p.exemption;
    if is_hoh {
        dc_exemp += p.exemption;
    }
    if dep_filer {
        dc_exemp = 0.0;
    }
    if let Some((floor, ceiling, band, share)) = p.phaseout {
        let band = band / sep;
        let ratio = if dc_agi > ceiling {
            1.0
        } else if dc_agi > floor {
            (share * (dc_agi - floor) / band).min(1.0)
        } else {
            0.0
        };
        dc_exemp *= 1.0 - ratio;
    }
    let taxinc = (dc_agi - dc_deduc - dc_exemp).max(0.0);

    // ---- tax ----
    let mut statax = p.brackets.tax(taxinc);
    // Joint returns: the lower of the joint tax and the tax on each earner's share.
    if is_joint && dc_agi > 0.0 {
        let agih = higher_earner_share(i, dc_agi);
        let agiw = dc_agi - agih;
        let denom = dc_agi.max(1e-9);
        let exemph = dc_exemp * agih / denom;
        let exempw = dc_exemp - exemph;
        let dedh_base = if dc_stded >= xitded { dc_stded } else { xitded };
        let dedh = dedh_base * agih / denom;
        let dedw = dedh_base - dedh;
        let taxyh = (agih - dedh - exemph).max(0.0);
        let taxyw = (agiw - dedw - exempw).max(0.0);
        statax = statax.min(p.brackets.tax(taxyh) + p.brackets.tax(taxyw));
    }

    // ---- credits ----
    let dc_chcr = match p.chcr_share {
        Some(share) => share * if y <= 1986 { f.federal_chcr } else { f.ccc_uncapped },
        None => 0.0,
    };
    statax = (statax - dc_chcr).max(0.0);

    // Property Tax Credit ("Schedule H").
    let hy = household;
    let agix = f.agi.max(0.0);
    let ptax_base = (p.rent_share * i.rentpaid).max(i.proptax);
    let mut pcred = match &p.property {
        Property::ByIncome(c) => {
            let aged_rate = c.aged_base_rate + hy / c.aged_rate_income / 100.0;
            let aged_pcred = (ptax_base - hy * aged_rate).max(0.0);
            let raw_pcred = (ptax_base - hy * (c.base_pct + hy / c.divisor) / 100.0).max(0.0);
            let young = if hy < c.low_income_limit { c.low_income_share * raw_pcred } else { c.share * raw_pcred };
            let pc = if aged > 0.0 { aged_pcred } else { young };
            if hy <= c.income_limit { c.cap.min(pc) } else { 0.0 }
        }
        Property::Schedule(s) => {
            // From 2022 the rent allowance is added to property tax.
            let ptax = i.proptax + s.rent_share * i.rentpaid;
            let nonelderly_rate = s.nonelderly_rates[s.nonelderly.index(agix)];
            let elderly_rate = s.elderly_rates[s.elderly.index(agix)];
            let elderly = (i.page >= s.elderly_age) || (i.sage >= s.elderly_age);
            let pc = s.max.min((ptax - if elderly { elderly_rate } else { nonelderly_rate } * agix).max(0.0));
            // Above the last income bound there is no credit (not a 0% rate).
            let limit = if elderly { s.elderly_limit } else { s.nonelderly_limit };
            if agix >= limit { 0.0 } else { pc }
        }
        Property::Rates(c) => {
            let under = agix <= c.income_limit;
            let over_25k = if y <= 2018 {
                if under { (ptax_base - c.rate * agix).max(0.0) } else { 0.0 }
            } else if agix < c.middle_income {
                (ptax_base - c.rate * agix).max(0.0)
            } else if under {
                (ptax_base - c.high_rate * agix).max(0.0)
            } else {
                0.0
            };
            let young = if agix < c.low_income { (ptax_base - c.low_rate * agix).max(0.0) } else { over_25k };
            let aged_pcred = if agix <= c.aged_income_limit { (ptax_base - c.low_rate * agix).max(0.0) } else { 0.0 };
            c.max.min(if aged > 0.0 { aged_pcred } else { young })
        }
    };
    if dep_filer && aged < 1.0 {
        pcred = 0.0;
    }

    // Low Income Credit (1987-2017), when federal tax before credits is zero.
    let ycr = match &p.low_income {
        Some(l) => {
            let dx2 = (if is_joint { aged } else { aged.min(2.0) }) as i64;
            let pick = |row: &[f64]| row[dx2.clamp(0, row.len() as i64 - 1) as usize];
            let base = if matches!(fs, Fs::Joint | Fs::Separate) {
                pick(&l.joint) / sep
            } else if fs == Fs::Single {
                pick(&l.single)
            } else {
                pick(&l.hoh)
            };
            let allow = i.depx + aged;
            let no_tax = f.tax_before_credits <= 0.0;
            let mut ycr = if no_tax && !dep_filer { base + l.per_dependent * allow } else { 0.0 };
            let federal_std = if f.itemizes { 0.0 } else { f.standard_deduction };
            let gap = federal_std - dc_stded;
            if no_tax && dep_filer && gap > 0.0 && f.agi <= federal_std {
                ycr += p.brackets.tax(gap.max(0.0));
            }
            ycr
        }
        None => 0.0,
    };
    let stat1 = (statax - ycr).max(0.0);

    // ---- earned income credit (2000+) ----
    let earncr = match p.eitc_share {
        None => 0.0,
        Some(share) => {
            let with_kids = share * f.eitc.max(0.0);
            match &p.childless {
                None => with_kids,
                Some(c) => {
                    let earned = f.earned_income;
                    let agimax = earned.max(f.agi);
                    let raw = if agimax <= c.max_earned {
                        c.max_credit.min(c.phase_in_rate * earned) - c.phaseout_rate * (agimax - c.phaseout_start).max(0.0)
                    } else {
                        0.0
                    };
                    // Investment income: interest and dividends and capital gains (D-40 EITC checklist).
                    let disqy = i.dividends.max(0.0) + i.intrec + (i.stcg + i.ltcg).max(0.0);
                    let mut none = disqy > c.disqualified || sep == 2.0 || dep_filer;
                    if y != 2021 {
                        none = none || aged >= txp;
                    }
                    let older = i.page.max(i.sage);
                    let younger = i.page.min(i.sage);
                    let too_young = if y == 2021 {
                        (older > 0.0 && older < 19.0) || (younger > 0.0 && younger < 19.0)
                    } else {
                        (older > 0.0 && older < c.minimum_age) || (older > c.maximum_age && younger > 0.0 && younger < c.minimum_age)
                    };
                    none = none || too_young;
                    let childless = if none { 0.0 } else { raw };
                    // TAXSIM treats a dependent under 18 as a qualifying child.
                    if i.dep18 > 0.0 { with_kids } else { childless }
                }
            }
        }
    };
    let stat2 = statax - earncr;
    // Taxpayer may not claim both the Low Income Credit and the EITC: whichever leaves a smaller final tax wins.
    stat1.min(stat2) - pcred
}
