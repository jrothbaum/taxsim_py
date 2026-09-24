"""Federal test cases, TX (state=44 - no state income tax subroutine in the
Fortran, isolating federal logic per the design doc's Rollout Sequence),
across multiple tax years (see YEARS below).

One growing table instead of separate scripts per feature: each row is a
full TAXSIM input record plus a `description` string saying what that row
is meant to exercise. Add rows here as new federal pieces get built; the
runner (scripts/validate_federal.py) diffs every row against
taxsim2022.exe in one pass and only prints the ones that fail, labeled by
their description.

Edge/boundary values are derived from the resolved parameter files for the
year in question (not hardcoded 2022 numbers) so that adding a new year to
YEARS actually exercises that year's own real thresholds - e.g. 2019's
bracket edges, AMT breakpoints, and payroll wage base are genuinely
different dollar amounts from 2022's, and the pre-2021 CTC/CCC formulas
have a different shape entirely (see calculators/federal.py).
"""

from pathlib import Path
from typing import Any

import polars as pl

ROOT = Path(__file__).resolve().parents[1]

YEARS = [1960, 1961, 1962, 1963, 1964, 1965, 1966, 1967, 1968, 1969, 1970, 1971, 1972, 1973, 1974, 1975, 1976, 1977, 1978, 1979, 1980, 1981, 1982, 1983, 1984, 1985, 1986, 1987, 1988, 1989, 1990, 1991, 1992, 1993, 1994, 1995, 1996, 1997, 1998, 1999, 2000, 2001, 2002, 2003, 2004, 2005, 2006, 2007, 2008, 2009, 2010, 2011, 2012, 2013, 2014, 2015, 2016, 2017, 2018, 2019, 2020, 2021, 2022, 2023]
STATE_TX = 44

_DEFAULTS: dict[str, Any] = {
    "state": STATE_TX,
    "mstat": 1,
    "depx": 0,
    "dep17": 0,
    "dep18": 0,
    "dep6": 0,
    "pwages": 0,
    "swages": 0,
    "proptax": 0,
    "otheritem": 0,
    "mortgage": 0,
    "state_sales_or_income_tax_ded": 0,
    "dep13": 0,
    "childcare": 0,
    "intrec": 0,
    "psemp": 0,
    "ssemp": 0,
    "dividends": 0,
    "stcg": 0,
    "ltcg": 0,
    "ui": 0,
    "pui": 0,
    "sui": 0,
}

_STATUS_BY_MSTAT = {1: "single", 2: "married_joint"}


def case(year: int, description: str, **overrides: Any) -> dict[str, Any]:
    row = dict(_DEFAULTS)
    row["year"] = year
    row.update(overrides)
    row["description"] = f"{description} [year={year}]"
    return row


def _status_bracket_edges(income_tax_params: dict, year: int, status: str) -> list[float]:
    from taxsim_py.engine.schema import resolve_year

    brackets = resolve_year(income_tax_params["brackets"][status], year)
    return [b[0] for b in brackets if b[0] > 0]


def _bracket_edge_cases(year: int, income_tax_params: dict) -> list[dict[str, Any]]:
    rows = []
    for mstat, status in _STATUS_BY_MSTAT.items():
        edges = _status_bracket_edges(income_tax_params, year, status)
        for wages in sorted({1, 5000, 15000, 600000} | {e + d for e in edges for d in (-1, 0, 1)}):
            rows.append(
                case(year, f"bracket edge, {status}, wages={wages}", mstat=mstat, pwages=wages)
            )
    return rows


def _eitc_edges_for(eitc_params: pl.DataFrame, year: int, num_children: int, status: str) -> list[int]:
    row = eitc_params.filter(
        (pl.col("year") == year)
        & (pl.col("num_children") == num_children)
        & (pl.col("filing_status") == status)
    )
    if row.is_empty():
        return []
    r = row.row(0, named=True)
    if r["rate_in"] == 0:
        # The childless EITC didn't exist at all before 1994 - the
        # source's own guard is `if(lawyr.lt.1994.and.data(8).eq.0)
        # earncr=0.` (taxsim_2022_10_21.f:27745). eitc.csv's 1993/kids=0
        # row is genuinely all-zero to reproduce that with no code
        # branch, not a real (rate_in=0%) credit worth boundary-testing.
        return []
    phase_in_end = r["max_credit"] / r["rate_in"]
    phaseout_start = r["phaseout_start"]
    phaseout_end = phaseout_start + r["max_credit"] / r["rate_out"]
    return [round(phase_in_end), round(phaseout_start), round(phaseout_end)]


def _eitc_cases(year: int, eitc_params: pl.DataFrame) -> list[dict[str, Any]]:
    rows = []
    for mstat, status in _STATUS_BY_MSTAT.items():
        for num_children in (0, 1, 2, 3):
            edges = _eitc_edges_for(eitc_params, year, num_children, status)
            for wages in sorted({1, 5000, 15000} | {e + d for e in edges for d in (-1, 0, 1)}):
                rows.append(
                    case(
                        year,
                        f"EITC boundary, {status}, {num_children} children, wages={wages}",
                        mstat=mstat,
                        depx=num_children,
                        dep18=num_children,
                        pwages=wages,
                    )
                )
    return rows


def _ctc_edges_for(credits_params: dict, year: int, ideps: int, dep6: int, status: str) -> list[int]:
    from taxsim_py.engine.schema import resolve_year

    ctc_p = credits_params["child_tax_credit"]
    if year >= 2018:
        odc_p = credits_params["other_dependent_credit"]
        tcja_thresh = resolve_year(odc_p["phaseout_threshold"][status], year)
        tcja_rate = resolve_year(odc_p["phaseout_rate_per_1000"], year) / 1000
    else:
        # Pre-2018: no ODC - CTC alone phases out above a flat threshold.
        tcja_thresh = resolve_year(ctc_p["pre2018_phaseout_threshold"][status], year)
        tcja_rate = resolve_year(ctc_p["pre2018_phaseout_rate_per_1000"], year) / 1000

    if year >= 2021:
        young_amt = resolve_year(ctc_p["young_child_amount"], year)
        older_amt = resolve_year(ctc_p["older_child_amount"], year)
        offset = resolve_year(ctc_p["base_amount_offset"], year)
        cap = resolve_year(ctc_p["enhanced_cap"][status], year)
        low_thresh = resolve_year(ctc_p["low_income_phaseout_threshold"][status], year)
        low_rate = resolve_year(ctc_p["low_income_phaseout_rate"], year)

        ccrmax = dep6 * young_amt + (ideps - dep6) * older_amt
        enhanced = min(ccrmax - ideps * offset, cap)
        low_income_end = low_thresh + (enhanced / low_rate if low_rate else 0)
        base_at_tcja_start = ccrmax - enhanced
        tcja_end = tcja_thresh + (base_at_tcja_start / tcja_rate if tcja_rate else 0)
        return [round(low_thresh), round(low_income_end), round(tcja_thresh), round(tcja_end)]
    else:
        # Pre-2021: flat per-child amount, no low-income phaseout mechanism -
        # only the TCJA-era $200k/$400k phaseout applies.
        flat_amount = resolve_year(ctc_p["flat_amount_pre2021"], year)
        combined_base = flat_amount * ideps
        tcja_end = tcja_thresh + (combined_base / tcja_rate if tcja_rate else 0)
        return [round(tcja_thresh), round(tcja_end)]


def _ctc_cases(year: int, credits_params: dict) -> list[dict[str, Any]]:
    rows = []
    for mstat, status in _STATUS_BY_MSTAT.items():
        for ideps in (1, 2):
            for dep6 in (0, 1):
                edges = _ctc_edges_for(credits_params, year, ideps, dep6, status)
                for wages in sorted({1, 5000, 15000, 600000} | {e + d for e in edges for d in (-1, 0, 1)}):
                    rows.append(
                        case(
                            year,
                            f"CTC/ACTC/ODC boundary, {status}, {ideps} kids "
                            f"(dep6={dep6}), wages={wages}",
                            mstat=mstat,
                            depx=ideps,
                            dep17=ideps,
                            dep18=ideps,
                            dep6=dep6,
                            pwages=wages,
                        )
                    )
    return rows


def _amt_itemized_cases(year: int) -> list[dict[str, Any]]:
    rows = []
    for wages in [50000, 90000, 150000, 200000, 250000, 300000, 400000, 600000, 1000000]:
        for proptax in [0, 8000, 10000, 15000, 25000]:
            for mortgage in [0, 20000, 50000, 100000]:
                rows.append(
                    case(
                        year,
                        f"itemized/AMT grid, single, wages={wages}, "
                        f"proptax={proptax}, mortgage={mortgage}",
                        pwages=wages,
                        proptax=proptax,
                        mortgage=mortgage,
                    )
                )
    return rows


def _payroll_cases(year: int, payroll_params: dict) -> list[dict[str, Any]]:
    from taxsim_py.engine.schema import resolve_year

    wage_base = resolve_year(payroll_params["oasdi_wage_base"], year)
    edges = {
        wage_base,
        resolve_year(payroll_params["additional_medicare_threshold"]["single"], year),
        resolve_year(payroll_params["additional_medicare_threshold"]["married_joint"], year),
    }
    wage_points = sorted({1, 5000, 80000} | {e + d for e in edges for d in (-1, 0, 1)} | {600000})
    rows = []
    for mstat, status in _STATUS_BY_MSTAT.items():
        for pwages in wage_points:
            swages_options = [0] if mstat == 1 else [0, 50000, 200000]
            for swages in swages_options:
                rows.append(
                    case(
                        year,
                        f"payroll tax boundary, {status}, pwages={pwages}, swages={swages}",
                        mstat=mstat,
                        pwages=pwages,
                        swages=swages,
                    )
                )
    return rows


def _child_care_credit_cases(year: int, credits_params: dict) -> list[dict[str, Any]]:
    from taxsim_py.engine.schema import resolve_year

    ccc_p = credits_params["child_care_credit"]
    if year >= 2021:
        agi_edges = [
            resolve_year(ccc_p["first_phase_start"], year),
            resolve_year(ccc_p["first_phase_ceiling"], year),
            resolve_year(ccc_p["second_phase_start"], year),
            resolve_year(ccc_p["second_phase_ceiling"], year),
        ]
        max_expense = resolve_year(ccc_p["max_expense_per_person"], year)
    else:
        # Rate floors at 20% once agi crosses phase_start by
        # (top_rate - floor_rate)/0.01 * step_amount dollars, then holds
        # forever - the only two AGI edges worth boundary-testing.
        phase_start = resolve_year(ccc_p["pre2021_phase_start"], year)
        top_rate = resolve_year(ccc_p["pre2021_rate_top"], year)
        floor_rate = resolve_year(ccc_p["pre2021_rate_floor"], year)
        step_amount = resolve_year(ccc_p["pre2021_step_amount"], year)
        floor_reached_at = phase_start + (top_rate - floor_rate) / 0.01 * step_amount
        agi_edges = [phase_start, floor_reached_at]
        max_expense = resolve_year(ccc_p["max_expense_per_person_pre2021"], year)

    wage_points = sorted({5000, 19400, 19401, 25000} | {e + d for e in agi_edges for d in (-1, 0, 1)})
    expense_points = sorted(
        {5000, max_expense - 1, max_expense, max_expense + 1, 2 * max_expense, 2.5 * max_expense}
    )
    rows = []
    for mstat, status in _STATUS_BY_MSTAT.items():
        for num_persons in (1, 2):
            for expense in expense_points:
                for wages in wage_points:
                    swages = 30000 if mstat == 2 else 0
                    rows.append(
                        case(
                            year,
                            f"child care credit, {status}, {num_persons} qualifying, "
                            f"expense={expense}, wages={wages}",
                            mstat=mstat,
                            depx=num_persons,
                            dep13=num_persons,
                            dep17=num_persons,
                            dep18=num_persons,
                            pwages=wages,
                            swages=swages,
                            childcare=expense,
                        )
                    )
    return rows


def _interest_income_cases(year: int, income_tax_params: dict) -> list[dict[str, Any]]:
    rows = []
    for mstat, status in _STATUS_BY_MSTAT.items():
        edges = _status_bracket_edges(income_tax_params, year, status)
        for wages in (0, 20000, 100000):
            for intrec in sorted({0, 1000, 5000} | {e - wages for e in edges if e > wages}):
                rows.append(
                    case(
                        year,
                        f"interest income, {status}, wages={wages}, intrec={intrec}",
                        mstat=mstat,
                        pwages=wages,
                        intrec=intrec,
                    )
                )
    return rows


def _eitc_disqualified_income_cases(year: int, eitc_misc_params: dict) -> list[dict[str, Any]]:
    from taxsim_py.engine.schema import resolve_year

    if year < 1996:
        # The disqualified-income test itself didn't exist before 1996 -
        # see calculators/federal.py's own year<1996 special case.
        return []
    dylim = resolve_year(eitc_misc_params["dylim"], year)
    rows = []
    for mstat, status in _STATUS_BY_MSTAT.items():
        for intrec in sorted({0, dylim - 1, dylim, dylim + 1, dylim + 1700, dylim + 5000}):
            rows.append(
                case(
                    year,
                    f"EITC disqualified income, {status}, intrec={intrec}",
                    mstat=mstat,
                    depx=1,
                    dep18=1,
                    pwages=5000,
                    intrec=intrec,
                )
            )
    return rows


def _niit_cases(year: int, niit_params: dict) -> list[dict[str, Any]]:
    from taxsim_py.engine.schema import resolve_year

    rows = []
    for mstat, status in _STATUS_BY_MSTAT.items():
        threshold = resolve_year(niit_params["threshold"][status], year)
        wages = threshold - 20000
        for intrec in (0, 10000, 19999, 20000, 20001, 30000, 70000):
            for proptax in (0, 8000):
                rows.append(
                    case(
                        year,
                        f"NIIT, {status}, intrec={intrec}, proptax={proptax}",
                        mstat=mstat,
                        pwages=wages,
                        intrec=intrec,
                        proptax=proptax,
                        mortgage=20000,  # ensures itemizing so proptax actually matters
                    )
                )
    return rows


def _self_employment_cases(year: int, payroll_params: dict) -> list[dict[str, Any]]:
    # Deliberately avoids combined pwages+psemp landing exactly at or above
    # the OASDI wage base with nonzero psemp/ssemp: the source has a real,
    # order-dependent bug there (oasb1/hib1 left unassigned in the
    # "wages alone already exceed the cap" branch, so they silently pick up
    # a stale value from whatever prior record ran in the same batch - see
    # engine/payroll_tax.py). A batched comparison against taxsim2022.exe
    # would get an unreliable "expected" value in that exact zone, not a
    # meaningful test of our (deliberately bug-free) implementation.
    from taxsim_py.engine.schema import resolve_year

    wage_base = float(resolve_year(payroll_params["oasdi_wage_base"], year))
    net_factor = float(resolve_year(payroll_params["se_net_earnings_factor"], year))
    cap_gross = wage_base / net_factor  # gross SE income whose net earnings exactly hit the cap

    rows = []
    for mstat, status in _STATUS_BY_MSTAT.items():
        for psemp in sorted({5000, 50000, round(cap_gross) - 100, round(cap_gross) + 100, 200000}):
            rows.append(
                case(year, f"pure self-employment income, {status}, psemp={psemp}", mstat=mstat, psemp=psemp)
            )
        # Mixed wages + SE, combined straddling the cap (wages alone stay
        # safely under it) - exercises the "earn >= cap" partial-room branch.
        # base_wages is capped below wage_base itself (not hardcoded at
        # $100,000) so this still holds for years whose wage base is under
        # $100k, like 2007's $97,500 - otherwise "wages alone" would already
        # exceed the cap and land in the stale-value bug zone the comment
        # above warns about.
        base_wages = min(100000, wage_base - 20000)
        room_after_base_wages = wage_base - base_wages
        se_at_room_edge = room_after_base_wages / net_factor
        for pwages, psemp in (
            (base_wages, base_wages),
            (base_wages, round(se_at_room_edge) - 1),
            (base_wages, round(se_at_room_edge)),
            (base_wages, round(se_at_room_edge) + 1),
            (base_wages, 100000),
        ):
            rows.append(
                case(
                    year,
                    f"mixed wages+SE, {status}, pwages={pwages}, psemp={psemp}",
                    mstat=mstat,
                    pwages=pwages,
                    psemp=psemp,
                )
            )
        if mstat == 2:
            # Both spouses self-employed.
            rows.append(
                case(year, "both spouses self-employed, married_joint", mstat=2, psemp=60000, ssemp=40000)
            )
    return rows


def _dividends_capital_gains_cases(
    year: int, capital_gains_params: dict, amt_params: dict
) -> list[dict[str, Any]]:
    from taxsim_py.engine.schema import resolve_year

    rows = []
    for mstat, status in _STATUS_BY_MSTAT.items():
        # Dividends/LTCG straddle both the regular-tax and AMT copies of the
        # 0/15% and 15/20% breakpoints (they can genuinely differ - see
        # capital_gains.yaml/amt.yaml docs on the 2022 single-filer $100 gap).
        # Pre-2013 years use a sentinel (effectively infinite) ceiling for
        # the no-longer-existent 20% tier (see those files' own notes) -
        # filtered out here, not a meaningful boundary to sweep near.
        # 1994-1996 (the `tax94` vintage) have no rate_15_ceiling/AMT
        # cg_rate_15_ceiling concept at all - calculators/federal.py's own
        # year<=1996 branch is a fundamentally different "cap the marginal
        # rate at 28%" alternative tax, not a tiered rate with a second
        # breakpoint, so those two parameters have no entries that far
        # back on purpose.
        edge_sources = [capital_gains_params["rate_0_ceiling"][status]]
        if year >= 1997:
            edge_sources.append(capital_gains_params["rate_15_ceiling"][status])
            edge_sources.append(amt_params["cg_rate_15_ceiling"][status])
        edges = {e for e in (resolve_year(src, year) for src in edge_sources) if e < 1.0e10}
        for wages in (0, 30000):
            for dividends in sorted({1000, 20000} | {e - wages for e in edges if e > wages}):
                rows.append(
                    case(
                        year,
                        f"dividends, {status}, wages={wages}, dividends={dividends}",
                        mstat=mstat,
                        pwages=wages,
                        dividends=dividends,
                    )
                )
        for wages in (0, 30000):
            for ltcg in sorted({1000, 20000} | {e - wages for e in edges if e > wages}):
                rows.append(
                    case(
                        year, f"LTCG, {status}, wages={wages}, ltcg={ltcg}", mstat=mstat, pwages=wages, ltcg=ltcg
                    )
                )
        # Short-term gains alone (ordinary rate - no preferential treatment).
        for wages in (0, 30000):
            rows.append(case(year, f"STCG, {status}, wages={wages}", mstat=mstat, pwages=wages, stcg=15000))
        # Combined: wages + dividends + LTCG + STCG together.
        rows.append(
            case(
                year,
                f"combined dividends+LTCG+STCG, {status}",
                mstat=mstat,
                pwages=50000,
                dividends=10000,
                ltcg=20000,
                stcg=5000,
            )
        )
        rows.append(
            case(
                year,
                f"combined dividends+LTCG+STCG, high income, {status}",
                mstat=mstat,
                pwages=400000,
                dividends=50000,
                ltcg=100000,
                stcg=10000,
            )
        )
    return rows


def _unemployment_income_cases(year: int) -> list[dict[str, Any]]:
    # The $10,200-per-spouse exclusion and the $150,000 AGI cliff only
    # apply for 2020, but sweeping every year confirms UI is ordinary
    # taxable income everywhere else (no exclusion at all).
    ui_edges = [10200, 150000]
    rows = []
    for mstat, status in _STATUS_BY_MSTAT.items():
        for wages in (0, 30000, 140000):
            for ui in sorted({0, 5000, 20000} | {e - wages for e in ui_edges if e > wages}):
                rows.append(
                    case(
                        year,
                        f"unemployment income, {status}, wages={wages}, ui={ui}",
                        mstat=mstat,
                        pwages=wages,
                        ui=ui,
                    )
                )
        if mstat == 2:
            # Split pui/sui to exercise the per-spouse exclusion cap
            # (only one spouse's UI, vs. both, vs. an even split).
            for pui, sui in ((15000, 0), (5000, 10000), (10000, 10000)):
                rows.append(
                    case(
                        year,
                        f"unemployment income split, married_joint, pui={pui}, sui={sui}",
                        mstat=2,
                        pui=pui,
                        sui=sui,
                    )
                )
    return rows


def _recovery_rebate_cases(year: int) -> list[dict[str, Any]]:
    # Only nonzero for 2020 (EIP1/EIP2) and 2021 (EIP3), but harmless to run
    # every year too - `cares` should be exactly $0 elsewhere.
    rows = []
    thresholds = {"single": (75000, 1.0), "married_joint": (150000, 2.0)}
    for mstat, status in _STATUS_BY_MSTAT.items():
        phcare, ncare = thresholds[status]
        phmax = phcare + 5000 * ncare
        edges = sorted({phcare - 1, phcare, phcare + 1, phmax - 1, phmax, phmax + 1})
        for depx in (0, 2):
            for agi in edges:
                rows.append(
                    case(
                        year,
                        f"recovery rebate, {status}, depx={depx}, agi={agi}",
                        mstat=mstat,
                        pwages=agi,
                        depx=depx,
                        dep17=depx,
                        dep18=depx,
                        dep13=depx,
                    )
                )
    # head_of_household (mstat=1, depx>0) gets its own 1.5x threshold -
    # confirmed via the source's internal mstat=4 renormalization.
    hoh_phcare, hoh_ncare = 112500, 1.5
    hoh_phmax = hoh_phcare + 5000 * hoh_ncare
    for agi in sorted({hoh_phcare - 1, hoh_phcare, hoh_phcare + 1, hoh_phmax - 1, hoh_phmax, hoh_phmax + 1}):
        rows.append(
            case(
                year,
                f"recovery rebate, head_of_household, agi={agi}",
                mstat=1,
                pwages=agi,
                depx=1,
                dep17=1,
                dep18=1,
                dep13=1,
            )
        )
    return rows


def _ctc_ccc_refundability_cases(year: int) -> list[dict[str, Any]]:
    # 2021 ARPA: CTC and CCC both become fully refundable, dropping any
    # tax-liability cap (and CTC's ACTC earned-income floor entirely) -
    # most visible at $0/very low wages, where the normal ACTC formula (or
    # a $0 tax-liability CCC cap) would otherwise give $0. Harmless to run
    # every year: it should reduce to the ordinary nonrefundable-then-ACTC
    # behavior everywhere else.
    rows = []
    for mstat, status in _STATUS_BY_MSTAT.items():
        for wages in (0, 1000, 2000):
            for dep6 in (0, 1):
                rows.append(
                    case(
                        year,
                        f"CTC refundability at low wages, {status}, wages={wages}, dep6={dep6}",
                        mstat=mstat,
                        pwages=wages,
                        depx=2,
                        dep17=2,
                        dep18=2,
                        dep6=dep6,
                    )
                )
    for wages in (5000, 15000, 40000):
        rows.append(
            case(
                year,
                f"CCC refundability, single, wages={wages}",
                mstat=1,
                depx=1,
                dep13=1,
                dep17=1,
                dep18=1,
                pwages=wages,
                childcare=8000,
            )
        )
    return rows


def _making_work_pay_cases(year: int) -> list[dict[str, Any]]:
    # 2009-2010 only (Making Work Pay, ARRA), but harmless to run every
    # year - the credit should be exactly $0 elsewhere. $400 single/HoH/
    # MFS ($800 joint) max credit at 6.2% of earned income, phased out 2
    # cents per dollar of AGI over $75k single/$150k joint, hard-zeroed
    # above a $95k/$190k AGI ceiling regardless of the phase-out's own
    # crossing point.
    rows = []
    for mstat, status in _STATUS_BY_MSTAT.items():
        num_filers = 2 if status == "married_joint" else 1
        phase_in_end = round(400 * num_filers / 0.062)
        phaseout_start = 75000 * num_filers
        hard_ceiling = 95000 * num_filers
        for wages in sorted(
            {1000, 10000}
            | {e + d for e in (phase_in_end, phaseout_start, hard_ceiling) for d in (-1, 0, 1)}
        ):
            rows.append(
                case(year, f"Making Work Pay, {status}, wages={wages}", mstat=mstat, pwages=wages)
            )
    return rows


def _pre1987_bracket_edges(pre1987_params: dict, year: int, status: str) -> list[float]:
    from taxsim_py.engine.schema import resolve_year

    brackets = resolve_year(pre1987_params["brackets"][status], year)
    return [b[0] for b in brackets if b[0] > 0]


def _pre1987_cases(year: int, pre1987_params: dict) -> list[dict[str, Any]]:
    """1977-1986 (`law79`) test cases - a separate generator from the
    law87-era ones above, since law79's own parameter shapes (a flat EITC
    schedule, a capital-gains EXCLUSION rather than a preferential rate,
    three different minimum-tax formulas) don't fit the existing
    generators' assumptions. See calculators/federal_pre1987.py."""
    from taxsim_py.engine.schema import resolve_year

    rows: list[dict[str, Any]] = []
    p = pre1987_params
    rate_in = resolve_year(p["eitc_rate_in"], year)
    max_credit = resolve_year(p["eitc_max_credit"], year)
    phaseout_start = resolve_year(p["eitc_phaseout_start"], year)
    rate_out = resolve_year(p["eitc_rate_out"], year)
    eitc_edges = [
        round(max_credit / rate_in),
        round(phaseout_start),
        round(phaseout_start + max_credit / rate_out),
    ]

    for mstat, status in _STATUS_BY_MSTAT.items():
        bracket_edges = _pre1987_bracket_edges(p, year, status)
        # Plain wage sweeps: bracket edges (also exercise the "maximum tax
        # on earned income" and minimum-tax floors at high income) plus
        # EITC edges (with a qualifying child, so the credit actually
        # applies).
        wage_points = sorted(
            {1, 5000, 15000, 50000, 120000, 250000}
            | {e + d for e in bracket_edges for d in (-1, 0, 1)}
            | {e + d for e in eitc_edges for d in (-1, 0, 1)}
        )
        for wages in wage_points:
            rows.append(case(year, f"pre1987 wages, {status}, wages={wages}", mstat=mstat, pwages=wages))
            rows.append(
                case(
                    year,
                    f"pre1987 EITC, {status}, 1 child, wages={wages}",
                    mstat=mstat,
                    depx=1,
                    dep18=1,
                    pwages=wages,
                )
            )

        # Dividend exclusion boundary (also a genuinely different mechanism
        # for 1979 (no exclusion - a replicated quirk) and 1981 (merges
        # interest into the same exclusion base)).
        divexc = resolve_year(p["dividend_exclusion"][status], year)
        for dividends in sorted({0, 50, divexc, divexc + 1, divexc + 500} | ({1, 250} if year == 1981 else set())):
            rows.append(
                case(year, f"pre1987 dividends, {status}, dividends={dividends}", mstat=mstat, dividends=dividends)
            )
        if year == 1981:
            for intrec in (0, 100, 500):
                rows.append(
                    case(
                        year,
                        f"pre1987 1981 interest+dividend merge, {status}, intrec={intrec}",
                        mstat=mstat,
                        dividends=100,
                        intrec=intrec,
                    )
                )

        # Capital gains: the exclusion itself, plus the pre-1979/1981
        # alternative-tax mechanisms (need real income to interact with).
        for wages, ltcg in ((0, 20000), (40000, 20000), (0, 80000), (60000, 150000), (0, 400000)):
            rows.append(
                case(
                    year,
                    f"pre1987 capital gains, {status}, wages={wages}, ltcg={ltcg}",
                    mstat=mstat,
                    pwages=wages,
                    ltcg=ltcg,
                )
            )
        rows.append(case(year, f"pre1987 short-term gain, {status}", mstat=mstat, pwages=30000, stcg=15000))

        # Unemployment compensation exclusion (inert pre-1979, real 1979+).
        uxemp = resolve_year(p["unemployment_exclusion_threshold"][status], year) if year >= 1979 else 0
        for wages, ui in ((0, 5000), (uxemp, 8000), (0, 15000)):
            rows.append(
                case(
                    year, f"pre1987 unemployment, {status}, wages={wages}, ui={ui}", mstat=mstat, pwages=wages, ui=ui
                )
            )

        # Itemized deductions (proptax+otheritem+mortgage - the only real
        # itemized categories reachable through this project's input
        # schema for this era) - both around the standard-deduction
        # boundary and at large-enough values to trigger the minimum
        # tax's excess-itemized-deductions preference item.
        zbr = resolve_year(p["standard_deduction"][status], year)
        for proptax, wages in ((0, 20000), (zbr, 20000), (zbr + 1000, 20000), (30000, 150000)):
            rows.append(
                case(
                    year,
                    f"pre1987 itemized, {status}, proptax={proptax}, wages={wages}",
                    mstat=mstat,
                    proptax=proptax,
                    mortgage=proptax,
                    pwages=wages,
                )
            )

        # Child and Dependent Care Credit.
        expense_cap = resolve_year(p["child_care_credit_expense_cap"], year)
        for childcare, wages in ((0, 20000), (expense_cap, 20000), (expense_cap * 2, 40000)):
            rows.append(
                case(
                    year,
                    f"pre1987 CCC, {status}, childcare={childcare}",
                    mstat=mstat,
                    depx=1,
                    dep13=1,
                    dep17=1,
                    dep18=1,
                    childcare=childcare,
                    pwages=wages,
                )
            )

        # Self-employment income (no SE-tax AGI deduction exists this era
        # at all - earned income and AGI both just add gross SE income).
        for psemp in (5000, 30000, 80000):
            rows.append(case(year, f"pre1987 self-employment, {status}, psemp={psemp}", mstat=mstat, psemp=psemp))

    # Two-earner deduction (married_joint only, 1982+ - inert but harmless
    # to run every year). Kept well under 1977's own $16,500 wage base (the
    # smallest of any year in this range) on purpose - exceeding the
    # payroll wage base lands in the source's own known cross-record
    # stale-value zone in a batched run (same family of bug already
    # documented for 1988-1990/2007's own self-employment test cases;
    # confirmed here too via an isolated single-record oracle probe that
    # matches this project's own output exactly, unlike the batched run).
    for wage1, wage2 in ((8000, 4000), (12000, 10000)):
        rows.append(
            case(
                year,
                f"pre1987 two-earner deduction, married_joint, wage1={wage1}, wage2={wage2}",
                mstat=2,
                pwages=wage1,
                swages=wage2,
            )
        )

    # head_of_household and married_separate: a smaller targeted sweep
    # (not the full per-status loop above) - both have genuinely different
    # parameter values (dividend exclusion, unemployment threshold, zbr)
    # worth exercising directly.
    for wages in (5000, 30000, 80000):
        rows.append(case(year, f"pre1987 HoH wages={wages}", mstat=3, depx=1, dep18=1, pwages=wages))
        rows.append(case(year, f"pre1987 married_separate wages={wages}", mstat=6, pwages=wages))

    return rows


def _law60_cases(year: int, law60_params: dict) -> list[dict[str, Any]]:
    """1971-1976 (`law60` Phase 1) test cases - see
    calculators/federal_law60.py. Distinct from `_pre1987_cases`: no
    per-status bracket table for married_separate in the YAML (derived at
    runtime by halving married_joint's), a genuinely different taxable-
    income formula shape, an additive (not floor) minimum tax, and a
    capital-gains alternative tax real across most of this range (not just
    two isolated years)."""
    from taxsim_py.engine.schema import resolve_year

    rows: list[dict[str, Any]] = []
    p = law60_params

    # head_of_household and married_separate: a smaller targeted sweep.
    # Placed FIRST (not last) in this generator on purpose - anything with
    # SE income running immediately before a wage-only record lands in the
    # source's own documented cross-record stale-value zone (see the
    # "UI at high income" note below for the same family of bug), confirmed
    # here too via an isolated single-record oracle probe matching this
    # project's own output exactly.
    for wages in (5000, 30000, 80000):
        rows.append(case(year, f"law60 HoH wages={wages}", mstat=3, depx=1, dep18=1, pwages=wages))
        rows.append(case(year, f"law60 married_separate wages={wages}", mstat=6, pwages=wages))

    for mstat, status in _STATUS_BY_MSTAT.items():
        brackets = resolve_year(p["brackets"][status], year)
        bracket_edges = [b[0] for b in brackets if 0 < b[0] < 1.0e28]
        ebot = resolve_year(p["max_tax_earned_income_floor"][status], year) if year >= 1971 else 40000.0
        eitc_edges = []
        if year >= 1975:
            rate_in = resolve_year(p["eitc_rate_in"], year)
            max_credit = resolve_year(p["eitc_max_credit"], year)
            phaseout_start = resolve_year(p["eitc_phaseout_start"], year)
            rate_out = resolve_year(p["eitc_rate_out"], year)
            eitc_edges = [round(max_credit / rate_in), round(phaseout_start), round(phaseout_start + max_credit / rate_out)]

        wage_points = sorted(
            {1, 5000, 15000, 50000, 120000, 250000}
            | {e + d for e in bracket_edges for d in (-1, 0, 1)}
            | {e + d for e in eitc_edges for d in (-1, 0, 1)}
            | {int(ebot) + d for d in (-1, 0, 1, 5000)}
        )
        for wages in wage_points:
            rows.append(case(year, f"law60 wages, {status}, wages={wages}", mstat=mstat, pwages=wages))
            rows.append(
                case(year, f"law60 EITC, {status}, 1 child, wages={wages}", mstat=mstat, depx=1, dep18=1, pwages=wages)
            )

        # Self-employment income (no AGI deduction of any kind in this
        # scope). Placed early in this loop (not last) on purpose - a
        # wage-only record immediately following an SE-income record lands
        # in the source's own known cross-record stale-value zone
        # (`oasb1`/`hib1` never explicitly zeroed for wage-only records -
        # same family of bug already documented for 2007/1988-1989's SE
        # test cases, unavoidably pervasive in this era given how small the
        # payroll wage base is - confirmed via an isolated single-record
        # oracle probe matching this project's own output exactly). Every
        # block below this one, all the way through childcare (the last
        # block in this loop, and thus the last row generated for each
        # year across the whole batch, married_joint being processed last)
        # is wage-only, so nothing SE-bearing is ever adjacent to the next
        # record in the batch - including across year boundaries.
        for psemp in (5000, 20000, 60000):
            rows.append(case(year, f"law60 self-employment, {status}, psemp={psemp}", mstat=mstat, psemp=psemp))

        # Dividend exclusion boundary.
        divexc = resolve_year(p["dividend_exclusion"][status], year)
        for dividends in (0, 50, divexc, divexc + 1, divexc + 500):
            rows.append(case(year, f"law60 dividends, {status}, dividends={dividends}", mstat=mstat, dividends=dividends))

        # Capital gains: the 50% exclusion plus (1971-1975) the $50,000/
        # sepret alternative-tax threshold, at both modest and large gains,
        # combined with wages to also exercise the minimum tax (add-on,
        # driven by `capgn`) and the maximum-tax-on-earned-income interaction.
        for wages, ltcg in ((0, 20000), (30000, 20000), (0, 80000), (40000, 150000), (0, 400000)):
            rows.append(
                case(year, f"law60 capital gains, {status}, wages={wages}, ltcg={ltcg}", mstat=mstat, pwages=wages, ltcg=ltcg)
            )
        rows.append(case(year, f"law60 short-term gain, {status}", mstat=mstat, pwages=20000, stcg=10000))

        # Unemployment compensation - inert for AGI, but a real preference-
        # income addback inside the maximum-tax-on-earned-income formula at
        # high income.
        rows.append(case(year, f"law60 UI at high income, {status}", mstat=mstat, pwages=120000, ui=10000))

        # Itemized deductions (proptax+otheritem+mortgage, plus childcare
        # itself as an uncapped itemized deduction for 1971-1975 only).
        zbr_probe = 2000  # comfortably above every year's own floor/below its ceiling
        for proptax, wages in ((0, 15000), (zbr_probe, 15000), (zbr_probe + 1000, 15000), (20000, 100000)):
            rows.append(
                case(
                    year,
                    f"law60 itemized, {status}, proptax={proptax}, wages={wages}",
                    mstat=mstat,
                    proptax=proptax,
                    mortgage=proptax,
                    pwages=wages,
                )
            )

        # Child Care Credit (1976 only) / childcare-as-itemized-deduction
        # (1971-1975) - same input exercises both mechanisms depending on year.
        expense_cap = resolve_year(p["child_care_credit_expense_cap"], year) if year == 1976 else 2000.0
        for childcare, wages in ((0, 15000), (expense_cap, 15000), (expense_cap * 2, 30000)):
            rows.append(
                case(
                    year,
                    f"law60 childcare, {status}, childcare={childcare}",
                    mstat=mstat,
                    depx=1,
                    dep13=1,
                    dep17=1,
                    dep18=1,
                    childcare=childcare,
                    pwages=wages,
                )
            )

    # The state tax calculator doesn't exist at all before 1977 - the
    # oracle's own `check()` hard-errors on any nonzero state code for
    # year<1977 (taxsim_2022_10_21.f:21541-21545), unlike 1977+ where
    # STATE_TX (44, no income tax) is used to isolate federal logic. state=0
    # is the pre-1977 equivalent.
    for row in rows:
        row["state"] = 0

    return rows


def _cases_for_year(
    year: int,
    eitc_params: pl.DataFrame,
    credits_params: dict,
    income_tax_params: dict,
    payroll_params: dict,
    eitc_misc_params: dict,
    niit_params: dict,
    capital_gains_params: dict,
    amt_params: dict,
    pre1987_params: dict,
    law60_params: dict,
) -> list[dict[str, Any]]:
    if year <= 1976:
        return _law60_cases(year, law60_params)
    if year <= 1986:
        return _pre1987_cases(year, pre1987_params)
    return (
        _bracket_edge_cases(year, income_tax_params)
        + _eitc_cases(year, eitc_params)
        + _ctc_cases(year, credits_params)
        + _amt_itemized_cases(year)
        + _payroll_cases(year, payroll_params)
        + _child_care_credit_cases(year, credits_params)
        + _interest_income_cases(year, income_tax_params)
        + _eitc_disqualified_income_cases(year, eitc_misc_params)
        + _niit_cases(year, niit_params)
        + _self_employment_cases(year, payroll_params)
        + _dividends_capital_gains_cases(year, capital_gains_params, amt_params)
        + _unemployment_income_cases(year)
        + _recovery_rebate_cases(year)
        + _ctc_ccc_refundability_cases(year)
        + _making_work_pay_cases(year)
    )


def build_federal_test_cases() -> pl.DataFrame:
    """The full federal test-case table, across every year in YEARS. Adds a
    `state_sales_or_income_tax_ded` column pre-filled via the TX
    sales-tax-deduction formula (needed for the itemized/AMT cases,
    harmless zero-ish elsewhere)."""
    import sys

    sys.path.insert(0, str(ROOT / "src"))
    from taxsim_py.engine.schema import load_yaml

    eitc_params = pl.read_csv(ROOT / "parameters" / "national" / "eitc.csv")
    credits_params = load_yaml(ROOT / "parameters" / "national" / "credits.yaml")
    income_tax_params = load_yaml(ROOT / "parameters" / "national" / "income_tax.yaml")
    payroll_params = load_yaml(ROOT / "parameters" / "national" / "payroll_tax.yaml")
    eitc_misc_params = load_yaml(ROOT / "parameters" / "national" / "eitc_misc.yaml")
    niit_params = load_yaml(ROOT / "parameters" / "national" / "niit.yaml")
    capital_gains_params = load_yaml(ROOT / "parameters" / "national" / "capital_gains.yaml")
    amt_params = load_yaml(ROOT / "parameters" / "national" / "amt.yaml")
    pre1987_params = load_yaml(ROOT / "parameters" / "national" / "pre1987.yaml")
    law60_params = load_yaml(ROOT / "parameters" / "national" / "law60.yaml")

    rows: list[dict[str, Any]] = []
    for year in YEARS:
        rows += _cases_for_year(
            year,
            eitc_params,
            credits_params,
            income_tax_params,
            payroll_params,
            eitc_misc_params,
            niit_params,
            capital_gains_params,
            amt_params,
            pre1987_params,
            law60_params,
        )
    df = pl.DataFrame(rows)
    df = df.with_row_index("taxsimid", offset=1)
    return df
