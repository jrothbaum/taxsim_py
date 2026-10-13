//! The call's configuration: everything Python passes besides the data.
//!
//! The compiled calculator implements statutory mode only, so there are no
//! behavior switches: Python never routes TAXSIM-mode batches here.

use serde::Deserialize;

#[derive(Deserialize, Clone, Debug)]
pub struct Config {
    pub parameters_root: String,
    /// One law year for every row...
    pub year: Option<i64>,
    /// ...or the name of an input column holding each row's law year.
    pub year_column: Option<String>,
    /// A marginal-rate code (TAXSIM `mtr`) for every row...
    pub marginal: Option<i64>,
    /// ...or the name of an input column holding each row's code (0 for none).
    pub marginal_column: Option<String>,
}
