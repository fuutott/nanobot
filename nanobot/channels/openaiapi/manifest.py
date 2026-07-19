"""OpenAI API channel manifest (fork).

Makes the ``nanobot-channel-openaiapi`` plugin discoverable under upstream's
self-contained channel model. Config (host, port, apiKeys, allowFrom) is read by
the channel from its ``channels.openaiapi`` section.
"""

from nanobot.channels.contracts import ChannelSetupSpec
from nanobot.channels.plugin import ChannelPlugin

SETUP_SPEC = ChannelSetupSpec(fields={})

PLUGIN = ChannelPlugin(
    name="openaiapi",
    display_name="OpenAI API",
    runtime=f"{__package__}.runtime:OpenAIAPIChannel",
    setup=SETUP_SPEC,
    settings_visible=False,
)
