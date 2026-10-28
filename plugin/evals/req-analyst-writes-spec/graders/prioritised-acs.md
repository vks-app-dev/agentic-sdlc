---
type: regex
pattern: '\*\*AC-\d+\*\*\s*\((?:must|should|could)\)\s+\w[^\n]*\n\s*- Given \w'
match: contains
target: {source: file, path: specs/REQ-001.md}
---
At least one acceptance criterion has an id, a priority, a title and a written Given step (the template's empty
`- **AC-1** (must)` does not count).
