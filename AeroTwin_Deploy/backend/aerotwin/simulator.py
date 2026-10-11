"""
Piston-engine simulator: produces realistic sensor streams with progressive engine faults
and sensor faults.  Used (a) to train/validate the ML models and (b) as a live demo source
when no real drone is connected.  It is NOT used when a real device is connected.
"""
from __future__ import annotations
import math, random
from . import physics as P
from .physics import Phys, SIM_PHYS

NOISE = {"temp": 0.6, "rpm": 8.0, "vib": 0.025, "press": 0.015}


class EngineSim:
    def __init__(self, seed=None, dt=1.0, phys: Phys = SIM_PHYS, ambient=None, auto_profile=True):
        self.rng = random.Random(seed)
        self.dt = dt
        self.p = phys
        self.ambient = ambient if ambient is not None else self.rng.uniform(15, 38)
        self.auto_profile = auto_profile
        self.reset()

    # ------------------------------------------------------------------ control
    def reset(self):
        self.t = 0.0
        self.load = 0.8
        self.manual_load = None
        self.rpm = P.rpm_target(0.4)
        self.T = self.ambient + 71.0
        self.d = {"cool": 0.0, "bear": 0.0, "leak": 0.0}
        self.rate = {"cool": 0.0, "bear": 0.0, "leak": 0.0}      # per-second growth at nominal load
        self.sfault = {}                                            # channel -> dict(mode,onset,...)
        self._profile_next = 90.0
        self._cruise = self.rng.uniform(0.32, 0.5)
        self._last_meas = None

    def inject_fault(self, kind: str, ttf: float = 300.0, start=0.03):
        """Start an engine fault that reaches severity 1 in ~ttf seconds at nominal load."""
        assert kind in P.FAULTS
        self.d[kind] = max(self.d[kind], start)
        self.rate[kind] = math.log(2.0) / max(ttf, 20.0) * 1.0 + 0.0  # d'=r*g*(1+d) -> d(ttf)~1 at g=1
        self.rate[kind] = math.log(2.0) / max(ttf, 20.0)

    def clear_faults(self):
        self.d = {k: 0.0 for k in self.d}
        self.rate = {k: 0.0 for k in self.rate}
        self.sfault = {}

    def inject_sensor_fault(self, ch: str, mode: str, magnitude=None):
        assert ch in P.CH and mode in ("stuck", "drift", "bias", "dropout", "spike")
        s = {"mode": mode, "onset": self.t, "stuck_val": None}
        sig = NOISE[ch]
        if mode == "drift":
            s["rate"] = (magnitude if magnitude is not None else self.rng.choice([-1, 1]) * sig * 0.35)   # units/s
        elif mode == "bias":
            s["bias"] = (magnitude if magnitude is not None else self.rng.choice([-1, 1]) * sig * self.rng.uniform(8, 18))
        elif mode == "spike":
            s["p"] = 0.12
        self.sfault[ch] = s

    def clear_sensor_faults(self):
        self.sfault = {}

    def set_load(self, load):
        self.manual_load = None if load is None else min(1.0, max(0.1, float(load)))

    # ------------------------------------------------------------------ dynamics
    def _profile(self):
        if self.manual_load is not None:
            self.load += (self.manual_load - self.load) * 0.2
            return
        if not self.auto_profile:
            return
        if self.t < 90:
            target = 0.8 - 0.4 * (self.t / 90.0)           # take-off/climb -> cruise
        else:
            if self.t >= self._profile_next:
                self._cruise = min(0.7, max(0.28, self._cruise + self.rng.uniform(-0.15, 0.15)))
                self._profile_next = self.t + self.rng.uniform(60, 200)
            target = self._cruise
        self.load += (target - self.load) * 0.1

    def health_truth(self):
        return P.health_from(self.d["cool"], self.d["bear"], self.d["leak"])

    def step(self):
        p, dt, d = self.p, self.dt, self.d
        self._profile()
        g = P.growth(self.load)
        for k in d:
            if self.rate[k] > 0 and d[k] < 1.0:
                d[k] = min(1.0, d[k] + self.rate[k] * g * (1 + d[k]) * dt)
        dmax = max(d.values())
        # rpm lags the throttle; unhealthy engines make less rpm
        cmd = P.rpm_target(self.load) * (1 - 0.15 * dmax)
        self.rpm += (cmd - self.rpm) * dt / 3.0
        # thermal model
        eta = P.eta_from_cool(p, d["cool"])
        q = P.heat_in(p, self.rpm, d["bear"], d["leak"])
        self.T += dt / p.cap * (q - P.cool_coef(p, self.rpm, eta) * (self.T - self.ambient))
        vib = P.vib_expected(p, self.rpm) + p.bear_vib * d["bear"] + p.c_vib_leak * d["leak"] + p.c_vib_cool * d["cool"]
        prs = P.press_expected(p, self.rpm) * (1 - p.leak_p * d["leak"] - p.c_p_cool * d["cool"] - p.c_p_bear * d["bear"])
        true = {"temp": self.T, "rpm": self.rpm, "vib": vib, "press": prs}
        meas = {c: true[c] + self.rng.gauss(0, NOISE[c]) for c in P.CH}
        meas["vib"] = max(0.0, meas["vib"]); meas["press"] = max(0.0, meas["press"])
        sf_active = {}
        for ch, s in self.sfault.items():
            el = self.t - s["onset"]
            m = s["mode"]
            if m == "stuck":
                if s["stuck_val"] is None:
                    s["stuck_val"] = meas[ch]
                meas[ch] = s["stuck_val"]; sf_active[ch] = el >= 12
            elif m == "drift":
                off = s["rate"] * el; meas[ch] += off; sf_active[ch] = abs(off) > 3 * NOISE[ch]
            elif m == "bias":
                meas[ch] += s["bias"]; sf_active[ch] = True
            elif m == "dropout":
                meas[ch] = float("nan"); sf_active[ch] = True
            elif m == "spike":
                if self.rng.random() < s["p"]:
                    meas[ch] += self.rng.choice([-1, 1]) * NOISE[ch] * self.rng.uniform(8, 20)
                sf_active[ch] = el >= 15
        self.t += dt
        self._last_meas = meas
        return {
            "meas": meas,
            "throttle": self.load,
            "ambient": self.ambient,
            "truth": {
                "health": self.health_truth(),
                "d": dict(d),
                "sensor_fault": {c: bool(sf_active.get(c, False)) for c in P.CH},
                "load": self.load,
            },
        }
