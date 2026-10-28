---
type: regex
pattern: 'totals\.py:\d+'
match: contains
target: {source: file, path: specs/REQ-001.md}
---
The root cause cites the file and line.
