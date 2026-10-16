"""Web configuration shared by search/fetch tools and the root schema."""

from pydantic import AliasChoices, Field

from nanobot.config_base import Base


class WebSearchConfig(Base):
    """Web search configuration."""
    provider: str = "duckduckgo"
    api_key: str = ""
    base_url: str = ""
    max_results: int = 5
    timeout: int = 30


class WebFetchConfig(Base):
    """Web fetch tool configuration."""
    use_jina_reader: bool = True


class WebToolsConfig(Base):
    """Web tools configuration."""
    # Fork: accept `enabled` as an alias for backward compatibility with older configs.
    enable: bool = Field(default=True, validation_alias=AliasChoices("enable", "enabled"))
    proxy: str | None = None
    user_agent: str | None = None
    search: WebSearchConfig = Field(default_factory=WebSearchConfig)
    fetch: WebFetchConfig = Field(default_factory=WebFetchConfig)
