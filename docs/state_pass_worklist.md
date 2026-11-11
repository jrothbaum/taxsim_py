# State pass worklist (TAXSIM mode vs the 2026 build)

Counts are failures from `scripts/validate_states.py` (cases that differ from `taxsim2026_mtr.exe`). Re-run with `TAXSIM_VALIDATE_DETAIL=0 uv run python scripts/validate_states.py XX`.

Not zero means unfinished: each state below needs another pass.

| State | Failures | Years with failures (count) |
| --- | ---: | --- |
| RI | 603 | 1977:2, 1978:2, 1979:2, 1980:2, 1981:2, 2011:10, 2012:5, 2013:5, 2014:3, 2015:3, 2016:83, 2017:5, 2018:82, 2019:82, 2020:81, 2021:81, 2022:78, 2023:75 |
| NC | 0 | done |
| AZ | 493 | 1977:8, 1978:8, 1979:9, 1980:9, 1981:9, 1982:10, 1983:9, 1984:9, 1985:10, 1986:10, 1987:10, 1988:10, 1989:10, 1990:3, 1991:13, 1992:12, 1993:12, 1994:12, 199... |
| MO | 146 | 2–5 per year; open: aged property-tax credit / renter cases (1990, 2008), high-income itemizers 2008 |
| NM | 0 | done |
| DE | 0 | done |
| VT | 0 | done |
| MT | 23 | 1977-81 professional/S corporation and non-property cases, 1984-86 unemployment couple, 2014-17 childcare couple at $90,000 (marginal rate), 2022-23 aged renter and aged couple with low wages |
| MD | 4 | 2020 aged single and 2022-23 aged couple with low wages (poverty/EITC interplay), 2023 low income single |
| OK | 50 | 1977-86 dividend/interest/gain and non-property cases (federal pre-1982 gaps), 2021-23 aged low-income marginal rate |
| MA | 39 | 1977-80 self-employment, 2020 capital gains (rounding of the exempt amount), 2022 pennies on very high incomes |
| WI | 130 | 2022-23 (own routine: separate returns, childcare, itemized, homestead), 1988-97 separate returns by $1.80, 2000-2016 one or two cases a year |
| IN | 20 | 1977-80 aged low-income (property/renter credits), 2022-23 aged low-income and 2023 joint |
| CA | 245 | 1987:3, 1988:3, 1989:4, 1990:5, 1991:1, 1992:1, 1993:2, 1994:2, 1995:2, 1996:1, 1997:1, 1998:4, 1999:4, 2000:2, 2001:2, 2002:2, 2003:2, 2004:2, 2005:2, 2006:... |
| UT | 240 | 1977:2, 1978:2, 1979:2, 1980:2, 1981:2, 1988:10, 1993:3, 1994:3, 1995:3, 1996:3, 1997:3, 1998:3, 1999:3, 2000:3, 2001:2, 2002:2, 2003:2, 2004:2, 2005:2, 2006... |
| NY | 237 | 1978:2, 1979:2, 1980:2, 1981:2, 1982:2, 1983:2, 1984:2, 1985:4, 1986:4, 1987:2, 1988:2, 1989:2, 1990:2, 1991:2, 1992:2, 1993:2, 1994:2, 1995:2, 1996:2, 1997:... |
| AR | 203 | 1977:2, 1978:2, 1979:2, 1980:2, 1981:2, 1982:2, 1983:2, 1984:5, 1985:4, 1986:4, 1987:3, 1988:3, 1989:3, 1990:5, 1991:5, 1992:5, 1993:5, 1994:5, 1995:5, 1996:... |
| KS | 188 | 1977:2, 1978:2, 1979:2, 1980:2, 1981:2, 1986:1, 1987:1, 1988:1, 1989:1, 1990:1, 1991:1, 1992:1, 1993:1, 1994:1, 1995:1, 1996:1, 1997:1, 1998:6, 1999:6, 2000:... |
| ME | 178 | 1987:1, 1988:82, 1989:2, 1990:2, 1991:2, 1992:2, 1993:2, 1994:2, 1995:2, 1996:2, 1997:2, 1998:2, 1999:2, 2000:2, 2001:2, 2002:2, 2003:2, 2004:2, 2005:2, 2006... |
| ID | 176 | 1978:2, 1979:2, 1980:2, 1981:2, 1982:2, 1983:2, 1984:2, 1985:2, 1986:2, 1987:2, 1988:2, 1989:2, 1990:2, 1991:2, 1992:2, 1993:2, 1994:2, 1995:2, 1996:2, 1997:... |
| WV | 174 | 1984:15, 1985:15, 1986:15, 2007:2, 2008:3, 2009:3, 2010:3, 2011:5, 2012:2, 2013:2, 2014:2, 2015:2, 2016:2, 2017:1, 2018:1, 2019:3, 2020:10, 2021:9, 2022:40, ... |
| OH | 137 | 1977:4, 1978:4, 1979:2, 1980:2, 1981:2, 1982:2, 1983:1, 1984:1, 1985:1, 1986:1, 1987:1, 1988:1, 1989:1, 1990:1, 1991:1, 1992:1, 1993:1, 1994:1, 1995:1, 1996:... |
| MI | 41 | 1977:1, 1978:2, 1979:2, 1980:2, 1981:2, 1982:2, 1983:2, 1984:2, 1985:2, 1986:2, 1987:2, 1988:2, 1989:2, 1990:2, 1991:2, 1992:2, 1993:2, 1994:2, 1995:2, 1996:... |
| GA | 84 | 1982:5, 1983:5, 1984:5, 1985:5, 1986:1, 1987:1, 1988:1, 1989:2, 1990:2, 1991:2, 1992:2, 1993:2, 1994:2, 1995:2, 1996:2, 1997:2, 1998:2, 1999:2, 2000:2, 2001:... |
| DC | 58 |  |
| MN | 57 | 1977:2, 1978:4, 1979:4, 1980:4, 1981:4, 1982:2, 1983:2, 1984:2, 1985:10, 1986:10, 2009:1, 2018:1, 2019:1, 2020:1, 2021:1, 2022:3, 2023:5 |
| VA | 51 | 1989:1, 1990:1, 1991:1, 1992:1, 1993:1, 1994:1, 1995:1, 1996:1, 1997:1, 1998:1, 1999:1, 2019:3, 2020:3, 2021:6, 2022:11, 2023:17 |
| PA | 47 | 1977:1, 1978:1, 1979:1, 1980:1, 1981:1, 1982:1, 1983:1, 1984:1, 1985:1, 1986:1, 1987:1, 1988:1, 1989:1, 1990:1, 1991:1, 1992:1, 1993:1, 1994:1, 1995:1, 1996:... |
| ND | 32 | 2007:1, 2008:1, 2009:1, 2010:1, 2011:1, 2012:1, 2013:1, 2014:1, 2015:1, 2016:1, 2017:1, 2018:1, 2019:1, 2020:1, 2021:1, 2022:2, 2023:15 |
| SC | 26 | 1985:5, 1986:5, 1987:5, 1988:5, 1989:5, 2021:1 |
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
