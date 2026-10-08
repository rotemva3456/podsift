# Install with Docker

`compose.yaml` runs three services behind one address:

- **caddy**: the only published port. It sends `/companion/*` to the companion and everything else to PodFetch.
- **podfetch**: PodFetch's official image (pinned) with this app's UI (`docker/app.Dockerfile`).
- **companion**: our Python service for transcripts, notes and AI features (`docker/companion.Dockerfile`).

An optional fourth service, **ollama**, runs AI models on your own machine (profile `local-ai`, off by default).

## Install

You need Docker Engine 25 or newer with Compose 2.24 or newer (`docker compose version`).
It runs on amd64 and arm64 computers, so a Raspberry Pi 4 or 5 or most NAS boxes work too.

The [v0.1.1 release](https://github.com/rotemva3456/podsift/releases/tag/v0.1.1)
provides a Docker bundle using digest-pinned release images, with source and MCP
included. Extract it and run the same command below; its first start downloads the
images. GitHub's source ZIP uses the source-building Compose file described here.

In the app's folder:

```sh
docker compose up -d --wait
```

The first start builds the two images, which takes a few minutes. Then open http://127.0.0.1:8080/ui/.

To change a setting, copy the settings file, edit it, and start again:

```sh
cp .env.example .env
docker compose up -d --wait
```

`.env.example` lists every setting with a one-line comment. The ones people change first:

- `APP_PORT`: the port (default 8080).
- `TRANSCRIPTION_API_BASE_URL`, `TRANSCRIPTION_API_KEY`, `TRANSCRIPTION_MODEL`: make transcripts for episodes that have none. A free Groq key works for shorter episodes.
- `LLM_BASE_URL`, `LLM_API_KEY`, `LLM_MODEL`: your own AI provider for briefs, cuts and questions.
- `BASIC_AUTH`, `USERNAME`, `PASSWORD`: a login. Only give accounts to people you trust.

### Local AI with Ollama

In `.env`, set `COMPOSE_PROFILES=local-ai` and `LLM_BASE_URL=http://ollama:11434/v1`, then:

```sh
docker compose up -d --wait
docker compose exec ollama ollama pull <model>     # then set LLM_MODEL=<model>
```

Models are large downloads and are stored in `./data/ollama`.

## Phones and other computers (HTTPS)

The app listens on `127.0.0.1` only: plain HTTP, reachable from this computer. For other devices, put a
reverse proxy with HTTPS in front of it (Caddy, Traefik, nginx, Tailscale Serve, ...). Installing the app
on a phone needs HTTPS. The proxy must pass the original `Host` (or `X-Forwarded-Host`) and
`X-Forwarded-Proto` headers, so that audio and image links use your address.

If the proxy runs on another machine, set `APP_BIND=0.0.0.0` so it can reach the port, and turn on a login first.

## Upgrade

Back up first (below). Then get the new version of the code, and rebuild and start:

```sh
docker compose up -d --build --wait
```

Your data in `./data` stays. Database changes run by themselves when the new version starts.

## Backup

Stop the app so no database is being written, copy `./data`, and start again:

```sh
docker compose stop
sudo cp -a data "data-backup-$(date +%F)"
docker compose start
```

The files in `./data` belong to the containers' users (root, and uid 10001 for the companion), so on
Linux you need `sudo`. `cp -a` keeps the owners.

## Restore

```sh
docker compose down
sudo rm -rf data
sudo cp -a data-backup-2026-09-24 data     # the backup you want
docker compose up -d --wait
```

## What is in ./data

| Folder | Contents |
|---|---|
| `data/podfetch/db` | PodFetch's database: podcasts, episodes, users, settings, play positions |
| `data/podfetch/podcasts` | Downloaded episodes, their images and transcripts |
| `data/companion` | The companion's database: notes, and later briefs and cuts |
| `data/ollama` | Local AI models (only with `local-ai`) |

## Check and logs

```sh
docker compose ps                    # every service should say (healthy)
docker compose logs -f podfetch companion
```

Each service keeps at most 3 log files of 10 MB.

## For maintainers

- `docker/app.Dockerfile` builds `ui/` and replaces PodFetch's stock UI in `/app/static`. PodFetch writes its
  own `index.html` and loads the first `assets/index*.js` and `assets/index*.css` it finds, so the build
  stops if there is not exactly one of each.
- The PodFetch image digest is pinned in three places: `compose.local.yaml`, `docker/app.Dockerfile` and
  `docker/companion.Dockerfile`. Change all three together.
- The companion image copies the static FFmpeg 8.1.1 build (GPLv3) from that PodFetch image.
- `.github/workflows/ci.yml` builds both images for linux/amd64 and linux/arm64 and pushes them to GHCR on a `v*` tag.
