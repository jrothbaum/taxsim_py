"""Fill the "Confirmed by" column of docs/statutory_corrections.md from the audit ledger.

usage: uv run python scripts/annotate_corrections.py

Every table row that names switches in backticks gets, as its last cell, where the statutory side was confirmed: the
published source recorded in docs/data/statutory_audit_status.yaml for a parameter error, or "TAXSIM source" for a logic
error (with the oracle result from docs/data/logic_switch_confirmation.csv when that file exists). Rows with no switch
are confirmed from TAXSIM's source and the compiled program. Safe to run again: an existing column is rebuilt.
"""

from __future__ import annotations

import re
from pathlib import Path

import polars as pl
import yaml

ROOT = Path(__file__).resolve().parents[1]
PAGE = ROOT / "docs" / "statutory_corrections.md"
HEADER = "Confirmed by"
LIMIT = 330

status = yaml.safe_load((ROOT / "docs" / "data" / "statutory_audit_status.yaml").read_text())
oracle = {}
csv = ROOT / "docs" / "data" / "logic_switch_confirmation.csv"
if csv.exists():
    for row in pl.read_csv(csv).to_dicts():
        oracle[row["switch"]] = row

ledger = {}
for line in (ROOT / "docs" / "statutory_audit.md").read_text().splitlines():
    cells = [c.strip() for c in line.split("|")]
    if len(cells) > 7 and cells[1].startswith("`"):
        ledger[cells[1].strip("`")] = {"type": cells[2], "status": cells[6], "source": cells[8] if len(cells) > 8 else ""}


def shorten(text: str) -> str:
    text = re.sub(r"; checked \d{4}-\d{2}-\d{2}$", "", text.strip())
    if len(text) <= LIMIT:
        return text
    cut = text[:LIMIT]
    return cut[: cut.rfind(" ")] + " ... (full text in the audit ledger)"


def for_switch(name: str) -> str | None:
    entry = status.get(name)
    info = ledger.get(name)
    if entry and entry["status"] in {"verified", "partial", "no_switch"}:
        label = {"verified": "Confirmed", "partial": "Partly confirmed", "no_switch": "Not an error"}[entry["status"]]
        return f"{label}: {shorten(entry['source'])}"
    if info and info["status"] in {"verified", "partial"} and info["source"]:
        return f"{'Confirmed' if info['status'] == 'verified' else 'Partly confirmed'}: {shorten(info['source'])}"
    if info and info["status"] == "unverified":
        return "Not yet confirmed against a form or statute"
    if info or entry:
        text = "TAXSIM source (logic error, no form check needed)"
        if name in oracle:
            r = oracle[name]
            if not r["affected"]:
                text += "; no CPS household reaches the rule, so it was not run through the compiled TAXSIM"
            else:
                text += f"; compiled TAXSIM agrees with taxsim mode on {r['oracle_equals_taxsim_mode']:.0%} of {r['affected']} affected households"
        return text
    return None


def annotate(cells: list[str]) -> str:
    names = list(dict.fromkeys(re.findall(r"`([a-z][a-z0-9_]{12,})`", " ".join(cells))))
    notes = [n for n in (for_switch(name) for name in names) if n]
    if not notes:
        return "TAXSIM source and the compiled program"
    return " ".join(dict.fromkeys(notes))


def main() -> None:
    lines = PAGE.read_text().splitlines()
    out: list[str] = []
    in_table = False
    strip_last = False
    for line in lines:
        if not line.startswith("|"):
            in_table = False
            out.append(line)
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if not in_table:
            in_table = True
            strip_last = cells[-1] == HEADER
            cells = cells[:-1] if strip_last else cells
            out.append("| " + " | ".join([*cells, HEADER]) + " |")
            continue
        if strip_last:
            cells = cells[:-1]
        if cells and set("".join(cells)) <= set(":-"):
            out.append("|" + "|".join([":--"] * (len(cells) + 1)) + "|")
            continue
        out.append("| " + " | ".join([*cells, annotate(cells)]) + " |")
    PAGE.write_text("\n".join(out) + "\n")


if __name__ == "__main__":
    main()
