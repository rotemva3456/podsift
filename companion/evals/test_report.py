"""row_line / failure_line / render_markdown: pure formatting, no network, exercised against a
hand-built ModelReport.row() / .failures() shape (the real shape is covered by
test_runner.py's evaluate() test)."""
from __future__ import annotations

from .report import failure_line, render_markdown, row_line


def _row(**over) -> dict:
    row = dict(provider="groq", model="test/model", fixtures=5, valid_json_first_try=3, repaired=1,
              brief_failed=1, brief_limits=1, brief_rejected=0, brief_bug=0,
              valid_citations_first_try=3, chapters_found=11, chapters_expected=12,
              verdict_agreement=3, forbidden_segments_kept=0, cut_failed=1, cut_limits=1,
              cut_bug=0, must_keep_hit=6, must_keep_expected=8, budget_kept=3, seconds=42.5,
              input_tokens=12345, not_recommended=False)
    row.update(over)
    return row


def _failure(**over) -> dict:
    entry = dict(provider="groq", model="test/model", call="brief", episode_id="hpr0000",
                failure_kind="limits", error="You have used up this provider's daily limit.")
    entry.update(over)
    return entry


def test_row_line_denominators_exclude_failures():
    # 5 fixtures, 1 brief failed (a "limits" one) -> 4 ok briefs; 1 cut failed -> 4 ok cuts
    line = row_line(_row())
    assert line == ("| test/model (groq) | 3/4 | 1/4 | 1/5 | 0/5 | 3/4 | 11/12 | 3/4 | 0 | 1/5 "
                    "| 3/4 | 42.5 | 12345 |")


def test_row_line_not_answered_folds_in_limits_and_bug():
    line = row_line(_row(brief_failed=2, brief_limits=1, brief_bug=1, brief_rejected=0,
                         cut_failed=2, cut_limits=1, cut_bug=1))
    cells = line.strip("|").split("|")
    cells = [c.strip() for c in cells]
    # column order: model, valid json, repaired, brief not answered, brief rejected, ...
    assert cells[3] == "2/5"    # brief not answered = limits(1) + bug(1)
    assert cells[4] == "0/5"    # brief answer rejected
    assert cells[9] == "2/5"    # cut not answered = limits(1) + bug(1)


def test_row_line_rejected_is_separate_from_not_answered():
    line = row_line(_row(brief_failed=1, brief_limits=0, brief_bug=0, brief_rejected=1))
    cells = [c.strip() for c in line.strip("|").split("|")]
    assert cells[3] == "0/5"    # nothing "not answered"
    assert cells[4] == "1/5"    # the one failure was a rejected answer


def test_row_line_all_ok_is_out_of_all_fixtures():
    line = row_line(_row(brief_failed=0, brief_limits=0, brief_bug=0, brief_rejected=0,
                         cut_failed=0, cut_limits=0, cut_bug=0, valid_json_first_try=5, repaired=0,
                         valid_citations_first_try=5, verdict_agreement=5, budget_kept=5))
    assert "| 5/5 | 0/5 | 0/5 | 0/5 |" in line
    assert line.endswith("| 5/5 | 42.5 | 12345 |")


def test_row_line_all_failed_is_zero_over_zero_ok():
    # every brief and cut failed on limits: the "ok" columns are 0/0, still renders (no crash)
    line = row_line(_row(brief_failed=5, brief_limits=5, cut_failed=5, cut_limits=5,
                         valid_json_first_try=0, repaired=0, valid_citations_first_try=0,
                         chapters_found=0, chapters_expected=0, verdict_agreement=0, budget_kept=0))
    assert "0/0" in line
    assert "5/5" in line       # brief and cut not-answered


def test_failure_line_marks_bug_in_capitals():
    limits = failure_line(_failure(failure_kind="limits"))
    bug = failure_line(_failure(failure_kind="bug", error="KeyError: 'segments'"))
    assert "[limits]" in limits
    assert "[BUG]" in bug
    assert "hpr0000" in limits and "brief" in limits
    assert "KeyError: 'segments'" in bug


def test_failure_line_names_the_call_and_model():
    entry = _failure(call="cut", model="qwen/qwen3.8-27b", episode_id="hpr4726",
                     failure_kind="rejected", error="chapter 1 starts at segment ??")
    line = failure_line(entry)
    assert line == ("- qwen/qwen3.8-27b (groq) / hpr4726 / cut [rejected]: "
                    "chapter 1 starts at segment ??")


def test_render_markdown_header_and_row_count():
    doc = render_markdown([_row(model="a"), _row(model="b", forbidden_segments_kept=2,
                                                 not_recommended=True)], "2026-09-26")
    assert doc.startswith("Measured 2026-09-26, `scripts/eval.py`, 5 fixture episodes")
    assert doc.count("\n|") >= 4    # header + separator + 2 rows
    assert "Not recommended (kept a forbidden segment at least once): b (groq)." in doc


def test_render_markdown_no_forbidden_segments():
    doc = render_markdown([_row()], "2026-09-26")
    assert "No model kept a forbidden segment." in doc
    assert "Not recommended" not in doc


def test_render_markdown_empty_rows():
    doc = render_markdown([], "2026-09-26")
    assert "0 fixture episodes" in doc
    assert "No model kept a forbidden segment." in doc


def test_render_markdown_lists_every_failure():
    doc = render_markdown([_row()], "2026-09-26",
                          failures=[_failure(episode_id="hpr2639", failure_kind="limits"),
                                   _failure(episode_id="hpr4731", call="cut",
                                            failure_kind="rejected", error="bad ids")])
    assert "Every failure, in detail:" in doc
    assert "hpr2639" in doc and "[limits]" in doc
    assert "hpr4731 / cut [rejected]: bad ids" in doc


def test_render_markdown_omits_failure_section_when_none():
    doc = render_markdown([_row(brief_failed=0, cut_failed=0)], "2026-09-26", failures=[])
    assert "Every failure, in detail:" not in doc
