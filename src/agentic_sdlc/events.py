"""Append-only event log (.sdlc/events.jsonl) that the dashboard tails.

The same line format is written by the engine and by the plugin hooks
(plugin/hooks/emit_event.py), so keep the two in sync with schema/event.schema.json.
"""

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

EVENTS_FILE = Path(".sdlc") / "events.jsonl"


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def emit(root: Path, event: str, *, req: str | None = None, stage: str | None = None,
         task: str | None = None, agent: str | None = None, status: str | None = None,
         detail: str | None = None, source: str = "engine", **extra: Any) -> dict[str, Any]:
    rec = {"ts": now(), "source": source, "event": event, "req": req, "stage": stage,
           "task": task, "agent": agent, "status": status, "detail": detail}
    rec = {k: v for k, v in rec.items() if v is not None}
    rec.update(extra)
    path = root / EVENTS_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    # One write() per line with O_APPEND keeps lines whole when agents emit concurrently.
    fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o644)
    try:
        os.write(fd, (json.dumps(rec, ensure_ascii=False) + "\n").encode())
    finally:
        os.close(fd)
    return rec


def read(root: Path, limit: int | None = None, req: str | None = None) -> list[dict[str, Any]]:
    path = root / EVENTS_FILE
    if not path.exists():
        return []
    out = []
    for line in path.read_text().splitlines():
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        if req is None or rec.get("req") == req:
            out.append(rec)
    return out[-limit:] if limit else out
