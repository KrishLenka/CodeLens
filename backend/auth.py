"""JWT helpers and GitHub OAuth utilities."""
from __future__ import annotations

import os
import secrets
import sys
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

import jwt
from fastapi import Cookie, HTTPException, status

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from tools.project_env import load_project_env

load_project_env()

GITHUB_AUTH_URL = "https://github.com/login/oauth/authorize"
GITHUB_TOKEN_URL = "https://github.com/login/oauth/access_token"
GITHUB_USER_URL = "https://api.github.com/user"

_JWT_SECRET = os.getenv("JWT_SECRET") or secrets.token_hex(32)
_JWT_ALGORITHM = "HS256"
_COOKIE_NAME = "codelens_session"


def _backend_url() -> str:
    return os.getenv("BACKEND_URL", "http://localhost:8000").rstrip("/")


def _frontend_url() -> str:
    return os.getenv("FRONTEND_URL", "http://localhost:3000").rstrip("/")


def oauth_redirect_uri() -> str:
    return f"{_backend_url()}/api/auth/callback"


def build_github_login_url() -> str:
    client_id = os.getenv("GITHUB_CLIENT_ID", "")
    state = secrets.token_hex(16)
    params = urlencode({
        "client_id": client_id,
        "redirect_uri": oauth_redirect_uri(),
        "scope": "read:user",
        "state": state,
    })
    return f"{GITHUB_AUTH_URL}?{params}", state


def exchange_code_for_user(code: str) -> dict[str, Any]:
    """Exchange OAuth code for GitHub user profile dict."""
    import httpx

    client_id = os.getenv("GITHUB_CLIENT_ID", "")
    client_secret = os.getenv("GITHUB_CLIENT_SECRET", "")

    token_resp = httpx.post(
        GITHUB_TOKEN_URL,
        data={"client_id": client_id, "client_secret": client_secret, "code": code},
        headers={"Accept": "application/json"},
        timeout=15,
    )
    token_resp.raise_for_status()
    token_data = token_resp.json()
    access_token = token_data.get("access_token", "")

    user_resp = httpx.get(
        GITHUB_USER_URL,
        headers={"Authorization": f"token {access_token}", "Accept": "application/vnd.github+json"},
        timeout=15,
    )
    user_resp.raise_for_status()
    profile = user_resp.json()

    return {
        "username": profile.get("login", ""),
        "avatar_url": profile.get("avatar_url", ""),
        "access_token": access_token,
    }


def create_session_token(user: dict[str, Any]) -> str:
    return jwt.encode(
        {"username": user["username"], "avatar_url": user.get("avatar_url", "")},
        _JWT_SECRET,
        algorithm=_JWT_ALGORITHM,
    )


def decode_session_token(token: str) -> dict[str, Any]:
    try:
        return jwt.decode(token, _JWT_SECRET, algorithms=[_JWT_ALGORITHM])
    except jwt.PyJWTError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired session",
        ) from exc


def get_current_user(codelens_session: str | None = Cookie(default=None)) -> dict[str, Any]:
    """FastAPI dependency — resolves the logged-in user from the session cookie."""
    if not codelens_session:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
        )
    return decode_session_token(codelens_session)
