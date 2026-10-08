#!/usr/bin/env python3
"""Tested-models eval: run the real brief and cut-plan code against 5
hand-labelled fixture episodes on a real model, and print one markdown table row per model.

The table becomes docs/models.md and, later, a README section. This script only
ever talks to the model provider (never PodFetch, never audio); companion/evals/runner.py does
the actual scoring against companion/evals/fixtures/*.json.

Examples (never paste a key on the command line: it is read from a file or LLM_API_KEY, never
printed, and this script never logs prompts):

    # what's on the free tier right now?
    scripts/eval.py --provider groq --list-models

    # score 3 models, one table
    scripts/eval.py --provider groq \\
        --model openai/gpt-oss-120b --model openai/gpt-oss-20b --model qwen/qwen3.8-27b

    # write the table straight into docs/models.md's own table file
    scripts/eval.py --provider groq --model openai/gpt-oss-120b --out /tmp/models-table.md

The key comes from $LLM_API_KEY if set, else --api-key-file (a file whose first non-comment,
non-blank line is the key) -- never printed, never written to a log.
"""
from __future__ import annotations

import argparse
import datetime
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]           # the app folder
sys.path.insert(0, str(ROOT))

from companion.evals.fixtures import load_fixtures  # noqa: E402
from companion.evals.report import failure_line, render_markdown  # noqa: E402
from companion.evals.runner import ModelReport, evaluate  # noqa: E402
from companion.llm import LLMError  # noqa: E402
from companion.providers.openai_compat import OpenAICompatLLM  # noqa: E402
from companion.providers.presets import PRESETS  # noqa: E402


def _read_key(provider: str, api_key_file: Path | None) -> str:
    env_key = os.environ.get("LLM_API_KEY")
    if env_key:
        return env_key.strip()
    if api_key_file is None:
        raise SystemExit(f"No key for {provider}: set $LLM_API_KEY, or pass --api-key-file "
                         "(a file whose first non-comment line is the key).")
    if not api_key_file.is_file():
        raise SystemExit(f"No key file at {api_key_file} and $LLM_API_KEY is not set.")
    for line in api_key_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            return line
    raise SystemExit(f"{api_key_file} has no key (every line is blank or a comment).")


def build_llm(provider: str, model: str, api_key_file: Path | None) -> OpenAICompatLLM:
    preset = PRESETS.get(provider)
    if preset is None:
        raise SystemExit(f"Unknown provider {provider!r}. Known: {', '.join(sorted(PRESETS))}.")
    key = _read_key(provider, api_key_file) if preset.needs_key else None
    return OpenAICompatLLM(base_url=preset.base_url, model=model, api_key=key, provider=provider,
                           max_input_chars=preset.max_input_chars, token_param=preset.token_param,
                           speech_model=preset.speech_model)


def list_models(provider: str, api_key_file: Path | None) -> None:
    llm = build_llm(provider, "", api_key_file)
    try:
        models = llm.list_models()
    except LLMError as exc:
        raise SystemExit(f"Couldn't list models for {provider}: {exc}") from exc
    for item in models:
        context = f" (context {item['context']})" if item.get("context") else ""
        print(f"{item['id']}{context}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--provider", default="groq", help="a companion.providers.presets id (default groq)")
    parser.add_argument("--model", action="append", default=[], dest="models",
                       help="a model id to score; repeat for more than one")
    parser.add_argument("--list-models", action="store_true", help="print GET /models and exit")
    parser.add_argument("--api-key-file", type=Path, default=None,
                       help="a file whose first non-comment line is the key (never printed)")
    parser.add_argument("--fixtures", default=None,
                       help="comma-separated episode ids to run, default: all 5")
    parser.add_argument("--out", type=Path, default=None, help="also write the markdown table here")
    args = parser.parse_args(argv)

    if args.list_models:
        list_models(args.provider, args.api_key_file)
        return 0
    if not args.models:
        parser.error("give at least one --model, or --list-models to see what's available")

    fixture_ids = args.fixtures.split(",") if args.fixtures else None
    fixtures = load_fixtures(fixture_ids)
    print(f"Loaded {len(fixtures)} fixtures: {', '.join(f.episode_id for f in fixtures)}", file=sys.stderr)

    rows = []
    failures = []
    for model in args.models:
        started = time.monotonic()
        llm = build_llm(args.provider, model, args.api_key_file)
        report: ModelReport = evaluate(args.provider, model, llm, fixtures)
        row = report.row()
        rows.append(row)
        model_failures = report.failures()
        failures += model_failures
        print(f"{model}: done in {time.monotonic() - started:.1f}s wall "
             f"({row['seconds']}s in the provider)", file=sys.stderr)
        for entry in model_failures:      # one line per failure, as it's known -- diagnosable
            print(failure_line(entry), file=sys.stderr)   # without re-reading the raw log

    date = datetime.date.today().isoformat()
    table = render_markdown(rows, date, failures)
    print(table)
    if args.out:
        args.out.write_text(table, encoding="utf-8")
        print(f"Wrote {args.out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
