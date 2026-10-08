# syntax=docker/dockerfile:1
# App image: the official PodFetch server, serving our UI build instead of the stock UI.
# Build from the repository root:  docker build -f docker/app.Dockerfile .
# The build context is limited by docker/app.Dockerfile.dockerignore.

# Stage 1: build ui/. The result is plain JS and CSS, so this stage always runs on the build
# machine's own platform, even when the image is for another one (no slow arm64 emulation).
FROM --platform=$BUILDPLATFORM node:22-slim AS ui
# The pnpm version that wrote ui/pnpm-lock.yaml.
RUN npm install --global --no-fund --no-audit pnpm@11.15.1
WORKDIR /ui
COPY ui/package.json ui/pnpm-lock.yaml ui/pnpm-workspace.yaml ./
RUN pnpm install --frozen-lockfile
COPY ui/ ./
RUN pnpm run build
# The build copies ui/public/* with the checkout's file modes, which can be private (0600).
# Everyone may read the result, so PodFetch can serve it even when it runs as a non-root user.
RUN chmod -R a+rX,go-w dist
# PodFetch ignores this index.html: it writes its own on every request and loads only the first
# assets/index*.js and assets/index*.css it finds (crates/podfetch-web/src/startup.rs,
# resolve_index_assets). So stop unless this index.html loads exactly one script and links
# exactly one stylesheet, and they are the only assets/index*.js and assets/index*.css. Anything
# else it loads would never reach the browser; the error names it.
RUN set -eu; cd dist; \
    fail() { printf 'UI build check: %s\n' "$*" >&2; exit 1; }; \
    words() { printf '%s' "$1" | tr '\n' ' '; }; \
    js=$(ls assets | grep '^index.*\.js$' || true); \
    css=$(ls assets | grep '^index.*\.css$' || true); \
    scripts=$(grep -o '<script[^>]*type="module"[^>]*>' index.html | grep -o 'src="[^"]*"' | cut -d'"' -f2); \
    styles=$(grep -o '<link[^>]*rel="stylesheet"[^>]*>' index.html | grep -o 'href="[^"]*"' | cut -d'"' -f2); \
    [ "$(printf '%s\n' "$js" | grep -c .)" = 1 ] \
        || fail "PodFetch needs exactly one assets/index*.js. Found: $(words "${js:-none}")"; \
    [ "$(printf '%s\n' "$css" | grep -c .)" = 1 ] \
        || fail "PodFetch serves only assets/index*.css and needs exactly one. Found: $(words "${css:-none}"). index.html links: $(words "${styles:-none}")"; \
    [ "$scripts" = "/ui/assets/$js" ] \
        || fail "index.html must load one script, /ui/assets/$js, the only one PodFetch loads. It loads: $(words "${scripts:-none}")"; \
    stray=$(printf '%s\n' "$styles" | grep . | grep -vxF "/ui/assets/$css" || true); \
    [ -z "$stray" ] && [ "$styles" = "/ui/assets/$css" ] \
        || fail "index.html must link one stylesheet, /ui/assets/$css, the only one PodFetch serves. PodFetch never serves: $(words "${stray:-none}"). index.html links: $(words "${styles:-none}")"

# Stage 2: the official PodFetch image, pinned by digest. Keep this digest the same as in
# compose.local.yaml and docker/companion.Dockerfile. It has linux/amd64 and linux/arm64.
FROM samuel19982/podfetch@sha256:b4f0eac9d9d93f5b850e29546277ad3093fa6398e7a112c8a8867c0c9843883d
# Podsift detects skips from transcripts; the inherited optional API stays unused.
ENV SPONSORBLOCK_API_URL=http://127.0.0.1:9
# PodFetch serves ./static from its working folder /app. The image has no shell, so a static
# busybox, mounted for this step only, deletes the stock UI first: copying over it would leave
# the stock index*.js next to ours, and PodFetch could pick either one.
RUN --mount=type=bind,from=busybox:1.37-musl,source=/bin/busybox,target=/busybox \
    ["/busybox", "rm", "-rf", "/app/static"]
COPY --from=ui /ui/dist/ /app/static/
