# syntax=docker/dockerfile:1
# Audio Cue Locator -- reproducible local runtime (M7-01).
#
# Two build targets share this one file, per docs/architecture.md's
# preference for the simplest topology consistent with the project's
# boundaries:
#
#   - `api`:   the FastAPI backend (REST API v1), FFmpeg-backed Media
#               Processing, and local SQLite/filesystem persistence.
#   - `webui`: the standalone WebUI, compiled to static assets and served by
#              a minimal Nginx that also reverse-proxies `/api/v1` to the
#              `api` service. This second container is only justified
#              because `webui/src/main.tsx` calls a fixed, same-origin
#              `/api/v1` base URL (docs constraint: this issue's allowed
#              edit paths do not include webui/src/ or a Vite dev-proxy
#              config, so the WebUI's static build cannot itself resolve a
#              cross-origin backend without a proxy in front of it).
#
# compose.yaml builds both targets from this one file. See
# docs/local-operation.md for the full build/start/stop/health procedure and
# the rationale for using two containers instead of one.

# ---------------------------------------------------------------------------
# Stage: webui-build -- compile the standalone WebUI to static assets only.
# Not a final target; its output is copied into the `webui` target below.
# ---------------------------------------------------------------------------
FROM node:20-slim AS webui-build
WORKDIR /webui
COPY webui/package.json ./package.json
RUN npm install
COPY webui/ ./
RUN npm run build

# ---------------------------------------------------------------------------
# Target: api -- FastAPI backend, FFmpeg, and local persistence.
# ---------------------------------------------------------------------------
FROM python:3.11.9-slim AS api

# FFmpeg/ffprobe: the project's sole external media-processing dependency
# (src/audio_cue_locator/infrastructure/media_processing/ffmpeg_adapter.py
# resolves both via `shutil.which`). Installed once here so the runtime does
# not depend on undeclared host state.
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY pyproject.toml README.md LICENSE ./
COPY src/ ./src/
RUN pip install --no-cache-dir .

# `var/` is where the backend's default, environment-overridable storage
# paths resolve (AUDIO_CUE_LOCATOR_ASSET_STORAGE_ROOT /
# AUDIO_CUE_LOCATOR_ANALYSIS_DB_PATH), relative to this working directory.
# `sqlite3.connect` does not create missing parent directories, so this
# directory must exist before the application starts; compose.yaml mounts a
# named volume here for persistence across container restarts.
RUN mkdir -p var

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/api/v1/health', timeout=3).status == 200 else 1)"

CMD ["uvicorn", "audio_cue_locator.interfaces.rest_api.app:app", "--host", "0.0.0.0", "--port", "8000"]

# ---------------------------------------------------------------------------
# Target: webui -- static WebUI assets served by Nginx, proxying /api/v1.
# ---------------------------------------------------------------------------
FROM nginx:1.27-alpine AS webui

COPY --from=webui-build /webui/dist /usr/share/nginx/html

COPY <<'NGINX_CONF' /etc/nginx/conf.d/default.conf
server {
    listen 80;
    server_name _;

    location /api/v1/ {
        proxy_pass http://api:8000/api/v1/;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
    }

    location / {
        root /usr/share/nginx/html;
        try_files $uri $uri/ /index.html;
    }
}
NGINX_CONF

EXPOSE 80
