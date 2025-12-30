# kick.py
"""
Kick platform integration for OpenStreamKit.

This module is intentionally split into:
- Descriptor functions (pure logic, no side effects)
- Action functions (network calls, mutations, external effects)

This separation allows the app layer to reason about *what should happen*
before deciding *when to execute it*.
"""

# =====================================================================
# IMPORTS
# =====================================================================
import os
import base64
import hashlib
import secrets
import urllib.parse
import requests

from dotenv import load_dotenv
load_dotenv()

# =====================================================================
# CONFIG / CONSTANTS
# =====================================================================
OAUTH_HOST = "https://id.kick.com"
API_HOST = "https://api.kick.com"

REDIRECT_URI = os.getenv("KICK_REDIRECT_URI", "http://localhost:8000/callback")
WEBHOOK_URL = f"{os.environ['KICK_WEBHOOK_PUBLIC_URL']}/kick/webhook"

CLIENT_ID = os.environ["KICK_CLIENT_ID"]
CLIENT_SECRET = os.environ["KICK_CLIENT_SECRET"]

# PKCE verifier is per-login state (in-memory)
PKCE_STORE: dict[str, str] = {}

# =====================================================================
# DESCRIPTOR FUNCTIONS
# (Pure logic: build, parse, decide — no side effects)
# =====================================================================

def pkce_verifier() -> str:
    """Generate a secure PKCE verifier."""
    return secrets.token_urlsafe(64)


def pkce_challenge_s256(verifier: str) -> str:
    """Derive a PKCE S256 challenge from a verifier."""
    digest = hashlib.sha256(verifier.encode()).digest()
    return base64.urlsafe_b64encode(digest).decode().rstrip("=")


def build_auth_url(state: str, challenge: str) -> str:
    """
    Construct the Kick OAuth authorization URL.
    Pure function: input → URL string.
    """
    q = {
        "response_type": "code",
        "client_id": CLIENT_ID,
        "redirect_uri": REDIRECT_URI,
        "scope": "events:subscribe",
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "state": state,
    }
    return f"{OAUTH_HOST}/oauth/authorize?{urllib.parse.urlencode(q)}"


def start_login() -> tuple[str, str]:
    """
    Descriptor for initiating a login flow.

    Returns:
        (auth_url, state)
    """
    verifier = pkce_verifier()
    challenge = pkce_challenge_s256(verifier)
    state = secrets.token_urlsafe(16)

    PKCE_STORE[state] = verifier
    auth_url = build_auth_url(state, challenge)
    return auth_url, state


def pop_verifier(state: str) -> str | None:
    """
    Retrieve and remove a stored PKCE verifier.
    Prevents replay attacks.
    """
    return PKCE_STORE.pop(state, None)

# =====================================================================
# ACTION FUNCTIONS
# (Side effects: HTTP requests, mutations, external systems)
# =====================================================================

def exchange_code_for_token(code: str, verifier: str) -> dict:
    """
    Exchange OAuth authorization code for an access token.

    Side effects:
    - Network call to Kick OAuth API

    Raises:
        requests.HTTPError on failure
    """
    data = {
        "grant_type": "authorization_code",
        "client_id": CLIENT_ID,
        "client_secret": CLIENT_SECRET,
        "redirect_uri": REDIRECT_URI,
        "code_verifier": verifier,
        "code": code,
    }

    r = requests.post(
        f"{OAUTH_HOST}/oauth/token",
        data=data,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        timeout=20,
    )
    r.raise_for_status()
    return r.json()


def do_subscribe(access_token: str) -> dict:
    """
    Subscribe the authenticated user to Kick events.

    Side effects:
    - Network call to Kick Events API

    Returns:
        dict containing status_code, text, and optional json
    """
    body = {
        "events": [
            {"name": "chat.message.sent", "version": 1},
            {"name": "channel.followed", "version": 1},
            {"name": "channel.subscription.created", "version": 1},
            {"name": "channel.subscription.gifted", "version": 1},
        ],
        "method": "webhook",
    }

    r = requests.post(
        f"{API_HOST}/public/v1/events/subscriptions",
        json=body,
        headers={"Authorization": f"Bearer {access_token}"},
        timeout=20,
    )

    return {
        "status_code": r.status_code,
        "text": r.text,
        "json": r.json() if r.headers.get("content-type", "").startswith("application/json") else None,
    }
