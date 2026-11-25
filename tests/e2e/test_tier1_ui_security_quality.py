"""Tier 1 isolated happy-path E2E tests for F_UI, F_SEC, and F_QUAL.

IMPORTANT: Zero top-level ``import hm_recommender.v2...`` or
``import hm_recommender.api.recommend_v2`` statements are allowed here.
"""

from __future__ import annotations

import base64
from datetime import date
import hashlib
import hmac
import importlib
import inspect
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import time as time_mod
from typing import Any
from unittest.mock import patch

from fastapi.testclient import TestClient
import httpx
import polars as pl
import pytest

from hm_recommender.api import auth
from hm_recommender.api.app import create_app
from hm_recommender.data.io import sha256
from hm_recommender.quality.contracts import load_config
from tests.e2e.harness import (
    MIGRATIONS_DIR,
    REPO_ROOT,
    FakeEventRegistry,
    InMemoryApiAdapter,
    build_synthetic_dataset,
    resolve_artifacts_dir,
    resolve_hm_v1_dir,
    start_uvicorn_app,
    ui_apptest_session,
    visible_text,
)

# ---------------------------------------------------------------------------
# Opaque-Box UI Helpers (Request Recording + Resilient Widget Discovery)
# ---------------------------------------------------------------------------


def _make_recording_transport(adapter: InMemoryApiAdapter):
    calls: list[dict[str, Any]] = []

    def _transport(request: httpx.Request) -> httpx.Response:
        body: Any = None
        if request.content:
            try:
                body = json.loads(request.content.decode("utf-8"))
            except Exception:
                body = request.content
        response = adapter.handle(request)
        resp_json: Any = None
        if response.headers.get("content-type", "").startswith("application/json"):
            try:
                resp_json = json.loads(response.content.decode("utf-8"))
            except Exception:
                resp_json = None
        calls.append(
            {
                "method": request.method.upper(),
                "path": request.url.path,
                "params": dict(request.url.params),
                "json": body,
                "status_code": response.status_code,
                "response_json": resp_json,
            }
        )
        return response

    return _transport, calls


def _find_control(
    app: Any,
    kinds: tuple[str, ...],
    *,
    label_re: str | None = None,
    key_re: str | None = None,
    option_re: str | None = None,
    exclude_key_re: str | None = None,
    exclude_label_re: str | None = None,
) -> Any | None:
    candidates: list[tuple[Any, str, str, list[str]]] = []
    for kind in kinds:
        try:
            widgets = getattr(app, kind)
        except Exception:
            continue
        for w in widgets:
            label_val = str(getattr(w, "label", "") or "")
            key_val = str(getattr(w, "key", "") or "")
            if exclude_key_re and re.search(exclude_key_re, key_val, re.I):
                continue
            if exclude_label_re and re.search(exclude_label_re, label_val, re.I):
                continue
            options_val = [str(o) for o in (getattr(w, "options", None) or [])]
            candidates.append((w, label_val, key_val, options_val))

    if key_re:
        for w, _label_val, key_val, _options_val in candidates:
            if re.search(key_re, key_val, re.I):
                return w
    if label_re:
        for w, label_val, _key_val, _options_val in candidates:
            if re.search(label_re, label_val, re.I):
                return w
    if option_re:
        for w, _label_val, _key_val, options_val in candidates:
            if any(re.search(option_re, opt, re.I) for opt in options_val):
                return w
    return None


def _find_buttons(
    app: Any,
    *,
    label_re: str | None = None,
    key_re: str | None = None,
) -> list[Any]:
    matches: list[Any] = []
    for btn in getattr(app, "button", []):
        label_val = str(getattr(btn, "label", "") or "")
        key_val = str(getattr(btn, "key", "") or "")
        if label_re and re.search(label_re, label_val, re.I):
            matches.append(btn)
        elif key_re and re.search(key_re, key_val, re.I):
            matches.append(btn)
    return matches


def _select_option_matching(ctrl: Any, pattern: str) -> str | None:
    options = list(getattr(ctrl, "options", []) or [])
    for opt in options:
        if re.search(pattern, str(opt), re.I):
            if hasattr(ctrl, "select"):
                ctrl.select(opt)
            elif hasattr(ctrl, "set_value"):
                ctrl.set_value(opt)
            return str(opt)
    return None


def _count_catalog_calls(calls: list[dict[str, Any]]) -> int:
    return sum(1 for c in calls if c["method"] == "GET" and c["path"] == "/catalog")


# ---------------------------------------------------------------------------
# A. F_UI — Real Shopper Streamlit UI (6 tests)
# ---------------------------------------------------------------------------


@pytest.mark.tier1
def test_t1_ui_cold_start_onboarding_preferences_personalizes_recommendations(
    synthetic_v2_release: tuple[Path, str],
    synthetic_image_zip: Path,
    e2e_image_cache_dir: Path,
) -> None:
    v2_dir, v2_id = synthetic_v2_release
    adapter = InMemoryApiAdapter(
        v2_dir,
        image_zip=synthetic_image_zip,
        image_cache_dir=e2e_image_cache_dir,
    )
    transport, calls = _make_recording_transport(adapter)

    with ui_apptest_session(transport, default_timeout=25) as app:
        assert not app.exception
        profile_sb = _find_control(app, ("selectbox",), key_re=r"^profile$")
        assert profile_sb is not None
        assert _select_option_matching(profile_sb, r"nuevo visitante") is not None
        app.run()
        assert not app.exception

        section_ctrl = _find_control(
            app,
            ("selectbox", "radio", "pills", "segmented_control"),
            label_re=r"sección|categor|departamento|inter[eé]s|preferencia",
            key_re=r"(?:onboarding|onboard|cold_start).*(?:section|group)|pref_group|onboarding_group",
            option_re=r"menswear|hombre",
            exclude_key_re=r"catalog|filter|^index_group_name$",
        )
        assert section_ctrl is not None, "Expected cold-start section onboarding control in UI"

        assert _select_option_matching(section_ctrl, r"menswear|hombre") is not None
        app.run()
        assert not app.exception

        age_ctrl = _find_control(
            app,
            ("selectbox", "radio", "pills", "segmented_control"),
            label_re=r"edad|rango",
            key_re=r"age_bin|pref_age|onboarding_age",
            option_re=r"<\s*25|menor(?:es)?\s+de\s+25|menos\s+de\s+25|hasta\s+24",
            exclude_key_re=r"catalog|filter",
        )
        assert age_ctrl is not None, "Expected cold-start age-bin onboarding control in UI"

        assert _select_option_matching(age_ctrl, r"<\s*25|menor(?:es)?\s+de\s+25|menos\s+de\s+25|hasta\s+24") is not None
        app.run()
        assert not app.exception

        refresh_btns = _find_buttons(app, key_re=r"^refresh$", label_re=r"actualizar recomendaciones")
        if refresh_btns:
            refresh_btns[0].click().run()
            assert not app.exception

        pref_calls = [
            c
            for c in calls
            if c["method"] == "POST"
            and c["path"].startswith("/streams/")
            and c["path"].endswith("/recommend")
            and isinstance(c["json"], dict)
            and c["json"].get("preferences")
        ]
        assert pref_calls, "Expected /streams/{id}/recommend payload with cold-start preferences"
        last_call = pref_calls[-1]
        prefs = last_call["json"]["preferences"]
        assert prefs.get("index_group_name") == "Menswear"
        assert prefs.get("age_bin") == "<25"

        resp_items = (last_call["response_json"] or {}).get("items", [])
        assert resp_items
        assert adapter.catalog_by_id[resp_items[0]["article_id"]]["index_group_name"] == "Menswear"

        rendered = visible_text(app)
        assert v2_id not in rendered
        assert re.search(r"TENDENCIA PARA TI|HOMBRE|MENSWEAR", rendered, re.I)


@pytest.mark.tier1
def test_t1_ui_shopper_actions_view_click_purchase_update_recommendations_live(
    synthetic_v2_release: tuple[Path, str],
    synthetic_image_zip: Path,
    e2e_image_cache_dir: Path,
) -> None:
    v2_dir, _ = synthetic_v2_release
    adapter = InMemoryApiAdapter(
        v2_dir,
        image_zip=synthetic_image_zip,
        image_cache_dir=e2e_image_cache_dir,
    )
    transport, calls = _make_recording_transport(adapter)

    with ui_apptest_session(transport, default_timeout=25) as app:
        assert not app.exception

        detail_btns = _find_buttons(app, label_re=r"ver\s+detalle|detalle", key_re=r"detail|view_")
        assert detail_btns, "Expected 'Ver detalle' action button on product cards"
        detail_btns[0].click().run()
        assert not app.exception
        assert "view" in {e["kind"] for e in adapter.store.events_log}

        rendered_after_view = visible_text(app)
        assert re.search(
            r"tailored|ladieswear|menswear|divided|baby|detalle|descripción|estampado|sección",
            rendered_after_view,
            re.I,
        )

        calls.clear()
        fav_btns = _find_buttons(app, label_re=r"favorit|♡|me gusta", key_re=r"fav|click_|like_")
        assert fav_btns, "Expected 'Favorito' action button on product cards"
        fav_btns[0].click().run()
        assert not app.exception
        assert "click" in {e["kind"] for e in adapter.store.events_log}
        click_rec_calls = [
            c
            for c in calls
            if c["method"] == "POST" and c["path"].startswith("/streams/") and c["path"].endswith("/recommend")
        ]
        assert click_rec_calls, "Expected live recommendation refresh after Favorito (click) event"

        calls.clear()
        buy_btns = _find_buttons(app, label_re=r"comprar|simular compra", key_re=r"buy|purchase")
        assert buy_btns, "Expected 'Comprar' action button on product cards"
        buy_btns[0].click().run()
        assert not app.exception
        assert "purchase" in {e["kind"] for e in adapter.store.events_log}

        rec_calls = [
            c
            for c in calls
            if c["method"] == "POST" and c["path"].startswith("/streams/") and c["path"].endswith("/recommend")
        ]
        assert rec_calls, "Expected live recommendation refresh after purchase event"


@pytest.mark.tier1
def test_t1_ui_mode_toggle_balanced_vs_discovery_hides_purchased_and_dedups_variants(
    synthetic_v2_release: tuple[Path, str],
    synthetic_image_zip: Path,
    e2e_image_cache_dir: Path,
) -> None:
    v2_dir, _ = synthetic_v2_release
    adapter = InMemoryApiAdapter(
        v2_dir,
        image_zip=synthetic_image_zip,
        image_cache_dir=e2e_image_cache_dir,
    )
    transport, calls = _make_recording_transport(adapter)

    with ui_apptest_session(transport, default_timeout=25) as app:
        assert not app.exception
        profile_sb = _find_control(app, ("selectbox",), key_re=r"^profile$")
        assert profile_sb is not None
        assert _select_option_matching(profile_sb, r"comprador frecuente") is not None
        app.run()
        assert not app.exception

        mode_ctrl = _find_control(
            app,
            ("radio", "segmented_control", "pills", "selectbox", "toggle"),
            label_re=r"modo|descubrimiento|equilibrado",
            key_re=r"^mode$|rec_mode|discovery",
            option_re=r"solo\s+descubrimiento|descubrimiento",
        )
        assert mode_ctrl is not None, "Expected mode toggle ('Equilibrado' vs 'Solo descubrimiento') in UI"

        balanced_calls = [
            c
            for c in calls
            if c["method"] == "POST"
            and c["path"].endswith("/recommend")
            and isinstance(c["json"], dict)
            and c["json"].get("mode", "balanced") == "balanced"
        ]
        assert balanced_calls

        calls.clear()
        if hasattr(mode_ctrl, "options") and getattr(mode_ctrl, "options", None):
            assert _select_option_matching(mode_ctrl, r"solo\s+descubrimiento|descubrimiento") is not None
        elif hasattr(mode_ctrl, "set_value"):
            mode_ctrl.set_value(True)
        app.run()
        assert not app.exception

        disc_calls = [
            c
            for c in calls
            if c["method"] == "POST"
            and c["path"].endswith("/recommend")
            and isinstance(c["json"], dict)
            and c["json"].get("mode") == "discovery"
        ]
        assert disc_calls, "Expected POST /streams/{id}/recommend with mode='discovery'"
        disc_items = (disc_calls[-1]["response_json"] or {}).get("items", [])
        assert disc_items

        hist_df = pl.read_parquet(v2_dir / "histories.parquet")
        purchased_cust0 = set(hist_df.filter(pl.col("customer_id") == "cust_0000")["article_id"].to_list())
        disc_ids = [it["article_id"] for it in disc_items]
        assert purchased_cust0.isdisjoint(disc_ids)
        pcodes = [adapter.catalog_by_id[aid]["product_code"] for aid in disc_ids]
        assert len(pcodes) == len(set(pcodes))


@pytest.mark.tier1
def test_t1_ui_catalog_search_filters_single_fetch_and_full_10_digit_ids(
    synthetic_v2_release: tuple[Path, str],
    synthetic_image_zip: Path,
    e2e_image_cache_dir: Path,
) -> None:
    v2_dir, _ = synthetic_v2_release
    adapter = InMemoryApiAdapter(
        v2_dir,
        image_zip=synthetic_image_zip,
        image_cache_dir=e2e_image_cache_dir,
    )
    transport, calls = _make_recording_transport(adapter)

    with ui_apptest_session(transport, default_timeout=25) as app:
        assert not app.exception
        assert _count_catalog_calls(calls) == 1, f"Expected single GET /catalog on initial render, got {calls}"

        initial_text = visible_text(app)
        assert re.search(r"\b[0-9]{10}\b", initial_text), "Catalog must display full 10-digit article IDs"
        assert not re.search(r"ART\.\s*[0-9]{8}\b", initial_text), "Truncated 8-digit article IDs must not appear"

        search_input = _find_control(
            app,
            ("text_input",),
            label_re=r"buscar|búsqueda|palabra|catálogo|search",
            key_re=r"search|query|catalog_q|^q$",
        )
        assert search_input is not None, "Expected catalog keyword search input in UI"

        calls.clear()
        search_input.input("Dress").run()
        assert not app.exception

        q_calls = [
            c
            for c in calls
            if c["method"] == "GET" and c["path"] == "/catalog" and c["params"].get("q")
        ]
        assert q_calls, "Expected GET /catalog with non-empty 'q' query parameter"
        assert q_calls[-1]["params"]["q"].lower() == "dress"

        search_input.input("").run()
        assert not app.exception

        section_filter = _find_control(
            app,
            ("selectbox", "radio", "pills", "segmented_control"),
            key_re=r"catalog_.*(?:section|group|index_group)|filter_.*(?:section|group)|^index_group_name$|^catalog_section$",
            label_re=r"sección|categor[ií]a|grupo|departamento",
            option_re=r"ladieswear|menswear|mujer|hombre",
            exclude_key_re=r"onboarding|pref_group|profile",
        )
        assert section_filter is not None, "Expected catalog index_group_name section filter control in UI"

        calls.clear()
        assert _select_option_matching(section_filter, r"ladieswear|mujer") is not None
        app.run()
        assert not app.exception

        group_calls = [
            c
            for c in calls
            if c["method"] == "GET"
            and c["path"] == "/catalog"
            and c["params"].get("index_group_name")
        ]
        assert group_calls, "Expected GET /catalog with index_group_name query parameter after section filter"
        assert group_calls[-1]["params"]["index_group_name"] == "Ladieswear"


@pytest.mark.tier1
def test_t1_ui_explainable_badges_and_session_activity_panel(
    synthetic_v2_release: tuple[Path, str],
    synthetic_image_zip: Path,
    e2e_image_cache_dir: Path,
) -> None:
    v2_dir, _ = synthetic_v2_release
    adapter = InMemoryApiAdapter(
        v2_dir,
        image_zip=synthetic_image_zip,
        image_cache_dir=e2e_image_cache_dir,
    )
    transport, _ = _make_recording_transport(adapter)

    with ui_apptest_session(transport, default_timeout=25) as app:
        assert not app.exception
        profile_sb = _find_control(app, ("selectbox",), key_re=r"^profile$")
        assert profile_sb is not None
        assert _select_option_matching(profile_sb, r"comprador frecuente") is not None
        app.run()
        assert not app.exception

        rendered = visible_text(app)
        assert re.search(
            r"\b(?:OTRO COLOR|COMB[ÍI]NALO|COMPRADO JUNTO|ESTILO AF[ÍI]N|TU ESTILO|ESTILO SIMILAR|M[ÁA]S COMO ESTE|PRENDA SIMILAR|SIMILAR|TENDENCIA PARA TI|POPULAR EN TU PERFIL|TU HISTORIAL)\b",
            rendered,
            re.I,
        ), "Expected v2 explainable reason badges in UI"
        assert re.search(
            r"actividad en (esta|la) sesión|vistos|favoritos|comprados",
            rendered,
            re.I,
        ), "Expected session activity panel in UI"


@pytest.mark.tier1
def test_t1_ui_image_rendering_and_pagination_single_catalog_call(
    synthetic_v2_release: tuple[Path, str],
    synthetic_image_zip: Path,
    e2e_image_cache_dir: Path,
) -> None:
    v2_dir, _ = synthetic_v2_release
    adapter = InMemoryApiAdapter(
        v2_dir,
        image_zip=synthetic_image_zip,
        image_cache_dir=e2e_image_cache_dir,
    )
    transport, calls = _make_recording_transport(adapter)

    with ui_apptest_session(transport, default_timeout=25) as app:
        assert not app.exception
        assert len(app.get("image")) > 0

        next_btns = _find_buttons(app, key_re=r"btn_next_page|next_page", label_re=r"siguiente")
        assert next_btns
        calls.clear()
        next_btns[0].click().run()
        assert not app.exception

        cat_calls = [c for c in calls if c["method"] == "GET" and c["path"] == "/catalog"]
        assert len(cat_calls) <= 1, f"Expected at most 1 GET /catalog on page 2, got {len(cat_calls)}"
        if cat_calls:
            assert cat_calls[0]["params"].get("offset") == "12"


# ---------------------------------------------------------------------------
# B. F_SEC — Security Portal Hardening & Gateway (6 tests)
# ---------------------------------------------------------------------------


@pytest.mark.tier1
def test_t1_sec_scrypt_login_cookie_auth_verify_and_logout_flow(
    gateway_config_factory,
    fake_registry: FakeEventRegistry,
    tmp_path: Path,
) -> None:
    cfg = gateway_config_factory({"shopper1": "ScryptStrongPass2026!"}, hash_mode="scrypt")
    cfg_data = json.loads(cfg.read_text(encoding="utf-8"))
    assert cfg_data["password_hashes"]["shopper1"].startswith("scrypt$")

    app = create_app(fake_registry, admin_token="adm-tok-1", input_root=str(tmp_path))
    client = TestClient(app)

    login_get = client.get("/login")
    assert login_get.status_code == 200
    for hdr, expected in auth.SECURITY_HEADERS.items():
        assert login_get.headers.get(hdr.lower()) == expected

    login_post = client.post(
        "/login",
        data={"username": "shopper1", "password": "ScryptStrongPass2026!", "remember": "1"},
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        follow_redirects=False,
    )
    assert login_post.status_code == 303
    assert login_post.headers["location"] == "/"

    set_cookie = login_post.headers["set-cookie"]
    assert f"{auth.COOKIE_NAME}=" in set_cookie
    assert "HttpOnly" in set_cookie
    assert "SameSite=lax" in set_cookie
    assert "Secure" in set_cookie
    assert f"Max-Age={auth.DEFAULT_SESSION_DURATION}" in set_cookie

    client.cookies.set(auth.COOKIE_NAME, login_post.cookies[auth.COOKIE_NAME])
    assert client.get("/auth/verify").status_code == 200

    logout = client.get("/logout", follow_redirects=False)
    assert logout.status_code == 303
    assert logout.headers["location"] == "/login"
    assert f"{auth.COOKIE_NAME}=" in logout.headers["set-cookie"]
    assert "Max-Age=0" in logout.headers["set-cookie"]


@pytest.mark.tier1
def test_t1_sec_legacy_sha256_transition_login_and_basic_auth(
    gateway_config_factory,
    fake_registry: FakeEventRegistry,
    tmp_path: Path,
) -> None:
    legacy_pwd = "LegacyPass2026!"
    scrypt_pwd = "ScryptPass2026!"
    cfg_path = gateway_config_factory(
        {"legacy": legacy_pwd},
        hash_mode="sha256",
        filename="gateway_transition.json",
    )
    data = json.loads(cfg_path.read_text(encoding="utf-8"))
    data["password_hashes"]["scrypt_u"] = auth.hash_password(scrypt_pwd)
    cfg_path.write_text(json.dumps(data), encoding="utf-8")

    app = create_app(fake_registry, admin_token="adm-tok-trans", input_root=str(tmp_path))
    client = TestClient(app)

    res_legacy = client.post(
        "/login",
        data={"username": "legacy", "password": legacy_pwd},
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        follow_redirects=False,
    )
    assert res_legacy.status_code == 303

    res_scrypt = client.post(
        "/login",
        data={"username": "scrypt_u", "password": scrypt_pwd},
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        follow_redirects=False,
    )
    assert res_scrypt.status_code == 303

    basic_legacy = "Basic " + base64.b64encode(f"legacy:{legacy_pwd}".encode("utf-8")).decode("ascii")
    assert client.get("/auth/verify", headers={"Authorization": basic_legacy}).status_code == 200


@pytest.mark.tier1
def test_t1_sec_hmac_domain_separated_secret_and_remember_false_12h(
    gateway_config_factory,
    fake_registry: FakeEventRegistry,
    tmp_path: Path,
) -> None:
    gateway_config_factory({"alice": "DomainSecretPass2026!"}, hash_mode="scrypt")
    raw_secret = "raw-admin-secret-2026"
    app = create_app(fake_registry, admin_token=raw_secret, input_root=str(tmp_path))
    client = TestClient(app)

    now = int(time_mod.time())
    short_res = client.post(
        "/login",
        data={"username": "alice", "password": "DomainSecretPass2026!"},
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        follow_redirects=False,
    )
    assert short_res.status_code == 303
    assert f"Max-Age={auth.SHORT_SESSION_DURATION}" in short_res.headers["set-cookie"]

    tok = short_res.cookies[auth.COOKIE_NAME]
    assert auth.verify_session_token(tok, raw_secret.encode("utf-8")) is None
    derived_secret = hmac.new(raw_secret.encode("utf-8"), b"hmrec:session-secret:v1", hashlib.sha256).digest()
    assert auth.verify_session_token(tok, derived_secret) == "alice"

    payload_b64 = tok.split(".", 1)[0]
    payload = json.loads(base64.urlsafe_b64decode(payload_b64.encode("ascii")).decode("utf-8"))
    assert now + 43190 <= payload["exp"] <= now + 43210

    long_res = client.post(
        "/login",
        data={"username": "alice", "password": "DomainSecretPass2026!", "remember": "on"},
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        follow_redirects=False,
    )
    assert long_res.status_code == 303
    assert f"Max-Age={auth.DEFAULT_SESSION_DURATION}" in long_res.headers["set-cookie"]


@pytest.mark.tier1
def test_t1_sec_server_gateway_configure_caddyfile_and_stat_cache_reload(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scripts_dir = str(REPO_ROOT / "scripts")
    monkeypatch.syspath_prepend(scripts_dir)
    server_gateway = importlib.import_module("server_gateway")

    with patch.object(server_gateway, "run", return_value="$2a$14$" + ("A" * 53)):
        server_gateway.configure(
            tmp_path,
            "hm.example.com",
            "alice",
            password="CorrectHorseBattery2026!",
        )

    gw_json = tmp_path / ".runtime" / "server" / "gateway.json"
    caddyfile_path = tmp_path / ".runtime" / "server" / "Caddyfile"
    assert gw_json.is_file()
    assert caddyfile_path.is_file()
    assert stat.S_IMODE(gw_json.stat().st_mode) == 0o600

    gw_data = json.loads(gw_json.read_text(encoding="utf-8"))
    assert gw_data["password_hashes"]["alice"].startswith("scrypt$")

    caddy_txt = caddyfile_path.read_text(encoding="utf-8")
    assert "path /login /logout" in caddy_txt
    assert "reverse_proxy api:8000" in caddy_txt
    assert "forward_auth api:8000" in caddy_txt
    assert "uri /auth/verify" in caddy_txt
    assert "reverse_proxy ui:8501" in caddy_txt
    assert "header_up -Authorization" in caddy_txt

    monkeypatch.setenv("HMREC_GATEWAY_CONFIG", str(gw_json))
    with auth._HASH_CACHE_LOCK:
        auth._HASH_CACHE.clear()

    loaded_1 = auth.load_user_hashes()
    assert list(loaded_1.keys()) == ["alice"]

    with patch.object(server_gateway, "run", return_value="$2a$14$" + ("B" * 53)):
        server_gateway.configure(
            tmp_path,
            "hm.example.com",
            "bob",
            password="SecondHorseBattery2026!",
        )
    st = gw_json.stat()
    os.utime(gw_json, ns=(st.st_atime_ns, st.st_mtime_ns + 1_000_000))

    loaded_2 = auth.load_user_hashes()
    assert set(loaded_2.keys()) == {"alice", "bob"}
    assert auth.verify_user_credentials("bob", "SecondHorseBattery2026!", loaded_2) is True


@pytest.mark.tier1
def test_t1_sec_auth_verify_routing_browser_307_vs_api_and_stcore_401(
    gateway_config_factory,
    fake_registry: FakeEventRegistry,
    tmp_path: Path,
) -> None:
    gateway_config_factory({"alice": "VerifyRoutingPass2026!"}, hash_mode="scrypt")
    app = create_app(fake_registry, admin_token="adm-tok-route", input_root=str(tmp_path))
    client = TestClient(app)

    browser = client.get("/auth/verify", headers={"Accept": "text/html"}, follow_redirects=False)
    assert browser.status_code == 307
    assert browser.headers["location"] == "/login"

    api_json = client.get("/auth/verify", headers={"Accept": "application/json"}, follow_redirects=False)
    assert api_json.status_code == 401
    assert 'Basic realm="restricted"' in api_json.headers.get("www-authenticate", "")

    stcore_stream = client.get(
        "/auth/verify",
        headers={"Accept": "text/html", "X-Forwarded-Uri": "/_stcore/stream"},
        follow_redirects=False,
    )
    assert stcore_stream.status_code == 401

    stcore_health = client.get(
        "/auth/verify",
        headers={"X-Forwarded-Uri": "/_stcore/health"},
        follow_redirects=False,
    )
    assert stcore_health.status_code == 401


@pytest.mark.tier1
def test_t1_sec_uvicorn_live_auth_and_admin_bearer_isolation(
    gateway_config_factory,
    fake_registry: FakeEventRegistry,
    tmp_path: Path,
) -> None:
    gateway_config_factory({"liveuser": "UvicornLivePass2026!"}, hash_mode="scrypt")
    input_root = tmp_path / "input"
    input_root.mkdir(parents=True, exist_ok=True)
    app = create_app(fake_registry, admin_token="adm-live-2026", input_root=str(input_root))

    with start_uvicorn_app(app, lifespan="on") as base_url:
        with httpx.Client(base_url=base_url, trust_env=False) as client:
            login = client.post(
                "/login",
                data={"username": "liveuser", "password": "UvicornLivePass2026!"},
                headers={"Content-Type": "application/x-www-form-urlencoded"},
                follow_redirects=False,
            )
            assert login.status_code == 303
            cookie_val = login.cookies[auth.COOKIE_NAME]
            verify = client.get("/auth/verify", cookies={auth.COOKIE_NAME: cookie_val})
            assert verify.status_code == 200

            no_auth = client.post("/admin/releases/prepare", json={"directory": "missing"})
            assert no_auth.status_code == 403

            wrong_auth = client.post(
                "/admin/releases/prepare",
                headers={"Authorization": "Bearer wrong-token"},
                json={"directory": "missing"},
            )
            assert wrong_auth.status_code == 403

            valid_auth = client.post(
                "/admin/releases/prepare",
                headers={"Authorization": "Bearer adm-live-2026"},
                json={"directory": "missing"},
            )
            assert valid_auth.status_code != 403


# ---------------------------------------------------------------------------
# C. F_QUAL — Quality v2, Docs Links, Host Tests & Repo Integrity (6 tests)
# ---------------------------------------------------------------------------


@pytest.mark.tier1
def test_t1_qual_check_docs_links_zero_broken() -> None:
    proc = subprocess.run(
        [sys.executable, str(REPO_ROOT / "scripts" / "check_docs_links.py"), "--root", str(REPO_ROOT), "--json"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, f"check_docs_links failed:\nstdout={proc.stdout}\nstderr={proc.stderr}"
    report = json.loads(proc.stdout)
    assert report["broken_count"] == 0, f"Broken links detected: {report['broken_links']}"
    assert report["broken_links"] == []
    assert report["files_checked"] >= 42
    assert report["local_links_checked"] >= 340
    assert report["include_archives"] is False


@pytest.mark.tier1
def test_t1_qual_host_unittest_suite_passes() -> None:
    env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1", "TMPDIR": "/tmp"}
    proc = subprocess.run(
        [sys.executable, "-m", "unittest", "discover", "-s", "tests/host", "-v"],
        cwd=str(REPO_ROOT),
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    combined = proc.stdout + "\n" + proc.stderr
    assert proc.returncode == 0, f"tests/host failed:\n{combined}"
    ran_match = re.search(r"Ran (\d+) tests?", combined)
    assert ran_match is not None and int(ran_match.group(1)) >= 23
    assert re.search(r"\bOK\b", combined)


@pytest.mark.tier1
def test_t1_qual_frozen_v1_migrations_archives_and_artifacts_intact() -> None:
    q1_path = REPO_ROOT / "configs" / "quality" / "quality-v1.json"
    load_config(q1_path)
    assert sha256(q1_path) == "d4383b6bfd899e0be8abfee7e28ee42533a9121d0565fdd6795d8fdf86e3e500"

    expected_migrations = {
        "001_bootstrap.sql": "04e94dace91a944eb46b8dc14de0482596a0f6f7a25261f0420f1db1b07b9b83",
        "002_releases.sql": "a1bec8206c237a41b81cdabc54f46a4b2e7f784a8be766174b62558b20a7925d",
        "003_events.sql": "3f95e9807c27dc9dd8403e9215c763305fdbd36d53ba6ca31ec7c836f331e71a",
        "004_pipeline.sql": "ec4698f41fe3055be8d5a4085029effc370f925102499c2ab40e932b4275dda3",
    }
    for fname, expected_digest in expected_migrations.items():
        fpath = MIGRATIONS_DIR / fname
        assert fpath.is_file()
        assert sha256(fpath) == expected_digest

    archive_root = REPO_ROOT / "docs" / "archive"
    for sub in ("evidence", "prompts", "legacy_scripts", "legacy_tests"):
        sub_dir = archive_root / sub
        assert sub_dir.is_dir() and any(sub_dir.iterdir()), f"Empty archive dir: {sub_dir}"
    for key_file in (
        "local-quality-q1.json",
        "local-quality-q2.json",
        "local-quality-q3.json",
        "local-quality-q4.json",
        "quality-decision-mac24.json",
        "final-review-mac24.json",
    ):
        assert (archive_root / "evidence" / key_file).is_file(), f"Missing frozen evidence {key_file}"

    hm_v1 = resolve_hm_v1_dir()
    assert hm_v1 is not None
    hm_manifest = json.loads((hm_v1 / "manifest.json").read_text(encoding="utf-8"))
    assert hm_manifest["counts"]["articles"] == 105542
    assert hm_manifest["counts"]["customers"] == 1371980
    assert hm_manifest["counts"]["transactions"] == 31788324

    artifacts = resolve_artifacts_dir()
    if artifacts is not None:
        diag = artifacts / "diagnostics"
        for name in ("q1-full-V1-status.json", "q1-full-V2-status.json", "q1-full-V3-status.json", "q4-status.json"):
            assert (diag / name).is_file(), f"Missing frozen diagnostic {name}"
        q4 = json.loads((diag / "q4-status.json").read_text(encoding="utf-8"))
        assert q4["status"] == "passed"
        assert (artifacts / "quality-budget" / "quality-v1.json").is_file()
        assert (artifacts / ".final-test-once.json").is_file()
        assert (artifacts / ".final-test-continuation.json").is_file()
        failed_logs = list((artifacts / "final-private").glob("*/failure-private.log"))
        assert len(failed_logs) >= 2
        eval_results = list((artifacts / "final-private").glob("*/evaluation-result.json"))
        assert eval_results
        assert list((artifacts / "serving").glob("h-*"))


@pytest.mark.tier1
def test_t1_qual_quality_v2_config_and_benchmark_gates() -> None:
    q2_path = REPO_ROOT / "configs" / "quality" / "quality-v2.json"
    assert q2_path.is_file(), "Expected configs/quality/quality-v2.json"
    q2 = json.loads(q2_path.read_text(encoding="utf-8"))
    assert q2.get("protocol") == "quality-v2"

    raw_folds = q2.get("folds", {})
    if isinstance(raw_folds, list):
        if raw_folds and all(isinstance(item, dict) for item in raw_folds):
            folds = {
                str(item.get("fold_id") or item.get("id") or item.get("name")): (
                    item.get("cutoff") or item.get("date")
                )
                for item in raw_folds
            }
        elif len(raw_folds) == 3 and all(isinstance(item, str) for item in raw_folds):
            folds = {"V1": raw_folds[0], "V2": raw_folds[1], "V3": raw_folds[2]}
        else:
            folds = {}
    else:
        folds = {
            str(k): (v.get("cutoff") if isinstance(v, dict) else v)
            for k, v in dict(raw_folds).items()
        }
    assert folds.get("V1") == "2020-08-26"
    assert folds.get("V2") == "2020-09-02"
    assert folds.get("V3") == "2020-09-09"
    test_cutoff = q2.get("test_cutoff") or (q2.get("descriptive_test_window") or {}).get("cutoff")
    assert test_cutoff == "2020-09-16"

    selection = q2.get("selection") or q2.get("gates") or q2
    assert selection.get("min_segment_users", q2.get("min_segment_users")) == 1000
    max_seg_loss = selection.get(
        "max_segment_loss",
        selection.get("max_segment_relative_loss", q2.get("max_segment_loss", q2.get("max_segment_relative_loss"))),
    )
    assert max_seg_loss is not None and float(max_seg_loss) <= 0.02

    artifacts = resolve_artifacts_dir()
    assert artifacts is not None, "Expected /artifacts mount to inspect quality-v2 benchmark results"
    candidates = [
        *(sorted((artifacts / "quality-v2").glob("*.json")) if (artifacts / "quality-v2").is_dir() else []),
        *(sorted((artifacts / "diagnostics").glob("*quality-v2*.json")) if (artifacts / "diagnostics").is_dir() else []),
        *(sorted((artifacts / "diagnostics").glob("*v2*.json")) if (artifacts / "diagnostics").is_dir() else []),
        *(sorted((artifacts / "serving").glob("v2-*/validation-reference.json")) if (artifacts / "serving").is_dir() else []),
        *(sorted((artifacts / "releases-v2").glob("v2-*/validation-reference.json")) if (artifacts / "releases-v2").is_dir() else []),
    ]
    assert candidates, "Expected quality-v2 benchmark JSON artifact under /artifacts"

    valid_reports: list[dict[str, Any]] = []
    for cand in candidates:
        data = json.loads(cand.read_text(encoding="utf-8"))
        if not isinstance(data, dict) or data.get("metrics_measured_for_this_package") is False:
            continue
        sum_v1_v3 = data.get("summary_v1_v3")
        cand_summary_v2 = sum_v1_v3.get("v2") if isinstance(sum_v1_v3, dict) else None
        cand_summary = data.get("summary", data)
        if isinstance(cand_summary_v2, dict) and "map_at_12" in cand_summary_v2:
            valid_reports.append(data)
        elif isinstance(cand_summary, dict) and (
            "mean_map_at_12" in cand_summary or "map_at_12" in cand_summary
        ):
            valid_reports.append(data)
    report: dict[str, Any] | None = next(
        (
            r
            for r in valid_reports
            if r.get("protocol") == "quality-v2"
            or (isinstance(r.get("summary_v1_v3"), dict) and isinstance(r["summary_v1_v3"].get("v2"), dict))
        ),
        valid_reports[0] if valid_reports else None,
    )
    assert report is not None, f"No valid quality-v2 benchmark summary found in {candidates}"

    if isinstance(report.get("summary_v1_v3"), dict) and isinstance(report["summary_v1_v3"].get("v2"), dict):
        v2_sum = report["summary_v1_v3"]["v2"]
        mean_map = float(v2_sum["map_at_12"])
        assert mean_map > 0.022225, f"v2 MAP@12 ({mean_map}) must strictly beat H (0.022225)"
        assert mean_map > 0.025444, f"v2 MAP@12 ({mean_map}) must strictly beat RG (0.025444)"
        assert mean_map >= 0.031, f"v2 MAP@12 ({mean_map}) must meet >= 0.031 acceptance target"

        disc_map = float(v2_sum["discovery_map_at_12"])
        assert disc_map > 0.006437, f"v2 Discovery MAP ({disc_map}) must exceed P discovery (0.006437)"

        desc_test = report.get("descriptive_test_2020_09_16") or {}
        test_map = float(((desc_test.get("v2") or {}).get("metrics") or {}).get("map_at_12", 0.0))
        assert test_map > 0.026285, f"2020-09-16 MAP@12 ({test_map}) must beat H (0.026285)"

        gates = report.get("gates") or {}
        seg_checks = gates.get("segment_checks")
        assert isinstance(seg_checks, list) and len(seg_checks) > 0
        assert all(
            int(seg["users"]) < 1000 or (float(seg["relative_diff"]) >= -0.02 and seg["passed"] is True)
            for seg in seg_checks
        )
        assert (gates.get("checks") or {}).get("zero_segment_regressions") is True
        assert gates.get("all_required_gates_passed") is True
    else:
        summary = report.get("summary", report)
        mean_map = float(summary.get("mean_map_at_12", summary.get("map_at_12", 0.0)))
        assert mean_map > 0.022225, f"v2 MAP@12 ({mean_map}) must strictly beat H (0.022225)"
        assert mean_map > 0.025444, f"v2 MAP@12 ({mean_map}) must strictly beat RG (0.025444)"
        assert mean_map >= 0.031, f"v2 MAP@12 ({mean_map}) must meet >= 0.031 acceptance target"

        disc_map = float(summary.get("discovery_map_at_12", summary.get("discovery_map", 0.0)))
        assert disc_map > 0.006437, f"v2 Discovery MAP ({disc_map}) must exceed P discovery (0.006437)"

        test_2020_09_16 = summary.get("test_2020_09_16", report.get("test_2020_09_16", {}))
        test_map = float(test_2020_09_16.get("map_at_12", summary.get("test_map_at_12", 0.0)))
        assert test_map > 0.026285, f"2020-09-16 MAP@12 ({test_map}) must beat H (0.026285)"

        assert (
            "regressing_segments_over_2pct" in summary
            or "regressing_segments" in summary
            or "segments" in summary
        ), "Expected explicit segment regression field (e.g. regressing_segments_over_2pct) in quality-v2 summary"
        if "regressing_segments_over_2pct" in summary:
            regressing_segments = summary["regressing_segments_over_2pct"]
            assert isinstance(regressing_segments, list) and len(regressing_segments) == 0
        elif "regressing_segments" in summary:
            regressing_segments = summary["regressing_segments"]
            assert isinstance(regressing_segments, list) and len(regressing_segments) == 0
        else:
            segs = summary["segments"]
            assert isinstance(segs, list) and len(segs) > 0
            assert all(
                int(seg["users"]) < 1000 or float(seg["rel_delta"]) >= -0.02
                for seg in segs
            )


@pytest.mark.tier1
def test_t1_qual_docs_recommender_v2_and_readme_published() -> None:
    readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
    assert "pytest tests/unit" in readme
    assert "unittest discover -s tests/host" in readme
    assert "check_docs_links.py" in readme
    assert "v2" in readme

    doc_v2 = REPO_ROOT / "docs" / "recommender-v2.md"
    assert doc_v2.is_file(), "Expected docs/recommender-v2.md benchmark report"
    content = doc_v2.read_text(encoding="utf-8")
    assert len(content.strip()) > 100
    for token in ("quality-v2", "V1", "V2", "V3", "2020-09-16", "40 ms"):
        assert token.lower() in content.lower(), f"docs/recommender-v2.md missing {token!r}"

    scripts_dir = str(REPO_ROOT / "scripts")
    if scripts_dir not in sys.path:
        sys.path.insert(0, scripts_dir)
    check_docs_links = importlib.import_module("check_docs_links")
    _links, broken = check_docs_links.check_file_links(doc_v2, REPO_ROOT)
    assert broken == [], f"Broken links in docs/recommender-v2.md: {broken}"


@pytest.mark.tier1
def test_t1_qual_synthetic_v2_offline_evaluation_pipeline(tmp_path: Path) -> None:
    v2_eval = importlib.import_module("hm_recommender.v2.evaluate")
    v2_ranker = importlib.import_module("hm_recommender.v2.ranker")
    v2_retrieval = importlib.import_module("hm_recommender.v2.retrieval")

    cutoff = date(2020, 9, 9)
    _, meta = build_synthetic_dataset(tmp_path / "eval_ds", cutoff=cutoff)
    base_tx: pl.DataFrame = meta["transactions_df"]
    articles_df: pl.DataFrame = meta["articles_df"]
    customers_df: pl.DataFrame = meta["customers_df"]
    aids = meta["article_ids"]

    compute_metrics = getattr(v2_eval, "compute_buyer_metrics", None)
    assert callable(compute_metrics), "Expected compute_buyer_metrics in hm_recommender.v2.evaluate"
    metrics_res = compute_metrics([aids[0], aids[1], aids[2]], {aids[0], aids[2]}, ks=(200, 300))
    assert metrics_res["map_at_12"] > 0.0
    assert metrics_res["ndcg_at_12"] > 0.0
    assert metrics_res["recall_at_200"] > 0.0
    assert metrics_res["recall_at_300"] > 0.0

    # Augment synthetic transactions across 2 sliding training weeks (< cutoff) + eval week (>= cutoff)
    extra_rows = [
        # Pre-V2 history (< 2020-08-26) so all items & warm users exist before dist=2
        (date(2020, 8, 10), "cust_0000", aids[0], 29.99, 2),
        (date(2020, 8, 12), "cust_0000", aids[1], 29.99, 2),
        (date(2020, 8, 14), "cust_0001", aids[2], 39.99, 2),
        (date(2020, 8, 15), "cust_0004", aids[0], 24.99, 2),
        # Training week dist=2: [2020-08-26, 2020-09-02)
        (date(2020, 8, 28), "cust_0000", aids[0], 29.99, 2),
        (date(2020, 8, 29), "cust_0001", aids[2], 39.99, 2),
        (date(2020, 8, 30), "cust_0004", aids[0], 24.99, 2),
        # Validation week dist=1: [2020-09-02, 2020-09-09)
        (date(2020, 9, 4), "cust_0000", aids[0], 29.99, 2),
        (date(2020, 9, 5), "cust_0001", aids[2], 39.99, 2),
        (date(2020, 9, 6), "cust_0004", aids[1], 24.99, 2),
        # Evaluation window [2020-09-09, 2020-09-16) including warm + cold buyers
        (date(2020, 9, 10), "cust_0000", aids[0], 29.99, 2),
        (date(2020, 9, 11), "cust_0001", aids[2], 39.99, 2),
        (date(2020, 9, 12), meta["cold_start_buyer"], aids[0], 24.99, 2),
    ]
    extra_df = pl.DataFrame(
        extra_rows,
        schema={
            "t_dat": pl.Date,
            "customer_id": pl.String,
            "article_id": pl.String,
            "price": pl.Float64,
            "sales_channel_id": pl.Int8,
        },
        orient="row",
    )
    tx_df = pl.concat([base_tx, extra_df], how="vertical")

    rk_cfg = v2_ranker.RankerConfig(
        weeks=2,
        val_weeks=1,
        negatives_per_group=4,
        hard_negatives_per_group=2,
        num_boost_round=4,
        early_stopping_rounds=2,
        num_leaves=7,
        min_data_in_leaf=1,
        num_threads=1,
    )
    r_cfg = v2_retrieval.RetrievalConfig(
        history_limit=6,
        variant_limit=4,
        cooc_limit=4,
        latent_dim=4,
        latent_limit=4,
        content_limit=4,
        segment_pop_limit=6,
        global_pop_limit=6,
    )
    rep = v2_eval.evaluate_fold(
        (tx_df, articles_df, customers_df),
        "V_SYNTH",
        cutoff,
        retrieval_config=r_cfg,
        ranker_config=rk_cfg,
        work_dir=tmp_path / "eval_work",
    )
    assert rep["v2"]["metrics"]["map_at_12"] > 0.0
    assert "H" in rep["baselines"] and "P" in rep["baselines"]
    assert rep["cold_start_routing"]["cold_users"] >= 1
