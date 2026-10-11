"""
System-level evaluation: streams FRESH simulated runs through the full Brain (exactly as live) and measures what an
operator cares about: early-warning lead time, false alarms, and whether the sensor-vs-engine validator prevents
false engine alarms.
    python -m aerotwin.evaluate --all --runs 8            # everything in one go (slow on 1 CPU: ~7 min)
    python -m aerotwin.evaluate --part engine --start 0 --count 4 ; ... ; python -m aerotwin.evaluate --merge
"""
from __future__ import annotations
import argparse, json, random
from pathlib import Path
import numpy as np
from . import physics as P
from .brain import Brain
from .config import settings
from .models import ModelBundle
from .simulator import EngineSim, NOISE
from .physics import CH

T = 600
PARTS = Path(settings.model_dir) / "eval_parts"
SEED = {"healthy": 90000, "engine": 91000, "sensor": 92000}


def run_one(bundle, kind, seed, use_validator=True):
    rng = random.Random(seed)
    sim = EngineSim(seed=seed, ambient=rng.uniform(12, 38)); sim.set_load(None)
    br = Brain(bundle, use_validator=use_validator)
    on = int(rng.uniform(150, 250))
    ftype = rng.choice(P.FAULTS); ttf = rng.uniform(150, 350)
    sch = rng.choice(CH); smode = rng.choice(["stuck", "drift", "bias", "dropout", "spike"])
    levels, truth_h, t_d01 = [], [], None
    for t in range(T):
        if t == on and kind == "engine": sim.inject_fault(ftype, ttf)
        if t == on and kind == "sensor":
            mag = rng.choice([-1, 1]) * rng.uniform(0.05, 0.3) * NOISE[sch] if smode == "drift" else None
            sim.inject_sensor_fault(sch, smode, mag)
        r = sim.step()
        f = br.process(r["meas"], r["throttle"], r["ambient"], whatif_on=False)
        levels.append(f["alert"]["level"]); truth_h.append(r["truth"]["health"])
        if t_d01 is None and max(r["truth"]["d"].values()) >= 0.1: t_d01 = t
    return np.array(levels), np.array(truth_h), on, t_d01, (ftype, sch, smode)


def one(bundle, part, i):
    seed = SEED[part] + i
    if part == "healthy":
        lv, th, on, _, _ = run_one(bundle, "healthy", seed)
        return {"false_alarm": bool((lv[40:] != "nominal").sum() >= 5)}
    if part == "engine":
        lv, th, on, t01, meta = run_one(bundle, "engine", seed)
        t_crit = np.where(th < P.CRIT_HEALTH)[0]; t_crit = int(t_crit[0]) if len(t_crit) else None
        al = np.where(lv != "nominal")[0]; al = al[al >= on]
        r = {"fault": meta[0], "missed": len(al) == 0, "delay": None, "lead": None}
        if len(al):
            r["delay"] = float(al[0] - (t01 if t01 is not None else on))
            if t_crit is not None: r["lead"] = float(t_crit - al[0])
        return r
    out = {}
    for key, uv in (("with", True), ("naive", False)):
        lv, th, on, _, meta = run_one(bundle, "sensor", seed, use_validator=uv)
        out[key] = bool((lv[on:] != "nominal").sum() >= 5); out["mode"] = f"{meta[1]}:{meta[2]}"
    return out


def save_part(part, start, res):
    PARTS.mkdir(parents=True, exist_ok=True)
    (PARTS / f"{part}_{start}.json").write_text(json.dumps(res))


def merge(bundle=None, write=True):
    R = {p: [] for p in SEED}
    for f in sorted(PARTS.glob("*.json")):
        R[f.name.split("_")[0]] += json.loads(f.read_text())
    h, e, s = R["healthy"], R["engine"], R["sensor"]
    lead = [x["lead"] for x in e if x["lead"] is not None]; delay = [x["delay"] for x in e if x["delay"] is not None]
    out = {
        "runs": {"healthy": len(h), "engine_fault": len(e), "sensor_fault": len(s)},
        "false_alarm_rate_healthy_engine": round(float(np.mean([x["false_alarm"] for x in h])), 3) if h else None,
        "engine_fault_missed_rate": round(float(np.mean([x["missed"] for x in e])), 3) if e else None,
        "median_detection_delay_s_after_10pct_damage": float(np.median(delay)) if delay else None,
        "median_warning_lead_time_before_critical_s": float(np.median(lead)) if lead else None,
        "min_warning_lead_time_before_critical_s": float(np.min(lead)) if lead else None,
        "sensor_fault_false_engine_alarm_rate_WITH_validator": round(float(np.mean([x["with"] for x in s])), 3) if s else None,
        "sensor_fault_false_engine_alarm_rate_NAIVE_no_validator": round(float(np.mean([x["naive"] for x in s])), 3) if s else None,
    }
    print(json.dumps(out, indent=2))
    if write:
        b = bundle or ModelBundle.load(settings.model_dir)
        b.metrics["system_level"] = out; b.save(settings.model_dir)
        docs = Path(settings.model_dir).parent / "docs" / "RESULTS.md"
        docs.write_text("# Measured results (simulated engine, held-out runs)\n\n```json\n" + json.dumps(b.metrics, indent=2) +
                        "\n```\n\nThese numbers come from a physics **simulator**, not a real UAV. They show the pipeline works as designed; "
                        "re-measure on real data before making real-world claims.\n")
    return out


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--part", choices=list(SEED)); ap.add_argument("--start", type=int, default=0); ap.add_argument("--count", type=int, default=4)
    ap.add_argument("--merge", action="store_true"); ap.add_argument("--all", action="store_true"); ap.add_argument("--runs", type=int, default=8)
    a = ap.parse_args()
    if a.merge:
        merge()
    else:
        b = ModelBundle.load(settings.model_dir)
        parts = list(SEED) if a.all else [a.part]
        for p in parts:
            start, count = (0, a.runs) if a.all else (a.start, a.count)
            res = [one(b, p, i) for i in range(start, start + count)]
            save_part(p, start, res); print(p, start, "done", flush=True)
        if a.all: merge(b)
