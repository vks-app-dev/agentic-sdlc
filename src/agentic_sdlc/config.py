"""Load, validate and write sdlc.config.yaml."""

from pathlib import Path
from typing import Any

import yaml

from .stages import (AUTONOMY_LEVELS, BY_NAME, DEFAULT_ORCHESTRATOR_MODEL, MODEL_ALIASES, SIZES, STAGE_NAMES,
                     TOGGLEABLE, WORK_TYPES, default_model)

CONFIG_FILE = "sdlc.config.yaml"


class ConfigError(Exception):
    pass


def find_root(start: Path | None = None) -> Path:
    """Walk up from `start` to the folder that holds sdlc.config.yaml."""
    here = (start or Path.cwd()).resolve()
    for d in (here, *here.parents):
        if (d / CONFIG_FILE).exists():
            return d
    raise ConfigError(f"No {CONFIG_FILE} found in {here} or its parents. Run /sdlc-init first.")


def default_stages() -> dict[str, dict[str, Any]]:
    stages = {}
    for name in STAGE_NAMES:
        st = BY_NAME[name]
        stages[name] = {
            "enabled": True,
            "autonomy": "L2",
            "gate": list(st.default_gate),
        }
    stages["development"].update(parallel_coders=3, tdd=True, max_attempts=3)
    stages["cicd"]["autonomy"] = "L3"
    stages["cicd"]["enabled"] = False  # the pipeline is set up once per project, not per work item
    stages["monitoring"]["enabled"] = False  # out of scope for now (docs/PARKED.md, P5)
    stages["release"]["autonomy"] = "L1"
    return stages


def default_work_types() -> dict[str, dict[str, Any]]:
    """The editable part of each work type, as written to sdlc.config.yaml."""
    out = {}
    for w in WORK_TYPES.values():
        d: dict[str, Any] = {"stages": list(w.stages)}
        if w.gates:
            d["gates"] = {k: list(v) for k, v in w.gates.items()}
        if w.design_if:
            d["design_if_size"] = list(w.design_if)
        out[w.name] = d
    return out


def work_type(cfg: dict[str, Any], name: str) -> dict[str, Any]:
    """A work type with this repo's overrides applied: stages, gates and design_if_size."""
    w = WORK_TYPES[name]
    o = (cfg.get("work_types") or {}).get(name) or {}
    return {"name": w.name, "title": w.title, "purpose": w.purpose, "template": w.template,
            "stages": list(o.get("stages", w.stages)),
            "gates": {**{k: list(v) for k, v in w.gates.items()}, **(o.get("gates") or {})},
            "design_if_size": list(o.get("design_if_size", w.design_if)),
            "steps": w.steps, "stage_titles": w.stage_titles}


KINDS = ("frontend", "backend", "fullstack", "other")


def _valid_model(m: Any) -> bool:
    return isinstance(m, str) and (m in MODEL_ALIASES or m.startswith("claude-"))


def agent_model(cfg: dict[str, Any], agent: str) -> str:
    """The model an agent runs on: the repo's override (models.agents), else the default for its role."""
    from .org import effective_model

    return effective_model(cfg, ((cfg.get("models") or {}).get("agents") or {}).get(agent) or default_model(agent))


def orchestrator_model(cfg: dict[str, Any]) -> str:
    return (cfg.get("models") or {}).get("orchestrator") or DEFAULT_ORCHESTRATOR_MODEL


def components(cfg: dict[str, Any]) -> list[dict[str, Any]]:
    """The parts of the repo, each with its own folder and commands. A repo without a `components:` section
    is one component at the root whose commands are `stack.commands` (name "")."""
    comps = cfg.get("components") or {}
    if not comps:
        return [{"name": "", "path": ".", "kind": "frontend" if cfg.get("stack", {}).get("ui") else "",
                 "commands": dict(cfg.get("stack", {}).get("commands") or {})}]
    return [{"name": name, "path": c.get("path", name), "kind": c.get("kind", "other"),
             "commands": dict(c.get("commands") or {})} for name, c in comps.items()]


def build_config(answers: dict[str, Any]) -> dict[str, Any]:
    """Merge wizard answers over defaults into a full config."""
    stages = default_stages()
    for name, overrides in (answers.get("stages") or {}).items():
        if name not in stages:
            raise ConfigError(f"Unknown stage '{name}'. Valid: {', '.join(STAGE_NAMES)}")
        stages[name].update(overrides or {})
    cfg = {
        "version": 1,
        "project": {"name": "my-project", "type": "new", "compliance": [], **(answers.get("project") or {})},
        "stack": {"languages": [], "framework": None, "commands": {}, **(answers.get("stack") or {})},
        "integrations": {"tracker": "local", "vcs": "git", "pull_requests": False, "remote": "origin",
                         "draft_prs": True, "status_checks": True, "hosts": ["claude"],
                         **(answers.get("integrations") or {})},
        "stages": stages,
        "work_types": default_work_types(),
    }
    if answers.get("models"):
        cfg["models"] = answers["models"]
    if answers.get("environments"):
        cfg["environments"] = answers["environments"]
    if answers.get("standards"):
        cfg["standards"] = answers["standards"]
    if answers.get("components"):
        cfg["components"] = {name: {k: v for k, v in c.items() if k in ("path", "kind", "commands")}
                             for name, c in answers["components"].items()}
    validate(cfg)
    return cfg


def validate(cfg: dict[str, Any]) -> None:
    errors = []
    if cfg.get("version") != 1:
        errors.append("version must be 1")
    if cfg.get("project", {}).get("type") not in ("new", "existing"):
        errors.append("project.type must be 'new' or 'existing'")
    cmds = cfg.get("stack", {}).get("commands", {})
    if not isinstance(cmds, dict):
        errors.append("stack.commands must be a mapping like {test: 'pytest'}")
    for name, st in cfg.get("stages", {}).items():
        if name not in STAGE_NAMES:
            errors.append(f"unknown stage '{name}'")
            continue
        if st.get("autonomy") not in AUTONOMY_LEVELS:
            errors.append(f"stages.{name}.autonomy must be one of {', '.join(AUTONOMY_LEVELS)}")
        if not isinstance(st.get("gate", []), list):
            errors.append(f"stages.{name}.gate must be a list of approver roles")
    for name, wt in (cfg.get("work_types") or {}).items():
        if name not in WORK_TYPES:
            errors.append(f"unknown work type '{name}'. Valid: {', '.join(WORK_TYPES)}")
            continue
        wt = wt or {}
        if not set(wt.get("stages", [])) <= set(TOGGLEABLE):
            errors.append(f"work_types.{name}.stages may only list {', '.join(TOGGLEABLE)}")
        if not set(wt.get("gates") or {}) <= set(TOGGLEABLE):
            errors.append(f"work_types.{name}.gates may only set {', '.join(TOGGLEABLE)}")
        if not set(wt.get("design_if_size", [])) <= set(SIZES):
            errors.append(f"work_types.{name}.design_if_size must use {', '.join(SIZES)}")
    for name, s in (cfg.get("standards") or {}).items():
        if not (isinstance(s, str) or (isinstance(s, dict) and isinstance(s.get("path"), str))):
            errors.append(f"standards.{name} must be a file path or {{path: ..., agents: [...]}}")
    for name, c in (cfg.get("components") or {}).items():
        if not isinstance(c, dict) or not isinstance(c.get("commands", {}), dict):
            errors.append(f"components.{name} must have a path and a commands mapping")
        elif c.get("kind", "other") not in KINDS:
            errors.append(f"components.{name}.kind must be one of {', '.join(KINDS)}")
        elif Path(c.get("path", name)).is_absolute() or ".." in Path(c.get("path", name)).parts:
            errors.append(f"components.{name}.path must be a folder inside the repo")
    bad_hosts = set((cfg.get("integrations") or {}).get("hosts") or []) - {"claude", "copilot"}
    if bad_hosts:
        errors.append(f"integrations.hosts: unknown {', '.join(sorted(bad_hosts))} (supported: claude, copilot)")
    models = cfg.get("models") or {}
    if models.get("orchestrator") and not _valid_model(models["orchestrator"]):
        errors.append(f"models.orchestrator must be one of {', '.join(MODEL_ALIASES)} or a claude-* model id")
    for agent, m in (models.get("agents") or {}).items():
        if not _valid_model(m):
            errors.append(f"models.agents.{agent} must be one of {', '.join(MODEL_ALIASES)} or a claude-* model id")
    for name, e in (cfg.get("environments") or {}).items():
        if not isinstance(e, dict):
            errors.append(f"environments.{name} must be a mapping (deploy, rollback, url, smoke)")
        elif not isinstance(e.get("smoke", []), list):
            errors.append(f"environments.{name}.smoke must be a list of commands")
        elif e.get("agent_ok") and name.lower() in ("production", "prod", "live"):
            errors.append(f"environments.{name}: production is always deployed by a person; remove agent_ok")
    dev = cfg.get("stages", {}).get("development", {})
    if not (isinstance(dev.get("parallel_coders", 1), int) and dev.get("parallel_coders", 1) >= 1):
        errors.append("stages.development.parallel_coders must be an integer >= 1")
    if errors:
        raise ConfigError("Invalid config:\n  - " + "\n  - ".join(errors))


def check_version(spec: str, what: str) -> None:
    """Fail clearly when the installed agentic-sdlc does not satisfy a project's `requires` or the org's minimum."""
    from packaging.specifiers import InvalidSpecifier, SpecifierSet
    from packaging.version import Version

    from . import __version__

    try:
        ok = Version(__version__) in SpecifierSet(spec if spec[:1] in "<>=!~" else f">={spec}")
    except InvalidSpecifier as e:
        raise ConfigError(f"{what}: '{spec}' is not a version requirement like '>=0.7'") from e
    if not ok:
        raise ConfigError(f"{what} needs agentic-sdlc {spec}; this machine has {__version__}. "
                          f"Upgrade: `uv tool upgrade agentic-sdlc` (or install the tag your company publishes).")


def load(root: Path) -> dict[str, Any]:
    from . import generate, org

    with open(root / CONFIG_FILE) as f:
        cfg = yaml.safe_load(f) or {}
    if cfg.get("requires"):
        check_version(str(cfg["requires"]), "This repo (sdlc.config.yaml `requires`)")
    try:
        org_path = org.locate(root, cfg)
        if org_path:
            o = org.load(org_path)
            if o.get("min_version"):
                check_version(str(o["min_version"]), f"Company policy ({org_path.name})")
            cfg, _ = org.apply(cfg, o)
    except org.OrgError as e:
        raise ConfigError(str(e)) from e
    generate.ORG_TEMPLATES = Path(cfg["_org"]["templates"]) if cfg.get("_org", {}).get("templates") else None
    validate(cfg)
    from .release import write_guard_file  # keep the hook's production rules in step with the config
    write_guard_file(root, cfg)
    return cfg


def write(root: Path, cfg: dict[str, Any]) -> Path:
    cfg = {k: v for k, v in cfg.items() if k != "_org"}  # the org's effect is applied at load, never saved
    path = root / CONFIG_FILE
    header = "# Agentic SDLC config. Generated by /sdlc-init; safe to edit by hand.\n"
    path.write_text(header + yaml.safe_dump(cfg, sort_keys=False, default_flow_style=None, width=100))
    return path
