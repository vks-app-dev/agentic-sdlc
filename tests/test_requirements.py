import json
import subprocess
from pathlib import Path

import pytest

from agentic_sdlc import config, gitops, state, trace
from agentic_sdlc.cli import main


@pytest.fixture
def repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "answers.json").write_text(json.dumps({"project": {"name": "demo", "type": "new", "compliance": ["gdpr", "soc2"]}}))
    assert main(["init", "--answers", "answers.json"]) == 0
    return tmp_path


def test_feature_spec_covers_the_eight_areas(repo: Path) -> None:
    assert main(["new", "Refunds"]) == 0
    spec = (repo / "specs" / "REQ-001.md").read_text()
    for heading in ("## 1. Business context", "### Objectives and success metrics", "### Stakeholders",
                    "## 2. Users and functional requirements", "### Users and roles", "### Business rules",
                    "### Acceptance criteria", "## 3. Non-functional requirements", "## 4. Process and user experience",
                    "## 5. Data and integrations", "## 6. Security, privacy and compliance",
                    "## 7. Testing and acceptance", "## 8. Delivery", "## Open questions", "## Resolved decisions"):
        assert heading in spec, heading


def test_product_docs_created_once_with_compliance_seeded(repo: Path) -> None:
    compliance = repo / "docs" / "product" / "compliance.md"
    assert "| gdpr | (to complete) |" in compliance.read_text() and "| soc2 |" in compliance.read_text()
    for name in ("overview", "data", "integrations"):
        assert (repo / "docs" / "product" / f"{name}.md").exists()
    compliance.write_text("# ours\n")
    assert main(["new", "Refunds"]) == 0  # never overwritten
    assert compliance.read_text() == "# ours\n"
    nxt = state.next_action(repo, config.load(repo), "REQ-001")
    assert nxt["compliance"] == ["gdpr", "soc2"] and "docs/product/data.md" in nxt["inputs"]
    assert [s["agent"] for s in nxt["steps"]] == ["req-analyst", "req-nfr-analyst", "req-ambiguity-checker"]
    assert gitops.managed("docs/product/data.md") and gitops.managed("docs/rtm/REQ-001.md")


SPEC = """# REQ-001: Refunds
### Acceptance criteria
- **AC-1** (must) Full refund
  - Given
- **AC-2** (should) Partial refund
- **AC-3** (must) Refund over the charge is rejected

## Open questions
- [x] **Q1 (AC-3, blocking):** Over-refund: reject or cap?
  **Answer:** reject
- [ ] **Q2 (non-blocking):** Currency?
  **Answer:**

## Resolved decisions
- Q0: refunds only for card payments → AC-1
"""


def test_trace_links_acs_to_tasks_tests_and_decisions(repo: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    assert main(["new", "Refunds"]) == 0
    assert main(["new", "Exports"]) == 0
    (repo / "specs" / "REQ-001.md").write_text(SPEC)
    state.set_plan(repo, "REQ-001", [{"id": "T1", "title": "service", "acceptance": ["AC-1", "AC-3"]},
                                      {"id": "T2", "title": "api", "acceptance": ["AC-2"]}])
    tests = repo / "tests"
    tests.mkdir()
    (tests / "test_refunds.py").write_text("def test_full():  # REQ-001 AC-1\n    pass\n")
    (tests / "test_req001_acceptance.py").write_text("# AC-2 partial\n")
    (tests / "test_exports.py").write_text("# REQ-002 AC-3 is another item's AC\n# AC-3 too\n")
    t = trace.build(repo, "REQ-001")
    rows = {r["id"]: r for r in t["rows"]}
    assert rows["AC-1"]["tests"] == ["tests/test_refunds.py"] and rows["AC-1"]["tasks"] == ["T1"]
    assert rows["AC-2"]["tests"] == ["tests/test_req001_acceptance.py"] and rows["AC-2"]["priority"] == "should"
    assert t["uncovered"] == ["AC-3"] and t["uncovered_must"] == ["AC-3"]
    assert t["decisions"] == ["Q0: refunds only for card payments → AC-1", "Q1: Over-refund: reject or cap? → reject"]
    assert main(["trace", "REQ-001"]) == 0
    rtm = (repo / "docs" / "rtm" / "REQ-001.md").read_text()
    assert "2/3 acceptance criteria have tests" in rtm and "| AC-3 | must |" in rtm and "**none**" in rtm
    assert "docs/rtm/REQ-001.md" in state.load(repo, "REQ-001")["stages"]["requirements"]["artifacts"]


def test_design_pack_workstreams_adrs_and_approval(repo: Path) -> None:
    c = config.load(repo)
    assert main(["new", "Refunds"]) == 0
    state.start_stage(repo, "REQ-001")
    state.complete_stage(repo, "REQ-001", c)
    assert main(["approve", "REQ-001"]) == 0
    assert main(["start", "REQ-001"]) == 0
    readme = repo / "docs" / "design" / "REQ-001" / "README.md"
    assert "## Workstreams" in readme.read_text() and "Status: proposed" in readme.read_text()
    assert (repo / "docs" / "design" / "TEMPLATE" / "operations.md").exists()
    assert (repo / "docs" / "architecture" / "overview.md").exists()
    nxt = state.next_action(repo, c, "REQ-001")
    assert [s["agent"] for s in nxt["steps"]][:2] == ["design-architect", "design-data-api-designer"]
    assert "docs/design/REQ-001/README.md" in nxt["inputs"] and "docs/architecture/overview.md" in nxt["inputs"]
    assert main(["design", "REQ-001", "api"]) == 0
    api = repo / "docs" / "design" / "REQ-001" / "api.md"
    assert "## Errors" in api.read_text()
    api.write_text(api.read_text() + "| POST | /refunds | create | user | AC-1 |\n")
    with pytest.raises(SystemExit):  # argparse rejects unknown workstreams
        main(["design", "REQ-001", "billing"])
    assert main(["adr", "REQ-001", "Use SQLite for storage"]) == 0
    assert main(["adr", "REQ-001", "Money as integer cents"]) == 0
    adrs = sorted(p.name for p in (repo / "docs" / "adr").glob("0*.md"))
    assert adrs == ["0001-use-sqlite-for-storage.md", "0002-money-as-integer-cents.md"]
    arts = state.load(repo, "REQ-001")["stages"]["design"]["artifacts"]
    assert "docs/design/REQ-001/api.md" in arts and "docs/adr/0002-money-as-integer-cents.md" in arts
    state.set_plan(repo, "REQ-001", [{"id": "T1", "title": "api", "acceptance": ["AC-1"]}])
    state.complete_stage(repo, "REQ-001", c)
    assert main(["approve", "REQ-001", "Option", "B"]) == 0
    text = readme.read_text()
    assert "Status: approved" in text and ": Option B." in text.split("## Decision")[1]
    assert "Status: accepted" in (repo / "docs" / "adr" / "0001-use-sqlite-for-storage.md").read_text()
    (repo / "specs" / "REQ-001.md").write_text("### Acceptance criteria\n- **AC-1** (must) Refund\n")
    t = trace.build(repo, "REQ-001")
    assert t["rows"][0]["design"] == ["docs/design/REQ-001/api.md"]


def test_items_with_a_legacy_adr_keep_it(repo: Path) -> None:
    c = config.load(repo)
    assert main(["new", "Refunds"]) == 0
    (repo / "docs" / "adr" / "ADR-REQ-001.md").write_text("# old style\n")
    state.start_stage(repo, "REQ-001")
    state.complete_stage(repo, "REQ-001", c)
    state.approve(repo, "REQ-001", c, "ana")
    assert main(["start", "REQ-001"]) == 0
    assert not (repo / "docs" / "design" / "REQ-001").exists()
    assert "docs/adr/ADR-REQ-001.md" in state.next_action(repo, c, "REQ-001")["inputs"]
