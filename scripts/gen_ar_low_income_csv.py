"""One-off generator for parameters/states/ar/low_income_table.csv, transcribed
directly from artax's own literal if/elseif chains (taxsim_2022_10_21.f:
~1595-1770 for 1991-2021; the pre-1991 four-formula table is small/stable
enough to hardcode directly in calculators/states/ar.py instead).

Each row is one linear piece: for lower_bound < agi <= upper_bound (or
lower_bound <= agi <= upper_bound when year>=2017 - a real, literal
boundary-style shift baked into the source itself, applied uniformly
across every status/tier that year and later), tax = base + rate*(agi -
formula_offset). `formula_offset` sometimes genuinely differs from
`lower_bound` (a handful of real off-by-some-dollars quirks in the source
itself, e.g. 2012 married dep<=1's `le.18992`/`gt.18922`) - preserved
exactly as found, not "corrected". `upper_exclusive` is true only for the
one single 1991-1997 row that uses `agi.lt.11400` instead of `.le.`.

Rows only cover mst in {1 (single), 2 (married_joint), 4 (head_of_household)}
- married_separate (mst=6) is excluded from this whole mechanism at the
source level (`if(sep.eq.1.and.law.ge.1991)`, sep=2 for mst=6).
"""

import csv

ROWS: list[dict] = []


def add(year, status, dep_tier, tier_order, lower, upper, offset, base, rate, upper_exclusive=False):
    ROWS.append(
        dict(
            year=year, status=status, dep_tier=dep_tier, tier_order=tier_order,
            lower_bound=lower, upper_bound=upper, formula_offset=offset, base=base, rate=rate,
            upper_exclusive=int(upper_exclusive),
        )
    )


# ---- single (mst=1), never dep-split ----
SINGLE_FLAT_1991_1997 = [(5500, 8400, 5500, 26, 0.01), (8400, 11400, 8400, 106, 0.02, True)]
for y in range(1991, 1998):
    for i, piece in enumerate(SINGLE_FLAT_1991_1997, start=1):
        lower, upper, offset, base, rate = piece[:5]
        excl = piece[5] if len(piece) > 5 else False
        add(y, "single", 0, i, lower, upper, offset, base, rate, excl)

SINGLE_BY_YEAR = {
    1998: [(7800, 11400, 7800, 21, 0.034)],
    1999: [(7800, 11400, 7800, 21, 0.034)],
    2000: [(7800, 11400, 7800, 21, 0.034)],
    2001: [(7800, 11400, 7800, 21, 0.034)],
    2002: [(7800, 11400, 7800, 21, 0.034)],
    2003: [(7800, 11400, 7800, 21, 0.034)],
    2004: [(7800, 11400, 7800, 21, 0.034)],
    2005: [(7800, 11400, 7800, 21, 0.034)],
    2006: [(7800, 11400, 7800, 21, 0.034)],
    2007: [(10200, 13500, 10200, 29, 0.073939394)],
    2008: [(10506, 13900, 10506, 33, 0.07424867)],
    2009: [(10526, 13800, 10526, 32, 0.068)],
    2010: [(10681, 14000, 10681, 33, 0.075)],
    2011: [(10940, 14400, 10940, 35, 0.075)],
    2012: [(11220, 14800, 11220, 36, 0.075)],
    2013: [(11410, 15100, 11410, 37, 0.075)],
    2014: [(11591, 15200, 11591, 36, 0.076)],
    2015: [(11643, 15100, 11643, 35, 0.073)],
    2016: [(11736, 15200, 11736, 35, 0.073)],
    2017: [(11970, 15500, 11970, 36, 0.07365)],
    2018: [(12260, 15900, 12260, 37, 0.0739)],
    2019: [(12492, 14900, 12492, 25, 0.0692)],
    2020: [(12675, 15200, 12675, 26, 0.0693)],
    2021: [(13054, 15700, 13054, 27, 0.0688)],
}
for y, pieces in SINGLE_BY_YEAR.items():
    for i, (lower, upper, offset, base, rate) in enumerate(pieces, start=1):
        add(y, "single", 0, i, lower, upper, offset, base, rate)

# ---- married_joint (mst=2), flat through 2006, dep-split 2007+ ----
MARRIED_FLAT_1991_1997 = [(10000, 13000, 10000, 71, 0.01467), (13000, 16200, 13000, 230, 0.03)]
for y in range(1991, 1998):
    for i, (lower, upper, offset, base, rate) in enumerate(MARRIED_FLAT_1991_1997, start=1):
        add(y, "married_joint", 0, i, lower, upper, offset, base, rate)

MARRIED_FLAT_1998_2006 = [(15500, 16000, 15500, 78.5, 0.015), (16000, 16200, 16000, 114, 0.02)]
for y in range(1998, 2007):
    for i, (lower, upper, offset, base, rate) in enumerate(MARRIED_FLAT_1998_2006, start=1):
        add(y, "married_joint", 0, i, lower, upper, offset, base, rate)

# dep<=1: (lower, upper, offset, base, rate); dep>=2: same shape
MARRIED_SPLIT_BY_YEAR = {
    2007: {1: (17200, 21400, 17200, 66, 0.1121412857), 2: (20700, 26700, 20700, 97, 0.123333)},
    2008: {1: (17716, 22000, 17716, 74, 0.112745098), 2: (21321, 27400, 21321, 107, 0.124198)},
    2009: {1: (17749, 21900, 17749, 73, 0.113), 2: (21362, 27400, 21362, 107, 0.1235)},
    2010: {1: (18011, 22400, 18011, 76, 0.11255), 2: (21676, 27800, 21676, 108, 0.124755)},
    2011: {1: (18448, 22400, 18448, 78, 0.1137), 2: (22202, 27800, 22202, 112, 0.1243)},
    2012: {1: (18992, 23600, 18922, 81, 0.113), 2: (22773, 29400, 22773, 116, 0.1257)},
    2013: {1: (19242, 24000, 19243, 83, 0.115), 2: (23159, 29900, 23159, 118, 0.115)},
    2014: {1: (19547, 24400, 19547, 84, 0.114), 2: (23525, 30400, 23525, 120, 0.125)},
    2015: {1: (19635, 24200, 19635, 79, 0.112), 2: (23631, 30200, 23631, 114, 0.1267)},
    2016: {1: (19793, 24300, 19793, 80, 0.1138), 2: (23821, 30500, 23821, 116, 0.12)},
    2017: {1: (20187, 24800, 20187, 82, 0.1136), 2: (24295, 31000, 24295, 118, 0.121)},
    2018: {1: (20675, 25500, 20675, 85, 0.11337), 2: (24882, 31800, 24882, 122, 0.1208)},
    2019: {1: (21067, 24800, 21067, 66, 0.102), 2: (25355, 30800, 25355, 95, 0.1465)},
    2020: {1: (21375, 25200, 21375, 67, 0.1033), 2: (25726, 31300, 25726, 97, 0.1464)},
    2021: {1: (22016, 26100, 22016, 70, 0.1018), 2: (26496, 32200, 26496, 100, 0.148)},
}
for y, tiers in MARRIED_SPLIT_BY_YEAR.items():
    for dep_tier, (lower, upper, offset, base, rate) in tiers.items():
        add(y, "married_joint", dep_tier, 1, lower, upper, offset, base, rate)

# ---- head_of_household (mst=4 or 7), flat through 2010, dep-split 2011+ ----
HOH_FLAT_1991_1997 = [(7200, 11600, 7400, 42, 0.0119), (11600, 16200, 11600, 189, 0.03)]
for y in range(1991, 1998):
    for i, (lower, upper, offset, base, rate) in enumerate(HOH_FLAT_1991_1997, start=1):
        add(y, "head_of_household", 0, i, lower, upper, offset, base, rate)

HOH_FLAT_1998_2006 = [
    (12100, 13000, 12100, 41, 0.01),
    (13000, 15200, 13000, 82.5, 0.015),
    (15200, 16100, 15200, 200.5, 0.025),
]
for y in range(1998, 2007):
    for i, (lower, upper, offset, base, rate) in enumerate(HOH_FLAT_1998_2006, start=1):
        add(y, "head_of_household", 0, i, lower, upper, offset, base, rate)

HOH_FLAT_2007_2010 = {
    2007: (14500, 19000, 14500, 59, 0.102666),
    2008: (14935, 19400, 14935, 67, 0.103471),
    2009: (14963, 19300, 14963, 66, 0.104),
    2010: (15184, 19600, 15184, 67, 0.1046),
}
for y, (lower, upper, offset, base, rate) in HOH_FLAT_2007_2010.items():
    add(y, "head_of_household", 0, 1, lower, upper, offset, base, rate)

HOH_SPLIT_BY_YEAR = {
    2011: {1: (15552, 20200, 15552, 70, 0.104), 2: (18539, 22900, 18540, 97, 0.1365)},
    2012: {1: (15952, 20800, 15952, 72, 0.104), 2: (19016, 23500, 19016, 100, 0.136)},
    2013: {1: (16223, 21200, 16223, 74, 0.105), 2: (19338, 23900, 19338, 102, 0.135)},
    2014: {1: (16479, 21400, 16479, 74, 0.104), 2: (19644, 24200, 19644, 103, 0.137)},
    2015: {1: (16553, 21300, 16553, 71, 0.103), 2: (19733, 24200, 19733, 99, 0.139)},
    2016: {1: (16686, 21400, 16686, 72, 0.105), 2: (19891, 24300, 19891, 100, 0.139)},
    2017: {1: (17019, 22000, 17019, 74, 0.1024), 2: (20287, 24800, 20287, 102, 0.139)},
    2018: {1: (17430, 22500, 17430, 76, 0.1026), 2: (20777, 25400, 20777, 105, 0.138)},
    2019: {1: (17761, 21600, 17761, 58, 0.093), 2: (21172, 24800, 21172, 81, 0.123)},
    2020: {1: (18021, 22000, 18021, 59, 0.0922), 2: (21482, 25100, 21482, 83, 0.123)},
    2021: {1: (18561, 22600, 18561, 62, 0.093), 2: (22125, 26000, 22125, 86, 0.17)},
}
for y, tiers in HOH_SPLIT_BY_YEAR.items():
    for dep_tier, (lower, upper, offset, base, rate) in tiers.items():
        add(y, "head_of_household", dep_tier, 1, lower, upper, offset, base, rate)

OUT = "/home/jrothbaum/Coding/claude_code/taxsim_py/parameters/states/ar/low_income_table.csv"
fieldnames = [
    "year", "status", "dep_tier", "tier_order", "lower_bound", "upper_bound",
    "formula_offset", "base", "rate", "upper_exclusive",
]
with open(OUT, "w", newline="") as f:
    w = csv.DictWriter(f, fieldnames=fieldnames)
    w.writeheader()
    for row in ROWS:
        w.writerow(row)

print(f"wrote {len(ROWS)} rows to {OUT}")
