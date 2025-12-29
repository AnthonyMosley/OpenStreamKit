"""
OpenStreamKit (learning-first)

This file is a TEACHING TWIN of the runnable version.

RULES FOR THIS FILE:
- Code behavior MUST remain identical to the runnable file
- Differences are comments, whitespace, and explanations only
- If behavior diverges, that is a BUG

This file exists so a beginner can read it top-to-bottom and
understand not just WHAT the code does, but WHY every piece exists.
"""

# =====================================================================
# IMPORTS
# =====================================================================
# Imports are grouped so readers can mentally categorize dependencies.
# This matters more for learning than for Python itself.

# -----------------------------
# Standard Library (built-in)
# -----------------------------

import base64
# base64 is used to convert binary data (bytes) into URL-safe text.
# OAuth PKCE requires this exact transformation step.

import hashlib
# hashlib provides cryptographic hashing functions.
# We use SHA-256 to create the PKCE challenge.

import os
# os lets us:
# - read environment variables
# - build file paths that work across operating systems

import secrets
# secrets is for *cryptographically secure* random values.
# DO NOT use random.random() for OAuth or security tokens.

import urllib.parse
# urllib.parse helps us build query strings safely.
# Example: turning a dict into ?a=1&b=2 without breaking URLs.

import json
# json lets us:
# - save API responses to disk
# - inspect payloads
# - debug webhook events

import logging
# logging is Python’s built-in logging system.
# Unlike print(), logging supports:
# - log levels
# - formatting
# - filtering
# - future redirection to files or services

# -----------------------------
# Third-party Libraries
# -----------------------------

import requests
# requests is a friendly HTTP client.
# We use it to talk to Kick’s OAuth server and API.

from fastapi import FastAPI, Request, Body
# FastAPI:
# - creates the web server
# - defines routes (/login, /callback, /webhook)
# - automatically parses JSON bodies

from dotenv import load_dotenv
# load_dotenv reads a `.env` file and injects values into os.environ.
# This keeps secrets OUT of source code.

from colorlog import ColoredFormatter
# colorlog adds color to logging output.
# This is purely for developer experience, not functionality.

# =====================================================================
# CONFIG / CONSTANTS
# =====================================================================
# This section defines configuration that affects behavior
# but is NOT logic by itself.

# ---------------------------------------------------------------------
# Environment Variable Loading
# ---------------------------------------------------------------------

# This must happen BEFORE reading any environment variables.
# If you forget this, os.getenv(...) will silently return None.
load_dotenv()

# ---------------------------------------------------------------------
# Logging Configuration
# ---------------------------------------------------------------------

# LOG_LEVEL controls verbosity.
# Common values: DEBUG, INFO, WARNING, ERROR, CRITICAL
# Default is INFO if not provided.
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()

# Create a stream handler (logs go to stdout / terminal)
handler = logging.StreamHandler()

# Attach a colored formatter so different log levels stand out visually.
handler.setFormatter(
    ColoredFormatter(
        "%(log_color)s%(asctime)s | %(levelname)s | %(message)s",
        log_colors={
            "DEBUG":    "cyan",
            "INFO":     "green",
            "WARNING":  "yellow",
            "ERROR":    "red",
            "CRITICAL": "bold_red",
        },
    )
)

# Initialize logging system
logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    handlers=[handler]
)

# Create a named logger for this project
log = logging.getLogger("openstreamkit")

# ---------------------------------------------------------------------
# Debug Flags
# ---------------------------------------------------------------------

# DEBUG_PAYLOADS toggles saving raw webhook payloads to disk.
# Useful for learning event schemas.
# Dangerous in production (payloads can contain user data).
DEBUG_PAYLOADS = os.getenv("DEBUG_PAYLOADS", "0") == "1"

# Directory where debug JSON files will be stored
JSON_DIR = "json"

# ---------------------------------------------------------------------
# Local File Storage
# ---------------------------------------------------------------------

# TOKEN_FILE stores OAuth tokens between restarts
TOKEN_FILE = os.getenv("TOKEN_FILE", "token.json")

# LAST_WEBHOOK_FILE stores the most recent webhook payload
LAST_WEBHOOK_FILE = os.getenv("LAST_WEBHOOK_FILE", "last_webhook.json")

# ---------------------------------------------------------------------
# Kick OAuth / API Configuration
# ---------------------------------------------------------------------

# OAuth server (login + token exchange)
OAUTH_HOST = "https://id.kick.com"

# API server (event subscriptions)
API_HOST = "https://api.kick.com"

# Redirect URI MUST match what is registered in Kick dev settings
REDIRECT_URI = os.getenv(
    "KICK_REDIRECT_URI",
    "http://localhost:8000/callback"
)

# Webhook URL where Kick sends events
# Usually points to a tunnel (ngrok, cloudflared, etc.)
WEBHOOK_URL = f"{os.environ['KICK_WEBHOOK_PUBLIC_URL']}/kick/webhook"

# These MUST exist or the app should crash immediately.
# Failing fast is intentional and correct.
CLIENT_ID = os.environ["KICK_CLIENT_ID"]
CLIENT_SECRET = os.environ["KICK_CLIENT_SECRET"]

# ---------------------------------------------------------------------
# In-memory Runtime State
# ---------------------------------------------------------------------

# PKCE_STORE temporarily maps:
#   state -> code_verifier
#
# This exists ONLY during OAuth login.
# It is cleared after /callback completes.
PKCE_STORE: dict[str, str] = {}

# TOKENS holds the active access token in memory.
# This avoids reading from disk on every request.
TOKENS: dict[str, str] = {}

# =====================================================================
# HELPER FUNCTIONS
# =====================================================================
# Helper functions are small, focused, reusable pieces of logic.

def save_json(data: dict, filename: str) -> None:
    """
    Save JSON data to disk.

    WHY THIS EXISTS:
    - Avoid duplicating file-write logic
    - Central place to change formatting / behavior later
    """
    with open(os.path.join(JSON_DIR, filename), "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)


def load_token() -> dict | None:
    """
    Load OAuth token from disk if it exists.

    Returns:
    - dict if token.json exists
    - None if file does not exist
    """
    try:
        path = os.path.join(JSON_DIR, TOKEN_FILE)
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        # First run = no token yet
        return None


def pkce_verifier() -> str:
    """
    Generate a PKCE verifier.

    PKCE protects OAuth flows from interception attacks.
    The verifier is SECRET and never sent directly to Kick.
    """
    return secrets.token_urlsafe(64)


def pkce_challenge_s256(verifier: str) -> str:
    """
    Convert a PKCE verifier into a PKCE challenge.

    Steps:
    1. SHA-256 hash
    2. Base64 URL-safe encode
    3. Strip '=' padding
    """
    digest = hashlib.sha256(verifier.encode()).digest()
    return base64.urlsafe_b64encode(digest).decode().rstrip("=")


def handle_chat_message(payload):
    """
    Handle chat.message.sent events.
    """
    sender = payload.get("sender", {}).get("username", "unknown")
    content = payload.get("content", "")
    log.info(f"[CHAT] {sender}: {content}")

    if DEBUG_PAYLOADS:
        save_json(payload, "last_chat.json")


def handle_follow(payload):
    """
    Handle channel.followed events.
    """
    user = payload.get("follower", {}).get("username", "unknown")
    log.info(f"[FOLLOW] {user}")

    if DEBUG_PAYLOADS:
        save_json(payload, "last_follow.json")

# =====================================================================
# STARTUP LOGIC (RUNS ONCE)
# =====================================================================

# Ensure JSON directory exists before writing files
os.makedirs(JSON_DIR, exist_ok=True)

# Attempt to restore previous OAuth session
saved = load_token()

# If token exists, preload it into memory
if saved and "access_token" in saved:
    TOKENS["access_token"] = saved["access_token"]

# =====================================================================
# EVENT HANDLERS / ROUTES
# =====================================================================

# Create FastAPI app instance
app = FastAPI()

# ---------------------------------------------------------------------
# LOGIN ROUTE
# ---------------------------------------------------------------------

@app.get("/login")
def login():
    """
    Step 1: Start OAuth login.

    This endpoint:
    - Generates PKCE values
    - Builds Kick authorization URL
    - Returns the URL for the user to open
    """
    verifier = pkce_verifier()
    challenge = pkce_challenge_s256(verifier)
    state = secrets.token_urlsafe(16)

    PKCE_STORE[state] = verifier

    q = {
        "response_type": "code",
        "client_id": CLIENT_ID,
        "redirect_uri": REDIRECT_URI,
        "scope": "events:subscribe",
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "state": state,
    }

    url = f"{OAUTH_HOST}/oauth/authorize?{urllib.parse.urlencode(q)}"
    return {"open_this_url_in_browser": url}

# ---------------------------------------------------------------------
# CALLBACK ROUTE
# ---------------------------------------------------------------------

@app.get("/callback")
def callback(code: str, state: str):
    """
    Step 2: OAuth callback.

    Exchanges authorization code + verifier for access token.
    """
    verifier = PKCE_STORE.pop(state, None)
    if not verifier:
        return {"error": "Unknown/expired state (restart login)"}

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

    token = r.json()
    save_json(token, TOKEN_FILE)

    TOKENS["access_token"] = token["access_token"]

    return {
        "ok": True,
        "next": "Call POST /subscribe",
        "token_keys": list(token.keys()),
    }

# ---------------------------------------------------------------------
# SUBSCRIBE ROUTE
# ---------------------------------------------------------------------

@app.post("/subscribe")
def subscribe():
    """
    Create Kick event subscriptions.
    """
    access_token = TOKENS.get("access_token")
    if not access_token:
        return {"error": "No token yet. Go to /login first."}

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

    log.info("[SUBSCRIBE]%s %s", r.status_code, r.text)
    return {
        "status_code": r.status_code,
        "response": r.json() if r.headers.get("content-type", "").startswith("application/json") else r.text
    }

# ---------------------------------------------------------------------
# WEBHOOK ROUTE
# ---------------------------------------------------------------------

@app.post("/kick/webhook")
async def kick_webhook(payload: dict = Body(...)):
    """
    Receive webhook events from Kick.
    """

    if DEBUG_PAYLOADS:
        save_json(payload, LAST_WEBHOOK_FILE)

    if "message_id" in payload and "sender" in payload and "content" in payload:
        handle_chat_message(payload)
        return {"ok": True}

    elif "follower" in payload:
        handle_follow(payload)
        return {"ok": True}

    log.error("Unknown payload shape. keys=%s", list(payload.keys()))
    return {"ok": True}
