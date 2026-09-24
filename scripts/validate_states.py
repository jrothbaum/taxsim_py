"""Unified validation runner for state income tax calculators, mirroring
scripts/validate_federal.py's pattern (one test-case table per state, one
shared runner here). Add a new state by writing `tests/{st}_cases.py`
(`build_{st}_test_cases()`) and `calculators/states/{st}.py`
(`compute_{st}_tax(df, year) -> pl.DataFrame`, adding a `siitax` column),
then register both below.
"""

import multiprocessing
import os
import subprocess
import sys
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import polars as pl

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))

from taxsim_py.engine.federal_state import resolve_federal_and_state  # noqa: E402
from taxsim_py.calculators.states.ak import compute_ak_tax  # noqa: E402
from taxsim_py.calculators.states.al import compute_al_tax  # noqa: E402
from taxsim_py.calculators.states.ar import compute_ar_tax  # noqa: E402
from taxsim_py.calculators.states.az import compute_az_tax  # noqa: E402
from taxsim_py.calculators.states.ca import compute_ca_tax  # noqa: E402
from taxsim_py.calculators.states.co import compute_co_tax  # noqa: E402
from taxsim_py.calculators.states.ct import compute_ct_tax  # noqa: E402
from taxsim_py.calculators.states.dc import compute_dc_tax  # noqa: E402
from taxsim_py.calculators.states.de import compute_de_tax  # noqa: E402
from taxsim_py.calculators.states.ga import compute_ga_tax  # noqa: E402
from taxsim_py.calculators.states.hi import compute_hi_tax  # noqa: E402
from taxsim_py.calculators.states.ia import compute_ia_tax  # noqa: E402
from taxsim_py.calculators.states.id import compute_id_tax  # noqa: E402
from taxsim_py.calculators.states.il import compute_il_tax  # noqa: E402
from taxsim_py.calculators.states.in_ import compute_in_tax  # noqa: E402
from taxsim_py.calculators.states.ks import compute_ks_tax  # noqa: E402
from taxsim_py.calculators.states.ky import compute_ky_tax  # noqa: E402
from taxsim_py.calculators.states.la import compute_la_tax  # noqa: E402
from taxsim_py.calculators.states.ma import compute_ma_tax  # noqa: E402
from taxsim_py.calculators.states.md import compute_md_tax  # noqa: E402
from taxsim_py.calculators.states.me import compute_me_tax  # noqa: E402
from taxsim_py.calculators.states.mi import compute_mi_tax  # noqa: E402
from taxsim_py.calculators.states.mn import compute_mn_tax  # noqa: E402
from taxsim_py.calculators.states.ms import compute_ms_tax  # noqa: E402
from taxsim_py.calculators.states.mo import compute_mo_tax  # noqa: E402
from taxsim_py.calculators.states.mt import compute_mt_tax  # noqa: E402
from taxsim_py.calculators.states.ne import compute_ne_tax  # noqa: E402
from taxsim_py.calculators.states.nh import compute_nh_tax  # noqa: E402
from taxsim_py.calculators.states.nj import compute_nj_tax  # noqa: E402
from taxsim_py.calculators.states.nm import compute_nm_tax  # noqa: E402
from taxsim_py.calculators.states.nc import compute_nc_tax  # noqa: E402
from taxsim_py.calculators.states.nd import compute_nd_tax  # noqa: E402
from taxsim_py.calculators.states.oh import compute_oh_tax  # noqa: E402
from taxsim_py.calculators.states.ny import compute_ny_tax  # noqa: E402
from ak_cases import build_ak_test_cases, YEARS as AK_YEARS  # noqa: E402
from al_cases import build_al_test_cases, YEARS as AL_YEARS  # noqa: E402
from ar_cases import build_ar_test_cases, YEARS as AR_YEARS  # noqa: E402
from az_cases import build_az_test_cases, YEARS as AZ_YEARS  # noqa: E402
from ca_cases import build_ca_test_cases, YEARS as CA_YEARS  # noqa: E402
from co_cases import build_co_test_cases, YEARS as CO_YEARS  # noqa: E402
from ct_cases import build_ct_test_cases, YEARS as CT_YEARS  # noqa: E402
from dc_cases import build_dc_test_cases, YEARS as DC_YEARS  # noqa: E402
from de_cases import build_de_test_cases, YEARS as DE_YEARS  # noqa: E402
from ga_cases import build_ga_test_cases, YEARS as GA_YEARS  # noqa: E402
from hi_cases import build_hi_test_cases, YEARS as HI_YEARS  # noqa: E402
from ia_cases import build_ia_test_cases, YEARS as IA_YEARS  # noqa: E402
from id_cases import build_id_test_cases, YEARS as ID_YEARS  # noqa: E402
from il_cases import build_il_test_cases, YEARS as IL_YEARS  # noqa: E402
from in_cases import build_in_test_cases, YEARS as IN_YEARS  # noqa: E402
from ks_cases import build_ks_test_cases, YEARS as KS_YEARS  # noqa: E402
from ky_cases import build_ky_test_cases, YEARS as KY_YEARS  # noqa: E402
from la_cases import build_la_test_cases, YEARS as LA_YEARS  # noqa: E402
from ma_cases import build_ma_test_cases, YEARS as MA_YEARS  # noqa: E402
from md_cases import build_md_test_cases, YEARS as MD_YEARS  # noqa: E402
from me_cases import build_me_test_cases, YEARS as ME_YEARS  # noqa: E402
from mi_cases import build_mi_test_cases, YEARS as MI_YEARS  # noqa: E402
from mn_cases import build_mn_test_cases, YEARS as MN_YEARS  # noqa: E402
from ms_cases import build_ms_test_cases, YEARS as MS_YEARS  # noqa: E402
from mo_cases import build_mo_test_cases, YEARS as MO_YEARS  # noqa: E402
from mt_cases import build_mt_test_cases, YEARS as MT_YEARS  # noqa: E402
from ne_cases import build_ne_test_cases, YEARS as NE_YEARS  # noqa: E402
from nh_cases import build_nh_test_cases, YEARS as NH_YEARS  # noqa: E402
from nj_cases import build_nj_test_cases, YEARS as NJ_YEARS  # noqa: E402
from nm_cases import build_nm_test_cases, YEARS as NM_YEARS  # noqa: E402
from nc_cases import build_nc_test_cases, YEARS as NC_YEARS  # noqa: E402
from nd_cases import build_nd_test_cases, YEARS as ND_YEARS  # noqa: E402
from oh_cases import build_oh_test_cases, YEARS as OH_YEARS  # noqa: E402
from ny_cases import build_ny_test_cases, YEARS as NY_YEARS  # noqa: E402

TAXSIM_EXE = ROOT / "taxsim2024.exe"
COMPARED_COLUMNS = ["siitax"]
MISMATCH_TOLERANCE = 0.015

INPUT_COLUMNS = [
    "taxsimid",
    "year",
    "state",
    "mstat",
    "depx",
    "dep17",
    "dep18",
    "dep6",
    "pwages",
    "swages",
    "proptax",
    "otheritem",
    "mortgage",
    "dep13",
    "childcare",
    "intrec",
    "psemp",
    "ssemp",
    "dividends",
    "stcg",
    "ltcg",
    "ui",
    "pui",
    "sui",
]

# Registry: state code -> (test-case builder, calculator, YEARS to run).
STATES = {
    "AK": (build_ak_test_cases, compute_ak_tax, AK_YEARS),
    "AL": (build_al_test_cases, compute_al_tax, AL_YEARS),
    "AR": (build_ar_test_cases, compute_ar_tax, AR_YEARS),
    "AZ": (build_az_test_cases, compute_az_tax, AZ_YEARS),
    "CA": (build_ca_test_cases, compute_ca_tax, CA_YEARS),
    "CO": (build_co_test_cases, compute_co_tax, CO_YEARS),
    "CT": (build_ct_test_cases, compute_ct_tax, CT_YEARS),
    "DC": (build_dc_test_cases, compute_dc_tax, DC_YEARS),
    "DE": (build_de_test_cases, compute_de_tax, DE_YEARS),
    "GA": (build_ga_test_cases, compute_ga_tax, GA_YEARS),
    "HI": (build_hi_test_cases, compute_hi_tax, HI_YEARS),
    "IA": (build_ia_test_cases, compute_ia_tax, IA_YEARS),
    "ID": (build_id_test_cases, compute_id_tax, ID_YEARS),
    "IL": (build_il_test_cases, compute_il_tax, IL_YEARS),
    "IN": (build_in_test_cases, compute_in_tax, IN_YEARS),
    "KS": (build_ks_test_cases, compute_ks_tax, KS_YEARS),
    "KY": (build_ky_test_cases, compute_ky_tax, KY_YEARS),
    "LA": (build_la_test_cases, compute_la_tax, LA_YEARS),
    "ME": (build_me_test_cases, compute_me_tax, ME_YEARS),
    "MD": (build_md_test_cases, compute_md_tax, MD_YEARS),
    "MA": (build_ma_test_cases, compute_ma_tax, MA_YEARS),
    "MI": (build_mi_test_cases, compute_mi_tax, MI_YEARS),
    "MN": (build_mn_test_cases, compute_mn_tax, MN_YEARS),
    "MS": (build_ms_test_cases, compute_ms_tax, MS_YEARS),
    "MO": (build_mo_test_cases, compute_mo_tax, MO_YEARS),
    "MT": (build_mt_test_cases, compute_mt_tax, MT_YEARS),
    "NE": (build_ne_test_cases, compute_ne_tax, NE_YEARS),
    "NH": (build_nh_test_cases, compute_nh_tax, NH_YEARS),
    "NJ": (build_nj_test_cases, compute_nj_tax, NJ_YEARS),
    "NM": (build_nm_test_cases, compute_nm_tax, NM_YEARS),
    "NY": (build_ny_test_cases, compute_ny_tax, NY_YEARS),
    "NC": (build_nc_test_cases, compute_nc_tax, NC_YEARS),
    "ND": (build_nd_test_cases, compute_nd_tax, ND_YEARS),
    "OH": (build_oh_test_cases, compute_oh_tax, OH_YEARS),
}


def run_taxsim_exe(cases: pl.DataFrame) -> pl.DataFrame:
    lines = [" ".join(INPUT_COLUMNS)]
    for row in cases.select(INPUT_COLUMNS).iter_rows(named=True):
        lines.append(" ".join(str(row[c]) for c in INPUT_COLUMNS))
    result = subprocess.run(
        [str(TAXSIM_EXE)], input="\n".join(lines) + "\n", capture_output=True, text=True, timeout=60
    )
    if result.returncode != 0:
        raise RuntimeError(f"taxsim.exe failed: {result.stderr}\n{result.stdout[-2000:]}")
    out_lines = [ln for ln in result.stdout.splitlines() if ln.strip()]
    header_cols = out_lines[0].split(",")
    data_rows = [ln.split(",") for ln in out_lines[1:]]
    out = pl.DataFrame(data_rows, schema=header_cols, orient="row")
    out = out.with_columns(pl.col("taxsimid").cast(pl.Float64).cast(pl.Int64))
    for col in (*COMPARED_COLUMNS, "fiitax"):
        out = out.with_columns(pl.col(col).cast(pl.Float64))
    return out.select(
        "taxsimid", *[pl.col(c).alias(f"{c}_expected") for c in COMPARED_COLUMNS], pl.col("fiitax").alias("oracle_fiitax")
    )


# Each year is an independent job holding every selected state's records,
# so one federal pass serves all states. Work per job is dominated by fixed
# per-call overhead rather than rows, so jobs run in parallel processes,
# each with a small Polars thread pool.
WORKERS = min(8, os.cpu_count() or 1)
POLARS_THREADS_PER_WORKER = "2"


# In 2023 this project uses real federal parameters where the oracle's are
# known to be wrong (see validate_federal.KNOWN_ORACLE_DIVERGENT_YEARS: the
# Social Security wage base, EITC amounts and others). A 2023 case whose
# federal tax differs from the oracle's carries that difference into the
# state tax, so it counts as an expected difference.
_FEDERAL_DIVERGENT_YEARS = {2023}
_FEDERAL_TOLERANCE = 0.015


def _federal_divergence() -> pl.Expr:
    return pl.col("year").is_in(_FEDERAL_DIVERGENT_YEARS) & (
        (pl.col("fiitax") - pl.col("oracle_fiitax")).abs() > _FEDERAL_TOLERANCE
    )


def _run_year(year: int, year_cases: pl.DataFrame, state_codes: dict[str, int]) -> pl.DataFrame:
    calculators = {code: STATES[name][1] for name, code in state_codes.items()}
    year_actual = resolve_federal_and_state(year_cases, year, calculators)
    return year_actual.select("state", "taxsimid", "description", *COMPARED_COLUMNS, "fiitax")


def main() -> None:
    total = 0
    total_failures = 0
    selected = set(a.upper() for a in sys.argv[1:]) or None
    states_to_run = {k: v for k, v in STATES.items() if selected is None or k in selected}

    all_cases = {name: pl.DataFrame(build()).with_row_index("taxsimid", offset=1) for name, (build, _, _) in states_to_run.items()}
    state_codes = {name: int(cases.get_column("state")[0]) for name, cases in all_cases.items()}
    years = sorted({year for _, _, state_years in states_to_run.values() for year in state_years})

    os.environ["POLARS_MAX_THREADS"] = POLARS_THREADS_PER_WORKER
    context = multiprocessing.get_context("spawn")
    with ProcessPoolExecutor(max_workers=WORKERS, mp_context=context) as pool:
        futures = []
        for year in years:
            parts = [
                cases.filter(pl.col("year") == year)
                for name, cases in all_cases.items()
                if year in states_to_run[name][2]
            ]
            year_cases = pl.concat(parts, how="diagonal_relaxed")
            futures.append(pool.submit(_run_year, year, year_cases, state_codes))
        results = pl.concat([future.result() for future in futures], how="diagonal_relaxed")

    for state_name in states_to_run:
        cases = all_cases[state_name]
        expected = run_taxsim_exe(cases)
        actual = results.filter(pl.col("state") == state_codes[state_name]).drop("state")

        comparison = actual.join(expected, on="taxsimid")
        # Cases marked `oracle_divergent` use real-law values where the
        # oracle's own parameter is known to be wrong; their mismatches are
        # counted in one summary line instead of printed as failures.
        flags = cases.select(
            "taxsimid",
            "year",
            case_divergent=(
                pl.col("oracle_divergent").fill_null(False) if "oracle_divergent" in cases.columns else pl.lit(False)
            ),
        )
        comparison = comparison.join(flags, on="taxsimid").with_columns(
            oracle_divergent=pl.col("case_divergent") | _federal_divergence()
        )
        diff_exprs = [(pl.col(c) - pl.col(f"{c}_expected")).abs().alias(f"{c}_diff") for c in COMPARED_COLUMNS]
        comparison = comparison.with_columns(diff_exprs)
        is_mismatch = pl.any_horizontal([pl.col(f"{c}_diff") > MISMATCH_TOLERANCE for c in COMPARED_COLUMNS])
        divergent = comparison.filter(is_mismatch & pl.col("oracle_divergent"))
        mismatches = comparison.filter(is_mismatch & ~pl.col("oracle_divergent"))

        print(f"[{state_name}] Ran {comparison.height} test cases across {COMPARED_COLUMNS}.")
        if not divergent.is_empty():
            print(
                f"[{state_name}] {divergent.height} mismatches are in cases using real-law values "
                "where the oracle is known to be wrong - expected, not printed individually."
            )
        total += comparison.height
        total_failures += mismatches.height
        if mismatches.is_empty():
            print(f"[{state_name}] All cases match {TAXSIM_EXE.name} exactly.")
        else:
            print(f"[{state_name}] {mismatches.height} FAILURES:")
            for row in mismatches.iter_rows(named=True):
                failed = [c for c in COMPARED_COLUMNS if row[f"{c}_diff"] > MISMATCH_TOLERANCE]
                detail = ", ".join(f"{c}: got {row[c]}, expected {row[f'{c}_expected']}" for c in failed)
                print(f"  [{row['description']}] {detail}")

    print(f"\nTotal: {total} cases, {total_failures} failures across {len(states_to_run)} state(s).")


if __name__ == "__main__":
    main()
