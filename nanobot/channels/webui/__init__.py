"""WebUI channel package (fork).

Thin in-tree wrapper that makes the fork's ``nanobot-channel-webui`` plugin
discoverable under upstream's self-contained channel model (462a0dfb removed the
``nanobot.channels`` entry-point group our plugins used). The runtime class
still lives in the installed plugin package; ``runtime.py`` re-exports it so the
manifest's in-package runtime check passes.
"""
