# syntax=docker/dockerfile:1.7
# We serve upstream's in-tree gateway WebUI (via the websocket channel) instead
# of the retired fork nanobot-channel-webui plugin. That needs nanobot/web/dist/
# built, so we re-add upstream's node webui-builder stage and COPY the dist into
# the final image. NANOBOT_SKIP_WEBUI_BUILD=1 stays on the pip installs so the
# hatch build hook doesn't try to rebuild it (no node in the final stage); the
# already-built dist is packaged via pyproject's include/artifacts.
FROM node:24-bookworm-slim AS webui-builder
WORKDIR /app
COPY webui/package.json webui/package-lock.json ./webui/
WORKDIR /app/webui
RUN npm ci
COPY webui/ ./
RUN mkdir -p /app/nanobot/web && npm run build

FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim

RUN apt-get update && \
    apt-get install -y --no-install-recommends ca-certificates git bubblewrap openssh-client libmagic1 tmux procps iputils-ping dnsutils && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Keep the runtime environment writable by the non-root nanobot user. Enabled
# channels may install their manifest-declared dependencies at startup.
ENV VIRTUAL_ENV=/app/.venv
ENV PATH="/app/.venv/bin:$PATH"
RUN uv venv --seed "$VIRTUAL_ENV"

# Install Python dependencies first (cached layer). Hatch reads the custom build
# hook from hatch_build.py even for this metadata-only install.
#
# Channel deps come from the fork's re-added `discord`/`telegram` extras (see
# pyproject.toml). Upstream's 462a0dfb moved these into per-channel manifests
# with a runtime pip auto-installer; we install them at build via the extras
# instead. Keep this in sync with the enabled channels.
ARG NANOBOT_EXTRAS=discord,telegram
COPY pyproject.toml README.md LICENSE THIRD_PARTY_NOTICES.md hatch_build.py ./
RUN mkdir -p nanobot && touch nanobot/__init__.py && \
    if [ -n "$NANOBOT_EXTRAS" ]; then \
        NANOBOT_SKIP_WEBUI_BUILD=1 uv pip install \
            --python "$VIRTUAL_ENV/bin/python" --no-cache ".[${NANOBOT_EXTRAS}]"; \
    else \
        NANOBOT_SKIP_WEBUI_BUILD=1 uv pip install \
            --python "$VIRTUAL_ENV/bin/python" --no-cache .; \
    fi && \
    rm -rf nanobot

# Copy the full source, the built gateway WebUI dist, and install. The dist is
# packaged into the wheel via pyproject's include/artifacts, so the gateway can
# serve it from the installed nanobot.web package. Install into the writable venv
# (on PATH) alongside the discord/telegram extras and our two remaining plugin
# channel packages (webui plugin retired in favour of the gateway WebUI).
COPY nanobot/ nanobot/
COPY plugins/ plugins/
COPY --from=webui-builder /app/nanobot/web/dist/ nanobot/web/dist/
RUN NANOBOT_SKIP_WEBUI_BUILD=1 uv pip install \
        --python "$VIRTUAL_ENV/bin/python" --no-cache ".[$NANOBOT_EXTRAS]" && \
    uv pip install --python "$VIRTUAL_ENV/bin/python" --no-cache \
        /app/plugins/nanobot-channel-openaiapi \
        /app/plugins/nanobot-channel-mcpserver

# Render deploy template (see render.yaml): committed gateway config that wires
# secrets through ${ANTHROPIC_API_KEY} / ${NANOBOT_WEB_TOKEN} env vars (resolved
# at startup). Lives in the code dir (/app), not the data dir, so a mounted disk
# won't shadow it. Only used when RENDER=true; ignored by local runs.
COPY render-config.json ./

# Create non-root user and config dir; hand ownership of /app (incl. the
# writable venv) to it so a channel's runtime dep install can write.
RUN useradd -m -u 1000 -s /bin/bash nanobottie && \
    mkdir -p /home/nanobottie/.nanobot && \
    chown -R nanobottie:nanobottie /home/nanobottie /app

COPY entrypoint.sh /usr/local/bin/entrypoint.sh
RUN sed -i 's/\r$//' /usr/local/bin/entrypoint.sh && chmod +x /usr/local/bin/entrypoint.sh

# Fork: we run the local docker-compose deployment directly as the non-root
# nanobottie user (our compose mounts ~/.nanobot to /home/nanobottie/.nanobot).
# Upstream's entrypoint still handles the Render root+setpriv path when RENDER=
# true and the container starts as root; here it takes the "already non-root"
# branch. See entrypoint.sh.
USER nanobottie
ENV HOME=/home/nanobottie
# Ensure crash output reaches container logs (app output is otherwise swallowed
# on non-graceful exit).
ENV PYTHONUNBUFFERED=1 PYTHONFAULTHANDLER=1

# Gateway default port + plugin channel ports (openaiapi 18791, mcpserver 18793)
# + websocket channel port 8765 (serves upstream's gateway WebUI). 18792 dropped
# with the retired fork webui plugin.
EXPOSE 18790 18791 18793 8765

ENTRYPOINT ["entrypoint.sh"]
CMD ["status"]
