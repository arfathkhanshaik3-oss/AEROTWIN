"""
What-if mission simulator.
Forward-simulates the digital twin's estimated fault state under three operating options and
Monte-Carlo samples the uncertainty in fault growth rate.  Closed form of the fault growth law
    d' = r * g(load) * (1 + d)   ->   d(t) = (1 + d0) * exp(r*g*t) - 1
makes this exact and fast (a few ms).
"""
from __future__ import annotations
import numpy as np
from . import physics as P

UAV_VALUE = 1.0        # value of bringing the aircraft home
MISSION_VALUE = 0.6    # value of completing the mission objectives
NAMES = {"continue": "Continue Mission", "modify": "Modify Mission", "return": "Return / Divert"}


def _health(d):                       # d (...,3) -> (...)
    m = d.max(-1); mean = d.mean(-1)
    return 100.0 * (1.0 - 0.85 * m - 0.15 * mean)


def simulate(sev: dict, r_hat: dict, r_sig: dict, load_now: float, mission_s: float, return_s: float,
             n: int = 300, step: float = 10.0, seed: int | None = None) -> dict:
    rng = np.random.default_rng(seed)
    load_c = max(load_now, 0.35)
    scen = {
        "continue": dict(load=load_c, expo=mission_s, frac=1.0),
        "modify": dict(load=max(0.25, 0.65 * load_c), expo=mission_s, frac=0.7),
        "return": dict(load=max(0.25, min(0.8 * load_c, 0.32)), expo=return_s, frac=0.0),
    }
    horizon = max(mission_s, return_s)
    grid = np.arange(0.0, horizon + step, step)                           # (K,)
    d0 = np.array([sev[k] for k in P.FAULTS]); r0 = np.array([r_hat[k] for k in P.FAULTS]); rs = np.array([r_sig[k] for k in P.FAULTS])
    # shared random draws across scenarios (common random numbers)
    z = rng.standard_normal((n, 3))
    rate = r0[None, :] * np.exp(0.35 * z) + np.abs(rs)[None, :] * np.abs(rng.standard_normal((n, 3))) * 0.5
    start = np.clip(d0[None, :] + 0.02 * rng.standard_normal((n, 3)), 0, 1)

    out, util = {}, {}
    for name, s in scen.items():
        g = P.growth(s["load"])
        d = np.clip((1 + start[:, :, None]) * np.exp(rate[:, :, None] * g * grid[None, None, :]) - 1, 0, 1)   # (n,3,K)
        h = _health(np.moveaxis(d, 1, 2))                                 # (n,K)
        k_end = int(min(len(grid) - 1, round(s["expo"] / step)))
        h_end = h[:, k_end]
        ok = h_end >= P.FAIL_HEALTH
        util[name] = ok * (UAV_VALUE + MISSION_VALUE * s["frac"])
        med = np.median(h, 0)
        med_plot = [round(float(v), 1) if i <= k_end else None for i, v in enumerate(med)]
        p_surv = float(ok.mean())
        out[name] = {
            "label": NAMES[name],
            "load": round(s["load"], 2),
            "exposure_s": s["expo"],
            "mission_value": s["frac"],
            "health_end": round(float(np.median(h_end)), 1),
            "health_end_p10": round(float(np.percentile(h_end, 10)), 1),
            "health_end_p90": round(float(np.percentile(h_end, 90)), 1),
            "p_survive": round(p_surv, 3),
            "risk": "LOW" if p_surv > 0.9 else ("MODERATE" if p_surv > 0.6 else "HIGH"),
            "utility": round(float(util[name].mean()), 3),
            "traj_median": med_plot,
            "traj_p10": [round(float(v), 1) if i <= k_end else None for i, v in enumerate(np.percentile(h, 10, 0))],
            "traj_p90": [round(float(v), 1) if i <= k_end else None for i, v in enumerate(np.percentile(h, 90, 0))],
        }
    best = max(out, key=lambda k: (round(out[k]["utility"], 3), out[k]["health_end"]))
    emergency = max(v["utility"] for v in out.values()) < 0.05
    if emergency:
        best = "return"
    others = [util[k] for k in out if k != best]
    conf = float(np.mean(util[best] >= np.max(others, axis=0) - 1e-9))
    med_c = np.array([v if v is not None else np.nan for v in out["continue"]["traj_median"]], dtype=float)

    def first_below(th):
        i = np.where(med_c < th)[0]
        return float(grid[i[0]]) if len(i) else None

    reasons = {
        "continue": "Engine health is projected to stay above the failure threshold for the whole mission.",
        "modify": "Reduced-demand profile slows the fault growth enough to keep the engine alive while still achieving part of the mission.",
        "return": "Continuing or even a reduced mission carries a high chance of engine failure; preserving the UAV now outweighs the mission.",
    }
    return {
        "scenarios": out,
        "grid_s": [float(v) for v in grid],
        "recommendation": {"action": best, "label": NAMES[best], "confidence": round(conf, 3),
                           "emergency": bool(emergency),
                           "reason": ("EMERGENCY: engine failure is likely under every option - return / land immediately and minimise engine load."
                                      if emergency else reasons[best])},
        "eta_degraded_s": first_below(P.WARN_HEALTH),
        "eta_critical_s": first_below(P.CRIT_HEALTH),
        "eta_failure_s": first_below(P.FAIL_HEALTH),
        "mission_s": mission_s, "return_s": return_s,
    }
