"""Model bundle: cross-sensor predictors, classifiers, regressors and anomaly detector."""
from __future__ import annotations
import json
from pathlib import Path
import numpy as np
import joblib
from sklearn.ensemble import HistGradientBoostingRegressor as HGBR
from . import physics as P
from .physics import Phys, CH

OTHERS = {c: [o for o in CH if o != c] for c in CH}
SENSOR_CLASSES = ["none"] + CH


class ModelBundle:
    def __init__(self):
        self.phys: Phys = Phys()
        self.cross: dict = {}
        self.cross_sigma: dict = {}
        self.sensor_clf = None
        self.engine_clf = None
        self.health_reg = None
        self.forecast_reg = None
        self.iforest = None
        self.anom_ref = 0.0
        self.anom_scale = 1.0
        self.metrics: dict = {}
        self.trained_on = "simulator"

    # ----------------------------------------------------------- cross-sensor prediction
    def _xin(self, X, amb, c):
        idx = [CH.index(o) for o in OTHERS[c]]
        return np.concatenate([X[:, idx], np.asarray(amb).reshape(-1, 1)], 1)

    def fit_cross(self, X, amb, X_val, amb_val):
        for c in CH:
            m = HGBR(max_iter=120, max_depth=5, learning_rate=0.1, random_state=0)
            m.fit(self._xin(X, amb, c), X[:, CH.index(c)])
            self.cross[c] = m
        for c in CH:
            r = X_val[:, CH.index(c)] - self.cross[c].predict(self._xin(X_val, amb_val, c))
            mad = np.median(np.abs(r - np.median(r))) * 1.4826
            self.cross_sigma[c] = float(max(mad, 1e-6))

    def cross_predict(self, X, amb):
        return np.stack([self.cross[c].predict(self._xin(X, amb, c)) for c in CH], 1)

    def zscores(self, X, amb):
        pred = self.cross_predict(X, amb)
        sig = np.array([self.cross_sigma[c] for c in CH])
        return (X - pred) / sig[None, :]

    # ----------------------------------------------------------- anomaly
    def anomaly_index(self, F):
        s = self.iforest.decision_function(F)
        return np.clip((self.anom_ref - s) / self.anom_scale, 0.0, 1.0)

    # ----------------------------------------------------------- io
    def save(self, d: Path):
        d = Path(d); d.mkdir(parents=True, exist_ok=True)
        joblib.dump(self, d / "bundle.joblib", compress=3)
        (d / "metrics.json").write_text(json.dumps(self.metrics, indent=2))

    @staticmethod
    def load(d: Path) -> "ModelBundle":
        return joblib.load(Path(d) / "bundle.joblib")

    @staticmethod
    def exists(d: Path) -> bool:
        return (Path(d) / "bundle.joblib").exists()
