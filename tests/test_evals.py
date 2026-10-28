import re
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

EVALS = Path(__file__).parents[1] / "plugin" / "evals"
CASES = sorted(p for p in EVALS.iterdir() if (p / "prompt.md").exists())
GRADERS = {"regex", "tool_used", "tool_order", "file_exists", "llm", "baseline"}


def front(path: Path) -> dict:
    return yaml.safe_load(path.read_text().split("---", 2)[1])


def test_cases_are_well_formed() -> None:
    assert len(CASES) >= 6
    for case in CASES:
        meta = front(case / "prompt.md")
        assert meta["name"] == case.name and meta["tags"] and meta["allowed_tools"]
        assert yaml.safe_load((case / "case.yaml").read_text())["schema_version"] == "1.1"
        graders = list((case / "graders").glob("*.md"))
        assert graders, case.name
        for g in graders:
            assert front(g)["type"] in GRADERS, g
            assert "focus" not in front(g), g  # rejected by claude plugin eval


@pytest.mark.skipif(not shutil.which("sdlc"), reason="the sdlc CLI is not on PATH")
@pytest.mark.parametrize("case", CASES, ids=lambda p: p.name)
def test_scaffold_builds_its_repo(case: Path, tmp_path: Path) -> None:
    r = subprocess.run(["bash", str(case / "scaffold.sh")], cwd=tmp_path, capture_output=True, text=True, timeout=120,
                       env={**__import__("os").environ, "GIT_CONFIG_GLOBAL": "/dev/null"})
    assert r.returncode == 0, r.stderr[-500:]
    assert (tmp_path / "sdlc.config.yaml").exists() and (tmp_path / ".sdlc" / "state" / "REQ-001.json").exists()
    for g in (case / "graders").glob("*.md"):  # a result grader must not already pass before the agent runs
        if g.stem.startswith(("not-", "does-not-")):
            continue  # invariants (the code is untouched, the gate still waits) pass before and after by design
        target = front(g).get("target")
        if isinstance(target, dict) and front(g)["type"] == "regex" and front(g).get("match") == "contains":
            path = tmp_path / target["path"]
            if path.exists():
                assert not re.search(front(g)["pattern"], path.read_text(), re.M), f"{g.name} passes before the run"
