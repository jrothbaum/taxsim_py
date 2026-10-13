//! Payroll and self-employment tax as TAXSIM's `sstax` computes them
//! (port of `engine.payroll_tax.taxsim_payroll`).

use serde_yaml::Value;

use crate::params::{status_at, Fs, YearParams};

pub struct PayrollParams {
    wage_base: f64,
    hi_wage_base: f64,
    oasdi_rate: f64,
    se_oasdi_rate: f64,
    hi_rate: f64,
    nef: f64,
    addmed_rate: f64,
    addmed_threshold: [f64; 4],
    own_share: f64,
}

impl PayrollParams {
    pub fn load(doc: &Value, year: i64) -> Result<Self, String> {
        let p = YearParams::new(doc, year);
        Ok(Self {
            wage_base: p.f("oasdi_wage_base")?,
            hi_wage_base: p.f("hi_wage_base")?,
            oasdi_rate: p.f("oasdi_rate_combined")?,
            se_oasdi_rate: p.f("se_oasdi_rate")?,
            hi_rate: p.f("se_hi_rate")?,
            nef: p.f("se_net_earnings_factor")?,
            addmed_rate: p.f("additional_medicare_rate")?,
            addmed_threshold: status_at(crate::params::get(doc, "additional_medicare_threshold")?, year)?,
            own_share: crate::params::num(crate::params::get(doc, "own_share_self_employment")?)?,
        })
    }
}

#[derive(Default, Clone, Copy)]
pub struct Payroll {
    pub setax: f64,
    pub setax_qbi: f64,
    pub setax_sstb: f64,
    pub addmed: f64,
    pub fica: f64,
    pub tfica: f64,
    pub ficar: f64,
    /// Each earner's own payroll tax (self-employment tax at `own_share`) and wage payroll tax.
    pub own_fica_primary: f64,
    pub own_wage_fica_primary: f64,
    pub own_fica_secondary: f64,
    pub own_wage_fica_secondary: f64,
}

/// Per-item tax with the cap applied to running factor-weighted earnings.
fn capped_items(
    items: [f64; 4],
    factors: [f64; 4],
    rates: [f64; 4],
    cap: f64,
    factor_in_rate: bool,
    coordinate: bool,
) -> ([f64; 4], bool) {
    let mut taxes = [0.0; 4];
    let (mut running_gross, mut running_net) = (0.0, 0.0);
    let mut capped_before = false;
    for k in 0..4 {
        let (item, factor, rate) = (items[k], factors[k], rates[k]);
        running_gross += item;
        running_net += factor * item;
        let effective_rate = if factor_in_rate { factor * rate } else { rate };
        let full = if factor_in_rate { item * effective_rate } else { factor * item * rate };
        let cap_earnings = if coordinate { running_net } else { running_gross * factor };
        let over = cap_earnings > cap;
        let excess_rate = if coordinate { rate } else { effective_rate };
        let reduced = full - (cap_earnings - cap) * excess_rate;
        taxes[k] = if capped_before { 0.0 } else if over { reduced } else { full };
        capped_before = capped_before || over;
    }
    (taxes, capped_before)
}

/// Half-to-even rounding to `decimals` places (Polars' default `round`).
pub fn round_half_even(x: f64, decimals: i32) -> f64 {
    let m = 10f64.powi(decimals);
    (x * m).round_ties_even() / m
}

pub struct PayrollInputs {
    pub pwages: f64,
    pub swages: f64,
    pub psemp: f64,
    pub ssemp: f64,
    pub pbusinc: f64,
    pub pprofinc: f64,
    pub sbusinc: f64,
    pub sprofinc: f64,
    pub fs: Fs,
}

pub fn payroll(i: &PayrollInputs, p: &PayrollParams) -> (Payroll, f64) {
    let n = p.nef;
    // Each earner's business income is assigned to that earner.
    let earners: [(f64, [f64; 3]); 2] =
        [(i.pwages, [i.psemp, i.pbusinc, i.pprofinc]), (i.swages, [i.ssemp, i.sbusinc, i.sprofinc])];
    let factors = [1.0, n, n, n];
    let oasdi_rates = [p.oasdi_rate, p.se_oasdi_rate, p.se_oasdi_rate, p.se_oasdi_rate];
    let hi_rates = [p.hi_rate; 4];

    let mut stream_taxes = [[0.0; 4]; 2];
    let mut primary_caps = (false, false);
    for (e, (wages, se)) in earners.iter().enumerate() {
        // Schedule SE allocation of net business income over positive sources.
        let mut se_items = *se;
        let mut taxable = (se[0] + se[1] + se[2]).max(0.0);
        taxable = if n * taxable >= 400.0 { taxable } else { 0.0 };
        let positive = [se[0].max(0.0), se[1].max(0.0), se[2].max(0.0)];
        let total = positive[0] + positive[1] + positive[2];
        for k in 0..3 {
            se_items[k] = if total > 0.0 { taxable * positive[k] / total } else { 0.0 };
        }
        let items = [wages.max(0.0), se_items[0], se_items[1], se_items[2]];
        let (oasdi, oasdi_capped) =
            capped_items(items, factors, oasdi_rates, p.wage_base, false, true);
        let (hi, hi_capped) =
            capped_items(items, factors, hi_rates, p.hi_wage_base, true, true);
        for k in 0..4 {
            stream_taxes[e][k] = oasdi[k] + hi[k];
        }
        if e == 0 {
            primary_caps = (oasdi_capped, hi_capped);
        }
    }

    let wage_tax = stream_taxes[0][0] + stream_taxes[1][0];
    let setax_self = stream_taxes[0][1] + stream_taxes[1][1];
    let setax_qbi = stream_taxes[0][2] + stream_taxes[1][2];
    let setax_sstb = stream_taxes[0][3] + stream_taxes[1][3];
    let setax = setax_self + setax_qbi + setax_sstb;

    // Self-employment income counts once toward the additional Medicare tax.
    let mut earnings = 0.0;
    for (wages, se) in &earners {
        earnings += wages.max(0.0);
        earnings += n * (se[0] + se[1] + se[2]).max(0.0);
    }
    let addmed = (earnings - p.addmed_threshold[i.fs as usize]).max(0.0) * p.addmed_rate;
    let oasdi_primary = if primary_caps.0 { 0.0 } else { p.oasdi_rate };
    let hi_primary = if primary_caps.1 { 0.0 } else { p.hi_rate };
    let payroll = Payroll {
        setax,
        setax_qbi,
        setax_sstb,
        addmed,
        fica: setax + wage_tax + addmed,
        tfica: setax + wage_tax / 2.0 + addmed,
        ficar: round_half_even((oasdi_primary + hi_primary) * 100.0, 2),
        own_fica_primary: stream_taxes[0][0] + p.own_share * (stream_taxes[0][1] + stream_taxes[0][2] + stream_taxes[0][3]),
        own_wage_fica_primary: stream_taxes[0][0],
        own_fica_secondary: stream_taxes[1][0] + p.own_share * (stream_taxes[1][1] + stream_taxes[1][2] + stream_taxes[1][3]),
        own_wage_fica_secondary: stream_taxes[1][0],
    };
    (payroll, wage_tax)
}
