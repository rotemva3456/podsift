"""Wait for this app's backend and apply streaming defaults on first use only."""
import json
import os
import time
import urllib.error
import urllib.request
from pathlib import Path

root = Path(__file__).resolve().parents[1]
# The same PodFetch dev.sh uses; agents and second copies point this at their own instance.
base = os.environ.get('PODFETCH_BACKEND', 'http://127.0.0.1:18080').rstrip('/')
# /api/v1/sys/config is public, so readiness is known even when PodFetch requires a login.
for attempt in range(40):
    try:
        with urllib.request.urlopen(base + '/api/v1/sys/config', timeout=2) as response:
            response.read()
        break
    except (OSError, urllib.error.URLError):
        if attempt == 39:
            raise SystemExit(f'PodFetch at {base} did not become ready. Check docker compose -f compose.local.yaml logs.')
        time.sleep(.5)
marker = root / 'runtime/.initialized'
if not marker.exists():
    try:
        with urllib.request.urlopen(base + '/api/v1/podcasts', timeout=5) as response:
            shows = json.load(response)
    except urllib.error.HTTPError as error:
        if error.code not in (401, 403):
            raise
        # Login is on and this script has no credentials: leave PodFetch's own settings alone,
        # and leave the marker unset so a later start without a login can still decide.
        print('PodFetch requires a login; first-use defaults were not applied.')
        raise SystemExit(0)
    if not shows:
        with urllib.request.urlopen(base + '/api/v1/settings', timeout=5) as response:
            settings = json.load(response)
        settings.update(autoDownload=False, autoCleanup=False, podcastPrefill=0,
                        maxParallelDownloads=1, sponsorblockEnabled=False)
        request = urllib.request.Request(base + '/api/v1/settings', method='PUT',
            data=json.dumps(settings).encode(), headers={'Content-Type':'application/json'})
        with urllib.request.urlopen(request, timeout=5) as response:
            response.read()
    marker.touch()
