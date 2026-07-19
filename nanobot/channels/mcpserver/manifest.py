"""MCP Server channel manifest (fork).

Makes the ``nanobot-channel-mcpserver`` plugin discoverable under upstream's
self-contained channel model. Config (host, port, apiKeys, protocol version) is
read by the channel from its ``channels.mcpserver`` section.
"""

from nanobot.channels.contracts import ChannelSetupSpec
from nanobot.channels.plugin import ChannelPlugin

SETUP_SPEC = ChannelSetupSpec(fields={})

PLUGIN = ChannelPlugin(
    name="mcpserver",
    display_name="MCP Server",
    runtime=f"{__package__}.runtime:MCPServerChannel",
    setup=SETUP_SPEC,
    settings_visible=False,
)
