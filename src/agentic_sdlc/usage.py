"""AI usage and cost per work item, stage, agent and model.

Source: `usage` events the plugin hook writes when a subagent or the session stops (tokens per model, read from
the Claude Code transcripts). Cost is an **estimate**: tokens × the price table below (Anthropic API list prices,
USD per million tokens, as of 2026-10-06), overridable per repo in `sdlc.config.yaml` → `pricing`. On a Claude
subscription (Pro, Max, Team, Enterprise) usage counts against the plan's limits rather than being billed per
token, so read the dollar figure as "API-equivalent cost". Runs started from the dashboard also record the CLI's
own `total_cost_usd`, shown next to the estimate.
"""

from typing import Any

# USD per million tokens: input, output, cache write (5-minute), cache read
PRICES: dict[str, tuple[float, float, float, float]] = {
    "claude-fable-5-1": (10.00, 50.00, 12.50, 0.25),
    "claude-fable-5": (10.00, 50.00, 12.50, 1.00),
    "claude-opus-5-5": (4.00, 20.00, 5.00, 0.20),
    "claude-opus-5": (5.00, 25.00, 6.25, 0.50),
    "claude-opus-4-8": (5.00, 25.00, 6.25, 0.50),
    "claude-opus-4-7": (5.00, 25.00, 6.25, 0.50),
    "claude-opus-4-6": (5.00, 25.00, 6.25, 0.50),
    "claude-sonnet-5-5": (2.00, 10.00, 2.50, 0.20),
    "claude-sonnet-5": (2.00, 10.00, 2.50, 0.20),
    "claude-sonnet-4-6": (3.00, 15.00, 3.75, 0.30),
    "claude-haiku-5-5": (0.10, 0.50, 0.125, 0.01),
    "claude-haiku-4-5": (1.00, 5.00, 1.25, 0.10),
}
FAMILY = {"fable": "claude-fable-5-1", "opus": "claude-opus-5-5", "sonnet": "claude-sonnet-5-5", "haiku": "claude-haiku-5-5"}
TOKENS = ("input_tokens", "output_tokens", "cache_creation_input_tokens", "cache_read_input_tokens")
STAGE_OF_PREFIX = {"req-": "requirements", "design-": "design", "plan-": "planning", "dev-": "development",
                   "qa-": "qa", "rel-": "release"}


def price(model: str, cfg: dict[str, Any] | None = None) -> tuple[float, float, float, float] | None:
    custom = ((cfg or {}).get("pricing") or {}).get(model)
    if custom:
        return (float(custom.get("input", 0)), float(custom.get("output", 0)),
                float(custom.get("cache_write", custom.get("input", 0) * 1.25)),
                float(custom.get("cache_read", custom.get("input", 0) * 0.1)))
    base = next((m for m in sorted(PRICES, key=len, reverse=True) if model.startswith(m)), None)
    if base is None:
        base = next((v for k, v in FAMILY.items() if k in model), None)
    return PRICES.get(base) if base else None


def cost(model: str, t: dict[str, int], cfg: dict[str, Any] | None = None) -> float | None:
    p = price(model, cfg)
    if p is None:
        return None
    return sum(t.get(k, 0) * rate for k, rate in zip(TOKENS, p)) / 1_000_000


def stage_of(e: dict[str, Any]) -> str:
    if e.get("stage"):
        return e["stage"]
    agent = e.get("agent") or ""
    return next((s for p, s in STAGE_OF_PREFIX.items() if agent.startswith(p)), "orchestration")


def _empty() -> dict[str, Any]:
    return {**{k: 0 for k in TOKENS}, "tokens": 0, "cost_usd": 0.0, "unpriced_tokens": 0}


def _add(bucket: dict[str, Any], model: str, e: dict[str, Any], cfg: dict[str, Any] | None) -> None:
    t = {k: int(e.get(k) or 0) for k in TOKENS}
    for k in TOKENS:
        bucket[k] += t[k]
    n = sum(t.values())
    bucket["tokens"] += n
    c = cost(model, t, cfg)
    if c is None:
        bucket["unpriced_tokens"] += n
    else:
        bucket["cost_usd"] += c


def summarize(evs: list[dict[str, Any]], cfg: dict[str, Any] | None = None, req: str | None = None) -> dict[str, Any]:
    total = _empty()
    by: dict[str, dict[str, dict[str, Any]]] = {"item": {}, "stage": {}, "agent": {}, "model": {}}
    for e in evs:
        if e.get("event") != "usage" or (req and e.get("req") != req):
            continue
        model = e.get("model") or "unknown"
        _add(total, model, e, cfg)
        for dim, key in (("item", e.get("req") or "(none)"), ("stage", stage_of(e)),
                         ("agent", e.get("agent") or "orchestrator"), ("model", model)):
            _add(by[dim].setdefault(key, _empty()), model, e, cfg)
    actual = sum(float(e.get("cost_usd") or 0) for e in evs
                 if e.get("event") == "run_finished" and (not req or e.get("req") == req))

    def rows(d: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
        return [{"name": k, **{x: round(v, 4) if x == "cost_usd" else v for x, v in b.items()}}
                for k, b in sorted(d.items(), key=lambda kv: (-kv[1]["cost_usd"], -kv[1]["tokens"]))]

    return {"total": {**total, "cost_usd": round(total["cost_usd"], 4)}, "runs_actual_usd": round(actual, 4),
            **{f"by_{dim}": rows(d) for dim, d in by.items()},
            "note": "Estimated: tokens × API list prices (sdlc.config.yaml → pricing). On a subscription this is "
                    "API-equivalent cost; usage counts against plan limits."}


def human_tokens(n: int) -> str:
    return f"{n / 1_000_000:.1f}M" if n >= 1_000_000 else f"{n / 1000:.0f}K" if n >= 1000 else str(n)
