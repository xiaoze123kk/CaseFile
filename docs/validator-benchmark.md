# CaseFile Validator Benchmark v1

This benchmark is the deterministic release gate for the CaseFile validator stack. It is intentionally separate from the live Chat T1 benchmark.

## Why it is separate from T1

T1 answers: **Did one user request eventually produce an acceptable outcome?**

The Validator benchmark answers three narrower questions without any LLM sampling:

- **V0 Rule Benchmark:** Did deterministic verification find exactly the defects it should find, without false positives on clean/boundary cases?
- **V1 Patch Gate Benchmark:** Did patch simulation accept safe changes, reject unsafe/invalid changes, preserve the input document, and return the correct reason code?
- **V2 Repair Contract Benchmark:** Did `ValidationIssue` values compile into the exact `RepairPlan` delta expected by the runtime?

A Validator benchmark failure must never be “fixed” by increasing `pass@k`, changing model temperature, or adding more LLM retries. It is a deterministic contract failure.

## Release gates

The default suite is fail-closed. Every gate below must pass:

| Layer | Metric | Target |
| --- | --- | ---: |
| V0 | case pass rate | 1.00 |
| V0 | required deterministic rule recall | 1.00 |
| V0 | clean/boundary false-positive rate | 0.00 |
| V0 | finding identity stability | 1.00 |
| V1 | case pass rate | 1.00 |
| V1 | batch safe false-reject rate | 0.00 |
| V1 | batch unsafe false-accept rate | 0.00 |
| V1 | batch reason-code accuracy | 1.00 |
| V1 | Chat Safe Patch Gate false-reject rate | 0.00 |
| V1 | Chat Safe Patch Gate false-accept rate | 0.00 |
| V1 | Chat Safe Patch Gate reason-code accuracy | 1.00 |
| V1 | input immutability | 1.00 |
| V2 | case pass rate | 1.00 |
| V2 | repair target precision | 1.00 |
| V2 | repair target recall | 1.00 |

These are intentionally stricter than Chat T1 because the benchmarked functions are deterministic.

## V0: Rule benchmark

V0 runs `VerificationEngine(profile="fast").verify(...)` twice for each document case. The second run is not a retry; it is an identity-stability assertion. The same frozen document must yield the same finding keys.

The initial suite contains:

- a clean restart-loop baseline;
- positive `knowledge_state_available_before_source` detection;
- a same-time knowledge boundary that must remain clean;
- positive `temporal_exclusivity_violation` detection;
- a non-overlapping temporal boundary that must remain clean;
- a structurally invalid dangling location reference;
- legacy LLM-finding normalization, including severity mapping and evidence preservation;
- normalization rejection cases for bad severity, missing fields, invalid confidence, invalid evidence, and duplicate finding fingerprints.

### V0 fixture example

```json
{
  "case_id": "v0-temporal-exclusivity-detected",
  "mutations": [
    {
      "op": "add",
      "path": "/events/0/participant_refs/-",
      "value": {"object_type": "entity", "object_id": "ent_researcher"}
    },
    {
      "op": "add",
      "path": "/events/-",
      "value": {"...": "a complete valid event fixture"}
    }
  ],
  "expected": {
    "structural_valid": true,
    "required_rule_codes": ["temporal_exclusivity_violation"],
    "exact_rule_codes": ["temporal_exclusivity_violation"]
  }
}
```

Use `exact_rule_codes` for semantically controlled cases where no extra finding is acceptable. For structural-invalid cases, prefer `structural_valid=false` plus `min_findings` if the precise contract error code is not the behavior under test.

## V1: Patch gate benchmark

V1 runs `VerificationEngine.simulate_patch_operation_batch(...)` using the production `EDITABLE_FIELDS` table. Every case verifies that the input CaseFile is unchanged after simulation.

The suite covers the explicit failure matrix currently exposed by the engine:

- `operation_limit_exceeded`
- `base_document_invalid`
- `operation_id_missing`
- `object_id_missing`
- `field_path_invalid`
- `operation_type_not_supported`
- `object_revision_conflict`
- `object_not_found`
- `object_type_conflict`
- `field_not_editable`
- `path_not_found`
- `old_value_conflict`
- `post_document_invalid`
- `finding_not_resolved`
- `structure_lock_conflict`
- `deterministic_severity_regression`

It also contains positive controls for a safe rename and for a patch that actually resolves a targeted deterministic finding.

V1 additionally executes the v15 `server_gate_audit_suggestions(...)` path so the benchmark covers the actual Safe Patch Registry handoff used by Chat. Those rows protect:

- canonical JSON patch values;
- plain-string normalization for known string targets;
- `object_not_found`, `field_not_editable`, and `path_not_found`;
- fenced/markdown `value_json` rejection;
- semantic-regression rejection through dry-run simulation;
- a patch that resolves an existing semantic finding;
- duplicate-target discard semantics;
- malformed proposal-shape rejection;
- propagation of `finding_ref` into the server-owned registry.

### V1 fixture example

```json
{
  "case_id": "v1-safe-entity-rename",
  "operation": {
    "operation_id": "op_safe_name",
    "object_id": "ent_researcher",
    "field_path": "/name",
    "old_value": "林研究员",
    "new_value": "林调查员",
    "object_type": "entity"
  },
  "expected": {
    "valid": true,
    "can_apply": true,
    "reason_code": null
  }
}
```

For finding-resolution tests, `target_rule_codes` is resolved to the actual deterministic finding keys produced from the frozen pre-patch document. This avoids hard-coding hash-derived finding keys in fixtures while still asserting the production finding identity contract.

## V2: Repair contract benchmark

V2 does not call a provider. It constructs production `ValidationIssue` objects and evaluates `plan_repairs(...)` exactly.

The initial suite covers:

- empty input -> empty plan;
- `missing` -> `add`;
- `extra` -> `remove`;
- same target appearing in `missing` and `extra` -> `add` wins and contradictory `remove` is removed;
- deterministic de-duplication/sorting of `preserve`;
- preservation of replacement payloads;
- non-repairable issues being ignored.

### V2 fixture example

```json
{
  "case_id": "v2-add-wins-over-remove-for-same-target",
  "issues": [
    {
      "code": "audit_suggestion_server_gate_failed",
      "stage": "patch",
      "path": "/suggestions/0",
      "repairable": true,
      "details": {"extra": ["ent_researcher:/description"]}
    },
    {
      "code": "audit_repairable_finding_missing_suggestion",
      "stage": "audit",
      "path": "/suggestions",
      "repairable": true,
      "details": {"missing": ["ent_researcher:/description"]}
    }
  ],
  "expected_plan": {
    "preserve": [],
    "add": ["ent_researcher:/description"],
    "remove": [],
    "replace": [],
    "fix": ["... exact issue records ..."]
  }
}
```

V2 is an exact contract benchmark. If the production RepairPlan schema intentionally changes, update the fixture and bump the benchmark schema/release notes together.

## Fixture mutation format

V0 and V1 cases may derive from a shared CaseFile fixture and apply a small JSON-Patch subset:

```json
{"op": "replace", "path": "/events/0/location_ref", "value": {...}}
{"op": "add", "path": "/events/-", "value": {...}}
{"op": "remove", "path": "/events/1"}
```

Supported operations are `add`, `replace`, and `remove`. `/-` appends to a list. The benchmark always deep-copies the base fixture before applying mutations.

## CLI

Run all deterministic validator gates:

```bash
python -m casefile.benchmark validator
```

Run one layer:

```bash
python -m casefile.benchmark validator --layer v0
python -m casefile.benchmark validator --layer v1
python -m casefile.benchmark validator --layer v2
```

Write a report:

```bash
python -m casefile.benchmark validator \
  --report-path backend/tmp/validator-benchmark.json
```

List all case IDs:

```bash
python -m casefile.benchmark validator --list-cases
```

The command exits with status `2` when any selected release gate fails.

## Report shape

```json
{
  "schema_version": "casefile-validator-benchmark-report-v1",
  "status": "passed",
  "engine_version": "verification-engine-v1",
  "layers": {
    "V0": {"status": "passed", "metrics": {}, "gates": {}, "rows": []},
    "V1": {"status": "passed", "metrics": {}, "gates": {}, "rows": []},
    "V2": {"status": "passed", "metrics": {}, "gates": {}, "rows": []}
  }
}
```

Each row keeps both `expected` and `actual` values so a CI artifact is sufficient to diagnose most deterministic regressions.

## Relationship to Chat T1

Keep the layers separate:

```text
Validator V0/V1/V2
    deterministic, no model calls
    must be fully green
        ↓
Chat runtime
    Initial + internal repair budget
        ↓
T1 live
    measures final user outcome and model stochasticity
```

`pass@1`/`pass@5` belong to T1. They do not belong to V0/V1/V2.

A recommended release sequence is:

1. Validator V0/V1/V2 all pass.
2. Chat unit/integration regressions pass.
3. Targeted live smoke passes.
4. Full T1 is started.

If V0/V1/V2 is red, do not spend live-model budget on T1.

## Adding a new validator rule

Every new deterministic rule should add at least three V0 rows before release:

1. one positive case that must detect the rule;
2. one clean negative case that must not detect it;
3. one near-boundary case that protects against over-triggering.

If the rule can be repaired by patch simulation, add V1 rows for both a safe repair and an unsafe/incorrect repair. If it introduces a new `ValidationIssue`/RepairPlan behavior, add a V2 exact-contract row as well.
