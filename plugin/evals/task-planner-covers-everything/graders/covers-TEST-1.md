---
type: regex
pattern: '"TEST-1"'
match: contains
target: {source: file, path: .sdlc/plans/REQ-001.json}
---
TEST-1 is delivered by a task.
