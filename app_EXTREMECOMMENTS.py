# app_EXTREMECOMMENTS.py
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

Big change in this version:
- OAuth/PKCE/Subscribe logic has been moved into kick.py
- app.py is now “the web server + UI routing + webhook ingest”
- kick.py is “Kick-specific API/OAuth brain”
"""

# ===================================================================== IMPORTS =====================================================================
# Imports are the “tools on the workbench”.
# Reading them top to bottom tells you what this app is capable of doing.
#
# In the previous version, app.py imported PKCE helpers and built OAuth URLs itself.
# In the NEW version, app.py delegates Kick-specific behavior to a local module: kick.py.

# --- Data / logging / OS ---
import json     # Read/write JSON files (tokens, payload snapshots)
import logging  # Structured logs with levels (INFO/WARNING/ERROR)
import os       # Environment variables + file paths

import urllib.parse
# urllib.parse helps safely encode strings for URLs (especially error messages).
# Example: turning "Invalid login session." into "Invalid%20login%20session."

# --- HTTP ---
import requests
# requests is an HTTP client. In *this* file, we mainly use it for the exception type
# (requests.RequestException) during error handling.
# The actual Kick HTTP calls happen inside kick.py.

# --- Fancy logging output ---
from colorlog import ColoredFormatter  # Adds colors to log output (nice for humans)

# --- Environment variables from .env ---
from dotenv import load_dotenv  # Loads .env into environment variables

# --- FastAPI framework pieces ---
from fastapi import Body, FastAPI, Header, Request
# Body(...) tells FastAPI “parse JSON body into this parameter”
# Header(...) lets us read request headers (Accept)
# Request gives access to request info (needed for templates)

# --- Responses / UI routing ---
from fastapi.responses import HTMLResponse, RedirectResponse
# HTMLResponse tells FastAPI "this route returns HTML"
# RedirectResponse sends user to another URL (browser-friendly flow)

# --- Serving front-end assets ---
from fastapi.staticfiles import StaticFiles
# StaticFiles serves CSS/JS/images
from fastapi.templating import Jinja2Templates
# Jinja2Templates renders HTML templates with variables

import kick  # local module (kick.py)
# This is the major refactor:
# - kick.py now owns Kick-specific logic:
#   - start_login()   -> build auth URL, store state/verifier internally
#   - pop_verifier()  -> retrieve verifier for callback validation
#   - exchange_code_for_token() -> token exchange with Kick OAuth
#   - do_subscribe()  -> subscribe to events
#   - WEBHOOK_URL     -> webhook URL/config shared with templates
#
# Result:
# - app.py stays small and readable
# - kick.py becomes the “Kick integration” module

# ===================================================================== CONFIG / CONSTANTS =====================================================================
# Configuration = “things that can change without changing the logic”.
# Typically: env vars, file paths, debug flags.

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
            "DEBUG": "cyan",
            "INFO": "green",
            "WARNING": "yellow",
            "ERROR": "red",
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
# Using a named logger helps later if you split the code into multiple modules.
log = logging.getLogger("openstreamkit")

# ---------- Debug Flags ----------
# DEBUG_PAYLOADS controls whether we dump incoming webhook payloads to disk.
# This is fantastic for learning (see the raw structure Kick sends).
# It can be risky in production because payloads can contain user data.
DEBUG_PAYLOADS = os.getenv("DEBUG_PAYLOADS", "0") == "1"

# JSON_DIR is a folder where we store runtime artifacts:
# - token.json
# - last webhook payload
# - last chat/follow snapshots (optional)
# - last subscribe response
JSON_DIR = os.getenv("JSON_DIR", "json")

# DISABLE_DOCS toggles FastAPI's /docs, /redoc, /openapi.json endpoints.
# Useful if you don't want to expose docs publicly when hosting.
DISABLE_DOCS = os.getenv("DISABLE_DOCS", "0") == "1"

# ---------- Local Files ----------
# TOKEN_FILE: where we persist the OAuth token response.
TOKEN_FILE = os.getenv("TOKEN_FILE", "token.json")

# LAST_WEBHOOK_FILE: where we persist the most recent webhook payload (debug).
LAST_WEBHOOK_FILE = os.getenv("LAST_WEBHOOK_FILE", "last_webhook.json")

# ---------- In-memory Runtime State ----------
# In-memory state disappears on server restart.
# So: if you need it across restarts, save it to disk.
TOKENS: dict[str, str] = {}

# =====================================================================
# ACTION HELPERS
# (Side effects: file IO, logging, state mutation)
# =====================================================================

def save_json(data: dict, filename: str) -> None:
    """
    Save JSON into JSON_DIR/filename.
    filename should be a simple name like "token.json".

    Why this exists:
    - We save multiple artifacts in multiple places.
    - Centralizing keeps behavior consistent and reduces copy/paste.
    """
    path = os.path.join(JSON_DIR, filename)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)


def load_token() -> dict | None:
    """
    Load saved OAuth token from disk if present.

    Returns:
    - dict if token file exists
    - None if not found (normal on first run)
    """
    try:
        path = os.path.join(JSON_DIR, TOKEN_FILE)
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return None


def log_chat_message(payload: dict):
    """Side-effect: log + optionally persist a chat message."""
    sender = payload.get("sender", {}).get("username", "unknown")
    content = payload.get("content", "")
    log.info("[CHAT] %s: %s", sender, content)

    if DEBUG_PAYLOADS:
        save_json(payload, "last_chat.json")


def log_follow_event(payload: dict):
    """Side-effect: log + optionally persist a follow event."""
    user = payload.get("follower", {}).get("username", "unknown")
    log.info("[FOLLOW] %s", user)

    if DEBUG_PAYLOADS:
        save_json(payload, "last_follow.json")

# =====================================================================
# DESCRIPTOR FUNCTIONS
# (Interpret inputs and decide what action to take)
# =====================================================================

def describe_kick_payload(payload: dict) -> str:
    """
    Inspect an incoming Kick webhook payload and classify it.

    Returns:
        - "chat"
        - "follow"
        - "unknown"

    Why a descriptor?
    - It keeps /kick/webhook clean.
    - It separates “understanding” from “doing”.

    How this works:
    - We detect event type by “shape” (which keys exist).
    - Later you could upgrade to explicit event.type parsing if Kick provides it.
    """
    if "message_id" in payload and "sender" in payload and "content" in payload:
        return "chat"

    if "follower" in payload:
        return "follow"

    return "unknown"

# =====================================================================
# STARTUP LOGIC (RUNS ONCE)
# =====================================================================

# Ensure json/ directory exists so saves don't fail.
os.makedirs(JSON_DIR, exist_ok=True)

# Load existing token (if any) so you don't have to log in every restart.
saved = load_token()
if saved and "access_token" in saved:
    TOKENS["access_token"] = saved["access_token"]
    log.info("Loaded token from %s/%s", JSON_DIR, TOKEN_FILE)

# =====================================================================
# FASTAPI APP SETUP
# =====================================================================

# Create the FastAPI app.
# docs_url/redoc_url/openapi_url are disabled when DISABLE_DOCS=1
app = FastAPI(
    docs_url=None if DISABLE_DOCS else "/docs",
    redoc_url=None if DISABLE_DOCS else "/redoc",
    openapi_url=None if DISABLE_DOCS else "/openapi.json",
)

# Serve templates/static from ./web
app.mount("/static", StaticFiles(directory="web"), name="static")
templates = Jinja2Templates(directory="web")

# =====================================================================
# ROUTES — UI / AUTH FLOW
# =====================================================================

@app.get("/")
def root():
    # Simple UX: visiting the root redirects to the login page.
    return RedirectResponse("/login")


@app.get("/login", response_class=HTMLResponse)
def login_page(request: Request):
    """
    Login UI route.

    New behavior:
    - Instead of building auth URL here, we call kick.start_login()

    kick.start_login():
    - generates PKCE verifier + challenge
    - generates state (anti-CSRF + correlation token)
    - stores verifier keyed by state *inside kick.py*
    - returns (auth_url, state)
    """
    auth_url, _state = kick.start_login()
    # We don't need _state here because:
    # - the browser will carry state into the /callback querystring
    # - kick.py will use that state to look up the verifier

    return templates.TemplateResponse(
        "index.html",
        {
            "request": request,
            "auth_url": auth_url,
        },
    )


@app.get("/success", response_class=HTMLResponse)
def success_page(request: Request):
    # Display a friendly success page after OAuth+subscribe flow.
    return templates.TemplateResponse("success.html", {"request": request})


@app.get("/failure", response_class=HTMLResponse)
def failure_page(request: Request, msg: str | None = None):
    # Display a friendly failure page.
    # msg is optional and comes from query string: /failure?msg=...
    return templates.TemplateResponse("failure.html", {"request": request, "message": msg})


@app.get("/partial-success", response_class=HTMLResponse)
def partial_success_page(request: Request, msg: str | None = None):
    """
    Partial success page:
    - OAuth was successful
    - but subscription failed

    The template can show:
    - a clean message
    - the webhook URL (so users can verify their tunnel URL)
    """
    return templates.TemplateResponse(
        "partial_success.html",
        {
            "request": request,
            "message": msg,
            "webhook_url": kick.WEBHOOK_URL,
        },
    )

# =====================================================================
# ROUTES — OAUTH CALLBACK / SUBSCRIPTION
# =====================================================================

@app.get("/callback")
def callback(code: str, state: str):
    """
    OAuth callback endpoint.

    Kick redirects the browser here with:
    - code: short-lived authorization code
    - state: must match the state we generated earlier

    Flow:
    1) ask kick.py for the verifier using the state
    2) exchange code + verifier for a token
    3) save token to disk (persist)
    4) subscribe to events
    5) redirect to success / partial-success / failure
    """
    verifier = kick.pop_verifier(state)
    if not verifier:
        # Most common causes:
        # - server restarted (kick.py memory store cleared)
        # - user clicked an old callback URL
        msg = urllib.parse.quote("Invalid or expired login session.")
        return RedirectResponse(f"/failure?msg={msg}", status_code=302)

    try:
        token = kick.exchange_code_for_token(code, verifier)
    except requests.RequestException as e:
        # We keep the user-facing message simple,
        # and log the detailed exception for debugging.
        log.error("Token exchange failed: %s", e)
        msg = urllib.parse.quote("Token exchange failed.")
        return RedirectResponse(f"/failure?msg={msg}", status_code=302)

    # Persist token response (so restarts don't require relogin).
    save_json(token, TOKEN_FILE)

    access_token = token.get("access_token")
    if not access_token:
        # Defensive: if API changed or response is malformed
        msg = urllib.parse.quote("Login succeeded but no access token returned.")
        return RedirectResponse(f"/failure?msg={msg}", status_code=302)

    TOKENS["access_token"] = access_token

    # Subscribe to events so Kick starts sending webhook payloads.
    sub = kick.do_subscribe(access_token)
    save_json(sub, "last_subscribe_response.json")

    if sub.get("status_code", 0) >= 400:
        msg = urllib.parse.quote("Subscribed failed. See logs.")
        return RedirectResponse(f"/partial-success?msg={msg}", status_code=302)

    return RedirectResponse("/success", status_code=302)

# =====================================================================
# ROUTES — WEBHOOK INGEST
# =====================================================================

@app.post("/kick/webhook")
async def kick_webhook(payload: dict = Body(...)):
    """
    Webhook ingest endpoint.

    Kick will POST events here.
    We:
    1) optionally save the raw payload (debug)
    2) classify it (chat/follow/unknown)
    3) run the relevant handler
    """
    if DEBUG_PAYLOADS:
        save_json(payload, LAST_WEBHOOK_FILE)

    event_type = describe_kick_payload(payload)

    if event_type == "chat":
        log_chat_message(payload)
        return {"ok": True}

    if event_type == "follow":
        log_follow_event(payload)
        return {"ok": True}

    log.error("Unknown payload shape. keys=%s", list(payload.keys()))
    return {"ok": True}
