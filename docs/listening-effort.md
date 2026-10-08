# Focus and Chill listening

Choose **Listening effort** in an episode's **Learn → Cut** controls or when cutting
your **Listen next** queue. The choice is optional; **Any effort** retains the existing
planning behavior.

| Mode | Useful material | When to use it |
|---|---|---|
| Focus | Difficult ideas, confusing parts you mention, deeper reasoning and worked problems, with their prerequisites. | When you are awake, sharp and ready to think. |
| Chill | Recap, familiar ideas, clear explanations and concrete examples that are easy to follow. | When you want a relaxed listen with less active thought. |

Importance and effort are separate. An important subject can belong in either mode,
depending on the explanation and your learning context. In your listening goal, say
which parts confuse you or what you already understand. Titles and completed playback
do not establish understanding.

With the app's AI connected, selecting Focus or Chill uses AI passage selection
automatically. Keyword matching cannot judge mental effort. Without app AI, **Copy agent
instructions** carries your mode, goal, skips and time budget to your connected agent.
Reading the transcript and selecting passages yourself remains available.

For manual selections, choose the mode in the selected-passage actions, then **Keep
selected**. The mode labels your choices; the service does not reassess or replace them.
It also works when making a listen without selected passages. Saved listens retain
their mode and show it beside the passage-selection method. Smart Play and MP3 export
use that saved selection. Changing the mode for a new plan does not change the current
audio or relabel an existing plan.

Make separate Focus and Chill listens from the same source or queue when useful. Each
gets its own saved plan and reasons. If no passages fit the chosen effort, the planner
can return an empty result; it does not fill the time with unrelated material. Mode
selection alone does not generate a quiz, notes or learning-progress records.

## Connected-agent tools

- `plan_from_segments(..., learning_mode="focus" | "chill" | "balanced")` saves the
  agent's version-bound choices without another app model. The agent should use your
  learner context, preserve prerequisites and explain the effort fit in its reasons.
- `plan_cut(..., mode="ai", learning_mode="focus" | "chill")` uses the app's configured
  provider. Specific effort modes with `mode="keyword"` are rejected.
- `list_plans(learning_mode="focus" | "chill" | "balanced")` retrieves saved listens
  for that effort. Filtering occurs before the result limit and remains user scoped.

Plans created before this feature are treated as **Any effort**. The mode is stored
on plans and their source scripts; no database migration or mastery inference is needed.

Automated checks use fixture selections to verify mode propagation, source ranges,
budgets and recovery. They do not measure how well a particular model judges difficulty.
