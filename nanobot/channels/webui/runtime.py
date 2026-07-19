"""Re-export the WebUI channel runtime from the installed plugin package.

The manifest requires an in-package runtime module; the real implementation
(including the prebuilt WebUI dist) ships in ``nanobot-channel-webui``.
"""

from nanobot_channel_webui.channel import WebUIChannel

__all__ = ["WebUIChannel"]
