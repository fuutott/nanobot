# Plugin Install How-To

Base repository:

```bash
https://github.com/fuutott/nanobot.git
```

General flow for each plugin:

1. Install plugin
2. List plugins
3. Run onboard (adds/merges defaults)
4. Enable and configure channel section

## 1) MCP Server Plugin

Install:

```bash
pip install "git+https://github.com/fuutott/nanobot.git#subdirectory=plugins/nanobot-channel-mcpserver"
```

Or with uv:

```bash
uv pip install "git+https://github.com/fuutott/nanobot.git#subdirectory=plugins/nanobot-channel-mcpserver"
```

List and onboard:

```bash
nanobot plugins list
nanobot onboard
```

Config hints (channels.mcpserver):

```json
{
  "channels": {
    "mcpserver": {
      "enabled": true,
      "host": "127.0.0.1",
      "port": 18793,
      "apiKeys": {
        "make-up-a-token-here": "owner"
      },
      "allowFrom": ["owner"],
      "allowedOrigins": [],
      "requestTimeoutSeconds": 120,
      "sessionTtlSeconds": 3600,
      "enableResumption": false,
      "defaultProtocolVersion": "2025-03-26"
    }
  }
}
```

Notes:

- apiKeys is required for authentication.
- Use allowFrom to restrict callers if needed.

## 2) OpenAI API Plugin

Install:

```bash
pip install "git+https://github.com/fuutott/nanobot.git#subdirectory=plugins/nanobot-channel-openaiapi"
```

Or with uv:

```bash
uv pip install "git+https://github.com/fuutott/nanobot.git#subdirectory=plugins/nanobot-channel-openaiapi"
```

List and onboard:

```bash
nanobot plugins list
nanobot onboard
```

Config hints (channels.openaiapi):

```json
{
  "channels": {
    "openaiapi": {
      "enabled": true,
      "host": "127.0.0.1",
      "port": 18791,
      "apiKeys": {
        "make-up-a-token-here": "owner"
      },
      "allowFrom": ["owner"],
      "requestTimeoutSeconds": 120
    }
  }
}
```

Notes:

- apiKeys is required.
- Endpoint is OpenAI-compatible, useful for clients and SDKs that already speak OpenAI APIs.

## 3) Web UI Plugin

Install:

```bash
pip install "git+https://github.com/fuutott/nanobot.git#subdirectory=plugins/nanobot-channel-webui"
```

Or with uv:

```bash
uv pip install "git+https://github.com/fuutott/nanobot.git#subdirectory=plugins/nanobot-channel-webui"
```

List and onboard:

```bash
nanobot plugins list
nanobot onboard
```

Config hints (channels.webui):

```json
{
  "channels": {
    "webui": {
      "enabled": true,
      "host": "127.0.0.1",
      "port": 18792,
      "title": "nanobot",
      "username": "owner",
      "password": "make-a-password-here",
      "allowFrom": ["owner"],
      "allowedOrigins": ["http://localhost:18792"]
    }
  }
}
```

Notes:

- If username and password are empty, authentication is effectively off.
- For remote browser access, set allowedOrigins explicitly.

## Quick Verify

After enabling a plugin:

```bash
nanobot plugins list
nanobot gateway
```
