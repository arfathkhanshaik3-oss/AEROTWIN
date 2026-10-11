"""
AeroTwin server.   uvicorn aerotwin.server:app --host 0.0.0.0 --port 8000
Dashboard  -> http://localhost:8000
Device in  -> POST /api/ingest  |  WS /ws/ingest  |  serial  |  MAVLink
"""
from __future__ import annotations
import asyncio, json, math, time, logging
from collections import deque
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException, Header
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import physics as P
from .brain import Brain
from .config import settings
from .models import ModelBundle
from .simulator import EngineSim
from .sources import Resampler, SerialSource, MavlinkSource
from .store import Store

log = logging.getLogger("aerotwin")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")


def clean(o):
    if isinstance(o, dict):
        return {k: clean(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [clean(v) for v in o]
    if isinstance(o, float) and not math.isfinite(o):
        return None
    if hasattr(o, "item"):
        return clean(o.item())
    return o


class Runtime:
    def __init__(self):
        self.mode = settings.source                      # sim | live
        self.clients: set[WebSocket] = set()
        self.latest: Optional[dict] = None
        self.recent = deque(maxlen=400)                  # light frames for dashboard back-fill
        self.last_ingest = 0.0
        self.live_count = 0
        self.level_prev = "nominal"
        self.speed = settings.sim_speed
        self.started = time.time()
        self.loop: Optional[asyncio.AbstractEventLoop] = None
        self.thread_src = None
        self.resampler = Resampler()

    def load_models(self):
        if not ModelBundle.exists(settings.model_dir):
            log.warning("No trained models found - training now (about 30 s) ...")
            from .train import train
            train(scale=1.0, verbose=False)
        try:
            self.bundle = ModelBundle.load(settings.model_dir)
        except Exception as e:      # e.g. scikit-learn version mismatch -> retrain automatically
            log.warning("Could not load saved models (%s) - retraining ...", e)
            from .train import train
            train(scale=1.0, verbose=False)
            self.bundle = ModelBundle.load(settings.model_dir)
        self.brain = Brain(self.bundle, settings.mission_remaining_s, settings.return_time_s)
        self.sim = EngineSim(seed=int(time.time()) % 100000, ambient=25.0)
        self.store = Store(settings.db_path)

    # ------------------------------------------------------------------ processing
    async def handle_frame(self, sample, throttle=None, ambient=None, truth=None, source="sim"):
        f = self.brain.process(sample, throttle, ambient, ts=time.time(), truth=truth)
        f["source"] = source
        f["mode"] = self.mode
        f["mission"] = {"remaining_s": self.brain.mission_s, "return_s": self.brain.return_s}
        f = clean(f)
        self.latest = f
        self.recent.append({"t": f["t"], "ts": f["ts"], "health": f["health"]["fused"], "forecast": f["health"]["forecast_ml_120s"],
                            "level": f["alert"]["level"], "rpm": f["sensors"]["rpm"]["value"]})
        if f["alert"]["level"] != self.level_prev:
            self.store.event("alert", {"from": self.level_prev, "to": f["alert"]["level"], "health": f["health"]["fused"]})
            self.level_prev = f["alert"]["level"]
        self.store.log_frame(f, source)
        await self.broadcast(f)

    async def broadcast(self, f):
        msg = json.dumps(f)
        dead = []
        for ws in list(self.clients):
            try:
                await ws.send_text(msg)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.clients.discard(ws)

    async def ingest(self, s: dict):
        """Called for every raw device sample (any rate)."""
        self.last_ingest = time.time(); self.live_count += 1
        if self.mode != "live":
            return False
        out = self.resampler.push(s)
        if out is not None:
            await self.handle_frame({c: out.get(c) for c in P.CH}, out.get("throttle"), out.get("ambient"), source="live")
        return True

    def ingest_threadsafe(self, s):
        if self.loop:
            asyncio.run_coroutine_threadsafe(self.ingest(s), self.loop)

    def set_mode(self, mode):
        self.mode = mode
        self.brain.reset(); self.recent.clear(); self.latest = None; self.level_prev = "nominal"
        self.resampler = Resampler()
        if mode == "live":
            self._start_live_thread()
        self.store.event("mode", {"mode": mode})

    def _start_live_thread(self):
        if self.thread_src is not None:
            self.thread_src.stop = True; self.thread_src = None
        li = settings.live_input
        if li == "serial" and settings.serial_port:
            self.thread_src = SerialSource(settings.serial_port, settings.serial_baud, self.ingest_threadsafe)
        elif li == "mavlink":
            self.thread_src = MavlinkSource(settings.mavlink_conn, self.ingest_threadsafe)
        if self.thread_src:
            self.thread_src.start()


rt = Runtime()


async def sim_loop():
    while True:
        if rt.mode == "sim":
            r = rt.sim.step()
            await rt.handle_frame(r["meas"], r["throttle"], r["ambient"], truth=r["truth"], source="simulator")
            await asyncio.sleep(1.0 / max(0.2, rt.speed))
        else:
            await asyncio.sleep(0.2)


@asynccontextmanager
async def lifespan(app):
    rt.loop = asyncio.get_running_loop()
    rt.load_models()
    if rt.mode == "live":
        rt._start_live_thread()
    t = asyncio.create_task(sim_loop())
    log.info("AeroTwin ready  mode=%s  models=%s", rt.mode, rt.bundle.metrics.get("trained_on"))
    yield
    t.cancel()


app = FastAPI(title="AeroTwin", version="1.0.0", lifespan=lifespan)


# ============================================================================ schemas
class Sample(BaseModel):
    temp: Optional[float] = None
    rpm: Optional[float] = None
    vib: Optional[float] = None
    press: Optional[float] = None
    throttle: Optional[float] = None
    ambient: Optional[float] = None


def check_key(x_api_key: Optional[str]):
    if settings.api_key and x_api_key != settings.api_key:
        raise HTTPException(401, "invalid or missing X-API-Key")


# ============================================================================ REST
@app.get("/api/status")
def status():
    src = rt.thread_src.status if rt.thread_src else None
    return {"mode": rt.mode, "live_input": settings.live_input, "live_adapter_status": src,
            "seconds_since_last_device_sample": None if not rt.last_ingest else round(time.time() - rt.last_ingest, 1),
            "device_samples_received": rt.live_count, "sim_speed": rt.speed, "clients": len(rt.clients),
            "uptime_s": round(time.time() - rt.started), "models": rt.bundle.metrics.get("trained_on"),
            "mission": {"remaining_s": rt.brain.mission_s, "return_s": rt.brain.return_s}, "api_key_required": bool(settings.api_key),
            "frames_logged": rt.store.n}


class ModeReq(BaseModel):
    mode: str


@app.post("/api/source")
def set_source(r: ModeReq):
    if r.mode not in ("sim", "live"):
        raise HTTPException(400, "mode must be sim or live")
    rt.set_mode(r.mode)
    return {"mode": rt.mode}


@app.post("/api/ingest")
async def ingest(s: Sample, x_api_key: Optional[str] = Header(default=None)):
    check_key(x_api_key)
    accepted = await rt.ingest(s.model_dump())
    return {"accepted": accepted, "mode": rt.mode}


@app.websocket("/ws/ingest")
async def ws_ingest(ws: WebSocket):
    await ws.accept()
    if settings.api_key and ws.query_params.get("key") != settings.api_key:
        await ws.close(code=4401); return
    try:
        while True:
            d = json.loads(await ws.receive_text())
            acc = await rt.ingest({k: d.get(k) for k in ("temp", "rpm", "vib", "press", "throttle", "ambient")})
            await ws.send_text(json.dumps({"accepted": acc, "level": (rt.latest or {}).get("alert", {}).get("level")}))
    except WebSocketDisconnect:
        pass


@app.websocket("/ws")
async def ws_dashboard(ws: WebSocket):
    await ws.accept()
    rt.clients.add(ws)
    try:
        await ws.send_text(json.dumps({"type": "hello", "recent": list(rt.recent), "mode": rt.mode}))
        if rt.latest:
            await ws.send_text(json.dumps(rt.latest))
        while True:
            await ws.receive_text()           # keep-alive; dashboard commands use REST
    except WebSocketDisconnect:
        pass
    finally:
        rt.clients.discard(ws)


# ---------------------------------------------------------------- simulator controls (demo / test harness)
def need_sim():
    if rt.mode != "sim":
        raise HTTPException(409, "simulator controls are only available in simulator mode")


class LoadReq(BaseModel):
    load: Optional[float] = None


class FaultReq(BaseModel):
    kind: str
    ttf_s: float = 240.0


class SFaultReq(BaseModel):
    channel: str
    mode: str


class SpeedReq(BaseModel):
    speed: float


@app.post("/api/sim/load")
def sim_load(r: LoadReq):
    need_sim(); rt.sim.set_load(r.load); return {"ok": True}


@app.post("/api/sim/fault")
def sim_fault(r: FaultReq):
    need_sim()
    if r.kind not in P.FAULTS:
        raise HTTPException(400, f"kind must be one of {P.FAULTS}")
    rt.sim.inject_fault(r.kind, r.ttf_s); rt.store.event("sim_fault", r.model_dump()); return {"ok": True}


@app.post("/api/sim/sensor_fault")
def sim_sfault(r: SFaultReq):
    need_sim()
    try:
        rt.sim.inject_sensor_fault(r.channel, r.mode)
    except AssertionError:
        raise HTTPException(400, "bad channel/mode")
    rt.store.event("sim_sensor_fault", r.model_dump()); return {"ok": True}


@app.post("/api/sim/clear")
def sim_clear():
    need_sim(); rt.sim.clear_faults(); return {"ok": True}


@app.post("/api/sim/reset")
def sim_reset():
    need_sim(); rt.sim.reset(); rt.sim.ambient = 25.0; rt.brain.reset(); rt.recent.clear(); rt.level_prev = "nominal"; return {"ok": True}


@app.post("/api/sim/speed")
def sim_speed(r: SpeedReq):
    rt.speed = min(20.0, max(0.5, r.speed)); return {"speed": rt.speed}


# ---------------------------------------------------------------- mission + decisions
class MissionReq(BaseModel):
    remaining_s: float
    return_s: float


@app.post("/api/mission")
def mission(r: MissionReq):
    rt.brain.mission_s = max(30.0, r.remaining_s); rt.brain.return_s = max(30.0, r.return_s)
    return {"ok": True}


class DecisionReq(BaseModel):
    action: str


@app.post("/api/decision")
def decision(r: DecisionReq):
    """Operator accepts a recommendation.  In SIM mode the simulated engine is actually put into that regime.
    In LIVE mode this is ADVISORY ONLY: AeroTwin never sends commands to the aircraft."""
    if r.action not in ("continue", "modify", "return"):
        raise HTTPException(400, "action must be continue|modify|return")
    rt.store.event("decision", {"action": r.action, "health": (rt.latest or {}).get("health", {}).get("fused"), "mode": rt.mode})
    if rt.mode == "sim":
        cur = rt.sim.load
        if r.action == "modify":
            rt.sim.set_load(max(0.25, 0.65 * max(cur, 0.35)))
        elif r.action == "return":
            rt.sim.set_load(max(0.25, min(0.8 * max(cur, 0.35), 0.32)))
        else:
            rt.sim.set_load(None)
        return {"applied": True, "mode": "sim", "action": r.action}
    return {"applied": False, "mode": "live", "action": r.action,
            "note": "Advisory only - command the aircraft through your ground control station / autopilot."}


# ---------------------------------------------------------------- data out
@app.get("/api/metrics")
def metrics():
    return rt.bundle.metrics


@app.get("/api/history")
def history(limit: int = 600):
    return rt.store.history(min(limit, 20000))


@app.get("/api/events")
def events(limit: int = 100):
    return rt.store.events(limit)


@app.get("/api/export.csv")
def export_csv():
    return StreamingResponse(rt.store.csv(), media_type="text/csv", headers={"Content-Disposition": "attachment; filename=aerotwin_log.csv"})


@app.get("/api/health")
def healthz():
    return {"ok": True}


if settings.frontend_dir.exists():
    app.mount("/", StaticFiles(directory=str(settings.frontend_dir), html=True), name="frontend")
