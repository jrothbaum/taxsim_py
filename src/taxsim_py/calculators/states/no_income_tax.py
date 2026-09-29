"""States without a broad income tax, and no state (TAXSIM state 0)."""

from taxsim_py.behavior import BehaviorProfile, TAXSIM_BEHAVIOR
import polars as pl


def compute_no_income_tax(df: pl.DataFrame, year: int, behavior: BehaviorProfile = TAXSIM_BEHAVIOR) -> pl.DataFrame:
    """Return zero state income tax for each row."""
    return df.with_columns(siitax=pl.lit(0.0))
