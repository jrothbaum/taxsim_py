"""One row per statutory correction: an input record, the column it changes,
and that column's value in each calculation mode. Run by
tests/test_statutory_corrections.py. The taxsim values match taxsim2024.exe
unless the row says otherwise."""

from typing import Any


def _case(
    correction: str,
    description: str,
    column: str,
    taxsim: float,
    statutory: float,
    **inputs: Any,
) -> dict[str, Any]:
    return {
        "correction": correction,
        "description": description,
        "column": column,
        "taxsim": taxsim,
        "statutory": statutory,
        "inputs": {"mstat": 1, "state": 0, **inputs},
    }


CASES = [
    # Provisional income adds tax-exempt interest, not nontaxable transfers.
    # Statutory: 0.5 * ($20,000 of pensions + half of $20,000 of benefits - $25,000).
    _case(
        "FED-INCOME-003", "single retiree with transfers", "taxable_social_security",
        9_600.0, 2_500.0, year=2019, page=70, gssi=20_000, pensions=20_000, transfers=10_000,
    ),
    # The spouse's business income is taxed in the spouse's own wage-base room:
    # 12.4% * 92.35% * $50,000 more OASDI.
    _case(
        "FED-PAYROLL-005", "spouse business income with primary wages over the base", "fica",
        21_878.68, 27_604.38, year=2019, mstat=2, pwages=140_000, sbusinc=50_000,
    ),
    # AMT is owed before 2000.
    _case(
        "FED-INCOME-004", "1995 itemizer owing AMT", "fiitax",
        59_231.74, 66_500.00, year=1995, pwages=250_000, proptax=60_000,
    ),
    # Nonrefundable credits apply before 1998: 20% of $2,400 of child care.
    _case(
        "FED-INCOME-005", "1995 child care credit", "ccc",
        0.0, 480.0, year=1995, depx=1, dep13=1, dep17=1, dep18=1, pwages=30_000, childcare=2_400,
    ),
    # The 1995 elderly credit ($637.50) limited to tax.
    _case(
        "FED-INCOME-005", "1995 elderly credit", "elderly_credit",
        0.0, 247.50, year=1995, page=70, pensions=9_000,
    ),
    # A joint return's AGI threshold is $10,000 with one spouse 65 or older:
    # 15% * ($5,000 - 0.5 * ($12,000 - $10,000)).
    _case(
        "FED-INCOME-006", "couple with one spouse 65", "federal_elder",
        412.50, 600.0, year=2019, mstat=2, page=70, sage=60, pensions=12_000,
    ),
    # The itemized-deduction limit (3% of AGI over $100,000) is not added to
    # AMT income from 1991.
    _case(
        "FED-INCOME-007", "1991 itemizer AMT income", "amt_income",
        151_500.0, 150_000.0, year=1991, pwages=150_000, proptax=20_000,
    ),
    # The young-filer AMT exemption cap applies only to dependents. The
    # executable ignores ages, so its taxsim value is 0 (the source gives 397.50).
    _case(
        "FED-INCOME-008", "12-year-old filer with dividends, not a dependent", "amt",
        397.50, 0.0, year=2010, page=12, dividends=60_000,
    ),
    # 1998-2000: three children make the unused $352.50 of credit refundable,
    # within employee payroll tax ($1,912.50) less the EITC ($1,175.08).
    _case(
        "FED-INCOME-009", "1999 head of household with three children", "actc",
        0.0, 352.50, year=1999, depx=3, dep13=3, dep17=3, dep18=3, pwages=25_000,
    ),
    # Business income is income before 1987.
    _case(
        "FED-INCOME-010", "1985 business income", "agi",
        30_000.0, 50_000.0, year=1985, pwages=30_000, pbusinc=20_000,
    ),
    _case(
        "FED-INCOME-010", "1975 business income", "agi",
        30_000.0, 50_000.0, year=1975, pwages=30_000, pbusinc=20_000,
    ),
    # The 1979 dividend exclusion ($200 joint).
    _case(
        "FED-INCOME-011", "1979 couple with dividends", "agi",
        35_000.0, 34_800.0, year=1979, mstat=2, pwages=30_000, dividends=5_000,
    ),
    # The elderly credit, 15% * ($5,000 - 0.5 * $500), limited to $438.80 of tax.
    _case(
        "FED-INCOME-012", "1985 aged single with pensions", "fiitax",
        438.80, 0.0, year=1985, page=70, pensions=8_000,
    ),
    # Housing interest reduces the 1983-1986 minimum-tax base.
    _case(
        "FED-INCOME-013", "1985 itemizer with gains and mortgage interest", "pre1987_alminy",
        500_000.0, 440_000.0, year=1985, mstat=2, pwages=200_000, ltcg=300_000, mortgage=60_000, proptax=5_000,
    ),
    # 1977-1978 preference income is the excluded half of the gain, not wages.
    _case(
        "FED-INCOME-014", "1977 itemizer with gains", "pre1987_pref",
        170_000.0, 20_000.0, year=1977, mstat=2, pwages=150_000, ltcg=40_000, proptax=40_000, mortgage=20_000,
    ),
    # The analytic federal rate that piggyback states report keeps the 1981
    # rate reduction (Nebraska: 22% of 30% * 0.9875) and counts the child
    # tax credit phaseout as 5 points (Vermont: 25% of 36%).
    _case(
        "FED-RATE-001", "Nebraska 1981 bracket rate", "state_marginal_rate_percent",
        6.60, 6.5175, year=1981, state=28, pwages=30_000,
    ),
    _case(
        "FED-RATE-001", "Vermont 1999 couple in the child credit phaseout", "state_marginal_rate_percent",
        7.7625, 9.0, year=1999, state=46, mstat=2, depx=2, dep13=2, dep17=2, dep18=2, pwages=125_000,
    ),
    # Before 1987 state child care credits read the federal credit, which
    # TAXSIM's early federal routine never fills (DC: 30% of $480).
    _case(
        "STATE-CCC-001", "DC 1985 child care", "dc_chcr",
        0.0, 144.0, year=1985, state=9, depx=1, dep13=1, pwages=20_000, childcare=2_000,
    ),
    # The 2007 married split uses 2007 rates, not 2006's.
    _case(
        "DC-004", "DC 2007 two-earner couple", "siitax",
        3_315.0, 2_870.0, year=2007, state=9, mstat=2, pwages=30_000, swages=30_000,
    ),
    # The dependent-filer low-income credit uses the year's rates: 4% of
    # $1,300 in 2010, not the 2000 table's 5%.
    _case(
        "DC-005", "DC 2010 dependent filer", "dc_ycr",
        65.0, 52.0, year=2010, state=9, mstat=8, pwages=5_000,
    ),
    # Iowa 1980: 5% of the $400 federal credit. New York 1985: 20% of $480.
    _case(
        "STATE-CCC-001", "Iowa 1980 child care", "state_child_and_dependent_care_credit",
        0.0, 20.0, year=1980, state=16, depx=1, dep13=1, dep17=1, dep18=1, pwages=15_000, childcare=2_000,
    ),
    _case(
        "STATE-CCC-001", "New York 1985 child care", "state_child_and_dependent_care_credit",
        0.0, 96.0, year=1985, state=33, depx=1, dep13=1, dep17=1, dep18=1, pwages=20_000, childcare=2_000,
    ),
    _case(
        "STATE-CCC-001", "Minnesota 1985 child care", "state_child_and_dependent_care_credit",
        0.0, 456.0, year=1985, state=24, depx=1, dep13=1, dep17=1, dep18=1, pwages=12_000, childcare=2_000,
    ),
    # Iowa's 2010 joint caps by number of children ($38,665 for one child),
    # and no caps outside 2010: 7% and 15% of the federal credit.
    _case(
        "IA-001", "Iowa 2010 couple with one child", "ia_earncr",
        0.0, 213.50, year=2010, state=16, mstat=2, depx=1, dep13=1, dep17=1, dep18=1, pwages=20_000,
    ),
    _case(
        "IA-001", "Iowa 2019 single parent above the 2010 cap", "ia_earncr",
        0.0, 74.19, year=2019, state=16, depx=1, dep13=1, dep17=1, dep18=1, pwages=38_000,
    ),
    # Illinois 2000-2002: the EITC is limited to tax after the property tax
    # credit, subtracted once ($165 left, not $90).
    _case(
        "IL-002", "Illinois 2001 parent with property tax", "il_eitc",
        90.0, 165.0, year=2001, state=14, depx=2, dep13=2, dep17=2, dep18=2, pwages=14_000, proptax=1_500,
    ),
    # Kentucky suspends the itemized limitation 2010-2012 (the 2011 Schedule
    # A has none).
    _case(
        "KY-002", "Kentucky 2011 high-income itemizer", "state_itemized_deductions",
        43_086.50, 50_000.0, year=2011, state=18, mstat=2, pwages=400_000, proptax=20_000, mortgage=30_000,
    ),
    # Massachusetts: the child care deduction ($4,800 per child) through 2020,
    # and no exemption carryover to interest on a separate return.
    _case(
        "MA-002", "Massachusetts 2015 parent with child care", "siitax",
        2_163.0, 2_039.40, year=2015, state=22, depx=2, dep13=2, dep17=2, dep18=2, pwages=60_000, childcare=10_000,
    ),
    _case(
        "MA-003", "Massachusetts 1986 separate return with interest", "siitax",
        865.70, 1_000.0, year=1986, state=22, mstat=6, pwages=1_000, intrec=10_000,
    ),
    # Maryland 2021 single minimum standard deduction ($1,550; Resident
    # Booklet Instruction 16), in both modes. The executable gives $2,300.
    _case(
        "MD-BOTH-001", "Maryland 2021 dependent filer", "state_standard_deduction",
        1_550.0, 1_550.0, year=2021, state=21, mstat=8, pwages=5_000,
    ),
    # Michigan 2013-2022 retirement tiers by birth year: born before 1946,
    # pensions up to the cap; born 1946-1952 and under 67, at most $20,000
    # of pensions.
    _case(
        "MI-001", "Michigan 2017 age 80 with $30,000 of pensions", "state_taxable_income",
        46_000.0, 36_000.0, year=2017, state=23, page=80, pwages=40_000, pensions=30_000,
    ),
    _case(
        "MI-001", "Michigan 2017 age 66 with $10,000 of pensions", "state_taxable_income",
        26_000.0, 36_000.0, year=2017, state=23, page=66, pwages=40_000, pensions=10_000,
    ),
    # Minnesota: the 1978-1984 homemaker credit goes to filers who are not
    # dependents; the Working Family Credit's 1998 childless phase-in (1.2%
    # of $4,000), 2012 joint thresholds, and 2014-2021 tables by children.
    _case(
        "MN-002", "Minnesota 1980 single parent homemaker", "siitax",
        1_278.12, 1_228.12, year=1980, state=24, depx=1, dep13=1, dep17=1, dep18=1, intrec=20_000,
    ),
    _case(
        "MN-003", "Minnesota 1998 childless worker", "mn_work",
        70.20, 48.0, year=1998, state=24, pwages=4_000,
    ),
    _case(
        "MN-004", "Minnesota 2012 couple with one child", "mn_work",
        680.26, 949.0, year=2012, state=24, mstat=2, depx=1, dep13=1, dep17=1, dep18=1, pwages=25_000,
    ),
    _case(
        "MN-005", "Minnesota 2019 single parent of two", "mn_work",
        1_921.85, 2_156.0, year=2019, state=24, depx=2, dep13=2, dep17=2, dep18=2, pwages=25_000,
    ),
    _case(
        "MN-005", "Minnesota 2019 couple with three children", "mn_work",
        2_483.20, 2_500.0, year=2019, state=24, mstat=2, depx=3, dep13=3, dep17=3, dep18=3, pwages=33_000,
    ),
    # Minnesota 2017 on: above the threshold the credit is the lesser of the
    # federal-based credit ($400) and $600 less 5% of the excess.
    _case(
        "MN-006", "Minnesota 2018 parent above the threshold", "state_child_and_dependent_care_credit",
        199.50, 399.50, year=2018, state=24, depx=1, dep13=1, dep17=1, dep18=1, pwages=55_000, childcare=2_000,
    ),
    # Minnesota: a household with rent and property tax gets both refunds.
    _case(
        "MN-007", "Minnesota 2015 renter who also paid property tax", "mn_pcred",
        603.21, 1_844.89, year=2015, state=24, pwages=20_000, rentpaid=6_000, proptax=2_000,
    ),
    # Missouri: before 1991 no Medicare tax on wages above the Social
    # Security base (1.45% of $20,400 less in the payroll deduction).
    _case(
        "STATE-SOCSEC-001", "Missouri 1985 itemizing couple", "state_itemized_deductions",
        11_792.60, 11_496.80, year=1985, state=26, mstat=2, pwages=60_000, swages=10_000, proptax=8_000,
    ),
    # Missouri property credit: tax paid up to the maximum, less the income
    # percentage (1/16 of 1% in the first step above the base).
    _case(
        "MO-001", "Missouri 2010 aged owner with $300 of property tax", "state_property_tax_credit",
        1_100.0, 300.0, year=2010, state=26, page=70, pensions=10_000, proptax=300,
    ),
    _case(
        "MO-001", "Missouri 2010 aged owner just above the base", "state_property_tax_credit",
        0.0, 291.0, year=2010, state=26, page=70, pensions=14_400, proptax=300,
    ),
    # Mississippi 1977-1978: excess charity is floored at 0, so 20% of AGI
    # is not added to itemized deductions.
    _case(
        "MS-001", "Mississippi 1977 single", "siitax",
        394.26, 540.0, year=1977, state=25, pwages=20_000,
    ),
    # Montana: subtract only the federally taxed unemployment ($5,600 of
    # $8,000 in 2009); a separate filer's capital loss once; no itemized
    # limit 2010-2012; the Social Security worksheet floored at 0.
    _case(
        "MT-001", "Montana 2009 single with unemployment", "state_adjusted_gross_income",
        17_600.0, 20_000.0, year=2009, state=27, pwages=20_000, ui=8_000,
    ),
    _case(
        "MT-002", "Montana 2015 separate filer with a capital loss", "state_adjusted_gross_income",
        37_000.0, 38_500.0, year=2015, state=27, mstat=6, pwages=40_000, ltcg=-5_000,
    ),
    _case(
        "MT-003", "Montana 2011 high-income itemizer", "state_itemized_deductions",
        53_086.50, 60_000.0, year=2011, state=27, mstat=2, pwages=400_000, proptax=20_000, mortgage=30_000,
    ),
    _case(
        "MT-004", "Montana 2015 retiree below the Social Security base", "state_adjusted_gross_income",
        -7_472.40, 18.40, year=2015, state=27, page=70, gssi=20_000, pensions=4_000,
    ),
    # North Carolina: the joint child care credit counts both children (7%
    # of $4,400) and comes off one spouse's tax; 1989-1990 joint returns use
    # the married table without income splitting.
    _case(
        "NC-001", "North Carolina 1985 couple with two children", "nc_chcr",
        154.0, 308.0, year=1985, state=34, mstat=2, depx=2, dep13=2, dep17=2, dep18=2,
        pwages=30_000, swages=25_000, childcare=5_000,
    ),
    _case(
        "NC-002", "North Carolina 1985 couple with similar earnings", "siitax",
        1_653.0, 1_793.0, year=1985, state=34, mstat=2, depx=1, dep13=1, dep17=1, dep18=1,
        pwages=20_000, swages=19_000, childcare=2_000,
    ),
    _case(
        "NC-003", "North Carolina 1989 one-earner couple", "siitax",
        1_860.0, 1_952.50, year=1989, state=34, mstat=2, pwages=40_000,
    ),
    # North Dakota 1987-2000 long form: state income tax is added back only
    # when it was deducted federally.
    _case(
        "ND-001", "North Dakota 1995 nonitemizer", "nd_taxinc",
        20_527.65, 20_027.50, year=1995, state=35, pwages=30_000,
    ),
    # Nebraska 1993-2017: a single filer's additional tax is added once.
    _case(
        "NE-001", "Nebraska 1995 single with $200,000", "siitax",
        13_661.60, 13_351.98, year=1995, state=28, pwages=200_000,
    ),
    # New Jersey allows no capital-loss deduction.
    _case(
        "NJ-002", "New Jersey 2015 single with a $12,000 loss", "nj_agi",
        31_000.0, 40_000.0, year=2015, state=31, pwages=40_000, ltcg=-12_000,
    ),
    # New Mexico: 1984-1989 benefits are subtracted, not added again; the
    # low-income rebate counts a taxpayer 65 or older once.
    _case(
        "NM-001", "New Mexico 1986 retiree", "state_adjusted_gross_income",
        31_000.0, 25_000.0, year=1986, state=32, page=70, pensions=25_000, gssi=12_000,
    ),
    _case(
        "NM-002", "New Mexico 1995 low-income retiree", "siitax",
        -30.0, -20.0, year=1995, state=32, page=70, pensions=5_000, gssi=6_000,
    ),
    # New York: before 1987 a head of household uses the single standard
    # deduction; the 1986 maximum tax table's sixth row; the 1987 tax on
    # unearned income (3% of $70,000 times 70% phased in); no minimum tax
    # without preference items; worksheet 6 phases in from its own bound.
    _case(
        "NY-002", "New York 1985 head of household", "siitax",
        1_132.11, 894.61, year=1985, state=33, depx=1, dep13=1, dep17=1, dep18=1, pwages=20_000,
    ),
    _case(
        "NY-003", "New York 1986 one-earner couple", "siitax",
        4_825.0, 4_678.50, year=1986, state=33, mstat=2, pwages=60_000,
    ),
    _case(
        "NY-004", "New York 1987 couple with dividends and interest", "siitax",
        13_130.0, 14_600.0, year=1987, state=33, mstat=2, pwages=100_000, dividends=50_000, intrec=20_000,
    ),
    _case(
        "NY-005", "New York 2021 low-income head of household", "siitax",
        -5_836.51, -5_889.90, year=2021, state=33, depx=3, dep13=3, dep17=3, dep18=3,
        pwages=16_000, childcare=6_000,
    ),
    _case(
        "NY-006", "New York 2015 single at $230,000", "siitax",
        15_213.85, 14_964.04, year=2015, state=33, pwages=230_000,
    ),
    # Ohio: before 1989 only joint returns get the joint filing credit; in
    # 1983-1988 the exemption credit is subtracted once.
    _case(
        "OH-001", "Ohio 1980 single with self-employment income", "siitax",
        836.48, 880.50, year=1980, state=36, pwages=30_000, psemp=10_000,
    ),
    _case(
        "OH-002", "Ohio 1988 single with unemployment", "siitax",
        733.39, 737.79, year=1988, state=36, pwages=20_000, ui=8_000,
    ),
    # Oregon: 1981-1984 Oregon's taxable unemployment replaces the federal
    # amount; the 2015 and 2017 surplus credits are paid.
    _case(
        "OR-004", "Oregon 1982 single with unemployment", "or_agi",
        32_000.0, 24_000.0, year=1982, state=38, pwages=20_000, ui=8_000,
    ),
    _case(
        "OR-005", "Oregon 2017 single", "siitax",
        3_361.79, 3_162.50, year=2017, state=38, pwages=50_000,
    ),
    # Pennsylvania tax forgiveness: eligibility income includes unemployment
    # compensation, and forgiveness falls in 10% steps.
    _case(
        "PA-001", "Pennsylvania 2020 single with unemployment", "siitax",
        147.36, 245.60, year=2020, state=39, pwages=8_000, ui=5_000,
    ),
    _case(
        "PA-001", "Pennsylvania 2020 single $100 over the allowance", "siitax",
        8.10, 20.26, year=2020, state=39, pwages=6_600,
    ),
    # Rhode Island: the 2003-2009 gains rates apply to net long-term gain
    # only; separate returns get half the married standard deduction; the
    # minimum tax subtracts the regular tax once; the 2011-2021 deduction
    # phaseout uses its 20% steps only.
    _case(
        "RI-001", "Rhode Island 2005 short-term gain", "siitax",
        3_660.40, 4_060.40, year=2005, state=40, pwages=60_000, stcg=20_000,
    ),
    _case(
        "RI-002", "Rhode Island 2005 separate return", "siitax",
        2_588.32, 2_878.82, year=2005, state=40, mstat=6, pwages=60_000,
    ),
    _case(
        "RI-003", "Rhode Island 2006 itemizer owing minimum tax", "siitax",
        67_630.26, 69_125.0, year=2006, state=40, pwages=1_000_000, proptax=200_000,
    ),
    _case(
        "RI-004", "Rhode Island 2011 single at $180,000", "state_standard_deduction",
        4_410.0, 4_500.0, year=2011, state=40, pwages=180_000,
    ),
    # South Carolina 1981-1984: the federal tax deduction uses 1980-law tax.
    # South Carolina (2018 on) and Virginia (2000 on) give each spouse their
    # own business income.
    _case(
        "SC-001", "South Carolina 1983 head of household", "siitax",
        220.20, 215.0, year=1983, state=41, depx=1, dep13=1, dep17=1, dep18=1, pwages=12_000, childcare=3_000,
    ),
    _case(
        "FED-PAYROLL-005", "South Carolina 2019 couple with spouse business income", "siitax",
        2_101.74, 1_928.83, year=2019, state=41, mstat=2, pwages=40_000, sbusinc=30_000,
    ),
    _case(
        "FED-PAYROLL-005", "Virginia 2010 couple with spouse business income", "siitax",
        3_193.68, 2_936.18, year=2010, state=47, mstat=2, pwages=40_000, sbusinc=30_000,
    ),
    # Virginia: the 2003 child care deduction (the $3,000 federal limit);
    # the 2021 itemized limit uses the scaled threshold throughout.
    _case(
        "VA-001", "Virginia 2003 parent with child care", "siitax",
        1_203.0, 1_030.50, year=2003, state=47, depx=1, dep13=1, dep17=1, dep18=1, pwages=30_000, childcare=3_000,
    ),
    _case(
        "VA-002", "Virginia 2021 itemizer at $340,000", "siitax",
        16_924.92, 16_835.39, year=2021, state=47, pwages=340_000, proptax=15_000, mortgage=20_000,
    ),
    # Vermont 1977-1988: no student credit (there is no student input).
    _case(
        "VT-001", "Vermont 1985 single", "siitax",
        746.52, 756.52, year=1985, state=46, pwages=20_000,
    ),
    # Wisconsin: indexed 2015-2018 joint brackets; a 1986 separate-return
    # schedule; the 1983 homestead credit above $10 (80%); the renter school
    # property credit on rent; standard-deduction tiers from their own
    # start; no negative gain exclusion; 1989-1990 homestead reduction on net
    # income; mortgage interest with cents; a halved separate AMT exemption.
    _case(
        "WI-001", "Wisconsin 2016 couple", "siitax",
        2_568.84, 2_561.38, year=2016, state=50, mstat=2, pwages=60_000,
    ),
    _case(
        "WI-002", "Wisconsin 1986 separate return", "siitax",
        0.0, 1_197.10, year=1986, state=50, mstat=6, pwages=20_000,
    ),
    _case(
        "WI-003", "Wisconsin 1983 homeowner", "siitax",
        129.82, -566.66, year=1983, state=50, pwages=8_000, proptax=1_000,
    ),
    _case(
        "WI-004", "Wisconsin 2005 renter", "siitax",
        304.24, 160.19, year=2005, state=50, pwages=20_000, rentpaid=6_000,
    ),
    _case(
        "WI-005", "Wisconsin 2005 head of household at $35,000", "siitax",
        1_821.40, 1_644.50, year=2005, state=50, depx=1, dep13=1, dep17=1, dep18=1, pwages=35_000,
    ),
    _case(
        "WI-005", "Wisconsin 1995 separate return at $5,200", "siitax",
        50.21, 52.02, year=1995, state=50, mstat=6, pwages=5_200,
    ),
    _case(
        "WI-006", "Wisconsin 1990 long-term loss and short-term gain", "siitax",
        2_410.10, 2_348.01, year=1990, state=50, pwages=30_000, stcg=10_000, ltcg=-2_000,
    ),
    _case(
        "WI-007", "Wisconsin 1990 homeowner with three dependents", "siitax",
        -439.33, -520.43, year=1990, state=50, depx=3, dep13=3, dep17=3, dep18=3, pwages=15_000, proptax=1_500,
    ),
    _case(
        "WI-008", "Wisconsin 2010 separate return with gains", "siitax",
        2_729.18, 3_412.50, year=2010, state=50, mstat=6, pwages=10_000, ltcg=50_000,
    ),
    # West Virginia: the 1984-1986 head-of-household table's 13% row; the
    # disability deduction for filers under 65 with Social Security; no state
    # income tax in 1977-1986 itemized deductions.
    _case(
        "WV-001", "West Virginia 1985 head of household at $90,000", "siitax",
        7_878.84, 8_957.0, year=1985, state=49, depx=1, dep13=1, dep17=1, dep18=1, pwages=90_000,
    ),
    _case(
        "WV-002", "West Virginia 1990 disabled filer under 65", "siitax",
        670.0, 350.0, year=1990, state=49, page=55, gssi=15_000, pensions=20_000,
    ),
    _case(
        "WV-003", "West Virginia 1982 itemizer", "siitax",
        1_204.09, 1_282.0, year=1982, state=49, pwages=40_000, proptax=3_000, mortgage=8_000,
    ),
    # Household income has no leftover $1 from TAXSIM's input reader, and the
    # state worksheet comes from the base calculation, not the marginal-rate
    # run.
    _case(
        "STATE-HHY-002", "Michigan 2015 household income", "state_household_income",
        20_001.0, 20_000.0, year=2015, state=23, pwages=20_000, proptax=2_000,
    ),
    # Ohio 1983-1988: both methods get the joint filing credit and the lower
    # tax applies.
    _case(
        "OH-002", "Ohio 1988 two-earner couple", "siitax",
        1_199.26, 1_020.85, year=1988, state=36, mstat=2, pwages=20_000, swages=20_000,
    ),
]
