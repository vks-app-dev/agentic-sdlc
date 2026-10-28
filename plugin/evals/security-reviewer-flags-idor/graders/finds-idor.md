---
type: llm
criteria: >-
  Judge docs/design/REQ-001/security.md. security.md identifies that any logged-in user can read another account's statement by changing account_id in
  the path (broken object-level authorization / IDOR), rates it high severity, requires an ownership check as a
  numbered mitigation (SEC-n), and does not give the verdict "OK".
---
