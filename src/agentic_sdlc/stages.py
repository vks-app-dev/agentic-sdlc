"""The 8 SDLC stages: which agents run, what they produce, and who approves."""

from dataclasses import dataclass, field


@dataclass(frozen=True)
class AgentStep:
    agent: str  # subagent name in plugin/agents/
    does: str
    output: str
    parallel_group: int = 0  # steps with the same group run at the same time
    when: str = ""  # "ui": only for work items with a user interface


@dataclass(frozen=True)
class Stage:
    name: str
    title: str
    purpose: str
    steps: tuple[AgentStep, ...] = field(default_factory=tuple)
    default_gate: tuple[str, ...] = ("developer",)
    automated: bool = True  # False = no agents yet in the MVP; done by hand


STAGES: tuple[Stage, ...] = (
    Stage(
        "requirements",
        "Requirements",
        "Clear acceptance criteria and scope approval",
        (
            AgentStep("req-analyst", "Business context, users and roles, business rules, Given/When/Then ACs, flows and UX, UAT, RAID and estimate", "specs/{req}.md"),
            AgentStep("req-nfr-analyst", "Non-functional targets, data and integrations, security, privacy and compliance", "Sections 3, 5 and 6 of specs/{req}.md, docs/product/", 1),
            AgentStep("req-ambiguity-checker", "Challenge the spec: missing cases, empty sections, conflicts, compliance gaps", "Open questions section in specs/{req}.md", 2),
        ),
        ("pm", "tech-lead"),
    ),
    Stage(
        "design",
        "Design",
        "Architecture, security and risk review",
        (
            AgentStep("design-architect", "Options, recommendation, high-level design, ADRs, rollout; picks the workstreams", "docs/design/{req}/README.md"),
            AgentStep("design-data-api-designer", "Data model, schema, migrations; API contracts, errors, integrations", "docs/design/{req}/data.md, api.md", 1),
            AgentStep("design-ux-designer", "Screens, wireframes, states, messages, accessibility", "docs/design/{req}/ux.md", 1),
            AgentStep("design-ops-designer", "Deployment, config and secrets, observability, scaling, recovery", "docs/design/{req}/operations.md", 1),
            AgentStep("design-detail-designer", "Modules, interfaces, sequence diagrams, error handling, test design", "docs/design/{req}/lld.md", 2),
            AgentStep("design-security-reviewer", "Data flow, STRIDE threat model, security design, verdict", "docs/design/{req}/security.md", 2),
        ),
        ("tech-lead",),
    ),
    Stage(
        "planning",
        "Planning",
        "Executable plan: tasks, dependencies, test plan, environments, risks, release and monitoring",
        (
            AgentStep("plan-test-planner", "Unit, integration, regression, performance, security and UAT test plan", "docs/plan/{req}/test-plan.md"),
            AgentStep("plan-release-planner", "Environment readiness, risk register, release plan, post-release verification", "docs/plan/{req}/release-plan.md"),
            AgentStep("plan-task-planner", "Scope, work breakdown by area, dependencies, waves; the task DAG", "docs/plan/{req}/README.md, .sdlc/plans/{req}.json", 1),
        ),
        ("tech-lead",),
    ),
    Stage(
        "development",
        "Development",
        "Branch protection, peer review, automated tests",
        (
            AgentStep("dev-test-writer", "Write failing tests from the acceptance criteria (TDD red)", "Test files"),
            AgentStep("dev-coder", "Implement each ready task in its own worktree until tests pass (one coder per task)", "Code on branch sdlc/{req}-<task>", 1),
            AgentStep("dev-code-reviewer", "Review each change in a separate context: correctness, maintainability, security, performance", ".sdlc/reviews/{req}-<task>.md", 2),
            AgentStep("dev-integration-verifier", "Run the integrated build: migrations, config, contracts, every AC end to end", "docs/dev/{req}/verification.md, handover.md", 3),
            AgentStep("dev-doc-writer", "Update API spec, architecture overview, ADRs, configuration docs and runbook", "docs/, README, .env.example", 3),
            AgentStep("dev-pr-reviewer", "Review the whole pull request: cross-task consistency, contract drift, completeness, risk", "Line comments on the PR", 4),
            AgentStep("dev-pr-responder", "Turn each PR comment into a fix task or a reasoned reply; reply once fixes are pushed", "Fix tasks and PR replies", 5),
        ),
        ("peer",),
    ),
    Stage("cicd", "CI/CD", "Project pipeline (set up once per project; off per item by default)", default_gate=(),
          automated=False),
    Stage(
        "qa",
        "QA",
        "Independent testing against the requirements, defect triage and sign-off",
        (
            AgentStep("qa-functional-tester", "Functional, integration and regression testing against the spec", "docs/qa/{req}/functional.md", 0),
            AgentStep("qa-security-tester", "Dynamic security testing and verification of the security controls", "docs/qa/{req}/security.md", 0),
            AgentStep("qa-exploratory-tester", "Exploratory and compatibility testing: unexpected behaviour, edge cases, clients", "docs/qa/{req}/exploratory.md", 0),
            AgentStep("qa-uat-tester", "UAT scenarios as the business user, evidence for the success metrics", "docs/qa/{req}/uat.md", 0),
            AgentStep("qa-ui-tester", "UI, user journeys, loading, error states, accessibility, browsers and devices", "docs/qa/{req}/ui.md", 0, "ui"),
            AgentStep("qa-performance-tester", "Latency, load, scalability and stability against the NFR targets", "docs/qa/{req}/performance.md", 1),
            AgentStep("qa-defect-triager", "Dedupe and triage every defect: severity, release blocker, fix", "docs/qa/{req}/defects.md", 2),
        ),
        ("qa-lead",),
    ),
    Stage(
        "release",
        "Release",
        "Readiness, staging deploy and check, rollback rehearsal, release notes; human release approval",
        (
            AgentStep("rel-deploy-verifier", "Post-deployment check on staging after `sdlc deploy <REQ> staging`", "docs/release/{req}/verification.md", 0),
            AgentStep("rel-rollback-rehearser", "Rehearse deploy, migrate, roll back and re-deploy on staging or locally", "docs/release/{req}/rollback.md", 0),
            AgentStep("rel-notes-writer", "Release notes, changelog entry and the version", "docs/release/{req}/release-notes.md, CHANGELOG.md", 0),
            AgentStep("rel-readiness-checker", "Release readiness checklist with evidence", "docs/release/{req}/readiness.md", 1),
        ),
        ("release-manager",),
    ),
    Stage("production", "Production", "Deployed by a person; smoke check, logs, metrics, alerts",
          default_gate=("on-call",), automated=False),
    Stage("monitoring", "Monitoring", "Incident response, root-cause analysis, corrective actions", default_gate=(), automated=False),
)

STAGE_NAMES = tuple(s.name for s in STAGES)
BY_NAME = {s.name: s for s in STAGES}

# ---- Work types: what kind of work an item is decides which early stages run and with which agents ----

TOGGLEABLE = ("requirements", "design", "planning", "development")  # stages a work type can turn on or off
SIZES = ("S", "M", "L")


@dataclass(frozen=True)
class WorkType:
    name: str
    title: str
    purpose: str
    template: str  # spec template in templates/, copied to the project on init
    stages: tuple[str, ...]  # which TOGGLEABLE stages run; later stages follow the config
    steps: dict[str, tuple[AgentStep, ...]] = field(default_factory=dict)  # agents that differ from the stage's
    stage_titles: dict[str, str] = field(default_factory=dict)
    gates: dict[str, tuple[str, ...]] = field(default_factory=dict)  # gate overrides; () = no human gate
    design_if: tuple[str, ...] = ()  # run Design and Planning only for these sizes (empty = always)


WORK_TYPES: dict[str, WorkType] = {w.name: w for w in (
    WorkType("feature", "Feature", "New capability: full requirements, design, planning and development", "spec.md",
             TOGGLEABLE),
    WorkType(
        "change", "Change", "Change to existing behaviour: current vs desired, impact on what already exists",
        "change.md", TOGGLEABLE,
        steps={"requirements": (
            AgentStep("req-change-impact-analyst", "Describe current behaviour and what the change touches: code, tests, linked ACs, compatibility", "Current behaviour and Impact sections in specs/{req}.md"),
            AgentStep("req-analyst", "Write the desired behaviour as acceptance criteria, the ACs it replaces, rollout and risks", "specs/{req}.md", 1),
            AgentStep("req-nfr-analyst", "Quality, data and security delta of the change", "Section 6 of specs/{req}.md, docs/product/", 2),
            AgentStep("req-ambiguity-checker", "Challenge the spec: missing cases, empty sections, conflicts, compliance gaps", "Open questions section in specs/{req}.md", 3),
        )},
        design_if=("M", "L"),
    ),
    WorkType(
        "bug", "Bug", "Something that should work does not: reproduce, find the root cause, fix with a regression test",
        "bug.md", ("requirements", "development"),
        steps={"requirements": (
            AgentStep("req-bug-triager", "Reproduce the bug, find the root cause, write the fix acceptance criteria", "specs/{req}.md"),
        )},
        stage_titles={"requirements": "Triage"},
        gates={"requirements": ()},
    ),
    WorkType("task", "Task", "Technical work with no behaviour change: upgrades, refactors, tooling", "task.md",
             ("development",)),
)}

# ---- Model per agent: the most capable model only where judgement drives quality ----
# Claude Code aliases (opus / sonnet / haiku) so the newest model of each tier is used. Mirrored in each agent's
# frontmatter (a test keeps them in step) and overridable per repo in sdlc.config.yaml → models.
MODEL_ALIASES = ("opus", "sonnet", "haiku", "inherit")
DEEP = ("design-architect", "design-security-reviewer", "dev-pr-reviewer")  # architecture, threats, whole-PR review
LIGHT = ("rel-notes-writer", "rel-readiness-checker", "qa-defect-triager")  # summarising and collecting evidence
DEFAULT_ORCHESTRATOR_MODEL = "sonnet"  # the /sdlc-run session: coordination and bookkeeping


def default_model(agent: str) -> str:
    return "opus" if agent in DEEP else "haiku" if agent in LIGHT else "sonnet"


AUTONOMY_LEVELS = {
    "L1": "Assist: agent drafts, human does the work",
    "L2": "Propose: agent does the work, human approves each output",
    "L3": "Auto with review: agent acts, human reviews in batches",
    "L4": "Autonomous: agent acts, human is notified",
}
