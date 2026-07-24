# syntax=docker/dockerfile:1.7
# We use nanobot-channel-webui plugin (port 18792, ships its own dist) instead
# of the in-tree gateway webui — so we skip upstream's node:24 webui-builder
# stage and the COPY of nanobot/web/dist/. NANOBOT_SKIP_WEBUI_BUILD=1 also
# prevents the hatch build hook from trying to build it during pip install.
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

# Copy the full source and install
COPY nanobot/ nanobot/
COPY plugins/ plugins/
# In-tree gateway webui is intentionally absent (no nanobot/web/dist/, no
# upstream webui-builder stage) — the nanobot-channel-webui plugin provides the
# UI on port 18792. Install into the writable venv (on PATH) alongside the
# discord/telegram extras and our three plugin channel packages.
RUN NANOBOT_SKIP_WEBUI_BUILD=1 uv pip install \
        --python "$VIRTUAL_ENV/bin/python" --no-cache ".[$NANOBOT_EXTRAS]" && \
    uv pip install --python "$VIRTUAL_ENV/bin/python" --no-cache \
        /app/plugins/nanobot-channel-webui \
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

# Gateway default port + plugin channel ports + optional WebSocket channel port
EXPOSE 18790 18791 18792 18793 8765

ENTRYPOINT ["entrypoint.sh"]
CMD ["status"]
