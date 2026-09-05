"""Strict public-only B3 diagnostic package validation and frozen gates."""

import json
from collections import Counter
from pathlib import Path
from typing import Any

from casefile.benchmark.prose_quality_eval import QUALITY_FOCI
from casefile.domain.narrative_compiler import (
    QUALITY_DIMENSIONS,
    canonical_json_sha256,
    validate_quality_pair_inputs,
    validate_semantic_acceptance,
)

ROOT = Path(__file__).resolve().parents[4]
DEFAULT_SUITE = ROOT / "fixtures/prose_quality_benchmark/diagnostic_v1/suite.json"
QUALITY_GATES = {"overall_accuracy_min": 21, "mirrored_consistency_min": 23}
POLISHER_GATES = {"preservation_min": 24, "stable_adoption_min": 18, "quality_non_loss_min": 22}


def load_diagnostic_suite(path: Path = DEFAULT_SUITE) -> dict[str, Any]:
    if not path.resolve().is_relative_to((ROOT / "fixtures/prose_quality_benchmark").resolve()):
        raise ValueError("diagnostic_suite_must_be_public")
    value: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    if (
        value.get("schema_id") != "casefile.prose-quality-diagnostic-suite.v1"
        or value.get("suite_role") != "development"
        or value.get("qualified") is not False
        or value.get("repeats") != 3
        or value.get("quality_gates") != QUALITY_GATES
        or value.get("polisher_gates") != POLISHER_GATES
        or value.get("suite_hash")
        != canonical_json_sha256({k: v for k, v in value.items() if k != "suite_hash"})
    ):
        raise ValueError("diagnostic_suite_freeze_invalid")
    review = value.get("review", {})
    if (
        review.get("reviewer") != "Codex"
        or review.get("reviewer_independence") is not False
        or review.get("unresolved_findings") != []
        or review.get("semantic_evidence_origin") != "authored_gold_not_live_council"
    ):
        raise ValueError("diagnostic_review_invalid")
    identities: set[str] = set()
    texts: set[str] = set()
    for group in ("quality_tasks", "polisher_tasks"):
        tasks = value[group]
        if len(tasks) != 24 or Counter(t["focus"] for t in tasks) != Counter(
            {f: 3 for f in QUALITY_FOCI}
        ):
            raise ValueError("diagnostic_distribution_invalid")
        for task in tasks:
            if task["task_id"] in identities:
                raise ValueError("diagnostic_duplicate_identity")
            identities.add(task["task_id"])
            if (
                canonical_json_sha256(task["profile"])
                != task["checklist"]["source"]["profile_hash"]
            ):
                raise ValueError("diagnostic_profile_binding_invalid")
            pairs: tuple[tuple[dict[str, Any], dict[str, Any]], ...]
            if group == "quality_tasks":
                validate_quality_pair_inputs(
                    checklist=task["checklist"],
                    profile=task["profile"],
                    original_render=task["render_a"],
                    polished_render=task["render_b"],
                    preservation_consensus=task["semantic_consensus_b"],
                )
                pairs = (
                    (task["render_a"], task["semantic_consensus_a"]),
                    (task["render_b"], task["semantic_consensus_b"]),
                )
                gold = task["gold"]
                if (
                    gold["overall_preference"] not in {"a", "b", "tie"}
                    or [p["dimension"] for p in gold["dimension_preferences"]]
                    != list(QUALITY_DIMENSIONS)
                    or any(
                        p["preference"] not in {"a", "b", "tie"}
                        for p in gold["dimension_preferences"]
                    )
                ):
                    raise ValueError("diagnostic_gold_invalid")
            else:
                pairs = ((task["original_render"], task["semantic_consensus"]),)
            for render, consensus in pairs:
                validate_semantic_acceptance(
                    consensus, checklist=task["checklist"], profile=task["profile"], render=render
                )
                text = "".join(b["text"] for b in render["blocks"])
                if text in texts or "\ufffd" in json.dumps(task, ensure_ascii=False):
                    raise ValueError("diagnostic_duplicate_or_corrupted_text")
                texts.add(text)
    for focus in QUALITY_FOCI:
        if Counter(
            t["gold"]["overall_preference"] for t in value["quality_tasks"] if t["focus"] == focus
        ) != Counter({"a": 1, "b": 1, "tie": 1}):
            raise ValueError("diagnostic_gold_distribution_invalid")
    return value
