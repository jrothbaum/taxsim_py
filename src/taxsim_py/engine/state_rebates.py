"""One-time state tax rebates, as the 2026 TAXSIM build pays them."""

from __future__ import annotations

from typing import Any

import polars as pl

from taxsim_py.engine.inputs import files_head_of_household, files_joint, files_separate
from taxsim_py.engine.schema import PARAMETERS_ROOT, load_yaml

_BY_ABBREVIATION = load_yaml(PARAMETERS_ROOT / "national" / "state_rebates.yaml")


def _taxsim_code(abbreviation: str) -> int:
    from taxsim_py.calculators.states import STATE_CALCULATOR_PATHS

    for code, (module, _function) in STATE_CALCULATOR_PATHS.items():
        if module.rstrip("_") == abbreviation:
            return code
    raise KeyError(abbreviation)


STATE_REBATE_PARAMS = {_taxsim_code(abbreviation): years for abbreviation, years in _BY_ABBREVIATION.items()}


def state_rebate(state: int, year: int, siitax: pl.Expr, tax_before_credits: pl.Expr) -> pl.Expr | None:
    """The rebate paid in `year` to residents of `state`, or None when there is none.

    `siitax` is the state tax before the rebate and `tax_before_credits` the
    state tax before credits.
    """
    spec: dict[str, Any] | None = STATE_REBATE_PARAMS.get(state, {}).get(year)
    if spec is None:
        return None
    filers = pl.when(files_joint()).then(2.0).otherwise(1.0)
    people = filers + pl.col("depx")
    not_dependent_filer = pl.col("mstat") != 8
    agi = pl.col("agi")
    amount = pl.lit(0.0)
    if "components" in spec:
        for component in spec["components"]:
            amount = amount + pl.max_horizontal(component["per_person"] * people, component["tax_share"] * siitax)
    if "per_filer" in spec:
        per_filer = float(spec["per_filer"])
        if "head_of_household" in spec:
            amount = amount + pl.when(files_head_of_household()).then(float(spec["head_of_household"])).otherwise(per_filer * filers)
        else:
            amount = amount + per_filer * filers
    if "per_person_high_income" in spec:
        low_income = agi < spec["agi_limit_per_filer"] * filers
        amount = amount + pl.when(low_income).then(spec["per_person"] * people).otherwise(spec["per_person_high_income"] * people)
    elif "per_person" in spec and "components" not in spec:
        amount = amount + spec["per_person"] * people
    if "per_dependent" in spec:
        amount = amount + pl.min_horizontal(spec["per_dependent"] * pl.col("depx"), float(spec["dependent_maximum"]))
    if "per_child" in spec:
        children = pl.min_horizontal(pl.col("dep17") if state != 40 else pl.col("dep18"), float(spec["maximum_children"]))
        child_amount = spec["per_child"] * children
        if "agi_limit" in spec:
            limits = spec["agi_limit"]
            limit = (
                pl.when(files_joint()).then(float(limits["married_joint"]))
                .when(files_head_of_household()).then(float(limits["head_of_household"]))
                .when(files_separate()).then(float(limits["married_separate"]))
                .otherwise(float(limits["single"]))
            )
            share = (1 - (agi - limit).clip(0, None) / 1000 * spec["phaseout_per_1000"]).clip(0, None)
            child_amount = child_amount * share
        amount = amount + child_amount
    if "per_other_dependent" in spec:
        amount = amount + spec["per_other_dependent"] * (pl.col("depx") - pl.col("dep17")).clip(0, None)
    if "tax_share" in spec and "components" not in spec:
        amount = amount + spec["tax_share"] * siitax.clip(0, None)
    if "minimum_primary_age" in spec:
        amount = pl.when(pl.col("page") >= spec["minimum_primary_age"]).then(amount).otherwise(0.0)
    if "agi_limit_per_filer" in spec and "per_person_high_income" not in spec:
        limit = spec["agi_limit_per_filer"] * filers
        if "head_of_household_agi_limit" in spec:
            limit = pl.when(files_head_of_household()).then(float(spec["head_of_household_agi_limit"])).otherwise(limit)
        amount = pl.when(agi < limit).then(amount).otherwise(0.0)
    if spec.get("requires_tax"):
        amount = pl.when(siitax > 0).then(amount).otherwise(0.0)
    if spec.get("limit_to_tax"):
        amount = pl.min_horizontal(amount, siitax.clip(0, None))
    if spec.get("limit_to_tax_before_credits"):
        amount = pl.min_horizontal(amount, tax_before_credits.clip(0, None))
    return pl.when(not_dependent_filer).then(amount).otherwise(0.0)
