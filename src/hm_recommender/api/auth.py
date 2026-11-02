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
    """Render high-polish, minimalist luxury editorial HTML login portal."""
    error_html = (
        f"""<div class="error-banner" role="alert">
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
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
    <title>H&amp;M Studio — Acceso de Operador</title>
    <link rel="icon" href="data:image/svg+xml,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 100 100'><text y='.9em' font-size='90'>◻</text></svg>">
    <style>
        @import url('https://fonts.googleapis.com/css2?family=Bodoni+Moda:ital,opsz,wght@0,6..96,400;0,6..96,600;0,6..96,700&family=Plus+Jakarta+Sans:wght@300;400;500;600;700&display=swap');

        :root {{
            --bg-canvas: #FAF9F7;
            --bg-card: #FFFFFF;
            --text-noir: #121212;
            --text-charcoal: #2A2A28;
            --text-muted: #73736E;
            --text-light-muted: #9E9E98;
            --border-atelier: #E8E8E3;
            --border-focus: #121212;
            --plate: #F4F4F1;
        }}
        * {{
            box-sizing: border-box;
            margin: 0;
            padding: 0;
            font-family: 'Plus Jakarta Sans', -apple-system, BlinkMacSystemFont, sans-serif;
            -webkit-font-smoothing: antialiased;
        }}
        body {{
            background-color: var(--bg-canvas);
            background-image: 
                radial-gradient(at 0% 0%, rgba(240, 238, 232, 0.6) 0px, transparent 50%),
                radial-gradient(at 100% 100%, rgba(242, 239, 233, 0.4) 0px, transparent 50%);
            color: var(--text-noir);
            min-height: 100vh;
            display: flex;
            align-items: center;
            justify-content: center;
            padding: 24px;
            position: relative;
        }}
        .login-card {{
            position: relative;
            z-index: 1;
            background: var(--bg-card);
            border: 1px solid var(--border-atelier);
            border-radius: 4px;
            box-shadow: 0 4px 24px rgba(18, 18, 18, 0.04);
            width: 100%;
            max-width: 420px;
            padding: 42px 38px;
            color: var(--text-noir);
        }}
        .telemetry-row {{
            display: flex;
            align-items: center;
            justify-content: space-between;
            margin-bottom: 24px;
            font-size: 10px;
            letter-spacing: 0.18em;
            text-transform: uppercase;
        }}
        .hud-status {{
            display: inline-flex;
            align-items: center;
            gap: 6px;
            color: #385E38;
            background: #EFF6EE;
            border: 1px solid #D6E8D4;
            padding: 3px 9px;
            border-radius: 20px;
            font-weight: 600;
        }}
        .hud-dot {{
            width: 5px; height: 5px;
            background: #385E38;
            border-radius: 50%;
        }}
        .hud-tag {{
            color: var(--text-light-muted);
            font-weight: 500;
        }}
        .brand-header {{
            text-align: center;
            margin-bottom: 30px;
        }}
        .brand-badge {{
            display: inline-block;
            font-family: 'Plus Jakarta Sans', sans-serif;
            font-weight: 700;
            font-size: 11px;
            letter-spacing: 0.28em;
            color: var(--text-noir);
            margin-bottom: 8px;
            text-transform: uppercase;
        }}
        .brand-title {{
            font-family: 'Bodoni Moda', 'Playfair Display', Georgia, serif;
            font-size: 28px;
            font-weight: 400;
            letter-spacing: -0.01em;
            color: var(--text-noir);
            margin-bottom: 8px;
        }}
        .brand-subtitle {{
            font-size: 13px;
            color: var(--text-muted);
            line-height: 1.5;
        }}
        .error-banner {{
            display: flex;
            align-items: center;
            gap: 10px;
            background: #FFF5F5;
            border: 1px solid #FED7D7;
            border-left: 3px solid #E53E3E;
            color: #C53030;
            padding: 11px 14px;
            border-radius: 2px;
            font-size: 12.5px;
            font-weight: 500;
            margin-bottom: 22px;
        }}
        .form-group {{
            margin-bottom: 20px;
        }}
        label {{
            display: block;
            font-family: 'Plus Jakarta Sans', sans-serif;
            font-size: 10.5px;
            font-weight: 600;
            letter-spacing: 0.14em;
            text-transform: uppercase;
            color: var(--text-charcoal);
            margin-bottom: 8px;
        }}
        input[type="text"],
        input[type="password"] {{
            width: 100%;
            padding: 12px 14px;
            border: 1px solid var(--border-atelier);
            border-radius: 2px;
            background: var(--bg-card);
            color: var(--text-noir);
            font-size: 14px;
            outline: none;
            transition: border-color 0.2s ease;
        }}
        input[type="text"]:focus,
        input[type="password"]:focus {{
            border-color: var(--border-focus);
            box-shadow: 0 0 0 1px var(--border-focus);
        }}
        .remember-row {{
            display: flex;
            align-items: center;
            gap: 9px;
            margin-bottom: 26px;
        }}
        .remember-row input[type="checkbox"] {{
            accent-color: var(--text-noir);
            width: 15px;
            height: 15px;
            cursor: pointer;
        }}
        .remember-row label {{
            margin-bottom: 0;
            font-family: 'Plus Jakarta Sans', sans-serif;
            font-size: 12.5px;
            font-weight: 400;
            letter-spacing: 0;
            text-transform: none;
            color: var(--text-muted);
            cursor: pointer;
        }}
        button[type="submit"] {{
            width: 100%;
            background: var(--text-noir);
            color: #FFFFFF;
            border: 1px solid var(--text-noir);
            border-radius: 2px;
            padding: 14px;
            font-family: 'Plus Jakarta Sans', sans-serif;
            font-size: 11.5px;
            font-weight: 600;
            letter-spacing: 0.16em;
            text-transform: uppercase;
            cursor: pointer;
            box-shadow: 0 2px 8px rgba(18, 18, 18, 0.1);
            transition: all 0.2s ease;
        }}
        button[type="submit"]:hover {{
            background: #333333;
            border-color: #333333;
            transform: translateY(-1px);
        }}
    </style>
</head>
<body>
    <div class="login-card">
        <div class="brand-header">
            <h1 class="brand-title">Iniciar sesión</h1>
            <p class="brand-subtitle">Introduce tus credenciales para continuar.</p>
        </div>
        {error_html}
        <form method="POST" action="/login">
            <div class="form-group">
                <label for="username">Usuario</label>
                <input type="text" id="username" name="username" autocomplete="username" autofocus required placeholder="Usuario">
            </div>
            <div class="form-group">
                <label for="password">Contraseña</label>
                <input type="password" id="password" name="password" autocomplete="current-password" required placeholder="••••••••••••••••">
            </div>
            <div class="remember-row">
                <input type="checkbox" id="remember" name="remember" value="1" checked>
                <label for="remember">Recordar sesión en este equipo</label>
            </div>
            <button type="submit">Entrar</button>
        </form>
    </div>
</body>
</html>"""
