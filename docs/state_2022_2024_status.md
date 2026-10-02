# State 2022-2024 status

This is the rollout tracker for replacing old projected state-law values with
dated statutory values. `Implemented` means the local calculator accepts actual
2022-2024 values in statutory mode and has been compared with PolicyEngine. It
does not mean every PolicyEngine program is representable by the TAXSIM input
contract.

| State | TAXSIM code | Status | Main remaining work |
|:---|---:|:---|:---|
| Colorado | 6 | Core import compared (2026-09-29 grid audit) | TABOR cash-back rebate and senior-housing credit are outside the TAXSIM contract |
| Alabama | 1 | Core import compared | Federal-tax deduction and other inputs outside TAXSIM |
| Arkansas | 4 | Dated core compared | Low-income tax tables and resident-only worksheet provisions need exact review |
| California | 5 | Core import compared (2026-09-29 grid audit) | Foster-youth/residency and AMT edge cases; exemption-credit phase-out at very high incomes |
| New Hampshire | 30 | Core import compared | Education credit and non-TAXSIM taxpayer-status inputs |
| Hawaii | 12 | Recent core imported; comparison open | Form-level food-credit details and credits requiring inputs outside TAXSIM |
| Arizona | 3 | Core import compared | Refundable credits and 2022 transition details |
| Connecticut | 7 | Core import compared | AMT, recapture, refundable credits, and input-contract cases |
| District of Columbia | 9 | Core import compared | Childless EITC and property/renter/take-up inputs |
| Delaware | 8 | Core import compared | Relief rebate and inputs outside TAXSIM |
| Georgia | 11 | Core import compared | Retirement exclusion and credits outside TAXSIM inputs |
| Idaho | 13 | Core import compared | QBI, retirement/subtraction, grocery credits, rebates |
| Illinois | 14 | Core import compared | Rebates and credits outside TAXSIM inputs |
| Indiana | 15 | Core import compared | County rates, elderly/refund/529 inputs |
| Kentucky | 18 | Core import compared | Credit interaction and unavailable deductions |
| Michigan | 23 | Core import compared | Homestead, heating, and retirement inputs |
| North Carolina | 34 | Core import compared | Military retirement and input-contract cases |
| Pennsylvania | 39 | Core import compared | Forgiveness edge cases |
| Utah | 45 | Core import compared | 529, military retirement, at-home-parent inputs |
| Ohio | 36 | Core import compared | Retirement, 529, and other deductions outside TAXSIM inputs |
| Oklahoma | 37 | Core import compared | Sales-tax/property credits and special income inputs |
| Oregon | 38 | Core import compared | Kicker, retirement, child-care, and age-specific inputs |

## Child-related provisions

The recent state models do not all have a separate state child tax credit. The
current implementation status is:

| State | Recent child-related implementation | Input limitation or boundary |
|:---|:---|:---|
| AZ | 2019+ dependent tax credit, including the higher amount for `dep17`; existing EITC/family provisions | Count-based; 2022 transition and programs outside TAXSIM inputs still need separate review |
| CO | Child-care credit, EITC, and 2022+ refundable child tax credit using `dep6` | Requires the under-six count; other family programs are outside the TAXSIM contract |
| CT | EITC; no separate recurring state child tax credit in the current model | Child-related rebates or non-income-tax programs are outside this calculator |
| DC | Child/dependent-care credit and EITC, including recent childless-worker rules | Property/renter credits and take-up inputs remain separate limitations |
| DE | Child/dependent-care credit and EITC | No separate recent state child tax credit is modeled |
| GA | Child/dependent-care credit and dependent-count low-income credit | No separate recent Georgia child tax credit is modeled |
| ID | 2018+ nonrefundable child tax credit, `$205 × dep17` subject to tax liability, plus the child-care deduction | Count-based and intentionally uses the TAXSIM-compatible eligible-child input |
| IL | EITC and property-tax credit | No separate recent state child tax credit is modeled |
| IN | Recent EITC with the statutory zero/one/two-child schedule using `dep18` | No separate recent state child tax credit is modeled |
| KY | Child/dependent-care credit | No separate recent state child tax credit is modeled |
| MI | EITC | No separate recent state child tax credit is modeled |
| NC | Historical child-care and child tax credits through 2013/2017 respectively | No 2022-2024 child credit is carried forward; later programs need a separate statutory source if added |
| OH | Child-care credit and the federal child-credit-based state credit formula | Uses federal credit inputs; other non-TAXSIM credits remain outside the contract |
| OK | Child-care/child-tax-credit-based credit and EITC | Uses federal credit inputs and does not add separate age-specific state inputs |
| OR | Recent EITC, with the higher rate when `children_under_3` is positive, and recent dependent-care credit using `dep13` | The optional semantic count is outside the TAXSIM 35-variable contract |
| PA | Dependent-count tax-forgiveness allowance | No separate recent state child tax credit is modeled |
| UT | 2022-2024 child tax credit and EITC using `children_under_4` | The optional semantic count is outside the TAXSIM 35-variable contract |

The CPS ASEC preparation exports `dep6`, `dep13`, `dep17`, and `dep18`, plus
the optional semantic extensions `children_under_3` and `children_under_4`.
Those extensions are not part of TAXSIM's 35-variable input set and default to
zero for ordinary callers. They let survey adapters preserve enrolled or
disabled-dependent information while still supplying exact age-sensitive state
counts. Explicit `age1`-`age3` inputs remain supported for callers that need
standard TAXSIM age behavior.

The remaining income-tax states have not yet been marked complete for this
period. The next pass should use the same order: import dated PolicyEngine
parameters, run a state-filtered CPS comparison for 2022-2024, run the local
TAXSIM compatibility suite, and record provisions that need new optional inputs
rather than guessing from CPS aggregates.

The comparison harness supports one or more state filters, for example:

```bash
uv run --group test scripts/compare_independent.py PATH/TO/cps_2011 \
  --state 34 --tax-year 2022 --tax-year 2023 --tax-year 2024 \
  --sample-size 1000 --engine policyengine --calculation-mode statutory
```
