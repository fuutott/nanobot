# Provider Fallback Plan

Date: 2026-04-13

## Goal

Add runtime provider failover so nanobot can:

- retry the current provider/model up to a configured limit
- fail over to secondary, then tertiary provider/model preferences
- automatically return to the original provider/model after a configured cooldown window

This should reduce user-visible outages when one provider is degraded or unresponsive.

## Scope

In scope:

- Runtime LLM request fallback across provider/model pairs
- Configurable ordered fallback preference array
- Cooldown-based return to primary provider/model
- Logging, status visibility, and tests

Out of scope for v1:

- Cost-aware routing
- Quality-score-based dynamic routing
- Per-tool or per-skill provider routing

## Current behavior summary

Today nanobot has:

- transient retry logic inside one provider instance
- retry modes standard or persistent
- provider selection at startup/config resolution time

Today nanobot does not:

- switch provider/model at runtime when retries fail
- keep a provider health state with cooldown and recovery

## Desired behavior

### Request lifecycle

For each model request:

1. Try primary provider/model.
2. Retry primary up to max_retries_per_target.
3. If still failing with retryable errors, try next fallback target.
4. Continue until one target succeeds or all targets fail.
5. If all fail, return last error with aggregated attempt summary.

### Return to primary

When primary is marked unhealthy:

- route new requests to fallback targets
- after primary_recovery_after_seconds, probe primary again
- if probe succeeds, restore primary as first choice
- if probe fails, re-open cooldown and continue on fallback

## Configuration design

Add to agent defaults (or a new provider resilience block):

```json
{
  "agents": {
    "defaults": {
      "provider_fallback": {
        "enabled": true,
        "max_retries_per_target": 3,
        "primary_recovery_after_seconds": 300,
        "probe_on_recovery": true,
        "fallback_targets": [
          { "provider": "anthropic", "model": "claude-sonnet-4-20250514" },
          { "provider": "openrouter", "model": "openai/gpt-4.1" },
          { "provider": "deepseek", "model": "deepseek-chat" }
        ],
        "failover_on": {
          "timeout": true,
          "connection": true,
          "status_codes": [408, 409, 429, 500, 502, 503, 504]
        },
        "never_failover_on": {
          "insufficient_quota": true,
          "invalid_api_key": true,
          "auth_error": true,
          "bad_request": true
        }
      }
    }
  }
}
```

Notes:

- fallback_targets is an ordered provider and model preference array.
- Primary target remains the currently configured provider and model.
- Fallback targets should be validated at startup.

## Data model additions

### Config schema

Add pydantic models in nanobot/config/schema.py:

- ProviderTarget
- ProviderFailoverRules
- ProviderFallbackConfig

Suggested fields:

- enabled: bool
- max_retries_per_target: int
- primary_recovery_after_seconds: int
- probe_on_recovery: bool
- fallback_targets: list[ProviderTarget]
- failover_on: ProviderFailoverRules
- never_failover_on: ProviderFailoverRules

### Runtime state

Add runtime health tracker (in memory):

- target_key: provider:model
- state: healthy | cooldown
- cooldown_until_epoch
- consecutive_failures
- last_error_signature
- last_success_epoch

Thread safety:

- protect shared health map with asyncio lock

## Architecture changes

### Provider manager layer

Introduce a runtime manager that wraps provider calls and can select targets:

- new module nanobot/providers/fallback_manager.py
- responsible for:
  - resolving target order
  - invoking chat_with_retry and chat_stream_with_retry on selected provider
  - classifying retryable vs non-retryable failures
  - updating health state and cooldown

### Agent integration

AgentRunner currently calls self.provider directly.

Change AgentRunner request path to call fallback manager interface:

- request_model(messages, tools, model, retry_mode, stream_callback)

This keeps fallback policy out of loop orchestration and tool execution logic.

### Provider instantiation

Need a provider factory that can build provider instances for each fallback target:

- reuse existing provider creation logic from nanobot/nanobot.py
- factor into shared provider factory function if needed

## Failover algorithm

Given primary P0 and fallback targets P1..Pn:

1. Build candidate list:
   - if P0 healthy: P0 then P1..Pn
   - if P0 in cooldown: P1..Pn then optionally probe P0 if cooldown expired
2. For each candidate Pi:
   - call Pi chat_with_retry using max_retries_per_target policy
   - if success: mark Pi healthy and return
   - if failure:
     - if non-failover error: return immediately
     - else continue to next candidate
3. If all candidates fail:
   - return error with attempt metadata

Primary recovery:

- on each request, if now >= cooldown_until and probe_on_recovery true:
  - issue normal request to primary first
  - success closes cooldown
  - failure extends cooldown

## Error classification rules

Failover eligible by default:

- timeout
- connection errors
- 5xx
- retryable 429 (rate limit style)

Not failover eligible by default:

- invalid credentials
- permission errors
- malformed request
- quota exhausted or billing hard stop

Reason:

- switching providers for invalid auth or bad request can hide real config errors.

## Streaming behavior

For stream requests:

- attempt failover before first content token is emitted
- once stream output has started, do not switch mid-stream
- if stream fails before first token and error is failover-eligible, continue to next target

## Observability

Add structured logs per attempt:

- request_id
- target provider and model
- attempt index
- retry count
- error type or status
- failover decision
- cooldown transitions

Expose status in nanobot status:

- current primary state
- cooldown remaining seconds
- last successful target

## Testing plan

### Unit tests

- target ordering with healthy primary
- target ordering when primary in cooldown
- recovery probe succeeds and restores primary
- recovery probe fails and extends cooldown
- non-failover errors stop chain immediately
- failover errors advance chain
- all targets fail returns aggregated error

### Integration tests

- mock provider A timeout, provider B success
- mock provider A rate limit, provider B success
- mock provider A invalid key, no fallback attempted
- streaming failure before first token falls back

### Regression tests

- existing behavior unchanged when provider_fallback.enabled is false
- existing provider_retry_mode semantics preserved per target

## Rollout plan

Phase 1:

- add config and fallback manager behind feature flag
- wire AgentRunner request path to manager
- add logs and unit tests

Phase 2:

- add status output and integration tests
- document configuration in README

Phase 3:

- optional persistence of health state across restart
- optional adaptive ordering based on recent success

## Risks and mitigations

Risk: hidden cost increase by falling back to expensive models.
Mitigation: require explicit ordered fallback_targets and document cost impact.

Risk: fallback loops or long latency chains.
Mitigation: cap retries per target and cap number of fallback targets.

Risk: policy confusion between retry and failover.
Mitigation: keep retry within target and failover across targets as separate stages.

## Acceptance criteria

- System can fail over from primary to secondary and tertiary targets after retry exhaustion.
- Fallback target order follows configured provider and model preference array.
- System returns to primary provider/model after configured recovery window when primary becomes healthy.
- Feature is configurable and can be disabled without changing current behavior.
- Tests cover success path, failover path, no-failover path, and recovery path.
