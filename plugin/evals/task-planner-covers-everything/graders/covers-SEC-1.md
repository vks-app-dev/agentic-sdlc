---
type: regex
pattern: '"SEC-1"'
match: contains
target: {source: file, path: .sdlc/plans/REQ-001.json}
---
SEC-1 is delivered by a task.
