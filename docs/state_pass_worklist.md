# State pass worklist (TAXSIM mode vs the 2026 build)

Counts are failures from `scripts/validate_states.py` (cases that differ from `taxsim2026_mtr.exe`). Re-run with `TAXSIM_VALIDATE_DETAIL=0 uv run python scripts/validate_states.py XX`.

Not zero means unfinished: each state below needs another pass.

| State | Failures | Years with failures (count) |
| --- | ---: | --- |
| RI | 603 | 1977:2, 1978:2, 1979:2, 1980:2, 1981:2, 2011:10, 2012:5, 2013:5, 2014:3, 2015:3, 2016:83, 2017:5, 2018:82, 2019:82, 2020:81, 2021:81, 2022:78, 2023:75 |
| NC | 0 | done |
| AZ | 493 | 1977:8, 1978:8, 1979:9, 1980:9, 1981:9, 1982:10, 1983:9, 1984:9, 1985:10, 1986:10, 1987:10, 1988:10, 1989:10, 1990:3, 1991:13, 1992:12, 1993:12, 1994:12, 199... |
| MO | 27 | 2008+ itemizers and dividends, srate-only head of household rows |
| NM | 0 | done |
| DE | 0 | done |
| VT | 0 | done |
| MT | 23 | 1977-81 professional/S corporation and non-property cases, 1984-86 unemployment couple, 2014-17 childcare couple at $90,000 (marginal rate), 2022-23 aged renter and aged couple with low wages |
| MD | 4 | 2020 aged single and 2022-23 aged couple with low wages (poverty/EITC interplay), 2023 low income single |
| OK | 50 | 1977-86 dividend/interest/gain and non-property cases (federal pre-1982 gaps), 2021-23 aged low-income marginal rate |
| MA | 39 | 1977-80 self-employment, 2020 capital gains (rounding of the exempt amount), 2022 pennies on very high incomes |
| WI | 81 | 2023 capital loss cases, 2022-23 married separate pennies/one case, 1988-97 separate returns $1.80, 2000-2016 one to four a year |
| IN | 20 | 1977-80 aged low-income (property/renter credits), 2022-23 aged low-income and 2023 joint |
| CA | 94 | marginal-rate (srate) gaps on high-income itemizers 2018-20, 1987-90 high income and AMT cases, 2005 HoH $260,000 (one case a year 1998-2013) |
| UT | 96 | 2008-2012 and 2022-23 small groups, 1988 (10), 1993-2007 three cases a year, 1977-81 two a year |
| NY | 16 | 1985-86 renter credit cutoff (hy offset), 2020 unemployment/aged low-income marginal rates, 2021 childless aged EITC and unemployment |
| AR | 18 | 2018-23 itemized (own rules), 2021 five cases, 1984 three cases |
| KS | 54 | 2018-23 itemizers and very high income (SALT limit rules), 2007-12 homestead/renter, 1977-81 two a year |
| ME | 39 | 2014-21 property tax fairness credit / renter cases, 2020 childless EITC 18-24 |
| ID | 46 | dependent filers 2010-23, 2018-23 high-income itemizers (own deduction rules) |
| WV | 91 | 2022-23 family tax credit phase-out (smooth), 2007-2021 one to five cases a year, 2020-21 ten |
| OH | 64 | 2017+ business income and non-property cases (own routines), 2021 pennies (rate rounding), 1977-78 two a year |
| MI | 41 | 1977:1, 1978:2, 1979:2, 1980:2, 1981:2, 1982:2, 1983:2, 1984:2, 1985:2, 1986:2, 1987:2, 1988:2, 1989:2, 1990:2, 1991:2, 1992:2, 1993:2, 1994:2, 1995:2, 1996:... |
| GA | 84 | aged cases 1989-2020 depend on uninitialised ceilings (record order), 1982-85 five a year, 2022-23 |
| DC | 6 | 2018-23 dependent filer with interest |
| MN | 39 | 1977-84 (federal pre-1982 gaps) 2-4 a year, 2022-23 eight |
| VA | 51 | 1989:1, 1990:1, 1991:1, 1992:1, 1993:1, 1994:1, 1995:1, 1996:1, 1997:1, 1998:1, 1999:1, 2019:3, 2020:3, 2021:6, 2022:11, 2023:17 |
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
