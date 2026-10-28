"""Where the Claude Code plugin (agents, commands, hooks) is.

In a source checkout it sits next to the package (`agentic-sdlc/plugin`); an installed package carries its own
copy (`agentic_sdlc/plugin`, added at build time), so `uv tool install git+https://…` works on any machine.
SDLC_PLUGIN_DIR overrides both.
"""

import os
from pathlib import Path

_HERE = Path(__file__).resolve().parent


def plugin_dir() -> Path:
    if os.environ.get("SDLC_PLUGIN_DIR"):
        return Path(os.environ["SDLC_PLUGIN_DIR"]).expanduser()
    for p in (_HERE.parents[1] / "plugin", _HERE / "plugin"):  # source checkout, then the bundled copy
        if (p / ".claude-plugin" / "plugin.json").exists():
            return p
    return _HERE / "plugin"
