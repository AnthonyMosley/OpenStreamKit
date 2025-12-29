"""
KickBot (learning-first)

This project is written to be approachable for beginners:
- clear sections (imports → config → helpers → startup → routes)
- minimal “magic”
- comments explain *why* something exists

Goal: become a local-first stream automation tool that can be extended over time.
"""

# ===================================================================== IMPORTS =====================================================================
import base64
import hashlib
import os
import secrets
import urllib.parse
import json
import requests
import logging
from fastapi import FastAPI, Request, Body
from dotenv import load_dotenv
from colorlog import ColoredFormatter

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

# ===================================================================== STARTUP LOGIC (RUNS ONCE) =====================================================================
os.makedirs(JSON_DIR, exist_ok=True)

saved = load_token()
if saved and "access_token" in saved:
    TOKENS["access_token"] = saved["access_token"]

# ===================================================================== EVENT HANDLERS / ROUTES =====================================================================
app = FastAPI()

@app.get("/login")
def login():
    verifier = pkce_verifier()
    challenge = pkce_challenge_s256(verifier)
    state = secrets.token_urlsafe(16)

    PKCE_STORE[state] = verifier

    # Required query params per Kick OAuth docs :contentReference[oaicite:3]{index=3}
    q = {
        "response_type": "code",
        "client_id": CLIENT_ID,
        "redirect_uri": REDIRECT_URI,
        "scope": "events:subscribe",   # needed to subscribe to chat.message.sent :contentReference[oaicite:4]{index=4}
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "state": state,
    }
    url = f"{OAUTH_HOST}/oauth/authorize?{urllib.parse.urlencode(q)}"  # :contentReference[oaicite:5]{index=5}
    return {"open_this_url_in_browser": url}

@app.get("/callback")
def callback(code: str, state: str):
    verifier = PKCE_STORE.pop(state, None)
    if not verifier:
        return {"error": "Unknown/expired state (restart login)"}

    # Exchange code for token via POST /oauth/token :contentReference[oaicite:6]{index=6}
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
    save_json(token,TOKEN_FILE)
    access_token = token["access_token"]

    TOKENS["access_token"] = access_token

    return {
        "ok": True,
        "next": "Call POST /subscribe (open /subscribe in browser)",
        "token_keys": list(token.keys()),
    }

@app.post("/subscribe")
def subscribe():
    """
    Subscribes to chat.message.sent via Kick events subscriptions endpoint :contentReference[oaicite:7]{index=7}
    """
    access_token = TOKENS.get("access_token")
    if not access_token:
        return {"error": "No token yet. Go to /login first."}

    body = {
        "events": [
            {"name": "chat.message.sent", "version": 1},

            # Social
            {"name": "channel.followed", "version": 1},

            # Monetization (common alert types)
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
        "response": r.json() if r.headers.get("content-type","").startswith("application/json") else r.text
    }

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
