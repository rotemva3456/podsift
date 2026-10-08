#!/bin/sh
# Run the companion as the unprivileged "app" user (uid 10001).
# Docker creates a missing bind-mounted ./data/companion as root. So when the container starts
# as root, give /data to "app", then drop root for good. When it already starts as another
# user (user: in compose.yaml, or rootless setups), run the command as that user.
set -eu

if [ "$(id -u)" = 0 ]; then
    mkdir -p /data
    # -h changes a symlink itself, never the file it points to.
    if ! find /data ! -user app -exec chown -h app:app {} +; then
        echo "companion: cannot give /data to the app user (uid 10001). Fix the owner of ./data/companion on the host, for example: sudo chown -R 10001:10001 data/companion" >&2
        exit 1
    fi
    exec setpriv --reuid=app --regid=app --init-groups --no-new-privs -- "$@"
fi
exec "$@"
