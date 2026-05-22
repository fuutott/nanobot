# syntax=docker/dockerfile:1.7
FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim

# Install Node.js 20 for the WhatsApp bridge
RUN apt-get update && \
    apt-get install -y --no-install-recommends curl ca-certificates gnupg git bubblewrap openssh-client tmux procps iputils-ping dnsutils && \
    mkdir -p /etc/apt/keyrings && \
    curl -fsSL https://deb.nodesource.com/gpgkey/nodesource-repo.gpg.key | gpg --dearmor -o /etc/apt/keyrings/nodesource.gpg && \
    echo "deb [signed-by=/etc/apt/keyrings/nodesource.gpg] https://deb.nodesource.com/node_20.x nodistro main" > /etc/apt/sources.list.d/nodesource.list && \
    apt-get update && \
    apt-get install -y --no-install-recommends nodejs && \
    apt-get purge -y gnupg && \
    apt-get autoremove -y && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python dependencies first (cached layer). Hatch reads the custom build
# hook from hatch_build.py even for this metadata-only install.
COPY pyproject.toml README.md LICENSE THIRD_PARTY_NOTICES.md hatch_build.py ./
RUN mkdir -p nanobot bridge && touch nanobot/__init__.py && \
    NANOBOT_SKIP_WEBUI_BUILD=1 uv pip install --system --no-cache '.[discord]' && \
    rm -rf nanobot bridge

# Copy the full source and install
COPY nanobot/ nanobot/
COPY bridge/ bridge/
COPY webui/ webui/
COPY plugins/ plugins/
# Skip the upstream hatch webui build — plugin nanobot-channel-webui ships its own dist.
RUN NANOBOT_SKIP_WEBUI_BUILD=1 uv pip install --system --no-cache '.[discord]' && \
        uv pip install --system --no-cache \
            /app/plugins/nanobot-channel-webui \
            /app/plugins/nanobot-channel-openaiapi \
            /app/plugins/nanobot-channel-mcpserver

# Build the WhatsApp bridge (set BUILD_BRIDGE=0 to skip if you don't use WhatsApp)
ARG BUILD_BRIDGE=1
WORKDIR /app/bridge
RUN --mount=type=cache,target=/root/.npm \
    if [ "$BUILD_BRIDGE" = "1" ]; then \
        git config --global --add url."https://github.com/".insteadOf ssh://git@github.com/ && \
        git config --global --add url."https://github.com/".insteadOf git@github.com: && \
        npm install --prefer-offline --no-audit --no-fund --fetch-timeout=600000 && \
        npm run build; \
    else \
        echo "BUILD_BRIDGE=0: skipping WhatsApp bridge build"; \
    fi
WORKDIR /app

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
