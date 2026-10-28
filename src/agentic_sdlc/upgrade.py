"""`sdlc upgrade`: bring a repo's Agentic SDLC files up to the installed version.

- sdlc.config.yaml: adds settings that newer versions introduced (a new stage, work types, integration switches)
  without changing any value the repo already set;
- the generated CLAUDE.md section and .claude/settings.json are rewritten; missing templates and product docs are
  added; the Copilot export is refreshed when Copilot is a host;
- templates that differ from the defaults are listed, and replaced with `--templates` (commit your copies first:
  a template a team customised on purpose should stay; use `sdlc templates --update <name>` for one at a time).
"""

from pathlib import Path
from typing import Any

import yaml

from . import __version__, config, generate, hosts


def _defaults(raw: dict[str, Any]) -> dict[str, Any]:
    base = config.build_config({"project": raw.get("project") or {"name": "x", "type": "new"}})
    return {k: base[k] for k in ("stages", "work_types", "integrations")}


def _add_missing(target: dict[str, Any], defaults: dict[str, Any], path: str = "") -> list[str]:
    added = []
    for k, v in defaults.items():
        if k not in target:
            target[k] = v
            added.append(f"{path}{k}")
        elif isinstance(v, dict) and isinstance(target[k], dict):
            added += _add_missing(target[k], v, f"{path}{k}.")
    return added


def run(root: Path, replace_templates: bool = False) -> dict[str, Any]:
    path = root / config.CONFIG_FILE
    raw = yaml.safe_load(path.read_text()) or {}
    added = _add_missing(raw, _defaults(raw))
    # work types are defined whole: only add a type the repo has never configured, never its keys one by one
    added = [a for a in added if not (a.startswith("work_types.") and a.count(".") > 1)]
    if added:
        config.validate(raw)
        config.write(root, raw)
    cfg = config.load(root)
    done = [f"sdlc.config.yaml: added {', '.join(added)}" if added else "sdlc.config.yaml: nothing new"]
    done += generate.ensure_templates(root, cfg)
    done.append(f"CLAUDE.md: {generate.write_claude_md(root, cfg)}")
    done.append(f".claude/settings.json: {generate.write_settings(root, cfg)}")
    if "copilot" in (cfg.get("integrations") or {}).get("hosts", ["claude"]):
        done += hosts.export_copilot(root, cfg)
    differ = [(name, rel) for name, rel in generate.PROJECT_TEMPLATES.items()
              if (root / rel).exists() and (root / rel).read_text() != generate.template(name)]
    if replace_templates:
        for name, rel in differ:
            (root / rel).write_text(generate.template(name))
            done.append(f"updated {rel} from the default {name}")
    elif differ:
        done.append("templates that differ from the defaults (kept; `--templates` replaces them): "
                    + ", ".join(rel for _, rel in differ))
    return {"version": __version__, "changes": done, "config_added": added,
            "templates_differ": [rel for _, rel in differ] if not replace_templates else []}
