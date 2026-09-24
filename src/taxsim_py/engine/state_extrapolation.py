"""State tax year extrapolation."""

import polars as pl

from taxsim_py.engine.schema import PARAMETERS_ROOT, load_yaml

_PARAMS = load_yaml(PARAMETERS_ROOT / "national" / "state_cpi_extrapolation.yaml")
LASTAT = int(_PARAMS["lastat"])
_XNDXA = {int(y): float(v) for y, v in _PARAMS["xndxa"].items()}


def resolve_state_year(year: int) -> tuple[int, float]:
    """Return the effective state-law year and inflation factor."""
    if year <= LASTAT:
        return year, 1.0
    flate = _XNDXA[year] / _XNDXA[LASTAT]
    return LASTAT, flate


def deflate_for_extrapolation(df: pl.DataFrame, flate: float, columns: list[str]) -> pl.DataFrame:
    """Deflate selected columns for an extrapolated state-law year."""
    if flate == 1.0:
        return df
    present = set(df.collect_schema().names())
    to_scale = [c for c in columns if c in present]
    if not to_scale:
        return df
    return df.with_columns([(pl.col(c) / flate).alias(c) for c in to_scale])
