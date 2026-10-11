import sys, time, json
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import os
os.environ["AEROTWIN_DB"] = "/tmp/aerotwin_test.db"
os.environ["AEROTWIN_SIM_SPEED"] = "20"
from fastapi.testclient import TestClient
from aerotwin.server import app


def test_api_end_to_end():
    with TestClient(app) as c:
        assert c.get("/api/health").json()["ok"]
        st = c.get("/api/status").json(); assert st["mode"] == "sim"
        with c.websocket_connect("/ws") as ws:
            hello = json.loads(ws.receive_text()); assert hello["type"] == "hello"
            frames = [json.loads(ws.receive_text()) for _ in range(40)]
            f = frames[-1]
            assert "health" in f and f["alert"]["level"] in ("nominal", "degrading", "critical")
            assert c.post("/api/sim/fault", json={"kind": "bear", "ttf_s": 60}).status_code == 200
            assert c.post("/api/sim/sensor_fault", json={"channel": "vib", "mode": "stuck"}).status_code == 200
            assert c.post("/api/sim/clear").status_code == 200
            assert c.post("/api/decision", json={"action": "modify"}).json()["applied"] is True
        assert c.post("/api/source", json={"mode": "live"}).json()["mode"] == "live"
        assert c.post("/api/sim/clear").status_code == 409                   # sim controls locked in live mode
        r = c.post("/api/ingest", json={"temp": 96, "rpm": 3100, "vib": 0.8, "press": 1.4}); assert r.json()["accepted"]
        assert c.post("/api/decision", json={"action": "return"}).json()["applied"] is False   # advisory only
        with c.websocket_connect("/ws/ingest") as wi:
            for _ in range(3):
                wi.send_text(json.dumps({"temp": 96, "rpm": 3100, "vib": 0.8, "press": 1.4})); wi.receive_text()
        assert c.get("/api/metrics").status_code == 200
        assert c.get("/api/export.csv").status_code == 200
        c.post("/api/source", json={"mode": "sim"})
