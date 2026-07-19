"""WebUI channel manifest (fork).

Makes the ``nanobot-channel-webui`` plugin discoverable under upstream's
self-contained channel model. Config (host, port, auth, allowedOrigins) is read
by the channel from its ``channels.webui`` section; the setup spec is left empty
because the fork configures these directly in config.json rather than through the
onboarding wizard.
"""

from nanobot.channels.contracts import ChannelSetupSpec
from nanobot.channels.plugin import ChannelPlugin

SETUP_SPEC = ChannelSetupSpec(fields={})

PLUGIN = ChannelPlugin(
    name="webui",
    display_name="Web UI",
    runtime=f"{__package__}.runtime:WebUIChannel",
    setup=SETUP_SPEC,
    settings_visible=False,
)
