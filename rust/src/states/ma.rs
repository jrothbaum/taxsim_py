//! Massachusetts income tax (port of `calculators.states.ma`).

use std::path::Path;

use crate::federal::{Fed, Inp};
use crate::params::{get, load, num, Fs, YearParams};
use crate::states::{law_year_is_exact, unemployment_total};

struct UiExclusion {
    poverty_level: f64,
    cap: f64,
}

enum Dependent {
    Flat(f64),
    PerChild(f64),
}

struct Early {
    nts_single: f64,
    nts_joint: f64,
    lic_rate: f64,
    limit_single: f64,
    limit_joint: f64,
}

struct Late {
    nts_single: f64,
    nts_joint: f64,
    addon: f64,
    limit_single: f64,
    limit_hoh: f64,
    limit_married: f64,
    per_dependent_nts: f64,
    per_dependent_limit: f64,
    lic_rate: f64,
}

enum Credits {
    Personal { per_taxpayer: f64, per_dependent: f64 },
    Early(Early),
    Late(Late),
}

struct CircuitBreaker {
    rent_share: f64,
    income_share: f64,
    maximum: f64,
    single: f64,
    hoh: f64,
    joint: f64,
}

pub struct MaParams {
    year: i64,
    part_b_rate: f64,
    part_b_first: Option<f64>,
    part_a_rate: f64,
    part_c_rate: f64,
    surtax: f64,
    surtax_threshold: Option<f64>,
    additional_tax: Option<(f64, f64)>,
    gains_exclusion: f64,
    personal_exemption: f64,
    married_cap: f64,
    married_base: f64,
    hoh_exemption: f64,
    dependent_exemption: f64,
    aged_exemption: f64,
    ui_exclusion: Option<UiExclusion>,
    interest_exclusion: f64,
    payroll_cap: f64,
    dependent: Dependent,
    dependent_cap: Option<f64>,
    child_care_deduction: Option<f64>,
    child_care_credit: Option<f64>,
    rent_share: Option<f64>,
    rent_cap: f64,
    rent_cap_separate: Option<f64>,
    loss_offset_limit: f64,
    federal_loss_limit: f64,
    schedule_b_limit: f64,
    credits: Credits,
    eitc_rate: f64,
    family_credit: Option<f64>,
    dependent_credit: Option<f64>,
    circuit: Option<CircuitBreaker>,
}

impl MaParams {
    pub fn load(root: &Path, year: i64) -> Result<Self, String> {
        let doc = load(root, "states/ma/income_tax.yaml")?;
        law_year_is_exact(root, &doc, year)?;
        let p = YearParams::new(&doc, year);
        let opt = |key: &str| -> Result<Option<f64>, String> { Ok(if p.in_effect(key)? { Some(p.f(key)?) } else { None }) };

        let dependent = {
            let d = p.value("dependent_deduction", &[])?;
            match d.get("flat") {
                Some(v) => Dependent::Flat(num(v)?),
                None => Dependent::PerChild(num(get(d, "per_child")?)?),
            }
        };
        let credits = if year >= 1995 {
            Credits::Late(Late {
                nts_single: p.f("no_tax_status_single")?,
                nts_joint: p.f("no_tax_status_joint")?,
                addon: p.f("no_tax_status_married_addon")?,
                limit_single: p.f("limited_income_credit_limit_single")?,
                limit_hoh: p.f("limited_income_credit_limit_hoh")?,
                limit_married: p.f("limited_income_credit_limit_married")?,
                per_dependent_nts: p.f("no_tax_status_per_dependent")?,
                per_dependent_limit: p.f("limited_income_credit_limit_per_dependent")?,
                lic_rate: p.f("limited_income_credit_rate")?,
            })
        } else if year >= 1984 {
            Credits::Early(Early {
                nts_single: p.f("no_tax_status_single")?,
                nts_joint: p.f("no_tax_status_joint")?,
                lic_rate: p.f("limited_income_credit_rate")?,
                limit_single: p.f("limited_income_credit_limit_single")?,
                limit_joint: p.f("limited_income_credit_limit_joint")?,
            })
        } else {
            let c = p.value("personal_credit", &[])?;
            Credits::Personal { per_taxpayer: num(get(c, "per_taxpayer")?)?, per_dependent: num(get(c, "per_dependent")?)? }
        };
        let circuit = if year >= 2001 {
            let limits = YearParams::new(get(&doc, "circuit_breaker_income_limit")?, year);
            Some(CircuitBreaker {
                rent_share: num(get(&doc, "circuit_breaker_rent_share")?)?,
                income_share: num(get(&doc, "circuit_breaker_income_share")?)?,
                maximum: p.f("circuit_breaker_max")?,
                single: limits.f("single")?,
                hoh: limits.f("head_of_household")?,
                joint: limits.f("married_joint")?,
            })
        } else {
            None
        };
        let additional_tax = match p.opt("additional_tax")? {
            Some(a) => Some((num(get(a, "rate")?)?, num(get(a, "threshold")?)?)),
            None => None,
        };
        Ok(Self {
            year,
            part_b_rate: p.f("part_b_rate")?,
            part_b_first: opt("part_b_rate_first_part")?,
            part_a_rate: p.f("part_a_rate")?,
            part_c_rate: if year >= 1997 { p.f("part_c_rate")? } else { 0.0 },
            surtax: p.f("surtax")?,
            surtax_threshold: opt("surtax_income_threshold")?,
            additional_tax,
            gains_exclusion: if year <= 1996 { p.f("capital_gains_exclusion")? } else { 0.0 },
            personal_exemption: p.f("personal_exemption")?,
            married_cap: p.f("married_exemption_cap")?,
            married_base: if year <= 1986 { p.f("married_additional_exemption_base")? } else { 0.0 },
            hoh_exemption: if year >= 1994 { p.f("head_of_household_exemption")? } else { 0.0 },
            dependent_exemption: p.f("dependent_exemption")?,
            aged_exemption: p.f("aged_exemption")?,
            ui_exclusion: if p.in_effect("ui_exclusion_cap_per_person")? {
                Some(UiExclusion { poverty_level: p.f("ui_exclusion_poverty_level")?, cap: p.f("ui_exclusion_cap_per_person")? })
            } else {
                None
            },
            interest_exclusion: num(get(&doc, "interest_exclusion_per_taxpayer")?)?,
            payroll_cap: num(get(&doc, "payroll_tax_deduction_cap")?)?,
            dependent,
            dependent_cap: opt("dependent_deduction_cap_per_child")?,
            child_care_deduction: opt("child_care_deduction_per_child")?,
            child_care_credit: if p.in_effect("child_care_deduction_per_child")? { None } else { opt("child_care_credit_per_child")? },
            rent_share: opt("rent_deduction_share")?,
            rent_cap: if p.in_effect("rent_deduction_share")? && year >= 1982 { p.f("rent_deduction_cap")? } else { 0.0 },
            rent_cap_separate: if year >= 1982 && year < 2001 { opt("rent_deduction_cap_separate")? } else { None },
            loss_offset_limit: if year <= 1996 { num(get(&doc, "capital_loss_offset_limit")?)? } else { 0.0 },
            federal_loss_limit: num(get(&doc, "federal_capital_loss_limit")?)?,
            schedule_b_limit: num(get(&doc, "schedule_b_offset_limit")?)?,
            credits,
            eitc_rate: if year >= 1997 { p.f("eitc_rate")? } else { 0.0 },
            family_credit: opt("child_and_family_credit_per_child")?,
            dependent_credit: if p.in_effect("child_and_family_credit_per_child")? { None } else { opt("dependent_credit_per_dependent")? },
            circuit,
        })
    }
}

pub fn tax(i: &Inp, f: &Fed, p: &MaParams) -> f64 {
    let y = p.year;
    let fs = i.fs();
    let is_joint = fs == Fs::Joint;
    let is_hoh = fs == Fs::Hoh;
    let is_sep = fs == Fs::Separate;
    let is_single = fs == Fs::Single;
    let not_sep = !is_sep;
    let n_tp = i.taxpayer_count();
    let aged = i.aged_count();
    let depx = i.depx;
    let untax = f.taxable_unemployment;
    let setax = f.payroll.setax;
    // Only the employee half of wage payroll tax counts, for each spouse up to the cap.
    let c183 = f.payroll.own_fica_primary - 0.5 * f.payroll.own_wage_fica_primary;
    let c183_spouse = f.payroll.own_fica_secondary - 0.5 * f.payroll.own_wage_fica_secondary;
    let schede = i.otherprop + if y >= 1987 { i.scorp } else { 0.0 };
    let x_flag = if y <= 1984 { if is_joint { 1.0 } else { 0.0 } } else if is_sep { 0.0 } else { 1.0 };

    // Negative wages count as self-employment income.
    let w1 = i.pwages.max(0.0);
    let w2 = i.swages.max(0.0);
    let wages = w1 + w2;
    let semp = i.psemp + i.ssemp + i.pwages.min(0.0) + i.swages.min(0.0);
    let business_h = i.pbusinc + i.pprofinc;
    let business_w = i.sbusinc + i.sprofinc;
    let d17 = semp + business_h + business_w;
    let ui_total = unemployment_total(i);

    // ---- exemptions ----
    let xmps = p.personal_exemption;
    let txp_joint = if y <= 1986 { (xmps + w1.min(w2) + p.married_base).min(p.married_cap) } else { (2.0 * xmps).min(p.married_cap) };
    let mut txp = if is_joint { txp_joint } else { xmps };
    if y >= 1994 && is_hoh {
        txp = p.hoh_exemption;
    }
    let ageex = aged * p.aged_exemption;
    let depex = depx * p.dependent_exemption;
    let exemp = depex + ageex + txp;

    // ---- Part B income ----
    let b1inc_base = wages + d17;
    let mut b2inc = i.pensions;
    if (1982..=1989).contains(&y) {
        b2inc += untax;
    }
    let (b1inc, binc);
    if y <= 1989 {
        b1inc = b1inc_base + schede;
        binc = b1inc + b2inc;
    } else {
        b1inc = b1inc_base;
        let mut b = b1inc + b2inc + schede + untax;
        if y == 2009 {
            b = b - untax + ui_total;
        }
        if let Some(u) = &p.ui_exclusion {
            let nsize = (if is_joint { 2.0 } else { 1.0 }) + depx.floor();
            let ulevel = 2.0 * u.poverty_level * nsize;
            let hyun = f.agi + i.gssi - f.taxable_social_security + ui_total - untax;
            let unded = if hyun <= ulevel { (ui_total - i.sui).min(u.cap) + i.sui.min(u.cap) } else { 0.0 };
            if ui_total > 0.0 {
                b = b + ui_total - untax - unded;
            }
        }
        if y >= 1999 {
            b += (i.intrec - p.interest_exclusion * n_tp).max(0.0);
        }
        binc = b;
    }

    // ---- Part B deductions ----
    let fica = c183.min(p.payroll_cap) + c183_spouse.min(p.payroll_cap) + i.gssi.max(0.0).min(p.payroll_cap);
    let ndep13 = i.dep13.min(2.0).floor();
    let d209 = depx - i.dep18;
    let ndep = (d209 + ndep13).min(2.0).floor();
    let mut ch = match p.dependent {
        Dependent::Flat(amount) => amount,
        Dependent::PerChild(amount) => amount * ndep13,
    };
    if let Some(cap) = p.dependent_cap {
        ch = ch.min(cap * i.dep13);
    }
    if is_sep {
        ch = 0.0;
    }
    let mut ch1 = 0.0;
    let mut chcred = 0.0;
    if let Some(per_child) = p.child_care_deduction {
        ch1 = i.childcare.min(per_child * ndep13);
    } else if let Some(per_child) = p.child_care_credit {
        chcred = i.childcare.min(per_child * ndep13);
    }
    let earned = f.earned_income;
    // A joint return splits earnings by spouse, with self-employment tax shared by business income.
    let (earnh, earnw) = if d17 > 0.0 {
        let setaxh = setax * business_h / d17;
        let setaxw = setax * business_w / d17;
        let earnww_se = (semp - 0.5 * (setax - setaxh - setaxw)).max(0.0);
        (
            (w1 + business_h - 0.5 * setaxh).max(0.0) + earnww_se / 2.0,
            (w2 + business_w - 0.5 * setaxw).max(0.0) + earnww_se / 2.0,
        )
    } else {
        let rest = (earned - w1 - w2).max(0.0) / 2.0;
        (w1 + rest, w2 + rest)
    };
    ch1 = (if is_joint { ch1.min(earnh).min(earnw) } else { ch1.min(earned) }).max(0.0);
    chcred = (if is_joint { chcred.min(earnh).min(earnw) } else { chcred.min(earned) }).max(0.0);
    ch = ch.max(ch1);
    let has_dep = ndep > 0.0;
    if !has_dep {
        ch = 0.0;
        chcred = 0.0;
    }
    let mut rnt = 0.0;
    if let Some(share) = p.rent_share {
        rnt = share * i.rentpaid;
        if y >= 2001 {
            rnt = rnt.min(p.rent_cap / if is_sep { 2.0 } else { 1.0 });
        } else if y >= 1982 {
            rnt = rnt.min(p.rent_cap);
            if let Some(cap_separate) = p.rent_cap_separate {
                if is_sep {
                    rnt = rnt.min(cap_separate);
                }
            }
        }
    }
    // Non-property income enters as a negative deduction.
    let bded = fica + ch + rnt - i.nonprop;
    let tbinc1 = (binc - bded).max(0.0);
    let tbinc2 = (tbinc1 - exemp).max(0.0);

    // ---- Part A income ----
    let (stcg, ltcg, divs, intrec) = (i.stcg, i.ltcg, i.dividends, i.intrec);
    let mut ainc = if y <= 1996 {
        let gnx = p.gains_exclusion;
        let mut cg = if y <= 1982 { ltcg - (ltcg * gnx).max(0.0) + stcg } else { (ltcg + stcg) - ((ltcg + stcg) * gnx).max(0.0) };
        let cfd = if cg < 0.0 { p.loss_offset_limit.min(-cg) } else { 0.0 };
        cg = cg.max(0.0);
        cg + (intrec + divs - cfd).max(0.0)
    } else if y <= 1998 {
        divs + intrec
    } else {
        stcg.max(0.0)
    };

    let mut binc_for_agi = binc;
    let (mut b1_1989, mut b2_1989) = (0.0, 0.0);
    if y <= 1988 {
        let binc_mod = (binc - bded).max(0.0);
        let xtra = (exemp - binc_mod).max(0.0);
        binc_for_agi = (binc_mod - exemp).max(0.0);
        ainc = (ainc - x_flag * xtra).max(0.0);
    } else if y == 1989 {
        let loss1 = b1inc.min(0.0);
        let loss2 = b2inc.min(0.0);
        let mut b1 = (b1inc + loss2).max(0.0);
        let xded = bded.min(bded - b1).max(0.0);
        b1 = (b1 - bded).max(0.0);
        let xex = (exemp - b1).max(0.0);
        b1 = (b1 - exemp).max(0.0);
        let mut b2 = (b2inc + loss1).max(0.0);
        b2 = (b2 - xded).max(0.0);
        let xex2 = (xex - b2).max(0.0);
        b2 = (b2 - xex).max(0.0);
        ainc = (ainc - x_flag * xex2).max(0.0);
        b1_1989 = b1;
        b2_1989 = b2;
    } else if y <= 1998 {
        let bagi = binc - bded;
        let exded = if bagi < exemp && not_sep { exemp - bagi } else { 0.0 };
        ainc = (ainc - exded).max(0.0);
    }

    let mut binc1 = 0.0;
    let mut tbinc3 = 0.0;
    let mut cinc = 0.0;
    if y >= 1999 {
        let divrec = divs;
        let mut capgn = stcg + ltcg;
        if capgn < 0.0 {
            capgn = capgn.max(if is_sep { -p.federal_loss_limit / 2.0 } else { -p.federal_loss_limit });
        }
        let sbl = p.schedule_b_limit;
        let xlin4 = (divrec.min(sbl) - (-stcg.min(0.0))).max(0.0);
        binc1 = if capgn >= 0.0 { divrec } else { divrec - xlin4.min(capgn.abs()) };
        let excxmp = if binc - bded < exemp && not_sep { exemp - (binc - bded).max(0.0) } else { 0.0 };

        // MA Schedule B (Part 1-4) and Schedule D worksheet, as transcribed.
        let xl20 = if stcg < 0.0 { sbl.min(divrec).min(-stcg) } else { 0.0 };
        let xl21 = stcg + xl20;
        let xl22 = if stcg < 0.0 && xl21 < 0.0 && ltcg > 0.0 { (-xl21).min(ltcg) } else { 0.0 };
        let xl24 = stcg.max(0.0);
        let xl25 = if stcg > 0.0 && ltcg < 0.0 { stcg.min(-ltcg) } else { 0.0 };
        let xl28 = xl24 - xl25;
        let xl31 = divrec - xl20;
        let dxl15 = if ltcg >= 0.0 { ltcg - xl22 } else { ltcg + xl22 };
        let xl32 = if xl31 > 0.0 && dxl15 < 0.0 { (sbl.min(divrec) - xl20).max(0.0).min((ltcg - xl22).min(0.0).abs()) } else { 0.0 };
        let xl35 = xl31 - xl32 + xl28;
        let xl37 = (xl35 - excxmp).max(0.0);
        let xl38 = if xl37 >= divs { divrec } else { xl37 };
        tbinc3 = xl38;
        ainc = xl37 - xl38;

        let dxl16 = if dxl15 < 0.0 { (divs.min(sbl) - (-stcg.min(0.0))).max(0.0).min(-ltcg.min(0.0)) } else { 0.0 };
        let dxl19 = (dxl16 + dxl15).max(0.0);
        let wdxl1 = xl35.max(0.0);
        let wdxl4 = exemp - tbinc1;
        let wdxl6 = wdxl4 - wdxl1.min(wdxl4);
        let dxl20 = if wdxl4 <= 0.0 || wdxl6 <= 0.0 { 0.0 } else { dxl19.min(wdxl6) };
        cinc = (dxl19 - dxl20).max(0.0);
    } else if y >= 1997 {
        cinc = f.ltg;
    }

    let taxbin = tbinc2 + tbinc3;
    let rate_b = p.part_b_rate;
    let mut statxb = match p.part_b_first {
        Some(first) => first * b2_1989 + rate_b * b1_1989.max(0.0),
        None => rate_b * taxbin,
    };
    let mut statxa = p.part_a_rate * ainc;
    let statxc = if y >= 1997 { p.part_c_rate * cinc.max(0.0) } else { 0.0 };
    let surtax = p.surtax;
    let pretax = if let Some(threshold) = p.surtax_threshold {
        if binc_for_agi > threshold {
            statxb *= surtax;
        }
        if ainc > threshold {
            statxa *= surtax;
        }
        statxa + statxb
    } else {
        let mut pre = (statxa + statxb + statxc) * surtax;
        if let Some((rate, threshold)) = p.additional_tax {
            pre += rate * (taxbin + ainc + cinc.max(0.0) - threshold).max(0.0);
        }
        pre
    };

    // ---- Massachusetts AGI and No Tax Status / Limited Income Credit ----
    let ma_agi = (binc_for_agi + binc1 + stcg + ltcg + intrec.min(p.interest_exclusion * n_tp)).max(0.0);
    let (mut ntscr, mut txcr, mut scred) = (0.0, 0.0, 0.0);
    match &p.credits {
        Credits::Early(c) => {
            let s1 = is_single || is_hoh;
            ntscr = if s1 && ma_agi <= c.nts_single {
                pretax
            } else if is_joint && ma_agi <= c.nts_joint {
                pretax
            } else {
                0.0
            };
            txcr = if s1 && ma_agi > c.nts_single && ma_agi <= c.limit_single && pretax >= c.lic_rate * (ma_agi - c.nts_single) {
                (pretax - c.lic_rate * (ma_agi - c.nts_single)).max(0.0)
            } else if is_joint && ma_agi > c.nts_joint && ma_agi <= c.limit_joint {
                (pretax - c.lic_rate * (ma_agi - c.nts_joint)).max(0.0)
            } else {
                0.0
            };
        }
        Credits::Late(c) => {
            let ntsr = if is_joint { c.nts_joint + c.addon } else { c.nts_joint };
            let licrr = if is_joint { c.limit_married } else { c.limit_hoh };
            let numdep = depx.floor();
            let s2 = is_joint || is_hoh;
            let lo = ntsr + numdep * c.per_dependent_nts;
            let hi = licrr + numdep * c.per_dependent_limit;
            ntscr = if is_single && ma_agi <= c.nts_single {
                pretax
            } else if s2 && ma_agi <= lo {
                pretax
            } else {
                0.0
            };
            txcr = if is_single && ma_agi > c.nts_single && ma_agi <= c.limit_single {
                (pretax - c.lic_rate * (ma_agi - c.nts_single)).max(0.0)
            } else if s2 && ma_agi > lo && ma_agi <= hi {
                (pretax - c.lic_rate * (ma_agi - lo)).max(0.0)
            } else {
                0.0
            };
        }
        Credits::Personal { per_taxpayer, per_dependent } => {
            scred = per_taxpayer * n_tp + per_dependent * depx;
        }
    }

    let mut statax = (pretax - (ntscr + txcr + scred)).max(0.0);
    if y >= 1997 {
        statax -= p.eitc_rate * f.eitc;
    }
    if let Some(per_child) = p.family_credit {
        statax -= per_child * i.dep13;
    } else if let Some(per_dependent) = p.dependent_credit {
        statax -= chcred.max(ndep * per_dependent);
    }

    // Senior circuit breaker credit (2001+, refundable).
    if let Some(c) = &p.circuit {
        let hymax = if is_joint { c.joint } else if is_hoh { c.hoh } else if is_single { c.single } else { 0.0 };
        let ptax = i.proptax.max(c.rent_share * i.rentpaid);
        let hycb = (ma_agi + i.gssi + i.transfers - ageex - depex).max(0.0);
        let mut cbcred = if hycb <= hymax { (ptax - c.income_share * hycb).max(0.0) } else { 0.0 };
        cbcred = cbcred.min(c.maximum);
        if !(not_sep && aged > 0.0) {
            cbcred = 0.0;
        }
        statax -= cbcred;
    }
    statax
}
