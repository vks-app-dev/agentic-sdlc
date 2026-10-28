"""Git steps the engine does itself, so agents never have to ask about them.

`prepare()` puts the repo on the work-item branch `sdlc/<REQ>` and commits the SDLC files earlier
stages produced (spec, ADR, plan, reviews), so coder worktrees branched from it can read them.
It only ever commits SDLC-managed paths; anything else uncommitted stops it with a clear message.
"""

import re
import subprocess
from pathlib import Path

# Files Agentic SDLC writes. Only these are committed automatically.
MANAGED = ("specs/", "docs/adr/", "docs/design/", "docs/plan/", "docs/dev/", "docs/qa/", "docs/release/", "CHANGELOG.md", "docs/architecture/", "docs/rca/", "docs/product/", "docs/rtm/", ".sdlc/", "sdlc.config.yaml", "CLAUDE.md", ".claude/settings.json",
           ".github/pull_request_template.md", ".github/agents/", ".github/prompts/", "AGENTS.md", ".vscode/mcp.json")
# Runtime files that must never be committed (rewritten by every `sdlc` call).
UNTRACKED = (".sdlc/state", ".sdlc/run", ".sdlc/events.jsonl", ".sdlc/library-history")


class GitError(Exception):
    pass


def git(root: Path, *args: str, check: bool = True) -> str:
    r = subprocess.run(["git", *args], cwd=root, capture_output=True, text=True)
    if check and r.returncode != 0:
        raise GitError(f"git {' '.join(args)} failed: {(r.stderr or r.stdout).strip()}")
    return r.stdout


# Claude Code keeps each agent's private working copy here; never part of the project's changes.
AGENT_WORKTREES = ".claude/worktrees/"


def dirty_paths(root: Path) -> list[str]:
    """Changed, staged and untracked files (respecting .gitignore), as repo-relative paths. Agents' own working
    copies (.claude/worktrees/) are not changes to the project."""
    out = git(root, "status", "--porcelain=v1", "-z", "-uall")
    paths, parts, i = [], out.split("\0"), 0
    while i < len(parts):
        entry = parts[i]
        if len(entry) > 3 and not entry[3:].startswith(AGENT_WORKTREES):
            paths.append(entry[3:])
            if entry[0] in "RC":  # rename/copy: the next field is the old path
                i += 1
        i += 1
    return paths


def managed(path: str) -> bool:
    return any(path == m or path.startswith(m) for m in MANAGED)


def ensure_runtime_ignored(root: Path) -> list[str]:
    """Make sure runtime files are git-ignored, and untrack them if an older setup committed them."""
    gi = root / ".sdlc" / ".gitignore"
    gi.parent.mkdir(exist_ok=True)
    lines = gi.read_text().splitlines() if gi.exists() else []
    for entry in ("events.jsonl", "state/", "run/", "library-history/"):
        if entry not in lines:
            lines.append(entry)
    gi.write_text("\n".join(lines) + "\n")
    exclude = root / ".git" / "info" / "exclude"  # local only: agents' working copies never show as changes
    if exclude.parent.is_dir() and AGENT_WORKTREES not in (exclude.read_text() if exclude.exists() else ""):
        with open(exclude, "a") as f:
            f.write(f"\n{AGENT_WORKTREES}\n")
    tracked = [p for p in git(root, "ls-files", "--", *UNTRACKED).splitlines() if p]
    if tracked:
        git(root, "rm", "-r", "--cached", "--quiet", "--ignore-unmatch", "--", *UNTRACKED)
    return tracked


def prepare(root: Path, req: str) -> dict:
    if not (root / ".git").exists():
        raise GitError("This repo has no git. Run `git init` and make a first commit, then run the agents again.")
    if not git(root, "rev-parse", "--verify", "HEAD", check=False).strip():
        raise GitError("The repo has no commits yet. Make a first commit, then run the agents again.")

    untracked_now = ensure_runtime_ignored(root)
    dirty = dirty_paths(root)
    foreign = [p for p in dirty if not managed(p)]
    if foreign:
        shown = ", ".join(foreign[:8]) + (f" and {len(foreign) - 8} more" if len(foreign) > 8 else "")
        raise GitError(f"Uncommitted changes outside SDLC files: {shown}. Commit or stash them, "
                       f"then run the agents again. SDLC files (specs, ADRs, plans) are committed automatically.")

    branch = f"sdlc/{req}"
    current = git(root, "rev-parse", "--abbrev-ref", "HEAD").strip()
    created = False
    if current != branch:
        exists = bool(git(root, "rev-parse", "--verify", "--quiet", f"refs/heads/{branch}", check=False).strip())
        git(root, "checkout", *([] if exists else ["-b"]), branch)  # uncommitted SDLC files come along
        created = not exists

    committed = commit_managed(root, req, "SDLC artifacts (spec, design, plan)", force=bool(untracked_now))
    return {"branch": branch, "from": current, "created": created, "committed": committed,
            "head": git(root, "rev-parse", "--short", "HEAD").strip()}


def commit_message(req: str, subject: str, task: str | None = None, refs: list[str] | tuple = (),
                   body: str = "", by: str = "agentic-sdlc") -> str:
    """The commit convention: `REQ-001/T3: subject`, a body, and machine-readable trailers."""
    head = f"{req}/{task}: {subject}" if task else f"{req}: {subject}"
    trailers = [f"Work-Item: {req}", *([f"Task: {task}"] if task else []),
                *([f"Refs: {', '.join(refs)}"] if refs else []), f"Generated-By: {by}"]
    return head + (f"\n\n{body.strip()}" if body.strip() else "") + "\n\n" + "\n".join(trailers) + "\n"


def commit_managed(root: Path, req: str, subject: str, force: bool = False) -> list[str]:
    """Commit the SDLC-managed files that changed (specs, design, plan, evidence) with the convention."""
    if not (force or any(managed(p) for p in dirty_paths(root))):
        return []
    git(root, "add", "-A", "--", *_existing_managed(root))
    staged = git(root, "diff", "--cached", "--name-only").split()
    if staged:
        git(root, "commit", "-q", "-m", commit_message(req, subject))
    return staged


def push(root: Path, branch: str, remote: str = "origin") -> str:
    """Push a work-item branch. Never a base branch: those change only through reviewed, human-merged PRs."""
    if not branch.startswith("sdlc/"):
        raise GitError(f"Only work-item branches (sdlc/...) are pushed by Agentic SDLC, not '{branch}'")
    if remote not in git(root, "remote").split():
        raise GitError(f"No git remote '{remote}'. Add one (e.g. `gh repo create --private --source . --remote {remote}`).")
    git(root, "push", "-q", "-u", remote, branch)
    return git(root, "rev-parse", "--short", branch).strip()


def tag(root: Path, version: str, ref: str, message: str, remote: str = "origin") -> str:
    """Tag a released commit and push the tag. Called only from `sdlc deployed`, which a person runs."""
    if not re.fullmatch(r"v?\d+\.\d+\.\d+[\w.+-]*", version):
        raise GitError(f"'{version}' is not a semantic version like v1.2.0")
    git(root, "tag", "-a", version, ref, "-m", message)
    if remote in git(root, "remote").split():
        git(root, "push", "-q", remote, f"refs/tags/{version}")
    return git(root, "rev-parse", "--short", f"{version}^{{commit}}").strip()


def _existing_managed(root: Path) -> list[str]:
    return [m.rstrip("/") for m in MANAGED if (root / m.rstrip("/")).exists()]
