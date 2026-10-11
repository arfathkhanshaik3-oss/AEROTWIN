import sys, math
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import numpy as np
from aerotwin.simulator import EngineSim
from aerotwin.twin import DigitalTwin
from aerotwin.brain import Brain
from aerotwin.models import ModelBundle
from aerotwin.config import settings
from aerotwin import physics as P, whatif


def test_simulator_physics_directions():
    for kind, ch in (("cool", "temp"), ("bear", "vib"), ("leak", "press")):
        s = EngineSim(seed=3, ambient=25); s.set_load(0.4)
        for _ in range(150): base = s.step()["meas"][ch]
        s.inject_fault(kind, ttf=150)
        for _ in range(250): m = s.step()["meas"][ch]
        assert (m < base) if kind == "leak" else (m > base)


def test_twin_tracks_cooling_fault():
    s = EngineSim(seed=5, ambient=28); tw = DigitalTwin()
    for i in range(700):
        if i == 200: s.inject_fault("cool", 400)
        r = s.step(); tw.update(r["meas"], r["ambient"])
    assert abs(tw.d["cool"] - r["truth"]["d"]["cool"]) < 0.12
    assert tw.d["bear"] < 0.1 and tw.d["leak"] < 0.1


def test_twin_survives_missing_channel():
    s = EngineSim(seed=6); tw = DigitalTwin()
    for i in range(100):
        r = s.step(); m = dict(r["meas"]); m["temp"] = None; tw.update(m, 25.0)
    assert tw.health() > 80


def test_whatif_prefers_return_when_failing_fast_and_continue_when_healthy():
    zero = {k: 0.0 for k in P.FAULTS}
    healthy = whatif.simulate(zero, zero, {k: 3e-5 for k in P.FAULTS}, 0.4, 900, 240, seed=1)
    assert healthy["recommendation"]["action"] == "continue"
    sev = {"cool": 0.0, "bear": 0.5, "leak": 0.0}; r = {"cool": 0.0, "bear": 0.004, "leak": 0.0}
    bad = whatif.simulate(sev, r, {k: 1e-4 for k in P.FAULTS}, 0.4, 900, 240, seed=1)
    assert bad["recommendation"]["action"] == "return"
    assert bad["scenarios"]["return"]["p_survive"] >= bad["scenarios"]["continue"]["p_survive"]


def _models():
    assert ModelBundle.exists(settings.model_dir), "run: python -m aerotwin.train"
    return ModelBundle.load(settings.model_dir)


def test_brain_healthy_no_alarm_and_engine_fault_detected():
    b = _models()
    br = Brain(b); s = EngineSim(seed=21, ambient=24); s.set_load(0.42); levels = []
    for i in range(500):
        if i == 200: s.inject_fault("bear", 300)
        r = s.step(); f = br.process(r["meas"], r["throttle"], r["ambient"], whatif_on=False); levels.append(f["alert"]["level"])
    assert set(levels[40:200]) == {"nominal"}
    assert "critical" in levels[300:]


def test_sensor_fault_is_not_mistaken_for_engine_fault():
    b = _models()
    for ch, mode in (("vib", "stuck"), ("temp", "dropout"), ("press", "bias")):
        br = Brain(b); s = EngineSim(seed=33, ambient=24); s.set_load(0.42)
        for i in range(450):
            if i == 150: s.inject_sensor_fault(ch, mode)
            r = s.step(); f = br.process(r["meas"], r["throttle"], r["ambient"], whatif_on=False)
        assert f["alert"]["level"] == "nominal", (ch, mode, f["health"])
        assert ch in f["diagnosis"]["declared_sensor_faults"]


def test_json_safe_frame():
    import json
    from aerotwin.server import clean
    b = _models(); br = Brain(b); s = EngineSim(seed=1)
    for i in range(60):
        r = s.step(); m = dict(r["meas"]); m["rpm"] = float("nan") if i > 40 else m["rpm"]
        f = br.process(m, None, None)
    json.dumps(clean(f), allow_nan=False)
