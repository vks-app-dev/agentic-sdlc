---
type: regex
pattern: '^Verdict: CHANGES_REQUESTED'
flags: m
match: contains
target: {source: file, path: .sdlc/reviews/REQ-001-T1.md}
---
The check compares one refund with the payment but ignores what was already refunded, so two refunds of 60 on a
payment of 100 pass: AC-2 is not met.
