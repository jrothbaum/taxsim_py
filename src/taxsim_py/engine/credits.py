"""Federal tax credit calculations."""

import polars as pl


def child_care_credit_rate(agi: pl.Expr, rate: dict) -> pl.Expr:
    """Child and dependent care credit rate for one year's `rate` parameters.

    The rate falls one point per `step_amount` of AGI above `phase_start`
    from `top_rate` to `floor_rate`; with a second phase, from `top_rate` to
    `mid_rate` up to `first_phase_ceiling` and from `mid_rate` to zero up to
    `second_phase_ceiling`.
    """
    top_rate = float(rate["top_rate"])
    if "second_phase_start" not in rate:
        stepped_down = top_rate - ((agi - rate["phase_start"]) / rate["step_amount"]).clip(0, None) * 0.01
        return pl.max_horizontal(stepped_down, pl.lit(float(rate["floor_rate"])))
    mid_rate = float(rate["mid_rate"])
    first_start, second_start = float(rate["first_phase_start"]), float(rate["second_phase_start"])
    first_phase_rate = top_rate - ((agi - first_start) / rate["first_phase_step_amount"]).clip(0, None) * 0.01
    second_phase_rate = mid_rate - ((agi - second_start) / rate["second_phase_step_amount"]).clip(0, None) * 0.01
    return (
        pl.when(agi <= first_start)
        .then(top_rate)
        .when(agi < rate["first_phase_ceiling"])
        .then(first_phase_rate)
        .when(agi <= second_start)
        .then(mid_rate)
        .when(agi < rate["second_phase_ceiling"])
        .then(second_phase_rate)
        .otherwise(0.0)
    )
