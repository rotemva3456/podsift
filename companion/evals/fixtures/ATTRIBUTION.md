# Eval fixtures: source, licence and attribution

Five episodes used only to measure brief and cut quality (`scripts/eval.py`).
No audio is committed here, and nothing here is served to a user; it exists so a model's answers
can be checked against a fixed, hand-written answer key.

## Reused from the demo pack

`hpr2639`, `hpr2649`, `hpr2659` — the same three "HPR Bash Tips" episodes as
`demo/library/hpr-bash-tips/` (transcript, licence and attribution already recorded in
`demo/LICENSE-CONTENT.md`, which says explicitly that this eval reuses them). This eval reads that
same `.timed.json` file; it is not copied here.

## Added for this eval

Two more episodes, a different HPR correspondent and topic each, licence checked individually on
each episode page (2026-09-26), the same way the demo pack did — the page's `<link rel="license">` tag and
its visible "released under a … license" line were both read and agree:

| # | Title | Episode page | Licence (per the episode page) | Published | Host |
|---|---|---|---|---|---|
| HPR4731 | A Website Hacked | <https://hackerpublicradio.org/eps/hpr4731/index.html> | CC BY-SA 4.0 | 2026-09-21 | [Lee](https://hackerpublicradio.org/correspondents/0403.html) |
| HPR4726 | Trurl's intro show | <https://hackerpublicradio.org/eps/hpr4726/index.html> | CC BY-SA 4.0 | 2026-09-14 | [Trurl](https://hackerpublicradio.org/correspondents/0464.html) |

Licence text: <https://creativecommons.org/licenses/by-sa/4.0/>.

Transcript: unlike the demo pack, these two were **not** re-transcribed with Groq Whisper. HPR
already publishes a timed `.srt` for every episode (`hub.hackerpublicradio.org/.../hprNNNN.srt`);
this eval downloads that file as is (`hpr4731.srt`, `hpr4726.srt`, committed here verbatim) and
parses it with the same `engine.transcript.parse_srt` the product uses for any SRT transcript.
That is a deliberate difference from the demo pack, not an oversight: this eval never plays or cuts real audio
against these fixtures (`companion/evals/runner.py` calls `companion.brief.ask` and
`companion.cuts._ai_spans` directly, in memory, with no downloaded file and no
`engine.align` media-identity check), so the "verified against this exact file" property the demo pack
needed for a real listening experience doesn't apply here — only correct segment text and timing
does, which HPR's own SRT already provides. No audio file was downloaded for either episode.

Attribution used for these two (episode credits, and this file):

> "A Website Hacked" by Lee, Hacker Public Radio (<https://hackerpublicradio.org/>), licensed
> under [CC BY-SA 4.0](https://creativecommons.org/licenses/by-sa/4.0/).

> "Trurl's intro show" by Trurl, Hacker Public Radio (<https://hackerpublicradio.org/>), licensed
> under [CC BY-SA 4.0](https://creativecommons.org/licenses/by-sa/4.0/).

Why these two: the demo pack's three episodes are all the same "Bash Tips" reference-syntax
format, which this eval's own hand-written labels judge as READ every time (the verdict rule:
syntax you look up, not reasoning or a story). A 5-fixture answer key that was 100% READ would
not exercise the HEAR/SKIP branches of the verdict prompt at all, so this eval picked one episode
of each of the other two kinds, checked independently, without cherry-picking for a model's
convenience:

- **HPR4731 "A Website Hacked" -> HEAR.** A first-person incident account (two chained 2026 CVEs,
  "WP2Shell") with real investigation reasoning and a decision not to blindly roll back a charity's
  site — exactly the kind of episode a HEAR verdict is for: "why things work... and what goes wrong in practice."
- **HPR4726 "Trurl's intro show" -> SKIP.** A 38-minute personal-interests conversation (maths,
  chess ranking systems, literature, politics) between two hosts. No operational or reference
  content a listener would look something up for; the closest fit is a SKIP verdict's
  personal-chat spirit ("career chat... or you already know it already").

Every label (verdict, chapters, key ideas, cut request) in `hpr4731.json` and `hpr4726.json` was
written by reading the full transcript, independently of the reused demo pack's own
`briefs.json` (which is itself a stored AI answer from the demo pack, not a ground truth).
