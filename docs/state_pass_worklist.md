# State pass worklist (TAXSIM mode vs the 2026 build)

Counts are failures from `scripts/validate_states.py` (cases that differ from `taxsim2026_mtr.exe`). Re-run with `TAXSIM_VALIDATE_DETAIL=0 uv run python scripts/validate_states.py XX`.

Not zero means unfinished: each state below needs another pass.

Counts are from one batched run of every state (`VALIDATE_OUT=<dir> TAXSIM_VALIDATE_DETAIL=0 uv run python scripts/validate_states.py`, about two minutes; it saves `cases.parquet`, `results.parquet` and `failures.parquet`). States not listed match exactly.

| State | 1977-2023 | 2024-2026 |
| --- | ---: | ---: |
| NY | 16 | 248 |
| WI | 16 | 195 |
| RI | 24 | 193 |
| KS | 16 | 169 |
| VT | 0 | 162 |
| ND | 20 | 159 |
| MN | 32 | 126 |
| MT | 23 | 116 |
| NE | 0 | 100 |
| UT | 80 | 89 |
| MI | 23 | 75 |
| NJ | 0 | 70 |
| DC | 6 | 64 |
| OK | 11 | 62 |
| CO | 4 | 49 |
| SC | 1 | 40 |
| VA | 31 | 37 |
| MA | 39 | 36 |
| ID | 5 | 36 |
| AL | 10 | 34 |
| PA | 0 | 32 |
| AR | 18 | 31 |
| ME | 11 | 28 |
| WV | 0 | 26 |
| GA | 84 | 23 |
| IN | 20 | 22 |
| MO | 21 | 21 |
| HI | 0 | 19 |
| DE | 0 | 16 |
| NM | 0 | 15 |
| MD | 4 | 15 |
| CA | 12 | 14 |
| IL | 1 | 12 |
| OR | 0 | 10 |
| CT | 8 | 6 |
| IA | 10 | 5 |
| OH | 0 | 4 |
| LA | 4 | 2 |
| KY | 10 | 2 |
| AZ | 10 | 2 |
| MS | 12 | 1 |

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
