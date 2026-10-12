"""Calculation modes and the deliberate behavioral differences between them."""


from __future__ import annotations
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
    include_unemployment_in_california_total_income: bool
    include_business_and_rental_income_in_california_minimum_tax: bool
    count_pre_1998_nonrefundable_credits_correctly: bool
    allow_other_dependent_credit_after_2021: bool
    require_young_child_for_california_yctc: bool
    exclude_hawaii_pensions: bool
    pay_california_yctc_per_young_child: bool
    apply_state_childless_eitc_minimum_age: bool
    count_additional_medicare_once_in_alabama_fica_deduction: bool
    limit_arizona_property_credit_per_statute: bool
    ignore_federal_salt_cap_in_california: bool
    use_age_qualified_children_for_dc_eitc: bool
    apply_illinois_exemption_income_limit: bool
    add_washington_working_families_credit: bool
    allow_kansas_itemizing_after_2020: bool
    apply_kentucky_pension_cap_per_taxpayer: bool
    deduct_massachusetts_employee_payroll_tax_per_spouse: bool
    count_capital_gains_in_dc_childless_eitc_investment_income: bool
    exclude_children_under_4_from_ny_empire_credit: bool
    count_only_children_under_13_for_oregon_dependent_care: bool


TAXSIM_BEHAVIOR = BehaviorProfile(
    mode=CalculationMode.TAXSIM,
    coordinate_oasdi_on_net_earnings=False,
    enforce_schedule_se_minimum=False,
    prevent_negative_self_employment_tax=False,
    count_self_employment_once_for_additional_medicare=False,
    include_unemployment_in_california_total_income=False,
    include_business_and_rental_income_in_california_minimum_tax=False,
    count_pre_1998_nonrefundable_credits_correctly=False,
    allow_other_dependent_credit_after_2021=False,
    require_young_child_for_california_yctc=False,
    exclude_hawaii_pensions=False,
    pay_california_yctc_per_young_child=True,
    apply_state_childless_eitc_minimum_age=False,
    count_additional_medicare_once_in_alabama_fica_deduction=False,
    limit_arizona_property_credit_per_statute=False,
    ignore_federal_salt_cap_in_california=False,
    use_age_qualified_children_for_dc_eitc=False,
    apply_illinois_exemption_income_limit=False,
    add_washington_working_families_credit=False,
    allow_kansas_itemizing_after_2020=False,
    apply_kentucky_pension_cap_per_taxpayer=False,
    deduct_massachusetts_employee_payroll_tax_per_spouse=False,
    count_capital_gains_in_dc_childless_eitc_investment_income=False,
    exclude_children_under_4_from_ny_empire_credit=False,
    count_only_children_under_13_for_oregon_dependent_care=False,
)

STATUTORY_BEHAVIOR = BehaviorProfile(
    mode=CalculationMode.STATUTORY,
    coordinate_oasdi_on_net_earnings=True,
    enforce_schedule_se_minimum=True,
    prevent_negative_self_employment_tax=True,
    count_self_employment_once_for_additional_medicare=True,
    include_unemployment_in_california_total_income=True,
    include_business_and_rental_income_in_california_minimum_tax=True,
    count_pre_1998_nonrefundable_credits_correctly=True,
    allow_other_dependent_credit_after_2021=True,
    require_young_child_for_california_yctc=True,
    exclude_hawaii_pensions=True,
    pay_california_yctc_per_young_child=False,
    apply_state_childless_eitc_minimum_age=True,
    count_additional_medicare_once_in_alabama_fica_deduction=True,
    limit_arizona_property_credit_per_statute=True,
    ignore_federal_salt_cap_in_california=True,
    use_age_qualified_children_for_dc_eitc=True,
    apply_illinois_exemption_income_limit=True,
    add_washington_working_families_credit=True,
    allow_kansas_itemizing_after_2020=True,
    apply_kentucky_pension_cap_per_taxpayer=True,
    deduct_massachusetts_employee_payroll_tax_per_spouse=True,
    count_capital_gains_in_dc_childless_eitc_investment_income=True,
    exclude_children_under_4_from_ny_empire_credit=True,
    count_only_children_under_13_for_oregon_dependent_care=True,
)


def resolve_behavior(mode: str | CalculationMode) -> BehaviorProfile:
    """Return the behavior profile for a public calculation-mode value."""
    try:
        resolved = CalculationMode(mode)
    except (TypeError, ValueError) as exc:
        choices = ", ".join(repr(item.value) for item in CalculationMode)
        raise ValueError(f"calculation_mode must be one of: {choices}") from exc
    return TAXSIM_BEHAVIOR if resolved is CalculationMode.TAXSIM else STATUTORY_BEHAVIOR
