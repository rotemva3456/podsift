# Agent-first podcast learning

Podsift's primary user is someone working with an AI agent they already use. The agent
brings their goals, books, notes and learning history. Podsift gives it access to
podcast words and tools for making a focused listen. A person can complete that flow
without opening the browser.

## The outcome

Turn a show, queue, set of episodes or topic into a listening session that fits the
listener's time and teaches something useful. A good result contains a recommendation,
the passages worth hearing, a short explanation of what repeats prior learning, and
citations back to the source. Keep the speaker's original explanation and enough
context to understand it.

Success is useful learning per listening minute, followed by recall. Reducing duration
alone is insufficient: a cut that removes necessary background or a new counterexample
has failed even if it is short. These are product goals, not measured performance claims.

## Responsibility split

| Component | Responsibility |
|---|---|
| Connected agent | Understand the goal; retrieve relevant books, notes and previous learning; read transcript pages; compare meaning and depth; recommend HEAR/READ/SKIP; choose passages; ask recall questions; update its learner memory. |
| Podcast service | Discover and subscribe to public RSS shows; prepare audio/transcripts when requested; return complete, paginated source text; check transcript versions and IDs; preserve explicit skips; store plans and reasons; export original audio with a source index. |
| Optional UI | Inspect recommendations and cut scripts, adjust a plan, listen, save highlights, and use the app's existing study tools. |

The service does not need a second chat model to execute an agent's selections. Speech
recognition is separate: a podcast without usable publisher text still needs a
transcript provider, and a timed transcript is required to cut audio.

## Prior learning

The agent should distinguish **encountered**, **understood**, **recalled**, and
**needs review** in its own memory, with evidence and a source reference. It should
never infer mastery from a book being uploaded, a title match, or a played episode.
The listener decides whether a book is planned reading or material already learned.

Compare claims and examples, not just topic labels. An episode can repeat definitions
while adding an unfamiliar application, a disagreement with a book, or a more advanced
explanation. Preserve those differences and necessary prerequisites. When the user's
prior knowledge is uncertain, explain that uncertainty rather than silently deleting
the passage.

Cut plans retain the agent's keep/skip reasons, including references such as "already
recalled from book chapter 12". This is an audit of the agent's decision, not independent
proof of learning. Raw books do not need to be copied into the podcast service. Its
built-in novelty score remains a comparison of vocabulary with finished episodes of
the same show; it cannot establish semantic overlap across books or mastery.

## Available now and remaining work

The [MCP workflow](mcp.md#agent-workflow) supports public directory search, RSS follow,
library/queue selection, explicit download/transcription requests, complete transcript
reading, agent-selected cuts, keep/skip inspection, and exports. Existing browser study
features remain available.

Account connections to external listening platforms and imports of their playlists
remain unimplemented. Each adapter needs a verified access and content-use contract;
a playlist URL does not itself provide usable audio or transcripts. No platform
integration should be advertised as working until it passes an end-to-end check.

There is no automatic cross-client semantic learner store or verified recall tracking
inside Podsift. The first workflow uses the connected agent's existing memory. A shared
learning ledger and exposing the existing flashcard/recall tools through MCP are later
extensions; their acceptance check must distinguish exposure from a demonstrated answer.
