---
type: regex
pattern: '"OPS-1"'
match: contains
target: {source: file, path: .sdlc/plans/REQ-001.json}
---
OPS-1 is delivered by a task.
