---
type: regex
pattern: '\*\*AC-[2-9]\d*\*\*'
match: contains
target: {source: file, path: specs/REQ-001.md}
---
More than one acceptance criterion: the main path and at least one error or edge case.
