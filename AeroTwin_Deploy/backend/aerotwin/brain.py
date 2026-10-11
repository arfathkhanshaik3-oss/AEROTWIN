"""
Brain: the streaming inference pipeline.  One call to process() per second of sensor data.

    raw sensors -> validation/range -> graceful reconstruction of missing channels
                -> cross-sensor consistency (sensor fault vs engine fault)
                -> trusted sensor vector -> digital twin update + ML health/forecast/anomaly
                -> fusion + alert hysteresis -> what-if simulation -> recommendation
"""
from __future__ import annotations
import math
from collections import deque
import numpy as np
from . import physics as P
from . import whatif
from .features import W, health_features, anomaly_features, sensor_features
from .models import ModelBundle, SENSOR_CLASSES
from .physics import CH
from .twin import DigitalTwin

RANGE = {"temp": (-40, 450), "rpm": (0, 9000), "vib": (0, 25), "press": (0, 12)}
DEFAULT = np.array([95.0, 3100.0, 0.85, 1.4])
STUCK_N = 15


def _f(x):
    return None if x is None or (isinstance(x, float) and math.isnan(x)) else float(x)


class Brain:
    def __init__(self, bundle: ModelBundle, mission_s=900.0, return_s=240.0, use_validator=True):
        self.b = bundle
        self.use_validator = use_validator
        self.mission_s, self.return_s = mission_s, return_s
        self.twin = DigitalTwin(bundle.phys)
        self.sig = np.array([bundle.cross_sigma[c] for c in CH])
        self.reset()

    def reset(self):
        self.twin.reset()
        self.xs = deque(maxlen=W)          # measured (filled) window
        self.zs = deque(maxlen=W)          # cross-sensor z-scores
        self.trusted = deque(maxlen=W)     # trusted window
        self.last_good = None
        self.sf_prob = np.zeros(5); self.sf_prob[0] = 1.0
        self.eng_prob = 0.0
        self.h_fused = None
        self.level = "nominal"; self._cnt_up = 0; self._cnt_dn = 0
        self.t = 0
        self.log = deque(maxlen=200)

    # ------------------------------------------------------------------ main entry
    def process(self, sample: dict, throttle=None, ambient=25.0, ts=None, truth=None, whatif_on=True) -> dict:
        b = self.b
        amb = float(ambient if ambient is not None else 25.0)
        x = np.array([np.nan if _f(sample.get(c)) is None else float(sample[c]) for c in CH])
        range_fail = {}
        for i, c in enumerate(CH):
            lo, hi = RANGE[c]
            if not np.isnan(x[i]) and not (lo <= x[i] <= hi):
                x[i] = np.nan; range_fail[c] = True
        nan = np.isnan(x)
        base = self.last_good if self.last_good is not None else DEFAULT
        xf = np.where(nan, base, x)
        reconstructed = np.zeros(4, bool)
        if nan.any() and len(self.xs) >= 5:
            pr = b.cross_predict(xf[None, :], np.array([amb]))[0]
            xf = np.where(nan, pr, xf); reconstructed = nan.copy()
        pred = b.cross_predict(xf[None, :], np.array([amb]))[0]
        z = (xf - pred) / self.sig
        z[nan] = 0.0
        self.xs.append(xf); self.zs.append(z)
        if self.last_good is None:
            self.last_good = xf.copy()
        else:
            self.last_good = np.where(nan, self.last_good, x)
        self.t += 1
        warm = len(self.xs) >= W

        rule_fault = nan.copy()
        stuck = np.zeros(4, bool)
        if len(self.xs) >= STUCK_N:
            a = np.array(self.xs)[-STUCK_N:]
            stuck = (np.ptp(a, axis=0) == 0.0) & ~nan
            rule_fault |= stuck

        probs_s = np.zeros(5); probs_s[0] = 1.0; p_eng = 0.0
        if warm and self.use_validator:
            Xw = np.array(self.xs)[None]; Zw = np.array(self.zs)[None]
            Fs = sensor_features(Xw, Zw)
            probs_s = b.sensor_clf.predict_proba(Fs)[0]; p_eng = float(b.engine_clf.predict_proba(Fs)[0, 1])
        for c in np.where(rule_fault)[0]:
            probs_s = np.zeros(5); probs_s[c + 1] = 1.0
        self.sf_prob = 0.6 * self.sf_prob + 0.4 * probs_s
        self.eng_prob = 0.5 * self.eng_prob + 0.5 * p_eng

        faulty = set(np.where(rule_fault)[0].tolist())
        k = int(np.argmax(self.sf_prob))
        if self.use_validator and k > 0 and self.sf_prob[k] > 0.6 and self.eng_prob < 0.5:
            faulty.add(k - 1)
        # trusted sensor vector (faulty channels are replaced by reconstruction from the others)
        xt = xf.copy()
        if faulty:
            for c in faulty:
                xt[c] = pred[c] if not nan[c] else xf[c]
        self.trusted.append(xt)
        tw_in = {c: (None if i in faulty else float(xf[i])) for i, c in enumerate(CH)}
        est = self.twin.update(tw_in, ambient=amb)

        # ---------------- ML health / forecast / anomaly
        if warm:
            Xt = np.array(self.trusted)[None]; A = np.array([amb])
            Fh = health_features(Xt, A, b.phys)
            h_ml = float(np.clip(b.health_reg.predict(Fh)[0], 0, 100))
            f_ml = float(np.clip(h_ml + b.forecast_reg.predict(Fh)[0], 0, 100))
            anom = float(b.anomaly_index(anomaly_features(Xt, A, b.phys))[0])
        else:
            h_ml, f_ml, anom = est["health"], est["health"], 0.0
        h_tw = est["health"]
        h = 0.7 * h_ml + 0.3 * h_tw if warm else h_tw
        self.h_fused = h if self.h_fused is None else self.h_fused + 0.5 * (h - self.h_fused)
        h = float(self.h_fused)
        agree = 1.0 - min(1.0, abs(h_ml - h_tw) / 40.0)
        conf = float(np.clip((0.55 + 0.45 * agree) - 0.15 * len(faulty), 0.05, 0.99)) if warm else 0.3
        self._update_level(h)

        # ---------------- what-if
        load_now = float(throttle) if throttle is not None else P.load_from_rpm(xt[1] / max(1e-3, 1 - 0.15 * max(est["severity"].values())))
        r_hat, r_sig = self.twin.rates(load_now)
        wi = whatif.simulate(est["severity"], r_hat, r_sig, load_now, self.mission_s, self.return_s, seed=self.t) if (whatif_on and warm) else None

        sens = {}
        for i, c in enumerate(CH):
            if nan[i]:
                state, conf_c = ("offline", 0.0)
            elif stuck[i]:
                state, conf_c = ("stuck", 0.02)
            elif i in faulty:
                state, conf_c = ("masked", float(max(0.02, 1 - self.sf_prob[i + 1])))
            else:
                state, conf_c = ("ok", float(1 - self.sf_prob[i + 1]))
            sens[c] = {"value": _f(x[i]), "used": float(xt[i]), "state": state, "confidence": round(conf_c, 3),
                       "z": round(float(z[i]), 2)}
        sev = est["severity"]
        main = max(sev, key=sev.get) if max(sev.values()) > 0.08 else None
        reasons = []
        if main:
            reasons.append(f"{P.FAULT_NAMES[main]} severity {sev[main]*100:.0f}%")
        if (1 - est["eta"]) > 0.05:
            reasons.append(f"Cooling efficiency down {100*(1-est['eta']):.0f}%")
        for i in faulty:
            reasons.append(f"{CH[i].upper()} sensor untrusted ({sens[CH[i]]['state']}) - value reconstructed from other sensors")
        frame = {
            "ts": ts, "t": self.t, "warming_up": not warm,
            "sensors": sens, "ambient": amb, "throttle": None if throttle is None else float(throttle), "load_est": round(load_now, 3),
            "health": {"fused": round(h, 1), "ml": round(h_ml, 1), "twin": round(h_tw, 1), "forecast_ml_120s": round(f_ml, 1),
                       "anomaly": round(anom, 3), "confidence": round(conf, 3)},
            "alert": {"level": self.level, "sensor_issue": bool(faulty), "reasons": reasons},
            "twin": {"eta": round(est["eta"], 3), "cooling_efficiency_pct": round(100 * est["eta"], 1),
                     "severity": {k: round(v, 3) for k, v in sev.items()},
                     "rates_per_min": {k: round(60 * r_hat[k], 4) for k in r_hat},
                     "T_hat": None if est["T_hat"] is None else round(est["T_hat"], 1),
                     "channels_used": est["channels_used"]},
            "diagnosis": {"engine_fault_prob": round(self.eng_prob, 3),
                          "sensor_probs": {SENSOR_CLASSES[i]: round(float(self.sf_prob[i]), 3) for i in range(5)},
                          "declared_sensor_faults": [CH[i] for i in sorted(faulty)],
                          "primary_fault": main, "range_fail": list(range_fail)},
            "whatif": wi,
        }
        if truth is not None:
            frame["sim_truth"] = truth
        return frame

    # ------------------------------------------------------------------ alert hysteresis
    def _update_level(self, h):
        want = P.status_for(h)
        order = {"nominal": 0, "degrading": 1, "critical": 2}
        if order[want] > order[self.level]:
            self._cnt_up += 1; self._cnt_dn = 0
            if self._cnt_up >= 3:
                self.level = want; self._cnt_up = 0
        elif order[want] < order[self.level]:
            lim = P.WARN_HEALTH if self.level == "degrading" else P.CRIT_HEALTH
            self._cnt_dn += 1; self._cnt_up = 0
            if self._cnt_dn >= 5 and h > lim + 2:
                self.level = want; self._cnt_dn = 0
        else:
            self._cnt_up = self._cnt_dn = 0
