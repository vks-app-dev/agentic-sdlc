---
type: regex
pattern: '"status": "awaiting_approval"'
match: contains
target: {source: file, path: .sdlc/state/REQ-001.json}
---
The gate is still waiting: no agent approved it.
