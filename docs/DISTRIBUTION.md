# Rolling Agentic SDLC out across a company

## 1. Publish it from one company repository
- Host `agentic-sdlc` in a company repository (e.g. `vks-app-dev/agentic-sdlc`). It holds the engine (`src/`), the Claude Code
  plugin (`plugin/`) and the plugin marketplace (`.claude-plugin/marketplace.json`).
- Release by tag: bump the version in `pyproject.toml`, `src/agentic_sdlc/__init__.py` and
  `plugin/.claude-plugin/plugin.json` (a test keeps them equal), add a `CHANGELOG.md` entry, tag `v0.8.0`.

## 2. Install on a machine
```bash
uv tool install "git+https://github.com/vks-app-dev/agentic-sdlc@v0.8.0"      # the sdlc CLI, pinned to a release
claude plugin marketplace add vks-app-dev/agentic-sdlc                         # the Claude Code plugin
claude plugin install agentic-sdlc@agentic-sdlc
sdlc --version
```
Upgrade with `uv tool install "git+https://github.com/vks-app-dev/agentic-sdlc@v0.8.0" --force`, then update the plugin
with `/plugin`. Then, in each project, `sdlc upgrade` (and `sdlc doctor`) brings its config and generated files
up to the new version; commit the result.

## 3. Company policy
- Keep `org.yaml` (see `docs/org.example.yaml`) and the company templates in a policy repository.
- Each project points at it with `org: ../sdlc-policy` in `sdlc.config.yaml`, or machines set `SDLC_ORG_CONFIG`.
- Policy is applied every time the config loads; `sdlc doctor` shows what it changed in a project.
- `min_version` in org.yaml makes older CLIs refuse to run; `requires: ">=0.7"` in a project's config does the
  same for that project.

## 4. Onboard a project
```bash
cd payments-api
/sdlc-init            # in Claude Code: scans, interviews, writes sdlc.config.yaml
sdlc doctor           # ✓ / ⚠ / ✗ for versions, policy, git, GitHub, tools, exports
sdlc github protect   # a person, once: GitHub blocks merging until QA and Release pass
```
For Copilot users, add `copilot` to `integrations.hosts` (or run `sdlc agents export --host copilot`).

## 5. See every project
```bash
sdlc portfolio                 # the projects listed in org.yaml, or paths: sdlc portfolio ../a ../b
sdlc portfolio --serve         # the read-only portfolio dashboard on http://127.0.0.1:8770
```
The portfolio reads local clones. A shared, always-on portfolio that pulls every repo is the central-service step
on the roadmap.
