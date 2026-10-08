# Content skipping

Podsift finds explicit English sponsor and promotional cues in a timed transcript.
It does not read SponsorBlock's database or copy community submissions. These are
suggested passages, not a guarantee that every ad has been found.

In **Settings → Sponsors**, enable the categories you want. Each enabled category
can **Skip automatically** or **Ask before skipping**. Set a minimum length to
leave short passages alone. These choices are saved on the server for your account
and apply to the browser player; they do not rewrite a saved cut plan or export.

Automatic skipping needs a downloaded episode and a transcript made from that
audio file. Unchecked publisher or library timing always offers a manual choice,
even when the category is set to automatic. A transcript or loaded audio with a
different duration stops content skipping and offers a link to the transcript
workspace. Matching duration alone does not prove that two audio copies are the
same: dynamically inserted ads can move the words without changing the total
length appreciably.

During a manually marked passage, **Skip this part** jumps to its end. After a
skip, **Undo skip** returns to the position that was skipped and allows that
passage to play once. Skipping resumes when you leave it. Undo also permits replay
when the passage overlaps a Smart Play omission; it keeps the saved plan and your
category preferences. The Undo notice lasts five seconds, and applies only to the
same episode and media source. Playback stays paused if it was paused.

Missing timestamps and scan errors are shown separately from a successful scan
with no matches. **Try again** retries a failed scan or preference lookup.

## Scope and limits

- Sponsor, self-promotion, interaction reminder, intro, outro, preview/recap,
  filler and non-music are the eight inherited app choices. They are not a claim
  of matching every SponsorBlock category or category definition.
- Filler and music-related detection require explicit transcript annotations.
  This is not acoustic music, silence or tangent classification.
- Unmarked ads, long uninterrupted reads and ads split from their offer can be
  missed. A source digest tracks transcript changes; it is not an audio fingerprint
  or an accuracy score.
- The current skip response does not fingerprint a library transcript against
  the current download, so library timestamps remain manual. Generated transcripts
  receive the file-derived timing status and an additional duration check in the
  media watcher.
- User corrections are temporary replay choices. There is no public submission,
  voting, moderation or shared segment database.
- These controls cover podcast episodes in the browser player. Export exclusions
  follow the saved cut's script; standalone imported-video playback has its own
  learning workflow.

## Comparison used for the upgrade

SponsorBlock documents community submissions and voting as its source of skip
boundaries, including feedback when a passage has the wrong timing or category.
It also supports local personal segments. [SponsorBlock FAQ](https://wiki.sponsor.ajay.app/w/FAQ).

Its configurable actions include disabled, overlay, manual and automatic skipping;
duration conditions can preserve short passages. Podsift adds automatic/manual
choices and a minimum length within its existing settings, without implementing a
general rule language. [Advanced skip options](https://wiki.sponsor.ajay.app/w/Advanced_skip_options).

SponsorBlock also has highlights, separate hook/greeting segments, chapters,
full-video labels and mute actions. Those are useful differences to evaluate for
YouTube/video parity, beyond this podcast-player upgrade.
[Category and action types](https://wiki.sponsor.ajay.app/w/Types),
[category guidelines](https://wiki.sponsor.ajay.app/w/Guidelines).

## Release evidence

Regression tests are provided for source timing, invalid boundaries,
account-separated preferences, manual choices, minimum length, Undo and Smart
Play overlap. A synthetic browser fixture checks real media seeks and saved
settings on desktop and mobile against the production build. The upgrade's
runtime checks are pending CPU admission; previous verification covers the
earlier implementation only.

Before advertising general automatic-ad accuracy, measure a held-out, manually
labelled set of real episodes. Include unknown sponsors, pre/mid/post-rolls,
dynamic insertion, long reads and genuine lessons mentioning sponsors. Report
missed sponsor seconds, useful-content seconds removed and boundary errors;
the fixture checks alone cannot establish those figures. The intended launch
scope is explicit English cues with manual fallback for unchecked timing.
