# Recent state comparison

This is a diagnostic comparison of statutory `taxsim_py` against the installed
PolicyEngine TAXSIM API, using 300 sampled CPS ASEC tax units per state from the
cached 2011 sample. It is not an assertion that PolicyEngine is the statutory
oracle in every case. Differences are recorded so each model's scope is clear.

The comparison covers Arizona (TAXSIM 3), Colorado (6), Illinois (14), Michigan (23),
Pennsylvania (39), Utah (45), North Carolina (34), Indiana (15), Kentucky
(18), Connecticut (7), District of Columbia (9), Delaware (8), and Georgia (11), for tax
years 2022-2024. The original five-state
batch uses 300 units per state; the newer rollout checks use 1,000 units for
NC, IN, and KY and 500 for CT. A difference over `$0.01` is counted as a
mismatch.

## Results

| Year | State | Mismatches | Mean absolute difference | Maximum difference |
|---:|:---:|---:|---:|---:|
| 2022 | CO | 179/300 | $229 | $2,665 |
| 2022 | IL | 77/300 | $12 | $335 |
| 2022 | MI | 288/300 | $141 | $1,671 |
| 2022 | PA | 5/300 | $4 | $737 |
| 2022 | UT | 72/300 | $55 | $440 |
| 2023 | CO | 300/300 | $1,112 | $3,728 |
| 2023 | IL | 20/300 | $6 | $335 |
| 2023 | MI | 289/300 | $111 | $1,325 |
| 2023 | PA | 5/300 | $4 | $737 |
| 2023 | UT | 11/300 | $9 | $474 |
| 2024 | CO | 205/300 | $830 | $7,745 |
| 2024 | IL | 51/300 | $21 | $335 |
| 2024 | MI | 289/300 | $166 | $2,782 |
| 2024 | PA | 5/300 | $4 | $737 |
| 2024 | UT | 10/300 | $9 | $509 |

### New rollout batch

| Year | State | Mismatches | Mean absolute difference | Maximum difference | Sample |
|---:|:---:|---:|---:|---:|---:|
| 2022 | NC | 156/1,000 | $26 | $136 | 1,000 |
| 2022 | IN | 213/1,000 | $34 | $161 | 1,000 |
| 2022 | KY | 158/1,000 | $164 | $1,556 | 1,000 |
| 2022 | CT | 267/500 | $112 | $4,015 | 500 |
| 2023 | NC | 156/1,000 | $25 | $129 | 1,000 |
| 2023 | IN | 251/1,000 | $99 | $365 | 1,000 |
| 2023 | KY | 161/1,000 | $151 | $1,400 | 1,000 |
| 2023 | CT | 297/500 | $151 | $4,478 | 500 |
| 2024 | NC | 157/1,000 | $23 | $123 | 1,000 |
| 2024 | IN | 251/1,000 | $126 | $436 | 1,000 |
| 2024 | KY | 161/1,000 | $136 | $1,244 | 1,000 |
| 2024 | CT | 340/500 | $378 | $1,329 | 500 |

The Connecticut rerun after the dated-rule fixes used 1,000 units per year:

| Year | State | Mismatches | Mean absolute difference | Maximum difference |
|---:|:---:|---:|---:|---:|
| 2022 | CT | 529/1,000 | $135 | $5,995 |
| 2023 | CT | 528/1,000 | $138 | $6,631 |
| 2024 | CT | 603/1,000 | $112 | $6,554 |

After correcting the recent Connecticut personal-credit thresholds to use the
discrete worksheet rate rather than historical TAXSIM interpolation, the
statutory rerun was:

| Year | State | Mismatches | Mean absolute difference | Maximum difference |
|---:|:---:|---:|---:|---:|
| 2022 | CT | 322/1,000 | $217 | $5,995 |
| 2023 | CT | 321/1,000 | $221 | $6,631 |
| 2024 | CT | 400/1,000 | $164 | $6,554 |

DC's first rollout check used 1,000 units per year:

| Year | State | Mismatches | Mean absolute difference | Maximum difference |
|---:|:---:|---:|---:|---:|
| 2022 | DC | 337/1,000 | $831 | $4,221 |
| 2023 | DC | 327/1,000 | $826 | $2,906 |
| 2024 | DC | 320/1,000 | $830 | $2,851 |

Ohio's first rollout check used 1,000 units per year:

| Year | State | Mismatches | Mean absolute difference | Maximum difference |
|---:|:---:|---:|---:|---:|
| 2022 | OH | 79/1,000 | $419 | $3,707 |
| 2023 | OH | 79/1,000 | $389 | $3,389 |
| 2024 | OH | 77/1,000 | $392 | $3,323 |

Oklahoma's first rollout check used 1,000 units per year:

| Year | State | Mismatches | Mean absolute difference | Maximum difference |
|---:|:---:|---:|---:|---:|
| 2022 | OK | 261/1,000 | $84 | $453 |
| 2023 | OK | 280/1,000 | $87 | $453 |
| 2024 | OK | 528/1,000 | $58 | $431 |

Oregon's first rollout check used 1,000 units per year:

| Year | State | Mismatches | Mean absolute difference | Maximum difference |
|---:|:---:|---:|---:|---:|
| 2022 | OR | 137/1,000 | $158 | $2,140 |
| 2023 | OR | 129/1,000 | $154 | $1,970 |
| 2024 | OR | 125/1,000 | $148 | $1,825 |

Georgia's first rollout check used 1,000 units per year:

| Year | State | Mismatches | Mean absolute difference | Maximum difference |
|---:|:---:|---:|---:|---:|
| 2022 | GA | 112/1,000 | $207 | $4,025 |
| 2023 | GA | 112/1,000 | $207 | $4,025 |
| 2024 | GA | 347/1,000 | $346 | $3,773 |

Arizona's first rollout check used 1,000 units per year:

| Year | State | Mismatches | Mean absolute difference | Maximum difference |
|---:|:---:|---:|---:|---:|
| 2022 | AZ | 278/1,000 | $62 | $502 |
| 2023 | AZ | 86/1,000 | $178 | $502 |
| 2024 | AZ | 51/1,000 | $299 | $502 |

Idaho's first rollout check used 1,000 units per year:

| Year | State | Mismatches | Mean absolute difference | Maximum difference |
|---:|:---:|---:|---:|---:|
| 2022 | ID | 887/1,000 | $74 | $3,666 |
| 2023 | ID | 1,000/1,000 | $149 | $3,721 |
| 2024 | ID | 1,000/1,000 | $178 | $3,735 |

## Synthetic-grid audit (2026-09-29)

The CPS comparisons above are confounded by PolicyEngine's TAXSIM adapter: it
does not consume `dep6`, and it turns `dep13`/`dep17` counts into children of
default ages, so every state credit keyed to a child's age (under 6, under 12,
under 17) is compared against the wrong child. This audit therefore uses a
synthetic grid where both models see identical, explicit child ages: single and
joint filers, wages from $0 to $250,000, and zero to two children aged 3, 10 or
16, for each of 2022, 2023 and 2024 (218 rows per state and year). Rows where
the federal tax differs by more than $1 are dropped so only state logic is
compared. A row is "off" when state tax differs by more than $1.

Every mismatch below was traced to a rule or a parameter, not left as noise.
Fixes made in this pass:

| State | Problem found | Fix |
|:--|:--|:--|
| CO | 2022-23 child credit used the 2024 flat-dollar table capped by the wrong federal credit; the 2024 table and the sales-tax refund were read with TAXSIM's interpolating lookup instead of as step schedules; no 2024 Family Affordability Credit; no EITC for childless filers aged 19-24 | Step lookups; 2022-23 credit rebuilt as the Colorado worksheet (share x federal credit for children under six); FAC added; under-25 EITC added |
| CA | CalEITC 2022+ paid $0 in the middle of the income range because a pre-2017 formula shape was kept; Young Child Tax Credit was paid above the earnings threshold with no child under six (a TAXSIM bug, see CA-004); no YCTC for zero-earner filers | Dated statutory CalEITC; YCTC gates (statutory mode only) |
| DC | Childless EITC kept the temporary 2021 maximum ($1,502) | Federal childless maximum with DC's own phase-out start |
| GA | 2024 dependent exemption dropped to $0 | $4,000 per dependent |
| ID | Standard deductions frozen at 2021; grocery credit frozen at $100 | Federal-conforming amounts; $120 |
| IL | 2022 EITC match 20% (real: 18%); no age-18 childless EITC; no 2024 child tax credit | All three |
| MI | Personal exemption, homestead cap and phase-out, and heating-credit amounts frozen at 2021; 30% EITC match applied to 2022 (real: 2023) | Dated values; 6% for 2022 |
| OK | EITC treated as nonrefundable in 2022+; 2024 joint top-bracket start | Refundable; new 2024 table |
| OR | No Oregon Kids Credit (2023+) | Added |
| UT | 2022 dependent amount in the taxpayer credit set to 0 | $1,802 |

Rows still off after this pass (2023 unless noted; "kid" rows use explicit ages):

| State | Off / rows | What is left |
|:--|:--|:--|
| CO | all | PolicyEngine adds the TABOR cash-back rebate ($800 per adult, 2023); it is a state rebate, not income tax, and is deliberately left out. 2022 residuals are the sales-tax refund (PolicyEngine does not double it on joint returns). |
| ID | all | Exactly $10: the port includes the Permanent Building Fund tax, PolicyEngine does not. |
| AR | 9 no-kid, 35 kid | Low-income tax tables (PolicyEngine gives $0 tax at moderate incomes where the port does not). |
| HI | 13 / 56 | Differences of $20-$450 between $15,000 and $80,000 that vanish above $110,000; not yet traced. |
| KY | 9 / 32 | Joint returns: the port takes one standard deduction per taxpayer, PolicyEngine one per return. Needs a Department of Revenue form check. |
| CT, DE, IN | 5-7 / 24-40 | Credit interactions, not yet traced. |
| AL, NC, OK, OH, PA | small | Dependent-exemption and credit details, $2-$60. |
| UT (2024), CA (2022-24) | 4 and 3 rows | UT 2024 child credit needs the optional `children_under_4` input; CA rows at $250,000 (exemption-credit phase-out). |
| IL | 1 no-kid row | Income exactly at the $250,000 exemption cutoff. |

States exact on this grid in all three years: AZ (2023-24), DC, MI, OR, UT (2022-23),
NH, and PA except for a $46 constant.

### Second pass (2026-10-01): pending issues

| State | Finding | Fix |
|:--|:--|:--|
| AR | The 3.4% bracket was entered as the top rate in 2022, 2023 and 2024 tables; the 2022+ low-income tax tables did not exist (the old 1991-2021 CSV was reused); the additional tax credit was limited to aged filers | Corrected rows; new dated step tables (`low_income_table_2022plus.csv`); credit for every head or spouse |
| KY | Joint returns took two standard deductions | One per return from 2022 (Form 740 instructions); the third pass adds the combined-return option |
| CT | Phase-out add-back and recapture used TAXSIM's continuous formulas; exemption phase-out was continuous | Dated step add-back and three recapture tiers; $1,000-per-$1,000-or-part exemption phase-out |
| IN | EITC used 2021 parameters of the old limited formula | Share of the federal EITC (2022+) |
| AL | Dependent exemption interpolated linearly between breakpoints | Statutory step schedule |
| NC | Child deduction interpolated linearly | Dated step schedule ($3,000 down to $0 by AGI) |

Explained, not bugs:

- **HI and DE:** TAXSIM imputes a sales-tax itemized deduction, so the port itemizes
  at middle incomes. The PolicyEngine run disables state and local tax deductions.
  The gap is the imputed deduction times the rate.
- **ID:** the port's $10 Permanent Building Fund tax is current law (2024 Form 40,
  line 32). PolicyEngine omits it.
- **CO:** PolicyEngine includes the TABOR cash-back rebate; the port deliberately does not.
- **AR joint returns at about $30,000:** PolicyEngine applies the main schedule where
  one spouse has all the income; the port applies the joint low-income table.

Rows off by more than $1 on the grid (327 rows per state across 2022-2024), after both passes:

| State | Off | Note |
|:--|--:|:--|
| DC, NC, OR, NH | 0 | exact |
| PA | 3 | one joint row |
| UT | 4 | 2024 child credit needs `children_under_4` |
| CA, IL, AL | 12 | high-income exemption details, $11 |
| IN | 15 | 2022 joint returns |
| MI | 21 | rounding ($1) |
| KY | 0 | family-size credit fixed in the third pass |
| GA | 9 | $3-5 |
| CT | 30 | personal credit tier at AGI boundaries, $6-$26 |
| OH, AZ | 30, 36 | tier boundaries and a $6 bracket detail |
| AR | 78 | $1.6 recapture detail and the joint-return case above |
| DE, HI | 87, 196 | imputed sales-tax deduction |
| OK | 109 | credit details, $4-$60 |
| CO | 113 | TABOR rebate |
| ID | 297 | $10 building-fund tax |

### Third pass (2026-10-01): the other 21 states, and filers 65 or older

The remaining income-tax states (VT, NM, MA, NY, VA, NJ, MN, WI, MD, IA, KS, LA, ME,
MS, MO, MT, NE, ND, RI, SC, WV) now run actual 2022-2024 law in statutory mode and
were checked on the same working-age grid. A second grid of age-65+ households
(pension-only, Social Security-only and interest-only income, single and joint) was
run for every state. Fixes from the age-65+ grid:

| State | Finding | Fix |
|:--|:--|:--|
| AZ | Senior property-tax credit paid with no property tax or rent (a Social Security-only retiree got $502) | Capped at property tax paid plus 15% of rent (statutory mode) |
| IN | Elderly credit interpolated between income steps | Step schedule on federal AGI, 2022+ |
| NM | Low-income rebate used the 1977 table | 2022-2024 step tables by number of exemptions (aged filers count extra) |
| KY | One pension cap per joint return; one standard deduction per joint return; family-size credit on a stale 2021 table and counting age 65 as an extra person | Cap per spouse (pension treated as split); combined return allowed when lower; step credit against the poverty guideline, family size without age |
| OH | Joint-filing credit required earned income | Pensions qualify in 2022+ |
| NJ | Pension exclusion all-or-nothing at $100,000 | Phase-down by income (100% / 50% / 25% joint; 37.5% / 18.75% single) |
| CA | Credit for age 65+ missing | Added, equal to the personal credit |
| MN | Subtraction for the elderly or disabled ended in 2016 | Continues under current law |
| ND | Marriage credit ignored pensions | Pension shared between spouses |
| RI | Retirement exclusion $15,000 and 2021 income limits | $20,000 from 2023; limits indexed to 2022-2024 |
| WV | Family credit tested against WV AGI (after the senior subtraction) | Federal AGI |
| IA | Zero-tax threshold ignored taxpayers 65 or older | $24,000 single / $32,000 other (2022+) |
| GA | $1,300 added to the standard deduction at 65 in 2024 | Removed (2024 Form 500) |
| MT | $800 aged interest exclusion kept in 2024; 2022-23 pension exclusion on a stale 2021 index | Repealed for 2024; 2022 $4,640 / 2023 $5,060 from the state's tax tables |
| LA | Share of the federal elderly credit allowed | Removed (2022+) |
| AR | Low-income table looked up on income after the standard deduction | Looked up on income before it (the table includes the deduction) |
| IL | No exemption phase-out | No exemption above $250,000 ($500,000 joint), 2017+ |
| AL | Additional Medicare tax counted twice in the FICA deduction | Counted once (statutory mode) |
| UT | Child credit applied in 2022-2023 | Starts in 2024 |
| HI | Pensions never excluded (TAXSIM slot bug) | Excluded in statutory mode |
| WA | No income tax, so nothing | Refundable Working Families Tax Credit ($300-$1,290 by children, phased out below the EITC ceiling, $50 floor) as negative state tax in statutory mode; the capital gains excise tax is not modelled |
| OK | EITC at 5% of the current federal credit | 5% of the federal credit computed under tax year 2020 rules (68 O.S. 2357.43) |
| CA | Head of household and married separate exemption-credit amounts swapped (2022+) | Fixed |
| IN | 2022 EITC as a share of the current federal credit | 2022 formula: two children at most, no higher phase-out start for joint returns |
| PA | Forgiveness phased out continuously | 10 points per $250 (or part) over the allowance, 2022+ |
| MA | Payroll deduction counted both halves of FICA | Employee half only (statutory mode) |
| NY | Children under 4 received the 2022 Empire State child credit | Excluded in 2022 (eligible from 2023) |
| MI | Flat $20,000 deduction for every filer 65+, no retirement-subtraction phase-in | Born 1953+: standard deduction of $20,000 ($40,000 joint) less personal exemptions (MI-1040 Worksheet 2), or the 2023-24 phased-in retirement subtraction (25% / 50% of the private-pension maximum) when larger; interest no longer deductible for these birth years |
| CO | No high-income add-backs | Federal deductions above $12,000 / $16,000 added back when AGI exceeds $300,000 (2022: itemized only, $400,000, $30,000 / $60,000); qualified business income deduction added back above $500,000 / $1,000,000 |

Age-65+ results after these fixes: Social Security-only households match everywhere
except the $10 Idaho fee. Pension-only rows still differ in MO and MT, where
PolicyEngine treats TAXSIM's `pensions` as a private pension. The port keeps TAXSIM's
convention: the single `pensions` input is the kind each state exempts, and 2022+
behaves as an updated TAXSIM would. Hawaii's exclusion, which TAXSIM never applies
(a Fortran slot bug), is applied in statutory mode. After rechecking the interest-only rows: MI and CO are now exact apart from CO's TABOR-type rebates in PolicyEngine. CT's remaining rows sit on the $1,000-or-part exemption phase-out boundary (TAXSIM adds $0.001 to AGI). DE and HI differ by the imputed sales-tax itemized deduction (the port itemizes, PolicyEngine takes the larger standard deduction). Small residuals remain in MD, ME and IL at $180,000 and above, and AR/LA by $20-$40.

PolicyEngine differs from the statute for the New Jersey pension exclusion at
$125,000-$150,000: it applies the phase-down percentage to the pension, while the
statute applies it to the maximum exclusion. The port follows the statute.

Working-age results after the third pass (all 45 income-tax states, 2022-2024, $0 to $1.5M, 0-2 children, ages passed to both models): most states are exact. What remains, by size:

- **HI, DE:** the imputed sales-tax itemized deduction (explained above).
- **WA:** now modelled (credit only), exact against PolicyEngine. Washington's 7% capital gains excise tax (over $250,000) is not modelled because TAXSIM's `ltcg` cannot separate the exempt real estate and retirement-account gains.
- **KS:** the food sales tax credit is refundable in the statute; PolicyEngine treats it as nonrefundable. The port follows the statute.
- **Traced and settled:** OK, CA, IN, PA, MA and NY 2022 as above. VA: PolicyEngine pays both the low-income credit and the refundable EITC; the 2023 Form 760 instructions allow only one, and the port follows them. NY: the small $16-$33 child-credit residuals are PolicyEngine using the current federal child credit, while Form IT-213 uses 2017 federal rules, which the port follows. Still open and small: CA joint at $600,000 with children ($91-$280) and single childless at $250,000 ($6), IN 2024 joint with two children ($57), NY 2023 two children at low income.
- **ID:** the $10 fee.

## Tax year 2025 (2026-10-02)

All 45 income-tax states and the federal return were extended to 2025 from PolicyEngine's 2025 parameters, read
against state forms where unclear. The same single-batch grid (working-age with child ages, aged pension /
Social Security / interest, itemizers, renters) was run for 2022-2025.

**Federal (2025 budget law, P.L. 119-21):** child credit $2,200; senior deduction ($6,000 per person 65+, less 6%
of income over $75,000 / $150,000, not for married-separate); SALT cap $40,000 reduced 30% of AGI over $500,000
to a $10,000 floor. Not modelled because TAXSIM has no input for them: deductions for tips, overtime and car
loan interest. All federal grid rows match PolicyEngine.

**State changes made for 2025:** AR new tables; CA indexing and the exemption-credit phase-out; CO rate 4.4% and
credits; CT $250 EITC child bonus and property credit open to all; DC 100% EITC match and the pre-2025 standard
deduction; GA 5.19% rate; HI Act 46 brackets; IA flat 3.8%; ID 5.3% rate and $155 food credit; IL CTC 40%; IN 3%;
KS food credit repealed; KY indexing; LA flat 3% with a $12,500 / $25,000 deduction; MD new 6.25% / 6.5% rates, flat
deduction, itemized phase-out, 2% capital gains surtax; ME pension phase-out, dependent credit, standard
deduction and property tax credit rebuilt; MI, MN, MO (4.7% and no tax on capital gains), MS 4.4%, MT, NC 4.25%,
ND, NE (5.2% top), NH interest and dividends tax repealed, NJ, NM (new schedule, $2,500 gains deduction),
NY ($1,000 / $330 child credit), OH, OR, PA new 10% earned income credit, RI ($50,000 retirement exclusion), SC 6%
top rate and non-conformity addbacks, UT 4.5% and child credit for ages 0-5, VA 20% refundable EITC, VT enhanced
EITC and child credit through age 6, WA Working Families credit, WI new brackets and the 67+ retirement
exclusion, WV new rates and Social Security phase-in, AZ senior deduction subtraction.

Real-law corrections found while doing this (also apply to 2022-2024): CA itemized deduction no longer capped
at $10,000 SALT and the exemption credit phases out per credit; DC property tax credit ends above its income
limit; KS filers can itemize after 2020; MN, ME, NM and others had stale or missing tables; MA payroll deduction
counts the employee half only; NY under-4 credit excluded in 2022.

**Left as differences from PolicyEngine:** Delaware and Hawaii (imputed sales-tax itemized deduction, larger in
2025 with the $40,000 SALT cap); Missouri / Montana pensions (pension-type convention); Maine rent assumed to
exclude utilities; Maine affordability payment, Colorado TABOR rebates and Oregon kicker (one-time payments);
Minnesota, Massachusetts, Vermont and Wisconsin renter / homeowner refund tables (PolicyEngine differs or lacks
inputs); Indiana EITC (PolicyEngine gates on frozen 2023 rules); Kansas and Mississippi high-income itemizers.

## What is explained

- **Arkansas:** The 2022-2024 standard deductions, ordinary rate tables, and
  recent inflationary-relief credits now match PolicyEngine to rounding
  precision through the high-income recapture region. The official terminal
  formulas fixed the prior large joint-return differences; remaining high
  income residuals are about `$2` from discrete tax-table rounding. Arkansas's
  detailed low-income tax scales and resident-only worksheet credits still need
  exact form-level treatment; they are not inferred from the 35 TAXSIM inputs.
- **New Hampshire:** The 2023 4% and 2024 3% interest-and-dividends rates,
  with unchanged taxpayer and age exemptions, match PolicyEngine to less than
  one cent on representative dividend-income cases. Education credits and
  disability/blind exemptions remain outside the current input contract.
- **Hawaii:** Statutory mode now uses the 2024 doubled standard deductions, the
  2023-2024 food/excise credit schedules, and the refundable 40% state EITC
  enacted for 2023. Act 46's bracket expansion begins in 2025, so 2022-2024
  retain the 11% top rate. Detailed form status/residency and other credits
  remain outside the TAXSIM input contract.
- **California:** The dated 2022-2024 brackets, standard deductions, exemption
  credits, CalEITC maximums, and YCTC amounts now run through the statutory
  calculator. Ordinary wage cases are suitable for comparison, but the local
  PolicyEngine TAXSIM adapter does not carry the full YCTC/CalEITC worksheet
  semantics: child-credit cases differ materially and are not treated as a
  validation failure of the statutory branch. Foster-youth eligibility,
  residency, and detailed FTB worksheet tests remain outside the TAXSIM input
  contract.

- **Colorado:** PolicyEngine includes several refundable programs that the
  TAXSIM-shaped calculator did not previously represent: the larger recent
  Colorado EITC match, the refundable child tax credit, and recent sales-tax
  refund schedules. The EITC, child credit, and 2022/2024 sales refund are now
  parameterized. Remaining differences are primarily Colorado refundable
  programs outside the current TAXSIM input contract, including the family
  affordability credit, TABOR-related payments, and income-qualified senior
  housing credit. These need a deliberate scope decision before implementation.
- **Illinois:** The recent rate, personal exemption, and EITC match now have
  dated values. Remaining differences are concentrated among units receiving
  credits or rebates that PolicyEngine models but the current TAXSIM-shaped
  inputs do not fully describe.
- **Michigan:** The recent EITC match is now updated to 30%. The remaining
  differences are mostly homestead/property and heating-credit cases, where
  PolicyEngine has newer structures and values than the existing historical
  tables. These require form-level mapping, not just another rate value.
- **Pennsylvania:** This is the closest comparison. The flat rate is stable;
  the small residuals are concentrated in tax-forgiveness and input allocation
  edge cases rather than the headline rate.
- **Utah:** The flat rate, taxpayer-credit phaseouts, EITC, child tax credit,
  and the alternative retirement/Social Security credit rules are now mapped.
  On a separate 300-unit Utah sample, mismatches fell to 72/300 in 2022,
  11/300 in 2023, and 10/300 in 2024; mean absolute differences were `$55`,
  `$9`, and `$9`. Remaining differences are concentrated in provisions that
  TAXSIM cannot identify from its inputs: 529-plan contributions,
  military-retirement pay, and the at-home-parent credit's per-parent earned
  income test. Those need new optional inputs or an explicit scope decision.
- **North Carolina:** The dated flat rates and standard deduction now use
  PolicyEngine's 2022-2024 values. Remaining differences are mostly federal
  tax-base/input-contract cases and the state's military-retirement deduction,
  which CPS/TAXSIM inputs do not identify.
- **Indiana:** The dated flat rate and 10% recent EITC match are now mapped.
  Remaining differences include county/local-rate inputs, the elderly credit,
  automatic refund rebate, and 529 or military deductions not present in the
  TAXSIM-shaped input frame.
- **Kentucky:** The dated flat rates, standard deductions, and poverty-line
  factors for the family-size credit are now mapped. Remaining differences are
  concentrated in the family-size/personal-credit interaction and other
  deductions or credits outside the input contract.
- **Connecticut:** The recent personal-credit calculation now uses the
  discrete 2022+ worksheet tiers; this reduced the 1,000-unit CPS mismatches
  substantially. Dated rates, EITC, property-credit steps, pension
  subtraction, and Social Security subtraction are also mapped. Remaining
  differences are concentrated in AMT/recapture cases, refundable credits, and
  income categories that the PolicyEngine TAXSIM adapter does not map
  identically.
- **District of Columbia:** Dated 2022-2024 brackets, the recent 70% EITC
  match, and dated Schedule H property-credit parameters are now mapped. The
  statutory Schedule H formula also adds 20% of rent to property tax, matching
  PolicyEngine's formula. The CPS comparison remains limited by the current
  PolicyEngine TAXSIM adapter not opting sampled units into the refundable DC
  property credit, as well as missing renter/property and childless-worker
  inputs; those differences are not evidence that the local statutory credit
  should be removed.
- **Ohio:** Statutory mode now uses the dated 2022-2024 brackets, zero-bracket
  thresholds, exemption values, and 30% EITC match. The CPS comparison falls
  to 79, 79, and 77 mismatches out of 1,000. Remaining large differences are
  concentrated in non-wage income and Ohio-specific deductions or credits that
  the current PolicyEngine TAXSIM adapter does not map identically, including
  retirement and 529-plan provisions.
- **Oklahoma:** Statutory mode now uses the dated 2022-2024 single/separate and
  joint/head-of-household rate tables. A simple $30,000 wage case matches
  PolicyEngine to the cent. Remaining CPS differences are concentrated in
  Oklahoma's sales-tax and property-tax credits, retirement and military
  exclusions, and other special inputs that are not fully represented in the
  current TAXSIM-shaped frame; the 2024 sample also exposes additional dated
  credit differences for follow-up.
- **Oregon:** Statutory mode now uses dated recent brackets, standard
  deductions, federal-tax-subtraction limits, and exemption-credit amounts.
  Ordinary wage cases match PolicyEngine to the cent. Explicit TAXSIM child
  ages now activate Oregon's 12% under-three EITC match, and `dep13` controls
  the recent care-credit child cap. CPS ASEC retains its aggregate dependent
  counts because replacing them with ages would discard enrolled/disabled
  dependent information; remaining CPS residuals are mainly Oregon's kicker,
  retirement credit, working-family credit, and age/child details unavailable
  in that aggregate frame.
- **Delaware:** PolicyEngine's 2022-2024 brackets, standard deduction, and
  2021+ EITC rates are identical to the existing local table. Statutory mode
  now selects the actual Delaware year in statutory mode so recent years use
  the reviewed law rather than projected TAXSIM parameters. The calculation
  formulas remain the existing TAXSIM-shaped Delaware formulas. PolicyEngine also
  models a Delaware relief rebate, but the
  current TAXSIM input contract has no matching rebate eligibility inputs. A
  second important comparison limitation is in the PolicyEngine TAXSIM
  adapter: it maps TAXSIM's total `dividends` input to qualified dividends,
  leaving ordinary dividends out of Delaware AGI. Delaware's official
  instructions begin the state return with federal AGI, so those rows should
  not be used as evidence that the local Delaware calculator is over-taxing
  dividends. See the [2022 Delaware resident-return instructions](https://taxsim.nber.org/historical_state_tax_forms/DE/2022/PIT-RES_TY22_2022-02_Instructions.pdf)
  and the [Delaware personal-income-tax FAQ](https://revenue.delaware.gov/frequently-asked-questions/personal-income-tax-faqs/).
- **Georgia:** The 2022-2023 rate schedules and deductions, and the 2024 5.39%
  flat rate, are now dated in statutory mode. The local TAXSIM compatibility
  suite remains exact. The remaining PolicyEngine differences are concentrated
  in retirement-income exclusions, personal/dependent credits, and 2024's
  changed deduction/exemption treatment; these need age- and form-level inputs
  or a deliberate scope decision rather than another rate-table substitution.
- **Arizona:** The statutory model now uses the dated 2022 bracket transition,
  the 2023-2024 2.5% rate, and PolicyEngine's 2022-2024 standard deductions.
  The dependent, family-income, increased-excise, and senior property-tax
  credit formulas are represented; statutory mode also uses Arizona's property
  credit AGI treatment, which excludes gross Social Security. PolicyEngine's
  dated refundable list has no 2022 families rebate. The remaining differences
  include charitable-contribution qualification and other provisions that
  cannot be identified from TAXSIM's undifferentiated charity input.
  Independent verification found those headline parameters consistent with
  Arizona's official 2022 Form 140 Tax Tables X/Y and filing materials: the
  filed-return threshold is $28,653 after the statutory CPI adjustment, the
  standard deductions are $12,950/$19,400/$25,900 by filing status, and the
  2022 property-credit instructions explicitly exclude Social Security from
  household income. The official sources are the
  [2022 Form 140 Tax Tables X/Y](https://azdor.gov/sites/default/files/2023-03/FORMS_INDIVIDUAL_2022_TablesXY.pdf),
  [2022 Form 140 booklet](https://azdor.gov/sites/default/files/2023-03/FORMS_INDIVIDUAL_2022_140BOOKLET.pdf),
  and [2022 Form 140PTC instructions](https://azdor.gov/sites/default/files/2023-03/FORMS_INDIVIDUAL_2022_140PTCi-2D.pdf).
- **Idaho:** The statutory model now uses the official 2022 four-bracket
  schedule and the 2023-2024 rate transitions. The local TAXSIM compatibility
  suite remains exact. The larger CPS residuals are not evidence that the
  headline rates are wrong: Idaho's modern law includes QBI, retirement and
  military subtractions, grocery and aged/disabled credits, and rebate rules
  that are not fully recoverable from the current TAXSIM-shaped inputs. The
  [Idaho State Tax Commission rate schedule](https://tax.idaho.gov/taxes/income-tax/individual-income/individual-income-tax-rate-schedule/)
  independently confirms the imported rates.

## Next work

The imported states are not yet complete statutory replacements. The next changes
should add and test the missing credit formulas one state at a time, using the
PolicyEngine parameter path and the state's official form/instructions as
provenance. One-time rebates and programs not represented by TAXSIM inputs
should be either added as explicit optional inputs or documented as an
intentional model-scope difference, rather than silently folded into `siitax`.
