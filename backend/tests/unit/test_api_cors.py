"""CORS configuration regressions for isolated local Web harnesses."""

from __future__ import annotations

from unittest.mock import patch

import pytest
from casefile.api.app import _cors_origins, create_app
from fastapi.testclient import TestClient


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
