# Demo pack: source, licence and attribution

Bundled with the product so a fresh install has something to read and hear before you have your
own shows, an AI key, or a single download. No audio is committed to this
repository — only its transcript (Groq Whisper large-v3-turbo) and metadata. `scripts/load-demo.sh`
points PodFetch at a feed built from this metadata, and PodFetch downloads the audio, on your own
server, directly from Hacker Public Radio.

Show: **HPR Bash Tips** — three consecutive instalments of Dave Morriss's long-running Bash-tips
segment on [Hacker Public Radio](https://hackerpublicradio.org/), a daily, listener-produced,
technology podcast. Series page: <https://hackerpublicradio.org/series/0042.html>.

Each episode's own page states its licence individually (checked 2026-09-26; HPR's episodes are
mostly, but not all, CC BY-SA — which is why every one below was
checked rather than assumed):

| # | Title | Episode page | Licence (per the episode page) | Published |
|---|---|---|---|---|
| HPR2639 | Making decisions in Bash (part 1) | <https://hackerpublicradio.org/eps/hpr2639/index.html> | CC BY-SA 4.0 | 2018-09-13 |
| HPR2649 | Making decisions in Bash (part 2) | <https://hackerpublicradio.org/eps/hpr2649/index.html> | CC BY-SA 4.0 | 2018-09-27 |
| HPR2659 | Making decisions in Bash (part 3) | <https://hackerpublicradio.org/eps/hpr2659/index.html> | CC BY-SA 4.0 | 2018-10-11 |

Licence text: <https://creativecommons.org/licenses/by-sa/4.0/>. Each page's `<link rel="license">`
tag and its visible "released under a CC-BY-SA license" line agree, and all three are hosted by
[Dave Morriss](https://hackerpublicradio.org/correspondents/0455.html) (checked individually, not
inferred from the series). Note: the recorded audio's own closing announcement on all three
episodes instead says "Creative Commons Attribution ShareAlike **3.0**" — an old boilerplate
outro HPR appears not to have re-recorded after the site's stated licence moved to 4.0. The
episode page is HPR's current, authoritative statement, so that is what this pack relies on; both
versions are BY-SA, so the obligations (attribution, share-alike) are the same either way.

Attribution used in the product (episode credits, and this file):

> "Making decisions in Bash (part N)" by Dave Morriss, Hacker Public Radio
> (<https://hackerpublicradio.org/>), licensed under
> [CC BY-SA 4.0](https://creativecommons.org/licenses/by-sa/4.0/).

Enclosure URLs shipped in `library/hpr-bash-tips/_feed.json` (`audio`) are Hacker Public Radio's
own, unmodified: `https://hub.hackerpublicradio.org/ccdn.php?filename=/eps/hprNNNN/hprNNNN.mp3`.
The transcripts (`hprNNNN.txt` / `hprNNNN.timed.json`) are ours (Groq `whisper-large-v3-turbo`, run
once against those exact files - see each `.timed.json`'s `media` block for the
sha256/byte length/duration that ties the timing to that exact enclosure, so PodFetch's own
download of the same URL is recognised as the same audio and the timing counts as checked, not
merely "unverified"), released under the same CC BY-SA 4.0 terms as a derivative work.

The tested-models eval (`docs/models.md`) reuses this same show and these same three episodes for
its brief and cut quality measurements.
