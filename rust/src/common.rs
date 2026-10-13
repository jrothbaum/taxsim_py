//! Shared helpers for the state kernels.

use polars::prelude::*;

/// Read named columns of the input struct as dense `f64` slices, borrowed from the
/// input (no copy when a column is already contiguous `f64` without nulls).
pub struct Inputs {
    columns: Vec<(String, Float64Chunked)>,
    pub len: usize,
}

impl Inputs {
    pub fn from_struct(series: &Series) -> PolarsResult<Self> {
        let st = series.struct_()?;
        let mut columns = Vec::new();
        for field in st.fields_as_series() {
            let cast = field.cast(&DataType::Float64)?;
            let ca = cast.f64()?;
            polars_ensure!(
                ca.null_count() == 0,
                ComputeError: "null in input column {}", field.name()
            );
            columns.push((field.name().to_string(), ca.rechunk().into_owned()));
        }
        Ok(Self { columns, len: series.len() })
    }

    pub fn col(&self, name: &str) -> PolarsResult<&[f64]> {
        let (_, ca) = self
            .columns
            .iter()
            .find(|(n, _)| n == name)
            .ok_or_else(|| polars_err!(ComputeError: "missing input column {}", name))?;
        ca.cont_slice().map_err(|_| polars_err!(ComputeError: "input column {} is not contiguous", name))
    }
}

/// Output columns, in the order given by `names`.
pub struct Outputs {
    names: &'static [&'static str],
    data: Vec<Vec<f64>>,
}

impl Outputs {
    pub fn new(names: &'static [&'static str], len: usize) -> Self {
        Self { names, data: names.iter().map(|_| Vec::with_capacity(len)).collect() }
    }

    pub fn push_row(&mut self, row: &[f64]) {
        for (column, value) in self.data.iter_mut().zip(row) {
            column.push(*value);
        }
    }

    pub fn into_struct(self, name: &str) -> PolarsResult<Series> {
        let len = self.data.first().map_or(0, Vec::len);
        let fields: Vec<Series> = self
            .names
            .iter()
            .zip(self.data)
            .map(|(n, v)| Series::new((*n).into(), v))
            .collect();
        Ok(StructChunked::from_series(name.into(), len, fields.iter())?.into_series())
    }
}

pub fn struct_output(names: &[&str]) -> PolarsResult<Field> {
    let fields = names.iter().map(|n| Field::new((*n).into(), DataType::Float64)).collect();
    Ok(Field::new("taxsim".into(), DataType::Struct(fields)))
}

/// Tax under marginal brackets given as `[start, rate]` rows.
pub struct Brackets {
    starts: Vec<f64>,
    rates: Vec<f64>,
    base: Vec<f64>,
}

impl Brackets {
    pub fn new(rows: &[Vec<f64>]) -> Self {
        let starts: Vec<f64> = rows.iter().map(|r| r[0]).collect();
        let rates: Vec<f64> = rows.iter().map(|r| r[1]).collect();
        let mut base = vec![0.0];
        for i in 0..starts.len() - 1 {
            base.push(base[i] + (starts[i + 1] - starts[i]) * rates[i]);
        }
        Self { starts, rates, base }
    }

    /// Index of the bracket containing `income` (a bracket starts at its threshold).
    fn index(&self, income: f64) -> usize {
        self.starts.partition_point(|s| *s <= income).saturating_sub(1)
    }

    pub fn tax(&self, income: f64) -> f64 {
        let i = self.index(income);
        self.base[i] + self.rates[i] * (income - self.starts[i]).max(0.0)
    }

    #[allow(dead_code)] // the marginal rate in the state detail outputs
    pub fn rate(&self, income: f64) -> f64 {
        self.rates[self.index(income)]
    }
}
