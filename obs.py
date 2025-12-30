# obs.py
"""
OBS integration for OpenStreamKit.

Split into:
- Descriptor functions: read/query OBS state, build data needed for actions
- Action functions: change OBS state (switch scene, enable sources, etc.)
"""

# =====================================================================
# IMPORTS
# =====================================================================
import os
from obswebsocket import obsws, requests as obsreq

# =====================================================================
# CONFIG / CONSTANTS
# =====================================================================
OBS_HOST = os.getenv("OBS_HOST", "127.0.0.1")
OBS_PORT = int(os.getenv("OBS_PORT", "4455"))
OBS_PASSWORD = os.getenv("OBS_PASSWORD", "")

# =====================================================================
# CONNECTION HELPERS
# (These are “actions” because they create/tear down an external connection)
# =====================================================================

def obs_connect():
    """Create + connect a websocket client to OBS."""
    ws = obsws(host=OBS_HOST, port=OBS_PORT, password=OBS_PASSWORD)
    ws.connect()
    return ws


def obs_disconnect(ws):
    """Disconnect from OBS cleanly."""
    try:
        ws.disconnect()
    except Exception:
        pass

# =====================================================================
# DESCRIPTOR FUNCTIONS
# (Read/query OBS state, return data)
# =====================================================================

def obs_get_scenes(ws):
    """Return list of scenes from OBS."""
    return ws.call(obsreq.GetSceneList()).getScenes()

# =====================================================================
# ACTION FUNCTIONS
# (Change OBS state)
# =====================================================================

def obs_set_scene(ws, scene_name: str):
    """Switch current program scene."""
    ws.call(obsreq.SetCurrentProgramScene(sceneName=scene_name))


def obs_enable_source():
    """Placeholder for later."""
    ...
