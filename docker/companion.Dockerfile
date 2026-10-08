# syntax=docker/dockerfile:1
# Companion image: our Python service (FastAPI), served under /companion/*.
# Build from the repository root:  docker build -f docker/companion.Dockerfile .
# The build context is limited by docker/companion.Dockerfile.dockerignore.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_ROOT_USER_ACTION=ignore

# ffmpeg and ffprobe for cutting and measuring audio: the static FFmpeg 8.1.1 build (GPLv3)
# from the same pinned PodFetch image as docker/app.Dockerfile. It has amd64 and arm64
# builds, libmp3lame and loudnorm, and needs no apt packages.
COPY --from=samuel19982/podfetch@sha256:b4f0eac9d9d93f5b850e29546277ad3093fa6398e7a112c8a8867c0c9843883d \
    /usr/local/bin/ffmpeg /usr/local/bin/ffprobe /usr/local/bin/

RUN groupadd --gid 10001 app \
    && useradd --uid 10001 --gid app --no-create-home --home-dir /nonexistent \
        --shell /usr/sbin/nologin app

WORKDIR /app
COPY companion/requirements.txt companion/requirements.txt
# Use a patched installer during the build, then leave no package installer in the
# runtime image. The service needs only the installed application dependencies.
RUN python -m pip install --upgrade pip==26.2.1 \
    && python -m pip install --requirement companion/requirements.txt \
    && python -m pip check \
    && python -m pip uninstall --yes pip
COPY companion/ companion/
# COPY keeps the checkout's file modes, which can be private (0600 files, 0700 folders). The
# companion runs as uid 10001, so everyone may read the code, and only root may change it.
RUN chmod -R a+rX,go-w /app/companion
COPY --chmod=0755 docker/companion-entrypoint.sh /usr/local/bin/companion-entrypoint

# All data lives in /data (./data/companion in compose.yaml). NOTES_DB is the older name
# of COMPANION_DB; both point at the same file.
ENV COMPANION_DB=/data/companion.db \
    NOTES_DB=/data/companion.db

EXPOSE 8000
# The entrypoint gives /data to the "app" user (uid 10001) and drops root before the command.
ENTRYPOINT ["companion-entrypoint"]
CMD ["uvicorn", "companion.server:default_app", "--factory", "--host", "0.0.0.0", "--port", "8000"]
