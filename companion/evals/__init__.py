"""Tested-models eval: does a real AI model still write a usable brief and a
usable cut plan? Structure tests (test_brief.py, test_cuts.py) use FakeLLM and can't see a model
regress; this package runs the real ``companion.brief.ask`` and ``companion.cuts._ai_spans`` /
``companion.engine.select_ranges`` code paths against five hand-labelled fixture episodes, no
PodFetch and no audio, and scores the answer against the label.

fixtures  Fixture, load_fixtures() -> the 5 episodes in companion/evals/fixtures/*.json, each with
          its transcript (loaded through engine.from_timed_json or engine.parse_srt) and a
          hand-written Label: verdict, 3 chapters that must appear, key ideas, and a cut request
          with segment ids that must be kept and ones that must never be kept.
runner    MeteredLLM (counts calls, characters and seconds around any LLM), run_brief, run_cut,
          ModelReport.row() -> the one dict `scripts/eval.py` turns into a table row.

`scripts/eval.py` is the CLI: it builds a real provider (companion.providers.openai_compat) from
a free Groq (or OpenRouter) key, runs it through this package, and prints the table that becomes
`docs/models.md`.
"""
