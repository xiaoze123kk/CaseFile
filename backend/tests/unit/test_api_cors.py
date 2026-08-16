"""CORS configuration regressions for isolated local Web harnesses."""

from __future__ import annotations

from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from casefile.api.app import _contract_validation_error_handler, _cors_origins, create_app
from casefile.contracts import ContractValidationError


def test_custom_cors_origin_is_exactly_allowed_for_preflight() -> None:
    origin = "http://127.0.0.1:13000"
    with patch.dict("os.environ", {"CASEFILE_CORS_ORIGINS": origin}):
        app = create_app(verify_database=False)
        with TestClient(app) as client:
            response = client.options(
                "/api/v1/projects",
                headers={
                    "Origin": origin,
                    "Access-Control-Request-Method": "POST",
                    "Access-Control-Request-Headers": "content-type,x-casefile-user-id",
                },
            )

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == origin


def test_invalid_cors_origin_configuration_fails_closed() -> None:
    with patch.dict(
        "os.environ",
        {"CASEFILE_CORS_ORIGINS": "http://127.0.0.1:13000/path"},
    ):
        with pytest.raises(RuntimeError, match=r"HTTP\(S\) origins"):
            _cors_origins()


@pytest.mark.asyncio
async def test_contract_validation_error_returns_readable_422_body() -> None:
    response = await _contract_validation_error_handler(
        None,  # type: ignore[arg-type]
        ContractValidationError(
            [
                {
                    "code": "conclusion_reasoning_path_scope_invalid",
                    "path": "/resolution_specs/0/conclusion/supporting_reasoning_path_refs/0",
                    "message": "must not leak candidate values",
                }
            ]
        ),
    )

    assert response.status_code == 422
    assert b"conclusion_reasoning_path_scope_invalid" in response.body
    assert "结论依据路径必须属于当前问题的必要推理链".encode() in response.body
    assert b"must not leak candidate values" not in response.body
