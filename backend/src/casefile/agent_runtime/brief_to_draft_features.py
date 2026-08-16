"""Feature protocols for brief-to-draft without heavy import dependencies.

The protocols live here (rather than in :mod:`brief_to_draft_runtime`) so both
the runtime spec registry and the CaseFile compiler can import them without
creating an import cycle through ``brief_to_draft_v8/__init__``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
    from casefile.agent_runtime.brief_to_draft_v12.contracts import TemporalPlanV1
    from casefile.agent_runtime.models import GenerationRequest


class StoryFeature(Protocol):
    """Pluggable Story-domain capability.

    The default story runtime is configured directly on the spec. A feature can
    replace the Story output model and own version-specific input fields,
    validation, and temporal-plan joining.
    """

    feature_id: str

    def domain_input_fields(self, request: GenerationRequest) -> dict[str, Any]:
        """Return extra fields for the Story component input payload."""
        ...

    def validate_story(
        self,
        story: Any,
        *,
        request: GenerationRequest,
    ) -> list[dict[str, Any]]:
        """Return contract-style issues for a compiled Story object."""
        ...

    def with_temporal_plan(self, story: Any, plan: TemporalPlanV1) -> Any:
        """Join a validated Temporal Plan into the compiler-facing Story IR."""
        ...


class CompilerFeature(Protocol):
    """Pluggable CaseFile compiler capability."""

    feature_id: str

    def compile_document(
        self,
        document: dict[str, Any],
        *,
        story: Any,
        linked: Any,
    ) -> None:
        """Mutate the compiled CaseFile document before contract validation."""
        ...


__all__ = ["CompilerFeature", "StoryFeature"]
