"""Write docs/statutory_audit.md: every statutory switch, its impact, and whether its statutory side is verified.

Inputs: docs/data/statutory_switch_impact.csv (scripts/audit_switch_impact.py), docs/data/statutory_audit_status.yaml
(verdicts, edited by hand as each rule is checked against a form or statute), docs/statutory_corrections.md (what
each switch's statutory side does).
"""

from __future__ import annotations

import re
from pathlib import Path

import polars as pl
import yaml

from taxsim_py.behavior import BehaviorProfile
from dataclasses import fields

ROOT = Path(__file__).resolve().parents[1]
impact = pl.read_csv(ROOT / "docs/data/statutory_switch_impact.csv").with_columns(
    stake=pl.col("changed") * pl.col("mean_abs_change_if_changed")
)
status = yaml.safe_load((ROOT / "docs/data/statutory_audit_status.yaml").read_text()) or {}
rows = [line for line in (ROOT / "docs/statutory_corrections.md").read_text().split("\n") if line.startswith("|")]


def statutory_side(switch: str) -> str:
    for line in rows:
        if switch in line:
            match = re.search(r"Statutory[^:|]*:\s*([^|]*?)(?:\s*`|\s*\||$)", line)
            if match:
                return match.group(1).strip()[:160]
    return ""


live = {f.name for f in fields(BehaviorProfile)}
frame = [r for r in impact.sort("stake", descending=True).to_dicts() if r["switch"] in live]
counts = {"verified": 0, "corrected": 0, "partial": 0, "kept_taxsim": 0, "unverified": 0}
kept = {k: v for k, v in status.items() if v["status"] == "kept_taxsim"}
counts["kept_taxsim"] = len(kept)
lines = []
for r in frame:
    entry = status.get(r["switch"], {"status": "unverified", "source": ""})
    if entry["status"] != "kept_taxsim":
        counts[entry["status"]] += 1
    lines.append(
        f"| `{r['switch']}` | {r['states']} | {r['changed']}/{r['units']} | {r['mean_abs_change_if_changed']:.0f} "
        f"| {entry['status']} | {statutory_side(r['switch'])} | {entry['source']} |"
    )
total = len(frame)
changed = sum(1 for r in frame if r["changed"] > 0)
page = f"""# Audit of the statutory side

Statutory mode is only useful if each place it departs from TAXSIM can be defended. This page lists every
statutory switch (`src/taxsim_py/behavior.py`), ranked by how much is at stake, and says whether its statutory
side has been checked against a form or statute.

- Switches: {total}. Verified against a primary source: {counts['verified']}. Corrected after checking: {counts['corrected']}.
  Partly checked: {counts['partial']}. Not yet checked: {counts['unverified']}.
  Switches where statutory mode keeps TAXSIM's behavior because TAXSIM was not shown wrong: {counts['kept_taxsim']} (listed after the table).
- Switches whose flip changes at least one household in the sample: {changed}. The rest change nobody in the sample
  (they apply to inputs or years the CPS sample does not reach) and are checked last.

How the ranking was made: for each switch, statutory mode is run on CPS households (40 per state, law years 1985,
2000, 2012, 2022 and 2025) with only that switch set to TAXSIM's behavior. "Changed" counts households whose federal or state
tax moves by more than $1; "average" is the mean move for those households.
(`scripts/audit_switch_impact.py`). Verdicts live in `docs/data/statutory_audit_status.yaml`; this page is built by
`scripts/build_audit_ledger.py`.

**Rule.** Statutory mode departs from TAXSIM only where TAXSIM is shown to be wrong: it contradicts a form or statute,
or its own source is evidently broken (a variable never set, a typo). Where TAXSIM's behavior is a defensible reading, or
the inputs cannot decide, statutory mode keeps TAXSIM's behavior (`KEEP_TAXSIM_BEHAVIOR` in `behavior.py`). Switches
that still show "unverified" depart from TAXSIM without that proof yet; each is a candidate to be moved to the kept list.

**Rule.** Statutory mode departs from TAXSIM only where TAXSIM is known to be wrong. The rows in
`statutory_corrections.md` are the errors found by reading TAXSIM's source and testing it, so each one is a known
bug unless this audit finds otherwise. The audit checks the statutory side of each row against the form or statute. Where the
check shows TAXSIM's behavior is not actually wrong (a modelling choice, or a rule the inputs cannot decide), statutory mode is moved
back to TAXSIM's behavior and the switch is listed under "keeps TAXSIM's behavior" (`KEEP_TAXSIM_BEHAVIOR` in `behavior.py`).

Status: **verified** = the statutory rule matches a cited primary source; **corrected** = the check found the
statutory side wrong and it was changed; **partial** = true by design or only part checked; **unverified** = nothing
yet beyond the source reading that logged it as a TAXSIM error.

| Switch | States | Households changed | Average change ($) | Status | Statutory side | Source |
|:--|:--:|:--:|--:|:--:|:--|:--|
""" + "\n".join(lines) + "\n\n## Statutory mode keeps TAXSIM's behavior\n\n| Switch | Why |\n|:--|:--|\n" + "\n".join(f"| `{k}` | {v['source']} |" for k, v in sorted(kept.items())) + "\n"
(ROOT / "docs/statutory_audit.md").write_text(page)
print(counts)
