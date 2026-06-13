"""Microsoft Entra ID SSO — direct OAuth2 (no MSAL dependency).

Uses the standard OAuth2 authorization code flow directly via requests,
bypassing MSAL for reliability with new Entra ID tenants.
"""

from __future__ import annotations

import os
import secrets
import threading
import time
import urllib.parse
from typing import Any

import requests as _requests

_SCOPES = "openid profile"  # email scope removed — causes issues on some new tenants


def _cfg() -> dict:
    """Return current config, re-reading env vars each call.

    All module-level constants are evaluated at import time, which is BEFORE
    load_dotenv() runs in app.py if the module is already in sys.modules.
    Reading from os.environ dynamically ensures we always see the live values.
    """
    tenant_id     = os.environ.get("ENTRA_TENANT_ID", "")
    tenant_domain = os.environ.get("ENTRA_TENANT_DOMAIN", "")
    tenant_ref    = tenant_domain if tenant_domain else tenant_id
    return {
        "tenant_id":     tenant_id,
        "tenant_domain": tenant_domain,
        "tenant_ref":    tenant_ref,
        "client_id":     os.environ.get("ENTRA_CLIENT_ID", ""),
        "client_secret": os.environ.get("ENTRA_CLIENT_SECRET", ""),
        "redirect_uri":  os.environ.get("ENTRA_REDIRECT_URI", "http://localhost:8501/"),
        "base":          f"https://login.microsoftonline.com/{tenant_ref}/oauth2/v2.0",
    }


# Keep module-level names for backward-compat with any direct imports
TENANT_ID     = os.environ.get("ENTRA_TENANT_ID", "")
TENANT_DOMAIN = os.environ.get("ENTRA_TENANT_DOMAIN", "")
CLIENT_ID     = os.environ.get("ENTRA_CLIENT_ID", "")
CLIENT_SECRET = os.environ.get("ENTRA_CLIENT_SECRET", "")
REDIRECT_URI  = os.environ.get("ENTRA_REDIRECT_URI", "http://localhost:8501/")

# ---- Server-side OAuth state store -------------------------------------------
# Streamlit creates a NEW session when Microsoft redirects back (fresh HTTP GET),
# so st.session_state loses the nonce. We store it server-side instead.
# Key = state nonce, value = creation timestamp. TTL = 10 minutes.
_STATE_STORE: dict[str, float] = {}
_STATE_LOCK  = threading.Lock()
_STATE_TTL   = 600.0  # seconds


def register_state(nonce: str) -> None:
    """Store a state nonce server-side before redirecting to Microsoft."""
    with _STATE_LOCK:
        _STATE_STORE[nonce] = time.monotonic()
        cutoff = time.monotonic() - _STATE_TTL
        expired = [k for k, v in _STATE_STORE.items() if v < cutoff]
        for k in expired:
            del _STATE_STORE[k]


def consume_state(nonce: str) -> bool:
    """Return True and remove the nonce if it exists and hasn't expired."""
    with _STATE_LOCK:
        ts = _STATE_STORE.pop(nonce, None)
        if ts is None:
            return False
        return time.monotonic() - ts < _STATE_TTL


# ---- JWKS cache ---------------------------------------------------------------
_JWKS_CLIENT_LOCK = threading.Lock()
_JWKS_CLIENT: Any = None
_JWKS_CLIENT_TS: float = 0.0
_JWKS_TTL = 3600.0  # seconds


def _get_jwks_client() -> Any:
    """Return a cached PyJWKClient, refreshing after _JWKS_TTL seconds."""
    global _JWKS_CLIENT, _JWKS_CLIENT_TS
    now = time.monotonic()
    with _JWKS_CLIENT_LOCK:
        if _JWKS_CLIENT is None or now - _JWKS_CLIENT_TS > _JWKS_TTL:
            from jwt import PyJWKClient  # PyJWT[cryptography]
            tenant_ref = _cfg()["tenant_ref"]
            jwks_uri = f"https://login.microsoftonline.com/{tenant_ref}/discovery/v2.0/keys"
            _JWKS_CLIENT = PyJWKClient(jwks_uri, lifespan=int(_JWKS_TTL))
            _JWKS_CLIENT_TS = now
        return _JWKS_CLIENT


def _verify_id_token(token: str) -> dict:
    """Verify the ID token RS256 signature, audience, issuer, and expiry via JWKS.

    Raises ValueError on any verification failure — callers must not admit
    the user if this raises.
    """
    try:
        import jwt as _jwt  # PyJWT
    except ImportError as exc:
        raise ValueError(
            "PyJWT[cryptography] is required for token verification. "
            "Run: pip install 'PyJWT[cryptography]>=2.8.0'"
        ) from exc

    jwks_client = _get_jwks_client()
    try:
        signing_key = jwks_client.get_signing_key_from_jwt(token)
    except Exception as exc:  # noqa: BLE001
        raise ValueError(f"Cannot retrieve signing key for token: {exc}") from exc

    c = _cfg()
    # Accept both v2.0 and v1.0 (sts.windows.net) issuers so the same code works
    # with single-tenant apps regardless of which endpoint issued the token.
    valid_issuers = [
        f"https://login.microsoftonline.com/{c['tenant_id']}/v2.0",
        f"https://sts.windows.net/{c['tenant_id']}/",
    ]

    try:
        claims = _jwt.decode(
            token,
            signing_key.key,
            algorithms=["RS256"],
            audience=c["client_id"],
            issuer=valid_issuers,
        )
    except _jwt.ExpiredSignatureError as exc:
        raise ValueError("ID token has expired") from exc
    except _jwt.InvalidIssuerError as exc:
        raise ValueError(f"Untrusted token issuer: {exc}") from exc
    except _jwt.InvalidTokenError as exc:
        raise ValueError(f"ID token signature is invalid: {exc}") from exc

    return claims


def is_enabled() -> bool:
    c = _cfg()
    return bool(c["tenant_id"] and c["client_id"] and c["client_secret"])


def generate_state_nonce() -> str:
    """Return a cryptographically random nonce, registered server-side.

    The nonce is stored in _STATE_STORE (not st.session_state) so it survives
    Microsoft's redirect: the callback is a fresh HTTP GET that Streamlit treats
    as a new session, wiping session_state. Call consume_state() on return to
    validate and single-use invalidate the nonce.
    """
    nonce = secrets.token_urlsafe(32)
    register_state(nonce)
    return nonce


def get_auth_url(state: str | None = None) -> str:
    """Build the Microsoft authorization URL.

    `state` should be a per-request random nonce from generate_state_nonce().
    If omitted, one is generated automatically.
    """
    if state is None:
        state = generate_state_nonce()
    c = _cfg()
    params = {
        "client_id":     c["client_id"],
        "response_type": "code",
        "redirect_uri":  c["redirect_uri"],
        "response_mode": "query",
        "scope":         _SCOPES,
        "state":         state,
        "prompt":        "login",
    }
    return f"{c['base']}/authorize?" + urllib.parse.urlencode(params)


def exchange_code_for_token(code: str) -> dict[str, Any]:
    """Exchange the authorization code for tokens."""
    c = _cfg()
    data = {
        "client_id":     c["client_id"],
        "client_secret": c["client_secret"],
        "code":          code,
        "redirect_uri":  c["redirect_uri"],
        "grant_type":    "authorization_code",
        "scope":         _SCOPES,
    }
    resp = _requests.post(f"{c['base']}/token", data=data, timeout=15)
    resp.raise_for_status()
    return resp.json()


def parse_user(token_result: dict) -> dict[str, Any]:
    """Verify the ID token signature and extract user info and role.

    Raises ValueError if the token is missing or RS256 signature verification
    fails. The caller must not trust the returned dict if this raises.
    """
    id_token = token_result.get("id_token", "")
    if not id_token:
        raise ValueError("Token response contains no id_token")
    claims = _verify_id_token(id_token)

    roles = claims.get("roles") or []
    roles_lower = [r.lower() for r in roles]

    if "admin" in roles_lower:
        role = "admin"
    elif "contributor" in roles_lower:
        role = "contributor"
    elif "viewer" in roles_lower:
        role = "viewer"
    else:
        # No explicit app role — default to contributor so authenticated tenant
        # users can run the demo without needing a manual role assignment in Azure.
        role = "contributor"

    return {
        "name":   claims.get("name") or claims.get("preferred_username", "Unknown"),
        "email":  claims.get("preferred_username", ""),
        "oid":    claims.get("oid", ""),
        "role":   role,
        "roles":  roles,
        "claims": claims,
    }
