"""The listen-back check and its re-cuts, on synthetic speech.

Every "word" here is a 0.25 s tone burst with a pitch of its own, and ``Ears`` - the fake
speech-to-text - really listens to each clip it is sent: it finds the bursts and names each by
its pitch. So it hears exactly what the rendered MP3 contains. A word the cut clipped is heard
short and not understood, a word of the part you cut that leaked in is heard, and a re-cut is
proven by listening again, the way the real service does. The transcript is written by hand,
and a test that wants a wrong cut gives it wrong times, the way publisher cues can be.

Everything runs offline with ffmpeg; no network, no real speech-to-text.
"""
from __future__ import annotations

import array
import math
import shutil
import socket
import subprocess
import wave
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from companion import cuts, engine, listen
from companion.engine import edges
from companion.engine.verify import tokens
from companion.llm import LLMError, get_llm
from companion.server import create_app

pytestmark = pytest.mark.skipif(not (shutil.which("ffmpeg") and shutil.which("ffprobe")),
                                reason="ffmpeg and ffprobe are not installed")

VOCAB = ("router packet switch cable signal network server client protocol address bridge gateway "
         "subnet latency").split()
HZ = {word: 380 + 55 * i for i, word in enumerate(VOCAB)}      # 380 to 1095 Hz, 55 Hz apart
RATE, WORD, STEP = 16000, 0.25, 0.08                             # a word is 0.25 s; 0.08 s between words
EP = "3f0c2a8e-5b7d-4c1e-9a6f-0d2e4b6c8a10"

SENTENCES = [                                   # (text, pause after it)
    ("router packet switch.", 0.6),             # 0
    ("cable signal network server.", 0.6),      # 1
    ("latency client protocol.", 0.6),          # 2  <- passage 1 starts here
    ("address bridge gateway subnet.", 0.1),    # 3  <- ... and ends here, run together with 4
    ("router cable signal.", 0.6),              # 4
    ("packet server bridge.", 3.0),             # 5
    ("gateway subnet address.", 0.6),           # 6  <- passage 2 starts here
    ("protocol latency switch network.", 0.6),  # 7  <- ... and ends here
    ("client router cable.", 0.8),              # 8
]


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def refuse(*_args, **_kwargs):
        raise AssertionError("a test tried the network")
    for name in ("getaddrinfo", "create_connection"):
        monkeypatch.setattr(socket, name, refuse)
    monkeypatch.setattr(socket.socket, "connect", refuse)


# ------------------------------------------------------------------ synthetic speech


def speak(folder: Path, sentences=SENTENCES, lead: float = 0.5) -> tuple[Path, list[tuple[float, float, str]]]:
    """An mp3 of the sentences, and each sentence's true (start, end, text)."""
    pcm = array.array("h", bytes(2 * round(lead * RATE)))
    truth = []
    for text, pause in sentences:
        words = text.rstrip(".").split()
        start = len(pcm) / RATE
        for k, word in enumerate(words):
            n = round(WORD * RATE)
            pcm.extend(int(12000 * min(1.0, i / 80, (n - 1 - i) / 80) * math.sin(2 * math.pi * HZ[word] * i / RATE))
                       for i in range(n))
            if k < len(words) - 1:
                pcm.extend(array.array("h", bytes(2 * round(STEP * RATE))))
        truth.append((round(start, 3), round(len(pcm) / RATE, 3), text))
        pcm.extend(array.array("h", bytes(2 * round(pause * RATE))))
    raw = folder / "talk.wav"
    with wave.open(str(raw), "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(RATE)
        out.writeframes(pcm.tobytes())
    mp3 = folder / "talk.mp3"
    subprocess.run(["ffmpeg", "-v", "error", "-nostdin", "-y", "-i", str(raw), "-ar", "44100", "-c:a", "libmp3lame",
                    "-q:a", "4", str(mp3)], check=True)
    return mp3, truth


def decode(path) -> array.array:
    raw = subprocess.run(["ffmpeg", "-v", "error", "-nostdin", "-i", str(path), "-ac", "1", "-ar", str(RATE),
                          "-f", "s16le", "-"], capture_output=True, check=True).stdout
    return array.array("h", raw[:len(raw) // 2 * 2])


class Ears:
    """Speech-to-text for synthetic speech: each tone burst in the clip is heard as its word,
    unless it is shorter than 60% of a word (clipped: not understood) or the word is in
    `deaf` (a word this service never gets right)."""

    def __init__(self, deaf=(), fail: Exception | None = None, fail_after: int | None = None):
        self.deaf, self.fail, self.fail_after, self.calls = set(deaf), fail, fail_after, []

    def transcribe_words(self, path):
        self.calls.append(Path(path).name)
        if self.fail and (self.fail_after is None or len(self.calls) > self.fail_after):
            raise self.fail
        return {"text": "", "duration": len(pcm := decode(path)) / RATE, "segments": [], "words": hear(pcm, self.deaf)}


def hear(pcm, deaf=()) -> list[dict]:
    frames = []
    for i in range(0, len(pcm) - 159, 160):
        power = sum(x * x for x in pcm[i:i + 160]) / 160
        frames.append(10 * math.log10(power) if power > 0 else -120.0)
    loud = max(frames, default=-120.0)
    words, first = [], None
    for k, level in enumerate([*frames, -120.0]):
        if level > loud - 20 and first is None:
            first = k
        elif level <= loud - 20 and first is not None:
            if (k - first) * 0.01 >= 0.6 * WORD:
                chunk = pcm[(first + 3) * 160:(k - 3) * 160]
                hz = sum(1 for a, b in zip(chunk, chunk[1:]) if (a < 0) != (b < 0)) / 2 / (len(chunk) / RATE)
                word = min(VOCAB, key=lambda w: abs(HZ[w] - hz))
                if abs(HZ[word] - hz) <= 20 and word not in deaf:
                    words.append({"word": word, "start": first * 0.01, "end": k * 0.01})
            first = None
    return words


def heard_words(path, deaf=()) -> list[str]:
    return [w["word"] for w in hear(decode(path), deaf)]


# ------------------------------------------------------------------ calling export directly


def script_lines(truth, moved=None) -> list[engine.Segment]:
    """The transcript: the true times, except sentences `moved` {index: (start shift, end shift)}."""
    moved = moved or {}
    return [engine.Segment(str(i), a + moved.get(i, (0, 0))[0], b + moved.get(i, (0, 0))[1], text)
            for i, (a, b, text) in enumerate(truth)]


def export(tmp_path, path, lines, passages, speech):
    """Render sentences `passages` [(first, last)] of the transcript `lines`, listen, fix."""
    source = listen.Source("ep", str(path), engine.probe_duration(path),
                           [piece for piece, _n in engine.sentence_pieces(lines)])
    pieces = [{"audio": str(path), "start": lines[a].start, "end": lines[b].end, "episode_id": "ep",
               "planned_start": lines[a].start, "planned_end": lines[b].end, "span_ids": [f"s{n}"]}
              for n, (a, b) in enumerate(passages, 1)]
    out = tmp_path / "cut.mp3"
    result, check = listen.export(pieces, {"ep": source}, out, speech=speech)
    return out, result, check


def wanted(truth, passages) -> list[str]:
    return [token for a, b in passages for i in range(a, b + 1) for token in tokens(truth[i][2])]


@pytest.fixture(scope="module")
def talk(tmp_path_factory):
    return speak(tmp_path_factory.mktemp("talk"))


def test_a_right_transcript_gives_a_cut_that_passes_on_the_first_render(tmp_path, talk):
    path, truth = talk
    ears = Ears()
    out, result, check = export(tmp_path, path, script_lines(truth), [(2, 3), (6, 7)], ears)
    assert check["status"] == "passed" and check["renders"] == 1, check
    assert heard_words(out) == wanted(truth, [(2, 3), (6, 7)])
    assert check["words"] == {"expected": 14, "heard": 14} and check["joins"] == 1
    assert not [f for f in check["findings"] if f["severity"] == "error"]
    assert [e["start"]["kind"] for e in check["edges"]] == ["pause", "pause"]
    spoken = (truth[3][1] - truth[2][0]) + (truth[7][1] - truth[6][0])
    assert result.duration <= spoken + 2 * (edges.LEAD + edges.TAIL) + 0.1          # no dead air kept
    assert sorted(p.name for p in tmp_path.iterdir()) == ["cut.json", "cut.md", "cut.mp3"]   # only the kept render


def test_a_late_start_that_clips_words_is_confirmed_on_the_original_and_cut_again(tmp_path, talk):
    path, truth = talk
    ears = Ears()
    # the transcript puts sentence 6 0.6 s late, so the first cut lands after its second word
    out, _result, check = export(tmp_path, path, script_lines(truth, {6: (0.6, 0.0)}), [(2, 3), (6, 7)], ears)
    assert check["status"] == "fixed" and check["renders"] == 2, check
    assert [(f["piece"], f["edge"], f["why"]) for f in check["fixes"]] == [(2, "start", "clipped_start")]
    fix = check["fixes"][0]
    assert fix["to"] < truth[6][0] < fix["from"]                           # moved back to before the first word
    assert check["words"] == {"expected": 14, "heard": 14}                  # counted after the fix, not before
    assert heard_words(out) == wanted(truth, [(2, 3), (6, 7)])
    assert any(name.startswith("probe") for name in ears.calls)             # confirmed on the original first


def test_a_fix_nobody_could_hear_again_is_not_shipped(tmp_path, talk):
    path, truth = talk
    # as above, but the service fails after the first listen and the probe: the fixed render is
    # unheard, so the export keeps the render it did hear, and says what is wrong with it
    ears = Ears(fail=LLMError("You have used up this provider's daily limit."), fail_after=2)
    out, _result, check = export(tmp_path, path, script_lines(truth, {6: (0.6, 0.0)}), [(2, 3), (6, 7)], ears)
    assert (check["status"], check["renders"], check["kept"]) == ("problems", 1, 1), check
    clipped = next(f for f in check["findings"] if f["severity"] == "error")
    assert (clipped["kind"], clipped["piece"], clipped["confirmed"]) == ("clipped_start", 2, True)
    assert heard_words(out) == wanted(truth, [(2, 3)]) + wanted(truth, [(6, 7)])[2:]      # the clipped render


def test_a_late_end_that_leaks_the_next_sentence_is_cut_again(tmp_path, talk):
    path, truth = talk
    # sentences 3 and 4 run together (0.1 s apart); the transcript has both 0.5 s late
    out, _result, check = export(tmp_path, path, script_lines(truth, {3: (0.5, 0.5), 4: (0.5, 0.5)}),
                                 [(2, 3), (6, 7)], Ears())
    assert check["status"] == "fixed", check
    assert [(f["piece"], f["edge"], f["why"]) for f in check["fixes"]] == [(1, "end", "extra_end")]
    assert heard_words(out) == wanted(truth, [(2, 3), (6, 7)])


def test_dead_air_at_a_join_is_trimmed_from_the_source(tmp_path, talk):
    path, truth = talk
    # sentence 5 is followed by 3 s of silence, and the transcript says it ends 2 s late
    out, result, check = export(tmp_path, path, script_lines(truth, {5: (0.0, 2.0)}), [(4, 5), (6, 7)], Ears())
    assert check["status"] == "fixed", check
    assert [(f["piece"], f["edge"], f["why"]) for f in check["fixes"]] == [(1, "end", "dead_air")]
    assert heard_words(out) == wanted(truth, [(4, 5), (6, 7)])
    join = result.index[1]["cut_start"]
    around = engine.envelope(out, [(join - 2, join + 2)])[0]
    pause = next(r for r in edges.quiet_runs(around, around.start, around.end, -60) if r[0] <= join <= r[1])
    assert pause[1] - pause[0] <= edges.LEAD + edges.TAIL + 0.05                 # the join keeps a normal pause


def test_a_word_the_service_never_hears_is_reported_and_not_re_cut(tmp_path, talk):
    path, truth = talk
    ears = Ears(deaf={"latency"})                  # sentence 2 starts with "latency"
    out, _result, check = export(tmp_path, path, script_lines(truth), [(2, 3), (6, 7)], ears)
    assert check["status"] == "passed" and check["renders"] == 1, check
    warning = next(f for f in check["findings"] if f["piece"] == 1 and f["edge"] == "start")
    assert (warning["kind"], warning["severity"], warning["words"]) == ("transcript_differs", "warning", ["latency"])
    assert heard_words(out) == wanted(truth, [(2, 3), (6, 7)])            # the audio has it all along


def test_a_transcript_that_does_not_fit_the_audio_is_reported_not_hidden(tmp_path, talk):
    path, truth = talk
    lines = script_lines(truth)
    lines[6] = engine.Segment("6", lines[6].start, lines[6].end, "bridge bridge bridge bridge.")
    lines[7] = engine.Segment("7", lines[7].start, lines[7].end, "cable cable cable cable cable cable.")
    _out, _result, check = export(tmp_path, path, lines, [(2, 3), (6, 7)], Ears())
    assert check["status"] == "problems"
    assert [f["kind"] for f in check["findings"] if f["severity"] == "error"] == ["wrong_audio"]


def test_without_speech_to_text_only_the_joins_are_checked(tmp_path, talk):
    path, truth = talk
    _out, _result, check = export(tmp_path, path, script_lines(truth), [(2, 3), (6, 7)], None)
    assert check["status"] == "audio_only" and "speech-to-text" in check["reason"] and check["words"] is None
    failing = Ears(fail=LLMError("You have used up this provider's daily limit. Try again tomorrow."))
    _out, _result, check = export(tmp_path, path, script_lines(truth), [(2, 3), (6, 7)], failing)
    assert check["status"] == "audio_only" and "daily limit" in check["reason"]


def test_where_the_probes_word_times_overlap_the_cut_is_looked_for_around_the_pair():
    # the probe of N4N032 at 34:03: "area." ends 2044.18 and "It" starts 2043.20 - the real
    # boundary, measured on another probe and on the audio, is 2043.68-2043.74
    lines = [engine.Segment("1", 2040.69, 2043.67, "all inter-area traffic having to get forced through the backbone area."),
             engine.Segment("2", 2043.67, 2045.85, "It does have to do with loop prevention.")]
    source = listen.Source("ep", "x.mp3", 3000.0, lines)
    piece = {"start": 2040.6, "end": 2043.40, "planned_start": 2040.69, "planned_end": 2043.67, "episode_id": "ep"}
    probe = [listen.Heard(w, a, b) for w, a, b in [("the", 2042.98, 2043.08), ("backbone", 2043.08, 2043.4),
             ("area.", 2043.4, 2044.18), ("It", 2043.2, 2043.96), ("does", 2043.96, 2044.16), ("have", 2044.16, 2044.34)]]
    found = listen.judge_edge(source, piece, "end", probe)
    assert (found["wanted"].text, found["unwanted"].text, found["missing"]) == ("area.", "It", [])
    target, low, high = listen._region("end", found["wanted"], found["unwanted"])
    assert target == pytest.approx(2043.68) and low < 2043.70 < high and high < 2043.96     # never past "It"


# ------------------------------------------------------------------ through the app


def test_the_script_you_approve_is_what_the_mp3_says(tmp_path, talk):
    from companion.test_cuts import PID, FakePodFetch, native, vtt, wait_for
    path, truth = talk
    fake = FakePodFetch(path.read_bytes())
    episode = fake.add(transcript="generated")
    duration = engine.probe_duration(path)
    episode["total_time"] = int(duration)
    lines, times = [t for _a, _b, t in truth], [(a, b) for a, b, _t in truth]
    fake.native[PID] = native(lines, times, source="generated")
    tid = next(iter(k for k in fake.files if k[0] == PID))[1]
    fake.files[(PID, tid)] = (vtt(lines, times), "text/vtt")
    ears = Ears()
    app = create_app("http://podfetch.test", tmp_path / "companion.db", transport=httpx.MockTransport(fake))
    app.dependency_overrides[get_llm] = lambda: None
    app.dependency_overrides[cuts.get_speech] = lambda: ears
    client = TestClient(app)
    made = client.post("/companion/plans", json={"episode_ids": [EP], "want": "latency", "skip": "server",
                                                 "skip_ads": False, "mode": "keyword"}).json()
    script = client.get(f"/companion/plans/{made['id']}/script").json()
    parts = script["episodes"][0]["parts"]
    assert script["words"] is True and {p["kind"] for p in parts} == {"keep", "cut"}
    assert all(p["lines"] for p in parts if p["kind"] == "keep")
    assert {p["reason"] for p in parts if p["kind"] == "cut"} <= {"skip", "other"}
    approved = [t for p in parts if p["kind"] == "keep" for line in p["lines"] for t in tokens(line["text"])]
    assert "server" not in approved                                      # skipped lines stay out of the script
    job = wait_for(client, client.post(f"/companion/plans/{made['id']}/render").json()["job_id"])
    assert job["status"] == "done", job
    cut = client.get(f"/companion/cuts/{job['cut_id']}").json()
    assert cut["check"]["status"] == "passed", cut["check"]
    mp3 = tmp_path / "heard.mp3"
    mp3.write_bytes(client.get(f"/companion/cuts/{job['cut_id']}.mp3").content)
    assert heard_words(mp3) == approved                                  # every approved word, nothing else
