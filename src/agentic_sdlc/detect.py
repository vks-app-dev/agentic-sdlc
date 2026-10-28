"""Scan a repo and guess its stack so the setup wizard only asks what it can't detect."""

import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

SKIP_DIRS = {".git", "node_modules", ".venv", "venv", "dist", "build", ".sdlc", "__pycache__", "target"}
SOURCE_EXT = {".py", ".ts", ".tsx", ".js", ".jsx", ".go", ".rs", ".java", ".kt", ".rb", ".cs", ".php", ".swift"}


def _count_source_files(root: Path, limit: int = 500) -> int:
    n = 0
    for p in root.rglob("*"):
        if any(part in SKIP_DIRS for part in p.parts):
            continue
        if p.suffix in SOURCE_EXT:
            n += 1
            if n >= limit:
                break
    return n


def _git_commits(root: Path) -> int:
    try:
        out = subprocess.run(["git", "rev-list", "--count", "HEAD"], cwd=root,
                             capture_output=True, text=True, timeout=10)
        return int(out.stdout.strip()) if out.returncode == 0 else 0
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return 0


def _node(root: Path, found: dict[str, Any]) -> None:
    pkg_file = root / "package.json"
    if not pkg_file.exists():
        return
    pkg = json.loads(pkg_file.read_text() or "{}")
    deps = {**pkg.get("dependencies", {}), **pkg.get("devDependencies", {})}
    ts = "typescript" in deps or (root / "tsconfig.json").exists()
    found["languages"].append("typescript" if ts else "javascript")
    pm = ("pnpm" if (root / "pnpm-lock.yaml").exists() else
          "yarn" if (root / "yarn.lock").exists() else
          "bun" if (root / "bun.lockb").exists() else "npm")
    found["package_manager"] = pm
    run = "npm run" if pm == "npm" else pm
    scripts = pkg.get("scripts", {})
    cmds = found["commands"]
    cmds.setdefault("install", f"{pm} install")
    for key, names in {"test": ["test"], "lint": ["lint"], "typecheck": ["typecheck", "type-check", "tsc"],
                       "build": ["build"], "format": ["format"], "dev": ["dev", "start"]}.items():
        for n in names:
            if n in scripts:
                cmds.setdefault(key, f"{run} {n}")
                break
    if "dev" in cmds:
        cmds.setdefault("run", cmds["dev"])
    if "@vitest/coverage-v8" in deps or "@vitest/coverage-istanbul" in deps:
        cmds.setdefault("coverage", f"{'npx' if pm == 'npm' else pm} vitest run --coverage --coverage.reporter=json-summary")
    elif "jest" in deps:
        cmds.setdefault("coverage", f"{'npx' if pm == 'npm' else pm} jest --coverage --coverageReporters=json-summary")
    npx = "npx" if pm == "npm" else pm
    for n in ("e2e", "test:e2e"):
        if n in scripts:
            cmds.setdefault("e2e", f"{run} {n}")
    if "@playwright/test" in deps or "playwright" in deps:
        cmds.setdefault("e2e", f"{npx} playwright test")
    elif "cypress" in deps:
        cmds.setdefault("e2e", f"{npx} cypress run")
    if ts and "typecheck" not in cmds:
        cmds["typecheck"] = f"{npx} tsc --noEmit"
    if pm in ("npm", "pnpm", "yarn"):
        cmds.setdefault("audit", f"{pm} audit --audit-level=high" + (" --omit=dev" if pm == "npm" else ""))
    for fw in ("next", "@nestjs/core", "express", "fastify", "react", "vue", "svelte", "@angular/core"):
        if fw in deps:
            found["frameworks"].append(fw.split("/")[-1] if fw.startswith("@") else fw)
    for tool in ("jest", "vitest", "playwright", "@playwright/test", "cypress", "mocha"):
        if tool in deps:
            found["test_tools"].append(tool.split("/")[-1] if tool.startswith("@") else tool)


def _python(root: Path, found: dict[str, Any]) -> None:
    pyproject = root / "pyproject.toml"
    reqs = root / "requirements.txt"
    if not (pyproject.exists() or reqs.exists()):
        return
    found["languages"].append("python")
    text = pyproject.read_text() if pyproject.exists() else ""
    if reqs.exists():
        text += reqs.read_text()
    low = text.lower()
    uv = (root / "uv.lock").exists()
    poetry = "[tool.poetry]" in text
    found["package_manager"] = "uv" if uv else "poetry" if poetry else "pip"
    prefix = "uv run " if uv else "poetry run " if poetry else ""
    cmds = found["commands"]
    cmds.setdefault("install", "uv sync" if uv else "poetry install" if poetry else "pip install -r requirements.txt")
    if "pytest" in low or (root / "tests").exists():
        cmds.setdefault("test", f"{prefix}pytest")
        found["test_tools"].append("pytest")
    if "ruff" in low:
        cmds.setdefault("lint", f"{prefix}ruff check .")
        cmds.setdefault("format", f"{prefix}ruff format .")
    if "pytest-cov" in low:
        cmds.setdefault("coverage", f"{prefix}pytest --cov --cov-report=json:coverage.json -q")
    if "mypy" in low:
        cmds.setdefault("typecheck", f"{prefix}mypy .")
    elif "pyright" in low:
        cmds.setdefault("typecheck", f"{prefix}pyright")
    if "bandit" in low:
        cmds.setdefault("sast", f"{prefix}bandit -q -r . -x ./tests,./.venv")
    if "pip-audit" in low:
        cmds.setdefault("audit", f"{prefix}pip-audit")
    for fw in ("fastapi", "django", "flask"):
        if fw in low:
            found["frameworks"].append(fw)
    if "fastapi" in low:
        for mod in ("app/main.py", "src/main.py", "main.py", "app.py"):
            if (root / mod).exists():
                cmds.setdefault("run", f"{prefix}uvicorn {mod[:-3].replace('/', '.')}:app --port 8000")
                break
    elif "django" in low and (root / "manage.py").exists():
        cmds.setdefault("run", f"{prefix}python manage.py runserver")


def _other(root: Path, found: dict[str, Any]) -> None:
    cmds = found["commands"]
    if (root / "go.mod").exists():
        found["languages"].append("go")
        cmds.setdefault("test", "go test ./...")
        cmds.setdefault("lint", "go vet ./...")
        cmds.setdefault("build", "go build ./...")
    if (root / "Cargo.toml").exists():
        found["languages"].append("rust")
        cmds.setdefault("test", "cargo test")
        cmds.setdefault("lint", "cargo clippy")
        cmds.setdefault("build", "cargo build")
    if (root / "pom.xml").exists():
        found["languages"].append("java")
        cmds.setdefault("test", "mvn test")
    elif (root / "build.gradle").exists() or (root / "build.gradle.kts").exists():
        found["languages"].append("java/kotlin")
        cmds.setdefault("test", "./gradlew test")
    if (root / "Gemfile").exists():
        found["languages"].append("ruby")
        cmds.setdefault("test", "bundle exec rspec" if (root / "spec").exists() else "bundle exec rake test")


def _scanners(found: dict[str, Any]) -> None:
    """Security scanners installed on this machine. The built-in secret scan runs whether or not they are."""
    cmds = found["commands"]
    if shutil.which("gitleaks"):
        cmds.setdefault("secrets", "gitleaks detect --no-banner --redact")
    if shutil.which("semgrep"):
        cmds.setdefault("sast", "semgrep scan --config auto --error --quiet")


FRONTEND = {"react", "next", "vue", "nuxt", "svelte", "angular", "solid", "remix", "astro"}
BACKEND = {"express", "nestjs", "fastify", "fastapi", "django", "flask"}
COMPONENT_DIRS = ("apps", "packages", "services")  # monorepo folders whose children are components


def _stack(d: Path) -> dict[str, Any]:
    found: dict[str, Any] = {"languages": [], "frameworks": [], "test_tools": [], "commands": {}, "package_manager": None}
    _node(d, found)
    _python(d, found)
    _other(d, found)
    return found


def _kind(found: dict[str, Any]) -> str:
    fws = set(found["frameworks"])
    if fws & FRONTEND:
        return "frontend"
    if fws & BACKEND or set(found["languages"]) & {"go", "rust", "java", "java/kotlin", "ruby"}:
        return "backend"
    return "other"


def components(root: Path) -> dict[str, dict[str, Any]]:
    """Sub-folders with their own stack (frontend/, backend/, apps/web, services/api ...)."""
    dirs = []
    for d in sorted(root.iterdir()):
        if not d.is_dir() or d.name in SKIP_DIRS or d.name.startswith("."):
            continue
        dirs += sorted(x for x in d.iterdir() if x.is_dir()) if d.name in COMPONENT_DIRS else [d]
    out = {}
    for d in dirs:
        st = _stack(d)
        if st["languages"]:
            rel = d.relative_to(root).as_posix()
            out[rel.replace("/", "-")] = {"path": rel, "kind": _kind(st), "languages": st["languages"],
                                          "frameworks": st["frameworks"], "commands": st["commands"]}
    return out


def detect(root: Path) -> dict[str, Any]:
    found: dict[str, Any] = {"languages": [], "frameworks": [], "test_tools": [], "commands": {},
                             "package_manager": None, "ci": None, "has_claude_md": (root / "CLAUDE.md").exists()}
    found.update(_stack(root))
    subs = components(root)
    if subs and (len(subs) > 1 or not found["languages"]):
        pkg = root / "package.json"
        workspace_only = pkg.exists() and "workspaces" in json.loads(pkg.read_text() or "{}")
        if found["languages"] and not workspace_only:  # the root is an app too (e.g. Django + frontend/)
            subs = {"root": {"path": ".", "kind": _kind(found), "languages": found["languages"],
                             "frameworks": found["frameworks"], "commands": dict(found["commands"])}, **subs}
        found["components"] = subs  # each component keeps its own commands; root commands stay for tools
        for c in subs.values():
            found["languages"] += [x for x in c["languages"] if x not in found["languages"]]
            found["frameworks"] += [x for x in c["frameworks"] if x not in found["frameworks"]]
    _scanners(found)
    if (root / ".github" / "workflows").exists():
        found["ci"] = "github-actions"
    elif (root / ".gitlab-ci.yml").exists():
        found["ci"] = "gitlab-ci"
    elif (root / "Jenkinsfile").exists():
        found["ci"] = "jenkins"
    found["is_git_repo"] = (root / ".git").exists()
    found["git_commits"] = _git_commits(root) if found["is_git_repo"] else 0
    found["source_files"] = _count_source_files(root)
    found["suggested_type"] = "existing" if found["source_files"] > 5 or found["git_commits"] > 10 else "new"
    return found
