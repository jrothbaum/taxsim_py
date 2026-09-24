"""Unified validation runner for state income tax calculators, mirroring
scripts/validate_federal.py's pattern (one test-case table per state, one
shared runner here). Add a new state by writing `tests/{st}_cases.py`
(`build_{st}_test_cases()`) and `calculators/states/{st}.py`
(`compute_{st}_tax(df, year) -> pl.DataFrame`, adding a `siitax` column),
then register both below.
"""

import subprocess
import sys
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
    for col in COMPARED_COLUMNS:
        out = out.with_columns(pl.col(col).cast(pl.Float64))
    return out.select("taxsimid", *[pl.col(c).alias(f"{c}_expected") for c in COMPARED_COLUMNS])


def main() -> None:
    total = 0
    total_failures = 0
    selected = set(a.upper() for a in sys.argv[1:]) or None
    states_to_run = {k: v for k, v in STATES.items() if selected is None or k in selected}
    for state_name, (build_cases, compute_state_tax, years) in states_to_run.items():
        rows = build_cases()
        cases = pl.DataFrame(rows).with_row_index("taxsimid", offset=1)
        expected = run_taxsim_exe(cases)

        actual_parts = []
        for year in years:
            year_cases = cases.filter(pl.col("year") == year)
            year_actual = resolve_federal_and_state(year_cases, year, compute_state_tax)
            actual_parts.append(year_actual.select("taxsimid", "description", *COMPARED_COLUMNS))
        actual = pl.concat(actual_parts)

        comparison = actual.join(expected, on="taxsimid")
        diff_exprs = [(pl.col(c) - pl.col(f"{c}_expected")).abs().alias(f"{c}_diff") for c in COMPARED_COLUMNS]
        comparison = comparison.with_columns(diff_exprs)
        is_mismatch = pl.any_horizontal([pl.col(f"{c}_diff") > MISMATCH_TOLERANCE for c in COMPARED_COLUMNS])
        mismatches = comparison.filter(is_mismatch)

        print(f"[{state_name}] Ran {comparison.height} test cases across {COMPARED_COLUMNS}.")
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
