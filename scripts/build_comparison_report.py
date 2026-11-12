"""Compare taxsim-py with TAXSIM and PolicyEngine-US on CPS ASEC tax units, for every state and year.

usage:
  uv run --group test python scripts/build_comparison_report.py PATH/TO/cps_2011 \
      [--units-per-state 200] [--pe-units-per-state 40] [--years 1960-2026] [--out docs]

One CPS ASEC year supplies the tax units (people, incomes, dependents, state). For each law year the
incomes are scaled by the consumer price index from the CPS income year, so the same households face every
year's law at that year's price level. Each (state, year) is then run through

* the TAXSIM executable (`taxsim2026_mtr.exe`),
* taxsim-py in TAXSIM mode (the Python reference engine), and
* taxsim-py in statutory mode (the Rust engine),

and, for 2022 onward, through PolicyEngine-US on a smaller sample. The script writes the per-state-year
agreement shares to `docs/data/`, heatmaps to `docs/img/`, and the page `docs/comparisons.md`.
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import polars as pl  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

from oracle import run_oracle  # noqa: E402
from taxsim_py import calculate_taxes  # noqa: E402
from taxsim_py.calculators.states import STATE_CALCULATOR_PATHS  # noqa: E402
from taxsim_py.engine.inputs import TAXSIM_INPUTS  # noqa: E402
from taxsim_py.prep import cps_asec_tax_units, read_cps_asec  # noqa: E402

FIRST_STATE_YEAR = 1977
TOLERANCE = 1.0  # dollars
EXACT = 0.02  # dollars: TAXSIM's own round-off

# Consumer price index for all urban consumers, annual averages (BLS, 1982-84 = 100). 2025 and 2026 are
# extrapolated at 2.7% and 2.5% a year, which only sets the scale of incomes in those years.
CPI = {
    1960: 29.6, 1961: 29.9, 1962: 30.2, 1963: 30.6, 1964: 31.0, 1965: 31.5, 1966: 32.4, 1967: 33.4, 1968: 34.8,
    1969: 36.7, 1970: 38.8, 1971: 40.5, 1972: 41.8, 1973: 44.4, 1974: 49.3, 1975: 53.8, 1976: 56.9, 1977: 60.6,
    1978: 65.2, 1979: 72.6, 1980: 82.4, 1981: 90.9, 1982: 96.5, 1983: 99.6, 1984: 103.9, 1985: 107.6, 1986: 109.6,
    1987: 113.6, 1988: 118.3, 1989: 124.0, 1990: 130.7, 1991: 136.2, 1992: 140.3, 1993: 144.5, 1994: 148.2,
    1995: 152.4, 1996: 156.9, 1997: 160.5, 1998: 163.0, 1999: 166.6, 2000: 172.2, 2001: 177.1, 2002: 179.9,
    2003: 184.0, 2004: 188.9, 2005: 195.3, 2006: 201.6, 2007: 207.3, 2008: 215.3, 2009: 214.5, 2010: 218.1,
    2011: 224.9, 2012: 229.6, 2013: 233.0, 2014: 236.7, 2015: 237.0, 2016: 240.0, 2017: 245.1, 2018: 251.1,
    2019: 255.7, 2020: 258.8, 2021: 271.0, 2022: 292.7, 2023: 304.7, 2024: 313.7,
}
CPI[2025] = round(CPI[2024] * 1.027, 1)
CPI[2026] = round(CPI[2025] * 1.025, 1)

DOLLAR_INPUTS = (
    "pwages", "swages", "psemp", "ssemp", "dividends", "intrec", "stcg", "ltcg", "otherprop", "nonprop",
    "pensions", "gssi", "ui", "pui", "sui", "transfers", "proptax", "rentpaid", "otheritem", "childcare",
    "mortgage", "scorp", "pbusinc", "pprofinc", "sbusinc", "sprofinc",
)
STATE_NAME = {
    1: "AL", 2: "AK", 3: "AZ", 4: "AR", 5: "CA", 6: "CO", 7: "CT", 8: "DE", 9: "DC", 10: "FL", 11: "GA", 12: "HI",
    13: "ID", 14: "IL", 15: "IN", 16: "IA", 17: "KS", 18: "KY", 19: "LA", 20: "ME", 21: "MD", 22: "MA", 23: "MI",
    24: "MN", 25: "MS", 26: "MO", 27: "MT", 28: "NE", 29: "NV", 30: "NH", 31: "NJ", 32: "NM", 33: "NY", 34: "NC",
    35: "ND", 36: "OH", 37: "OK", 38: "OR", 39: "PA", 40: "RI", 41: "SC", 42: "SD", 43: "TN", 44: "TX", 45: "UT",
    46: "VT", 47: "VA", 48: "WA", 49: "WV", 50: "WI", 51: "WY",
}
NO_INCOME_TAX = {code for code, (module, _) in STATE_CALCULATOR_PATHS.items() if module == "no_income_tax" and code}
NO_INCOME_TAX |= {2, 30, 43, 48}  # modules with a parameter file but no wage tax


def sample_units(units: pl.DataFrame, per_state: int, seed: int) -> pl.DataFrame:
    """The same `per_state` units for each state, drawn at random."""
    parts = []
    for state, group in units.group_by("state", maintain_order=True):
        parts.append(group.sample(n=min(per_state, group.height), seed=seed + int(state[0])))
    return pl.concat(parts)


def for_year(units: pl.DataFrame, year: int, base_year: int) -> pl.DataFrame:
    """The units with dollar inputs at `year` prices, taxed under `year` law (federal only before state law)."""
    scale = CPI[year] / CPI[base_year]
    frame = units.with_columns(
        [(pl.col(c) * scale).round(0) for c in DOLLAR_INPUTS if c in units.columns], year=pl.lit(year, dtype=pl.Int64)
    )
    if year < FIRST_STATE_YEAR:
        frame = frame.with_columns(state=pl.lit(0, dtype=frame["state"].dtype))
    return frame


def share(diff: pl.Expr, tolerance: float) -> pl.Expr:
    return (diff.abs() <= tolerance).cast(pl.Float64).mean()


def compare_with_taxsim(units: pl.DataFrame, years: list[int], base_year: int) -> pl.DataFrame:
    """Per state and year: share of units whose tax is within tolerance of TAXSIM's, in both modes.

    All years are stacked into one call per engine; the Python engine's fixed cost per call would otherwise
    dominate.
    """
    keep = [c for c in units.columns if c in TAXSIM_INPUTS or c in ("taxsimid",)]
    started = time.time()
    stacked = pl.concat([for_year(units, year, base_year).select(keep) for year in years])
    cases = stacked.with_row_index("row", offset=1).with_columns(taxsimid=pl.col("row").cast(pl.Int64)).drop("row")
    print(f"  {cases.height:,} unit-years built in {time.time() - started:.0f}s", flush=True)

    started = time.time()
    # TAXSIM's results for a record depend on the records that ran before it (variables left over from another
    # state's routine), so each state and year runs on its own, as the validators do.
    groups = cases.partition_by(["state", "year"], maintain_order=True)
    oracle = pl.concat(
        [run_oracle(group).select("taxsimid", pl.col("fiitax").alias("o_fi"), pl.col("siitax").alias("o_si")) for group in groups]
    )
    print(f"  TAXSIM executable: {time.time() - started:.0f}s for {len(groups):,} state-years", flush=True)
    started = time.time()
    taxsim_mode = calculate_taxes(cases, calculation_mode="taxsim", engine="python").select(
        "taxsimid", pl.col("fiitax").alias("t_fi"), pl.col("siitax").alias("t_si")
    )
    print(f"  taxsim-py, TAXSIM mode: {time.time() - started:.0f}s", flush=True)
    started = time.time()
    statutory = calculate_taxes(cases, calculation_mode="statutory", engine="rust").select(
        "taxsimid", pl.col("fiitax").alias("s_fi"), pl.col("siitax").alias("s_si")
    )
    print(f"  taxsim-py, statutory mode: {time.time() - started:.0f}s", flush=True)

    joined = cases.select("taxsimid", "state", "year").join(oracle, on="taxsimid").join(taxsim_mode, on="taxsimid").join(
        statutory, on="taxsimid"
    )
    return (
        joined.group_by("state", "year")
        .agg(
            pl.len().alias("n"),
            share(pl.col("t_si") - pl.col("o_si"), EXACT).alias("taxsim_state_exact"),
            share(pl.col("t_si") - pl.col("o_si"), TOLERANCE).alias("taxsim_state"),
            share(pl.col("s_si") - pl.col("o_si"), TOLERANCE).alias("statutory_state"),
            share(pl.col("t_fi") - pl.col("o_fi"), EXACT).alias("taxsim_federal_exact"),
            share(pl.col("t_fi") - pl.col("o_fi"), TOLERANCE).alias("taxsim_federal"),
            share(pl.col("s_fi") - pl.col("o_fi"), TOLERANCE).alias("statutory_federal"),
        )
        .sort("state", "year")
    )


def compare_with_policyengine(units: pl.DataFrame, years: list[int], base_year: int) -> pl.DataFrame:
    """Per state and year: share of units whose statutory state tax is within tolerance of PolicyEngine's.

    Only units whose federal tax agrees within the tolerance count, so the comparison is of state logic.
    """
    from taxsim_py.validation.independent import run_policyengine

    keep = [c for c in units.columns if c in TAXSIM_INPUTS or c in ("taxsimid",)]
    rows = []
    for year in years:
        started = time.time()
        frame = for_year(units, year, base_year).select(keep)
        frame = frame.filter(pl.col("state") > 0).with_row_index("row", offset=1)
        cases = frame.drop("row").with_columns(taxsimid=frame["row"].cast(pl.Int64))
        statutory = calculate_taxes(cases, calculation_mode="statutory", engine="rust").select(
            "taxsimid", pl.col("fiitax").alias("s_fi"), pl.col("siitax").alias("s_si")
        )
        try:
            pe = run_policyengine(cases)
        except Exception as error:  # a year PolicyEngine cannot run is left out of the table
            print(f"  PolicyEngine {year}: {type(error).__name__}: {str(error)[:100]}", flush=True)
            continue
        pe = pe.select("taxsimid", pl.col("fiitax").alias("p_fi"), pl.col("siitax").alias("p_si"))
        joined = cases.select("taxsimid", "state").join(statutory, on="taxsimid").join(pe, on="taxsimid")
        joined = joined.filter((pl.col("s_fi") - pl.col("p_fi")).abs() <= TOLERANCE)
        rows.append(
            joined.group_by("state")
            .agg(pl.len().alias("n_pe"), share(pl.col("s_si") - pl.col("p_si"), TOLERANCE).alias("policyengine_state"))
            .with_columns(year=pl.lit(year, dtype=pl.Int64))
        )
        print(f"  PolicyEngine {year}: {cases.height} units in {time.time() - started:.1f}s", flush=True)
    return pl.concat(rows).sort("state", "year") if rows else pl.DataFrame()


def heatmap(table: pl.DataFrame, column: str, title: str, path: Path, first_year: int) -> None:
    """States by years, shaded by the share of units that agree."""
    states = [s for s in sorted(STATE_NAME) if s not in NO_INCOME_TAX]
    years = sorted(y for y in table["year"].unique().to_list() if y >= first_year)
    grid = np.full((len(states), len(years)), np.nan)
    for row in table.filter(pl.col("year") >= first_year).iter_rows(named=True):
        if row["state"] in states and row[column] is not None:
            grid[states.index(row["state"]), years.index(row["year"])] = 100.0 * row[column]
    figure, axis = plt.subplots(figsize=(13, 9))
    colors = matplotlib.colormaps["RdYlGn"].copy()
    colors.set_bad("#dddddd")
    image = axis.imshow(grid, aspect="auto", cmap=colors, vmin=0, vmax=100, interpolation="nearest")
    step = 1 if len(years) <= 12 else 5
    axis.set_xticks(range(0, len(years), step), [years[i] for i in range(0, len(years), step)], rotation=90)
    axis.set_yticks(range(len(states)), [STATE_NAME[s] for s in states], fontsize=7)
    axis.set_title(title)
    figure.colorbar(image, ax=axis, label="% of households within $1")
    figure.tight_layout()
    path.parent.mkdir(parents=True, exist_ok=True)
    figure.savefig(path, dpi=110)
    plt.close(figure)


PERIODS = ((1977, 1989), (1990, 1999), (2000, 2009), (2010, 2019), (2020, 2026))


def period_table(table: pl.DataFrame, columns: dict[str, str]) -> str:
    """A markdown table of state by period averages, in percent."""
    heads = [f"{a}-{b}" for a, b in PERIODS]
    lines = ["| State | " + " | ".join(f"{label} {h}" for label in columns.values() for h in heads) + " |"]
    lines.append("|:--|" + "|".join(":--:" for _ in range(len(columns) * len(heads))) + "|")
    for state in [s for s in sorted(STATE_NAME) if s not in NO_INCOME_TAX]:
        cells = []
        sub = table.filter(pl.col("state") == state)
        for column in columns:
            for a, b in PERIODS:
                values = sub.filter(pl.col("year").is_between(a, b))[column].drop_nulls()
                cells.append("" if values.len() == 0 else f"{100 * values.mean():.0f}")
        lines.append(f"| {STATE_NAME[state]} | " + " | ".join(cells) + " |")
    return "\n".join(lines)


def year_table(table: pl.DataFrame, column: str) -> str:
    """A markdown table of state by year, in percent."""
    years = sorted(table["year"].unique().to_list())
    lines = ["| State | " + " | ".join(str(y) for y in years) + " |", "|:--|" + "|".join(":--:" for _ in years) + "|"]
    for state in [s for s in sorted(STATE_NAME) if s not in NO_INCOME_TAX]:
        sub = table.filter(pl.col("state") == state)
        cells = []
        for year in years:
            value = sub.filter(pl.col("year") == year)[column]
            cells.append("" if value.len() == 0 or value[0] is None else f"{100 * value[0]:.0f}")
        lines.append(f"| {STATE_NAME[state]} | " + " | ".join(cells) + " |")
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("directory", type=Path, help="CPS ASEC cache with person.parquet and hhld.parquet")
    parser.add_argument("--units-per-state", type=int, default=200)
    parser.add_argument("--pe-units-per-state", type=int, default=40)
    parser.add_argument("--years", default="1960-2026")
    parser.add_argument("--pe-years", default="2022-2026")
    parser.add_argument("--out", type=Path, default=ROOT / "docs")
    parser.add_argument("--seed", type=int, default=2011)
    args = parser.parse_args()
    os.environ.setdefault("POLARS_MAX_THREADS", "8")

    first, last = (int(x) for x in args.years.split("-"))
    pe_first, pe_last = (int(x) for x in args.pe_years.split("-"))
    files = read_cps_asec(args.directory)
    units, _ = cps_asec_tax_units(files["person"], files["hhld"], include_nonfilers=False)
    base_year = int(units["year"][0])
    print(f"{units.height} CPS tax units, income year {base_year}", flush=True)
    units = units.drop("year").with_columns(year=pl.lit(base_year, dtype=pl.Int64))
    sample = sample_units(units, args.units_per_state, args.seed)
    pe_sample = sample_units(units, args.pe_units_per_state, args.seed + 1)

    print("TAXSIM comparison", flush=True)
    taxsim = compare_with_taxsim(sample, list(range(first, last + 1)), base_year)
    print("PolicyEngine comparison", flush=True)
    policy = compare_with_policyengine(pe_sample, list(range(pe_first, pe_last + 1)), base_year)

    data = args.out / "data"
    data.mkdir(parents=True, exist_ok=True)
    taxsim.write_csv(data / "comparison_taxsim.csv")
    if policy.height:
        policy.write_csv(data / "comparison_policyengine.csv")
    images = args.out / "img"
    heatmap(taxsim, "taxsim_state", "State tax, taxsim-py in TAXSIM mode vs TAXSIM", images / "compare_taxsim_mode.png", FIRST_STATE_YEAR)
    heatmap(taxsim, "statutory_state", "State tax, taxsim-py in statutory mode vs TAXSIM", images / "compare_statutory.png", FIRST_STATE_YEAR)
    if policy.height:
        heatmap(policy, "policyengine_state", "State tax, taxsim-py in statutory mode vs PolicyEngine-US", images / "compare_policyengine.png", pe_first)
    write_page(args, taxsim, policy, base_year, first, last)
    print(f"wrote {args.out / 'comparisons.md'}", flush=True)


def write_page(args, taxsim: pl.DataFrame, policy: pl.DataFrame, base_year: int, first: int, last: int) -> None:
    federal = (
        taxsim.group_by("year")
        .agg(
            pl.col("taxsim_federal_exact").mean(), pl.col("taxsim_federal").mean(), pl.col("statutory_federal").mean(),
        )
        .sort("year")
    )
    fed_lines = ["| Years | TAXSIM mode within 2 cents | TAXSIM mode within $1 | Statutory within $1 |", "|:--|:--:|:--:|:--:|"]
    for a, b in ((1960, 1976),) + PERIODS:
        sub = federal.filter(pl.col("year").is_between(a, b))
        if sub.height:
            fed_lines.append(
                f"| {a}-{b} | {100 * sub['taxsim_federal_exact'].mean():.1f}% | {100 * sub['taxsim_federal'].mean():.1f}%"
                f" | {100 * sub['statutory_federal'].mean():.1f}% |"
            )
    state_only = taxsim.filter(~pl.col("state").is_in(list(NO_INCOME_TAX)) & (pl.col("year") >= FIRST_STATE_YEAR))
    overall = lambda column: 100 * state_only[column].mean()  # noqa: E731
    worst = (
        state_only.filter(pl.col("taxsim_state") < 0.95)
        .sort("taxsim_state")
        .head(15)
        .select("state", "year", "taxsim_state")
    )
    worst_lines = ["| State | Year | Share within $1 |", "|:--|:--:|:--:|"] + [
        f"| {STATE_NAME[r['state']]} | {r['year']} | {100 * r['taxsim_state']:.0f}% |" for r in worst.iter_rows(named=True)
    ]
    page = f"""# Comparison with TAXSIM and PolicyEngine-US

This page is generated by `scripts/build_comparison_report.py`. It compares taxsim-py with the TAXSIM
executable and with PolicyEngine-US on real households, for every state and year.

## Households

The households are the {args.units_per_state} randomly drawn CPS ASEC tax units per state from the {base_year + 1}
public-use file (income year {base_year}), {args.units_per_state * 51:,} units in all, built with
`taxsim_py.prep.cps_asec_tax_units`. For each law year from {first} to {last} the same units are run with every
dollar input multiplied by the ratio of that year's consumer price index to {base_year}'s (2025 and 2026 use assumed
2.7% and 2.5% inflation), so each year sees a realistic mix at that year's prices. Before 1977 only the federal return
is calculated. TAXSIM itself is run one state and one year at a time, because its results for a record depend on
the records that ran before it (variables left over from another state's routine; Maryland 1977-1984 differs by
about $90 per household when mixed with other states). A household "agrees" when the tax differs by $1 or less. Federal rows count all units; state rows count the
states with a wage income tax.

## Against TAXSIM

*TAXSIM mode* reproduces the executable, bugs included. *Statutory mode* follows the law, so it is expected to differ
where TAXSIM is wrong or out of date; each difference is listed in [TAXSIM errors and
corrections](statutory_corrections.md).

Over all states and years since 1977, **{overall("taxsim_state"):.1f}%** of households agree with TAXSIM's state tax in
TAXSIM mode ({overall("taxsim_state_exact"):.1f}% within 2 cents), and **{overall("statutory_state"):.1f}%** in statutory
mode.

### Federal income tax

{chr(10).join(fed_lines)}

### State income tax: TAXSIM mode

![TAXSIM mode against TAXSIM](img/compare_taxsim_mode.png)

### State income tax: statutory mode

![Statutory mode against TAXSIM](img/compare_statutory.png)

The tables give the percent of households that agree, averaged over each period (the first group of columns is TAXSIM
mode, the second statutory mode).

{period_table(taxsim, {"taxsim_state": "TAXSIM mode", "statutory_state": "Statutory"})}

State-years where TAXSIM mode agrees for less than 95% of households:

{chr(10).join(worst_lines) if worst.height else "None."}

The full state-by-year values are in [comparison_taxsim.csv](data/comparison_taxsim.csv).
"""
    if policy.height:
        page += f"""
## Against PolicyEngine-US

Statutory mode against PolicyEngine-US's TAXSIM interface for {policy['year'].min()}-{policy['year'].max()}, on
{args.pe_units_per_state} units per state (PolicyEngine is much slower). Only households whose federal tax agrees within $1
count, so the comparison is of state logic. PolicyEngine's adapter ignores `dep6` and turns the dependent counts into
children of default ages, so credits that depend on a child's age differ in ways that are not errors in either model.
Where the two disagree, the form or statute decides; the known cases are in [Comparison with
PolicyEngine-US](policyengine_recent_state_comparison.md).

![Statutory mode against PolicyEngine-US](img/compare_policyengine.png)

{year_table(policy, "policyengine_state")}

The full values are in [comparison_policyengine.csv](data/comparison_policyengine.csv).
"""
    (args.out / "comparisons.md").write_text(page)


if __name__ == "__main__":
    main()
