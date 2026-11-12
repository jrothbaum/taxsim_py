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
    count_other_property_income_from_2024: bool = True
    index_salt_cap_from_2026: bool = True
    halve_salt_phaseout_for_separate_returns_per_statute: bool = True
    phase_out_amt_exemption_at_half_rate_from_2026: bool = True
    honor_separate_and_unmarried_codes_with_dependents: bool = True
    use_published_capital_gains_thresholds: bool = True
    tax_alaska_before_1979: bool = True
    use_vermont_published_standard_deductions: bool = True
    use_vermont_social_security_exemption_starts_per_statute: bool = True
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
    use_additional_medicare_separate_threshold: bool = True
    use_iowa_2020_bracket_inflation_per_statute: bool = True
    include_state_taxes_in_iowa_itemized_deduction_from_2020: bool = True
    compute_iowa_tax_from_2023_per_statute: bool = True
    include_unemployment_in_california_total_income: bool = True
    include_business_and_rental_income_in_california_minimum_tax: bool = True
    count_pre_1998_nonrefundable_credits_correctly: bool = True
    allow_other_dependent_credit_after_2021: bool = True
    require_young_child_for_california_yctc: bool = True
    exclude_hawaii_pensions: bool = True
    pay_california_yctc_per_young_child: bool = False
    apply_state_childless_eitc_minimum_age: bool = True
    count_additional_medicare_once_in_alabama_fica_deduction: bool = True
    deduct_half_of_fica_in_alabama_itemized_deduction: bool = True
    deduct_alabama_federal_tax_before_stimulus_credits: bool = True
    step_alabama_dependent_exemption_per_statute: bool = True
    step_alabama_standard_deduction_per_statute: bool = True
    exempt_alabama_retirement_income_in_2022: bool = True
    limit_arizona_property_credit_per_statute: bool = True
    ignore_federal_salt_cap_in_california: bool = True
    use_age_qualified_children_for_dc_eitc: bool = True
    apply_illinois_exemption_income_limit: bool = True
    add_washington_working_families_credit: bool = True
    allow_kansas_itemizing_after_2020: bool = True
    deduct_kansas_other_taxes_per_statute: bool = True
    pay_kansas_homestead_refund_from_2007_per_statute: bool = True
    apply_kentucky_pension_cap_per_taxpayer: bool = True
    compute_louisiana_tax_per_statute: bool = True
    count_wisconsin_rent_at_statutory_share: bool = True
    exclude_other_itemized_deductions_from_wisconsin_credit: bool = True
    compute_new_mexico_deductions_per_statute: bool = True
    use_new_mexico_low_income_rebate_per_statute: bool = True
    use_new_mexico_working_families_rates_per_statute: bool = True
    use_new_york_household_credit_table_1990_2016: bool = True
    pay_new_jersey_renter_credit_per_statute: bool = True
    compute_new_jersey_tax_per_statute: bool = True
    exclude_nonproperty_income_from_new_jersey_income: bool = True
    exclude_new_jersey_pensions_per_statute: bool = True
    tax_new_jersey_top_rate_after_1982_per_statute: bool = True
    tax_full_capital_gains_in_new_jersey_before_1987: bool = True
    tax_nebraska_before_1987_per_statute: bool = True
    limit_nebraska_property_tax_deduction_to_federal_cap: bool = True
    use_nebraska_high_income_tables_per_statute: bool = True
    pay_nebraska_refundable_child_care_per_statute: bool = True
    zero_nebraska_standard_deduction_when_federal_itemizes: bool = True
    exclude_niit_from_louisiana_federal_tax_deduction: bool = True
    use_louisiana_brackets_per_statute: bool = True
    reduce_louisiana_joint_tax_once_for_dependents: bool = True
    allow_louisiana_federal_credit_share_per_statute: bool = True
    use_louisiana_child_care_credit_per_statute: bool = True
    apply_kentucky_pension_exclusion_schedule: bool = True
    step_kentucky_family_size_credit_by_poverty_share: bool = True
    limit_kentucky_itemized_deductions_to_mortgage_interest: bool = True
    double_kentucky_standard_deduction_for_joint_returns: bool = True
    file_kentucky_combined_returns_per_statute: bool = True
    deduct_massachusetts_employee_payroll_tax_per_spouse: bool = True
    count_capital_gains_in_dc_childless_eitc_investment_income: bool = True
    exclude_children_under_4_from_ny_empire_credit: bool = True
    pay_new_york_empire_child_credit_per_statute: bool = True
    round_new_york_empire_child_credit_phaseout_per_statute: bool = True
    limit_new_york_property_credit_rent_per_statute: bool = True
    round_new_york_recapture_ratio_per_statute: bool = True
    pay_new_york_household_credit_to_separate_returns: bool = True
    step_ohio_exemption_by_income: bool = True
    count_children_under_6_by_dep6: bool = True
    use_idaho_published_brackets: bool = True
    initialize_arizona_short_term_loss: bool = True
    use_delaware_published_tax_brackets: bool = True
    use_maine_published_phaseout_starts: bool = True
    use_maine_published_2024_sales_tax_credit_per_statute: bool = True
    deflate_maine_earned_income_credit_per_statute: bool = True
    limit_maine_child_care_credit_by_federal_tax_in_2021_per_statute: bool = True
    limit_maine_dependent_filer_standard_deduction_per_statute: bool = True
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
    count_colorado_credit_children_per_statute: bool = True
    split_virginia_age_deduction_between_spouses_per_statute: bool = True
    limit_virginia_2021_child_care_deduction_per_statute: bool = True
    add_back_other_itemized_in_california_minimum_tax_per_statute: bool = True
    use_indiana_elderly_credit_steps_per_statute: bool = True
    split_delaware_income_before_pension_exclusion_per_statute: bool = True
    deny_delaware_personal_credit_to_dependent_filers_per_statute: bool = True
    tax_kansas_other_nonproperty_income_per_statute: bool = True
    scale_kansas_zero_tax_income_by_taxpayers_per_statute: bool = True
    reconstruct_connecticut_minimum_tax_income_per_statute: bool = True
    exclude_transfers_from_connecticut_income_per_statute: bool = True
    use_published_minnesota_2025_aged_addition_per_statute: bool = True
    halve_minnesota_motor_fuels_credit_for_separate_returns_per_statute: bool = True
    use_published_minnesota_dependent_standard_deduction_per_statute: bool = True
    add_earnings_to_district_of_columbia_dependent_standard_deduction_per_statute: bool = True
    apply_illinois_earned_income_credit_age_test_per_statute: bool = True
    add_idaho_aged_standard_deduction_in_projected_years_per_statute: bool = True
    use_north_dakota_published_marriage_credit_limits_per_statute: bool = True
    use_arkansas_published_2025_rate_table_per_statute: bool = True
    apply_new_york_earned_income_credit_age_test_per_statute: bool = True
    use_new_york_agi_for_earned_income_credit_per_statute: bool = True
    exclude_other_itemized_from_missouri_state_tax_limit_per_statute: bool = True
    split_missouri_income_before_capital_gains_subtraction_per_statute: bool = True
    lift_maryland_childless_refundable_credit_limit_per_statute: bool = True
    apply_maryland_earned_income_credit_age_test_per_statute: bool = True
    use_maryland_published_2025_tax_table_per_statute: bool = True
    require_indiana_federal_credit_for_decoupled_credit_per_statute: bool = True
    use_california_published_2025_brackets_per_statute: bool = True
    use_california_2025_minimum_tax_exemption_per_statute: bool = True
    start_child_tax_credit_phaseout_at_statutory_threshold_for_dependent_filers: bool = True
    count_eitc_children_from_ages_per_statute: bool = True
    count_business_inputs_in_earned_income_before_1987_per_statute: bool = True
    exclude_nonproperty_income_from_maximum_tax_personal_service_income: bool = True
    start_minnesota_itemized_phaseout_without_status_multiplier_per_statute: bool = True
    add_back_minnesota_unemployment_exclusion_to_working_family_income_per_statute: bool = True
    apply_minnesota_pension_exclusion_through_1986_per_statute: bool = True
    exclude_utah_other_itemized_from_state_tax_limit_per_statute: bool = True
    deflate_wisconsin_exemption_count_per_statute: bool = True
    deflate_massachusetts_payroll_tax_deduction_per_statute: bool = True
    use_massachusetts_2020_part_c_rate_per_statute: bool = True
    use_massachusetts_2001_rent_cap_per_statute: bool = True
    halve_new_york_married_itemized_limit_for_separate_per_statute: bool = True
    compute_new_york_property_credit_per_statute: bool = True
    compute_new_york_earned_income_credit_base_per_statute: bool = True
    offset_massachusetts_dividends_only_by_losses_per_statute: bool = True
    count_maine_fairness_credit_children_before_deflation_per_statute: bool = True
    count_arkansas_other_itemized_deductions_per_statute: bool = True
    limit_kansas_child_care_credit_by_federal_tax_per_statute: bool = True
    round_connecticut_property_credit_steps_continuously_per_statute: bool = True
    cap_oregon_property_tax_deduction_per_statute: bool = True
    exempt_missouri_social_security_at_any_age_per_statute: bool = True
    deduct_alabama_federal_tax_components_per_statute: bool = True
    cap_michigan_senior_renter_credit_at_statute_amount: bool = True
    give_michigan_dependent_filers_exemption_per_statute: bool = True
    allow_georgia_retirement_exclusion_before_1989_per_statute: bool = True
    compute_utah_child_tax_credit_per_statute: bool = True
    limit_rhode_island_child_care_credit_by_federal_tax_per_statute: bool = True
    count_arkansas_s_corporation_income_once: bool = True
    exempt_arkansas_unemployment_from_2024_per_statute: bool = True
    omit_georgia_itemizer_credit_per_statute: bool = True
    exclude_massachusetts_bank_interest_from_2024_per_statute: bool = True
    cap_virginia_state_tax_deduction_per_statute: bool = True
    allow_virginia_standard_deduction_when_itemizing_federally_per_statute: bool = True
    cap_delaware_property_tax_deduction_per_statute: bool = True
    halve_minnesota_deduction_reduction_for_separate_returns_per_statute: bool = True
    compute_minnesota_property_tax_refund_per_statute: bool = True
    cap_new_york_state_tax_deduction_per_statute: bool = True
    compute_montana_tax_from_2024_per_statute: bool = True
    deflate_oklahoma_earned_income_credit_income_per_statute: bool = True
    reinflate_colorado_earned_income_credit_per_statute: bool = True
    cap_south_carolina_state_tax_addback_per_statute: bool = True
    limit_south_carolina_standard_deduction_addback_to_nonitemizers_per_statute: bool = True
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
    deduct_colorado_federal_tax_after_1977: bool = True
    pay_kansas_credits_per_statute: bool = True
    use_idaho_standard_deduction_2008_2009_per_statute: bool = True
    use_idaho_building_fund_limits_per_statute: bool = True
    use_rhode_island_published_brackets: bool = True
    test_arizona_family_credit_before_exemptions: bool = True
    compute_missouri_pension_exemption_per_statute: bool = True
    exclude_unemployment_in_district_of_columbia_every_year: bool = True
    split_district_of_columbia_joint_returns_per_statute: bool = True
    split_mississippi_joint_returns_per_statute: bool = True
    use_mississippi_federal_itemized_deductions_from_2021: bool = True
    count_idaho_child_credit_children_under_17: bool = True
    subtract_state_tax_in_georgia_itemized_deduction: bool = True
    pay_hawaii_credits_below_zero_tax: bool = True
    count_all_dependents_for_hawaii_child_care: bool = True
    limit_hawaii_state_tax_deduction_from_2011: bool = True
    tax_hawaii_gains_net_of_federal_exclusion: bool = True
    use_colorado_itemized_deductions_before_1987: bool = True
    phase_out_connecticut_exemption_per_statute: bool = True
    use_connecticut_personal_credit_table_per_statute: bool = True
    add_connecticut_recapture_per_statute: bool = True
    phase_out_connecticut_pension_deduction_per_statute: bool = True
    allow_connecticut_property_credit_to_all_from_2022: bool = True
    subtract_colorado_pensions_for_nonitemizers_before_2021: bool = True
    use_colorado_statutory_high_income_addback: bool = True
    credit_iowa_dependents_before_1998: bool = True
    add_back_iowa_self_employment_tax_twice: bool = True
    reduce_iowa_single_tax_before_2017: bool = True
    double_minnesota_separate_returns: bool = True
    read_minnesota_child_care_from_federal_credit: bool = True
    subtract_minnesota_elderly_and_charity_from_income: bool = True
    compute_minnesota_marriage_credit_earnings_per_statute: bool = True
    treat_minnesota_other_itemized_as_state_tax_2018_2019: bool = True
    count_unknown_ages_as_eligible_for_minnesota_childless_credit: bool = True
    reduce_minnesota_deductions_per_statute: bool = True
    compute_minnesota_child_care_credit_from_2021_per_statute: bool = True
    use_minnesota_2022_working_family_credit_per_statute: bool = True
    use_minnesota_published_brackets_from_2022: bool = True
    phase_out_minnesota_exemptions_per_statute: bool = True
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
    phase_out_oregon_federal_tax_limit_in_steps: bool = True
    use_oregon_standard_deduction_index_per_statute: bool = True
    apply_oregon_dependent_care_credit_per_statute: bool = True
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
    use_north_carolina_head_of_household_deduction_per_statute: bool = True
    read_north_carolina_child_deduction_per_statute: bool = True
    cap_north_carolina_property_tax_per_statute: bool = True
    add_back_north_dakota_state_tax_for_itemizers_only: bool = True
    add_nebraska_single_additional_tax_once: bool = True
    disallow_new_jersey_capital_losses: bool = True
    exempt_new_mexico_social_security_1984_1989: bool = True
    count_new_mexico_aged_once_in_rebate: bool = True
    set_new_mexico_capital_gains_share_per_statute: bool = True
    test_new_mexico_aged_deduction_on_state_agi: bool = True
    compute_new_mexico_capital_gains_deduction_per_statute: bool = True
    compute_delaware_before_1996_per_statute: bool = True
    split_montana_joint_returns_per_statute: bool = True
    compute_montana_retirement_exclusion_per_statute: bool = True
    read_montana_elderly_credit_multiplier_per_statute: bool = True
    compute_maryland_earned_income_credit_per_statute: bool = True
    split_maryland_joint_income_per_statute: bool = True
    pay_maryland_poverty_level_credit_per_statute: bool = True
    scale_maryland_child_care_credit_per_statute: bool = True
    step_maryland_exemption_phaseout_per_statute: bool = True
    compute_maryland_itemized_deduction_from_2018_per_statute: bool = True
    compute_oklahoma_pension_exclusion_per_statute: bool = True
    compute_oklahoma_sales_tax_credit_income_per_statute: bool = True
    read_oklahoma_federal_credit_law_year_per_statute: bool = True
    limit_oklahoma_2021_child_credits_to_tax: bool = True
    compute_oklahoma_itemized_deduction_from_2022_per_statute: bool = True
    test_oklahoma_earned_income_credit_age_per_statute: bool = True
    pay_wisconsin_child_care_credit_per_statute: bool = True
    test_indiana_childless_credit_ages_per_statute: bool = True
    apply_indiana_federal_credit_floor_from_2011: bool = True
    use_indiana_decoupled_credit_schedule_in_2021: bool = True
    compute_california_2021_earned_income_credit_per_statute: bool = True
    keep_california_salt_addback_cents_per_statute: bool = True
    exclude_utah_state_income_tax_from_taxpayer_credit_before_2018: bool = True
    pay_utah_retirement_credit_per_statute: bool = True
    use_utah_2022_credit_threshold_per_statute: bool = True
    start_utah_dependent_filer_credit_phaseout_per_statute: bool = True
    use_wisconsin_published_2022_separate_brackets_per_statute: bool = True
    use_utah_statute_exempt_taxpayer_floor_per_statute: bool = True
    deny_new_york_2021_earned_income_credit_to_separate_returns: bool = True
    pick_new_york_worksheet_by_filing_status: bool = True
    exclude_arkansas_pensions_per_taxpayer: bool = True
    split_arkansas_joint_income_by_higher_earner: bool = True
    add_kansas_aged_and_dependent_standard_deduction_per_statute: bool = True
    use_kansas_2021_standard_deduction_per_statute: bool = True
    credit_maine_1988_standard_deduction_and_exemptions: bool = True
    make_maine_elderly_credit_refundable: bool = True
    halve_west_virginia_separate_brackets_per_statute: bool = True
    limit_rhode_island_retirement_exclusion_to_aged_per_statute: bool = True
    count_arizona_dependent_credit_children_under_17_per_statute: bool = True
    take_arizona_larger_of_property_and_renter_credit: bool = True
    read_west_virginia_family_credit_limits_per_statute: bool = True
    step_west_virginia_family_credit_phaseout_per_statute: bool = True
    subtract_west_virginia_social_security_per_statute: bool = True
    leave_out_west_virginia_child_care_credit_from_2024: bool = True
    require_earned_income_for_ohio_joint_credit: bool = True
    start_ohio_business_deduction_per_statute: bool = True
    test_ohio_credits_before_business_deduction_per_statute: bool = True
    count_ohio_joint_credit_unemployment_as_taxable_per_statute: bool = True
    compute_north_dakota_marriage_credit_earnings_per_statute: bool = True
    tax_south_carolina_capital_gains_1985_1989: bool = True
    pay_district_of_columbia_property_credit_2014_2018: bool = True
    use_wisconsin_separate_standard_deduction_from_2023: bool = True
    step_missouri_property_credit_per_statute: bool = True
    pay_wisconsin_homestead_credit_from_2023: bool = True
    deduct_district_of_columbia_other_itemized_with_state_taxes: bool = True
    start_wisconsin_head_of_household_third_tier_per_statute: bool = True
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
    pay_oregon_2019_kicker: bool = True
    tax_oregon_dependent_filers_per_statute: bool = True
    pay_oregon_kids_credit_per_statute: bool = True
    omit_oregon_2021_kicker: bool = True
    apply_pennsylvania_forgiveness_rules: bool = True
    use_net_long_term_gain_in_rhode_island: bool = True
    halve_rhode_island_separate_standard_deduction: bool = True
    subtract_rhode_island_tax_once_in_minimum_tax: bool = True
    use_rhode_island_deduction_phaseout_steps_only: bool = True
    deduct_south_carolina_federal_tax_under_1980_law: bool = True
    allow_virginia_child_care_deduction_2003: bool = True
    compute_virginia_spouse_adjustment_per_statute: bool = True
    zero_virginia_tax_below_filing_threshold: bool = True
    read_virginia_low_income_limits_per_statute: bool = True
    refund_virginia_earned_income_credit_at_2025_rate: bool = True
    use_north_dakota_married_separate_brackets_from_2023: bool = True
    use_published_north_dakota_brackets_from_2024: bool = True
    use_published_kansas_exemption_from_2024: bool = True
    allow_unreported_ages_in_dc_childless_credit_from_2024: bool = True
    cap_district_of_columbia_state_tax_deduction_from_2024: bool = True
    subtract_minnesota_social_security_without_age_from_2024: bool = True
    limit_minnesota_itemized_deduction_from_2024_per_statute: bool = True
    charge_minnesota_alternative_minimum_tax_from_2024: bool = True
    deduct_idaho_itemized_from_2018_per_statute: bool = True
    limit_idaho_dependent_standard_deduction_per_statute: bool = True
    add_back_michigan_self_employment_tax_from_2021_per_statute: bool = True
    pay_maine_childless_credit_rate_to_all_ages_per_statute: bool = True
    take_utah_small_tax_credit_once: bool = True
    start_oklahoma_joint_top_bracket_per_statute: bool = True
    take_maine_property_fairness_rent_at_statutory_share: bool = True
    deduct_virginia_age_deduction_per_statute: bool = True
    limit_virginia_itemized_deductions_without_other_items: bool = True
    scale_virginia_itemized_limit_threshold: bool = True
    omit_vermont_student_credit: bool = True
    floor_wisconsin_capital_gain_exclusion: bool = True
    start_wisconsin_standard_deduction_tiers_at_their_range: bool = True
    start_wisconsin_separate_standard_deduction_tier_at_its_range: bool = True
    limit_wisconsin_capital_loss_to_statutory_amount: bool = True
    limit_wisconsin_dependent_standard_deduction_from_2023: bool = True
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
        "start_wisconsin_standard_deduction_tiers_at_their_range",
        "disallow_new_jersey_capital_losses",
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
        "floor_montana_social_security_worksheet",
        "limit_arizona_property_credit_per_statute",
        "count_capital_gains_in_dc_childless_eitc_investment_income",
        "include_self_employment_in_household_income",
        "tax_wisconsin_1986_separate_returns",
        "complete_west_virginia_1984_head_of_household_table",
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
