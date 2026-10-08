#!/usr/bin/env python3
"""Import RSS subscriptions through PodFetch, without downloading episode audio."""
import argparse
import json
import urllib.request
from pathlib import Path


def request(base, path, data=None, method=None):
    req = urllib.request.Request(base + path,
        data=json.dumps(data).encode() if data is not None else None,
        headers={"Content-Type": "application/json"}, method=method)
    with urllib.request.urlopen(req, timeout=120) as response:
        return json.load(response)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("library", type=Path)
    parser.add_argument("--backend", default="http://127.0.0.1:18080")
    args = parser.parse_args()
    manifests = sorted(args.library.glob("*/_feed.json"))
    if not manifests:
        parser.error("No podcast manifests found in that library.")
    settings = request(args.backend, "/api/v1/settings")
    settings.update(autoDownload=False, autoCleanup=False, podcastPrefill=0,
                    maxParallelDownloads=1, sponsorblockEnabled=False)
    request(args.backend, "/api/v1/settings", settings, "PUT")
    existing = {show["rssfeed"] for show in request(args.backend, "/api/v1/podcasts")}
    for manifest in manifests:
        feed = json.loads(manifest.read_text()).get("feed")
        if not feed or feed in existing:
            continue
        show = request(args.backend, "/api/v1/podcasts/feed", {"rssFeedUrl": feed})
        existing.add(feed)
        print("Imported:", show["name"], flush=True)


if __name__ == "__main__":
    main()
