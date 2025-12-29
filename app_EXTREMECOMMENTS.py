"""
OpenStreamKit (learning-first)

This project is written to be approachable for beginners:
- clear sections (imports → config → helpers → startup → routes)
- minimal “magic”
- comments explain *why* something exists

Goal: become a local-first stream automation tool that can be extended over time.

===============================================================================
TEACHING TWIN NOTE (EXTREMECOMMENTS MODE)
===============================================================================
This file is designed to be the “director’s commentary” track for the code.

RULES:
- The executable behavior should match the non-teaching version.
- Only comments, whitespace, and explanations should differ.
- If the code behaves differently than the non-teaching twin, treat that as a bug.
"""
# ===================================================================== IMPORTS =====================================================================
# Imports are the “tools on the workbench”.
# Reading them top to bottom tells you what this app is capable of doing.

# --- PKCE / OAuth helpers ---
import base64   # Used to base64-url encode bytes (PKCE challenge)
import hashlib  # Used to SHA-256 hash the PKCE verifier (PKCE S256)

# --- Data / logging / OS ---
import json     # Read/write JSON files (tokens, payload snapshots)
import logging  # Structured logs with levels (INFO/WARNING/ERROR)
import os       # Environment variables + file paths
import requests # HTTP client for talking to Kick OAuth + Kick API
import secrets  # Cryptographically secure random strings (PKCE + state)

# --- Fancy logging output ---
from colorlog import ColoredFormatter  # Adds colors to log output (nice for humans)

# --- Environment variables from .env ---
from dotenv import load_dotenv  # Loads .env into environment variables

# --- FastAPI framework pieces ---
from fastapi import Body, FastAPI, Request
# Body(...) tells FastAPI “parse JSON body into this parameter”
# Request gives access to request info (needed for templates)
from fastapi import Header
# Header(...) lets us read HTTP headers (here: Accept) to decide JSON vs HTML response

# --- Responses / UI routing ---
from fastapi.responses import HTMLResponse, RedirectResponse
# HTMLResponse tells FastAPI "this returns HTML"
# RedirectResponse sends user to another URL (browser-friendly flow)

# --- Serving front-end assets ---
from fastapi.staticfiles import StaticFiles
# StaticFiles serves files like CSS/JS/images
from fastapi.templating import Jinja2Templates
# Jinja2Templates lets us render HTML templates with variables

import urllib.parse
# urllib.parse helps build and safely encode URLs and query parameters.
# We use it for:
# - building the Kick OAuth authorize URL
# - URL-encoding error messages for redirects

# ===================================================================== CONFIG / CONSTANTS =====================================================================
# Configuration = “things that can change without changing the logic”.
# Typically: env vars, file paths, debug flags, URLs.

# Load environment variables from .env
# MUST happen before reading os.environ / os.getenv.
load_dotenv()

# ---------- Logging ----------
# LOG_LEVEL controls how chatty the application is in the terminal.
# DEBUG  -> extremely detailed
# INFO   -> normal
# WARNING/ERROR -> only important problems
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()

# StreamHandler means “write logs to the console/terminal”.
handler = logging.StreamHandler()

# ColoredFormatter makes log lines easier to scan with your eyeballs.
handler.setFormatter(
    ColoredFormatter(
        "%(log_color)s%(asctime)s | %(levelname)s | %(message)s",
        # A mapping of level name -> color name
        log_colors={
            "DEBUG":    "cyan",
            "INFO":     "green",
            "WARNING":  "yellow",
            "ERROR":    "red",
            "CRITICAL": "bold_red",
        },
    )
)

# logging.basicConfig wires up Python logging globally.
# getattr(logging, LOG_LEVEL, logging.INFO) means:
# - if LOG_LEVEL is a real logging level (like "DEBUG"), use it
# - otherwise default to logging.INFO
logging.basicConfig(level=getattr(logging, LOG_LEVEL, logging.INFO), handlers=[handler])

# Create a named logger for this app.
# Using a named logger lets you filter logs per module later if you split files.
log = logging.getLogger("openstreamkit")

# ---------- Debug Flags ----------
# DEBUG_PAYLOADS controls whether we dump incoming webhook payloads to disk.
# This is fantastic for learning (see the raw structure Kick sends).
# It can be risky in production because payloads can contain user data.
DEBUG_PAYLOADS = os.getenv("DEBUG_PAYLOADS", "0") == "1"

# JSON_DIR is a folder where we store runtime artifacts:
# - token.json
# - last webhook payload
# - last subscribe response
JSON_DIR = "json"

# DISABLE_DOCS toggles FastAPI's /docs, /redoc, /openapi.json endpoints.
# Useful if you don't want to expose docs publicly when hosting.
DISABLE_DOCS = os.getenv("DISABLE_DOCS", "0") == "1"

# ---------- Local Files ----------
# TOKEN_FILE: where we persist the OAuth token response.
TOKEN_FILE = os.getenv("TOKEN_FILE", "token.json")

# LAST_WEBHOOK_FILE: where we persist the most recent webhook payload (debug).
LAST_WEBHOOK_FILE = os.getenv("LAST_WEBHOOK_FILE", "last_webhook.json")

# ---------- Kick OAuth / API ----------
# OAuth host = login/token exchange server
OAUTH_HOST = "https://id.kick.com"

# API host = main Kick API server
API_HOST = "https://api.kick.com"

# REDIRECT_URI is where Kick sends the user after login authorization.
# Must match what is configured in your Kick developer application.
REDIRECT_URI = os.getenv("KICK_REDIRECT_URI", "http://localhost:8000/callback")

# WEBHOOK_URL is the URL Kick will call with events.
# KICK_WEBHOOK_PUBLIC_URL should be a public URL (often a tunnel).
WEBHOOK_URL = f"{os.environ['KICK_WEBHOOK_PUBLIC_URL']}/kick/webhook"

# Required env vars (fail fast with a clear error)
# Using os.environ["NAME"] intentionally throws KeyError if missing.
# That is GOOD in this situation: we want a loud failure, not a silent broken app.
CLIENT_ID = os.environ["KICK_CLIENT_ID"]
CLIENT_SECRET = os.environ["KICK_CLIENT_SECRET"]

# ---------- In-memory Runtime State ----------
# These dicts are stored in RAM only. If the server restarts, they reset.
# Anything important should be persisted to disk (token.json etc).
PKCE_STORE: dict[str, str] = {}
# PKCE_STORE maps: state -> verifier
# "state" comes from /login and returns to us at /callback
# "verifier" is the secret used in PKCE for the token exchange.

TOKENS: dict[str, str] = {}
# TOKENS holds the access token so we don't have to reread from disk every request.


# ===================================================================== HELPER FUNCTIONS =====================================================================
# Helpers are “small tools” the rest of the code uses.
# Keeping them here:
# - reduces duplication
# - keeps routes shorter and easier to understand

def save_json(data: dict, filename: str) -> None:
    """
    Save a Python dict to a JSON file inside JSON_DIR.

    Why this exists:
    - We write JSON files in multiple places (token, payload snapshots, responses).
    - Centralizing it avoids repeating open(...)/json.dump(...) everywhere.

    Parameters:
    - data: the dict you want to save (must be JSON-serializable)
    - filename: the file name inside JSON_DIR (e.g. "token.json")
    """
    with open(os.path.join(JSON_DIR, filename), "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)


def load_token() -> dict | None:
    """
    Load token JSON from disk if it exists.

    Returns:
    - dict: token data if file exists
    - None: if file does not exist

    Important:
    - We only catch FileNotFoundError.
    - If the JSON is corrupted, json.load(...) will throw (and that's okay).
      A corrupted token should be visible, not silently ignored.
    """
    try:
        path = os.path.join(JSON_DIR, TOKEN_FILE)
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return None


def pkce_verifier():
    """
    Generate a PKCE verifier (secret random string).

    PKCE mental model:
    - verifier = secret password you keep
    - challenge = hash(verifier) you show publicly
    - later you prove you know the verifier by sending it during token exchange
    """
    return secrets.token_urlsafe(64)


def pkce_challenge_s256(verifier: str) -> str:
    """
    Convert verifier -> PKCE challenge using SHA-256 (S256 method).

    Steps:
    1) hash verifier bytes with SHA-256
    2) base64-url encode the digest
    3) strip '=' padding because OAuth PKCE expects base64url without padding
    """
    digest = hashlib.sha256(verifier.encode()).digest()
    return base64.urlsafe_b64encode(digest).decode().rstrip("=")


def handle_chat_message(payload):
    """
    Handle a Kick chat.message.sent event.

    payload is expected to include:
    - sender.username
    - content

    This handler:
    - logs a nice readable line to terminal
    - optionally saves payload to json/last_chat.json when DEBUG_PAYLOADS=1
    """
    sender = payload.get("sender", {}).get("username", "unknown")
    content = payload.get("content", "")
    log.info(f"[CHAT] {sender}: {content}")

    if DEBUG_PAYLOADS:
        save_json(payload, "last_chat.json")


def handle_follow(payload):
    """
    Handle a Kick channel.followed event.

    payload is expected to include:
    - follower.username

    This handler:
    - logs the follower name
    - optionally saves payload to json/last_follow.json when DEBUG_PAYLOADS=1
    """
    user = payload.get("follower", {}).get("username", "unknown")
    log.info(f"[FOLLOW] {user}")

    if DEBUG_PAYLOADS:
        save_json(payload, "last_follow.json")


def do_subscribe(access_token: str) -> dict:
    """
    Subscribe to multiple Kick events in one API call.

    Why this is a helper:
    - /callback does auto-subscribe after login
    - /subscribe allows manual retry
    - /subscribe-ui does a UI-friendly retry
    All three want the same subscription logic.

    Returns a dict with:
    - status_code: HTTP status
    - text: raw response body
    - json: parsed JSON response if response is JSON, else None
    """
    body = {
        "events": [
            {"name": "chat.message.sent", "version": 1},
            {"name": "channel.followed", "version": 1},
            {"name": "channel.subscription.created", "version": 1},
            {"name": "channel.subscription.gifted", "version": 1},
        ],
        # method tells Kick how to deliver events.
        # "webhook" means: Kick POSTs to your webhook URL.
        "method": "webhook",
    }

    # POST to Kick's subscriptions endpoint.
    # Authorization header must include Bearer token.
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

# ===================================================================== STARTUP LOGIC (RUNS ONCE) =====================================================================
# This section runs when the Python module is imported (when uvicorn starts).

# Ensure json/ directory exists so saves don't fail.
os.makedirs(JSON_DIR, exist_ok=True)

# Load existing token (if any) so you don't have to log in every restart.
saved = load_token()
if saved and "access_token" in saved:
    TOKENS["access_token"] = saved["access_token"]

# ===================================================================== EVENT HANDLERS / ROUTES =====================================================================
# Routes are the HTTP endpoints your browser and Kick will hit.

# Create the FastAPI app.
# docs_url/redoc_url/openapi_url are disabled when DISABLE_DOCS=1
app = FastAPI(
    docs_url=None if DISABLE_DOCS else "/docs",
    redoc_url=None if DISABLE_DOCS else "/redoc",
    openapi_url=None if DISABLE_DOCS else "/openapi.json",
)

# Serve the /static path from the local "web" directory.
# That means files like:
# - web/style.css
# - web/app.js
# - web/logo.png
# can be requested at:
# - /static/style.css
# - /static/app.js
# - /static/logo.png
app.mount("/static", StaticFiles(directory="web"), name="static")

# Jinja2Templates lets us render HTML templates from the web/ directory.
# So "index.html" means "web/index.html"
templates = Jinja2Templates(directory="web")


@app.get("/")
def root():
    """
    Root route:
    If someone visits the site without a path, redirect them to /login.

    Why redirect?
    - This app's primary user flow begins at /login
    - It also gives a nice “landing page” experience if /login is HTML
    """
    return RedirectResponse("/login")


@app.get("/login", response_class=HTMLResponse)
def login_page(request: Request):
    """
    Login UI route (HTML page):
    - Generates PKCE verifier/challenge + state
    - Builds Kick OAuth authorize URL
    - Renders web/index.html with the auth_url injected

    Important:
    - This replaces the old “return JSON with open_this_url” pattern
      with a friendlier UI.
    """
    verifier = pkce_verifier()
    challenge = pkce_challenge_s256(verifier)
    state = secrets.token_urlsafe(16)

    # Store verifier keyed by state so /callback can retrieve it.
    PKCE_STORE[state] = verifier

    # OAuth authorize query parameters
    q = {
        "response_type": "code",
        "client_id": CLIENT_ID,
        "redirect_uri": REDIRECT_URI,
        "scope": "events:subscribe",
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "state": state,
    }

    # Full URL user will be sent to for Kick login/consent
    auth_url = f"{OAUTH_HOST}/oauth/authorize?{urllib.parse.urlencode(q)}"

    # Render template web/index.html with variables:
    # - request (required by FastAPI templating)
    # - auth_url (used by the page to make a login button/link)
    return templates.TemplateResponse(
        "index.html",
        {
            "request": request,
            "auth_url": auth_url,
        },
    )


@app.get("/success", response_class=HTMLResponse)
def success_page(request: Request):
    """
    Simple success page.
    Used after:
    - OAuth succeeds AND subscription succeeds
    - manual subscribe retry succeeds
    """
    return templates.TemplateResponse("success.html", {"request": request})


@app.get("/failure", response_class=HTMLResponse)
def failure_page(request: Request, msg: str | None = None):
    """
    Failure page.

    msg comes from query parameter ?msg=...
    Example:
      /failure?msg=No%20token%20yet

    We keep messages URL-encoded when redirecting, then show them here.
    """
    return templates.TemplateResponse("failure.html", {"request": request, "message": msg})


@app.get("/partial-success", response_class=HTMLResponse)
def partial_success_page(request: Request, msg: str | None = None):
    """
    Partial success page:
    - OAuth login worked
    - But subscription creation failed

    We show the webhook URL so users can confirm it’s correct,
    and we point them to the saved debug file.
    """
    return templates.TemplateResponse(
        "partial_success.html",
        {
            "request": request,
            "message": msg,
            "webhook_url": WEBHOOK_URL,
        },
    )


@app.get("/callback")
def callback(code: str, state: str):
    """
    OAuth callback endpoint.

    Kick redirects the user here with:
    - code: short-lived authorization code
    - state: must match the value we generated earlier

    We then:
    1) validate state and get PKCE verifier
    2) exchange code + verifier for an access token
    3) save token
    4) auto-subscribe to events
    5) redirect user to success/partial-success/failure UI
    """
    verifier = PKCE_STORE.pop(state, None)
    if not verifier:
        # This usually happens when:
        # - server restarted (PKCE_STORE cleared)
        # - user used an old callback URL
        # - state was tampered with
        log.warning("OAuth callback with invalid or expired state")
        msg = urllib.parse.quote("Invalid or expired login session (state). Please try again.")
        return RedirectResponse(f"/failure?msg={msg}", status_code=302)

    # Data for token exchange request
    data = {
        "grant_type": "authorization_code",
        "client_id": CLIENT_ID,
        "client_secret": CLIENT_SECRET,
        "redirect_uri": REDIRECT_URI,
        "code_verifier": verifier,  # proves we are the original /login request
        "code": code,
    }

    try:
        # Exchange authorization code for token.
        # OAuth token endpoints typically expect application/x-www-form-urlencoded.
        r = requests.post(
            f"{OAUTH_HOST}/oauth/token",
            data=data,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            timeout=20,
        )
        r.raise_for_status()
    except requests.RequestException as e:
        # If the HTTP request fails or returns an error status,
        # we log detailed info server-side and show a clean message to the user.
        status = getattr(getattr(e, "response", None), "status_code", None)
        body = getattr(getattr(e, "response", None), "text", "")
        log.error("Token exchange failed. status=%s body=%s err=%s", status, body, e)

        clean = "Token exchange failed. Check redirect URI + client credentials."
        msg = urllib.parse.quote(clean)
        return RedirectResponse(f"/failure?msg={msg}", status_code=302)

    # Parse token response JSON.
    token = r.json()

    # Persist token so a server restart still has access.
    save_json(token, TOKEN_FILE)

    # Grab access token from response
    access_token = token["access_token"]

    # Store token in memory for quick use
    TOKENS["access_token"] = access_token

    # Automatically attempt to subscribe right after auth.
    sub = do_subscribe(access_token)

    # Save subscription response for debugging.
    save_json(sub, "last_subscribe_response.json")

    log.info("Auto-subscribe: %s", sub["status_code"])

    # If subscription failed, we still consider OAuth a success,
    # so we show partial-success page and instruct user where to look.
    if sub.get("status_code", 0) >= 400:
        msg = urllib.parse.quote(
            "Authorized OK, but event subscription failed. See json/last_subscribe_response.json."
        )
        return RedirectResponse(f"/partial-success?msg={msg}", status_code=302)

    return RedirectResponse("/success", status_code=302)


@app.post("/subscribe")
def subscribe(accept: str | None = Header(default=None)):
    """
    Manual subscription endpoint.

    Why this exists:
    - Sometimes subscription fails temporarily
    - Sometimes you want to re-run subscribe without logging in again
    - This endpoint can return JSON or redirect HTML based on the Accept header

    Accept header behavior:
    - If client says it accepts text/html -> we redirect to UI pages
    - Otherwise -> return JSON (API style)
    """
    access_token = TOKENS.get("access_token")
    if not access_token:
        # If browser wants HTML, redirect to a friendly failure page.
        if accept and "text/html" in accept:
            msg = urllib.parse.quote("No token yet. Go to /login first.")
            return RedirectResponse(f"/failure?msg={msg}", status_code=302)
        # Otherwise return JSON error (API caller)
        return {"error": "No token yet. Go to /login first."}

    # Attempt subscription
    result = do_subscribe(access_token)

    # Save result so user can inspect the exact API response
    save_json(result, "last_subscribe_response.json")

    # If browser wants HTML, redirect to a UI page based on status.
    if accept and "text/html" in accept:
        if result.get("status_code", 0) >= 400:
            msg = urllib.parse.quote("Retry failed. Check json/last_subscribe_response.json.")
            return RedirectResponse(f"/partial-success?msg={msg}", status_code=302)
        return RedirectResponse("/success", status_code=302)

    # Otherwise return JSON for API callers.
    return result


@app.get("/subscribe-ui")
def subscribe_ui():
    """
    Convenience UI endpoint for re-subscribing.

    Differences from /subscribe:
    - always redirects to HTML pages
    - no Accept header logic
    - feels nicer for a human clicking buttons
    """
    access_token = TOKENS.get("access_token")
    if not access_token:
        msg = urllib.parse.quote("No token available. Please log in first.")
        return RedirectResponse(f"/failure?msg={msg}", status_code=302)

    result = do_subscribe(access_token)
    save_json(result, "last_subscribe_response.json")

    if result.get("status_code", 0) >= 400:
        msg = urllib.parse.quote(
            "Subscription retry failed. Check json/last_subscribe_response.json."
        )
        return RedirectResponse(f"/partial-success?msg={msg}", status_code=302)

    return RedirectResponse("/success", status_code=302)


@app.post("/kick/webhook")
async def kick_webhook(payload: dict = Body(...)):
    """
    Webhook endpoint (Kick calls THIS).

    Kick will POST event payloads to:
      {KICK_WEBHOOK_PUBLIC_URL}/kick/webhook

    FastAPI detail:
    - payload: dict = Body(...) means FastAPI will parse JSON automatically
      and hand you a Python dict.
    """

    if DEBUG_PAYLOADS:
        # Save the raw payload. Great for learning the schema of events.
        save_json(payload, LAST_WEBHOOK_FILE)

    # “Shape detection”:
    # We inspect the payload keys to decide which handler should run.
    #
    # This is beginner-friendly and works fine early on.
    # Later, you might implement:
    # - explicit event type field parsing
    # - pydantic models for payload schemas
    if "message_id" in payload and "sender" in payload and "content" in payload:
        handle_chat_message(payload)
        return {"ok": True}
    elif "follower" in payload:
        handle_follow(payload)
        return {"ok": True}

    # If we got here, we don't recognize the payload structure.
    # Log keys so we can learn and add support later.
    log.error("Unknown payload shape. keys=%s", list(payload.keys()))
    return {"ok": True}
