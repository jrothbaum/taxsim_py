"""Calculation modes and the deliberate behavioral differences between them."""


from __future__ import annotations
from dataclasses import dataclass, fields
from enum import Enum


class CalculationMode(str, Enum):
    """Select TAXSIM replication or independently verified statutory corrections."""

    TAXSIM = "taxsim"
    STATUTORY = "statutory"


@dataclass(frozen=True)
class BehaviorProfile:
    """Internal switches for differences documented in statutory_corrections.md.

    Each default is the statutory behavior; TAXSIM mode flips every switch.
    """

    mode: CalculationMode = CalculationMode.STATUTORY
    coordinate_oasdi_on_net_earnings: bool = True
    enforce_schedule_se_minimum: bool = True
    prevent_negative_self_employment_tax: bool = True
    count_self_employment_once_for_additional_medicare: bool = True
    include_unemployment_in_california_total_income: bool = True
    include_business_and_rental_income_in_california_minimum_tax: bool = True
    count_pre_1998_nonrefundable_credits_correctly: bool = True
    allow_other_dependent_credit_after_2021: bool = True
    require_young_child_for_california_yctc: bool = True
    exclude_hawaii_pensions: bool = True
    pay_california_yctc_per_young_child: bool = False
    apply_state_childless_eitc_minimum_age: bool = True
    count_additional_medicare_once_in_alabama_fica_deduction: bool = True
    limit_arizona_property_credit_per_statute: bool = True
    ignore_federal_salt_cap_in_california: bool = True
    use_age_qualified_children_for_dc_eitc: bool = True
    apply_illinois_exemption_income_limit: bool = True
    add_washington_working_families_credit: bool = True
    allow_kansas_itemizing_after_2020: bool = True
    apply_kentucky_pension_cap_per_taxpayer: bool = True
    deduct_massachusetts_employee_payroll_tax_per_spouse: bool = True
    count_capital_gains_in_dc_childless_eitc_investment_income: bool = True
    exclude_children_under_4_from_ny_empire_credit: bool = True
    count_only_children_under_13_for_oregon_dependent_care: bool = True
    exclude_transfers_from_provisional_income: bool = True
    assign_business_income_to_its_earner: bool = True
    add_amt_before_2000: bool = True
    apply_nonrefundable_credits_before_1998: bool = True
    exempt_amt_from_itemized_limit_1991_1992: bool = True
    cap_amt_exemption_for_dependents_only: bool = True
    use_joint_elderly_credit_threshold: bool = True
    allow_additional_child_credit_before_2001: bool = True
    include_business_income_before_1987: bool = True
    apply_1979_dividend_exclusion: bool = True
    exclude_wages_from_1977_preference_income: bool = True
    apply_elderly_credit_before_1987: bool = True
    deduct_mortgage_interest_from_1983_minimum_tax: bool = True
    correct_analytic_federal_rate: bool = True
    read_federal_child_care_credit_before_1987: bool = True
    use_current_rates_in_dc_earner_split: bool = True
    use_current_rates_in_dc_low_income_credit: bool = True
    apply_iowa_eitc_caps_as_in_2010_law: bool = True
    subtract_illinois_property_credit_once_in_eitc_limit: bool = True
    suspend_kentucky_itemized_limitation_through_2012: bool = True
    exclude_separate_returns_from_massachusetts_exemption_carryover: bool = True
    allow_massachusetts_child_care_deduction_2011_2020: bool = True
    include_self_employment_in_household_income: bool = True
    apply_michigan_retirement_tiers: bool = True
    pay_minnesota_homemaker_credit_to_nondependents: bool = True
    order_minnesota_1998_working_family_credit: bool = True
    use_minnesota_2012_joint_working_family_thresholds: bool = True
    use_minnesota_working_family_tables_by_children: bool = True
    limit_minnesota_child_care_credit_by_maximum_after_2017: bool = True
    allow_minnesota_renter_and_homeowner_refunds: bool = True
    cap_medicare_in_state_payroll_deduction: bool = True
    apply_missouri_property_credit_to_tax_paid: bool = True
    floor_mississippi_excess_charity: bool = True
    subtract_only_taxable_unemployment_in_montana: bool = True
    subtract_montana_separate_capital_loss_once: bool = True
    floor_montana_social_security_worksheet: bool = True
    suspend_montana_itemized_limit_through_2012: bool = True
    count_all_children_for_north_carolina_joint_child_care: bool = True
    take_north_carolina_child_care_credit_once: bool = True
    use_north_carolina_married_table_without_splitting: bool = True
    add_back_north_dakota_state_tax_for_itemizers_only: bool = True
    add_nebraska_single_additional_tax_once: bool = True
    disallow_new_jersey_capital_losses: bool = True
    exempt_new_mexico_social_security_1984_1989: bool = True
    count_new_mexico_aged_once_in_rebate: bool = True
    use_own_bounds_in_new_york_worksheets: bool = True
    use_single_standard_deduction_for_new_york_head_of_household_before_1987: bool = True
    complete_new_york_1986_maximum_tax_table: bool = True
    charge_new_york_unearned_income_tax_1987_1988: bool = True
    charge_new_york_minimum_tax_on_preferences_only: bool = True
    floor_new_york_itemized_deductions: bool = True
    limit_ohio_joint_credit_to_joint_returns: bool = True
    subtract_ohio_exemption_credit_once: bool = True
    replace_federal_unemployment_in_oregon_1981_1984: bool = True
    pay_oregon_2015_2017_kickers: bool = True
    apply_pennsylvania_forgiveness_rules: bool = True
    use_net_long_term_gain_in_rhode_island: bool = True
    halve_rhode_island_separate_standard_deduction: bool = True
    subtract_rhode_island_tax_once_in_minimum_tax: bool = True
    use_rhode_island_deduction_phaseout_steps_only: bool = True
    deduct_south_carolina_federal_tax_under_1980_law: bool = True
    allow_virginia_child_care_deduction_2003: bool = True
    scale_virginia_itemized_limit_threshold: bool = True
    omit_vermont_student_credit: bool = True
    floor_wisconsin_capital_gain_exclusion: bool = True
    start_wisconsin_standard_deduction_tiers_at_their_range: bool = True
    keep_wisconsin_mortgage_interest_cents: bool = True
    keep_wisconsin_1999_itemized_credit: bool = True
    index_wisconsin_joint_brackets: bool = True
    tax_wisconsin_1986_separate_returns: bool = True
    base_wisconsin_renter_credit_on_rent: bool = True
    halve_wisconsin_separate_amt_exemption: bool = True
    add_wisconsin_minimum_tax_once: bool = True
    use_wisconsin_net_household_income_1989_1990: bool = True
    credit_wisconsin_1983_homestead_excess: bool = True
    allow_west_virginia_disability_deduction: bool = True
    remove_state_tax_from_west_virginia_itemized_deductions: bool = True
    complete_west_virginia_1984_head_of_household_table: bool = True
    drop_household_income_record_adjustment: bool = True
    report_base_state_worksheet: bool = True


STATUTORY_BEHAVIOR = BehaviorProfile()
TAXSIM_BEHAVIOR = BehaviorProfile(
    mode=CalculationMode.TAXSIM,
    **{f.name: not f.default for f in fields(BehaviorProfile) if f.name != "mode"},
)


def resolve_behavior(mode: str | CalculationMode) -> BehaviorProfile:
    """Return the behavior profile for a public calculation-mode value."""
    try:
        resolved = CalculationMode(mode)
    except (TypeError, ValueError) as exc:
        choices = ", ".join(repr(item.value) for item in CalculationMode)
        raise ValueError(f"calculation_mode must be one of: {choices}") from exc
    return TAXSIM_BEHAVIOR if resolved is CalculationMode.TAXSIM else STATUTORY_BEHAVIOR
