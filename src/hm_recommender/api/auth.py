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
    """Render high-polish, futuristic Cyberpunk HTML login terminal."""
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
    <title>H&amp;M // Intelligent Styling Nexus — Acceso</title>
    <link rel="icon" href="data:image/svg+xml,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 100 100'><text y='.9em' font-size='90'>⚡</text></svg>">
    <style>
        @import url('https://fonts.googleapis.com/css2?family=Orbitron:wght@600;700;800;900&family=Rajdhani:wght@500;600;700&family=JetBrains+Mono:wght@400;500;600;700&family=Plus+Jakarta+Sans:wght@400;500;600;700&display=swap');

        :root {{
            --cyber-bg: #06080e;
            --cyber-card: rgba(11, 18, 33, 0.82);
            --cyber-neon-cyan: #00f0ff;
            --cyber-neon-red: #ff003c;
            --cyber-neon-green: #00ff9d;
            --cyber-border: rgba(0, 240, 255, 0.28);
            --text-main: #f1f5f9;
            --text-muted: #94a3b8;
            --focus-glow: rgba(0, 240, 255, 0.4);
        }}
        * {{
            box-sizing: border-box;
            margin: 0;
            padding: 0;
            font-family: 'Plus Jakarta Sans', -apple-system, BlinkMacSystemFont, sans-serif;
        }}
        body {{
            background: radial-gradient(circle at 50% 0%, #0e172e 0%, #06080e 70%, #020306 100%);
            color: var(--text-main);
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
                linear-gradient(to right, rgba(0, 240, 255, 0.035) 1px, transparent 1px),
                linear-gradient(to bottom, rgba(0, 240, 255, 0.035) 1px, transparent 1px);
            background-size: 36px 36px;
            pointer-events: none;
            z-index: 0;
        }}
        .login-card {{
            position: relative;
            z-index: 1;
            background: var(--cyber-card);
            backdrop-filter: blur(20px);
            -webkit-backdrop-filter: blur(20px);
            border: 1px solid var(--cyber-border);
            border-radius: 16px;
            box-shadow: 0 20px 50px rgba(0, 0, 0, 0.7), inset 0 0 25px rgba(0, 240, 255, 0.04);
            width: 100%;
            max-width: 440px;
            padding: 40px 34px;
            overflow: hidden;
        }}
        .login-card::before {{
            content: "";
            position: absolute;
            top: 0; left: 0; right: 0;
            height: 3px;
            background: linear-gradient(90deg, #ff003c, #00f0ff);
        }}
        .telemetry-row {{
            display: flex;
            align-items: center;
            justify-content: space-between;
            margin-bottom: 24px;
            font-family: 'JetBrains Mono', monospace;
            font-size: 11px;
            letter-spacing: 0.8px;
        }}
        .hud-status {{
            display: inline-flex;
            align-items: center;
            gap: 6px;
            color: var(--cyber-neon-green);
            background: rgba(0, 255, 157, 0.1);
            border: 1px solid rgba(0, 255, 157, 0.3);
            padding: 3px 8px;
            border-radius: 12px;
            text-transform: uppercase;
            font-weight: 600;
        }}
        .hud-dot {{
            width: 6px; height: 6px;
            background: var(--cyber-neon-green);
            border-radius: 50%;
            box-shadow: 0 0 6px var(--cyber-neon-green);
            animation: pulse-glow 2s infinite ease-in-out;
        }}
        @keyframes pulse-glow {{
            0%, 100% {{ opacity: 1; transform: scale(1); box-shadow: 0 0 6px #00ff9d; }}
            50% {{ opacity: 0.35; transform: scale(0.8); box-shadow: 0 0 1px #00ff9d; }}
        }}
        .hud-tag {{
            color: var(--text-muted);
            text-transform: uppercase;
        }}
        .brand-header {{
            text-align: center;
            margin-bottom: 30px;
        }}
        .brand-badge {{
            display: inline-block;
            background: linear-gradient(135deg, #ff003c 0%, #e50010 100%);
            color: #ffffff;
            font-family: 'Orbitron', sans-serif;
            font-weight: 800;
            font-size: 16px;
            letter-spacing: 2px;
            padding: 4px 16px;
            border-radius: 6px;
            box-shadow: 0 0 15px rgba(255, 0, 60, 0.5);
            margin-bottom: 14px;
        }}
        .brand-title {{
            font-family: 'Orbitron', sans-serif;
            font-size: 22px;
            font-weight: 800;
            letter-spacing: 1px;
            background: linear-gradient(135deg, #ffffff 0%, #a5f3fc 60%, #00f0ff 100%);
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
            margin-bottom: 6px;
        }}
        .brand-subtitle {{
            font-size: 13px;
            color: var(--text-muted);
            letter-spacing: 0.3px;
        }}
        .error-banner {{
            display: flex;
            align-items: center;
            gap: 10px;
            background: rgba(255, 0, 60, 0.1);
            border: 1px solid rgba(255, 0, 60, 0.4);
            color: #ff4d6d;
            padding: 12px 14px;
            border-radius: 8px;
            font-size: 13px;
            font-weight: 500;
            margin-bottom: 22px;
            box-shadow: 0 0 15px rgba(255, 0, 60, 0.2);
        }}
        .form-group {{
            margin-bottom: 20px;
        }}
        label {{
            display: block;
            font-family: 'Rajdhani', sans-serif;
            font-size: 13px;
            font-weight: 700;
            letter-spacing: 1.2px;
            text-transform: uppercase;
            color: #94a3b8;
            margin-bottom: 7px;
        }}
        input[type="text"],
        input[type="password"] {{
            width: 100%;
            padding: 13px 15px;
            border: 1px solid var(--cyber-border);
            border-radius: 8px;
            background: rgba(14, 23, 42, 0.75);
            color: #ffffff;
            font-size: 15px;
            outline: none;
            transition: all 0.2s cubic-bezier(0.16, 1, 0.3, 1);
        }}
        input[type="text"]:focus,
        input[type="password"]:focus {{
            border-color: var(--cyber-neon-cyan);
            box-shadow: 0 0 20px var(--focus-glow), inset 0 0 8px rgba(0, 240, 255, 0.1);
            background: rgba(14, 23, 42, 0.95);
        }}
        .remember-row {{
            display: flex;
            align-items: center;
            gap: 9px;
            margin-bottom: 26px;
        }}
        .remember-row input[type="checkbox"] {{
            accent-color: var(--cyber-neon-red);
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
            background: linear-gradient(135deg, #ff003c 0%, #ff1744 50%, #e50010 100%);
            color: #ffffff;
            border: 1px solid rgba(255, 255, 255, 0.25);
            border-radius: 8px;
            padding: 14px;
            font-family: 'Rajdhani', sans-serif;
            font-size: 15px;
            font-weight: 700;
            letter-spacing: 2px;
            text-transform: uppercase;
            cursor: pointer;
            box-shadow: 0 0 22px rgba(255, 0, 60, 0.45);
            transition: all 0.2s cubic-bezier(0.16, 1, 0.3, 1);
        }}
        button[type="submit"]:hover {{
            box-shadow: 0 0 35px rgba(255, 0, 60, 0.8), 0 0 10px #ffffff;
            transform: translateY(-2px);
        }}
        .footer-note {{
            text-align: center;
            font-family: 'JetBrains Mono', monospace;
            font-size: 11px;
            color: #64748b;
            letter-spacing: 0.5px;
            margin-top: 26px;
        }}
    </style>
</head>
<body>
    <div class="login-card">
        <div class="telemetry-row">
            <span class="hud-status"><span class="hud-dot"></span> GATEWAY ONLINE</span>
            <span class="hud-tag">PROTOCOL v2.4</span>
        </div>
        <div class="brand-header">
            <div class="brand-badge">H&amp;M</div>
            <h1 class="brand-title">INTELLIGENT NEXUS</h1>
            <p class="brand-subtitle">Catalog Intelligence &amp; Recommendation Engine</p>
        </div>
        {error_html}
        <form method="POST" action="/login">
            <div class="form-group">
                <label for="username">// IDENTIFICADOR DE OPERADOR</label>
                <input type="text" id="username" name="username" autocomplete="username" autofocus required placeholder="Ej. alanmagno">
            </div>
            <div class="form-group">
                <label for="password">// CLAVE DE ACCESO</label>
                <input type="password" id="password" name="password" autocomplete="current-password" required placeholder="••••••••••••••••">
            </div>
            <div class="remember-row">
                <input type="checkbox" id="remember" name="remember" value="1" checked>
                <label for="remember">Recordar sesión en este equipo</label>
            </div>
            <button type="submit">AUTORIZAR ACCESO // INICIAR SESIÓN →</button>
        </form>
        <p class="footer-note">ZERO-TRUST QUANTUM GATEWAY · SESIÓN CIFRADA</p>
    </div>
</body>
</html>"""
