"""Isolated test environments: `sdlc env up <name> --port 8101` / `sdlc env down <name>`.

Each QA tester (and the integration verifier) gets its own running copy of the app with its own data, started
and stopped the same way every time instead of by hand. Two ways to define it:

1. `test_env` in sdlc.config.yaml, for anything (docker compose, a script, several services):
       test_env:
         up: docker compose -p sdlc-{name} up -d
         down: docker compose -p sdlc-{name} down -v
         url: http://127.0.0.1:{port}/health
         env: {EXPENSE_DB_PATH: "{data}/expenses.db"}
   Placeholders: {name}, {port}, {data} (a fresh temp folder per environment).
2. Without `test_env`, each component's `run` command is started in the background in its folder, on {port} (a
   `--port N` in the command is rewritten; PORT is also set) and the frontend on {port}+100 with API_URL and
   BACKEND_URL pointing at the backend. `test_env.env` still applies.

`up` waits until the URL answers (any HTTP status below 500). State is kept in .sdlc/run/envs/<name>.json.
"""

import json
import os
import re
import shutil
import signal
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from . import config


class EnvError(Exception):
    pass


def _dir(root: Path) -> Path:
    return root / ".sdlc" / "run" / "envs"


def _fill(text: str, values: dict[str, str]) -> str:
    for k, v in values.items():
        text = text.replace("{" + k + "}", v)
    return text


def _answering(url: str) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=2) as r:
            return r.status < 500
    except urllib.error.HTTPError as e:
        return e.code < 500
    except (urllib.error.URLError, OSError, ValueError):
        return False


def _wait(url: str, timeout: float, procs: list[subprocess.Popen]) -> bool:
    end = time.time() + timeout
    while time.time() < end:
        if _answering(url):
            return True
        if procs and all(p.poll() is not None for p in procs):
            return False  # everything exited
        time.sleep(0.5)
    return False


def up(root: Path, cfg: dict[str, Any], name: str, port: int, timeout: float = 60) -> dict[str, Any]:
    if not re.fullmatch(r"[\w.-]{1,40}", name):
        raise EnvError("Name the environment with letters, digits, dots or dashes, e.g. qa-functional")
    if (_dir(root) / f"{name}.json").exists():
        raise EnvError(f"Environment {name} is already up; `sdlc env down {name}` first")
    spec = cfg.get("test_env") or {}
    data = tempfile.mkdtemp(prefix=f"sdlc-env-{name}-")
    values = {"name": name, "port": str(port), "data": data}
    extra = {k: _fill(str(v), values) for k, v in (spec.get("env") or {}).items()}
    env = {**os.environ, **extra, "PORT": str(port), "SDLC_ENV": name, "SDLC_ENV_DATA_DIR": data}
    record: dict[str, Any] = {"name": name, "port": port, "data": data, "pids": [], "logs": [], "started": time.time()}
    procs: list[subprocess.Popen] = []
    _dir(root).mkdir(parents=True, exist_ok=True)

    def start(cmd: str, cwd: Path, env_: dict[str, str], label: str) -> None:
        log = _dir(root) / f"{name}-{label}.log"
        f = open(log, "w")
        p = subprocess.Popen(cmd, shell=True, cwd=cwd, env=env_, stdout=f, stderr=subprocess.STDOUT,
                             stdin=subprocess.DEVNULL, start_new_session=True)
        procs.append(p)
        record["pids"].append(p.pid)
        record["logs"].append(str(log.relative_to(root)))

    if spec.get("up"):
        r = subprocess.run(_fill(spec["up"], values), shell=True, cwd=root, env=env, capture_output=True, text=True)
        if r.returncode != 0:
            shutil.rmtree(data, ignore_errors=True)
            raise EnvError(f"test_env.up failed: {(r.stderr or r.stdout).strip()[-500:]}")
        url = _fill(spec.get("url") or "http://127.0.0.1:{port}/", values)
    else:
        comps = [c for c in config.components(cfg) if c["commands"].get("run")]
        if not comps:
            shutil.rmtree(data, ignore_errors=True)
            raise EnvError("Nothing to start: add a `run` command (stack.commands or a component) or `test_env.up`")
        back = [c for c in comps if c["kind"] != "frontend"] or comps[:1]
        for i, c in enumerate(back):
            cmd = re.sub(r"--port[ =]\d+", f"--port {port + i}", _fill(c["commands"]["run"], values))
            start(cmd, root / c["path"], {**env, "PORT": str(port + i)}, c["name"] or "app")
        api = f"http://127.0.0.1:{port}"
        for c in (c for c in comps if c not in back):
            fport = port + 100
            cmd = re.sub(r"--port[ =]\d+", f"--port {fport}", _fill(c["commands"]["run"], values))
            start(cmd, root / c["path"], {**env, "PORT": str(fport), "API_URL": api, "BACKEND_URL": api}, c["name"])
            record["frontend_url"] = f"http://127.0.0.1:{fport}/"
        url = _fill(spec.get("url") or "http://127.0.0.1:{port}/", values)
    record["url"] = url
    (_dir(root) / f"{name}.json").write_text(json.dumps(record, indent=2))
    if not _wait(url, float(spec.get("ready_timeout", timeout)), procs):
        logs = "".join((root / lg).read_text(errors="replace")[-400:] for lg in record["logs"])
        down(root, cfg, name)
        raise EnvError(f"{name} did not answer on {url}" + (f":\n{logs.strip()}" if logs.strip() else ""))
    return {k: record[k] for k in ("name", "port", "url", "data", "logs") if k in record} | (
        {"frontend_url": record["frontend_url"]} if "frontend_url" in record else {})


def down(root: Path, cfg: dict[str, Any], name: str) -> dict[str, Any]:
    p = _dir(root) / f"{name}.json"
    if not p.exists():
        raise EnvError(f"No environment {name}; see `sdlc env list`")
    record = json.loads(p.read_text())
    spec = cfg.get("test_env") or {}
    if spec.get("down"):
        subprocess.run(_fill(spec["down"], {"name": name, "port": str(record["port"]), "data": record["data"]}),
                       shell=True, cwd=root, capture_output=True)
    for pid in record["pids"]:
        try:
            os.killpg(pid, signal.SIGTERM)
        except (ProcessLookupError, PermissionError):
            pass
    time.sleep(0.3)
    for pid in record["pids"]:
        try:
            os.killpg(pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass
    shutil.rmtree(record["data"], ignore_errors=True)
    p.unlink()
    return {"name": name, "stopped": len(record["pids"]), "data_removed": record["data"]}


def listing(root: Path) -> list[dict[str, Any]]:
    d = _dir(root)
    return [json.loads(p.read_text()) for p in sorted(d.glob("*.json"))] if d.exists() else []
