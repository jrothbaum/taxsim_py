# 2026 state law in statutory mode: sources and verification status

Statutory mode (`calculation_mode="statutory"`) applies actual law through
2026 for all 44 modeled states (`law_coded_through: 2026` in each state
YAML). TAXSIM mode is unchanged: the TAXSIM build only knows 2025 law, so it
carries 2025 forward (with its own inflation proxy).

Each 2026 value was entered, compared with PolicyEngine-US on a grid
of about 40 synthetic returns per state (singles, couples, families, retirees), and
then checked against public sources. This page records what was checked and
what was not. Dates are the dates of the sources, retrieved November 2026.

**Status key**

- **Official**: the state's own form, schedule, press release or rule.
- **Law**: the enacted bill or a reliable news/tax-advisory report of it.
- **Derived**: not published yet; computed from the 2025 amount and an
  inflation factor, or copied from PolicyEngine-US. Expect small differences
  (usually under $20 of tax) once the state publishes.

## Where we deliberately differ from PolicyEngine-US

| State | What | Why |
|---|---|---|
| CO | Family Affordability Credit is off for 2026; state EITC match is 25% | Governor's office: both turned off for tax year 2026 (revenue trigger). PolicyEngine still pays the credit. |
| CO | Federal-deduction addback cap of $1,000 ($2,000 joint) for AGI over $300,000 | Taken from PolicyEngine; source not independently found. |
| AZ | Standard deduction follows the 2026 federal amount ($16,100 single) | PolicyEngine still shows $15,750. |
| CA | All indexed amounts use the Franchise Tax Board's 3.4% factor | PolicyEngine assumes about 2.3%. |
| MN | Brackets, standard deduction, itemized limits and credit thresholds use the Revenue Department's official 2026 table | PolicyEngine is $10-$50 lower on several. |
| MO | Brackets from the official 2026 MO-1040ES rate chart | PolicyEngine indexes them slightly lower. |
| LA | Standard deduction $12,875 / $25,750 | Department of Revenue 2026 withholding rule. |
| MD | Pension exclusion maximum $40,600 for 2026 (down from $41,200) | Comptroller guidance; PolicyEngine agrees. |

## State by state

| State | 2026 change in the model | Status |
|---|---|---|
| AL, AK, FL, NV, NH, SD, TN, TX, WA, WY | No wage income tax. | n/a |
| AR | Top rate 3.7% (from 3.9%); rate table and low-income tables rebuilt; standard deduction $2,570. | Rate: **Law** (HB 1001 / SB 1, May 2026 special session). Table rows and low-income tables: **Derived** from PolicyEngine; the 2026 DFA tables were not available. |
| AZ | Dependent credit $125 per child under 17; standard deduction $16,100. | **Law** (HB 4168, signed 2026-06-13). The same bill's other changes (SALT cap on itemizers, charitable standard deduction, adoption subtraction) are not modeled. |
| CA | 3.4% inflation factor on brackets, standard deduction, exemption credits, renter's credit, AMT, CalEITC, young child credit. | Standard deduction ($5,900 / $11,800), exemption credits ($158 / $491), renter's AGI limits: **Official** (FTB Tax News, October 2026). Bracket tops and CalEITC: **Derived** (FTB posts schedules in late December). |
| CO | See table above; flat rate 4.4% unchanged; QBI addback continues. | EITC 25% and credit shutoff: **Law**. Addback cap: **Derived**. |
| DC | Refundable child tax credit $1,000 per child under 18, reduced $50 per $1,000 of AGI over $55,000 ($70,000 joint, $35,000 separate); standard deduction, property credit and EITC amounts indexed. | **Law** (Act 26-217) but the credit's survival is **uncertain**: a congressional disapproval of DC's tax decoupling was reported in February 2026. Amounts for the other items are **Derived**. |
| GA | Flat rate 4.99%; standard deduction $15,000 / $30,000; dependent exemption $5,000; $250 credit per child under six. | Rate and standard deduction: **Official** (Georgia DOR). Dependent exemption: **Law** (HB 463). Under-six credit: secondary source only. |
| HI | Standard deduction $8,000 / $16,000 / $12,000; brackets unchanged from 2025. | **Official** (Hawaii DOTAX announcement 24-03). A new higher rate on income over $1 million (enacted July 2026) is not modeled. |
| IA | Flat rate 3.8% carried forward. | **Law** (most sources; one lists 3.9%). |
| ID | Brackets, standard deduction (federal amounts), child credit ended after 2025. | Child credit ending: **Official** (State Tax Commission). Others: **Derived**. |
| IL | Rate 4.95%, exemption indexed. | Rate: **Law**. Exemption: **Derived**. |
| IN | Rate 2.95%. | **Law**. |
| KY | Rate 3.5%; standard deduction $3,360. | **Official** (Department of Revenue). |
| LA | Flat 3%; standard deduction $12,875 / $25,750. | **Official** (LAC 61.I.1501 emergency rule, 2026 withholding). |
| MD | Standard deduction flat amounts $3,400 / $6,850; pension exclusion $40,600. | Pension: **Official** (Comptroller). Standard deduction: **Derived**. |
| ME | Brackets $27,400 / $64,850 (single); personal exemption $5,250; itemized cap $37,100; dependent credit $310; fairness credit amounts; **new 2% surcharge** on taxable income over $1 million ($1.5 million joint and head of household, $750,000 separate). | Surcharge: **Law** (L.D. 2212, approved 2026-04-09, retroactive to January 1). All indexed amounts: **Derived** (PolicyEngine). The 2025 affordability payment does not recur. |
| MI | Rate 4.25%, retirement subtraction fully phased in, indexed amounts. | **Derived** except rate (**Law**). Heating credit not re-checked. |
| MN | All 2026 amounts. | **Official** (Minnesota Revenue, "Tax Year 2026 Inflation-Adjusted Amounts" and bracket release, 2025-12-16). Social Security subtraction thresholds for 2024 and 2025 were also corrected (they had been left at the 2023 amounts). |
| MO | 2026 brackets $1,348 to $9,436; top rate 4.7% unchanged; capital gains subtraction continues. | **Official** (2026 MO-1040ES rate chart). |
| MS | Rate 4.0% above $10,000. | **Law**. |
| MT | 4.7% to $47,500 ($95,000 joint, $71,250 head of household), 5.65% above; same thresholds for capital gains (3% / 4.1%); state EITC match 20% (from 10%). | Brackets and rates: **Official** (Department of Revenue, HB 337). EITC match: **Law**. |
| NC | Rate 3.99%. | **Law**. |
| ND | Brackets $49,575 / $250,400 (single), $82,800 / $304,850 (joint); marriage credit amounts. | **Derived** (PolicyEngine). One secondary source matches. The Tax Commissioner's 2026 schedule was not found. |
| NE | Two upper rates become 4.55%; indexed amounts. | **Law** for the rate; amounts **Derived**. |
| NJ | Child tax credit up to $1,250 per child under six (2026-2028); other amounts indexed. | One tax guide only; enactment not confirmed from an official source. |
| NM | Child tax credit and low-income rebate tables scaled by PolicyEngine's inflation factor. | **Derived**. |
| NY | Five lowest rates fall 0.1 point (3.9%, 4.4%, 5.15%, 5.4%, 5.9%); child credit $500 for ages 4 to 16 (from $330); supplemental tax tables; itemized limit starts. | Rate cut and credit: **Law** (Thomson Reuters, CPA Journal). Exact rates and tables: **Derived** (PolicyEngine). |
| OH | Tax is $332 plus 2.75% of income over $26,050 (the 3.125% top rate is gone); exemption and joint credit limit at $500,000. | **Law** (HB 96, Legislative Service Commission, EY, Plante Moran); the $332 base from one secondary source. |
| OK | Rates 2.5% / 3.5% / 4.5% (three lowest rates removed). | **Law** (HB 2764). Exact bracket tops: **Derived**. |
| OR | Standard deduction, exemption credit and the state EITC match (14%) indexed or raised. | **Derived** (PolicyEngine / statute). |
| RI | Brackets $82,050 / $186,450; standard deduction $11,200 / $16,800 / $22,400; exemption $5,250; property tax credit maximum $715. | Standard deduction and exemption: **Official** (Tax Foundation index). Brackets: **Derived**, one guide matches. |
| SC | 1.99% to $30,000 and 5.21% above; new state deduction ($15,000 / $22,500 / $30,000, phased out over $55,000 to $110,000 of income); state EITC capped at $200. | **Law** (H. 4216, signed 2026-03-30). Phase-out start and widths: **Derived**. |
| UT | Rate 4.45%; taxpayer credit $2,159; child credit phase-out starts $49,000 / $61,000. | Rate: **Law** (S.B. 60). Credit amounts: **Derived**. |
| VT | Standard deduction and exemption indexed. | **Derived**. |
| WI | Standard deduction and brackets indexed. | **Derived**. |
| WV | Rates cut about 5% (top rate 4.58%); Social Security fully exempt. | **Law** (S.B. 392, effective retroactively to January 1). Individual bracket rates **Derived**. |

## Not checked against anything

- Michigan heating credit, Maine property tax fairness credit tiers, New
  Mexico rebate rows, the Arkansas rate-table constants above $95,000, and New
  York's supplemental tax tables come from PolicyEngine-US only.
- Provisions with no TAXSIM input (tips and overtime deductions, state senior
  deductions that depend on benefit data) are not modeled for 2026.
