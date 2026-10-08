"""Render a cut: one mp3 of the kept spans, in span order, levelled for listening.

Measured landmines this code steps around:
- One decode, one encode. Encoding each span to its own mp3 and concat-copying them costs a
  second lossy generation and leaves the encoder's padding gap at every splice.
- A show can mix sample rates (44.1 and 48 kHz episodes), and ffmpeg's concat filter refuses
  mismatched branches, so every branch is resampled and re-laid-out before the splice.
- Podcast feeds are not level-matched: one measured show shipped anywhere from -13.7 to -23.0
  LUFS, and some episodes clip at 0 dBFS. Every render is normalised to the podcast standard,
  -16 LUFS / -1.5 dBTP, in two passes: measure the spliced result, then apply ONE flat gain
  (linear=true). Single-pass loudnorm is a dynamic compressor and would flatten the hosts.
- Mono VBR -q:a 3: on the measured show both channels were bit-identical, so stereo would spend
  the bitrate carrying nothing.
- Each source file's media identity is verified before cutting (see media.py).
- Audio for a speech-to-text upload is 16 kHz mono mp3, never opus (see `shrink`).

Safety: ffmpeg is called with argument lists, never a shell. It reads only local files through
the `file:` protocol with a demuxer allow-list, so a hostile "mp3" that is really a playlist
cannot make it open other files or URLs. Downloads go through `net.safe_fetch`, temp files live
in a private temp dir, and the output appears only when complete.
"""
from __future__ import annotations

import array
import hashlib
import json
import math
import os
import re
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Callable, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Any, Protocol
from urllib.parse import urlsplit

from .media import AudioError, ffmpeg_input, probe_duration, verify_media
from .net import DEFAULT_MAX_BYTES, safe_fetch
from .transcript import hms

LUFS, DBTP, LRA = -16.0, -1.5, 11.0
MIN_SPAN = 0.5                     # shorter spans are rendered at this length
AUDIO_EXTENSIONS = {".mp3", ".m4a", ".mp4", ".aac", ".ogg", ".oga", ".opus", ".webm", ".wav", ".flac"}

Progress = Callable[[float], None]


class CancelEvent(Protocol):
    def is_set(self) -> bool: ...


class RenderError(AudioError):
    """ffmpeg failed. The message says what to do."""


class Cancelled(RenderError):
    """The caller's cancel event was set while rendering."""


@dataclass(frozen=True)
class CutResult:
    path: str
    duration: float                          # seconds, measured on the written file
    size_bytes: int
    index: list[dict[str, Any]]              # [{cut_start, cut_end, episode_id, source_start, source_end, title}]
    loudness: dict[str, float] | None = None  # pass-1 measurement of the splice, when levelled


def _finite(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _bounds(span: Mapping[str, Any], number: int) -> tuple[float, float]:
    start, end = _finite(span.get("start")), _finite(span.get("end"))
    if start is None or end is None or start < 0 or end <= start:
        raise ValueError(f"Span {number + 1} has invalid times.")
    if not span.get("audio"):
        raise ValueError(f"Span {number + 1} has no audio source.")
    return start, end


def span_length(span: Mapping[str, Any]) -> float:
    """Seconds a span takes in the cut."""
    return max(MIN_SPAN, float(span["end"]) - float(span["start"]))


def gap_after(span: Mapping[str, Any]) -> float:
    """Seconds of silence the cut adds after a span (`gap_after`, at most 2 s; see edges.pace)."""
    return min(2.0, max(0.0, _finite(span.get("gap_after")) or 0.0))


def is_url(src: str) -> bool:
    return urlsplit(str(src)).scheme.lower() in ("http", "https")


def cache_key(url: str) -> str:
    """Cache file name for a downloaded source: a long hash, so one URL can't be made to
    collide with another episode's cached audio."""
    ext = os.path.splitext(urlsplit(url).path)[1].lower()
    return hashlib.sha256(url.encode()).hexdigest()[:32] + (ext if ext in AUDIO_EXTENSIONS else ".audio")


def source_audio(src: str, cache_dir: str | os.PathLike[str] | None = None, *,
                 identity: Mapping[str, Any] | None = None, source_url: str | None = None,
                 max_bytes: int = DEFAULT_MAX_BYTES, allow_private: bool = False) -> str:
    """A local path for an episode's audio, verified against `identity` when one is known.

    `src` is a local file (the episode as the server downloaded it; trusted, callers resolve
    it themselves) or an http(s) URL, which is downloaded ONCE into `cache_dir` through
    `net.safe_fetch` and reused, so a re-render is free. `source_url` is the episode URL the
    identity was recorded for; it defaults to `src` for URLs."""
    if is_url(src):
        if not cache_dir:
            raise ValueError("A cache folder is needed to download the episode audio.")
        os.makedirs(cache_dir, exist_ok=True)
        dest = os.path.join(os.fspath(cache_dir), cache_key(src))
        if not (os.path.isfile(dest) and os.path.getsize(dest) > 0):
            safe_fetch(src, dest, max_bytes, allow_private=allow_private)
        verify_media(identity, source_url or src, dest)
        return dest
    path = os.path.abspath(os.fspath(src))
    if not os.path.isfile(path):
        raise RenderError(f"The episode's audio file is missing ({os.path.basename(path)}). "
                          "Download the episode again.")
    verify_media(identity, source_url or "", path)
    return path


MIN_FADE = 0.005       # afade with d=0 falls back to a whole second (its nb_samples default)


def _fade(value: Any, default: float) -> float:
    number = _finite(value)
    return min(max(MIN_FADE, default if number is None else number), 5.0)


def splice_graph(spans: Sequence[Mapping[str, Any]], fade: float = 0.35, rate: int = 44100,
                 channels: int = 1) -> tuple[list[str], str]:
    """ONE ffmpeg filter graph: fan each source out, atrim every span, splice in span order.

    Returns (sources in input order, graph). Sources are fanned with asplit because one input
    pad can only feed one filter. Only numbers and generated labels enter the graph. A span's
    own `fade_in` / `fade_out` (seconds) replace `fade` at its edges: a cut placed in a short
    pause gets a fade that ends before the first word."""
    for number, span in enumerate(spans):
        _bounds(span, number)
    fade = _fade(fade, 0.0)
    order = sorted({str(s["audio"]) for s in spans})
    layout = "mono" if channels == 1 else "stereo"
    fans, branches = [], []
    for i, src in enumerate(order):
        mine = [j for j, s in enumerate(spans) if str(s["audio"]) == src]
        fans.append(f"[{i}:a]asplit={len(mine)}" + "".join(f"[f{i}_{k}]" for k in range(len(mine))))
        for k, j in enumerate(mine):
            span = spans[j]
            length = span_length(span)
            fade_in = min(_fade(span.get("fade_in"), fade), length / 2)
            fade_out = min(_fade(span.get("fade_out"), fade), length / 2)
            gap = gap_after(span)
            branches.append((j,
                f"[f{i}_{k}]atrim=start={float(span['start']):.3f}:duration={length:.3f},"
                f"asetpts=PTS-STARTPTS,aresample={int(rate)},"
                f"aformat=sample_fmts=fltp:channel_layouts={layout},"
                f"afade=t=in:st=0:d={fade_in:.3f},"
                f"afade=t=out:st={max(0.0, length - fade_out):.3f}:d={fade_out:.3f}"
                + (f",apad=pad_dur={gap:.3f}" if gap > 0 else "") + f"[s{j}]"))
    branches.sort()                                  # play in span order, not source order
    labels = "".join(f"[s{j}]" for j, _ in branches)
    return order, ";".join(fans + [b for _, b in branches] +
                           [f"{labels}concat=n={len(branches)}:v=0:a=1[cat]"])


def _run_ffmpeg(args: Sequence[str], *, workdir: str | None = None, expected: float = 0.0,
                progress: Progress | None = None, cancel: CancelEvent | None = None,
                phase: tuple[float, float] = (0.0, 1.0), loglevel: str = "error") -> str:
    """Run ffmpeg (argument list, no shell), report progress, honour cancel. Returns stderr."""
    if cancel is not None and cancel.is_set():
        raise Cancelled("The render was cancelled.")
    cmd = ["ffmpeg", "-hide_banner", "-nostdin", "-y", "-v", loglevel,
           "-progress", "pipe:1", "-nostats", *args]
    with tempfile.TemporaryFile(dir=workdir) as err:
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=err, text=True)
        try:
            for line in proc.stdout or ():
                if cancel is not None and cancel.is_set():
                    raise Cancelled("The render was cancelled.")
                key, _, value = line.strip().partition("=")
                if progress and expected > 0 and key == "out_time_us" and value.isdigit():
                    done = min(1.0, int(value) / 1e6 / expected)
                    progress(phase[0] + (phase[1] - phase[0]) * done)
            code = proc.wait()
        finally:
            if proc.poll() is None:
                proc.kill()
                proc.wait()
            if proc.stdout:
                proc.stdout.close()
        err.seek(0)
        text = err.read().decode("utf-8", "replace")
    if code != 0:
        detail = text.strip().splitlines()[-1:] or ["no detail"]
        raise RenderError(f"ffmpeg could not render the audio ({detail[0][:300]}).")
    return text


def measure(sources: Sequence[str], graph: str, *, workdir: str | None = None,
            expected: float = 0.0, progress: Progress | None = None,
            cancel: CancelEvent | None = None) -> dict[str, float] | None:
    """Pass 1 of loudnorm: the SPLICED result's real loudness. Measuring first lets pass 2
    apply one flat gain. Returns None if ffmpeg printed no usable numbers (silence, say), and
    the caller falls back to single-pass levelling."""
    args: list[str] = []
    for src in sources:
        args += ffmpeg_input(src)
    args += ["-filter_complex",
             f"{graph};[cat]loudnorm=I={LUFS}:TP={DBTP}:LRA={LRA}:print_format=json[out]",
             "-map", "[out]", "-f", "null", "-"]
    err = _run_ffmpeg(args, workdir=workdir, expected=expected, progress=progress, cancel=cancel,
                      phase=(0.0, 0.5), loglevel="info")
    blocks = re.findall(r"\{[^{}]*input_i[^{}]*\}", err, re.S)
    try:
        found = json.loads(blocks[-1]) if blocks else None
        values = {key: float(found[key]) for key in
                  ("input_i", "input_tp", "input_lra", "input_thresh", "target_offset")} if found else None
    except (ValueError, KeyError, TypeError):
        return None
    if not values or not all(math.isfinite(v) for v in values.values()):
        return None
    return values


def _tag_args(tags: Mapping[str, Any] | None) -> list[str]:
    args = []
    for key, value in (tags or {}).items():
        key = re.sub(r"[^a-z0-9_]", "", str(key).lower())
        if key:
            args += ["-metadata", f"{key}={str(value).replace(chr(0), '')}"]
    return args


def build_index(spans: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Where every span sits in the cut and in its source episode, in seconds."""
    pos, out = 0.0, []
    for span in spans:
        start, length = float(span["start"]), span_length(span)
        out.append({"cut_start": round(pos, 3), "cut_end": round(pos + length, 3),
                    "episode_id": span.get("episode_id"), "source_start": round(start, 3),
                    "source_end": round(start + length, 3), "title": str(span.get("title") or "")})
        pos += length + gap_after(span)
    return out


def write_index(out_path: str, spans: Sequence[Mapping[str, Any]], index: Sequence[Mapping[str, Any]],
                duration: float) -> tuple[str, str]:
    """`<name>.json` and `<name>.md` beside the mp3: cut time -> source episode time, per span."""
    base = os.path.splitext(out_path)[0]
    kept = sum(entry["cut_end"] - entry["cut_start"] for entry in index)
    with open(base + ".json", "w", encoding="utf-8") as handle:
        json.dump({"version": 1, "audio": os.path.basename(out_path), "duration": round(duration, 3),
                   "kept_seconds": round(kept, 3), "lufs": LUFS, "spans": list(index)}, handle, indent=1)
    lines = [f"# {os.path.basename(base)}", "",
             f"{len(index)} spans, {kept / 60:.1f} min, levelled to {LUFS:g} LUFS. "
             "Times on the right are in the original episode.", ""]
    for span, entry in zip(spans, index, strict=True):
        note = " ".join(str(span.get("why") or span.get("text") or "").split())[:90]
        title = " ".join(entry["title"].split()) or str(entry.get("episode_id") or "episode")
        lines.append(f"- **{hms(entry['cut_start'])}** in the cut = {title} "
                     f"{hms(entry['source_start'])}-{hms(entry['source_end'])}" + (f" - {note}" if note else ""))
    with open(base + ".md", "w", encoding="utf-8") as handle:
        handle.write("\n".join(lines) + "\n")
    return base + ".json", base + ".md"


def cut(spans: Sequence[Mapping[str, Any]], out_path: str | os.PathLike[str], *, fade: float = 0.35,
        cache: str | os.PathLike[str] | None = None, quality: int = 3,
        tags: Mapping[str, Any] | None = None, level: bool = True, index: bool = True,
        allow_private: bool = False, progress: Progress | None = None,
        cancel: CancelEvent | None = None) -> CutResult:
    """spans = [{audio, start, end, episode_id?, title?, media?, source_url?}] -> ONE mp3 in
    span order, levelled, plus `<name>.json` / `<name>.md` indexes (index=False skips them).

    `audio` is a local file or an http(s) URL (downloaded into `cache`, or into the private
    temp dir that is removed afterwards). `media` is the identity the span's times belong to;
    a source whose file no longer matches it is refused (MediaIdentityError).
    `progress(fraction)` is called as ffmpeg works; setting `cancel` (e.g. a threading.Event)
    stops it (Cancelled)."""
    spans = list(spans)
    if not spans:
        raise ValueError("There is nothing to cut: no spans were kept.")
    for number, span in enumerate(spans):
        _bounds(span, number)
    out_path = os.path.abspath(os.fspath(out_path))
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    # A private (0700) temp dir beside the output: same filesystem, so the finished file is
    # renamed into place in one step and a failed render leaves nothing half-written behind.
    scratch = tempfile.mkdtemp(prefix=".podcut-", dir=os.path.dirname(out_path))
    part = os.path.join(scratch, "cut.mp3")
    try:
        order, graph = splice_graph(spans, fade=fade)
        sources = []
        for src in order:
            mine = [s for s in spans if str(s["audio"]) == src]
            known = [s["media"] for s in mine if s.get("media")]
            if known and any(identity != known[0] for identity in known[1:]):
                raise ValueError("Two spans give different media identities for the same audio.")
            source_url = next((str(s["source_url"]) for s in mine if s.get("source_url")), None)
            sources.append(source_audio(src, cache or scratch, identity=known[0] if known else None,
                                        source_url=source_url, allow_private=allow_private))
        expected = sum(span_length(span) + gap_after(span) for span in spans)
        norm = f"loudnorm=I={LUFS}:TP={DBTP}:LRA={LRA}"
        measured = None
        if level:
            measured = measure(sources, graph, workdir=scratch, expected=expected,
                               progress=progress, cancel=cancel)
            if measured:                             # one flat gain, dynamics intact
                norm += (f":measured_I={measured['input_i']:.2f}:measured_LRA={measured['input_lra']:.2f}"
                         f":measured_TP={measured['input_tp']:.2f}"
                         f":measured_thresh={measured['input_thresh']:.2f}"
                         f":offset={measured['target_offset']:.2f}:linear=true")
        args: list[str] = []
        for src in sources:
            args += ffmpeg_input(src)
        args += ["-filter_complex", f"{graph};[cat]{norm},aresample=44100[out]", "-map", "[out]",
                 *_tag_args(tags),
                 "-c:a", "libmp3lame", "-q:a", str(int(quality)), "-ar", "44100", "-ac", "1",
                 "-id3v2_version", "3", "-write_xing", "1", "-f", "mp3", part]
        _run_ffmpeg(args, workdir=scratch, expected=expected, progress=progress, cancel=cancel,
                    phase=(0.5 if level else 0.0, 1.0))
        os.replace(part, out_path)
        duration = probe_duration(out_path)
        entries = build_index(spans)
        if index:
            write_index(out_path, spans, entries, duration)
        if progress:
            progress(1.0)
        return CutResult(out_path, duration, os.path.getsize(out_path), entries, measured)
    finally:
        shutil.rmtree(scratch, ignore_errors=True)


def _safe_name(name: str) -> str:
    return re.sub(r"[/\\\x00]", "-", str(name)).strip().lstrip(".") or "cut"


def render_set(jobs: Sequence[Mapping[str, Any]], out_dir: str | os.PathLike[str], *,
               cache: str | os.PathLike[str] | None = None, quality: int = 3, fade: float = 0.35,
               workers: int = 3, allow_private: bool = False,
               on_done: Callable[[Mapping[str, Any], str, float], None] | None = None
               ) -> list[tuple[Mapping[str, Any], str, float]]:
    """Render ONE mp3 per episode into out_dir, tagged, plus an INDEX.md.

    jobs = [{name, spans, title?, tags?, source_secs?}]. One file per episode, not one file for
    the batch: a player keeps your place in a track, and a 6-hour blob loses it the moment you
    take a phone call. INDEX.md lists every skip of 45 s or more, mapped back to the episode.
    `on_done(job, path, duration)` is called as each file finishes."""
    out_dir = os.fspath(out_dir)
    os.makedirs(out_dir, exist_ok=True)

    def one(job: Mapping[str, Any]) -> tuple[Mapping[str, Any], str, float]:
        out = os.path.join(out_dir, _safe_name(job["name"]) + ".mp3")
        result = cut(job["spans"], out, fade=fade, cache=cache, quality=quality,
                     tags=job.get("tags"), allow_private=allow_private)
        return job, out, result.duration

    done = []
    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        for job, out, duration in pool.map(one, jobs):
            done.append((job, out, duration))
            if on_done:
                on_done(job, out, duration)
    done.sort(key=lambda row: str(row[0]["name"]))
    kept = sum(row[2] for row in done)
    raw = sum(float(row[0].get("source_secs") or 0) for row in done)
    with open(os.path.join(out_dir, "INDEX.md"), "w", encoding="utf-8") as fh:
        fh.write(f"# {os.path.basename(os.path.abspath(out_dir))}\n\n{len(done)} episodes, "
                 f"{kept / 3600:.1f} h of listening cut from {raw / 3600:.1f} h "
                 f"({100 * (1 - kept / max(raw, 1)):.0f}% dropped).\n"
                 f"Levelled to {LUFS:g} LUFS so no episode shouts and none disappears.\n")
        for job, out, duration in done:
            fh.write(f"\n## {job.get('title', job['name'])}\n\n"
                     f"`{os.path.basename(out)}` - {duration / 60:.1f} min "
                     f"(was {float(job.get('source_secs') or 0) / 60:.0f} min)\n\n")
            # Only the SKIPS, and only the ones worth checking. One row per span is a complete
            # map and an unreadable one - 700 lines nobody opens twice.
            pos, rows = 0.0, []
            for span, nxt in zip(job["spans"], job["spans"][1:], strict=False):
                pos += span_length(span)
                skipped = float(nxt["start"]) - float(span["end"])
                if skipped >= 45:
                    rows.append(f"- **{hms(pos)}** in the cut - skipped {skipped / 60:.1f} min "
                                f"({hms(span['end'])}-{hms(nxt['start'])} of the episode)\n")
            fh.write(f"{len(job['spans'])} spans kept. "
                     f"{len(rows) or 'No'} cut{'' if len(rows) == 1 else 's'} longer than 45 s"
                     f"{':' if rows else '.'}\n\n")
            fh.writelines(rows)
    return done


def shrink(src: str | os.PathLike[str], dest: str | os.PathLike[str], *, start: float | None = None,
           seconds: float | None = None) -> str:
    """16 kHz mono mp3 (~4 MB an hour) for a speech-to-text upload, optionally one window.

    Do NOT switch this to opus: Groq returned a 1.74x stretched timeline for opus input (965 s
    of audio came back as 1681 s), which silently puts every cut in the wrong place. Also check
    the returned duration against the audio (`align.duration_agrees`). The window is cut after
    decoding (output-side seek), so its start is exact even in a VBR mp3. -vn drops cover art."""
    args = ffmpeg_input(src)
    if start is not None:
        args += ["-ss", f"{float(start):.3f}"]
    if seconds is not None:
        args += ["-t", f"{float(seconds):.3f}"]
    args += ["-vn", "-ar", "16000", "-ac", "1", "-c:a", "libmp3lame", "-b:a", "32k", "-f", "mp3",
             os.path.abspath(os.fspath(dest))]
    _run_ffmpeg(args)
    return os.fspath(dest)


def clips(src: str | os.PathLike[str], windows: Sequence[tuple[float, float]],
          folder: str | os.PathLike[str], *, name: str = "clip") -> list[str]:
    """Several windows of one file as speech-to-text uploads (16 kHz mono mp3, like `shrink`),
    in ONE decode: `shrink` per window would decode the file from its start every time.
    Returns one path per window, `<folder>/<name>-<n>.mp3`, in window order."""
    if not windows:
        return []
    count = len(windows)
    graph = [f"[0:a]asplit={count}" + "".join(f"[w{k}]" for k in range(count))]
    for k, (start, end) in enumerate(windows):
        start = max(0.0, float(start))
        graph.append(f"[w{k}]atrim=start={start:.3f}:end={max(start + 0.05, float(end)):.3f},"
                     f"asetpts=PTS-STARTPTS[o{k}]")
    args = [*ffmpeg_input(src), "-filter_complex", ";".join(graph)]
    paths = []
    for k in range(count):
        path = os.path.join(os.path.abspath(os.fspath(folder)), f"{name}-{k}.mp3")
        args += ["-map", f"[o{k}]", "-ar", "16000", "-ac", "1", "-c:a", "libmp3lame", "-b:a", "32k",
                 "-f", "mp3", path]
        paths.append(path)
    _run_ffmpeg(args)
    return paths


@dataclass(frozen=True)
class Envelope:
    """Loudness of one window of a file: `levels[i]` is the RMS in dB of the `frame` seconds
    starting at `start + i * frame` (silence is -120)."""
    start: float
    frame: float
    levels: tuple[float, ...]

    @property
    def end(self) -> float:
        return self.start + self.frame * len(self.levels)

    def time(self, index: int) -> float:
        return self.start + index * self.frame

    def index(self, seconds: float) -> int:
        return max(0, min(len(self.levels), int(round((seconds - self.start) / self.frame))))


SILENT_DB = -120.0


def envelope(src: str | os.PathLike[str], windows: Sequence[tuple[float, float]], *, rate: int = 16000,
             frame: float = 0.01, cancel: CancelEvent | None = None) -> list[Envelope]:
    """The loudness around given moments of a local audio file, one `Envelope` per window.

    One decode, from the start of the file to the last window's end, streamed through a pipe
    and measured only inside the windows: an input seek (-ss before -i) is not exact in a VBR
    mp3, and the whole file as PCM would be ~130 MB an hour. Mono, `rate` Hz, so a 16 kHz
    measurement still sees the hiss of an "s" and never mistakes it for a pause."""
    if not windows:
        return []
    per = max(1, int(round(rate * frame)))
    spans = [(max(0, int(float(a) / frame)), max(0, int(float(b) / frame))) for a, b in windows]
    wanted = sorted({i for first, last in spans for i in range(first, max(first, last))})
    if not wanted:
        return [Envelope(first * frame, frame, ()) for first, _last in spans]
    until = (wanted[-1] + 1) * frame
    cmd = ["ffmpeg", "-hide_banner", "-nostdin", "-v", "error", *ffmpeg_input(src), "-vn", "-ac", "1",
           "-ar", str(int(rate)), "-t", f"{until + frame:.3f}", "-f", "s16le", "-"]
    levels: dict[int, float] = {}
    step = 2 * per
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    try:
        carry, first, cursor = b"", 0, 0          # frame index of carry's first byte; next wanted
        while cursor < len(wanted):
            if cancel is not None and cancel.is_set():
                raise Cancelled("The render was cancelled.")
            data = proc.stdout.read(1 << 16) if proc.stdout else b""
            if not data:
                break
            carry += data
            whole = len(carry) // step
            if not whole:
                continue
            block = array.array("h")
            block.frombytes(carry[:whole * step])
            if sys.byteorder == "big":
                block.byteswap()
            carry = carry[whole * step:]
            while cursor < len(wanted) and wanted[cursor] < first + whole:
                offset = (wanted[cursor] - first) * per
                power = sum(x * x for x in block[offset:offset + per]) / per
                levels[wanted[cursor]] = 10 * math.log10(power / 1073741824.0) if power > 0 else SILENT_DB
                cursor += 1
            first += whole
    finally:
        if proc.poll() is None:
            proc.kill()
        proc.wait()
        if proc.stdout:
            proc.stdout.close()
    # A window that runs past the end of the file stops at its last decoded frame: past the end
    # is not a pause, and must never read as one.
    return [Envelope(start * frame, frame, tuple(levels[i] for i in range(start, max(start, min(stop, first)))))
            for start, stop in spans]
