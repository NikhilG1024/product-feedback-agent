import pytest
from fastapi import Depends
from fastapi.testclient import TestClient

from app.api.auth import require_pm, require_principal
from app.config import Settings
from app.domain import Principal
from app.errors import ServiceError
from app.main import create_app


def client_with_protected_routes(settings):
    app = create_app(settings)

    @app.get("/api/v1/whoami", response_model=Principal)
    def whoami(principal: Principal = Depends(require_principal)):
        return principal

    @app.get("/api/v1/pm", response_model=Principal)
    def pm_only(principal: Principal = Depends(require_pm)):
        return principal

    return TestClient(app)


def test_missing_token_is_unauthorized(settings):
    response = client_with_protected_routes(settings).get("/api/v1/whoami")
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "unauthorized"


def test_reviewer_cannot_access_pm_dependency(settings):
    response = client_with_protected_routes(settings).get(
        "/api/v1/pm", headers={"Authorization": "Bearer reviewer-secret-value"}
    )
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "forbidden"


def test_forged_role_and_identity_headers_have_no_effect(settings):
    response = client_with_protected_routes(settings).get(
        "/api/v1/whoami",
        headers={
            "Authorization": "Bearer reviewer-secret-value",
            "X-Role": "pm",
            "X-User-Id": "attacker",
        },
    )
    assert response.status_code == 200
    assert response.json() == {"user_id": "demo-reviewer", "role": "reviewer"}


def test_duplicate_configured_tokens_are_rejected():
    with pytest.raises(ValueError) as error:
        Settings(
            mongo_uri="mongodb://localhost:27017",
            mongo_database="product_feedback_test",
            reviewer_token="same-secret",
            pm_token="same-secret",
        )
    assert "same-secret" not in str(error.value)


def test_missing_auth_environment_fails_closed(monkeypatch):
    for name in ("DEMO_REVIEWER_TOKEN", "DEMO_PM_TOKEN"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("MONGODB_URI", "mongodb://localhost:27017")
    monkeypatch.setenv("MONGODB_DATABASE", "product_feedback_test")
    with pytest.raises(ValueError):
        Settings.from_env()


def test_provider_settings_from_env_and_repr_hide_secrets(monkeypatch):
    monkeypatch.setenv("MONGODB_URI", "mongodb://localhost:27017")
    monkeypatch.setenv("MONGODB_DATABASE", "product_feedback_test")
    monkeypatch.setenv("DEMO_REVIEWER_TOKEN", "reviewer-secret-value")
    monkeypatch.setenv("DEMO_PM_TOKEN", "pm-secret-value")
    monkeypatch.setenv("HINDSIGHT_API_URL", "https://memory.example.test")
    monkeypatch.setenv("HINDSIGHT_API_KEY", "memory-secret-value")
    monkeypatch.setenv("LLM_API_URL", "https://api.groq.com/openai/v1")
    monkeypatch.setenv("LLM_API_KEY", "model-secret-value")
    monkeypatch.setenv("LLM_MODEL", "openai/gpt-oss-20b")
    loaded = Settings.from_env()
    assert loaded.hindsight_api_url == "https://memory.example.test"
    assert loaded.hindsight_api_key == "memory-secret-value"
    assert loaded.llm_api_url == "https://api.groq.com/openai/v1"
    assert loaded.llm_api_key == "model-secret-value"
    assert loaded.llm_model == "openai/gpt-oss-20b"
    for secret in ("reviewer-secret-value", "pm-secret-value", "memory-secret-value", "model-secret-value"):
        assert secret not in repr(loaded)


def test_auth_errors_do_not_echo_secrets(settings):
    response = client_with_protected_routes(settings).get(
        "/api/v1/whoami", headers={"Authorization": "Bearer reviewer-secret-value-extra"}
    )
    assert response.status_code == 401
    assert "reviewer-secret-value" not in response.text
    assert "pm-secret-value" not in response.text


def test_service_errors_and_unexpected_errors_do_not_echo_secrets(settings):
    app = create_app(settings)

    @app.get("/api/v1/service-failure")
    def service_failure():
        raise ServiceError("provider_unavailable", 503, "pm-secret-value")

    @app.get("/api/v1/unexpected-failure")
    def unexpected_failure():
        raise RuntimeError("reviewer-secret-value")

    client = TestClient(app, raise_server_exceptions=False)
    for path, code in (("/api/v1/service-failure", "provider_unavailable"), ("/api/v1/unexpected-failure", "internal_error")):
        response = client.get(path)
        assert response.json()["error"]["code"] == code
        assert "secret-value" not in response.text


def test_http_exception_does_not_echo_secret_detail(settings):
    from fastapi import HTTPException

    app = create_app(settings)

    @app.get("/api/v1/http-failure")
    def http_failure():
        raise HTTPException(status_code=400, detail="pm-secret-value")

    response = TestClient(app).get("/api/v1/http-failure")
    assert response.status_code == 400
    assert "pm-secret-value" not in response.text


def test_health_checks_are_sanitized(settings):
    client = TestClient(create_app(settings))
    assert client.get("/health/live").json() == {"status": "ok"}
    ready = client.get("/health/ready")
    assert ready.status_code == 503
    assert ready.json() == {"status": "unavailable"}
    assert "secret-value" not in ready.text


def test_cors_allows_only_configured_origin(settings):
    client = TestClient(create_app(settings))
    allowed = client.options(
        "/api/v1/whoami",
        headers={"Origin": "https://app.example.test", "Access-Control-Request-Method": "GET"},
    )
    denied = client.options(
        "/api/v1/whoami",
        headers={"Origin": "https://evil.example.test", "Access-Control-Request-Method": "GET"},
    )
    assert allowed.headers["access-control-allow-origin"] == "https://app.example.test"
    assert "access-control-allow-origin" not in denied.headers


def test_streamed_body_limit_rejects_actual_bytes(settings):
    app = create_app(settings)

    @app.post("/api/v1/echo")
    async def echo():
        return {"accepted": True}

    def chunks():
        yield b"a" * 32768
        yield b"b" * 32769

    response = TestClient(app).post("/api/v1/echo", content=chunks())
    assert response.status_code == 413
    assert response.json()["error"]["code"] == "request_too_large"


def test_body_within_limit_reaches_route(settings):
    from fastapi import Request

    app = create_app(settings)

    @app.post("/api/v1/size")
    async def size(request: Request):
        return {"size": len(await request.body())}

    response = TestClient(app).post("/api/v1/size", content=b"a" * 65536)
    assert response.status_code == 200
    assert response.json() == {"size": 65536}
