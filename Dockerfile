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

# Install Python dependencies first (cached layer). Hatch reads the custom build
# hook from hatch_build.py even for this metadata-only install.
ARG NANOBOT_EXTRAS=discord
COPY pyproject.toml README.md LICENSE THIRD_PARTY_NOTICES.md hatch_build.py ./
RUN mkdir -p nanobot && touch nanobot/__init__.py && \
    NANOBOT_SKIP_WEBUI_BUILD=1 uv pip install --system --no-cache ".[$NANOBOT_EXTRAS]" && \
    rm -rf nanobot

# Copy the full source and install
COPY nanobot/ nanobot/
COPY plugins/ plugins/
# In-tree gateway webui is intentionally absent (no nanobot/web/dist/) —
# the nanobot-channel-webui plugin provides the UI on port 18792.
RUN NANOBOT_SKIP_WEBUI_BUILD=1 uv pip install --system --no-cache ".[$NANOBOT_EXTRAS]" && \
        uv pip install --system --no-cache \
            /app/plugins/nanobot-channel-webui \
            /app/plugins/nanobot-channel-openaiapi \
            /app/plugins/nanobot-channel-mcpserver

# Create non-root user and config directory
RUN useradd -m -u 1000 -s /bin/bash nanobottie && \
    mkdir -p /home/nanobottie/.nanobot && \
    chown -R nanobottie:nanobottie /home/nanobottie /app

COPY entrypoint.sh /usr/local/bin/entrypoint.sh
RUN sed -i 's/\r$//' /usr/local/bin/entrypoint.sh && chmod +x /usr/local/bin/entrypoint.sh

USER nanobottie
ENV HOME=/home/nanobottie

# Gateway default port + plugin channel ports + optional WebSocket channel port
EXPOSE 18790 18791 18792 18793 8765

ENTRYPOINT ["entrypoint.sh"]
CMD ["status"]
