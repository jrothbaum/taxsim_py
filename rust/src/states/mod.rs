//! State income tax calculators, one module per state.
//!
//! Each state loads its own parameters for the law year (returning an error for
//! any provision it does not port, so Python keeps those years) and computes
//! `siitax` from the household and the federal results.

use std::path::Path;

use serde_yaml::Value;

use crate::federal::{Fed, Inp};
use crate::params::{load, num};

mod ak;
mod al;
mod ar;
mod az;
mod ca;
mod co;
mod ct;
mod dc;
mod de;
mod ga;
mod hi;
mod ia;
mod id;
mod il;
mod in_;
mod ks;
mod ky;
mod la;
mod mn;
mod mo;
mod mt;
mod nc;
mod nd;
mod nj;
mod ne;
mod nm;
mod ok;
mod ma;
mod md;
mod me;
mod mi;
mod ms;
mod ny;
mod oh;
mod nh;
mod or_;
mod pa;
mod ri;
mod sc;
mod ut;
mod va;
mod vt;
mod tn;
mod wa;
mod wi;
mod wv;

/// A state's calculator for one law year.
pub enum StateCalc {
    /// No state income tax (or no state).
    Zero,
    Ak(ak::AkParams),
    Al(al::AlParams),
    Ar(ar::ArParams),
    Az(az::AzParams),
    Ca(ca::CaParams),
    Co(co::CoParams),
    Ct(ct::CtParams),
    Dc(dc::DcParams),
    De(de::DeParams),
    Ga(ga::GaParams),
    Hi(hi::HiParams),
    Ia(ia::IaParams),
    Id(id::IdParams),
    Il(il::IlParams),
    In(in_::InParams),
    Ks(ks::KsParams),
    Ky(ky::KyParams),
    La(la::LaParams),
    Nc(nc::NcParams),
    Nd(nd::NdParams),
    Ne(ne::NeParams),
    Nj(nj::NjParams),
    Nm(nm::NmParams),
    Ok(ok::OkParams),
    Ma(ma::MaParams),
    Md(md::MdParams),
    Me(me::MeParams),
    Mi(mi::MiParams),
    Mn(mn::MnParams),
    Mo(mo::MoParams),
    Ms(ms::MsParams),
    Mt(mt::MtParams),
    Ny(ny::NyParams),
    Oh(oh::OhParams),
    Nh(nh::NhParams),
    Or(or_::OrParams),
    Pa(pa::PaParams),
    Ri(ri::RiParams),
    Sc(sc::ScParams),
    Ut(ut::UtParams),
    Va(va::VaParams),
    Vt(vt::VtParams),
    Tn(tn::TnParams),
    Wa(wa::WaParams),
    Wi(wi::WiParams),
    Wv(wv::WvParams),
}

impl StateCalc {
    pub fn load(root: &Path, year: i64, code: i64) -> Result<Self, String> {
        match code {
            // No state, Florida, Nevada, South Dakota, Texas and Wyoming.
            0 | 10 | 29 | 42 | 44 | 51 => Ok(Self::Zero),
            // Alaska taxed income only through 1979.
            2 if year > 1979 => Ok(Self::Zero),
            2 => Ok(Self::Ak(ak::AkParams::load(root, year)?)),
            4 => Ok(Self::Ar(ar::ArParams::load(root, year)?)),
            3 => Ok(Self::Az(az::AzParams::load(root, year)?)),
            1 => Ok(Self::Al(al::AlParams::load(root, year)?)),
            5 => Ok(Self::Ca(ca::CaParams::load(root, year)?)),
            6 => Ok(Self::Co(co::CoParams::load(root, year)?)),
            7 => Ok(Self::Ct(ct::CtParams::load(root, year)?)),
            9 => Ok(Self::Dc(dc::DcParams::load(root, year)?)),
            8 => Ok(Self::De(de::DeParams::load(root, year)?)),
            11 => Ok(Self::Ga(ga::GaParams::load(root, year)?)),
            37 => Ok(Self::Ok(ok::OkParams::load(root, year)?)),
            40 => Ok(Self::Ri(ri::RiParams::load(root, year)?)),
            41 => Ok(Self::Sc(sc::ScParams::load(root, year)?)),
            31 => Ok(Self::Nj(nj::NjParams::load(root, year)?)),
            32 => Ok(Self::Nm(nm::NmParams::load(root, year)?)),
            15 => Ok(Self::In(in_::InParams::load(root, year)?)),
            17 => Ok(Self::Ks(ks::KsParams::load(root, year)?)),
            18 => Ok(Self::Ky(ky::KyParams::load(root, year)?)),
            28 => Ok(Self::Ne(ne::NeParams::load(root, year)?)),
            34 => Ok(Self::Nc(nc::NcParams::load(root, year)?)),
            35 => Ok(Self::Nd(nd::NdParams::load(root, year)?)),
            12 => Ok(Self::Hi(hi::HiParams::load(root, year)?)),
            27 => Ok(Self::Mt(mt::MtParams::load(root, year)?)),
            24 => Ok(Self::Mn(mn::MnParams::load(root, year)?)),
            26 => Ok(Self::Mo(mo::MoParams::load(root, year)?)),
            16 => Ok(Self::Ia(ia::IaParams::load(root, year)?)),
            13 => Ok(Self::Id(id::IdParams::load(root, year)?)),
            19 => Ok(Self::La(la::LaParams::load(root, year)?)),
            14 => Ok(Self::Il(il::IlParams::load(root, year)?)),
            22 => Ok(Self::Ma(ma::MaParams::load(root, year)?)),
            20 => Ok(Self::Me(me::MeParams::load(root, year)?)),
            21 => Ok(Self::Md(md::MdParams::load(root, year)?)),
            23 => Ok(Self::Mi(mi::MiParams::load(root, year)?)),
            33 => Ok(Self::Ny(ny::NyParams::load(root, year)?)),
            36 => Ok(Self::Oh(oh::OhParams::load(root, year)?)),
            25 => Ok(Self::Ms(ms::MsParams::load(root, year)?)),
            47 => Ok(Self::Va(va::VaParams::load(root, year)?)),
            46 => Ok(Self::Vt(vt::VtParams::load(root, year)?)),
            45 => Ok(Self::Ut(ut::UtParams::load(root, year)?)),
            38 => Ok(Self::Or(or_::OrParams::load(root, year)?)),
            39 => Ok(Self::Pa(pa::PaParams::load(root, year)?)),
            30 => Ok(Self::Nh(nh::NhParams::load(root, year)?)),
            43 => Ok(Self::Tn(tn::TnParams::load(root, year)?)),
            48 => Ok(Self::Wa(wa::WaParams::load(root, year)?)),
            50 => Ok(Self::Wi(wi::WiParams::load(root, year)?)),
            49 => Ok(Self::Wv(wv::WvParams::load(root, year)?)),
            _ => Err(format!("state {code} is not supported by the compiled calculator")),
        }
    }

    pub fn tax(&self, i: &Inp, f: &Fed) -> f64 {
        match self {
            Self::Zero => 0.0,
            Self::Ak(p) => ak::tax(i, f, p),
            Self::Al(p) => al::tax(i, f, p),
            Self::Ar(p) => ar::tax(i, f, p),
            Self::Az(p) => az::tax(i, f, p),
            Self::Ca(p) => ca::tax(i, f, p),
            Self::Co(p) => co::tax(i, f, p),
            Self::Ct(p) => ct::tax(i, f, p),
            Self::Dc(p) => dc::tax(i, f, p),
            Self::De(p) => de::tax(i, f, p),
            Self::Ga(p) => ga::tax(i, f, p),
            Self::Hi(p) => hi::tax(i, f, p),
            Self::Ia(p) => ia::tax(i, f, p),
            Self::Id(p) => id::tax(i, f, p),
            Self::Il(p) => il::tax(i, f, p),
            Self::In(p) => in_::tax(i, f, p),
            Self::Ks(p) => ks::tax(i, f, p),
            Self::Ky(p) => ky::tax(i, f, p),
            Self::La(p) => la::tax(i, f, p),
            Self::Nc(p) => nc::tax(i, f, p),
            Self::Nd(p) => nd::tax(i, f, p),
            Self::Ne(p) => ne::tax(i, f, p),
            Self::Nj(p) => nj::tax(i, f, p),
            Self::Nm(p) => nm::tax(i, f, p),
            Self::Ok(p) => ok::tax(i, f, p),
            Self::Nh(p) => nh::tax(i, p),
            Self::Or(p) => or_::tax(i, f, p),
            Self::Pa(p) => pa::tax(i, f, p),
            Self::Ma(p) => ma::tax(i, f, p),
            Self::Md(p) => md::tax(i, f, p),
            Self::Me(p) => me::tax(i, f, p),
            Self::Mi(p) => mi::tax(i, f, p),
            Self::Mn(p) => mn::tax(i, f, p),
            Self::Mo(p) => mo::tax(i, f, p),
            Self::Ms(p) => ms::tax(i, f, p),
            Self::Mt(p) => mt::tax(i, f, p),
            Self::Ny(p) => ny::tax(i, f, p),
            Self::Oh(p) => oh::tax(i, f, p),
            Self::Ri(p) => ri::tax(i, f, p),
            Self::Sc(p) => sc::tax(i, f, p),
            Self::Ut(p) => ut::tax(i, f, p),
            Self::Va(p) => va::tax(i, f, p),
            Self::Vt(p) => vt::tax(i, f, p),
            Self::Tn(p) => tn::tax(i, p),
            Self::Wa(p) => wa::tax(f, p),
            Self::Wi(p) => wi::tax(i, f, p),
            Self::Wv(p) => wv::tax(i, f, p),
        }
    }
}

/// Python extrapolates a state's law (deflating inputs) past the last year it
/// codes, or past TAXSIM's last state year when the state names none. The
/// compiled calculators do not, so those years stay in Python.
pub fn law_year_is_exact(root: &Path, state_doc: &Value, year: i64) -> Result<(), String> {
    let through = match state_doc.get("law_coded_through") {
        Some(v) => num(v)? as i64,
        None => {
            let cpi = load(root, "national/state_cpi_extrapolation.yaml")?;
            num(crate::params::get(&cpi, "lastat")?)? as i64
        }
    };
    if year > through {
        return Err(format!("the state law is extrapolated past {through}; not supported by the compiled calculator"));
    }
    Ok(())
}

/// TAXSIM's fixed adjustment added to the dividend input.
pub fn dividend_adjustment(root: &Path) -> Result<f64, String> {
    let doc = load(root, "national/state_adjustments.yaml")?;
    num(crate::params::get(&doc, "household_income_dividend_adjustment")?)
}

/// TAXSIM's household-income total (`data(159)`) in statutory mode.
pub fn household_income(i: &Inp, dividend_adjustment: f64) -> f64 {
    let self_employment = i.psemp + i.ssemp;
    self_employment
        + (i.pwages.max(0.0)
            + i.swages.max(0.0)
            + i.dividends
            + dividend_adjustment
            + i.pensions
            + i.ui.max(i.pui + i.sui)
            + i.gssi
            + i.transfers
            + i.intrec
            + i.pbusinc
            + i.pprofinc
            + i.sbusinc
            + i.sprofinc
            + i.scorp
            + i.otherprop
            + (i.stcg + i.ltcg).max(0.0)
            + 0.0)
}

/// The federal dividend exclusion that states add back to federal AGI (`divexc`).
///
/// Before 1987 this is the dividends (plus interest in 1981) excluded from
/// federal AGI; from 1987 there is no exclusion.
pub fn dividend_exclusion_addback(year: i64, i: &Inp, f: &Fed, dividend_adjustment: f64) -> f64 {
    if year >= 1987 {
        return 0.0;
    }
    let mut excluded = i.dividends + dividend_adjustment;
    if year == 1981 {
        excluded += i.intrec;
    }
    excluded.min(f.pre87.divexc).max(0.0)
}

/// Unemployment compensation, preferring the spouse split when larger.
pub fn unemployment_total(i: &Inp) -> f64 {
    i.ui.max(i.pui + i.sui)
}

/// The federal exemption count (TAXSIM `comnew(68)`): taxpayers and dependents,
/// plus taxpayers 65 or older before 1987; none for a dependent filer from 1987.
pub fn federal_exemption_count(year: i64, i: &Inp) -> f64 {
    if year <= 1986 {
        i.taxpayer_count() + i.depx + i.aged_count()
    } else if i.is_dependent_filer() {
        0.0
    } else {
        i.taxpayer_count() + i.depx
    }
}

/// Gross federal itemized deductions through 1986, including the state tax deduction.
pub fn pre1987_gross_itemized(i: &Inp, f: &Fed) -> f64 {
    f.ded + i.proptax + i.otheritem + i.mortgage + i.charity_cash
}

/// TAXSIM's `tablki` interpolation of `[threshold, value]` rows.
///
/// Below the first threshold the first value applies and at or above the last
/// threshold the last value applies; in between, TAXSIM's weighting between the
/// neighbouring rows is used.
pub struct Interp {
    thresholds: Vec<f64>,
    values: Vec<f64>,
}

impl Interp {
    pub fn new(rows: &[Vec<f64>]) -> Self {
        let last = rows.len() - 1;
        Self { thresholds: rows[..last].iter().map(|r| r[0]).collect(), values: rows.iter().map(|r| r[1]).collect() }
    }

    pub fn eval(&self, income: f64) -> f64 {
        let last = self.thresholds.len();
        let count = self.thresholds.partition_point(|t| *t <= income);
        if count == 0 {
            return self.values[0];
        }
        if count >= last {
            return self.values[last];
        }
        let lower_threshold = self.thresholds[(count - 1).min(last - 1)];
        let upper_threshold = self.thresholds[count.min(last - 1)];
        let lower_value = self.values[(count - 1).min(last)];
        let upper_value = self.values[count.min(last)];
        let weight = (income - lower_threshold) / (upper_threshold - lower_threshold);
        if upper_value > lower_value {
            weight * lower_value + (1.0 - weight) * upper_value
        } else {
            weight * upper_value + (1.0 - weight) * lower_value
        }
    }
}

/// Values of the first tier whose upper bound is at least `value` (the last tier beyond);
/// with `strict`, the first tier whose upper bound exceeds it (TAXSIM `tablk`).
pub struct Tiers {
    uppers: Vec<f64>,
    strict: bool,
}

impl Tiers {
    pub fn new(uppers: Vec<f64>, strict: bool) -> Self {
        Self { uppers, strict }
    }

    pub fn index(&self, value: f64) -> usize {
        let position = if self.strict {
            self.uppers.partition_point(|u| *u <= value)
        } else {
            self.uppers.partition_point(|u| *u < value)
        };
        position.min(self.uppers.len() - 1)
    }
}

/// Payroll tax as TAXSIM's `socsec` reports it to states (port of `engine.state.taxsim_socsec`):
/// employee FICA on each spouse's wages (joint returns) or on total wages, plus
/// self-employment tax and, from 2013, the additional Medicare tax.
pub struct Socsec {
    rate: f64,
    ceiling: f64,
    above: f64,
    medicare_ceiling: f64,
}

impl Socsec {
    pub fn load(root: &Path, year: i64) -> Result<Self, String> {
        let socsec = load(root, "national/state_socsec.yaml")?;
        let payroll = load(root, "national/payroll_tax.yaml")?;
        Ok(Self {
            rate: num(crate::params::resolve_year(crate::params::get(&socsec, "rate")?, year)?)?,
            ceiling: num(crate::params::resolve_year(crate::params::get(&socsec, "wage_ceiling")?, year)?)?,
            above: num(crate::params::get(&socsec, "rate_above_ceiling")?)?,
            medicare_ceiling: num(crate::params::resolve_year(crate::params::get(&payroll, "hi_wage_base")?, year)?)?,
        })
    }

    fn fica(&self, wages: f64) -> f64 {
        let medicare_wages = wages.min(self.medicare_ceiling);
        wages.min(self.ceiling) * self.rate + (medicare_wages - self.ceiling).max(0.0) * self.above
    }

    pub fn total(&self, year: i64, i: &Inp, setax: f64, addmed: f64) -> f64 {
        let (pw, sw) = (i.pwages.max(0.0), i.swages.max(0.0));
        let mut total = if i.fs() == crate::params::Fs::Joint { self.fica(pw) + self.fica(sw) } else { self.fica(pw + sw) };
        total += setax;
        if year >= 2013 {
            total += addmed;
        }
        total
    }
}

/// The higher earner's share of joint income: their wages plus half the rest.
pub fn higher_earner_share(i: &Inp, income: f64) -> f64 {
    i.pwages.max(i.swages) + (income - (i.pwages + i.swages)) / 2.0
}
