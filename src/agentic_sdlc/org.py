"""Company policy across projects: org.yaml, between Agentic SDLC's defaults and each project's sdlc.config.yaml.

    package defaults  <  org defaults  <  the project's config  <  org policy

`defaults` are where a new project starts and may be changed. `policy` is enforced on the effective config every
time it is loaded: a project may tighten it (more gates, more checks) but never loosen it. What policy changed is
reported by `sdlc doctor`. A project finds its org file through `org:` in sdlc.config.yaml (a path, relative to the
repo) or the SDLC_ORG_CONFIG environment variable. Templates in the org's `templates/` folder sit between the
packaged defaults and the project's own copies.

    version: 1
    name: Acme
    min_version: "0.7.0"                 # oldest agentic-sdlc the company supports
    defaults: {stages: {qa: {gate: [qa-lead]}}, integrations: {pull_requests: true}}
    policy:
      required_stages: [requirements, design, planning, development, qa, release]
      min_gates: {requirements: [pm], release: [release-manager]}
      compliance: [soc2]
      require: {lint: true, plan_check: true, checks: true}
      required_commands: [secrets, audit]
      max_model: sonnet                  # haiku < sonnet < opus
      human_only_envs: [production, eu-prod]
    templates: templates                 # folder next to org.yaml
    projects: [../payments-api, ../web]   # for `sdlc portfolio`
"""

import copy
import os
from pathlib import Path
from typing import Any

import yaml

from .stages import STAGE_NAMES

TIERS = ("haiku", "sonnet", "opus")
POLICY_KEYS = {"required_stages", "min_gates", "compliance", "require", "required_commands", "max_model",
               "human_only_envs", "pull_requests"}


class OrgError(Exception):
    pass


def locate(root: Path, raw: dict[str, Any]) -> Path | None:
    ref = raw.get("org") or os.environ.get("SDLC_ORG_CONFIG")
    if not ref:
        return None
    p = Path(ref).expanduser()
    p = p if p.is_absolute() else (root / p)
    p = p / "org.yaml" if p.is_dir() else p
    if not p.exists():
        raise OrgError(f"org config not found: {p} (from `org:` in sdlc.config.yaml or SDLC_ORG_CONFIG)")
    return p.resolve()


def load(path: Path) -> dict[str, Any]:
    org = yaml.safe_load(path.read_text()) or {}
    errors = []
    if org.get("version") != 1:
        errors.append("version must be 1")
    unknown = set(org.get("policy") or {}) - POLICY_KEYS
    if unknown:
        errors.append(f"unknown policy keys: {', '.join(sorted(unknown))}")
    pol = org.get("policy") or {}
    bad = set(pol.get("required_stages") or []) - set(STAGE_NAMES)
    if bad:
        errors.append(f"policy.required_stages: unknown {', '.join(sorted(bad))}")
    if pol.get("max_model") and pol["max_model"] not in TIERS:
        errors.append(f"policy.max_model must be one of {', '.join(TIERS)}")
    if errors:
        raise OrgError(f"Invalid {path}:\n  - " + "\n  - ".join(errors))
    org["_path"] = str(path)
    return org


def deep_merge(base: dict[str, Any], over: dict[str, Any]) -> dict[str, Any]:
    out = copy.deepcopy(base)
    for k, v in over.items():
        out[k] = deep_merge(out[k], v) if isinstance(v, dict) and isinstance(out.get(k), dict) else copy.deepcopy(v)
    return out


def _clamp(model: str, max_model: str) -> str:
    if model in TIERS and TIERS.index(model) > TIERS.index(max_model):
        return max_model
    return model


def apply(cfg: dict[str, Any], org: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    """The effective config: org defaults under the project, then org policy on top. Returns (cfg, notes)."""
    eff = deep_merge(org.get("defaults") or {}, cfg)
    pol = org.get("policy") or {}
    notes = []
    from .config import default_stages  # late: config imports this module when loading

    stages = eff.setdefault("stages", {})
    defaults = default_stages()
    for s in pol.get("required_stages") or []:
        st = stages.setdefault(s, copy.deepcopy(defaults[s]))  # a stage older configs never mention
        for k, v in defaults[s].items():
            st.setdefault(k, copy.deepcopy(v))
        if st.get("enabled") is False:
            notes.append(f"stages.{s} turned back on (required by {org.get('name', 'the org')})")
        st["enabled"] = True
    for s, roles in (pol.get("min_gates") or {}).items():
        gate = stages.setdefault(s, copy.deepcopy(defaults[s])).setdefault("gate", [])
        missing = [r for r in roles if r not in gate]
        if missing:
            gate.extend(missing)
            notes.append(f"stages.{s}.gate adds {', '.join(missing)}")
    if pol.get("compliance"):
        comp = eff.setdefault("project", {}).setdefault("compliance", [])
        add = [c for c in pol["compliance"] if c not in comp]
        comp.extend(add)
        if add:
            notes.append(f"project.compliance adds {', '.join(add)}")
    flags = {"lint": ("requirements", "require_lint"), "plan_check": ("planning", "require_plan_check"),
             "checks": ("development", "require_checks")}
    for key, on in (pol.get("require") or {}).items():
        if on and key in flags:
            stage, flag = flags[key]
            if stages.setdefault(stage, {}).get(flag) is False:
                notes.append(f"stages.{stage}.{flag} forced on")
            stages[stage][flag] = True
    if pol.get("pull_requests"):
        integ = eff.setdefault("integrations", {})
        if not integ.get("pull_requests"):
            notes.append("integrations.pull_requests forced on")
        integ["pull_requests"] = True
    if pol.get("max_model"):
        models = eff.setdefault("models", {})
        for name, m in list((models.get("agents") or {}).items()):
            if _clamp(m, pol["max_model"]) != m:
                models["agents"][name] = pol["max_model"]
                notes.append(f"models.agents.{name}: {m} → {pol['max_model']} (max_model)")
        models["max"] = pol["max_model"]
    for env in pol.get("human_only_envs") or []:
        e = eff.setdefault("environments", {}).get(env)
        if e is not None:
            if e.get("agent_ok"):
                notes.append(f"environments.{env}.agent_ok removed (human-only by policy)")
            e.pop("agent_ok", None)
            e["human_only"] = True
    eff["_org"] = {"name": org.get("name", ""), "path": org["_path"], "notes": notes,
                   "required_commands": pol.get("required_commands") or [], "min_version": org.get("min_version"),
                   "templates": str((Path(org["_path"]).parent / org["templates"]).resolve())
                   if org.get("templates") else None,
                   "projects": [str((Path(org["_path"]).parent / p).resolve()) for p in org.get("projects") or []]}
    return eff, notes


def effective_model(cfg: dict[str, Any], model: str) -> str:
    """Respect the org's max_model for defaults too (opus agents run on sonnet when max_model is sonnet)."""
    cap = (cfg.get("models") or {}).get("max")
    return _clamp(model, cap) if cap else model
