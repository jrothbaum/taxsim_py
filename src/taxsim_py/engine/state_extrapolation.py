"""State-tax year extrapolation (`statax`, taxsim_2022_10_21.f:4-90).

The source's own state dispatcher hardcodes a `lastat` constant (2021 in this
vintage) and never has real per-state statutory parameters beyond it - any
year past `lastat` is handled generically, not per-state: deflate the
relevant inputs by the CPI ratio `xndxa(year)/xndxa(lastat)`, run the real
`lastat`-year law, inflate the resulting tax back up by the same ratio. Every
state calculator in this project should call `resolve_state_year` once and
use the returned `(effective_year, flate)` the same way: divide every
dollar-valued (or count-valued - see the module note in
parameters/national/state_cpi_extrapolation.yaml about `exemps` being
divided too, a genuine, replicated-as-found quirk) federal-computed input by
`flate` before running `effective_year`'s real parameters, then multiply the
final state tax by `flate`.
"""

import polars as pl

from taxsim_py.engine.schema import PARAMETERS_ROOT, load_yaml

_PARAMS = load_yaml(PARAMETERS_ROOT / "national" / "state_cpi_extrapolation.yaml")
LASTAT = int(_PARAMS["lastat"])
_XNDXA = {int(y): float(v) for y, v in _PARAMS["xndxa"].items()}


def resolve_state_year(year: int) -> tuple[int, float]:
    """Return `(effective_year, flate)` for a state-tax computation.

    `effective_year` is the real, parameter-coded year to run (never above
    `LASTAT`). `flate` is 1.0 for year<=LASTAT (no extrapolation needed);
    for year>LASTAT it's `xndxa(year)/xndxa(LASTAT)` - divide dollar/count
    inputs by it before computing, multiply the resulting tax by it after.
    """
    if year <= LASTAT:
        return year, 1.0
    flate = _XNDXA[year] / _XNDXA[LASTAT]
    return LASTAT, flate


def deflate_for_extrapolation(df: pl.DataFrame, flate: float, columns: list[str]) -> pl.DataFrame:
    """Divide each of `columns` (whichever are actually present in `df`) by
    `flate`, matching the source's own blanket, upfront scaling loop
    (`ds(i)=data(i)/flate` for raw inputs, `coms(i)=comnew(i)/flate` for
    federal-computed values - taxsim_2022_10_21.f:62-65) rather than
    scattering `/flate` through a calculator's own formulas piecemeal. A
    no-op for year<=LASTAT (`flate==1.0`), so safe to call unconditionally
    at the top of every state calculator, right after `resolve_state_year`,
    before any of the calculator's own logic reads these columns.

    Callers still need their own `* flate` on the FINAL `siitax` at the end
    (this only handles the deflate half) and still need to follow the
    source's own small set of named exceptions that do NOT get scaled (a
    handful of specific comnew positions, e.g. the itemize/standard flag -
    not every column here is exempt automatically, only ones a calculator
    deliberately omits from `columns`)."""
    if flate == 1.0:
        return df
    to_scale = [c for c in columns if c in df.columns]
    if not to_scale:
        return df
    return df.with_columns([(pl.col(c) / flate).alias(c) for c in to_scale])
