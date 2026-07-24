"""Spawn tool for creating background subagents."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from nanobot.agent.tools.base import Tool, ToolResult, tool_parameters
from nanobot.agent.tools.context import current_request_context
from nanobot.agent.tools.schema import (
    ArraySchema,
    BooleanSchema,
    NumberSchema,
    StringSchema,
    tool_parameters_schema,
)
from nanobot.security.workspace_access import current_workspace_scope

if TYPE_CHECKING:
    from nanobot.agent.subagent import SubagentManager


@tool_parameters(
    tool_parameters_schema(
        task=StringSchema("The task for the subagent to complete"),
        label=StringSchema("Optional short label for the task (for display)"),
        temperature=NumberSchema(
            description=(
                "Optional sampling temperature for the subagent "
                "(0.0 = deterministic, higher = more creative). "
                "Defaults to the provider's configured temperature."
            ),
            minimum=0.0,
            maximum=2.0,
        ),
        provider=StringSchema(
            "Optional provider name (e.g. 'openrouter', 'zhipu'). "
            "Defaults to the main agent's provider. "
            "Run `my check available_providers` to see what's configured."
        ),
        model=StringSchema(
            "Optional model name (e.g. 'claude-opus-4-5', 'glm-5.1'). "
            "Defaults to the main agent's model. "
            "Run `my check available_models` to see configured presets."
        ),
        systemPrompt=StringSchema(
            "Optional minimal system prompt for lightweight tasks. "
            "Omit to inherit the full parent system prompt (AGENTS.md, skills, "
            "tool schemas, runtime context — ~10K tokens). Provide a short "
            "string to drastically cut input cost when the task doesn't need "
            "the full instruction set (e.g. 'Write a one-page story.')."
        ),
        tools=ArraySchema(
            items=StringSchema(""),
            description=(
                "Optional whitelist of tool names the subagent may use (e.g. "
                "[\"write_file\", \"read_file\"]). Omit to inherit the full "
                "parent tool set (~5K tokens of tool-schema preamble per spawn). "
                "Provide a short list to cut input cost when the task only "
                "needs a couple of tools. Pass [] for a no-tools text-only "
                "subagent."
            ),
        ),
        skills=ArraySchema(
            items=StringSchema(""),
            description=(
                "Optional whitelist of skill names listed in the subagent's "
                "system prompt (e.g. [\"adhd\"]). Omit to inherit every "
                "available skill (~1500 tokens of skill catalog in a default "
                "workspace). Pass [] to strip the skill catalog entirely "
                "(best for lightweight tasks where the subagent doesn't need "
                "to consult any skills). Ignored when systemPrompt is set."
            ),
        ),
        wait=BooleanSchema(
            description=(
                "Wait for the subagent and return its result directly. Use this for a "
                "blocking consultation that must inform the current turn. Defaults to "
                "false for background execution."
            ),
            default=False,
        ),
        required=["task"],
    )
)
class SpawnTool(Tool):
    """Tool to spawn a subagent for background task execution."""

    def __init__(self, manager: "SubagentManager"):
        self._manager = manager

    @classmethod
    def create(cls, ctx: Any) -> Tool:
        return cls(manager=ctx.subagent_manager)

    @property
    def name(self) -> str:
        return "spawn"

    @property
    def description(self) -> str:
        return (
            "Spawn a subagent to handle a task in the background. "
            "Use this for complex or time-consuming tasks that can run independently. "
            "Set wait=true for a consultation whose result must inform the current turn. "
            "The subagent will complete the task and report back when done. "
            "For deliverables or existing projects, inspect the workspace first "
            "and use a dedicated subdirectory when helpful."
        )

    async def execute(
        self,
        task: str,
        label: str | None = None,
        temperature: float | None = None,
        provider: str | None = None,
        model: str | None = None,
        system_prompt: str | None = None,
        tools: list[str] | None = None,
        skills: list[str] | None = None,
        wait: bool = False,
        **kwargs: Any,
    ) -> str:
        """Spawn a subagent to execute the given task."""
        running = self._manager.get_running_count()
        limit = self._manager.max_concurrent_subagents
        if running >= limit:
            return (
                f"Cannot spawn subagent: concurrency limit reached "
                f"({running}/{limit} running). Wait for a running subagent "
                f"to complete before spawning a new one."
            )
        request_ctx = current_request_context()
        if request_ctx is None or request_ctx.runtime is None:
            return ToolResult.error("Error: spawn requires an active model runtime")
        origin_channel = request_ctx.channel
        origin_chat_id = request_ctx.chat_id
        session_key = request_ctx.session_key or f"{origin_channel}:{origin_chat_id}"
        method = self._manager.run_inline if wait else self._manager.spawn
        return await method(
            task=task,
            runtime=request_ctx.runtime,
            label=label,
            origin_channel=origin_channel,
            origin_chat_id=origin_chat_id,
            session_key=session_key,
            origin_message_id=request_ctx.message_id,
            temperature=temperature,
            workspace_scope=current_workspace_scope(),
            provider=provider,
            model=model,
            system_prompt=system_prompt,
            tools=tools,
            skills=skills,
        )
