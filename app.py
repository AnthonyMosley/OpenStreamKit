"""
OpenStreamKit (learning-first)

This project is written to be approachable for beginners:
- clear sections (imports → config → helpers → startup → routes)
- minimal “magic”
- comments explain *why* something exists

Goal: become a local-first stream automation tool that can be extended over time.
"""

# ===================================================================== IMPORTS =====================================================================
import base64
import hashlib
import json
import logging
import os
import requests
import secrets
from colorlog import ColoredFormatter
from dotenv import load_dotenv
from fastapi import Body, FastAPI, Request
from fastapi import Header
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi import Query
import urllib.parse

# ===================================================================== CONFIG / CONSTANTS =====================================================================
# Load environment variables from .env (must happen before reading os.environ / os.getenv)
load_dotenv()

# ---------- Logging ----------
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()

handler = logging.StreamHandler()
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

logging.basicConfig(level=getattr(logging, LOG_LEVEL, logging.INFO), handlers=[handler])
log = logging.getLogger("openstreamkit")

# ---------- Debug Flags ----------
DEBUG_PAYLOADS = os.getenv("DEBUG_PAYLOADS", "0") == "1"
JSON_DIR = "json"
DISABLE_DOCS = os.getenv("DISABLE_DOCS", "0") == "1"
# ---------- Local Files ----------
TOKEN_FILE = os.getenv("TOKEN_FILE", "token.json")
LAST_WEBHOOK_FILE = os.getenv("LAST_WEBHOOK_FILE", "last_webhook.json")

# ---------- Kick OAuth / API ----------
OAUTH_HOST = "https://id.kick.com"
API_HOST = "https://api.kick.com"

REDIRECT_URI = os.getenv("KICK_REDIRECT_URI", "http://localhost:8000/callback")
WEBHOOK_URL = f"{os.environ['KICK_WEBHOOK_PUBLIC_URL']}/kick/webhook"

# Required env vars (fail fast with a clear error)
CLIENT_ID = os.environ["KICK_CLIENT_ID"]
CLIENT_SECRET = os.environ["KICK_CLIENT_SECRET"]

# ---------- In-memory Runtime State ----------
# (Note: cleared on restart; persisted data goes in TOKEN_FILE / LAST_WEBHOOK_FILE)
PKCE_STORE: dict[str, str] = {}
TOKENS: dict[str, str] = {}






# ===================================================================== HELPER FUNCTIONS =====================================================================
def save_json(data:dict,filename:str)->None:
    with open(os.path.join(JSON_DIR,filename),"w", encoding="utf-8") as f:
        json.dump(data,f,indent=2)
        
def load_token() -> dict | None:
    try:
        path = os.path.join(JSON_DIR, TOKEN_FILE)
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return None

def pkce_verifier():
    return secrets.token_urlsafe(64)

def pkce_challenge_s256(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode()).digest()
    return base64.urlsafe_b64encode(digest).decode().rstrip("=")

def handle_chat_message(payload):
    sender = payload.get("sender", {}).get("username","unknown")
    content=payload.get("content","")
    log.info(f"[CHAT] {sender}: {content}")
    if DEBUG_PAYLOADS:
        save_json(payload, "last_chat.json")
    

def handle_follow(payload):
    user = payload.get("follower", {}).get("username", "unknown")
    log.info(f"[FOLLOW] {user}")
    if DEBUG_PAYLOADS:
        save_json(payload,"last_follow.json")

def do_subscribe(access_token: str) -> dict:
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
        "json": r.json() if r.headers.get("content-type","").startswith("application/json") else None,
    }

# ===================================================================== STARTUP LOGIC (RUNS ONCE) =====================================================================
os.makedirs(JSON_DIR, exist_ok=True)

saved = load_token()
if saved and "access_token" in saved:
    TOKENS["access_token"] = saved["access_token"]

# ===================================================================== EVENT HANDLERS / ROUTES =====================================================================
app = FastAPI(
    docs_url=None if DISABLE_DOCS else "/docs",
    redoc_url=None if DISABLE_DOCS else "/redoc",
    openapi_url=None if DISABLE_DOCS else "/openapi.json",
)
app.mount("/static",StaticFiles(directory="web"),name="static")
templates=Jinja2Templates(directory="web")

@app.get("/")
def root():
    return RedirectResponse("/login")


@app.get("/login", response_class=HTMLResponse)
def login_page(request: Request):
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
    auth_url = f"{OAUTH_HOST}/oauth/authorize?{urllib.parse.urlencode(q)}"

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
            "webhook_url": WEBHOOK_URL,
        },
    )

@app.get("/callback")
def callback(code: str, state: str):
    verifier = PKCE_STORE.pop(state, None)
    if not verifier:
        log.warning("OAuth callback with invalid or expired state")
        msg = urllib.parse.quote("Invalid or expired login session (state). Please try again.")
        return RedirectResponse(f"/failure?msg={msg}", status_code=302)

    data = {
        "grant_type": "authorization_code",
        "client_id": CLIENT_ID,
        "client_secret": CLIENT_SECRET,
        "redirect_uri": REDIRECT_URI,
        "code_verifier": verifier,
        "code": code,
    }

    try:
        r = requests.post(
            f"{OAUTH_HOST}/oauth/token",
            data=data,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            timeout=20,
        )
        r.raise_for_status()
    except requests.RequestException as e:
        # Log the juicy details server-side, show a clean summary to the user
        status = getattr(getattr(e, "response", None), "status_code", None)
        body = getattr(getattr(e, "response", None), "text", "")
        log.error("Token exchange failed. status=%s body=%s err=%s", status, body, e)

        clean = "Token exchange failed. Check redirect URI + client credentials."
        msg = urllib.parse.quote(clean)
        return RedirectResponse(f"/failure?msg={msg}", status_code=302)

    token = r.json()
    save_json(token, TOKEN_FILE)
    access_token = token["access_token"]

    TOKENS["access_token"] = access_token

    sub = do_subscribe(access_token)
    save_json(sub, "last_subscribe_response.json")
    log.info("Auto-subscribe: %s", sub["status_code"])

    if sub.get("status_code", 0) >= 400:
        msg = urllib.parse.quote(
            "Authorized OK, but event subscription failed. See json/last_subscribe_response.json."
        )
        return RedirectResponse(f"/partial-success?msg={msg}", status_code=302)

    return RedirectResponse("/success", status_code=302)


@app.post("/subscribe")
def subscribe(accept: str | None = Header(default=None)):
    access_token = TOKENS.get("access_token")
    if not access_token:
        if accept and "text/html" in accept:
            msg = urllib.parse.quote("No token yet. Go to /login first.")
            return RedirectResponse(f"/failure?msg={msg}", status_code=302)
        return {"error": "No token yet. Go to /login first."}

    result = do_subscribe(access_token)
    save_json(result, "last_subscribe_response.json")

    if accept and "text/html" in accept:
      if result.get("status_code", 0) >= 400:
          msg = urllib.parse.quote("Retry failed. Check json/last_subscribe_response.json.")
          return RedirectResponse(f"/partial-success?msg={msg}", status_code=302)
      return RedirectResponse("/success", status_code=302)

    return result

@app.get("/subscribe-ui")
def subscribe_ui():
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
    Kick will POST chat.message.sent payloads here.
    Payload example includes sender.username + content :contentReference[oaicite:9]{index=9}
    """

    if DEBUG_PAYLOADS:
        #log.info("Payload: \n%s", json.dumps(payload,indent=2))
        save_json(payload,LAST_WEBHOOK_FILE)

    if "message_id" in payload and "sender" in payload and "content" in payload:
        handle_chat_message(payload)
        return{"ok":True}
    elif "follower" in payload:
        handle_follow(payload)
        return{"ok":True}
    log.error("Unknown payload shape. keys=%s", list(payload.keys()))


    return {"ok": True}
