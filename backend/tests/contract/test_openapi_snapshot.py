from __future__ import annotations

import json
from pathlib import Path

from casefile.api.app import create_app


def test_openapi_snapshot_matches_application() -> None:
    repo_root = Path(__file__).resolve().parents[3]
    expected = json.loads((repo_root / "contracts" / "openapi.json").read_text(encoding="utf-8"))

    actual = create_app(verify_database=False).openapi()

    assert actual == expected


def test_agent_json_routes_publish_generated_public_models() -> None:
    document = create_app(verify_database=False).openapi()
    paths = document["paths"]
    expected = {
        "/api/v1/projects/{project_id}/agent/threads/{thread_id}/messages": {
            "get": "#/components/schemas/PublicAgentMessage",
            "post": "#/components/schemas/PublicAgentMessageReceipt",
        },
        (
            "/api/v1/projects/{project_id}/agent/threads/{thread_id}/messages/"
            "{message_id}/routing-feedback"
        ): {"post": "#/components/schemas/PublicRoutingFeedbackReceipt"},
        "/api/v1/projects/{project_id}/agent/runs/{run_id}": {
            "get": "#/components/schemas/PublicAgentRun",
        },
        "/api/v1/projects/{project_id}/agent/runs/{run_id}/cancel": {
            "post": "#/components/schemas/PublicAgentRun",
        },
        "/api/v1/projects/{project_id}/agent/runs/{run_id}/events": {
            "get": "#/components/schemas/PublicAgentEvent",
        },
        "/api/v1/projects/{project_id}/agent/patch-sets/{patch_set_id}/simulate": {
            "post": "#/components/schemas/PublicPatchReviewResult",
        },
        "/api/v1/projects/{project_id}/agent/patch-sets/{patch_set_id}/apply": {
            "post": "#/components/schemas/PublicPatchResponse",
        },
        "/api/v1/projects/{project_id}/agent/patch-sets/{patch_set_id}/undo": {
            "post": "#/components/schemas/PublicPatchResponse",
        },
        "/api/v1/projects/{project_id}/agent/patch-sets/{patch_set_id}/redo": {
            "post": "#/components/schemas/PublicPatchResponse",
        },
    }
    for path, methods in expected.items():
        for method, schema_ref in methods.items():
            responses = paths[path][method]["responses"]
            success = next(
                response for status, response in responses.items() if status.startswith("2")
            )
            schema = success["content"]["application/json"]["schema"]
            if schema.get("type") == "array":
                assert schema["items"]["$ref"] == schema_ref
            else:
                assert schema["$ref"] == schema_ref
