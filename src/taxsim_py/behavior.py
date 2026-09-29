"""Calculation modes and the deliberate behavioral differences between them."""

from dataclasses import dataclass
from enum import Enum


class CalculationMode(str, Enum):
    """Select TAXSIM replication or independently verified statutory corrections."""

    TAXSIM = "taxsim"
    STATUTORY = "statutory"


@dataclass(frozen=True)
class BehaviorProfile:
    """Internal switches for differences documented in statutory_corrections.md."""

    mode: CalculationMode
    coordinate_oasdi_on_net_earnings: bool
    enforce_schedule_se_minimum: bool
    prevent_negative_self_employment_tax: bool
    count_self_employment_once_for_additional_medicare: bool


TAXSIM_BEHAVIOR = BehaviorProfile(
    mode=CalculationMode.TAXSIM,
    coordinate_oasdi_on_net_earnings=False,
    enforce_schedule_se_minimum=False,
    prevent_negative_self_employment_tax=False,
    count_self_employment_once_for_additional_medicare=False,
)

STATUTORY_BEHAVIOR = BehaviorProfile(
    mode=CalculationMode.STATUTORY,
    coordinate_oasdi_on_net_earnings=True,
    enforce_schedule_se_minimum=True,
    prevent_negative_self_employment_tax=True,
    count_self_employment_once_for_additional_medicare=True,
)


def resolve_behavior(mode: str | CalculationMode) -> BehaviorProfile:
    """Return the behavior profile for a public calculation-mode value."""
    try:
        resolved = CalculationMode(mode)
    except (TypeError, ValueError) as exc:
        choices = ", ".join(repr(item.value) for item in CalculationMode)
        raise ValueError(f"calculation_mode must be one of: {choices}") from exc
    return TAXSIM_BEHAVIOR if resolved is CalculationMode.TAXSIM else STATUTORY_BEHAVIOR
