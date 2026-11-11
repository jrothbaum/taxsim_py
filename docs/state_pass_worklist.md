# State pass worklist (TAXSIM mode vs the 2026 build)

Counts are failures from `scripts/validate_states.py` (cases that differ from `taxsim2026_mtr.exe`). Re-run with `TAXSIM_VALIDATE_DETAIL=0 uv run python scripts/validate_states.py XX`.

Not zero means unfinished: each state below needs another pass.

| State | Failures | Years with failures (count) |
| --- | ---: | --- |
| RI | 24 | 1977-81 professional and non-property cases, 2021 childcare marginal rate, 2022-23 high-income wages ($1-12) |
| NC | 0 | done |
| AZ | 10 | 1977-81 professional and non-property cases (federal pre-1982 gaps) |
| MO | 21 | 2008-12 itemizers, 1986-87 HoH low income, small dividends 2017-21 (pennies) |
| NM | 0 | done |
| DE | 0 | done |
| VT | 0 | done |
| MT | 23 | 1977-81 professional/S corporation and non-property cases, 1984-86 unemployment couple, 2014-17 childcare couple at $90,000 (marginal rate), 2022-23 aged renter and aged couple with low wages |
| MD | 4 | 2020 aged single and 2022-23 aged couple with low wages (poverty/EITC interplay), 2023 low income single |
| OK | 11 | 1979-81 federal gaps, 2021-23 aged low-income marginal rate |
| MA | 39 | 1977-80 self-employment, 2020 capital gains (rounding of the exempt amount), 2022 pennies on very high incomes |
| WI | 16 | 2022-23 married separate pennies, 2023 aged couple 2 cents |
| IN | 20 | 1977-80 aged low-income (property/renter credits), 2022-23 aged low-income and 2023 joint |
| CA | 12 | 1987-95 itemized joint high-income (AMT), 2022-23 a few cents on high-income wages |
| UT | 96 | 2008-2012 and 2022-23 small groups, 1988 (10), 1993-2007 three cases a year, 1977-81 two a year |
| NY | 16 | 1985-86 renter credit cutoff (hy offset), 2020 unemployment/aged low-income marginal rates, 2021 childless aged EITC and unemployment |
| AR | 18 | 2018-23 itemized (own rules), 2021 five cases, 1984 three cases |
| KS | 16 | 1977-81 federal gaps, 2013-16 non-property income, 2021 low-income childcare, 2008 aged couple |
| ME | 11 | 2020-21 aged low-income and renter refundable credits, 2021 itemized and low-income childcare |
| ID | 5 | 1977 aged renter, 2021-22 dependent filer rate |
| WV | 0 | done |
| OH | 0 | done |
| MI | 23 | 2017-23 aged low-income refundable credits, 2021-23 dependent filer and self-employed property credit |
| GA | 84 | aged cases 1989-2020 depend on uninitialised ceilings (record order), 1982-85 five a year, 2022-23 |
| DC | 6 | 2018-23 dependent filer with interest |
| MN | 32 | 1977-86 federal pre-1982 gaps, 2023 married separate, 2018-20 itemized joint high income |
| VA | 31 | itemizer pennies 2019-23, 1989-99 one a year, 2021 childcare HoH and aged couple |
| PA | 0 | done |
| ND | 20 | 2018-22 joint self-employment ($2.20), 2023 married separate |
| SC | 1 | 2021 itemized joint |
| MS | 12 | 1977:1, 1978:1, 1979:1, 2018:3, 2019:3, 2020:3 |
| AL | 10 | 1977:2, 1978:2, 1979:2, 1980:2, 1981:2 |
| IA | 10 | 1977:2, 1978:2, 1979:2, 1980:2, 1981:2 |
| KY | 10 | 1977:2, 1978:2, 1979:2, 1980:2, 1981:2 |
| CT | 8 | 2003:1, 2004:1, 2005:1, 2006:1, 2007:1, 2008:1, 2009:1, 2010:1 |
| CO | 4 | 1977:2, 2008:1, 2022:1 |
| LA | 4 | 1980:2, 1981:2 |
| IL | 1 | 2022:1 |
| AK | 0 |  |
| HI | 0 |  |
| NE | 0 |  |
| NJ | 0 |  |
| OR | 0 |  |

## Known open causes

- Federal 1977-81 (nonprop, professional income, maximum tax): IA, AL, CO, MS and others inherit it.
- Federal AGI carries 1 cent more in the oracle ($0.011 vs $0.001): interpolation-heavy states (CT credit).
- Federal AMT large married-separate (CT 2003-2010).
- NC 1995-2013 child care credit: the oracle gives about $12 where the table implies $216; unexplained.
- RI 2016+: about $2 on mid incomes (bracket/aif rounding); RI 2011-2012 high income.
- KS: aged food refund, self-employment, 2021.
- GA: retirement exclusion (age 62+, earned-income rules).
- ID: 2018-2023 rows, dependent filers, aged renters, self-employment.
- VA: 2022-23 Form 760ADJ choice, aged couples 1989-99, married-separate itemizers 2019-22.
- DC: 2014+ property tax credit (Schedule H), 2021+ high-income itemizers.
- MN: 1977-86, 2018-23 itemizers and Social Security.
- AZ, MD, MA, ME, MI, MT, ND, OK, PA, SC, UT, VT, WV, WI, NM, NY, OH, DE, CA, AR, IN: not yet re-examined after the oracle rebuild (see counts).
