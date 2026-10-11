"""
Digital twin core.

A running physics model of the engine (thermal ODE + vibration/pressure envelopes) that is
continuously corrected by the incoming sensor data.  From the model/measurement mismatch it
estimates, in real time:
    * cooling efficiency  eta   (-> cooling-loss severity)
    * bearing-wear severity     (-> vibration excess)
    * oil-leak severity         (-> pressure deficit)
and the growth *rate* of each, which the what-if engine uses to forecast the future.
Missing / untrusted channels are skipped (graceful degradation).
"""
from __future__ import annotations
import math
from collections import deque
import numpy as np
from . import physics as P
from .physics import Phys


def _clip(x, lo=0.0, hi=1.0):
    return lo if x < lo else hi if x > hi else x


class DigitalTwin:
    HIST = 90          # seconds of severity history kept for rate estimation

    def __init__(self, phys: Phys | None = None):
        self.p = phys or Phys()
        self.reset()

    def reset(self):
        self.T_hat = None
        self.eta = 1.0
        self.vib_ema = None
        self.p_ema = None
        self.d = {"cool": 0.0, "bear": 0.0, "leak": 0.0}
        self.hist = {k: deque(maxlen=self.HIST) for k in self.d}
        self.n = 0
        self.used = {c: False for c in P.CH}

    # ------------------------------------------------------------------ update
    def update(self, x: dict, ambient: float = 25.0, dt: float = 1.0):
        """x: dict channel -> float or None/NaN (untrusted/missing). Returns estimate dict."""
        p = self.p
        ok = {c: (x.get(c) is not None and not (isinstance(x[c], float) and math.isnan(x[c]))) for c in P.CH}
        self.used = dict(ok)
        rpm = x["rpm"] if ok["rpm"] else (self._last_rpm if hasattr(self, "_last_rpm") else 3000.0)
        self._last_rpm = rpm
        self.n += 1

        # ---- thermal state + cooling efficiency (LMS parameter estimation)
        if ok["temp"]:
            T = x["temp"]
            if self.T_hat is None:
                self.T_hat = T
            q = P.heat_in(p, rpm, self.d["bear"], self.d["leak"])
            cc = P.cool_coef(p, rpm, self.eta)
            T_pred = self.T_hat + dt / p.cap * (q - cc * (self.T_hat - ambient))
            e = T - T_pred
            phi = -(dt / p.cap) * p.h0 * (0.5 + 0.5 * rpm / 3000.0) * (self.T_hat - ambient)
            if abs(phi) > 0.3:                      # enough thermal excitation to identify eta
                self.eta += 0.08 * e * phi / (phi * phi + 0.05)
                self.eta = min(1.05, max(0.2, self.eta))
            self.T_hat = T_pred + 0.35 * e
        elif self.T_hat is not None:                # no temp sensor: free-run the model
            q = P.heat_in(p, rpm, self.d["bear"], self.d["leak"])
            self.T_hat += dt / p.cap * (q - P.cool_coef(p, rpm, self.eta) * (self.T_hat - ambient))

        d_cool = _clip((1.0 - self.eta) / p.cool_k)

        # ---- pressure -> leak severity
        d_leak = self.d["leak"]
        if ok["press"]:
            ratio = x["press"] / max(P.press_expected(p, rpm), 1e-3)
            self.p_ema = ratio if self.p_ema is None else self.p_ema + 0.12 * (ratio - self.p_ema)
            d_leak = _clip(((1 - self.p_ema) - p.c_p_cool * d_cool - p.c_p_bear * self.d["bear"]) / p.leak_p)

        # ---- vibration -> bearing wear severity
        d_bear = self.d["bear"]
        if ok["vib"]:
            ex = x["vib"] - P.vib_expected(p, rpm)
            self.vib_ema = ex if self.vib_ema is None else self.vib_ema + 0.12 * (ex - self.vib_ema)
            d_bear = _clip((self.vib_ema - p.c_vib_leak * d_leak - p.c_vib_cool * d_cool) / p.bear_vib)

        # light smoothing, never negative
        for k, v in (("cool", d_cool), ("bear", d_bear), ("leak", d_leak)):
            self.d[k] = self.d[k] + 0.25 * (v - self.d[k]) if self.n > 1 else v
            self.hist[k].append(self.d[k])
        return self.estimate()

    # ------------------------------------------------------------------ outputs
    def health(self) -> float:
        return P.health_from(self.d["cool"], self.d["bear"], self.d["leak"])

    def rates(self, load_now: float):
        """Estimate per-fault growth parameter r (d' = r*g(load)*(1+d)).
        Returns (r_hat, r_sigma) dicts, shrinking noisy slopes toward zero."""
        g = P.growth(load_now)
        r_hat, r_sig = {}, {}
        for k, h in self.hist.items():
            n = len(h)
            if n < 20 or self.d[k] < 0.04:
                r_hat[k], r_sig[k] = 0.0, 0.00003
                continue
            y = np.log1p(np.asarray(h, dtype=float))
            t = np.arange(n, dtype=float)
            tm = t.mean(); ym = y.mean()
            sxx = ((t - tm) ** 2).sum()
            slope = ((t - tm) * (y - ym)).sum() / sxx
            resid = y - (ym + slope * (t - tm))
            se = math.sqrt(max((resid ** 2).sum() / max(n - 2, 1), 1e-12) / sxx)
            r = max(0.0, slope - 1.0 * se) / g          # shrink by 1 std-err (conservative against noise)
            r_hat[k], r_sig[k] = r, se / g
        return r_hat, r_sig

    def estimate(self) -> dict:
        return {
            "eta": self.eta,
            "T_hat": self.T_hat,
            "severity": dict(self.d),
            "health": self.health(),
            "channels_used": dict(self.used),
        }
