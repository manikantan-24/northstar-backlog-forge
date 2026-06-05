"""Microsoft Entra ID SSO — direct OAuth2 (no MSAL dependency).

Uses the standard OAuth2 authorization code flow directly via requests,
bypassing MSAL for reliability with new Entra ID tenants.
"""

from __future__ import annotations

import os
import urllib.parse
from typing import Any

import requests as _requests

TENANT_ID     = os.environ.get("ENTRA_TENANT_ID", "")
TENANT_DOMAIN = os.environ.get("ENTRA_TENANT_DOMAIN", "")
CLIENT_ID     = os.environ.get("ENTRA_CLIENT_ID", "")
CLIENT_SECRET = os.environ.get("ENTRA_CLIENT_SECRET", "")
REDIRECT_URI  = os.environ.get("ENTRA_REDIRECT_URI", "http://localhost:8501/")

# Use domain name in authority — more reliable for new tenants than GUID
_TENANT_REF   = TENANT_DOMAIN if TENANT_DOMAIN else TENANT_ID
_BASE         = f"https://login.microsoftonline.com/{_TENANT_REF}/oauth2/v2.0"
_SCOPES       = "openid profile"   # email scope removed — causes issues on some new tenants

_ENTRA_ENABLED = bool(TENANT_ID and CLIENT_ID and CLIENT_SECRET)


def is_enabled() -> bool:
    return _ENTRA_ENABLED


def get_auth_url(state: str = "backlog-synth") -> str:
    """Build the Microsoft authorization URL directly."""
    params = {
        "client_id":     CLIENT_ID,
        "response_type": "code",
        "redirect_uri":  REDIRECT_URI,
        "response_mode": "query",
        "scope":         _SCOPES,
        "state":         state,
        "prompt":        "login",   # force completely fresh login, no cached session
    }
    return f"{_BASE}/authorize?" + urllib.parse.urlencode(params)


def exchange_code_for_token(code: str) -> dict[str, Any]:
    """Exchange the authorization code for tokens."""
    data = {
        "client_id":     CLIENT_ID,
        "client_secret": CLIENT_SECRET,
        "code":          code,
        "redirect_uri":  REDIRECT_URI,
        "grant_type":    "authorization_code",
        "scope":         _SCOPES,
    }
    resp = _requests.post(f"{_BASE}/token", data=data, timeout=15)
    return resp.json()


def _decode_jwt_payload(token: str) -> dict:
    """Decode the payload of a JWT without verification (for ID token claims)."""
    import base64, json
    try:
        parts = token.split(".")
        if len(parts) < 2:
            return {}
        payload = parts[1]
        # Add padding
        payload += "=" * (4 - len(payload) % 4)
        return json.loads(base64.urlsafe_b64decode(payload))
    except Exception:  # noqa: BLE001
        return {}


def parse_user(token_result: dict) -> dict[str, Any]:
    """Extract user info and role from the token response."""
    # Decode the ID token to get claims
    id_token = token_result.get("id_token", "")
    claims   = _decode_jwt_payload(id_token) if id_token else {}

    # App roles come from the 'roles' claim.
    # Compare case-insensitively — Azure may return "Admin" or "admin".
    roles = claims.get("roles") or []
    roles_lower = [r.lower() for r in roles]

    if "admin" in roles_lower:
        role = "admin"
    elif "contributor" in roles_lower:
        role = "contributor"
    elif "viewer" in roles_lower:
        role = "viewer"
    else:
        role = "viewer"

    return {
        "name":   claims.get("name") or claims.get("preferred_username", "Unknown"),
        "email":  claims.get("preferred_username", ""),
        "oid":    claims.get("oid", ""),
        "role":   role,
        "roles":  roles,
        "claims": claims,
    }
