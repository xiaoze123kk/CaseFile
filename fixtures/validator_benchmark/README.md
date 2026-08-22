# Validator benchmark fixtures

Release suite files:

- `v0-rules.json` — deterministic VerificationEngine rules and LLM-finding normalization.
- `v1-patch-gates.json` — PatchOperation / BatchSimulation matrix plus v15 Chat Safe Patch Gate cases.
- `v2-repair-contracts.json` — ValidationIssue -> RepairPlan exact contracts.

`examples/` contains small copyable case fragments. The examples are documentation only and are not executed by the release suite.

All release suite files use `schema_version = "casefile-validator-benchmark-v1"`.
