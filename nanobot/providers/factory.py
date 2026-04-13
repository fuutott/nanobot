"""Create LLM providers from config."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from nanobot.config.schema import Config
from nanobot.providers.base import GenerationSettings, LLMProvider
from nanobot.providers.registry import find_by_name


@dataclass(frozen=True)
class ProviderSnapshot:
    provider: LLMProvider
    model: str
    context_window_tokens: int
    signature: tuple[object, ...]


def _resolve_text_provider(config: Config) -> tuple[str, str, object | None, object | None]:
    """Resolve which provider/model/spec to use for the primary (text) provider.

    Honors ``defaultTextModel`` and ``defaultTextProvider`` overrides, falling
    back to the standard ``provider`` / ``model`` defaults.
    """
    defaults = config.agents.defaults
    model = defaults.default_text_model or defaults.model

    forced_text_provider = (defaults.default_text_provider or "").strip()
    if forced_text_provider:
        forced_spec = find_by_name(forced_text_provider)
        if not forced_spec:
            raise ValueError(
                f"Unknown defaultTextProvider '{defaults.default_text_provider}'."
            )
        provider_name = forced_spec.name
        p = getattr(config.providers, provider_name, None)
        spec = forced_spec
    else:
        provider_name = config.get_provider_name(model)
        p = config.get_provider(model)
        spec = find_by_name(provider_name) if provider_name else None

    return model, provider_name, p, spec


def make_provider(config: Config) -> LLMProvider:
    """Create the LLM provider implied by config."""
    model, provider_name, p, spec = _resolve_text_provider(config)
    backend = spec.backend if spec else "openai_compat"

    api_base = p.api_base if p and p.api_base else None
    if not api_base and spec and (spec.is_gateway or spec.is_local):
        api_base = spec.default_api_base or None
    if not api_base:
        api_base = config.get_api_base(model)

    if backend == "azure_openai":
        if not p or not p.api_key or not p.api_base:
            raise ValueError("Azure OpenAI requires api_key and api_base in config.")
    elif backend == "openai_compat" and not model.startswith("bedrock/"):
        needs_key = not (p and p.api_key)
        exempt = spec and (spec.is_oauth or spec.is_local or spec.is_direct)
        if needs_key and not exempt:
            raise ValueError(f"No API key configured for provider '{provider_name}'.")

    if backend == "openai_codex":
        from nanobot.providers.openai_codex_provider import OpenAICodexProvider

        provider = OpenAICodexProvider(default_model=model)
    elif backend == "azure_openai":
        from nanobot.providers.azure_openai_provider import AzureOpenAIProvider

        provider = AzureOpenAIProvider(
            api_key=p.api_key,
            api_base=p.api_base,
            default_model=model,
        )
    elif backend == "github_copilot":
        from nanobot.providers.github_copilot_provider import GitHubCopilotProvider

        provider = GitHubCopilotProvider(default_model=model)
    elif backend == "anthropic":
        from nanobot.providers.anthropic_provider import AnthropicProvider

        provider = AnthropicProvider(
            api_key=p.api_key if p else None,
            api_base=api_base,
            default_model=model,
            extra_headers=p.extra_headers if p else None,
        )
    else:
        from nanobot.providers.openai_compat_provider import OpenAICompatProvider

        provider = OpenAICompatProvider(
            api_key=p.api_key if p else None,
            api_base=api_base,
            default_model=model,
            extra_headers=p.extra_headers if p else None,
            spec=spec,
        )

    defaults = config.agents.defaults
    provider.generation = GenerationSettings(
        temperature=defaults.temperature,
        max_tokens=defaults.max_tokens,
        reasoning_effort=defaults.reasoning_effort,
    )
    return provider


def provider_signature(config: Config) -> tuple[object, ...]:
    """Return the config fields that affect the primary LLM provider."""
    model, provider_name, _, _ = _resolve_text_provider(config)
    defaults = config.agents.defaults
    return (
        model,
        defaults.provider,
        defaults.default_text_provider,
        provider_name,
        config.get_api_key(model),
        config.get_api_base(model),
        defaults.max_tokens,
        defaults.temperature,
        defaults.reasoning_effort,
        defaults.context_window_tokens,
    )


def build_provider_snapshot(config: Config) -> ProviderSnapshot:
    model, _, _, _ = _resolve_text_provider(config)
    return ProviderSnapshot(
        provider=make_provider(config),
        model=model,
        context_window_tokens=config.agents.defaults.context_window_tokens,
        signature=provider_signature(config),
    )


def load_provider_snapshot(config_path: Path | None = None) -> ProviderSnapshot:
    from nanobot.config.loader import load_config, resolve_config_env_vars

    return build_provider_snapshot(resolve_config_env_vars(load_config(config_path)))
