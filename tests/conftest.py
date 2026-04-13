from __future__ import annotations

import sys
from pathlib import Path


def _add_plugin_path(path: Path) -> None:
    text = str(path)
    if path.exists() and text not in sys.path:
        sys.path.insert(0, text)


_ROOT = Path(__file__).resolve().parent.parent
_add_plugin_path(_ROOT / "plugins" / "nanobot-channel-webui")
_add_plugin_path(_ROOT / "plugins" / "nanobot-channel-openaiapi")
_add_plugin_path(_ROOT / "plugins" / "nanobot-channel-mcpserver")
