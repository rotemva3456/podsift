"""Turns a `runner.ModelReport.row()` / `.failures()` into what `docs/models.md` and
`scripts/eval.py`'s console output show.

Pure formatting, no I/O and no network, so it's unit tested directly (`scripts/eval.py` is a thin
CLI wrapper with no logic of its own worth testing, matching every other script in `scripts/`).
"""
from __future__ import annotations

from typing import Any

# (row key, column header). "model", "brief not answered" and "cut not answered" are counted out
# of every fixture; everything else in between is counted out of only the fixtures whose brief (or
# cut) got a real answer to judge, since a failure has no verdict, citations or chapters to be
# right about. "not answered" folds in both `failure_kind`s that never reached our own validation
# ("limits": a provider error -- rate limit, daily cap, timeout, cut off by max_tokens, ...; "bug":
# anything else, which should never happen -- see `failures()` below for which one, and why,
# per fixture). "brief answer rejected" is the other `failure_kind`, "rejected": the model did
# answer, and `companion.brief.validate()` turned it down even after one repair.
COLUMNS = [
    ("model", "model"),
    ("valid_json_first_try", "valid JSON 1st try"),
    ("repaired", "repaired once"),
    ("brief_not_answered", "brief not answered (limits)"),
    ("brief_rejected", "brief answer rejected"),
    ("valid_citations_first_try", "valid citations 1st try"),
    ("chapters_found", "chapters found"),
    ("verdict_agreement", "verdict agreement"),
    ("forbidden_segments_kept", "forbidden segments kept"),
    ("cut_not_answered", "cut not answered (limits)"),
    ("budget_kept", "budget kept"),
    ("seconds", "seconds"),
    ("input_tokens", "input tokens"),
]


def row_line(row: dict[str, Any]) -> str:
    """One markdown table row for `row` (a `ModelReport.row()` dict)."""
    n = row["fixtures"]
    ok_briefs = n - row["brief_failed"]
    ok_cuts = n - row["cut_failed"]
    brief_not_answered = row["brief_limits"] + row["brief_bug"]
    cut_not_answered = row["cut_limits"] + row["cut_bug"]
    cells = {
        "model": f"{row['model']} ({row['provider']})",
        "valid_json_first_try": f"{row['valid_json_first_try']}/{ok_briefs}",
        "repaired": f"{row['repaired']}/{ok_briefs}",
        "brief_not_answered": f"{brief_not_answered}/{n}",
        "brief_rejected": f"{row['brief_rejected']}/{n}",
        "valid_citations_first_try": f"{row['valid_citations_first_try']}/{ok_briefs}",
        "chapters_found": f"{row['chapters_found']}/{row['chapters_expected']}",
        "verdict_agreement": f"{row['verdict_agreement']}/{ok_briefs}",
        "forbidden_segments_kept": str(row["forbidden_segments_kept"]),
        "cut_not_answered": f"{cut_not_answered}/{n}",
        "budget_kept": f"{row['budget_kept']}/{ok_cuts}",
        "seconds": f"{row['seconds']:.1f}",
        "input_tokens": str(row["input_tokens"]),
    }
    return "| " + " | ".join(cells[key] for key, _label in COLUMNS) + " |"


def failure_line(entry: dict[str, Any]) -> str:
    """One line for one failed brief or cut (an item of `ModelReport.failures()`): which model,
    which fixture, which call, why (`failure_kind`), and the exact message -- so a run is
    diagnosable without re-reading the raw log. `bug` is called out in capitals: it is the one
    kind that means this eval (or the product function it called) has a real defect."""
    kind = entry["failure_kind"] or "unknown"
    marker = "BUG" if kind == "bug" else kind
    return (f"- {entry['model']} ({entry['provider']}) / {entry['episode_id']} / {entry['call']} "
           f"[{marker}]: {entry['error']}")


def render_markdown(rows: list[dict[str, Any]], date: str, failures: list[dict[str, Any]] = ()) -> str:
    """The whole table (header + one line per model), a one-line verdict per model, and one line
    per failure (if any), for `docs/models.md` and, later, a README section."""
    n = rows[0]["fixtures"] if rows else 0
    lines = [f"Measured {date}, `scripts/eval.py`, {n} fixture episodes "
             "(companion/evals/fixtures/, see ATTRIBUTION.md).", "",
             "| " + " | ".join(label for _key, label in COLUMNS) + " |",
             "|" + "---|" * len(COLUMNS)]
    lines += [row_line(row) for row in rows]
    not_recommended = [f"{row['model']} ({row['provider']})" for row in rows if row["not_recommended"]]
    lines.append("")
    if not_recommended:
        lines.append("Not recommended (kept a forbidden segment at least once): "
                     + ", ".join(not_recommended) + ".")
    else:
        lines.append("No model kept a forbidden segment.")
    if failures:
        lines += ["", "Every failure, in detail:", ""]
        lines += [failure_line(entry) for entry in failures]
    return "\n".join(lines) + "\n"
