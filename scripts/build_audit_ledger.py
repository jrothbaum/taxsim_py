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


_PARAM = re.compile(r"typo|indexed|index(ing)? |inflation factor|stale|brackets?|thresholds?|\$[\d,]{3,}|\d{1,3},\d{3}|table|amount|rates?|maximum|limit|cap\b", re.I)
_LOGIC = re.compile(r"never|overwrit|ignores|reversed|slot|never true|uninitiali|not set|unset|never set|stops|multiplies", re.I)


def kind(switch: str) -> str:
    """A parameter error (a number in TAXSIM's tables differs from the published one) or a logic error (found in its source)."""
    for line in rows:
        if switch in line:
            cells = line.split("|")
            desc = cells[4].strip() if len(cells) > 4 else ""
            if _PARAM.search(desc) and re.search(r"\d", desc) and not (_LOGIC.search(desc) and not re.search("typo|stale|indexed", desc, re.I)):
                return "parameter"
    return "logic"


def statutory_side(switch: str) -> str:
    for line in rows:
        if switch in line:
            match = re.search(r"Statutory[^:|]*:\s*([^|]*?)(?:\s*`|\s*\||$)", line)
            if match:
                return match.group(1).strip()[:160]
    return ""


live = {f.name for f in fields(BehaviorProfile)}
frame = [r for r in impact.sort("stake", descending=True).to_dicts() if r["switch"] in live]
counts = {"logic": 0, "verified": 0, "corrected": 0, "partial": 0, "kept_taxsim": 0, "unverified": 0}
kept = {k: v for k, v in status.items() if v["status"] == "no_switch"}
counts["kept_taxsim"] = len(kept)
oracle_check: dict[str, str] = {}
confirmation = ROOT / "docs/data/logic_switch_confirmation.csv"
if confirmation.exists():
    for row in pl.read_csv(confirmation).to_dicts():
        n = row["affected"]
        if not n:
            oracle_check[row["switch"]] = "not reached by the CPS sample"
            continue
        tm, st = row["oracle_equals_taxsim_mode"], row["oracle_equals_statutory"]
        if tm >= 0.9 and st <= tm - 0.15:
            oracle_check[row["switch"]] = f"confirmed: compiled TAXSIM equals taxsim mode on {tm:.0%} of {n} affected households, statutory on {st:.0%}"
        elif tm >= 0.9:
            oracle_check[row["switch"]] = f"inconclusive: compiled TAXSIM equals taxsim mode on {tm:.0%} of {n} affected households but statutory also on {st:.0%} (other switches interact)"
        else:
            oracle_check[row["switch"]] = f"taxsim mode equals compiled TAXSIM on only {tm:.0%} of {n} affected households (residual logged in pending_issues.md)"
lines = []
for r in frame:
    entry = status.get(r["switch"], {"status": "unverified", "source": ""})
    typ = kind(r["switch"])
    if entry["status"] == "unverified" and typ == "logic":
        entry = {"status": "logic", "source": "Found by reading TAXSIM's source; no form or statute check needed"}
    if entry["status"] != "no_switch":
        counts[entry["status"]] += 1
    lines.append(
        f"| `{r['switch']}` | {typ} | {r['states']} | {r['changed']}/{r['units']} | {r['mean_abs_change_if_changed']:.0f} "
        f"| {entry['status']} | {statutory_side(r['switch'])} | {entry['source']} | {oracle_check.get(r['switch'], '')} |"
    )
total = len(frame)
changed = sum(1 for r in frame if r["changed"] > 0)
page = f"""# Audit of the statutory side

Statutory mode departs from TAXSIM only where TAXSIM is known to be wrong. The rows in `statutory_corrections.md` are
the errors found by reading TAXSIM's source and testing it, so each is a known bug. Only **parameter errors**, where a number
in TAXSIM's tables differs from the published value, need a source to confirm the published number. **Logic errors**
(a variable never set, a typo in a condition, a result overwritten) need nothing more. Where a check shows TAXSIM's
behavior is not actually wrong (a modelling choice, or a rule the inputs cannot decide), statutory mode is moved back to
TAXSIM's behavior in both modes: the switch is removed (a switch exists only to let TAXSIM mode override the correct behavior to reproduce a bug) and listed at the end.

- Switches: {total}. Logic errors (no confirmation needed): {counts['logic']}. Parameter errors verified against a primary
  source: {counts['verified']}; partly verified: {counts['partial']}; still to verify: {counts['unverified']}. Switch removed because TAXSIM's behavior
  is not a bug: {counts['kept_taxsim']}.
- Switches whose flip changes at least one household in the sample: {changed}. The rest change nobody in the sample.

How the impact was measured: for each switch, statutory mode is run on CPS households (40 per state, law years 1985, 2000,
2012, 2022 and 2025) with only that switch set to TAXSIM's behavior. "Changed" counts households whose federal or state tax moves by more than $1;
"average" is the mean move for those households (`scripts/audit_switch_impact.py`). Verdicts live in
`docs/data/statutory_audit_status.yaml`; this page is built by `scripts/build_audit_ledger.py`. The type column is a first sort
by the wording of each row and may misfile a few.

The last column is the check against the compiled TAXSIM for logic errors (`scripts/confirm_logic_switches.py`, results in
`docs/data/logic_switch_confirmation.csv`): the CPS households that the switch changes are run through the compiled
program, and the column says how often it agrees with taxsim mode and with statutory mode. "Not reached by the CPS sample"
means no sampled household (up to 800 per state in 17 law years) has the situation the rule covers; those rules rest on
TAXSIM's source and on the oracle comparisons made while building taxsim mode.

Status: **logic** = nothing more to confirm; **verified** = the published value matches a cited source; **partial** =
confirmed in part; **unverified** = a parameter error whose published value has not been checked yet.

| Switch | Type | States | Households changed | Average change ($) | Status | Statutory side | Source | Compiled TAXSIM check (logic errors) |
|:--|:--:|:--:|:--:|--:|:--:|:--|:--|:--|
""" + "\n".join(lines) + "\n\n## Switches removed: TAXSIM's behavior is not a bug\n\n| Switch | Why |\n|:--|:--|\n" + "\n".join(f"| `{k}` | {v['source']} |" for k, v in sorted(kept.items())) + "\n"
(ROOT / "docs/statutory_audit.md").write_text(page)
print(counts)
