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
    """Render high-polish, Mid-Century Modern HTML login terminal."""
    error_html = (
        f"""<div class="error-banner" role="alert">
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
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
    <title>H&amp;M · Estudio de Estilo &amp; Recomendación — Acceso</title>
    <link rel="icon" href="data:image/svg+xml,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 100 100'><text y='.9em' font-size='90'>✦</text></svg>">
    <style>
        @import url('https://fonts.googleapis.com/css2?family=DM+Serif+Display:ital@0;1&family=Outfit:wght@400;500;600;700&family=Plus+Jakarta+Sans:wght@400;500;600;700&family=JetBrains+Mono:wght@400;500&display=swap');

        :root {{
            --mcm-bg: #FAF7F2;
            --mcm-card: #251B14;
            --mcm-terracotta: #C85A32;
            --mcm-terracotta-dark: #B34820;
            --mcm-ochre: #DDA15E;
            --mcm-olive: #8EB872;
            --mcm-border: #3E2F24;
            --text-main: #FFFDF9;
            --text-muted: #CFC3B3;
            --focus-ring: rgba(200, 90, 50, 0.4);
        }}
        * {{
            box-sizing: border-box;
            margin: 0;
            padding: 0;
            font-family: 'Plus Jakarta Sans', -apple-system, BlinkMacSystemFont, sans-serif;
        }}
        body {{
            background: radial-gradient(circle at 50% 0%, #FCFBF9 0%, #F8F4EC 65%, #F0E9DE 100%);
            color: #281F19;
            min-height: 100vh;
            display: flex;
            align-items: center;
            justify-content: center;
            padding: 24px;
            position: relative;
            overflow-x: hidden;
        }}
        body::before {{
            content: "";
            position: fixed;
            top: 0; left: 0;
            width: 100vw; height: 100vh;
            background-image: 
                linear-gradient(to right, rgba(198, 166, 138, 0.08) 1px, transparent 1px),
                linear-gradient(to bottom, rgba(198, 166, 138, 0.08) 1px, transparent 1px);
            background-size: 38px 38px;
            pointer-events: none;
            z-index: 0;
        }}
        .login-card {{
            position: relative;
            z-index: 1;
            background: var(--mcm-card);
            border: 1px solid var(--mcm-border);
            border-top: 4px solid var(--mcm-terracotta);
            border-radius: 16px;
            box-shadow: 0 16px 40px rgba(37, 27, 20, 0.22), inset 0 1px 0 rgba(255, 255, 255, 0.08);
            width: 100%;
            max-width: 440px;
            padding: 38px 34px;
            overflow: hidden;
            color: var(--text-main);
        }}
        .telemetry-row {{
            display: flex;
            align-items: center;
            justify-content: space-between;
            margin-bottom: 24px;
            font-family: 'Outfit', sans-serif;
            font-size: 11px;
            letter-spacing: 0.8px;
        }}
        .hud-status {{
            display: inline-flex;
            align-items: center;
            gap: 6px;
            color: var(--mcm-olive);
            background: rgba(142, 184, 114, 0.16);
            border: 1px solid rgba(142, 184, 114, 0.35);
            padding: 4px 10px;
            border-radius: 20px;
            text-transform: uppercase;
            font-weight: 700;
        }}
        .hud-dot {{
            width: 6px; height: 6px;
            background: var(--mcm-olive);
            border-radius: 50%;
            box-shadow: 0 0 6px var(--mcm-olive);
            animation: pulse-glow 2.5s infinite ease-in-out;
        }}
        @keyframes pulse-glow {{
            0%, 100% {{ opacity: 1; transform: scale(1); }}
            50% {{ opacity: 0.4; transform: scale(0.85); }}
        }}
        .hud-tag {{
            color: var(--text-muted);
            text-transform: uppercase;
            letter-spacing: 0.6px;
            font-weight: 500;
        }}
        .brand-header {{
            text-align: center;
            margin-bottom: 28px;
        }}
        .brand-badge {{
            display: inline-block;
            background: var(--mcm-terracotta);
            color: #ffffff;
            font-family: 'Outfit', sans-serif;
            font-weight: 700;
            font-size: 13px;
            letter-spacing: 1.5px;
            padding: 4px 14px;
            border-radius: 6px;
            box-shadow: 0 4px 12px rgba(200, 90, 50, 0.3);
            margin-bottom: 12px;
            text-transform: uppercase;
        }}
        .brand-title {{
            font-family: 'DM Serif Display', Georgia, serif;
            font-size: 26px;
            font-weight: 400;
            letter-spacing: 0.5px;
            color: #FFFDF9;
            margin-bottom: 6px;
        }}
        .brand-subtitle {{
            font-size: 13.5px;
            color: var(--text-muted);
            letter-spacing: 0.3px;
            line-height: 1.5;
        }}
        .error-banner {{
            display: flex;
            align-items: center;
            gap: 10px;
            background: rgba(200, 90, 50, 0.15);
            border: 1px solid rgba(200, 90, 50, 0.5);
            color: #F8B4A0;
            padding: 12px 14px;
            border-radius: 8px;
            font-size: 13px;
            font-weight: 500;
            margin-bottom: 22px;
        }}
        .form-group {{
            margin-bottom: 20px;
        }}
        label {{
            display: block;
            font-family: 'Outfit', sans-serif;
            font-size: 12px;
            font-weight: 700;
            letter-spacing: 0.8px;
            text-transform: uppercase;
            color: var(--text-muted);
            margin-bottom: 7px;
        }}
        input[type="text"],
        input[type="password"] {{
            width: 100%;
            padding: 13px 15px;
            border: 1px solid #4A3A2E;
            border-radius: 8px;
            background: #1C140E;
            color: #FFFDF9;
            font-size: 14.5px;
            outline: none;
            transition: all 0.2s cubic-bezier(0.16, 1, 0.3, 1);
        }}
        input[type="text"]:focus,
        input[type="password"]:focus {{
            border-color: var(--mcm-terracotta);
            box-shadow: 0 0 0 3px var(--focus-ring);
            background: #140E0A;
        }}
        .remember-row {{
            display: flex;
            align-items: center;
            gap: 9px;
            margin-bottom: 26px;
        }}
        .remember-row input[type="checkbox"] {{
            accent-color: var(--mcm-terracotta);
            width: 16px;
            height: 16px;
            cursor: pointer;
        }}
        .remember-row label {{
            margin-bottom: 0;
            font-family: 'Plus Jakarta Sans', sans-serif;
            font-size: 13px;
            font-weight: 400;
            letter-spacing: 0;
            text-transform: none;
            color: var(--text-muted);
            cursor: pointer;
        }}
        button[type="submit"] {{
            width: 100%;
            background: var(--mcm-terracotta);
            color: #ffffff;
            border: 1px solid var(--mcm-terracotta-dark);
            border-radius: 8px;
            padding: 14px;
            font-family: 'Outfit', sans-serif;
            font-size: 14.5px;
            font-weight: 700;
            letter-spacing: 1.2px;
            text-transform: uppercase;
            cursor: pointer;
            box-shadow: 0 4px 14px rgba(200, 90, 50, 0.35);
            transition: all 0.2s cubic-bezier(0.16, 1, 0.3, 1);
        }}
        button[type="submit"]:hover {{
            background: var(--mcm-terracotta-dark);
            box-shadow: 0 6px 20px rgba(200, 90, 50, 0.5);
            transform: translateY(-2px);
        }}
        .footer-note {{
            text-align: center;
            font-family: 'Outfit', sans-serif;
            font-size: 11px;
            color: #8C7E72;
            letter-spacing: 0.8px;
            text-transform: uppercase;
            margin-top: 26px;
        }}
    </style>
</head>
<body>
    <div class="login-card">
        <div class="telemetry-row">
            <span class="hud-status"><span class="hud-dot"></span> ACCESO SEGURO</span>
            <span class="hud-tag">EDICIÓN 1947</span>
        </div>
        <div class="brand-header">
            <div class="brand-badge">H&amp;M · ESTUDIO</div>
            <h1 class="brand-title">RECOMENDADOR DE VESTUARIO</h1>
            <p class="brand-subtitle">Curaduría predictiva de vestuario y catálogo de estilo</p>
        </div>
        {error_html}
        <form method="POST" action="/login">
            <div class="form-group">
                <label for="username">IDENTIFICADOR DE OPERADOR</label>
                <input type="text" id="username" name="username" autocomplete="username" autofocus required placeholder="Ej. alanmagno">
            </div>
            <div class="form-group">
                <label for="password">CLAVE DE ACCESO</label>
                <input type="password" id="password" name="password" autocomplete="current-password" required placeholder="••••••••••••••••">
            </div>
            <div class="remember-row">
                <input type="checkbox" id="remember" name="remember" value="1" checked>
                <label for="remember">Recordar sesión en este equipo</label>
            </div>
            <button type="submit">INICIAR SESIÓN ✦</button>
        </form>
        <p class="footer-note">H&amp;M ESTUDIO DE ESTILO · SESIÓN CIFRADA</p>
    </div>
</body>
</html>"""
