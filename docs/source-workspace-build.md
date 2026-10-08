# Source workspace build

Accepted continuation, 4 October 2026. The owner approved the handoff's agent-first
transcript workspace and asked to begin building with agents. This is the first
working slice of that direction, in the existing Podsift app.

## Outcome

Open an episode, navigate its subjects, inspect the exact words, select passages,
ask for an explanation, save an idea, and inspect or adjust a shared original-audio
cut plan. Find saved ideas again in Knowledge. The connected agent and browser use
the same user-scoped plans; the existing Ask panel remains episode Q&A.

Primary destinations are Today (`/home/view`), Library (`/podcasts`), Learn
(`/learn`), and Knowledge (`/knowledge`). Discovery, the listening queue, saved-note
links, and existing tools remain directly reachable. Playback stays persistent.

## Build contracts

- Preserve the existing transcript and player source clock. Use existing shadcn
  primitives and semantic theme tokens, including loading, empty, and error states.
- Subjects come from publisher chapters or an already cached brief. Opening an
  episode does not generate AI content. Search filters exact transcript passages.
- Passage actions are deliberate: explain using episode Q&A, save a source-linked
  idea through the notes API, copy a contextual instruction for the connected agent,
  or create a manual listen through the version-bound `mode: agent` plan API. Manual
  choices carry explicit user-selection reasons. Missing timing/digest/IDs disables
  precise cut actions while leaving reading and notes usable.
- `Original transcript` / `My selected listen` is an explicit switch. A `plan` query
  parameter opens a stored plan; a plan must include the current source before the
  source workspace displays it. Keep/omit reasons, duration, remove/restore, Smart
  Play, and MP3 export reuse the existing cut workspace and script.
- Add `GET /companion/plans?episode_id=<uuid>&limit=20`, returning a newest-first
  array of public plans for the signed-in user, with a bounded limit (1–50).
  Expose matching discovery through MCP so existing agent plans are easy to reopen.
- Extend `CutWorkspace` with optional `initialPlanId` and `onPlanChange` props.
  Query shared plans only for an episode target. A different source's plan must
  never be presented or exported as this episode's selection. Existing queue cuts
  and their tests retain their behavior.
- Knowledge is a searchable view of the user's existing notes and highlights,
  including saved source quotes and links back to the original moment. Saving an
  idea does not create generated topic pages or change progress scores.

## Ownership and acceptance

Parallel writers own separate files: transcript workspace and new passage-action
components; cuts UI and API; shared plan discovery backend/MCP. The owner handles
navigation and Knowledge after the integration contracts are in place.

Run scoped behavior checks for passage selection, shared-plan recovery, source
validation, saved-idea navigation, and user isolation. Then run companion/MCP/UI
regressions, TypeScript, production build, and desktop/mobile browser checks under
the repository CPU gate. Preserve pre-existing edits to BATCH.md and
docs/learning-direction.md and all unrelated shared-tree changes.

The wider proposal's mixed-media ingestion, learning ledger, goals, and topic wiki
remain subsequent slices. This build does not claim those capabilities are present.

The source workspace has automated UI coverage. Run the application checks using
[the contributor instructions](../CONTRIBUTING.md).
