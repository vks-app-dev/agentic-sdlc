import json
import subprocess
from pathlib import Path

import pytest

from agentic_sdlc import config, events, gitops, prbody, state
from agentic_sdlc.cli import main

FAKE_GH = '''#!/usr/bin/env python3
import json, os, sys
store = os.environ["FAKE_GH_STATE"]
s = json.load(open(store)) if os.path.exists(store) else {{"prs": [], "calls": [], "threads": []}}
a = sys.argv[1:]
s["calls"].append(a)
def arg(flag):
    return a[a.index(flag) + 1]
out = ""
if a[:2] == ["repo", "view"]:
    out = "acme/shop"
elif a[:2] == ["pr", "list"]:
    out = json.dumps([p for p in s["prs"] if p["head"] == arg("--head")])
elif a[:2] == ["pr", "create"]:
    n = 7 + len(s["prs"])
    s["prs"].append({{"number": n, "url": f"https://github.com/acme/shop/pull/{{n}}", "title": arg("--title"),
                     "head": arg("--head"), "base": arg("--base"), "body": open(arg("--body-file")).read(),
                     "draft": "--draft" in a}})
    out = s["prs"][-1]["url"]
elif a[:2] == ["pr", "edit"] and "--body-file" in a:
    pr = next(p for p in s["prs"] if p["number"] == int(a[2]))
    pr["body"] = open(arg("--body-file")).read()
elif a[:2] == ["api", "graphql"]:
    q = next(x for x in a if x.startswith("query="))
    if "reviewThreads" in q:
        out = json.dumps({{"data": {{"repository": {{"pullRequest": {{"reviewDecision": None,
              "reviewThreads": {{"nodes": s["threads"]}}, "comments": {{"nodes": s.get("comments", [])}}}}}}}}}})
    else:
        s.setdefault("mutations", []).append(a)
        out = "{{}}"
elif a[0] == "api" and a[1].endswith("/reviews"):
    s.setdefault("reviews", []).append(json.loads(sys.stdin.read()))
elif a[:2] == ["pr", "comment"]:
    s.setdefault("pr_comments", []).append(arg("--body"))
elif a[:2] == ["pr", "ready"]:
    s["prs"][0]["draft"] = "--undo" in a
elif a[:2] == ["pr", "view"] and "labels" in a:
    out = json.dumps({{"labels": [{{"name": n}} for n in s["prs"][0].get("labels", [])]}})
elif a[:2] == ["pr", "edit"] and ("--add-label" in a or "--remove-label" in a):
    labels = s["prs"][0].setdefault("labels", [])
    if "--add-label" in a:
        labels.append(arg("--add-label"))
    else:
        labels.remove(arg("--remove-label"))
elif a[0] == "api" and "/statuses/" in a[1]:
    s.setdefault("statuses", []).append({{"sha": a[1].rsplit("/", 1)[-1], **json.loads(sys.stdin.read())}})
elif a[0] == "api" and a[1].endswith("/protection"):
    if "PUT" in a:
        s["protection"] = json.loads(sys.stdin.read())
    elif "protection" in s:
        out = json.dumps(s["protection"])
    else:
        print("gh: Branch not protected (HTTP 404)", file=sys.stderr)
        json.dump(s, open(store, "w"))
        sys.exit(1)
json.dump(s, open(store, "w"))
print(out)
'''


def git(root: Path, *args: str) -> str:
    return subprocess.run(["git", "-c", "core.hooksPath=/dev/null", "-c", "user.email=a@b", "-c", "user.name=a", *args],
                          cwd=root, check=True, capture_output=True, text=True).stdout


@pytest.fixture
def repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    monkeypatch.chdir(root)
    fake = tmp_path / "gh"
    fake.write_text(FAKE_GH.format())
    fake.chmod(0o755)
    monkeypatch.setenv("SDLC_GH_BIN", str(fake))
    monkeypatch.setenv("FAKE_GH_STATE", str(tmp_path / "gh.json"))
    answers = {"project": {"name": "shop", "type": "new"}, "integrations": {"pull_requests": True},
               "stages": {s: {"enabled": False} for s in ("design", "planning")}}
    (root / "answers.json").write_text(json.dumps(answers))
    assert main(["init", "--answers", "answers.json"]) == 0
    git(root, "init", "-q", "-b", "main")
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "init")
    subprocess.run(["git", "init", "-q", "--bare", str(tmp_path / "origin.git")], check=True)
    git(root, "remote", "add", "origin", str(tmp_path / "origin.git"))
    assert main(["new", "Refunds"]) == 0
    c = config.load(root)
    state.start_stage(root, "REQ-001")
    state.complete_stage(root, "REQ-001", c)
    state.approve(root, "REQ-001", c, "lead")
    assert main(["prepare", "REQ-001"]) == 0
    state.start_stage(root, "REQ-001")
    (root / "refunds.py").write_text("def refund():\n    return 1\n")
    git(root, "add", "refunds.py")
    git(root, "commit", "-q", "-m", gitops.commit_message("REQ-001", "refund service", "T1", ["AC-1"], by="dev-coder"))
    state.update_task(root, "REQ-001", c, "T1", "start")
    state.update_task(root, "REQ-001", c, "T1", "done")
    state.record_checks(root, "REQ-001", {"ts": events.now(), "head": "x", "passed": True, "results": []})
    return root


def fake(tmp: Path) -> dict:
    return json.loads((tmp.parent / "gh.json").read_text())


def test_commit_convention() -> None:
    msg = gitops.commit_message("REQ-001", "refund service", "T3", ["AC-2", "SEC-1"], "Adds the service.", "dev-coder")
    assert msg == ("REQ-001/T3: refund service\n\nAdds the service.\n\n"
                   "Work-Item: REQ-001\nTask: T3\nRefs: AC-2, SEC-1\nGenerated-By: dev-coder\n")


def test_pr_is_pushed_opened_from_template_and_required(repo: Path, capsys: pytest.CaptureFixture) -> None:
    with pytest.raises(state.StateError, match="open the pull request first"):
        main_complete(repo)
    capsys.readouterr()
    assert main(["pr", "REQ-001"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["created"] and out["number"] == 7
    pr = fake(repo)["prs"][0]
    assert pr["title"] == "REQ-001: Refunds" and pr["head"] == "sdlc/REQ-001" and pr["base"] == "main"
    assert "## REQ-001: Refunds" in pr["body"] and "### Reviewer checklist" in pr["body"] and "{" not in pr["body"]
    assert "- T1: Refunds" in pr["body"] and "passed at `x`" in pr["body"]
    remote = subprocess.run(["git", "ls-remote", "origin"], cwd=repo, capture_output=True, text=True).stdout
    assert "refs/heads/sdlc/REQ-001" in remote and "refs/heads/main" not in remote  # only the work-item branch
    log = git(repo, "log", "-1", "--format=%B")
    assert log.startswith("REQ-001: evidence for review") and "Work-Item: REQ-001" in log  # handover committed
    assert state.load(repo, "REQ-001")["pr"]["number"] == 7
    main_complete(repo)  # PR is current: completes
    (repo / "more.py").write_text("x = 2\n")
    git(repo, "add", "more.py")
    git(repo, "commit", "-q", "-m", "REQ-001/T1: more")
    state.reject(repo, "REQ-001", "ana", "one more change")
    with pytest.raises(state.StateError, match="commits the PR does not have"):
        main_complete(repo)
    assert main(["pr", "REQ-001"]) == 0  # updates the same PR
    assert len(fake(repo)["prs"]) == 1  # the same PR was updated, not a second one opened
    main_complete(repo)


def main_complete(root: Path) -> None:
    from agentic_sdlc.cli import _require_pr_current
    _require_pr_current(root, config.load(root), "REQ-001")
    state.complete_stage(root, "REQ-001", config.load(root))


def test_review_comments_and_replies(repo: Path, capsys: pytest.CaptureFixture) -> None:
    assert main(["pr", "REQ-001"]) == 0
    review = repo / "review.json"
    review.write_text(json.dumps({"summary": "Two issues.", "comments": [
        {"path": "refunds.py", "line": 2, "body": "[blocking] returns a constant"}]}))
    assert main(["pr-review", "REQ-001", "--file", str(review)]) == 0
    posted = fake(repo)["reviews"][0]
    assert posted["event"] == "COMMENT" and posted["comments"][0]["body"].startswith("**[agentic-sdlc · dev-pr-reviewer]**")
    s = fake(repo)
    s["threads"] = [
        {"id": "PRRT_agent", "isResolved": False, "isOutdated": False, "path": "refunds.py", "line": 2,
         "comments": {"nodes": [{"databaseId": 1, "author": {"login": "bot"}, "body": posted["comments"][0]["body"],
                                 "createdAt": "t"}]}},
        {"id": "PRRT_human", "isResolved": False, "isOutdated": False, "path": "refunds.py", "line": 1,
         "comments": {"nodes": [{"databaseId": 2, "author": {"login": "ana"}, "body": "rename this", "createdAt": "t"}]}},
        {"id": "PRRT_done", "isResolved": True, "isOutdated": False, "path": "x", "line": 1, "comments": {"nodes": []}}]
    s["comments"] = [{"databaseId": 9, "author": {"login": "ana"}, "body": "Please add a test", "createdAt": "t"}]
    (repo.parent / "gh.json").write_text(json.dumps(s))
    capsys.readouterr()
    assert main(["pr-comments", "REQ-001"]) == 0
    got = json.loads(capsys.readouterr().out)
    assert [(t["thread"], t["from_agent"], t["waiting_for_reply"]) for t in got["threads"]] == [
        ("PRRT_agent", True, True), ("PRRT_human", False, True)]
    assert got["comments"][0]["id"] == 9 and got["comments"][0]["waiting_for_reply"]
    assert main(["pr-reply", "REQ-001", "PRRT_human", "-m", "Renamed", "--resolve"]) == 1  # a person's thread
    assert main(["pr-reply", "REQ-001", "PRRT_agent", "-m", "Fixed in abc", "--resolve"]) == 0
    assert main(["pr-reply", "REQ-001", "9", "-m", "Added test_refund"]) == 0
    muts = fake(repo)["mutations"]
    assert any("resolveReviewThread" in " ".join(m) for m in muts)
    assert fake(repo)["pr_comments"][0].startswith("**[agentic-sdlc · dev-pr-responder]**") and "(re #9)" in fake(repo)["pr_comments"][0]


def test_repo_with_its_own_pr_template_keeps_it(repo: Path) -> None:
    (repo / ".github" / "pull_request_template.md").write_text("## Ticket\n\n## Screenshots\n")
    body = prbody.body(repo, "REQ-001", config.load(repo))
    assert body.startswith("## Ticket\n\n## Screenshots") and "<summary>Agentic SDLC: REQ-001 evidence</summary>" in body
    assert "**Checks:** passed" in body


def latest(repo: Path) -> dict:
    """The last status posted per context, for the PR's current head."""
    s = fake(repo)
    head = git(repo, "rev-parse", "sdlc/REQ-001").strip()
    out = {}
    for st in s.get("statuses", []):
        if st["sha"] == head:
            out[st["context"].removeprefix("Agentic SDLC / ")] = st["state"]
    return out


def advance(repo: Path, stage: str) -> None:
    """Finish the current stage and approve it as a person, through the CLI (which syncs the PR)."""
    c = config.load(repo)
    assert state.load(repo, "REQ-001")["current_stage"] == stage
    state.complete_stage(repo, "REQ-001", c)
    assert main(["approve", "REQ-001", "ok"]) == 0


def test_draft_pr_statuses_follow_the_gates(repo: Path, capsys: pytest.CaptureFixture) -> None:
    capsys.readouterr()
    assert main(["pr", "REQ-001"]) == 0
    out = json.loads(capsys.readouterr().out)
    pr = fake(repo)["prs"][0]
    assert pr["draft"] and out["draft"] and out["label"] == "sdlc: development"
    assert latest(repo) == {"QA": "pending", "Release": "pending"}
    advance(repo, "development")
    assert fake(repo)["prs"][0]["labels"][-1] == "sdlc: qa"  # one stage label at a time
    assert "sdlc: development" not in fake(repo)["prs"][0]["labels"]
    state.start_stage(repo, "REQ-001")
    state.add_defect(repo, "REQ-001", "Over-refund accepted", "high", True, ["AC-1"], "qa-functional-tester")
    assert main(["test-report", "REQ-001"]) == 0
    assert latest(repo)["QA"] == "failure"  # a release blocker shows red
    state.update_defect(repo, "REQ-001", "D1", "duplicate", of="D1")  # (resolved for the test)
    state.record_checks(repo, "REQ-001", {"ts": events.now(), "head": "x", "passed": True, "results": []})
    advance(repo, "qa")
    assert latest(repo) == {"QA": "success", "Release": "pending"} and fake(repo)["prs"][0]["draft"]

    # a document-only commit after QA keeps QA green; a code change sets it back to pending
    (repo / "docs" / "release").mkdir(parents=True, exist_ok=True)
    (repo / "docs" / "release" / "notes.md").write_text("notes\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "REQ-001: release notes")
    assert main(["pr", "REQ-001"]) == 0
    assert latest(repo)["QA"] == "success"
    (repo / "refunds.py").write_text("def refund():\n    return 2\n")
    git(repo, "commit", "-qam", "REQ-001/T1: late fix")
    assert main(["pr", "REQ-001"]) == 0
    assert latest(repo)["QA"] == "pending"
    (repo / "refunds.py").write_text("def refund():\n    return 1\n")  # back to the approved code
    git(repo, "commit", "-qam", "REQ-001/T1: undo late fix")
    assert main(["pr", "REQ-001"]) == 0
    assert latest(repo)["QA"] == "success"

    assert main(["start", "REQ-001"]) == 0  # Release: creates the release pack
    for name in ("readiness", "rollback", "verification"):
        p = repo / "docs" / "release" / "REQ-001" / f"{name}.md"
        p.write_text("Verdict: PASS\n" + p.read_text().split("\n", 1)[1])
    notes = repo / "docs" / "release" / "REQ-001" / "release-notes.md"
    notes.write_text(notes.read_text().replace("Version: vX.Y.Z", "Version: v0.1.0"))
    advance(repo, "release")
    assert latest(repo) == {"QA": "success", "Release": "success"}
    pr = fake(repo)["prs"][0]
    assert not pr["draft"] and pr["labels"][-1] == "sdlc: ready to merge"


def test_skipped_gates_are_green(repo: Path) -> None:
    c = config.load(repo)
    c["stages"]["qa"]["enabled"] = False
    config.write(repo, c)
    assert main(["pr", "REQ-001"]) == 0
    state.load(repo, "REQ-001")  # QA stays as created; a skipped stage reports success
    from agentic_sdlc import prsync
    item = state.load(repo, "REQ-001")
    item["stages"]["qa"]["status"] = "skipped"
    assert prsync.statuses(repo, item, "abc")["QA"] == ("success", "QA stage is off for this item")


def test_branch_protection_is_set_by_a_person(repo: Path, monkeypatch: pytest.MonkeyPatch,
                                              capsys: pytest.CaptureFixture) -> None:
    s = fake_state = {"prs": [], "calls": [], "threads": [],
                      "protection": {"required_status_checks": {"contexts": ["ci / test"]}}}
    (repo.parent / "gh.json").write_text(json.dumps(fake_state))
    capsys.readouterr()
    assert main(["github", "protect"]) == 0
    p = fake(repo)["protection"]
    assert p["required_status_checks"]["contexts"] == ["Agentic SDLC / QA", "Agentic SDLC / Release", "ci / test"]
    assert p["enforce_admins"] is True and p["required_pull_request_reviews"] is None
    assert main(["github", "protect", "--no-enforce-admins", "--reviews", "1"]) == 0
    p = fake(repo)["protection"]
    assert p["enforce_admins"] is False and p["required_pull_request_reviews"] == {"required_approving_review_count": 1}
    from agentic_sdlc import human
    monkeypatch.setattr(human, "interactive", lambda: False)
    assert main(["github", "protect"]) == 1  # agents and scripts cannot change repository settings
    assert s is fake_state
