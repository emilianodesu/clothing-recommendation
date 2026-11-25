"""Unit tests for the authentication portal, session tokens, and security gateway."""

import base64
import hashlib
import hmac
import inspect
import json
import os
from pathlib import Path
import re
import time

from fastapi.testclient import TestClient
import pytest

import hm_recommender.api.auth as auth_module
from hm_recommender.api.app import create_app
from hm_recommender.api.auth import (
    COOKIE_NAME,
    DEFAULT_SESSION_DURATION,
    MAX_LOGIN_ATTEMPTS,
    MAX_TRACKED_IPS,
    SHORT_SESSION_DURATION,
    LoginRateLimiter,
    create_session_token,
    extract_client_ip,
    hash_password,
    load_user_hashes,
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
    hashed_second = hash_password(pwd)
    assert re.fullmatch(r"scrypt\$n=16384\$r=8\$p=1\$[0-9a-f]{32}\$[0-9a-f]{64}", hashed)
    assert hashed != hashed_second

    hashes = {"alanmagno": hashed}
    assert verify_user_credentials("alanmagno", pwd, hashes) is True
    assert verify_user_credentials("alanmagno", "WrongPassword", hashes) is False
    assert verify_user_credentials("unknown_user", pwd, hashes) is False
    assert verify_user_credentials("", pwd, hashes) is False
    assert verify_user_credentials("alanmagno", "", hashes) is False
    assert verify_user_credentials("a" * 129, pwd, {"a" * 129: hashed}) is False
    assert verify_user_credentials("alanmagno", "x" * 257, {"alanmagno": hash_password("x" * 257)}) is False


def test_legacy_sha256_compat_and_malformed_hashes():
    pwd = "LegacyPassword2026!"
    legacy_hash = hashlib.sha256(pwd.encode("utf-8")).hexdigest()
    assert len(legacy_hash) == 64

    hashes = {"legacy_user": legacy_hash}
    assert verify_user_credentials("legacy_user", pwd, hashes) is True
    assert verify_user_credentials("legacy_user", "WrongPassword", hashes) is False

    malformed_entries = [
        "scrypt$n=16384$r=8$p=1$short$deadbeef",
        "scrypt$n=1000$r=8$p=1$" + ("aa" * 16) + "$" + ("bb" * 32),  # non-power-of-two n
        "scrypt$n=1048576$r=8$p=1$" + ("aa" * 16) + "$" + ("bb" * 32),  # excessive memory
        "scrypt$n=16384$r=8$p=1$" + ("aa" * 15 + "  ") + "$" + ("bb" * 32),  # whitespace in hex
        "not-a-valid-hash",
    ]
    for idx, bad_hash in enumerate(malformed_entries):
        assert verify_user_credentials(f"u{idx}", pwd, {f"u{idx}": bad_hash}) is False


def test_verify_user_credentials_runs_constant_time_scrypt_for_all_paths(monkeypatch: pytest.MonkeyPatch):
    calls: list[bytes] = []
    real_scrypt = hashlib.scrypt

    def spy_scrypt(password: bytes, *, salt: bytes, n: int, r: int, p: int, maxmem: int = 0, dklen: int = 64):
        calls.append(salt)
        return real_scrypt(password, salt=salt, n=n, r=r, p=p, maxmem=maxmem, dklen=dklen)

    monkeypatch.setattr(hashlib, "scrypt", spy_scrypt)
    scrypt_hash = real_scrypt(b"pw", salt=b"\x11" * 16, n=16384, r=8, p=1, maxmem=32 * 1024 * 1024, dklen=32)
    hashes = {
        "scrypt_user": f"scrypt$n=16384$r=8$p=1${'11' * 16}${scrypt_hash.hex()}",
        "legacy_user": hashlib.sha256(b"pw").hexdigest(),
        "bad_user": "corrupt-hash",
    }

    assert verify_user_credentials("scrypt_user", "pw", hashes) is True
    assert len(calls) == 1
    assert verify_user_credentials("legacy_user", "pw", hashes) is True
    assert len(calls) == 2
    assert verify_user_credentials("unknown_user", "pw", hashes) is False
    assert len(calls) == 3
    assert verify_user_credentials("bad_user", "pw", hashes) is False
    assert len(calls) == 4
    assert verify_user_credentials("", "pw", hashes) is False
    assert len(calls) == 5


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

    # 9b. GET /auth/verify unauthenticated browser check with Accept: text/html returns 307
    res = client.get(
        "/auth/verify",
        headers={"User-Agent": "hmrec-server-check/1.0", "Accept": "text/html"},
        follow_redirects=False,
    )
    assert res.status_code == 307
    assert res.headers["location"] == "/login"

    # 10. GET /auth/verify unauthenticated WebSocket endpoint returns 401
    res = client.get("/auth/verify", headers={"X-Forwarded-Uri": "/_stcore/stream"})
    assert res.status_code == 401

    # 11. GET /logout clears cookie and redirects to /login
    res = client.get("/logout", follow_redirects=False)
    assert res.status_code == 303
    assert res.headers["location"] == "/login"

    # 12. Brute-force protection returns 429 after MAX_LOGIN_ATTEMPTS failures
    for _ in range(MAX_LOGIN_ATTEMPTS):
        client.post(
            "/login",
            data={"username": "testuser", "password": "BadPassword"},
            headers={
                "Content-Type": "application/x-www-form-urlencoded",
                "X-Forwarded-For": "203.0.113.99",
            },
        )
    res_blocked = client.post(
        "/login",
        data={"username": "testuser", "password": pwd},
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "X-Forwarded-For": "203.0.113.99",
        },
    )
    assert res_blocked.status_code == 429
    assert "Demasiados intentos" in res_blocked.text


def test_login_rate_limiter_unit():
    assert MAX_TRACKED_IPS == 10_000
    limiter = LoginRateLimiter(max_attempts=3, window_seconds=60)
    assert limiter.is_blocked("198.51.100.1", now=100.0) is False
    limiter.record_failure("198.51.100.1", now=100.0)
    limiter.record_failure("198.51.100.1", now=110.0)
    assert limiter.is_blocked("198.51.100.1", now=120.0) is False
    limiter.record_failure("198.51.100.1", now=120.0)
    assert limiter.is_blocked("198.51.100.1", now=125.0) is True
    # Expires after window
    assert limiter.is_blocked("198.51.100.1", now=165.0) is False


def test_login_rate_limiter_sweep_and_lru_eviction():
    limiter = LoginRateLimiter(max_attempts=2, window_seconds=60, max_tracked_ips=3)
    limiter.record_failure("198.51.100.1", now=10.0)
    limiter.record_failure("198.51.100.2", now=20.0)
    limiter.record_failure("198.51.100.3", now=50.0)

    # Touch .1 via is_blocked? It has 1 failure (<2) and is not expired at t=55, so .1 becomes MRU and .2 is LRU
    assert limiter.is_blocked("198.51.100.1", now=55.0) is False
    limiter.record_failure("198.51.100.4", now=56.0)
    assert list(limiter._failures.keys()) == ["198.51.100.3", "198.51.100.1", "198.51.100.4"]

    # Expired sweep clears .3 and .1 before evicting .4 at t=118.0
    limiter.record_failure("198.51.100.5", now=118.0)
    assert "198.51.100.5" in limiter._failures
    assert len(limiter._failures) <= 3


def test_extract_client_ip_validation_and_normalization():
    assert extract_client_ip({"X-Forwarded-For": "203.0.113.99"}) == "203.0.113.99"
    assert extract_client_ip({"x-forwarded-for": "198.51.100.1, 203.0.113.99"}) == "203.0.113.99"
    assert extract_client_ip({"X-Forwarded-For": "garbage, 203.0.113.99:44321"}) == "203.0.113.99"
    assert extract_client_ip({"X-Forwarded-For": "2001:0db8:0000:0000:0000:0000:0000:0001"}) == "2001:db8::1"
    assert extract_client_ip({"X-Forwarded-For": "[2001:0db8::1]:8443"}) == "2001:db8::1"
    assert extract_client_ip({"X-Forwarded-For": "not-an-ip", "X-Real-IP": "198.51.100.77:8080"}) == "198.51.100.77"
    assert extract_client_ip({"X-Forwarded-For": "spoofed;drop table", "X-Real-IP": "bad"}, "testclient") == "testclient"
    assert extract_client_ip({"X-Forwarded-For": "fe80::1%eth0"}, "bad-host") == "unknown"


def test_load_user_hashes_stat_cache_and_no_hardcoded_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    assert "/data/hm_project" not in inspect.getsource(auth_module)
    auth_module._HASH_CACHE.clear()

    cfg_a = tmp_path / "gw_a.json"
    cfg_b = tmp_path / "gw_b.json"
    hash_a1 = hash_password("PasswordOne123456!")
    hash_a2 = hash_password("PasswordTwo123456!")
    hash_b = hash_password("PasswordThree1234!")
    cfg_a.write_text(json.dumps({"password_hashes": {"alice": hash_a1}}), encoding="utf-8")
    cfg_b.write_text(json.dumps({"password_hashes": {"bob": hash_b}}), encoding="utf-8")

    monkeypatch.setenv("HMREC_GATEWAY_CONFIG", str(cfg_a))
    read_count = 0
    orig_read_text = Path.read_text

    def spy_read_text(self: Path, *args, **kwargs):
        nonlocal read_count
        read_count += 1
        return orig_read_text(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", spy_read_text)
    assert load_user_hashes() == {"alice": hash_a1}
    assert load_user_hashes() == {"alice": hash_a1}
    assert read_count == 1

    # Mutating content + mtime invalidates cache
    cfg_a.write_text(json.dumps({"password_hashes": {"alice": hash_a2, "carol": hash_b}}), encoding="utf-8")
    st = cfg_a.stat()
    os.utime(cfg_a, ns=(st.st_atime_ns, st.st_mtime_ns + 1_000_000))
    assert load_user_hashes() == {"alice": hash_a2, "carol": hash_b}
    assert read_count == 2

    # Switching config path loads new file
    monkeypatch.setenv("HMREC_GATEWAY_CONFIG", str(cfg_b))
    assert load_user_hashes() == {"bob": hash_b}
    assert read_count == 3

    # Corrupt JSON returns empty dict safely
    bad_cfg = tmp_path / "bad.json"
    bad_cfg.write_text("{not-json", encoding="utf-8")
    monkeypatch.setenv("HMREC_GATEWAY_CONFIG", str(bad_cfg))
    assert load_user_hashes(bad_cfg) == {}


def test_session_secret_domain_separation_and_remember_durations(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    assert SHORT_SESSION_DURATION == 12 * 3600
    raw_token = "admin_bearer_secret_2026"
    monkeypatch.setenv("HM_APP_PASSWORD", raw_token)
    pwd = "SessionCheckPassword2026!"
    cfg_file = tmp_path / "gateway.json"
    cfg_file.write_text(
        json.dumps({"domain": "hm.test", "users": {"testuser": "bcrypt"}, "password_hashes": {"testuser": hash_password(pwd)}}),
        encoding="utf-8",
    )
    monkeypatch.setenv("HMREC_GATEWAY_CONFIG", str(cfg_file))

    app = create_app(admin_token=raw_token)
    client = TestClient(app)

    # remember=False -> 12h cookie max-age and 12h token exp
    before_short = int(time.time())
    res_short = client.post(
        "/login",
        data={"username": "testuser", "password": pwd},
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        follow_redirects=False,
    )
    assert res_short.status_code == 303
    set_cookie_short = res_short.headers["set-cookie"]
    assert f"Max-Age={SHORT_SESSION_DURATION}" in set_cookie_short
    tok_short = res_short.cookies[COOKIE_NAME]

    # Token must NOT verify under raw admin bearer secret; MUST verify under HMAC domain-separated secret
    assert verify_session_token(tok_short, raw_token.encode("utf-8")) is None
    derived_secret = hmac.new(raw_token.encode("utf-8"), b"hmrec:session-secret:v1", hashlib.sha256).digest()
    assert verify_session_token(tok_short, derived_secret) == "testuser"

    payload_short = json.loads(base64.urlsafe_b64decode(tok_short.split(".")[0].encode("ascii")).decode("utf-8"))
    assert before_short + SHORT_SESSION_DURATION - 5 <= payload_short["exp"] <= int(time.time()) + SHORT_SESSION_DURATION + 5

    # remember="1" -> 30d cookie max-age and 30d token exp
    before_long = int(time.time())
    res_long = client.post(
        "/login",
        data={"username": "testuser", "password": pwd, "remember": "1"},
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        follow_redirects=False,
    )
    assert res_long.status_code == 303
    assert f"Max-Age={DEFAULT_SESSION_DURATION}" in res_long.headers["set-cookie"]
    tok_long = res_long.cookies[COOKIE_NAME]
    payload_long = json.loads(base64.urlsafe_b64decode(tok_long.split(".")[0].encode("ascii")).decode("utf-8"))
    assert before_long + DEFAULT_SESSION_DURATION - 5 <= payload_long["exp"] <= int(time.time()) + DEFAULT_SESSION_DURATION + 5


def test_render_login_html_escapes_xss():
    malicious = '<script>alert("xss")</script>'
    rendered = render_login_html(error=malicious)
    assert "<script>" not in rendered
    assert "&lt;script&gt;" in rendered

