"""Portable learning-mode fixture built on the isolated skipping fixture."""
import skipping_fixture as base

from companion.test_cuts import native


PASSAGES = [
    "A network forwards packets by matching destination addresses against its routing table.",
    "BGP exchanges reachability between autonomous systems and carries path attributes with each route.",
    "The best path process considers local preference before comparing the length of the AS path.",
    "An operator can use communities to label routes and apply a consistent routing policy downstream.",
    "Route reflectors reduce the number of internal BGP sessions required in a large autonomous system.",
    "A prefix withdrawal tells neighboring routers that a previously advertised network is no longer reachable.",
    "MED can suggest which entry point a neighboring network should prefer when several links are available.",
    "Convergence ends when the routers agree on current reachability after a topology or policy change.",
    "Filtering routes at network boundaries helps prevent accidental leaks from spreading between providers.",
    "Careful monitoring connects BGP updates, routing policy changes, and packet loss during an incident.",
]
TIMES = [(5.0 + index * 11.0, 14.0 + index * 11.0) for index in range(len(PASSAGES))]
assert len(PASSAGES) > 6 and max(end for _start, end in TIMES) <= 120.0

# Keep the real companion application, temporary database, production UI routes, and
# silent Range-capable WAV from the skipping fixture. Only its generated transcript
# changes for this acceptance driver; every passage remains inside the 120-second WAV.
base.fake.native[base.PID] = native(PASSAGES, TIMES, source="generated")
base.set_source("generated")
base.fake.episodes[base.EP]["name"] = "Synthetic networking lesson"

app = base.app
