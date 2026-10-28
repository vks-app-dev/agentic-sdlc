---
type: regex
pattern: 'len\(amounts\)'
match: contains
target: {source: file, path: app/totals.py}
---
The triager does not change the code: the buggy line is still there.
