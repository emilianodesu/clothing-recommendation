"""Authentication portal, session management, and perimeter security."""

import base64
from collections import OrderedDict, deque
import hashlib
import hmac
import html
import ipaddress
import json
import os
from pathlib import Path
import re
import threading
import time
from typing import Any

COOKIE_NAME = "hmrec_session"
DEFAULT_SESSION_DURATION = 30 * 86400  # 30 days
SHORT_SESSION_DURATION = 12 * 3600  # 12 hours
MAX_LOGIN_ATTEMPTS = 8
RATE_LIMIT_WINDOW_SECONDS = 300  # 5 minutes
MAX_TRACKED_IPS = 10_000
MAX_LOGIN_BODY_BYTES = 4096

_SCRYPT_N = 16384
_SCRYPT_R = 8
_SCRYPT_P = 1
_SCRYPT_SALT_BYTES = 16
_SCRYPT_DKLEN = 32
_SCRYPT_MAXMEM = 32 * 1024 * 1024
_SCRYPT_MAX_ACCEPT_MAXMEM = 64 * 1024 * 1024
_DUMMY_SCRYPT_SALT = b"\x00" * _SCRYPT_SALT_BYTES
_DUMMY_SCRYPT_DIGEST = b"\x00" * _SCRYPT_DKLEN
_HEX_RE = re.compile(r"^[0-9a-fA-F]+$")
_LEGACY_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")

_HASH_CACHE_LOCK = threading.Lock()
_HASH_CACHE: dict[Path, tuple[int, int, dict[str, str]]] = {}

SECURITY_HEADERS = {
    "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
    "Pragma": "no-cache",
    "X-Frame-Options": "DENY",
    "X-Content-Type-Options": "nosniff",
}


class LoginRateLimiter:
    """Thread-safe sliding-window brute-force rate limiter keyed by client IP."""

    def __init__(
        self,
        max_attempts: int = MAX_LOGIN_ATTEMPTS,
        window_seconds: int = RATE_LIMIT_WINDOW_SECONDS,
        max_tracked_ips: int = MAX_TRACKED_IPS,
    ) -> None:
        if max_tracked_ips < 1:
            raise ValueError("max_tracked_ips must be >= 1")
        self.max_attempts = max_attempts
        self.window_seconds = window_seconds
        self.max_tracked_ips = max_tracked_ips
        self._failures: OrderedDict[str, deque[float]] = OrderedDict()
        self._lock = threading.Lock()

    def _prune(self, key: str, now: float) -> deque[float]:
        bucket = self._failures.get(key)
        if not bucket:
            return deque()
        cutoff = now - self.window_seconds
        while bucket and bucket[0] <= cutoff:
            bucket.popleft()
        if not bucket:
            self._failures.pop(key, None)
        return bucket

    def _sweep_expired(self, now: float) -> None:
        for tracked_key in list(self._failures.keys()):
            self._prune(tracked_key, now)

    def is_blocked(self, key: str, now: float | None = None) -> bool:
        """Return True if the client IP has reached the maximum failed login attempts."""
        ts = time.time() if now is None else now
        with self._lock:
            bucket = self._prune(key, ts)
            if bucket:
                self._failures.move_to_end(key)
            return len(bucket) >= self.max_attempts

    def record_failure(self, key: str, now: float | None = None) -> None:
        """Record a failed login attempt for the given client IP."""
        ts = time.time() if now is None else now
        with self._lock:
            bucket = self._prune(key, ts)
            if key not in self._failures:
                if len(self._failures) >= self.max_tracked_ips:
                    self._sweep_expired(ts)
                    while len(self._failures) >= self.max_tracked_ips:
                        self._failures.popitem(last=False)
                self._failures[key] = bucket
            else:
                self._failures.move_to_end(key)
            bucket.append(ts)

    def reset(self, key: str) -> None:
        """Clear failure history after a successful authentication."""
        with self._lock:
            self._failures.pop(key, None)


def _get_header(headers: Any, name: str) -> str:
    if headers is None:
        return ""
    value = headers.get(name) if hasattr(headers, "get") else None
    if value is None and hasattr(headers, "items"):
        lowered = name.lower()
        for k, v in headers.items():
            if isinstance(k, str) and k.lower() == lowered:
                value = v
                break
    return value if isinstance(value, str) else ""


def _parse_ip(raw: str) -> str | None:
    if not isinstance(raw, str):
        return None
    token = raw.strip().strip('"').strip("'")
    if not token or len(token) > 128 or "%" in token or any(ch.isspace() for ch in token):
        return None
    if token.startswith("["):
        end = token.find("]")
        if end <= 1:
            return None
        rest = token[end + 1 :]
        if rest and not (rest.startswith(":") and len(rest) > 1 and rest[1:].isdigit()):
            return None
        token = token[1:end]
    try:
        return str(ipaddress.ip_address(token))
    except ValueError:
        if token.count(":") == 1:
            host, port = token.rsplit(":", 1)
            if port.isdigit():
                try:
                    return str(ipaddress.ip_address(host))
                except ValueError:
                    return None
        return None


def extract_client_ip(headers: Any, fallback_host: str = "unknown") -> str:
    """Extract and validate the real client IP address from reverse-proxy headers or socket peer."""
    forwarded = _get_header(headers, "x-forwarded-for")
    if forwarded:
        parts = forwarded[-2048:].split(",")[-32:]
        for part in reversed(parts):
            parsed = _parse_ip(part)
            if parsed is not None:
                return parsed
    real_ip = _get_header(headers, "x-real-ip")
    if real_ip:
        parsed = _parse_ip(real_ip)
        if parsed is not None:
            return parsed
    if isinstance(fallback_host, str):
        parsed_fallback = _parse_ip(fallback_host)
        if parsed_fallback is not None:
            return parsed_fallback
        stripped = fallback_host.strip()
        if stripped in {"testclient", "unknown"}:
            return stripped
    return "unknown"


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
    """Compute a salted scrypt hash of the password."""
    salt = os.urandom(_SCRYPT_SALT_BYTES)
    digest = hashlib.scrypt(
        password.encode("utf-8"),
        salt=salt,
        n=_SCRYPT_N,
        r=_SCRYPT_R,
        p=_SCRYPT_P,
        maxmem=_SCRYPT_MAXMEM,
        dklen=_SCRYPT_DKLEN,
    )
    return f"scrypt$n={_SCRYPT_N}$r={_SCRYPT_R}$p={_SCRYPT_P}${salt.hex()}${digest.hex()}"


def _parse_scrypt_hash(encoded: str) -> tuple[int, int, int, bytes, bytes] | None:
    parts = encoded.split("$")
    if len(parts) != 6:
        return None
    algo, n_part, r_part, p_part, salt_hex, hash_hex = parts
    if (
        algo != "scrypt"
        or not n_part.startswith("n=")
        or not r_part.startswith("r=")
        or not p_part.startswith("p=")
    ):
        return None
    n_str, r_str, p_str = n_part[2:], r_part[2:], p_part[2:]
    if not (n_str.isdigit() and r_str.isdigit() and p_str.isdigit()):
        return None
    n = int(n_str)
    r = int(r_str)
    p = int(p_str)
    if n < 2 or (n & (n - 1)) != 0 or r < 1 or p < 1 or p > 16:
        return None
    if 128 * r * (n + p) + 1024 * 1024 > _SCRYPT_MAX_ACCEPT_MAXMEM:
        return None
    if (
        not _HEX_RE.fullmatch(salt_hex)
        or not _HEX_RE.fullmatch(hash_hex)
        or len(salt_hex) % 2 != 0
        or len(hash_hex) % 2 != 0
    ):
        return None
    salt = bytes.fromhex(salt_hex)
    digest = bytes.fromhex(hash_hex)
    if len(salt) < 8 or not (16 <= len(digest) <= 128):
        return None
    return n, r, p, salt, digest


def load_user_hashes(config_path: Path | None = None) -> dict[str, str]:
    """Load valid username -> password_hash mapping from server configuration with stat caching."""
    candidates: list[Path] = []
    if config_path is not None:
        candidates.append(Path(config_path))
    env_cfg = os.environ.get("HMREC_GATEWAY_CONFIG", "").strip()
    if env_cfg:
        candidates.append(Path(env_cfg))
    candidates.extend([
        Path("/etc/hmrec/server/gateway.json"),
        Path(".runtime/server/gateway.json"),
    ])

    with _HASH_CACHE_LOCK:
        for candidate in candidates:
            try:
                if not candidate.is_file():
                    continue
                resolved = candidate.resolve()
                st = resolved.stat()
                cached = _HASH_CACHE.get(resolved)
                if cached is not None and cached[0] == st.st_mtime_ns and cached[1] == st.st_size:
                    return dict(cached[2])
                data = json.loads(resolved.read_text(encoding="utf-8"))
                raw_hashes = data.get("password_hashes")
                if not isinstance(raw_hashes, dict):
                    continue
                cleaned = {
                    k: v
                    for k, v in raw_hashes.items()
                    if isinstance(k, str) and isinstance(v, str) and k and v
                }
                if cleaned:
                    _HASH_CACHE[resolved] = (st.st_mtime_ns, st.st_size, cleaned)
                    return dict(cleaned)
            except (OSError, ValueError, TypeError):
                continue
    return {}


def verify_user_credentials(username: str, password: str, user_hashes: dict[str, str]) -> bool:
    """Verify username and password against scrypt or legacy SHA-256 hashes in constant time."""
    valid_inputs = (
        isinstance(username, str)
        and isinstance(password, str)
        and bool(username)
        and bool(password)
        and len(username) <= 128
        and len(password) <= 256
        and isinstance(user_hashes, dict)
        and username in user_hashes
    )
    safe_password = (password[:256] if isinstance(password, str) and password else "").encode("utf-8")
    stored = user_hashes.get(username) if (isinstance(username, str) and isinstance(user_hashes, dict)) else None

    if valid_inputs and isinstance(stored, str):
        parsed = _parse_scrypt_hash(stored)
        if parsed is not None:
            n, r, p, salt, expected_digest = parsed
            maxmem = max(_SCRYPT_MAXMEM, 128 * r * (n + p) + 1024 * 1024)
            derived = hashlib.scrypt(
                safe_password,
                salt=salt,
                n=n,
                r=r,
                p=p,
                maxmem=maxmem,
                dklen=len(expected_digest),
            )
            return hmac.compare_digest(derived, expected_digest)
        if _LEGACY_SHA256_RE.fullmatch(stored):
            dummy = hashlib.scrypt(
                safe_password,
                salt=_DUMMY_SCRYPT_SALT,
                n=_SCRYPT_N,
                r=_SCRYPT_R,
                p=_SCRYPT_P,
                maxmem=_SCRYPT_MAXMEM,
                dklen=_SCRYPT_DKLEN,
            )
            _ = hmac.compare_digest(dummy, _DUMMY_SCRYPT_DIGEST)
            legacy_digest = hashlib.sha256(safe_password).hexdigest()
            return hmac.compare_digest(legacy_digest, stored)

    dummy = hashlib.scrypt(
        safe_password,
        salt=_DUMMY_SCRYPT_SALT,
        n=_SCRYPT_N,
        r=_SCRYPT_R,
        p=_SCRYPT_P,
        maxmem=_SCRYPT_MAXMEM,
        dklen=_SCRYPT_DKLEN,
    )
    _ = hmac.compare_digest(dummy, _DUMMY_SCRYPT_DIGEST)
    return False


def render_login_html(error: str | None = None) -> str:
    """Render high-polish, minimalist luxury editorial HTML login portal."""
    safe_error = html.escape(error, quote=True) if error else ""
    error_html = (
        f"""<div class="error-banner" role="alert">
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                <circle cx="12" cy="12" r="10"></circle>
                <line x1="12" y1="8" x2="12" y2="12"></line>
                <line x1="12" y1="16" x2="12.01" y2="16"></line>
            </svg>
            <span>{safe_error}</span>
        </div>"""
        if safe_error
        else ""
    )

    return f"""<!DOCTYPE html>
<html lang="es">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Iniciar sesión</title>
    <link rel="icon" href="data:image/svg+xml,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 100 100'><text y='.9em' font-size='90'>◻</text></svg>">
    <style>
        @import url('https://fonts.googleapis.com/css2?family=Bodoni+Moda:ital,opsz,wght@0,6..96,400;0,6..96,600;0,6..96,700&family=Plus+Jakarta+Sans:wght@300;400;500;600;700&display=swap');

        :root {{
            color-scheme: light dark;
            --bg-canvas: light-dark(#FAF9F7, #0E0E0D);
            --bg-card: light-dark(#FFFFFF, #171716);
            --text-noir: light-dark(#121212, #F2F0EC);
            --text-charcoal: light-dark(#2A2A28, #D6D3CD);
            --text-muted: light-dark(#73736E, #9A968E);
            --text-light-muted: light-dark(#9E9E98, #6E6B64);
            --border-atelier: light-dark(#E8E8E3, #2A2A27);
            --border-focus: light-dark(#121212, #F2F0EC);
            --btn-bg: light-dark(#121212, #F2F0EC);
            --btn-fg: light-dark(#FFFFFF, #121212);
            --btn-hover-bg: light-dark(#333333, #DCD9D2);
            --err-bg: light-dark(#FFF5F5, #291414);
            --err-border: light-dark(#FED7D7, #5C2323);
            --err-fg: light-dark(#C53030, #FCA5A5);
        }}
        @media (prefers-color-scheme: dark) {{
            :root {{
                --bg-canvas: #0E0E0D;
                --bg-card: #171716;
                --text-noir: #F2F0EC;
                --text-charcoal: #D6D3CD;
                --text-muted: #9A968E;
                --text-light-muted: #6E6B64;
                --border-atelier: #2A2A27;
                --border-focus: #F2F0EC;
                --btn-bg: #F2F0EC;
                --btn-fg: #121212;
                --btn-hover-bg: #DCD9D2;
                --err-bg: #291414;
                --err-border: #5C2323;
                --err-fg: #FCA5A5;
            }}
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
            box-shadow: 0 4px 24px rgba(0, 0, 0, 0.12);
            width: 100%;
            max-width: 420px;
            padding: 42px 38px;
            color: var(--text-noir);
        }}
        .brand-header {{
            text-align: center;
            margin-bottom: 30px;
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
            background: var(--err-bg);
            border: 1px solid var(--err-border);
            border-left: 3px solid #E53E3E;
            color: var(--err-fg);
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
            background: var(--btn-bg);
            color: var(--btn-fg);
            border: 1px solid var(--btn-bg);
            border-radius: 2px;
            padding: 14px;
            font-family: 'Plus Jakarta Sans', sans-serif;
            font-size: 11.5px;
            font-weight: 600;
            letter-spacing: 0.16em;
            text-transform: uppercase;
            cursor: pointer;
            box-shadow: 0 2px 8px rgba(0, 0, 0, 0.1);
            transition: all 0.2s ease;
        }}
        button[type="submit"]:hover {{
            background: var(--btn-hover-bg);
            border-color: var(--btn-hover-bg);
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
