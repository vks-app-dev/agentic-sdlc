"""Pull requests on GitHub through the `gh` CLI: one PR per work item, `sdlc/<REQ>` into the base branch.

Agents never call `gh` or `git push` themselves (the hook blocks it); they go through `sdlc pr`, `sdlc pr-review`,
`sdlc pr-comments` and `sdlc pr-reply`. Reviews are posted as COMMENT only: approving and merging are human.
The human and the agents may share one GitHub account, so every agent comment starts with a marker.
SDLC_GH_BIN overrides the binary (tests use a fake).
"""

import json
import os
import subprocess
import tempfile
from pathlib import Path
from typing import Any

REVIEWER = "**[agentic-sdlc · dev-pr-reviewer]**"
RESPONDER = "**[agentic-sdlc · dev-pr-responder]**"
# comments posted before the rename carry the old name; still recognised as the agents' own
OLD_REVIEWER, OLD_RESPONDER = "**[sdlc-pilot · dev-pr-reviewer]**", "**[sdlc-pilot · dev-pr-responder]**"
MARKERS = (REVIEWER, RESPONDER, OLD_REVIEWER, OLD_RESPONDER)


class GitHubError(Exception):
    pass


def gh(root: Path, *args: str, input: str | None = None) -> str:
    binary = os.environ.get("SDLC_GH_BIN", "gh")
    try:
        r = subprocess.run([binary, *args], cwd=root, capture_output=True, text=True, input=input, timeout=120)
    except OSError as e:
        raise GitHubError(f"GitHub CLI not available ({binary}): {e}. Install gh and run `gh auth login`.") from e
    if r.returncode != 0:
        raise GitHubError(f"gh {' '.join(args[:3])} failed: {(r.stderr or r.stdout).strip()[:500]}")
    return r.stdout


def repo_slug(root: Path) -> str:
    return gh(root, "repo", "view", "--json", "nameWithOwner", "-q", ".nameWithOwner").strip()


def find_pr(root: Path, branch: str) -> dict[str, Any] | None:
    prs = json.loads(gh(root, "pr", "list", "--head", branch, "--state", "open", "--json", "number,url,title") or "[]")
    return prs[0] if prs else None


def _body_file(body: str) -> str:
    f = tempfile.NamedTemporaryFile("w", suffix=".md", delete=False)
    f.write(body)
    f.close()
    return f.name


def open_or_update(root: Path, branch: str, base: str, title: str, body: str, labels: list[str],
                   draft: bool = False) -> dict[str, Any]:
    path = _body_file(body)
    try:
        pr = find_pr(root, branch)
        if pr:
            gh(root, "pr", "edit", str(pr["number"]), "--title", title, "--body-file", path)
            created = False
        else:
            url = gh(root, "pr", "create", "--base", base, "--head", branch, "--title", title,
                     "--body-file", path, *(["--draft"] if draft else [])).strip().splitlines()[-1]
            pr = {"number": int(url.rstrip("/").rsplit("/", 1)[-1]), "url": url, "title": title}
            created = True
    finally:
        os.unlink(path)
    for label in labels:  # best effort: a missing permission must not stop the PR
        try:
            gh(root, "label", "create", label, "--color", "5319e7", "--force")
            gh(root, "pr", "edit", str(pr["number"]), "--add-label", label)
        except GitHubError:
            pass
    return {**pr, "created": created}


THREADS = """query($owner: String!, $name: String!, $number: Int!) {
  repository(owner: $owner, name: $name) { pullRequest(number: $number) {
    reviewDecision
    reviewThreads(first: 100) { nodes { id isResolved isOutdated path line
      comments(first: 30) { nodes { databaseId author { login } body createdAt } } } }
    comments(last: 50) { nodes { databaseId author { login } body createdAt } } } } }"""


def comments(root: Path, number: int) -> dict[str, Any]:
    """Unresolved review threads and conversation comments, marking which are agents' and which wait for a reply."""
    owner, name = repo_slug(root).split("/", 1)
    data = json.loads(gh(root, "api", "graphql", "-f", f"query={THREADS}", "-F", f"owner={owner}", "-F", f"name={name}",
                         "-F", f"number={number}"))
    pr = data["data"]["repository"]["pullRequest"]
    threads = []
    for t in pr["reviewThreads"]["nodes"]:
        if t["isResolved"]:
            continue
        cs = t["comments"]["nodes"]
        threads.append({"thread": t["id"], "path": t["path"], "line": t["line"], "outdated": t["isOutdated"],
                        "from_agent": bool(cs) and cs[0]["body"].startswith((REVIEWER, OLD_REVIEWER)),
                        "waiting_for_reply": bool(cs) and not cs[-1]["body"].startswith((RESPONDER, OLD_RESPONDER)),
                        "comments": [{"author": c["author"]["login"] if c["author"] else "", "body": c["body"]}
                                     for c in cs]})
    convo = pr["comments"]["nodes"]
    answered = {str(c["databaseId"]) for r in convo if r["body"].startswith((RESPONDER, OLD_RESPONDER))
                for c in convo if f"#{c['databaseId']}" in r["body"]}
    general = [{"id": c["databaseId"], "author": c["author"]["login"] if c["author"] else "", "body": c["body"],
                "waiting_for_reply": str(c["databaseId"]) not in answered}
               for c in convo if not c["body"].startswith(MARKERS)]
    return {"review_decision": pr["reviewDecision"], "threads": threads, "comments": general}


def post_review(root: Path, number: int, summary: str, items: list[dict[str, Any]]) -> None:
    """A COMMENT review with line comments. Never APPROVE or REQUEST_CHANGES: the decision is human."""
    payload = {"event": "COMMENT", "body": f"{REVIEWER} {summary}".strip(),
               "comments": [{"path": c["path"], "line": int(c["line"]), "side": "RIGHT",
                             "body": f"{REVIEWER} {c['body']}"} for c in items]}
    gh(root, "api", f"repos/{repo_slug(root)}/pulls/{number}/reviews", "--method", "POST", "--input", "-",
       input=json.dumps(payload))


REPLY = """mutation($thread: ID!, $body: String!) {
  addPullRequestReviewThreadReply(input: {pullRequestReviewThreadId: $thread, body: $body}) { comment { id } } }"""
RESOLVE = """mutation($thread: ID!) { resolveReviewThread(input: {threadId: $thread}) { thread { isResolved } } }"""


def reply(root: Path, number: int, target: str, body: str, resolve: bool = False) -> None:
    """Reply to a review thread (id starts with PRRT_) or a conversation comment (numeric id)."""
    text = f"{RESPONDER} {body}"
    if target.isdigit():
        gh(root, "pr", "comment", str(number), "--body", f"{text}\n\n(re #{target})")
        return
    gh(root, "api", "graphql", "-f", f"query={REPLY}", "-F", f"thread={target}", "-F", f"body={text}")
    if resolve:
        gh(root, "api", "graphql", "-f", f"query={RESOLVE}", "-F", f"thread={target}")


# ---- draft state, stage labels and commit statuses ----

STATUS_PREFIX = "Agentic SDLC / "
LABEL_PREFIX = "sdlc: "


def set_draft(root: Path, number: int, draft: bool) -> None:
    gh(root, "pr", "ready", str(number), *(["--undo"] if draft else []))


def set_stage_label(root: Path, number: int, label: str) -> None:
    """One `sdlc: …` label at a time (e.g. `sdlc: qa`, `sdlc: ready to merge`)."""
    current = json.loads(gh(root, "pr", "view", str(number), "--json", "labels").strip() or "{}").get("labels", [])
    for old in (x["name"] for x in current if x["name"].startswith(LABEL_PREFIX) and x["name"] != label):
        gh(root, "pr", "edit", str(number), "--remove-label", old)
    if label not in (x["name"] for x in current):
        gh(root, "label", "create", label, "--color", "0e8a16" if "ready" in label else "c5def5", "--force")
        gh(root, "pr", "edit", str(number), "--add-label", label)


def set_status(root: Path, sha: str, context: str, state: str, description: str, url: str = "") -> None:
    """A commit status (pending | success | failure | error) that branch protection can require."""
    payload = {"state": state, "context": f"{STATUS_PREFIX}{context}", "description": description[:140]}
    if url:
        payload["target_url"] = url
    gh(root, "api", f"repos/{repo_slug(root)}/statuses/{sha}", "--method", "POST", "--input", "-",
       input=json.dumps(payload))


def protect(root: Path, branch: str, contexts: list[str], enforce_admins: bool, reviews: int) -> dict[str, Any]:
    """Require the Agentic SDLC statuses (and any already-required checks) on the base branch."""
    slug = repo_slug(root)
    try:
        current = json.loads(gh(root, "api", f"repos/{slug}/branches/{branch}/protection"))
        existing = (current.get("required_status_checks") or {}).get("contexts") or []
    except GitHubError as e:
        if "Not Found" not in str(e) and "not protected" not in str(e).lower():
            raise
        existing = []
    payload = {"required_status_checks": {"strict": False, "contexts": sorted(set(existing) | set(contexts))},
               "enforce_admins": enforce_admins,
               "required_pull_request_reviews": {"required_approving_review_count": reviews} if reviews else None,
               "restrictions": None}
    gh(root, "api", f"repos/{slug}/branches/{branch}/protection", "--method", "PUT", "--input", "-",
       input=json.dumps(payload))
    return payload


def review_decision(root: Path, number: int) -> str:
    return json.loads(gh(root, "pr", "view", str(number), "--json", "reviewDecision")).get("reviewDecision") or ""
