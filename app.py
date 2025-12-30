# app.py
"""
OpenStreamKit (learning-first)

This project is written to be approachable for beginners:
- clear sections (imports → config → helpers → startup → routes)
- minimal “magic”
- comments explain *why* something exists

Goal: become a local-first stream automation tool that can be extended over time.
"""

# =====================================================================
# IMPORTS
# =====================================================================
import json
import logging
import os
import urllib.parse

import requests
from colorlog import ColoredFormatter
from dotenv import load_dotenv
from fastapi import Body, FastAPI, Header, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

import kick  # local module (kick.py)

# =====================================================================
# CONFIG / CONSTANTS
# =====================================================================
# Load environment variables from .env (must happen before reading os.environ / os.getenv)
load_dotenv()

# ---------- Logging ----------
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()

handler = logging.StreamHandler()
handler.setFormatter(
    ColoredFormatter(
        "%(log_color)s%(asctime)s | %(levelname)s | %(message)s",
        log_colors={
            "DEBUG": "cyan",
            "INFO": "green",
            "WARNING": "yellow",
            "ERROR": "red",
            "CRITICAL": "bold_red",
        },
    )
)

logging.basicConfig(level=getattr(logging, LOG_LEVEL, logging.INFO), handlers=[handler])
log = logging.getLogger("openstreamkit")

# ---------- Debug Flags ----------
DEBUG_PAYLOADS = os.getenv("DEBUG_PAYLOADS", "0") == "1"
JSON_DIR = os.getenv("JSON_DIR", "json")
DISABLE_DOCS = os.getenv("DISABLE_DOCS", "0") == "1"

# ---------- Local Files ----------
TOKEN_FILE = os.getenv("TOKEN_FILE", "token.json")
LAST_WEBHOOK_FILE = os.getenv("LAST_WEBHOOK_FILE", "last_webhook.json")

# ---------- In-memory Runtime State ----------
# (Note: cleared on restart; persisted data goes in TOKEN_FILE / LAST_WEBHOOK_FILE)
TOKENS: dict[str, str] = {}

# =====================================================================
# ACTION HELPERS
# (Side effects: file IO, logging, state mutation)
# =====================================================================

def save_json(data: dict, filename: str) -> None:
    """
    Save JSON into JSON_DIR/filename.
    filename should be a simple name like "token.json".
    """
    path = os.path.join(JSON_DIR, filename)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)


def load_token() -> dict | None:
    """Load saved OAuth token from disk if present."""
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
    """
    if "message_id" in payload and "sender" in payload and "content" in payload:
        return "chat"

    if "follower" in payload:
        return "follow"

    return "unknown"

# =====================================================================
# STARTUP LOGIC (RUNS ONCE)
# =====================================================================
os.makedirs(JSON_DIR, exist_ok=True)

saved = load_token()
if saved and "access_token" in saved:
    TOKENS["access_token"] = saved["access_token"]
    log.info("Loaded token from %s/%s", JSON_DIR, TOKEN_FILE)

# =====================================================================
# FASTAPI APP SETUP
# =====================================================================
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
    return RedirectResponse("/login")


@app.get("/login", response_class=HTMLResponse)
def login_page(request: Request):
    auth_url, _state = kick.start_login()

    return templates.TemplateResponse(
        "index.html",
        {
            "request": request,
            "auth_url": auth_url,
        },
    )


@app.get("/success", response_class=HTMLResponse)
def success_page(request: Request):
    return templates.TemplateResponse("success.html", {"request": request})


@app.get("/failure", response_class=HTMLResponse)
def failure_page(request: Request, msg: str | None = None):
    return templates.TemplateResponse("failure.html", {"request": request, "message": msg})


@app.get("/partial-success", response_class=HTMLResponse)
def partial_success_page(request: Request, msg: str | None = None):
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
    verifier = kick.pop_verifier(state)
    if not verifier:
        msg = urllib.parse.quote("Invalid or expired login session.")
        return RedirectResponse(f"/failure?msg={msg}", status_code=302)

    try:
        token = kick.exchange_code_for_token(code, verifier)
    except requests.RequestException as e:
        log.error("Token exchange failed: %s", e)
        msg = urllib.parse.quote("Token exchange failed.")
        return RedirectResponse(f"/failure?msg={msg}", status_code=302)

    save_json(token, TOKEN_FILE)
    access_token = token.get("access_token")

    if not access_token:
        msg = urllib.parse.quote("Login succeeded but no access token returned.")
        return RedirectResponse(f"/failure?msg={msg}", status_code=302)

    TOKENS["access_token"] = access_token

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
