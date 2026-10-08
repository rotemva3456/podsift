# Imported video learning

Open **Learn → Open or add a video**, choose a downloaded MP4, MOV, M4V, MKV or
WebM, and import it. The source stays playable and downloadable. Importing does
not start transcription, learning, a goal, practice, reminders, or vision.

Choose **Transcribe full video** to process all its audio in timed chunks. Then
choose **Summarize** or **What should I watch?**. Focus and time are optional.
Recap citations and watch intervals return to the original video's seconds.
The transcript remains readable when AI is unavailable.

The service stores source files and per-chunk speech responses beside the
companion database under `videos/`. Cancelling or retrying retains the original
file and completed speech chunks. An interrupted process becomes retryable on
the next service start; it never silently reports a completed transcript.
Repeated imports of the same bytes are deduplicated within each user's library.

## Providers and full coverage

Video STT uses `TRANSCRIPTION_API_BASE_URL`, `TRANSCRIPTION_API_KEY` and
`TRANSCRIPTION_MODEL` when configured. The historical PodFetch base address
without `/v1` is accepted. Otherwise it uses the speech-capable provider selected
in **Settings → AI**. Recaps and watch guides use the existing app chat provider.
Both actions are explicit and can incur that provider's charges.

The implementation reuses Podsift's restricted local media inputs, accurate
decoded slicing, duration checks, speech client and transcript types. It follows
Course Watcher's original-clock and resumable-chunk approach through a portable
adapter; installing Podsift does not need this monorepo or its credential pools.

Each audio slice is lossless 16 kHz mono FLAC, up to ten minutes. Responses are
cached by the media hash and speech provider/model. Provider durations and codec
lead-in never accumulate into later source offsets. Digitally silent audio
does not call STT; untimed or stretched responses cannot establish a completed
timed transcript. A final word's decoder padding of at most one second is clamped
to the measured source end and counted in the stored coverage record; a word
starting outside the source is rejected. When optional word timestamps are
unusable but the complete segment text, segment clock and measured duration are
valid, the transcript uses passage timestamps and records the loss of word
precision. It never invents replacement word times. STT can still mishear speech.

The learning pipeline reads every timed passage using bounded map/reduction
calls instead of truncating a long transcript's end. Every returned citation
and interval is validated against supplied source IDs. The server derives
seconds from those IDs and enforces the requested budget using whole intervals.
Citation validation establishes a source location; it is not a claim that model
interpretations are always correct.

Coverage is explicitly **full audio, no visual analysis**. A silent demonstration
may matter. A `check_screen` suggestion means the words nominate a moment to
inspect, not that AI has understood a slide, code, diagram or action.

## Optional Course Watcher handoff

Set both `COURSE_WATCHER_URL` (server address) and
`COURSE_WATCHER_PUBLIC_URL` (learner's browser address). These variables are
passed into the companion by Compose. Without them, the visual control is hidden.

**Check visuals with Course Watcher → Prepare visual review** transfers the
selected video to the existing watcher and creates a saved review. It prioritizes
`check_screen` moments, otherwise the guide's intervals, with their original
seconds and the learner's question. Without a guide, the watcher uses its default
plan. Repeating an unchanged handoff reopens its existing review. Course Watcher
allows one active review per source: edit or finish/cancel an earlier review
there before preparing another goal or interval selection.

Open the review, inspect or change the plan, then use Course Watcher's existing
**Ready — ask AI to watch** action. Preparing the handoff never runs vision.
Visual evidence and results stay in Course Watcher; they are not merged into the
speech recap. The current watcher review engines accept source videos up to
30 minutes. Longer videos still support full STT and speech-based learning here.

## Existing agents

The MCP server adds `list_videos`, `get_video`, `get_video_transcript`,
`transcribe_video` and `request_video_learning`. Start transcript pagination at
cursor zero and follow every `next_cursor`, checking the same digest, before
claiming full coverage. An agent can read the complete speech and answer directly
using its own model; requesting another app generation is optional.

## Boundaries

- Files are limited to 1 GB and eight hours. The original is kept, with no edited
  video export. Browser playback depends on the original codecs; H.264 MP4 is
  broadly playable, and the original download remains available.
- Records, transcripts and jobs use the existing PodFetch login and user scope.
  Native playback uses a per-video private read link because a video element
  cannot attach a stored bearer header. Treat that link as access to the file.
- The queue runs one video action at a time, with at most six active/waiting
  actions. Cancellation waits for an in-flight provider request to return.
- This slice does not add platform downloading, goal ladders, quizzes, wiki
  generation, collection-wide video questions, or automatic visual analysis.

CI verifies synthetic import, transcription, recap, cancellation and desktop/mobile
playback. Run the same browser checks using [the contributor instructions](../CONTRIBUTING.md).
