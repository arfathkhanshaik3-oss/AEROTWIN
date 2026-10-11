"""
Calibrate the digital twin's healthy-engine envelope to YOUR engine from a CSV of healthy flight/test-stand logs.
    python -m aerotwin.calibrate healthy.csv        (columns: temp,rpm,vib,press[,ambient]; 1 Hz, in time order)
    python -m aerotwin.train --healthy-csv healthy.csv      # then adapt the label-free models
Fits: vibration baseline (v_a + v_b*rpm), oil-pressure law (p_k*(rpm/3120)^p_exp) and the thermal heat/cooling ratio.
Thermal capacity (time constant) is NOT fitted by this tool - tune Phys.cap from a throttle step-response if needed.
"""
import json, sys
import numpy as np
from .config import settings
from .physics import Phys, CH


def main(path):
    d = np.genfromtxt(path, delimiter=",", names=True)
    X = np.stack([d[c] for c in CH], 1)
    amb = d["ambient"] if "ambient" in d.dtype.names else np.full(len(X), 25.0)
    ok = np.isfinite(X).all(1) & (X[:, 1] > 500); X, amb = X[ok], amb[ok]
    temp, rpm, vib, press = X.T
    p = Phys()
    b, a = np.polyfit(rpm, vib, 1); p.v_a, p.v_b = float(a), float(b)
    e, lk = np.polyfit(np.log(rpm / 3120.0), np.log(np.maximum(press, 1e-3)), 1); p.p_exp, p.p_k = float(e), float(np.exp(lk))
    # thermal: steady-state samples only (CHT nearly constant over +-10 s)
    dT = np.abs(np.convolve(np.gradient(temp), np.ones(10) / 10, mode="same"))
    ss = dT < 0.05
    if ss.sum() < 50:
        ss = np.ones_like(ss)
    best = None
    for he in np.arange(1.0, 2.01, 0.1):
        f = (rpm[ss] / 3000.0) ** he / (0.5 + 0.5 * rpm[ss] / 3000.0)
        k = float((f * (temp[ss] - amb[ss])).sum() / (f * f).sum())
        err = float((((temp[ss] - amb[ss]) - k * f) ** 2).mean())
        if best is None or err < best[0]:
            best = (err, he, k)
    _, p.heat_exp, k = best; p.h0 = 1.0; p.kq = k
    out = settings.model_dir / "calibration.json"
    out.parent.mkdir(parents=True, exist_ok=True); out.write_text(p.to_json())
    res = {c: float(np.std(np.diff(X[:, i])) / np.sqrt(2)) for i, c in enumerate(CH)}
    print("wrote", out); print(p.to_json())
    print("estimated sensor noise (1 sigma):", {k: round(v, 4) for k, v in res.items()},
          "\n -> if these differ a lot from aerotwin.features.SIGMA (0.6, 8, 0.025, 0.015), edit SIGMA and retrain.")
    print("next: python -m aerotwin.train --healthy-csv", path)


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    main(sys.argv[1])
