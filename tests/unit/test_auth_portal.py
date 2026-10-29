"""Unit tests for the authentication portal, session tokens, and security gateway."""

from pathlib import Path
import time
import urllib.parse

from fastapi.testclient import TestClient
import pytest

from hm_recommender.api.app import create_app
from hm_recommender.api.auth import (
    COOKIE_NAME,
    create_session_token,
    hash_password,
    render_login_html,
    verify_session_token,
    verify_user_credentials,
)


def test_session_token_create_and_verify():
    secret = b"test_secret_key_12345"
    token = create_session_token("alanmagno", secret, max_age=3600)
    assert token is not None
    assert "." in token

    # Valid token returns username
    assert verify_session_token(token, secret) == "alanmagno"

    # Wrong secret fails
    assert verify_session_token(token, b"wrong_secret") is None

    # Tampered token fails
    parts = token.split(".")
    tampered = parts[0] + ".badsignature"
    assert verify_session_token(tampered, secret) is None

    # Expired token fails
    expired_token = create_session_token("alanmagno", secret, max_age=-10)
    assert verify_session_token(expired_token, secret) is None


def test_password_hashing_and_verification():
    pwd = "MySuperSecretPassword123!"
    hashed = hash_password(pwd)
    assert len(hashed) == 64

    hashes = {"alanmagno": hashed}
    assert verify_user_credentials("alanmagno", pwd, hashes) is True
    assert verify_user_credentials("alanmagno", "WrongPassword", hashes) is False
    assert verify_user_credentials("unknown_user", pwd, hashes) is False
    assert verify_user_credentials("", pwd, hashes) is False


def test_render_login_html():
    html = render_login_html()
    assert 'action="/login"' in html
    assert 'name="username"' in html
    assert 'name="password"' in html
    assert 'autocomplete="username"' in html
    assert 'autocomplete="current-password"' in html
    assert 'role="alert"' not in html

    html_error = render_login_html(error="Credenciales incorrectas")
    assert 'role="alert"' in html_error
    assert "Credenciales incorrectas" in html_error


def test_api_auth_endpoints(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("HM_APP_PASSWORD", "test_app_token_98765")

    # Set up mock gateway.json configuration
    cfg_file = tmp_path / "gateway.json"
    pwd = "SecretPassWord2026!"
    cfg_file.write_text(
        f'{{"domain": "hm.test", "users": {{"testuser": "bcrypt_stub"}}, "password_hashes": {{"testuser": "{hash_password(pwd)}"}}}}',
        encoding="utf-8",
    )
    monkeypatch.setenv("HMREC_GATEWAY_CONFIG", str(cfg_file))

    app = create_app(admin_token="test_app_token_98765")
    client = TestClient(app)

    # 1. GET /login returns 200 with HTML form
    res = client.get("/login")
    assert res.status_code == 200
    assert "text/html" in res.headers["content-type"]
    assert 'action="/login"' in res.text

    # 2. POST /login with invalid password returns 200 with error
    res = client.post(
        "/login",
        data={"username": "testuser", "password": "WrongPassword"},
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    assert res.status_code == 200
    assert "error-banner" in res.text

    # 3. POST /login with correct password sets cookie and redirects
    res = client.post(
        "/login",
        data={"username": "testuser", "password": pwd, "remember": "1"},
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        follow_redirects=False,
    )
    assert res.status_code == 303
    assert res.headers["location"] == "/"
    assert COOKIE_NAME in res.cookies
    cookie_token = res.cookies[COOKIE_NAME]

    # 4. GET /login when already authenticated redirects to /
    client.cookies.set(COOKIE_NAME, cookie_token)
    res = client.get("/login", follow_redirects=False)
    assert res.status_code == 303
    assert res.headers["location"] == "/"

    # 5. GET /auth/verify with valid cookie returns 200
    res = client.get("/auth/verify")
    assert res.status_code == 200

    # 6. GET /auth/verify with Basic Auth returns 200
    client.cookies.clear()
    import base64
    auth_header = "Basic " + base64.b64encode(f"testuser:{pwd}".encode()).decode()
    res = client.get("/auth/verify", headers={"Authorization": auth_header})
    assert res.status_code == 200

    # 7. GET /auth/verify with invalid Basic Auth returns 401
    bad_auth = "Basic " + base64.b64encode(b"testuser:wrong").decode()
    res = client.get("/auth/verify", headers={"Authorization": bad_auth})
    assert res.status_code == 401

    # 8. GET /auth/verify unauthenticated browser request redirects to /login (307)
    client.cookies.clear()
    res = client.get("/auth/verify", headers={"Accept": "text/html,application/xhtml+xml"}, follow_redirects=False)
    assert res.status_code == 307
    assert res.headers["location"] == "/login"

    # 9. GET /auth/verify unauthenticated automated checker returns 401
    res = client.get("/auth/verify", headers={"User-Agent": "hmrec-server-check/1.0"})
    assert res.status_code == 401
    assert "Basic" in res.headers.get("www-authenticate", "")

    # 10. GET /auth/verify unauthenticated WebSocket endpoint returns 401
    res = client.get("/auth/verify", headers={"X-Forwarded-Uri": "/_stcore/stream"})
    assert res.status_code == 401

    # 11. GET /logout clears cookie and redirects to /login
    res = client.get("/logout", follow_redirects=False)
    assert res.status_code == 303
    assert res.headers["location"] == "/login"
