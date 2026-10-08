# Tested models

Measured 2026-09-26 with `scripts/eval.py` against the 5 fixture episodes in
`companion/evals/fixtures/` (hand-labelled: verdict, 3 chapters, key ideas, and one cut request
with segment ids that must be kept and ones that must never be kept — see `fixtures/ATTRIBUTION.md`
and `demo/LICENSE-CONTENT.md` for what the episodes are and their licence). Every row is a real
call to the model, no mocks: `companion.brief.ask` for the brief, and `companion.cuts._ai_spans` +
`companion.engine.select_ranges` for the AI-mode cut plan — the same functions the product calls.

## Free Groq (`GET /models`, checked 2026-09-26)

| model | valid JSON 1st try | repaired once | brief not answered (limits) | brief answer rejected | valid citations 1st try | chapters found | verdict agreement | forbidden segments kept | cut not answered (limits) | budget kept | seconds | input tokens |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| openai/gpt-oss-120b (groq) | 5/5 | 0/5 | 0/5 | 0/5 | 5/5 | 12/15 | 1/5 | 0 | 0/5 | 5/5 | 661.2 | 70602 |
| openai/gpt-oss-20b (groq) | 5/5 | 0/5 | 0/5 | 0/5 | 5/5 | 13/15 | 3/5 | 1 | 0/5 | 5/5 | 833.2 | 76002 |
| qwen/qwen3.8-27b (groq) | 1/3 | 2/3 | 1/5 | 1/5 | 2/3 | 3/9 | 2/3 | 0 | 2/5 | 3/3 | 596.9 | 76992 |

`openai/gpt-oss-120b` and `openai/gpt-oss-20b`'s rows above are complete 5/5 runs, every fixture
answered, measured 2026-09-26 with a Groq key that had a fresh daily quota for these two models
(the key used earlier that day had already spent its quota for them from repeated testing while
this table was being built) — a fresh quota, not a different day. Detail on how, and on what that
run found, below the qwen section.

(This is `companion.evals.report.render_markdown`'s own output, reproduced exactly —
`scripts/eval.py --provider groq --model <model>` regenerates it, and now prints one line per
failure with its exact reason as it runs, so a run stays diagnosable without re-reading a raw
log.) Every column except "not answered" is counted out of only the runs that got a real answer
to judge — a failed brief has no verdict or chapters to be right or wrong about, and "budget kept"
is out of the cuts that got an answer. "not answered (limits)" and "answer rejected" are never the
same thing: **not answered** means the provider itself never delivered a usable answer (Groq's
free tier ran out of tokens for a minute, or — the one that matters here — its *daily* cap for
that specific model); no content ever reached `companion.brief.validate()` or
`companion.cuts._ai_spans`'s own id-checking, so there is nothing to judge and nothing wrong with
the model's reasoning. **Answer rejected** means the model *did* answer, and our own validation
turned it down even after one repair — that is a real answer-quality problem. One model,
`openai/gpt-oss-20b`, kept a forbidden segment once across its 5 cuts; per this eval's own rule
that makes it **not recommended**, regardless of its other numbers (see below) — the other two rows
never keep a forbidden segment. `companion.evals.report.render_markdown` says this itself,
verbatim: "Not recommended (kept a forbidden segment at least once): openai/gpt-oss-20b (groq)."

### qwen/qwen3.8-27b — answer quality problem, confirmed with the exact message

On a full, clean run its brief for `hpr2659` failed like this (captured live, this run):

> The AI's brief didn't check out, even after one repair: key idea 1 cites segment 1 to 246, which
> is not in the transcript. Try again, or pick another model in Settings → AI.

That message comes from `companion.brief.validate()` (via `companion.brief.ask`): the model wrote
a segment *range* ("1 to 246") into a `key_ideas[].segment_ids` field that only ever holds single
segment numbers, so every id in it fails the "is this segment in the transcript" check. An earlier
single-fixture check on `hpr4731` hit the same family of bug from the other direction — it wrote
the literal placeholder `"??"` for a chapter's `start_segment_id` instead of a real number:

> chapter 1 starts at segment ??, which is not in the transcript

Two different fixtures, two different malformed values, same underlying problem: **qwen/qwen3.8-27b
does not reliably follow the "answer with a plain segment number" instruction** — matching an
issue found in earlier testing, now with the exact rejected values on record. Its cut plan mode is fine (the
one cut failure it had was a daily-cap "not answered", not a rejection) — a cut answer only has to
*copy* an id that's already printed on the line in front of it, never invent or transform one the
way a brief's `key_ideas`/`chapters` fields do, and copying is exactly what it's good at.
**qwen/qwen3.8-27b is not recommended for briefs.**

### openai/gpt-oss-120b and openai/gpt-oss-20b — complete runs on a fresh quota (2026-09-26)

The rows above for both models were measured today with a Groq key that had a fresh daily
quota for these two models (the key used earlier that day had already spent its quota for them
from repeated testing while this table was being built and reviewed — not a bug in
`cuts._ai_spans` or `brief.validate()`, and not either model answering badly). Loaded safely into
`LLM_API_KEY` (never printed, never passed as a command argument):

- `openai/gpt-oss-120b` on one key: a clean 5/5 run, 0 failures of any kind, first try.
- `openai/gpt-oss-20b` on the same key: 4/5 briefs and 3/5 cuts before *that key's own* quota
  for this model ran out mid-run — 1 brief and 2 cuts failed with the identical message below. That
  a key which had just finished a full clean run for `gpt-oss-120b` then ran out partway through
  `gpt-oss-20b` shows the free-tier daily cap is scoped to a **(key, model) pair**, not one shared
  per-model budget across every key — each key gets its own daily allowance per model. A second key
  completed a clean run: 0 not-answered or rejected failures on either call type (the
  forbidden-segment finding below is separate — that run still answered every question, just not
  always safely).

Both `gpt-oss` models produce the identical message once a given key's daily cap for that model
trips:

> You have used up this provider's daily limit. Try again tomorrow, or pick another model.

### openai/gpt-oss-20b kept a forbidden segment once — a real finding, not a rerun artifact

This only showed up once the run was complete enough to judge every cut: 1 of the 5 cut plans kept
a segment id that fixture's own `Fixture.never_keep_ids()` says must never survive. Everything else about that run was
clean (5/5 valid citations, 13/15 chapters, 0 daily-cap or validation failures), which is exactly
why this is worth calling out rather than folding into the good numbers — a model can answer
promptly and validate cleanly and still not be safe to trust with the one rule this eval exists to
check. `companion/evals/report.py`'s existing rule (already in place before this round) catches it
automatically: any model that keeps a forbidden segment even once is marked **not recommended**, no
exception for otherwise-good numbers. This eval does not yet log which fixture or which segment —
`ModelReport.failures()` only records calls that returned an error, and a kept-forbidden-segment is
a *successful* call by that definition — so the next useful step (not done here, out of this
round's scope) is a small addition to the runner to name the fixture and segment id whenever
`forbidden_segments_kept > 0`, the same way `failures()` already does for hard errors.

**How this compares with the app.** This eval hands the model your skip words and lets it judge
them; it does not apply the app's own skip guard. In AI mode the app also removes, before the model
picks anything, every passage that says a skip word and none of your wanted words
(`companion.cuts._skipped`), so a passage that names the topic you skip can't reach your cut. The
table is therefore stricter than the app: a kept forbidden segment here means the model chose a
passage about the skipped topic that never uses the skip word itself.

- Both `gpt-oss` models needed `max_tokens >= 2000` to leave room for their reasoning tokens before
  the JSON answer (already the case here: `brief.py` asks for 4000, `cuts.py` for 2000, so nothing
  to change; noted because a lower limit will silently cut the answer off).
- A full run's wall time varies a lot with Groq's live load, not just model size: on today's
  complete runs, `gpt-oss-120b` took 11.0 min and `gpt-oss-20b` took 13.9 min — both over the
  10-minute mark, and both slower than the 5.6 min / 5.0 min seen earlier the same day on the
  partial runs of the same two models. `qwen/qwen3.8-27b` remains the slowest across every run
  measured (9.9-11.7 min). Expect a full run to be slower than a single quick question either way:
  Groq's free tier limits tokens per minute, and 4 of the 5 fixtures are long enough that a brief
  needs 2 calls (a part plus a merge) rather than 1.

## One-line pick

- **Small:** No small (~20B) model is recommended yet: `openai/gpt-oss-20b`, the only one tried at
  this size, otherwise answers well (5/5 valid citations, 13/15 chapters, 0 daily-cap or validation
  failures on a fresh key) but kept a forbidden segment once in its one complete run — the one
  failure mode this eval treats as disqualifying regardless of everything else (see above). Use
  `gpt-oss-120b` meanwhile.
- **Medium:** No medium-sized (~30B) model is recommended yet: the one tried at that size,
  `qwen/qwen3.8-27b`, does not reliably write a plain segment number for a brief's chapters or key
  ideas (see above) and is also the slowest of the three. Use `gpt-oss-120b` until a working medium
  model is confirmed.
- **Large:** `openai/gpt-oss-120b` — the clear pick. A complete, clean 5/5 run measured 2026-09-26
  on a fresh daily quota: every brief and cut answered, 5/5 valid citations, 12/15 required chapters
  found, 0 forbidden segments kept.

## OpenRouter free tier

OpenRouter also lists free (`:free`-suffixed) models, and `scripts/eval.py --provider openrouter`
works the same way. On the day this was measured, the two tried (`qwen/qwen3.8-27b:free`,
`google/gemma-4-26b-a4b-it:free`) hit OpenRouter's own free-tier rate limit before finishing even
one fixture (an HTTP 429 that outlasts the provider's built-in retry budget — "not answered", the
same as Groq's daily cap above, not an answer problem), so they aren't listed here with real
numbers; the free tier's usable throughput seems to vary by time of day. Worth trying again if you
don't have a Groq key.

## Local Ollama models

Not tested yet, results welcome. `scripts/eval.py --provider ollama --model <name>` should work
unchanged once a model is pulled and `ollama serve` is reachable from wherever you run the script
(runs against your own Ollama).

## Reproduce this

```bash
export LLM_API_KEY=...   # your own Groq key; never echo this
scripts/eval.py --provider groq --model openai/gpt-oss-120b
```

Watch stderr while it runs: every failed brief or cut prints its own line immediately, in the form
`model (provider) / episode_id / brief|cut [limits|rejected|bug]: <exact message>`, so you don't
have to wait for the final table to know whether something needs a retry (`limits`), is a real
answer problem worth filing (`rejected`), or is a defect in this eval itself (`bug`, which should
never appear — if it does, it's naming a bad assumption in `companion/evals/runner.py`, not the
model). `--fixtures hpr4731` limits a run to one episode while you're checking a change quickly;
the default is all 5.
