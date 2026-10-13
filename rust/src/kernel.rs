//! The fused calculation: federal, payroll, state, the itemize-or-standard
//! choice and the state-tax feedback passes for every household, in one
//! plugin call.

use std::path::Path;

use polars::prelude::*;
use pyo3_polars::derive::polars_expr;
use rayon::prelude::*;
use serde::Deserialize;

use crate::common::{struct_output, Inputs, Outputs};
use crate::config::Config;
use crate::federal::{Federal, Inp};
use crate::payroll::round_half_even;
use crate::sales_tax::SalesTax;
use crate::states::StateCalc;

/// Passes of federal tax and state tax before the final one (`federal_state.ITERATIONS - 1`).
const FEEDBACK_PASSES: usize = 2;
/// The itemized choice wins only when it is lower by more than round-off.
const TIE_TOLERANCE: f64 = 1e-6;
/// Below this many households one thread does the work.
const PARALLEL_MIN_ROWS: usize = 4096;

const OUTPUTS: &[&str] = &[
    "fiitax",
    "siitax",
    "fica",
    "ficar",
    "tfica",
    "addmed",
    "agi",
    "taxable_income",
    "taxable_unemployment",
    "earned_income",
    "regular_tax",
    "cares",
    "frate",
    "srate",
];

/// The step TAXSIM changes an input by for marginal rates.
const MARGINAL_STEP: f64 = 0.01;
/// TAXSIM retries with a decrease when an increase gives a rate outside these.
const FEDERAL_RATE_LIMIT: f64 = 100.0;
const STATE_RATE_LIMIT: f64 = 25.0;

#[derive(Deserialize)]
pub struct Kwargs {
    config: Config,
}

fn output_type(_: &[Field]) -> PolarsResult<Field> {
    struct_output(OUTPUTS)
}

struct Context {
    federal: Federal,
    sales: SalesTax,
    /// The distinct state codes in the batch for this law year, sorted.
    codes: Vec<i64>,
    /// One calculator for each of `codes`.
    states: Vec<StateCalc>,
}

/// A household and the position of its state's calculator in `Context::states`.
struct Row {
    inp: Inp,
    slot: usize,
}

impl Context {
    fn load(config: &Config, year: i64, codes: Vec<i64>) -> Result<Self, String> {
        let root = Path::new(&config.parameters_root);
        Ok(Self {
            federal: Federal::load(root, year)?,
            sales: SalesTax::load(root, year)?,
            states: codes.iter().map(|code| StateCalc::load(root, year, *code)).collect::<Result<_, _>>()?,
            codes,
        })
    }
}

/// One variant (itemizing forced on or off): three passes of federal and
/// state tax, the first two feeding the state tax back as a deduction.
fn variant(row: &Row, itemizes: bool, c: &Context) -> [f64; 12] {
    let i = &row.inp;
    let mut ded = 0.0;
    let mut result = [0.0; 12];
    for pass in 0..=FEEDBACK_PASSES {
        let f = c.federal.run(i, ded, itemizes);
        let siitax = c.states[row.slot].tax(i, &f);
        if pass < FEEDBACK_PASSES {
            let resources = f.agi + i.gssi - f.taxable_social_security + i.transfers;
            let family_size = i.taxpayer_count() + i.depx;
            ded = siitax.max(0.0).max(c.sales.deduction(resources, family_size, i.state));
        } else {
            result = [
                f.fiitax,
                siitax,
                f.payroll.fica,
                f.payroll.ficar,
                f.payroll.tfica,
                f.payroll.addmed,
                f.agi,
                f.taxable_income,
                f.taxable_unemployment,
                f.earned_income,
                f.regular_tax,
                f.cares,
            ];
        }
    }
    result
}

/// The row with the input for marginal-rate `code` changed by `step`; `None` for an unknown code.
fn perturbed(inp: &Inp, code: i64, step: f64) -> Option<Inp> {
    let mut i = *inp;
    match code {
        11 => {
            let wages = i.pwages + i.swages;
            let (p, s) = (i.pwages, i.swages);
            if wages != 0.0 {
                i.pwages = p + step * p / wages;
                i.swages = s + step * s / wages;
            } else {
                i.pwages = p + step / 2.0;
                i.swages = s + step / 2.0;
            }
        }
        12 => i.dividends += step,
        14 => i.intrec += step,
        17 => i.psemp += step,
        20 => i.pensions += step,
        30 => i.nonprop -= step,
        41 => i.transfers += step,
        51 => i.proptax += step,
        54 => i.otheritem += step,
        56 => i.mortgage += step,
        64 => i.childcare += step,
        68 => i.stcg += step,
        70 => i.ltcg += step,
        79 => i.otherprop += step,
        82 => i.ui += step,
        85 => i.pwages += step,
        86 => i.swages += step,
        91 => i.gssi += step,
        160 => i.rentpaid += step,
        180 => i.sui += step,
        211 => i.pbusinc += step,
        212 => i.pprofinc += step,
        213 => i.scorp += step,
        214 => i.sbusinc += step,
        215 => i.sprofinc += step,
        _ => return None,
    }
    Some(i)
}

/// Federal and state marginal rates (percent) from a one-cent increase, or a decrease when
/// the increase gives an implausible rate.
fn marginal_rates(row: &Row, base: &[f64; 12], code: i64, c: &Context) -> (f64, f64) {
    let mut rates = (0.0, 0.0);
    for step in [MARGINAL_STEP, -MARGINAL_STEP] {
        let inp = perturbed(&row.inp, code, step).expect("marginal-rate code was validated");
        let changed = household(&Row { inp, slot: row.slot }, c);
        rates = (100.0 * (changed[0] - base[0]) / step, 100.0 * (changed[1] - base[1]) / step);
        if step < 0.0 || (rates.0.abs() < FEDERAL_RATE_LIMIT && rates.1.abs() < STATE_RATE_LIMIT) {
            break;
        }
    }
    (round_half_even(rates.0.clamp(-99.0, 999.0), 2), round_half_even(rates.1.clamp(-99.0, 999.0), 2))
}

fn household(row: &Row, c: &Context) -> [f64; 12] {
    let itemized = variant(row, true, c);
    let standard = variant(row, false, c);
    let itemize_wins = (itemized[0] + itemized[1]) < (standard[0] + standard[1]) - TIE_TOLERANCE;
    if itemize_wins { itemized } else { standard }
}

/// The input columns, from which one household at a time is built (no per-row vectors are kept).
struct Reader<'a> {
    state: &'a [f64],
    mstat: &'a [f64],
    depx: &'a [f64],
    dep6: &'a [f64],
    dep13: &'a [f64],
    dep17: &'a [f64],
    dep18: &'a [f64],
    page: &'a [f64],
    sage: &'a [f64],
    pwages: &'a [f64],
    swages: &'a [f64],
    dividends: &'a [f64],
    intrec: &'a [f64],
    stcg: &'a [f64],
    ltcg: &'a [f64],
    otherprop: &'a [f64],
    nonprop: &'a [f64],
    pensions: &'a [f64],
    gssi: &'a [f64],
    ui: &'a [f64],
    transfers: &'a [f64],
    proptax: &'a [f64],
    otheritem: &'a [f64],
    childcare: &'a [f64],
    mortgage: &'a [f64],
    scorp: &'a [f64],
    pbusinc: &'a [f64],
    pprofinc: &'a [f64],
    sbusinc: &'a [f64],
    sprofinc: &'a [f64],
    pui: &'a [f64],
    sui: &'a [f64],
    psemp: &'a [f64],
    ssemp: &'a [f64],
    charity_cash: Option<&'a [f64]>,
    rentpaid: Option<&'a [f64]>,
    under3: Option<&'a [f64]>,
    under4: Option<&'a [f64]>,
    under7: Option<&'a [f64]>,
}

impl<'a> Reader<'a> {
    fn new(x: &'a Inputs) -> PolarsResult<Self> {
        let col = |name: &str| x.col(name);
        let optional = |name: &str| x.col(name).ok();
        Ok(Self {
            state: col("state")?,
            mstat: col("mstat")?,
            depx: col("depx")?,
            dep6: col("dep6")?,
            dep13: col("dep13")?,
            dep17: col("dep17")?,
            dep18: col("dep18")?,
            page: col("page")?,
            sage: col("sage")?,
            pwages: col("pwages")?,
            swages: col("swages")?,
            dividends: col("dividends")?,
            intrec: col("intrec")?,
            stcg: col("stcg")?,
            ltcg: col("ltcg")?,
            otherprop: col("otherprop")?,
            nonprop: col("nonprop")?,
            pensions: col("pensions")?,
            gssi: col("gssi")?,
            ui: col("ui")?,
            transfers: col("transfers")?,
            proptax: col("proptax")?,
            otheritem: col("otheritem")?,
            childcare: col("childcare")?,
            mortgage: col("mortgage")?,
            scorp: col("scorp")?,
            pbusinc: col("pbusinc")?,
            pprofinc: col("pprofinc")?,
            sbusinc: col("sbusinc")?,
            sprofinc: col("sprofinc")?,
            pui: col("pui")?,
            sui: col("sui")?,
            psemp: col("psemp")?,
            ssemp: col("ssemp")?,
            charity_cash: optional("charity_cash"),
            rentpaid: optional("rentpaid"),
            under3: optional("children_under_3"),
            under4: optional("children_under_4"),
            under7: optional("children_under_7"),
        })
    }

    fn at(&self, r: usize) -> Inp {
        Inp {
            state: self.state[r] as i64,
            mstat: self.mstat[r],
            depx: self.depx[r],
            dep6: self.dep6[r],
            dep13: self.dep13[r],
            dep17: self.dep17[r],
            dep18: self.dep18[r],
            page: self.page[r],
            sage: self.sage[r],
            pwages: self.pwages[r],
            swages: self.swages[r],
            dividends: self.dividends[r],
            intrec: self.intrec[r],
            stcg: self.stcg[r],
            ltcg: self.ltcg[r],
            otherprop: self.otherprop[r],
            nonprop: self.nonprop[r],
            pensions: self.pensions[r],
            gssi: self.gssi[r],
            ui: self.ui[r],
            transfers: self.transfers[r],
            proptax: self.proptax[r],
            otheritem: self.otheritem[r],
            childcare: self.childcare[r],
            mortgage: self.mortgage[r],
            scorp: self.scorp[r],
            pbusinc: self.pbusinc[r],
            pprofinc: self.pprofinc[r],
            sbusinc: self.sbusinc[r],
            sprofinc: self.sprofinc[r],
            pui: self.pui[r],
            sui: self.sui[r],
            psemp: self.psemp[r],
            ssemp: self.ssemp[r],
            charity_cash: self.charity_cash.map_or(0.0, |c| c[r]),
            rentpaid: self.rentpaid.map_or(0.0, |c| c[r]),
            children_under_3: self.under3.map_or(0.0, |c| c[r]),
            children_under_4: self.under4.map_or(0.0, |c| c[r]),
            children_under_7: self.under7.map_or(0.0, |c| c[r]),
        }
    }
}

#[polars_expr(output_type_func=output_type)]
fn taxsim(inputs: &[Series], kwargs: Kwargs) -> PolarsResult<Series> {
    let x = Inputs::from_struct(&inputs[0])?;
    let reader = Reader::new(&x)?;
    let config = &kwargs.config;
    // Each row's law year: one value for the whole call, or an input column.
    let years: Vec<i64> = match (&config.year_column, config.year) {
        (Some(name), _) => {
            let column = x.col(name)?;
            if let Some(bad) = column.iter().find(|y| y.fract() != 0.0) {
                polars_bail!(ComputeError: "year column {name} holds the non-integer value {bad}");
            }
            column.iter().map(|y| *y as i64).collect()
        }
        (None, Some(year)) => vec![year; x.len],
        (None, None) => polars_bail!(ComputeError: "the config needs a year or a year column"),
    };
    // One context (federal law, sales tax and the states present) for each law year in the call.
    let mut pairs: Vec<(i64, i64)> = years.iter().zip(reader.state).map(|(y, s)| (*y, *s as i64)).collect();
    pairs.sort_unstable();
    pairs.dedup();
    let mut law_years: Vec<i64> = Vec::new();
    let mut contexts: Vec<Context> = Vec::new();
    let mut start = 0;
    while start < pairs.len() {
        let year = pairs[start].0;
        let end = start + pairs[start..].iter().take_while(|(y, _)| *y == year).count();
        let codes: Vec<i64> = pairs[start..end].iter().map(|(_, s)| *s).collect();
        contexts.push(Context::load(config, year, codes).map_err(|e| polars_err!(ComputeError: "{e}"))?);
        law_years.push(year);
        start = end;
    }
    // Each row's marginal-rate code (0 for none).
    let codes: Option<Vec<i64>> = match (&config.marginal_column, config.marginal) {
        (Some(name), _) => Some(x.col(name)?.iter().map(|m| *m as i64).collect()),
        (None, Some(code)) => Some(vec![code; x.len]),
        (None, None) => None,
    };
    if let Some(codes) = &codes {
        let mut seen: Vec<i64> = codes.clone();
        seen.sort_unstable();
        seen.dedup();
        if let Some(bad) = seen.iter().find(|code| **code != 0 && perturbed(&Inp::default(), **code, MARGINAL_STEP).is_none()) {
            polars_bail!(ComputeError: "Unsupported marginal-rate code (mtr): {bad}");
        }
    }
    let compute = |r: usize| -> [f64; 14] {
        let inp = reader.at(r);
        let context = &contexts[law_years.binary_search(&years[r]).expect("year was collected")];
        let slot = context.codes.binary_search(&inp.state).expect("state was collected");
        let row = Row { inp, slot };
        let base = household(&row, context);
        let (frate, srate) = match &codes {
            Some(codes) if codes[r] != 0 => marginal_rates(&row, &base, codes[r], context),
            _ => (0.0, 0.0),
        };
        [
            base[0], base[1], base[2], base[3], base[4], base[5], base[6], base[7], base[8], base[9], base[10], base[11], frate, srate,
        ]
    };
    let results: Vec<[f64; 14]> = if x.len >= PARALLEL_MIN_ROWS {
        polars_core::runtime::RAYON.install(|| (0..x.len).into_par_iter().map(compute).collect())
    } else {
        (0..x.len).map(compute).collect()
    };
    let mut out = Outputs::new(OUTPUTS, results.len());
    for row in &results {
        out.push_row(row);
    }
    out.into_struct("taxsim")
}
