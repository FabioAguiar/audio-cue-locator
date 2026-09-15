# Local Operation

This document describes the reproducible local runtime established by
**M7-01 — Establish a reproducible local runtime with controlled
dependencies and FFmpeg availability**. It packages the existing,
completed M1-M6 functional baseline (canonical media pipeline, acoustic
matching, Analysis/Cue/Occurrence core, local SQLite/filesystem
persistence, REST API v1, and the standalone WebUI) into a
clean-environment-buildable local runtime.

This document does not redefine or extend the product itself; it only
describes how to build, start, verify, and stop it locally. See
[`docs/architecture.md`](architecture.md) for the architectural boundaries
this packaging must preserve, and [`docs/milestones.md`](milestones.md)
for the M7 Definition of Done.

## Topology and its rationale

The runtime is built from a single [`Dockerfile`](../Dockerfile) with two
build targets, orchestrated by [`compose.yaml`](../compose.yaml):

- **`api`** -- the FastAPI backend (REST API v1), the FFmpeg-backed Media
  Processing adapter, and local SQLite/filesystem persistence. This is the
  entire existing application; no Core, Application, or Infrastructure
  behavior is changed by this packaging.
- **`webui`** -- the standalone WebUI (`webui/`), compiled to static
  assets and served by a minimal Nginx that also reverse-proxies
  `/api/v1/*` requests to the `api` service. `compose.yaml` mounts
  [`webui/nginx/default.conf`](../webui/nginx/default.conf) read-only as
  this container's effective `/etc/nginx/conf.d/default.conf`; the
  Dockerfile's embedded Nginx configuration remains only as that image's
  fallback outside the supported Compose runtime. Upload-size policy stays
  entirely API-owned: the `/api/v1/` proxy location imposes no product-level
  request-body ceiling of its own and does not buffer a proxied upload
  before forwarding it, so `api`'s own bounded upload handling (see "Safe
  example configuration" below) is the first product-policy boundary an
  upload meets.

This is a **two-container** topology, not the single container that
`docs/architecture.md`'s "Containerização" guidance prefers by default.
The second container is specifically justified: `webui/src/main.tsx`
declares a fixed, same-origin `API_BASE_URL = "/api/v1"`, and both that
file and any Vite dev-proxy configuration under `webui/` are outside this
issue's authorized edit paths. Without a same-origin reverse proxy in
front of the compiled static assets, the WebUI's relative `/api/v1`
requests would not reach the `api` container from a browser. Serving
static assets and reverse-proxying is a distinct operational
responsibility from running the Python API process, so a second container
is used instead of attempting to merge the two into the `api` image
(which would require modifying `src/audio_cue_locator/interfaces/rest_api/
app.py` to mount static files -- also outside this issue's authorized
edit paths). No broker, remote store, or additional service beyond these
two is introduced.

## Prerequisites

- Docker Engine with BuildKit enabled (the default on modern Docker
  Engine/Desktop releases). The `Dockerfile`'s Nginx configuration is
  written using a Dockerfile heredoc (`COPY <<'NGINX_CONF' ...`), which
  requires BuildKit.
- Docker Compose (the `docker compose` CLI plugin).

No other host-installed dependency is required: Python, FFmpeg, Node, and
all backend/WebUI packages are resolved inside the images below, not from
host state.

## Clean build

From the repository root:

```bash
docker compose build
```

This builds both the `api` and `webui` images from the same
[`Dockerfile`](../Dockerfile):

- `api` installs Python 3.11.9 (matching the repository's own
  [`.python-version`](../.python-version) and `pyproject.toml`'s
  `requires-python`), installs `ffmpeg`/`ffprobe` via `apt-get`, and
  installs this project (`pip install .`) using the dependencies declared
  in [`pyproject.toml`](../pyproject.toml).
- `webui` installs the WebUI's declared dependencies
  ([`webui/package.json`](../webui/package.json)) with `npm install`,
  runs `npm run build`, and serves the resulting `webui/dist/` output
  through Nginx.

A failed build (for example, an unreachable package registry, or a
declared dependency that cannot be resolved) fails explicitly at this
step; it does not silently fall back to host state, since nothing from
the host Python/Node environment is used inside the build.

## Start

```bash
docker compose up
```

(add `-d` to run in the background.)

This starts both containers on one Docker Compose network. `webui` waits
for `api`'s own container `HEALTHCHECK` (defined in the `Dockerfile`) to
report healthy before starting, since the WebUI's static build depends on
`api` being reachable through its own reverse proxy.

Once started:

- REST API v1 is reachable directly at `http://localhost:8000/api/v1`.
- The WebUI is reachable at `http://localhost:8080`, and its own
  `/api/v1/*` calls are transparently proxied to the `api` container.

## Health verification

The backend already exposes a versioned health route
(`GET /api/v1/health`, `docs/rest-api-v1-contract.md`):

```bash
curl http://localhost:8000/api/v1/health
# {"status": "ok", "api_version": "v1"}
```

The same check is used as the `api` image's Docker `HEALTHCHECK`
(`docker compose ps` reports the container's health status), and through
the reverse proxy at `http://localhost:8080/api/v1/health`.

To confirm FFmpeg availability inside the running `api` container
explicitly, rather than only inferring it from a successful build:

```bash
docker compose exec api ffmpeg -version
docker compose exec api ffprobe -version
```

## Local storage

The backend's local SQLite database and filesystem asset storage resolve
under `var/` relative to its own working directory, using the two
environment variables the application already supports
(`AUDIO_CUE_LOCATOR_ASSET_STORAGE_ROOT`,
`AUDIO_CUE_LOCATOR_ANALYSIS_DB_PATH` --
`src/audio_cue_locator/interfaces/rest_api/app.py`). `compose.yaml` sets
both explicitly (to their existing default relative paths) and mounts a
named Docker volume, `api-data`, at `/app/var` inside the `api` container,
so this data persists across `docker compose restart`/`down` (without
`-v`) and is not written into the repository working tree or the build
context (excluded via [`.dockerignore`](../.dockerignore)).

`/app/var/asset_storage` holds every uploaded and derived physical Asset as
an extensionless, canonical-UUID-named file. Two independent cleanup rules
bound how long those bytes persist
(`docs/artifact-retention-and-cleanup-policy.md`,
`docs/retention-and-cleanup.md`):

- **Analysis-owned bytes** -- once uniquely owned by a terminal (`SUCCEEDED`
  or `FAILED`) Analysis, they are eligible for deletion **7 days** after
  that Analysis's terminal timestamp.
- **Unreferenced uploads** (S0012) -- a physical Asset that no persisted
  Analysis has ever referenced (for example, an upload whose `POST
  /analyses` call never happened) becomes eligible for deletion **24 hours**
  after it was written to local storage, using the file's own storage
  timestamp, never an Analysis timestamp.

Both rules run once at process startup, immediately before the API can
claim a queued Analysis, and -- as of S0011 -- also run periodically
thereafter, for as long as the `api` process keeps running: the API process
itself performs its own local periodic housekeeping. No host cron job and
no additional container are required or introduced. The periodic interval
is:

- **`AUDIO_CUE_LOCATOR_ASSET_CLEANUP_INTERVAL_SECONDS`** -- default `86400`
  (24 hours), minimum `3600` (one hour). A non-numeric, non-finite, or
  sub-one-hour value fails the `api` container's startup explicitly rather
  than silently falling back.

An in-flight Analysis-creation request that is already using an Asset id is
protected from periodic deletion by a narrow, process-local, in-memory
reservation (`docs/retention-and-cleanup.md`, "Asset reservation /
maintenance coordination boundary (S0011)"); this is not durable and does
not change either rule's eligibility window above. A consequence operators
should still expect: an upload left unreferenced for 24 hours or more is no
longer guaranteed to still be available for a later Analysis-creation
attempt that has not yet started using it -- this is accepted staging
behavior for the normal upload-then-create flow, not a bug.

To observe current local Asset storage usage from the logs (no filesystem
access required), look for the `asset_storage_usage_observed` structured
event -- emitted once after startup maintenance and once after every
periodic maintenance cycle (`docs/observability.md`) -- and read its
`count` (managed Asset file count) and `size_bytes` (their total size)
fields:

```bash
docker compose logs api | grep asset_storage_usage_observed
```

To inspect current local Asset storage usage directly and safely
(read-only, no state change):

```bash
du -sh /app/var
du -sh /app/var/asset_storage
find /app/var/asset_storage -maxdepth 1 -type f | wc -l
```

**Do not manually delete UUID-named files under `/app/var/asset_storage`.**
A file may still be referenced by a persisted Analysis in any lifecycle
state; only the two cleanup rules above -- which check every persisted
reference before deleting anything -- are safe to rely on for reclaiming
space.

To reset all local state (Analysis history and stored media):

```bash
docker compose down -v
```

This is a destructive, whole-volume reset, not routine cleanup: it also
removes Analysis-owned and still-within-grace-window unreferenced Assets
that the two cleanup rules above would otherwise have preserved.

### Audio audition transient disk use (S0013)

`GET /api/v1/analyses/{analysis_id}/cues/{cue_id}/occurrences/{occurrence_index}/audio`
(`docs/rest-api-v1-contract.md`, "Analysis Cue/occurrence audio audition")
renders its bounded WAV segment through the existing FFmpeg boundary. While
handling one such request, the `api` process:

- may temporarily use disk space under the process's own operating-system
  temporary directory (a private `tempfile.TemporaryDirectory()` per
  request) to hold the source-media input and the rendered WAV output
  FFmpeg needs on a real filesystem path;
- does **not** add any file to `/app/var/asset_storage`;
- does **not** add a row, an owned-Asset reference, or any other
  persisted state to the Analysis Repository (`owned_asset_ids` is never
  extended for an audition);
- deletes that temporary input/output automatically when the request's
  renderer call returns, whether it succeeds or fails -- nothing here
  survives past the single HTTP request that created it.

This temporary location is an internal renderer implementation detail, not
part of the public API surface: no route, response header, or documented
contract ever exposes its path, and an operator should not rely on it for
anything beyond the duration of one in-flight request.

Audition (both the Cue and the occurrence route) becomes unavailable --
returning the existing 404 `resource_not_found` response -- once the
source/Cue retention cleanup rules above ("Local storage") have already
deleted the required bytes, even though the persisted Analysis and its
Result remain queryable exactly as before. S0013 does not extend either
cleanup rule's eligibility window to keep audition available longer.

## Shutdown

```bash
docker compose down
```

This stops and removes both containers. The `api-data` named volume is
preserved unless `-v` is also passed (see above).

## Safe example configuration

`compose.yaml` sets only two non-secret, path-only environment variables
(both restating the application's own existing defaults). No `.env` file,
credential, token, or secret is introduced or required by this runtime.
[`.dockerignore`](../.dockerignore) excludes `.env*`, `secrets.*`,
`credentials.*`, `*.pem`, `*.key`, local databases (`*.sqlite*`, `*.db*`),
`var/`, and other local runtime/derived-artifact directories from the
Docker build context.

The backend also supports further non-secret, environment-overridable
limits that this runtime does not need to change from their built-in
defaults, but that a future operator may set explicitly on the `api`
service if needed:

- `AUDIO_CUE_LOCATOR_MAX_SOURCE_MEDIA_UPLOAD_BYTES`
- `AUDIO_CUE_LOCATOR_MAX_CUE_UPLOAD_BYTES`
- `AUDIO_CUE_LOCATOR_MAX_CUE_COUNT`
- `AUDIO_CUE_LOCATOR_ASSET_CLEANUP_INTERVAL_SECONDS` (S0011; default
  `86400`, minimum `3600` -- see "Local storage" above)

## Known limitations and open items

These are carried forward from the M7-01 implementation handoff (an
ASF support-root artifact, not part of this repository) and are **not**
resolved by this packaging:

- No dependency lockfile exists for either the backend (`pyproject.toml`)
  or the WebUI (`webui/package.json`); both currently rely on declared
  version ranges only. Generating one requires running a package manager,
  which is outside this issue's authorized scope (no command execution).
- FFmpeg's exact resolved version is whatever Debian's `bookworm-slim`
  package repository provides for the `python:3.11.9-slim` base image at
  build time; no specific FFmpeg version or compatibility range is pinned
  or verified beyond "the `ffmpeg`/`ffprobe` binaries are present and
  executable" (see health verification above).
- This runtime is local-development-grade: it does not configure TLS, an
  externally reachable hostname, authentication, or multi-user isolation.
  Public or multi-user deployment requires a separate, explicitly
  authorized security review, per `docs/architecture.md`.
- A clean-environment build/start/health run of this exact packaging has
  not been executed as part of this implementation; command execution is
  a separate, later-authorized phase. This document describes the
  intended, reproducible procedure for that future validation to follow.
