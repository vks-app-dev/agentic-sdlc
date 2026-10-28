---
type: regex
pattern: 'As a <role>'
match: not_contains
target: {source: file, path: specs/REQ-001.md}
---
The template's user story placeholder was replaced.
