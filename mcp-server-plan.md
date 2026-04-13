# MCP Server Plan

Date: 2026-04-06

## Goal

Add a new built-in channel, `mcpserver`, so nanobot can be used as a proper MCP server over Streamable HTTP, with API-key auth, session support, progress notifications, cancellation, and server-to-client push.

This needs to be done as plugin check CHANNEL_PLUGIN_GUIDE.md

This should be separate from the existing OpenAI-compatible HTTP channel in `nanobot/channels/openaiapi.py`.

## Relevant existing code

- Existing OpenAI-style channel: `nanobot/channels/openaiapi.py`
- Channel lifecycle and discovery: `nanobot/channels/base.py`, `nanobot/channels/manager.py`, `nanobot/channels/registry.py`
- MCP client support already exists: `nanobot/agent/tools/mcp.py`
- Agent direct invocation path: `nanobot/agent/loop.py`
- Slash commands: `nanobot/command/builtin.py`, `nanobot/command/router.py`
- Outbound messaging path: `nanobot/bus/events.py`, `nanobot/bus/queue.py`, `nanobot/agent/tools/message.py`
- Existing unprompted/follow-up style behavior: `nanobot/heartbeat/service.py`, `nanobot/agent/subagent.py`

## Protocol target

Primary target: MCP Streamable HTTP.

The new server should follow current MCP guidance for:

- single MCP endpoint path supporting `POST`, `GET`, and `DELETE`
- initialization lifecycle: `initialize` then `notifications/initialized`
- session management via `MCP-Session-Id`
- protocol version handling via `MCP-Protocol-Version`
- server-to-client push via SSE on `GET` and optionally `POST`
- progress updates via `notifications/progress`
- cancellation via `notifications/cancelled`
- origin validation for HTTP requests
- authentication on all connections

## Recommended implementation shape

### New built-in channel

Add a new channel module:

- `nanobot/channels/mcpserver.py`

Add a new config model:

- `MCPServerChannelConfig` in `nanobot/config/schema.py`

Suggested config fields:

- `enabled`
- `host`
- `port`
- `api_key`
- `api_keys`
- `allow_from`
- `allowed_origins`
- `request_timeout_seconds`
- `session_ttl_seconds`
- `enable_resumption`
- `default_protocol_version`

Safer default bind should be `127.0.0.1`.

### Transport behavior

Expose one MCP endpoint, for example:

- `POST /mcp`
- `GET /mcp`
- `DELETE /mcp`

Behavior:

- `POST /mcp`
	- accepts one JSON-RPC request, notification, or response
	- returns `202` for accepted notifications/responses
	- returns either JSON or SSE for requests
- `GET /mcp`
	- opens SSE stream for server-to-client push
	- used for unrelated notifications and resumable replay later
- `DELETE /mcp`
	- terminates the current MCP session if session deletion is enabled

### Session state

Track per-session state:

- authenticated principal
- negotiated protocol version
- initialized flag
- active SSE streams
- in-flight requests/tasks by request id
- progress tokens
- per-stream replay buffer if resumability is enabled
- logging level preference

## MCP surface area for v1

### Core lifecycle and utility methods

Implement at least:

- `initialize`
- `ping`
- `tools/list`
- `tools/call`
- `notifications/initialized`
- `notifications/cancelled`

Optional but cheap for v1:

- `logging/setLevel`
- `notifications/message`

### Expose the agent as a tool

Start with one MCP tool:

- `agent_chat`

Suggested input schema:

- `message: string`
- `sessionId?: string`
- `conversationId?: string`
- `metadata?: object`

Suggested behavior:

- map `sessionId` or `conversationId` to nanobot session continuity
- call `AgentLoop.process_direct(...)`
- return MCP tool result content as text

This keeps the first version simple and highly interoperable: any MCP client that supports tools can use nanobot immediately.

## Security model

### Authentication

Use API-key auth for v1.

Pattern:

- `Authorization: Bearer <key>`
- support both `api_key` and mapped `api_keys`, same style as the existing OpenAI API channel

Notes:

- API keys are acceptable for a private/self-hosted MCP server
- keep auth middleware pluggable so OAuth can be added later without changing transport or tool execution logic

### Origin validation

Validate `Origin` for all incoming HTTP requests.

Suggested policy:

- if `Origin` is absent: allow non-browser clients
- if `Origin` is present: require exact match against `allowed_origins`
- otherwise return `403`

### Sender allowlist

Reuse the existing `allow_from` idea from the OpenAI channel:

- principal id
- optional client-provided user/session alias
- client host fallback when appropriate

## Progress and long-running work

When a client provides `_meta.progressToken`, the server should send `notifications/progress` while `agent_chat` is running.

Suggested progress mapping:

- progress message for agent startup
- progress message for model generation phases
- progress message for tool activity summaries
- stop sending progress after final result or cancellation

Use coarse progress values unless nanobot can produce stable numeric stages. The human-readable `message` field matters more than exact percentages.

## Cancellation

Support `notifications/cancelled` for in-flight `tools/call` requests.

Implementation notes:

- keep a request-id to task map per session
- on cancellation, stop the task if still running
- free resources and clear progress token state
- do not emit a late success response if cancellation wins

## Resumability

Recommended split:

- v1: no replay, but structure code so replay can be added cleanly
- v1.1: attach SSE event ids and support `Last-Event-ID`

If replay is added later:

- ids must be unique per stream/session
- replay only messages from the same originating stream
- never broadcast the same pushed message on multiple streams

## Question 1: should slash commands like `/new` be recreated as tools?

Short answer: yes, selectively, but not as literal slash commands.

Recommendation:

- do not expose slash parsing over MCP as the primary interface
- do expose the useful command semantics as explicit MCP tools

Why:

- MCP clients already discover capabilities through `tools/list`
- tool names and schemas are clearer than hidden slash syntax
- this avoids teaching clients a nanobot-specific text command language

### Suggested mapping

Expose these as tools:

- `/new` -> `new_session`
- `/status` -> `get_status`
- `/stop` -> `stop_active_tasks`

Maybe expose later, admin-only:

- `/restart` -> `restart_server`

Do not bother exposing as a tool:

- `/help`

Reason: `tools/list` already serves the discovery role.

### Recommended tool behavior

`new_session`
- clears or rotates the target conversation/session
- returns a confirmation message

`get_status`
- returns the same sort of runtime/session info as `/status`

`stop_active_tasks`
- cancels active work for the target session

`restart_server`
- dangerous
- should be disabled by default or restricted to admin principals only

### Implementation note

Do not duplicate business logic if avoidable.

Preferred approach:

- extract the core command behaviors into reusable functions/service methods
- keep slash command handlers and MCP tools as thin adapters over the same logic

## Question 2: can nanobot send messages unprompted, or as follow-up, through MCP?

Short answer: yes.

There are already internal patterns proving nanobot can do this:

- the `message` tool can push outbound content directly
- heartbeat can notify a user later without a direct prompt
- subagents can inject follow-up work/results back into the main agent loop

### What is possible in MCP terms

Yes, via server-to-client push over SSE.

MCP Streamable HTTP allows the server to send JSON-RPC notifications and requests to the client on an open SSE stream.

That gives us two distinct cases:

#### 1. Follow-up related to an active request

This is the easy case.

Options:

- progress notifications during `tools/call`
- final result in the normal JSON-RPC response
- optional extra logging notifications during execution

This should be part of v1.

#### 2. Unprompted messages unrelated to a current request

This is also possible, but it needs a defined contract.

Recommended approach:

- require the client to keep a `GET /mcp` SSE stream open for the session
- advertise an experimental/custom capability such as `experimental.nanobotPush`
- send a custom notification method, for example `notifications/nanobot/message`

Suggested notification payload:

- `sessionId`
- `conversationId`
- `kind` (`followup`, `proactive`, `system`)
- `content`
- `createdAt`
- optional `source` (`heartbeat`, `subagent`, `message_tool`, etc.)

Why a custom notification:

- MCP does not define a standard generic “chat message from server” notification for this use case
- a custom method keeps the transport compliant while making nanobot-specific behavior explicit

### Product recommendation

For v1:

- support follow-up/progress within active requests
- support optional server push via a custom notification only when a client has explicitly opened an SSE stream

For v1.1:

- add durable queued delivery for disconnected sessions
- add replay with `Last-Event-ID`
- optionally expose a resource or inbox model for missed proactive messages

## Suggested phases

### v1

- new `mcpserver` channel
- API-key auth
- origin validation
- Streamable HTTP transport
- session management
- `agent_chat` tool
- `tools/list`, `tools/call`, `initialize`, `ping`
- progress notifications
- cancellation
- basic SSE push plumbing

### v1.1

- session replay with `Last-Event-ID`
- selected command tools: `new_session`, `get_status`, `stop_active_tasks`
- optional custom push notification: `notifications/nanobot/message`
- structured logging support

### v1.2

- admin-only `restart_server`
- optional legacy compatibility layer for older MCP HTTP+SSE clients
- durable proactive message queue/inbox

## Proposed first implementation decision

Build the smallest useful compliant server first:

1. `mcpserver` channel
2. API-key auth
3. Streamable HTTP endpoint
4. `agent_chat` tool
5. progress + cancellation
6. follow-up push only for active requests

Then add command-tools and proactive notifications once the base transport/session layer is stable.
