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
    apply_payroll_wage_bases_correctly: bool = True
    use_form_8995a_qbi_streams: bool = True
    floor_child_credit_phaseout_at_zero: bool = True
    count_business_and_professional_inputs_as_income: bool = True
    count_nonproperty_income_once: bool = True
    honor_separate_and_unmarried_codes_with_dependents: bool = True
    use_published_capital_gains_thresholds: bool = True
    tax_alaska_before_1979: bool = True
    use_vermont_published_standard_deductions: bool = True
    use_vermont_capital_gain_exclusion_options: bool = True
    use_new_mexico_published_child_tax_credit: bool = True
    pay_pennsylvania_child_care_credit_only_from_2023: bool = True
    keep_itemized_inputs_when_taking_standard_deduction: bool = True
    use_published_dependent_standard_deduction_minimum: bool = True
    limit_arkansas_low_income_table_to_its_range: bool = True
    use_arkansas_statutory_step_credits: bool = True
    leave_out_one_time_state_rebates: bool = True
    count_vermont_child_credit_by_statutory_age: bool = True
    use_colorado_statutory_sales_tax_refund: bool = True
    weight_falling_tables_correctly: bool = True
    use_wisconsin_form_standard_deduction: bool = True
    use_wisconsin_statutory_school_credit: bool = True
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
    apply_kentucky_pension_exclusion_schedule: bool = True
    step_kentucky_family_size_credit_by_poverty_share: bool = True
    limit_kentucky_itemized_deductions_to_mortgage_interest: bool = True
    double_kentucky_standard_deduction_for_joint_returns: bool = True
    file_kentucky_combined_returns_per_statute: bool = True
    deduct_massachusetts_employee_payroll_tax_per_spouse: bool = True
    count_capital_gains_in_dc_childless_eitc_investment_income: bool = True
    exclude_children_under_4_from_ny_empire_credit: bool = True
    pay_new_york_empire_child_credit_per_statute: bool = True
    step_ohio_exemption_by_income: bool = True
    count_children_under_6_by_dep6: bool = True
    use_idaho_published_brackets: bool = True
    initialize_arizona_short_term_loss: bool = True
    use_delaware_published_tax_brackets: bool = True
    use_maine_published_phaseout_starts: bool = True
    count_maine_fairness_credit_children_by_dep17: bool = True
    count_all_dependents_for_maine_dependent_credit: bool = True
    use_maine_year_standard_deduction: bool = True
    deduct_maine_pensions_at_any_age: bool = True
    use_maine_current_deductions_when_itemizing: bool = True
    credit_delaware_spouse_low_income_exclusion_to_spouse: bool = True
    let_delaware_itemize_when_federal_return_takes_standard_deduction: bool = True
    round_up_phaseout_steps: bool = True
    give_south_carolina_pension_deduction_to_each_taxpayer: bool = True
    use_south_carolina_published_tax_table: bool = True
    allow_qbi_deduction_in_south_carolina: bool = True
    subtract_state_tax_from_south_carolina_itemized_deductions: bool = True
    assign_south_carolina_business_income_to_its_earner: bool = True
    pay_vermont_telephone_credit: bool = True
    use_vermont_published_brackets: bool = True
    pay_minnesota_property_tax_refund_through_2023: bool = True
    deduct_iowa_federal_tax_after_refundable_credits: bool = True
    floor_maryland_itemized_deduction_at_zero: bool = True
    use_maryland_itemized_deduction_without_federal_itemizing: bool = True
    pay_maryland_refundable_eitc_only_above_tax: bool = True
    split_iowa_joint_income_by_wages_only: bool = True
    double_minnesota_separate_returns: bool = True
    allow_minnesota_personal_exemption_2013_to_2018: bool = True
    deduct_missouri_federal_tax_after_refundable_credits: bool = True
    compute_missouri_itemized_deduction_before_2018: bool = True
    step_missouri_federal_tax_deduction_share: bool = True
    pay_missouri_working_family_credit_by_statute: bool = True
    split_missouri_joint_income_by_earner_only: bool = True
    leave_missouri_federal_tax_deduction_uncapped_before_1994: bool = True
    limit_missouri_payroll_tax_deduction: bool = True
    deduct_missouri_federal_tax_from_2022: bool = True
    use_vermont_nonrefundable_child_care_credit_before_2003: bool = True
    pay_vermont_renter_rebate_after_1999: bool = True
    use_current_federal_standard_deduction_for_south_carolina_addback: bool = True
    use_ohio_published_tax_brackets: bool = True
    require_age_65_for_ohio_retirement_credit: bool = True
    subtract_new_york_social_security_at_any_age: bool = True
    compute_new_york_supplemental_tax_per_statute: bool = True
    use_new_york_statutory_child_care_credit: bool = True
    use_new_york_dependent_standard_deduction_alone: bool = True
    interpolate_new_york_household_credit_per_person: bool = True
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
    report_social_security_taxable_earnings: bool = True


STATUTORY_BEHAVIOR = BehaviorProfile()
# Switches whose TAXSIM error was fixed by the 2026 build (taxsim_2026_11_09.f):
# TAXSIM mode now keeps the statutory value for them.
FIXED_IN_2026_BUILD = frozenset(
    {
        "coordinate_oasdi_on_net_earnings",
        "prevent_negative_self_employment_tax",
        "include_business_income_before_1987",
        "add_amt_before_2000",
        "allow_other_dependent_credit_after_2021",
        "assign_business_income_to_its_earner",
        "count_self_employment_once_for_additional_medicare",
        "add_washington_working_families_credit",
        "drop_household_income_record_adjustment",
        "apply_illinois_exemption_income_limit",
        "require_young_child_for_california_yctc",
        "deduct_massachusetts_employee_payroll_tax_per_spouse",
        "complete_new_york_1986_maximum_tax_table",
        "limit_ohio_joint_credit_to_joint_returns",
        "use_minnesota_working_family_tables_by_children",
        "floor_montana_social_security_worksheet",
        "limit_arizona_property_credit_per_statute",
        "apply_michigan_retirement_tiers",
        "count_capital_gains_in_dc_childless_eitc_investment_income",
    }
)
TAXSIM_BEHAVIOR = BehaviorProfile(
    mode=CalculationMode.TAXSIM,
    **{
        f.name: f.default if f.name in FIXED_IN_2026_BUILD else not f.default
        for f in fields(BehaviorProfile)
        if f.name != "mode"
    },
)


def resolve_behavior(mode: str | CalculationMode) -> BehaviorProfile:
    """Return the behavior profile for a public calculation-mode value."""
    try:
        resolved = CalculationMode(mode)
    except (TypeError, ValueError) as exc:
        choices = ", ".join(repr(item.value) for item in CalculationMode)
        raise ValueError(f"calculation_mode must be one of: {choices}") from exc
    return TAXSIM_BEHAVIOR if resolved is CalculationMode.TAXSIM else STATUTORY_BEHAVIOR
