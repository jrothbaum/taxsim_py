#!/usr/bin/env python3
"""Archive build identity, output schema, and representative outputs for every
TAXSIM executable generation available on this machine.

TAXSIM's maintainer has retired and the service is being sunset (see
`docs/pending_issues.md`, "TAXSIM executable generations"), so each compiled
build is a finite snapshot rather than a rolling dependency. This script
records what a build *is* (path, sha256, self-reported build stamp, output
schema) and what it *does* (outputs on a small deterministic case matrix, and
pairwise differences against every other generation present), so that
evidence survives even if a binary later becomes unreachable.

This intentionally does not require a cached CPS ASEC directory: it uses a
small synthetic matrix so the archive can be regenerated anywhere. Large-N
CPS-based diff counts already recorded in `docs/pending_issues.md` are a
separate, complementary form of evidence and are not reproduced here.

Usage:
    uv run scripts/archive_oracle_generations.py
"""

from __future__ import annotations

import itertools
import json
import sys
from datetime import date, datetime, timezone
from pathlib import Path

import polars as pl

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

from compare_taxsim_oracles import compare_frames, executable_metadata  # noqa: E402
from oracle import run_oracle  # noqa: E402

TOLERANCE = 0.015

# Every generation named in docs/pending_issues.md's "TAXSIM executable
# generations" section that has a binary present in this checkout. Labels
# match that section's prose so the archive and the doc stay cross-referable.
CANDIDATE_GENERATIONS = {
    "frozen-2024": ROOT / "taxsim2024.exe",
    "repo-taxsimtest-2026072709": ROOT / "taxsim.exe",
    "policyengine-taxsimtest-2026081819": (
        ROOT / ".venv/share/policyengine_taxsim/taxsimtest/taxsimtest-linux.exe"
    ),
    # A distinct, older release family bundled alongside taxsimtest -- not one
    # of the four rolling-build generations, kept separate deliberately.
    "policyengine-taxsim35-legacy": (
        ROOT / ".venv/share/policyengine_taxsim/taxsim35/taxsim35-unix.exe"
    ),
}

# Documented but not present on this machine as of the archive date below;
# recorded so the gap itself is visible rather than silently dropped.
KNOWN_UNAVAILABLE = {
    "nber-out2psl-2026092717": "NBER out2psl/linux download, seen 2026-09-27; not cached locally.",
}

# State codes (TAXSIM's alphabetical 1-51 numbering, DC=9): federal-only plus
# the three states pending_issues.md already flags as having open
# independent-model differences (CA, NY, MN).
STATE_CODES = {"federal": 0, "CA": 5, "NY": 33, "MN": 24}

# Years spanning the "strict synthetic matrix" range docs/pending_issues.md
# already validates against (2013 onward for federal/payroll, 2021 onward for
# state-comparable independent checks).
YEARS = (2013, 2018, 2021, 2023)


def _case_matrix() -> pl.DataFrame:
    rows = []
    case_id = 1
    for year, state_code in itertools.product(YEARS, STATE_CODES.values()):
        for marital_status, primary_wages, spouse_wages, dependents in (
            (1, 20_000, 0, 0),
            (1, 125_000, 0, 0),
            (2, 60_000, 20_000, 2),
        ):
            rows.append(
                {
                    "taxsimid": case_id,
                    "year": year,
                    "state": state_code,
                    "mstat": marital_status,
                    "page": 40,
                    "sage": 40 if marital_status == 2 else 0,
                    "depx": dependents,
                    "dep6": min(dependents, 1),
                    "dep17": dependents,
                    "dep18": dependents,
                    "pwages": primary_wages,
                    "swages": spouse_wages,
                }
            )
            case_id += 1
    return pl.DataFrame(rows)


def main() -> None:
    generations = {
        label: path for label, path in CANDIDATE_GENERATIONS.items() if path.is_file()
    }
    missing = {
        label: str(path)
        for label, path in CANDIDATE_GENERATIONS.items()
        if not path.is_file()
    }
    if not generations:
        raise SystemExit("No known TAXSIM generation found on disk")

    cases = _case_matrix()
    print(f"Synthetic matrix: {cases.height} cases across {YEARS} and {sorted(STATE_CODES)}")

    metadata: dict[str, dict[str, object]] = {}
    outputs: dict[str, pl.DataFrame] = {}
    output_dir = ROOT / "docs/oracle_archive/outputs"
    output_dir.mkdir(parents=True, exist_ok=True)

    for label, path in generations.items():
        meta = executable_metadata(path)
        result = run_oracle(cases, idtl=2, executable=path)
        meta["output_schema"] = result.columns
        metadata[label] = meta
        outputs[label] = result
        result.write_csv(output_dir / f"{label}.csv")
        print(f"  {label}: build {meta['build']}, {len(result.columns)} columns")

    comparisons: dict[str, dict[str, dict[str, float | int]]] = {}
    for left_label, right_label in itertools.combinations(sorted(generations), 2):
        differences = compare_frames(outputs[left_label], outputs[right_label])
        comparisons[f"{left_label}_vs_{right_label}"] = differences
        print(
            f"\n{left_label} vs {right_label}: {len(differences)} differing outputs "
            f"on the {cases.height}-case synthetic matrix"
        )
        for column, result in sorted(
            differences.items(), key=lambda item: item[1]["mismatch_count"], reverse=True
        ):
            print(
                f"  {column:12} {result['mismatch_count']:>4} "
                f"({result['mismatch_rate']:.1%}), max ${result['maximum_absolute_difference']:,.2f}"
            )

    report = {
        "archived_at": datetime.now(timezone.utc).isoformat(),
        "generations": metadata,
        "unavailable_generations": {**missing, **KNOWN_UNAVAILABLE},
        "synthetic_matrix": {
            "case_count": cases.height,
            "years": YEARS,
            "states": STATE_CODES,
        },
        "synthetic_matrix_comparisons": comparisons,
        "note": (
            "Large-N CPS-based diff counts are recorded separately in "
            "docs/pending_issues.md (\"TAXSIM executable generations\"); this "
            "archive does not reproduce them."
        ),
    }
    out_path = ROOT / f"docs/oracle_archive/generations_{date.today().isoformat()}.json"
    out_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    print(f"\nWrote {out_path}")


if __name__ == "__main__":
    main()
