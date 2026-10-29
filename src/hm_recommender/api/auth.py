"""Authentication portal, session management, and perimeter security."""

import base64
import hashlib
import hmac
import json
import os
from pathlib import Path
import time
from typing import Any

COOKIE_NAME = "hmrec_session"
DEFAULT_SESSION_DURATION = 30 * 86400  # 30 days


def create_session_token(username: str, secret: bytes, max_age: int = DEFAULT_SESSION_DURATION) -> str:
    """Create a cryptographically signed HMAC-SHA256 session token."""
    expires_at = int(time.time()) + max_age
    payload = json.dumps({"u": username, "exp": expires_at}, separators=(",", ":")).encode("utf-8")
    signature = hmac.new(secret, payload, hashlib.sha256).hexdigest()
    encoded_payload = base64.urlsafe_b64encode(payload).decode("ascii")
    return f"{encoded_payload}.{signature}"


def verify_session_token(token: str, secret: bytes) -> str | None:
    """Verify an HMAC-SHA256 session token. Returns username if valid and active."""
    try:
        encoded_payload, signature = token.split(".", 1)
        payload = base64.urlsafe_b64decode(encoded_payload.encode("ascii"))
        expected_sig = hmac.new(secret, payload, hashlib.sha256).hexdigest()
        if not hmac.compare_digest(signature, expected_sig):
            return None
        data = json.loads(payload.decode("utf-8"))
        if time.time() > data.get("exp", 0):
            return None
        return data.get("u")
    except Exception:
        return None


def hash_password(password: str) -> str:
    """Compute constant-format SHA-256 digest of password."""
    return hashlib.sha256(password.encode("utf-8")).hexdigest()


def load_user_hashes(config_path: Path | None = None) -> dict[str, str]:
    """Load valid username -> sha256_hash mapping from server configuration."""
    candidates = [
        config_path,
        Path(os.environ.get("HMREC_GATEWAY_CONFIG", "")),
        Path("/etc/hmrec/server/gateway.json"),
        Path("/data/hm_project/service/.runtime/server/gateway.json"),
        Path(".runtime/server/gateway.json"),
    ]
    for candidate in candidates:
        if candidate and candidate.is_file():
            try:
                data = json.loads(candidate.read_text(encoding="utf-8"))
                hashes = data.get("password_hashes")
                if isinstance(hashes, dict) and hashes:
                    return hashes
            except Exception:
                pass
    return {}


def verify_user_credentials(username: str, password: str, user_hashes: dict[str, str]) -> bool:
    """Verify username and password against SHA-256 hash dictionary."""
    if not username or not password or username not in user_hashes:
        return False
    expected_hash = user_hashes[username]
    computed_hash = hash_password(password)
    return hmac.compare_digest(computed_hash, expected_hash)


def render_login_html(error: str | None = None) -> str:
    """Render high-polish, human-readable HTML login form with autocomplete."""
    error_html = (
        f"""<div class="error-banner" role="alert">
            <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                <circle cx="12" cy="12" r="10"></circle>
                <line x1="12" y1="8" x2="12" y2="12"></line>
                <line x1="12" y1="16" x2="12.01" y2="16"></line>
            </svg>
            <span>{error}</span>
        </div>"""
        if error
        else ""
    )

    return f"""<!DOCTYPE html>
<html lang="es">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>H&amp;M Recommender — Iniciar Sesión</title>
    <link rel="icon" href="data:image/svg+xml,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 100 100'><text y='.9em' font-size='90'>👔</text></svg>">
    <style>
        :root {{
            --hm-red: #e50010;
            --hm-red-hover: #c4000e;
            --text-main: #1e293b;
            --text-muted: #64748b;
            --bg-page: #f8fafc;
            --bg-card: #ffffff;
            --border-color: #e2e8f0;
            --focus-ring: rgba(229, 0, 16, 0.2);
        }}
        @media (prefers-color-scheme: dark) {{
            :root {{
                --text-main: #f1f5f9;
                --text-muted: #94a3b8;
                --bg-page: #0f172a;
                --bg-card: #1e293b;
                --border-color: #334155;
                --focus-ring: rgba(229, 0, 16, 0.35);
            }}
        }}
        * {{
            box-sizing: border-box;
            margin: 0;
            padding: 0;
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
        }}
        body {{
            background-color: var(--bg-page);
            color: var(--text-main);
            min-height: 100vh;
            display: flex;
            align-items: center;
            justify-content: center;
            padding: 20px;
        }}
        .login-card {{
            background-color: var(--bg-card);
            border: 1px solid var(--border-color);
            border-radius: 16px;
            box-shadow: 0 10px 25px -5px rgba(0, 0, 0, 0.05), 0 8px 10px -6px rgba(0, 0, 0, 0.05);
            width: 100%;
            max-width: 420px;
            padding: 36px 32px;
        }}
        .brand-header {{
            text-align: center;
            margin-bottom: 28px;
        }}
        .brand-badge {{
            display: inline-block;
            background-color: var(--hm-red);
            color: #ffffff;
            font-weight: 800;
            font-size: 18px;
            letter-spacing: 1px;
            padding: 4px 14px;
            border-radius: 6px;
            margin-bottom: 12px;
        }}
        .brand-title {{
            font-size: 22px;
            font-weight: 700;
            color: var(--text-main);
            margin-bottom: 6px;
        }}
        .brand-subtitle {{
            font-size: 13px;
            color: var(--text-muted);
        }}
        .error-banner {{
            display: flex;
            align-items: center;
            gap: 10px;
            background-color: rgba(229, 0, 16, 0.08);
            border: 1px solid rgba(229, 0, 16, 0.25);
            color: var(--hm-red);
            padding: 12px 14px;
            border-radius: 8px;
            font-size: 13px;
            margin-bottom: 20px;
            font-weight: 500;
        }}
        .form-group {{
            margin-bottom: 18px;
        }}
        label {{
            display: block;
            font-size: 13px;
            font-weight: 600;
            color: var(--text-main);
            margin-bottom: 6px;
        }}
        input[type="text"],
        input[type="password"] {{
            width: 100%;
            padding: 12px 14px;
            border: 1px solid var(--border-color);
            border-radius: 8px;
            background-color: var(--bg-card);
            color: var(--text-main);
            font-size: 15px;
            outline: none;
            transition: border-color 0.15s ease, box-shadow 0.15s ease;
        }}
        input[type="text"]:focus,
        input[type="password"]:focus {{
            border-color: var(--hm-red);
            box-shadow: 0 0 0 3px var(--focus-ring);
        }}
        .remember-row {{
            display: flex;
            align-items: center;
            gap: 8px;
            margin-bottom: 24px;
        }}
        .remember-row input[type="checkbox"] {{
            accent-color: var(--hm-red);
            width: 16px;
            height: 16px;
            cursor: pointer;
        }}
        .remember-row label {{
            margin-bottom: 0;
            font-size: 13px;
            font-weight: 400;
            color: var(--text-muted);
            cursor: pointer;
        }}
        button[type="submit"] {{
            width: 100%;
            background-color: var(--hm-red);
            color: #ffffff;
            border: none;
            border-radius: 8px;
            padding: 13px;
            font-size: 15px;
            font-weight: 600;
            cursor: pointer;
            transition: background-color 0.15s ease;
        }}
        button[type="submit"]:hover {{
            background-color: var(--hm-red-hover);
        }}
        .footer-note {{
            text-align: center;
            font-size: 12px;
            color: var(--text-muted);
            margin-top: 24px;
        }}
    </style>
</head>
<body>
    <div class="login-card">
        <div class="brand-header">
            <div class="brand-badge">H&amp;M</div>
            <h1 class="brand-title">Recommender System</h1>
            <p class="brand-subtitle">Personal Styling &amp; Catalog Intelligence</p>
        </div>
        {error_html}
        <form method="POST" action="/login">
            <div class="form-group">
                <label for="username">Usuario</label>
                <input type="text" id="username" name="username" autocomplete="username" autofocus required placeholder="Ej. alanmagno">
            </div>
            <div class="form-group">
                <label for="password">Contraseña</label>
                <input type="password" id="password" name="password" autocomplete="current-password" required placeholder="••••••••••••••••">
            </div>
            <div class="remember-row">
                <input type="checkbox" id="remember" name="remember" value="1" checked>
                <label for="remember">Recordar sesión en este equipo</label>
            </div>
            <button type="submit">Iniciar Sesión</button>
        </form>
        <p class="footer-note">Acceso restringido para el equipo · Sesión protegida</p>
    </div>
</body>
</html>"""
