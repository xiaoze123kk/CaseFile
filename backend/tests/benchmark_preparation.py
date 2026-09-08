"""Scoped document preparation reuse for deterministic benchmark tests only."""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from copy import deepcopy
from hashlib import sha256
from typing import Any

import pytest
import rfc8785
from casefile.domain.verification_engine import VerificationEngine, VerificationFinding


@contextmanager
def reuse_document_findings() -> Iterator[None]:
    """Validate each document/configuration once; never reuse simulation or provider results."""
    findings: dict[str, tuple[tuple[VerificationFinding, ...], bool]] = {}
    original = VerificationEngine._deterministic_findings

    def prepared_findings(
        verifier: VerificationEngine, document: Mapping[str, Any],
    ) -> tuple[tuple[VerificationFinding, ...], bool]:
        key = sha256(rfc8785.dumps({
            "document": document,
            "profile": verifier.profile,
            "draft_revision": verifier.draft_revision,
            "closure_policy_version": verifier.closure_policy_version,
            "editable_fields": {
                kind: sorted(fields) for kind, fields in verifier.editable_fields_by_type.items()
            },
        })).hexdigest()
        if key not in findings:
            findings[key] = original(verifier, document)
        return deepcopy(findings[key])

    # Exit restores the real verifier even if the benchmark fails. No global cache.
    with pytest.MonkeyPatch.context() as patcher:
        patcher.setattr(VerificationEngine, "_deterministic_findings", prepared_findings)
        yield


@contextmanager
def reuse_prose_judge_inputs(loaded_suite: dict[str, Any]) -> Iterator[None]:
    """Reuse frozen input validation; judge responses and Council decisions still run."""
    from casefile.agent_runtime import prose_judge
    from casefile.benchmark import prose_judge_eval
    from casefile.domain.narrative_compiler import prose_checklist

    load_suite = prose_judge_eval.load_prose_judge_dev_suite
    validate_profile = prose_checklist.validate_novel_profile_v2
    validate_render = prose_checklist.validate_scene_render
    profiles: dict[str, Any] = {}
    renders: dict[str, Any] = {}

    def prepared_suite(
        suite_path: Any = prose_judge_eval.DEFAULT_SUITE,
        attestation_path: Any = prose_judge_eval.DEFAULT_ATTESTATION,
    ) -> dict[str, Any]:
        if (suite_path, attestation_path) == (
            prose_judge_eval.DEFAULT_SUITE, prose_judge_eval.DEFAULT_ATTESTATION,
        ):
            return deepcopy(loaded_suite)
        # Tampered files and alternate suites must always reach the actual loader.
        return load_suite(suite_path, attestation_path)

    def prepared_profile(profile: dict[str, Any]) -> Any:
        key = sha256(rfc8785.dumps(profile)).hexdigest()
        if key not in profiles:
            profiles[key] = validate_profile(profile)
        return profiles[key].model_copy(deep=True)

    def prepared_render(
        render: dict[str, Any], *, checklist: dict[str, Any], profile: dict[str, Any],
    ) -> Any:
        key = sha256(rfc8785.dumps([render, checklist, profile])).hexdigest()
        if key not in renders:
            renders[key] = validate_render(render, checklist=checklist, profile=profile)
        return renders[key].model_copy(deep=True)

    with pytest.MonkeyPatch.context() as patcher:
        patcher.setattr(prose_judge_eval, "load_prose_judge_dev_suite", prepared_suite)
        for owner in (prose_checklist, prose_judge, prose_judge_eval):
            if getattr(owner, "validate_novel_profile_v2", None) is validate_profile:
                patcher.setattr(owner, "validate_novel_profile_v2", prepared_profile)
            if getattr(owner, "validate_scene_render", None) is validate_render:
                patcher.setattr(owner, "validate_scene_render", prepared_render)
        yield
