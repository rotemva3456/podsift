"""The 5 fixtures load, and every label is internally consistent with its own transcript."""
from __future__ import annotations

from pathlib import Path

import pytest

from .fixtures import FIXTURES_DIR, FixtureError, load_fixture, load_fixtures

EXPECTED_IDS = {"hpr2639", "hpr2649", "hpr2659", "hpr4731", "hpr4726"}


def test_loads_all_five():
    fixtures = load_fixtures()
    assert {f.episode_id for f in fixtures} == EXPECTED_IDS
    assert len(fixtures) == 5


def test_sorted_by_file_name():
    fixtures = load_fixtures()
    assert [f.episode_id for f in fixtures] == sorted(f.episode_id for f in fixtures)


def test_filters_by_episode_id():
    fixtures = load_fixtures(["hpr2639", "hpr4731"])
    assert {f.episode_id for f in fixtures} == {"hpr2639", "hpr4731"}


def test_unknown_episode_id_raises():
    with pytest.raises(FixtureError, match="hpr0000"):
        load_fixtures(["hpr2639", "hpr0000"])


@pytest.mark.parametrize("path", sorted(FIXTURES_DIR.glob("*.json")), ids=lambda p: p.stem)
def test_each_fixture_label_matches_its_transcript(path: Path):
    fixture = load_fixture(path)
    assert fixture.segments, "transcript has segments"
    assert fixture.duration > 0

    label = fixture.label
    assert label.verdict in ("HEAR", "READ", "SKIP")
    assert len(label.chapters) == 3, "each fixture needs exactly 3 chapters"
    assert label.key_ideas, "at least one key idea"
    assert label.cut_request.want.strip()
    assert label.cut_request.must_keep, "a cut request needs must-keep segments"
    assert label.cut_request.never_keep, "a cut request needs never-keep segments"

    # every index resolves to a real segment (load_fixture already raised if not, but check the
    # values are sane too: strictly increasing chapter times, all keep/citation refs in range)
    starts = [start for start, _title in fixture.chapter_targets()]
    assert starts == sorted(starts), "chapters are listed in time order"
    assert starts[-1] - starts[0] > 4 * 60, "chapters should span most of the episode, not cluster"

    for idea in label.key_ideas:
        assert idea.text.strip()
        for index in idea.indices:
            fixture.segment_id(index)  # raises FixtureError if out of range

    must_keep, never_keep = fixture.must_keep_ids(), fixture.never_keep_ids()
    assert not (must_keep & never_keep), "a segment can't be both must-keep and never-keep"


def test_out_of_range_index_is_rejected(tmp_path: Path):
    import json
    import shutil

    broken = tmp_path / "hpr2639.json"
    raw = json.loads((FIXTURES_DIR / "hpr2639.json").read_text())
    raw["label"]["chapters"][0]["segment_index"] = 999999
    broken.write_text(json.dumps(raw))
    # the transcript path inside the fixture is relative to the app folder, which still resolves
    # from tmp_path's copy, so this only exercises the index bound check, not file lookup.
    with pytest.raises(FixtureError, match="999999"):
        load_fixture(broken)
    shutil.rmtree(tmp_path, ignore_errors=True)
