"""The podcast engine: plain functions the companion's routes call. No web framework, no AI.

Every time is seconds from the start of the episode's DOWNLOADED audio file. A model never
supplies times: it supplies segment ids, and `select_ranges` turns them into times.
Transcripts and feeds are untrusted text; nothing here follows instructions found in them.
Only `net` touches the network, and every server-side download goes through `net.safe_fetch`.

transcript  Segment(id, start, end, text, words, speaker), Word(text, start, end)
            parse(body, fmt=None, *, duration, content_type)  - any format below, sniffed
            from_podfetch, from_timed_json, parse_vtt, parse_srt, parse_json (Podcasting 2.0)
            as_segments, clean, group_words, sentences (a cut script's lines), sentence_pieces,
            join_pieces, spread_words, plain_text, hms
select      terms(text), spans_for(query, episodes, ...) -> (terms, spans, seconds),
            budget(spans, minutes), plan(want, episodes, ...) -> Plan,
            select_ranges(segments, ranges, exclude, budget_seconds) -> (spans, omitted),
            widen(spans, seconds, duration=, exclude=), snap(start, end, lines, exclude) (onto
            whole sentences), merge_intervals, subtract
ads         ad_spans(segments) -> [(start, end)], ad_segment_ids, teaching_spans,
            domain_vocab, density
novelty     percent_new(segments, heard_segment_lists) -> (fraction | None, new_concepts),
            concepts, phrases, STOP
cards       schedule(state, grade, now=None) (SM-2), grade_value, is_meta, parse_cards,
            CARD_RE, GRADES, META_RE
render      cut(spans, out_path, ...) -> CutResult, render_set(jobs, out_dir, ...),
            build_index, write_index, source_audio, splice_graph, measure, shrink,
            clips(src, windows, folder) (speech-to-text uploads in one decode),
            envelope(src, windows) -> [Envelope] (loudness every 10 ms), LUFS, DBTP
edges       start_cut / end_cut(envelope, planned) -> Cut: where to cut in a pause, and its fade;
            place(piece, start_env, end_env, lines) moves a render piece's edges
verify      tokens, expected_words, check_words(pieces, heard, covered) -> Result,
            check_joins(pieces, envelopes, duration): a rendered cut against its script
media       identify(path, source_url), describe_media, verify_media, same_media,
            media_duration, probe_duration, sha256_file
align       check_timing(segments, origin, media, made_from=None) -> TimingCheck,
            spot_check(segments, heard) -> TimingCheck, duration_agrees
net         safe_fetch(url, dest, max_bytes, allow_private=False) -> Fetched, safe_read,
            blocked_reason, check_address

Errors: TranscriptError, SelectionError, MediaIdentityError, AudioError / RenderError /
Cancelled, FetchError / BlockedAddressError. Their messages are written for the user.
"""
from . import ads, align, cards, edges, media, net, novelty, render, select, transcript, verify
from .ads import ad_segment_ids, ad_spans, density, domain_vocab, teaching_spans
from .align import TimingCheck, check_timing, duration_agrees, spot_check
from .cards import CARD_RE, GRADES, META_RE, grade_value, is_meta, parse_cards, schedule
from .media import (
    AudioError, MediaIdentityError, describe_media, identify, media_duration, probe_duration,
    same_media, sha256_file, verify_media,
)
from .net import BlockedAddressError, Fetched, FetchError, blocked_reason, check_address, safe_fetch, safe_read
from .novelty import concepts, percent_new, phrases
from .render import (
    DBTP, LUFS, Cancelled, CutResult, Envelope, RenderError, build_index, clips, cut, envelope, measure,
    render_set, shrink, source_audio, splice_graph, write_index,
)
from .select import (
    Plan, SelectionError, budget, merge_intervals, plan, select_ranges, snap, spans_for, subtract, terms, widen,
)
from .transcript import (
    Segment, TranscriptError, Word, as_segments, clean, from_podfetch, from_timed_json, group_words, hms,
    join_pieces, parse, parse_json, parse_srt, parse_vtt, plain_text, sentence_pieces, sentences, spread_words,
)

__all__ = [
    "ads", "align", "cards", "edges", "media", "net", "novelty", "render", "select", "transcript", "verify",
    "ad_segment_ids", "ad_spans", "density", "domain_vocab", "teaching_spans",
    "TimingCheck", "check_timing", "duration_agrees", "spot_check",
    "CARD_RE", "GRADES", "META_RE", "grade_value", "is_meta", "parse_cards", "schedule",
    "AudioError", "MediaIdentityError", "describe_media", "identify", "media_duration",
    "probe_duration", "same_media", "sha256_file", "verify_media",
    "BlockedAddressError", "FetchError", "Fetched", "blocked_reason", "check_address",
    "safe_fetch", "safe_read",
    "concepts", "percent_new", "phrases",
    "DBTP", "LUFS", "Cancelled", "CutResult", "Envelope", "RenderError", "build_index", "clips", "cut",
    "envelope", "measure", "render_set", "shrink", "source_audio", "splice_graph", "write_index",
    "Plan", "SelectionError", "budget", "merge_intervals", "plan", "select_ranges", "snap", "spans_for",
    "subtract", "terms", "widen",
    "Segment", "TranscriptError", "Word", "as_segments", "clean", "from_podfetch",
    "from_timed_json", "group_words", "hms", "parse", "parse_json", "parse_srt", "parse_vtt",
    "join_pieces", "plain_text", "sentence_pieces", "sentences", "spread_words",
]
