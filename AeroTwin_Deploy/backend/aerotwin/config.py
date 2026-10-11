import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


class Settings:
    host = os.getenv("AEROTWIN_HOST", "0.0.0.0")
    port = int(os.getenv("AEROTWIN_PORT") or os.getenv("PORT") or "8000")   # hosts (Render/HF/Railway) inject PORT
    source = os.getenv("AEROTWIN_SOURCE", "sim")                 # sim | live
    live_input = os.getenv("AEROTWIN_LIVE_INPUT", "http")        # http | serial | mavlink
    serial_port = os.getenv("AEROTWIN_SERIAL_PORT", "")          # e.g. COM5 or /dev/ttyUSB0
    serial_baud = int(os.getenv("AEROTWIN_SERIAL_BAUD", "115200"))
    mavlink_conn = os.getenv("AEROTWIN_MAVLINK", "udp:127.0.0.1:14550")
    api_key = os.getenv("AEROTWIN_API_KEY", "")                  # if set, device ingest needs X-API-Key
    sim_speed = float(os.getenv("AEROTWIN_SIM_SPEED", "2"))      # simulated seconds per real second
    mission_remaining_s = float(os.getenv("AEROTWIN_MISSION_S", "900"))
    return_time_s = float(os.getenv("AEROTWIN_RETURN_S", "240"))
    model_dir = Path(os.getenv("AEROTWIN_MODEL_DIR", ROOT / "models"))
    db_path = Path(os.getenv("AEROTWIN_DB", ROOT / "data" / "aerotwin.db"))
    frontend_dir = ROOT / "frontend"


settings = Settings()
