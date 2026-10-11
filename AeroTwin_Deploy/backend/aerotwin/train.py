"""
Training pipeline.
    python -m aerotwin.train                  # full training on the built-in simulator (~2-4 min)
    python -m aerotwin.train --scale 0.25     # quick smoke training
    python -m aerotwin.train --healthy-csv my_healthy_logs.csv
        -> re-trains the label-free models (cross-sensor validators + anomaly detector) on REAL healthy
           flight data (columns: temp,rpm,vib,press[,ambient]); the supervised models are kept from simulation.
All models + honest held-out metrics are written to models/ .
"""
from __future__ import annotations
import argparse, json, random, time
from pathlib import Path
import numpy as np
from sklearn.ensemble import (HistGradientBoostingClassifier as HGBC, HistGradientBoostingRegressor as HGBR,
                              IsolationForest)
from sklearn.metrics import (accuracy_score, f1_score, precision_score, recall_score, roc_auc_score,
                             confusion_matrix)
from . import physics as P
from .config import settings
from .features import W, H_FORECAST, health_features, anomaly_features, sensor_features, windows
from .models import ModelBundle, SENSOR_CLASSES
from .physics import CH, Phys, SIM_PHYS
from .simulator import EngineSim

T_RUN = 1200
STRIDE = 6


# ============================================================================ data generation
def gen_run(kind: str, seed: int, T=T_RUN):
    rng = random.Random(seed)
    sim = EngineSim(seed=seed, ambient=rng.uniform(12, 38))
    e_on = int(rng.uniform(150, 600)); s_on = int(rng.uniform(100, 900))
    faults = []
    if kind in ("engine", "combo"):
        faults = [rng.choice(P.FAULTS)]
        if rng.random() < 0.15:
            faults.append(rng.choice([f for f in P.FAULTS if f != faults[0]]))
    s_ch = rng.choice(CH) if kind in ("sensor", "combo") else None
    s_mode = rng.choice(["stuck", "drift", "bias", "spike"])
    X = np.zeros((T, 4)); amb = np.zeros(T); hl = np.zeros(T); dd = np.zeros((T, 3))
    sf = np.zeros((T, 4), bool); sf_any = np.zeros(T, bool); load = np.zeros(T)
    for t in range(T):
        if t == e_on:
            ttf = rng.uniform(200, 900)
            for f in faults:
                sim.inject_fault(f, ttf=ttf * rng.uniform(0.8, 1.25))
        if s_ch is not None and t == s_on:
            mag = None
            if s_mode == "drift":
                mag = rng.choice([-1, 1]) * rng.uniform(0.03, 0.3) * {"temp": .6, "rpm": 8, "vib": .025, "press": .015}[s_ch]
            sim.inject_sensor_fault(s_ch, s_mode, mag)
        r = sim.step()
        X[t] = [r["meas"][c] for c in CH]; amb[t] = r["ambient"]; hl[t] = r["truth"]["health"]
        dd[t] = [r["truth"]["d"][f] for f in P.FAULTS]
        sf[t] = [r["truth"]["sensor_fault"][c] for c in CH]; sf_any[t] = bool(sim.sfault); load[t] = r["throttle"]
    return dict(kind=kind, X=X, amb=amb, health=hl, d=dd, sf=sf, sf_any=sf_any, load=load)


def make_runs(scale=1.0, seed0=1000):
    plan = [("healthy", 70), ("engine", 160), ("sensor", 120), ("combo", 50)]
    runs = []
    k = seed0
    for kind, n in plan:
        for _ in range(max(2, int(n * scale))):
            runs.append(gen_run(kind, k)); k += 1
    return runs


def clean_mask(r):
    return (r["d"].max(1) < 0.005) & (~r["sf_any"])


# ============================================================================ training
def build_tables(runs, bundle: ModelBundle, p: Phys, stride=STRIDE):
    rows = dict(Fh=[], Fa=[], Fs=[], y_h=[], y_f=[], y_e=[], m_e=[], y_s=[], dmax=[], clean=[], run=[], t=[])
    for ri, r in enumerate(runs):
        Z = bundle.zscores(r["X"], r["amb"])
        Xw = windows(r["X"]); Zw = windows(Z)
        T = len(r["X"])
        idx = np.arange(0, Xw.shape[0], stride)                 # window i ends at t=i+W-1
        t_end = idx + W - 1
        amb = r["amb"][t_end]
        Fh = health_features(Xw[idx], amb, p); Fa = anomaly_features(Xw[idx], amb, p); Fs = sensor_features(Xw[idx], Zw[idx])
        dmax = r["d"].max(1)[t_end]
        ys = np.zeros(len(idx), int)
        for c in range(4):
            ys = np.where((ys == 0) & r["sf"][t_end, c], c + 1, ys)
        tf = np.minimum(t_end + H_FORECAST, T - 1)
        rows["Fh"].append(Fh); rows["Fa"].append(Fa); rows["Fs"].append(Fs)
        rows["y_h"].append(r["health"][t_end]); rows["y_f"].append(r["health"][tf])
        rows["y_e"].append((dmax >= 0.12).astype(int)); rows["m_e"].append((dmax < 0.05) | (dmax >= 0.12))
        rows["y_s"].append(ys); rows["dmax"].append(dmax); rows["clean"].append(clean_mask(r)[t_end])
        rows["run"].append(np.full(len(idx), ri)); rows["t"].append(t_end)
    return {k: np.concatenate(v) for k, v in rows.items()}


def train(scale=1.0, out=None, seed=0, verbose=True):
    t0 = time.time()
    out = Path(out or settings.model_dir)
    log = (lambda *a: print(*a, flush=True)) if verbose else (lambda *a: None)
    log(f"[1/6] simulating {int((70+160+120+50)*scale)} labelled engine runs ...")
    runs = make_runs(scale)
    rng = np.random.RandomState(seed)
    order = rng.permutation(len(runs)); n_test = max(4, int(0.2 * len(runs)))
    test_ids = set(order[:n_test].tolist())
    tr = [r for i, r in enumerate(runs) if i not in test_ids]; te = [r for i, r in enumerate(runs) if i in test_ids]
    p = SIM_PHYS
    b = ModelBundle(); b.phys = p

    log("[2/6] training cross-sensor validators on healthy data ...")
    Xc = np.concatenate([r["X"][clean_mask(r)][::2] for r in tr]); Ac = np.concatenate([r["amb"][clean_mask(r)][::2] for r in tr])
    Xv = np.concatenate([r["X"][clean_mask(r)][::3] for r in te]); Av = np.concatenate([r["amb"][clean_mask(r)][::3] for r in te])
    b.fit_cross(Xc, Ac, Xv, Av)
    log("      cross-sensor residual sigma:", {c: round(v, 4) for c, v in b.cross_sigma.items()})

    log("[3/6] building feature tables ...")
    Tr = build_tables(tr, b, p); Te = build_tables(te, b, p)

    log("[4/6] training health / forecast models ...")
    b.health_reg = HGBR(max_iter=250, max_depth=6, learning_rate=0.08, random_state=0).fit(Tr["Fh"], Tr["y_h"])
    # forecast = current health estimate + learned change over the horizon (delta target beats absolute target)
    b.forecast_reg = HGBR(max_iter=250, max_depth=5, learning_rate=0.06, min_samples_leaf=40, random_state=0).fit(Tr["Fh"], Tr["y_f"] - Tr["y_h"])

    log("[5/6] training sensor-vs-engine fault classifiers + anomaly detector ...")
    m = Tr["m_e"]
    b.engine_clf = HGBC(max_iter=200, max_depth=6, learning_rate=0.1, class_weight="balanced", random_state=0).fit(
        Tr["Fs"][m], Tr["y_e"][m])
    b.sensor_clf = HGBC(max_iter=200, max_depth=6, learning_rate=0.1, class_weight="balanced", random_state=0).fit(Tr["Fs"], Tr["y_s"])
    cl = Tr["clean"]
    b.iforest = IsolationForest(n_estimators=200, contamination="auto", random_state=0).fit(Tr["Fa"][cl])
    s_h = b.iforest.decision_function(Te["Fa"][Te["clean"]])
    b.anom_ref = float(np.percentile(s_h, 0.5))
    sev = Te["dmax"] > 0.5
    b.anom_scale = float(max(b.anom_ref - np.median(b.iforest.decision_function(Te["Fa"][sev])), 1e-3))

    log("[6/6] evaluating on held-out runs ...")
    met = evaluate_windows(b, Te)
    b.metrics = {"window_level": met, "n_train_runs": len(tr), "n_test_runs": len(te),
                 "trained_on": "simulator", "trained_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                 "train_seconds": round(time.time() - t0, 1), "window_s": W, "forecast_horizon_s": H_FORECAST}
    b.save(out)
    log(json.dumps(met, indent=2)); log(f"saved -> {out}  ({time.time()-t0:.0f}s)")
    return b, te


def evaluate_windows(b: ModelBundle, Te):
    ph = b.health_reg.predict(Te["Fh"]); pf = np.clip(ph + b.forecast_reg.predict(Te["Fh"]), 0, 100)
    cur = Te["y_h"]; fut = Te["y_f"]
    m = Te["m_e"]
    pe = b.engine_clf.predict(Te["Fs"][m]); ye = Te["y_e"][m]
    ps = b.sensor_clf.predict(Te["Fs"]); ys = Te["y_s"]
    an = b.anomaly_index(Te["Fa"])
    sel = Te["clean"] | (Te["dmax"] > 0.15)
    auc = roc_auc_score((Te["dmax"][sel] > 0.15).astype(int), an[sel])
    cm = confusion_matrix(ys, ps, labels=list(range(5))).tolist()
    rec = {SENSOR_CLASSES[i]: round(float(recall_score(ys == i, ps == i)), 3) for i in range(5) if (ys == i).any()}
    return {
        "health_mae": round(float(np.mean(np.abs(ph - cur))), 2),
        "forecast_mae_120s": round(float(np.mean(np.abs(pf - fut))), 2),
        "forecast_mae_persistence_baseline": round(float(np.mean(np.abs(cur - fut))), 2),
        "forecast_mae_120s_active_faults": round(float(np.mean(np.abs(pf - fut)[Te["dmax"] > 0.1])), 2),
        "forecast_mae_persistence_active_faults": round(float(np.mean(np.abs(cur - fut)[Te["dmax"] > 0.1])), 2),
        "engine_fault_accuracy": round(float(accuracy_score(ye, pe)), 3),
        "engine_fault_precision": round(float(precision_score(ye, pe)), 3),
        "engine_fault_recall": round(float(recall_score(ye, pe)), 3),
        "sensor_fault_accuracy": round(float(accuracy_score(ys, ps)), 3),
        "sensor_fault_recall_by_class": rec,
        "sensor_confusion_matrix(rows=true,cols=pred;none,temp,rpm,vib,press)": cm,
        "anomaly_auc": round(float(auc), 3),
    }


def retrain_on_healthy_csv(csv_path, model_dir=None):
    """Adapt the label-free models to a real engine using healthy flight logs."""
    model_dir = Path(model_dir or settings.model_dir)
    from .physics import load_twin_phys
    b = ModelBundle.load(model_dir)
    b.phys = load_twin_phys(model_dir)          # use the calibrated healthy envelope
    d = np.genfromtxt(csv_path, delimiter=",", names=True)
    X = np.stack([d[c] for c in CH], 1)
    amb = d["ambient"] if "ambient" in d.dtype.names else np.full(len(X), 25.0)
    ok = np.isfinite(X).all(1); X, amb = X[ok], amb[ok]
    n = len(X); cut = int(n * 0.8)
    b.fit_cross(X[:cut], amb[:cut], X[cut:], amb[cut:])
    Xw = windows(X); aw = amb[W - 1:]
    Fa = anomaly_features(Xw[::3], aw[::3], b.phys)
    b.iforest.fit(Fa)
    s = b.iforest.decision_function(Fa)
    b.anom_ref = float(np.percentile(s, 0.5)); b.anom_scale = float(max(b.anom_ref - s.min(), 1e-3))
    b.trained_on = "simulator + real healthy data"
    b.metrics["real_healthy_samples"] = int(n)
    b.save(model_dir)
    print(f"adapted cross-sensor validators + anomaly detector on {n} real healthy samples")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--scale", type=float, default=1.0)
    ap.add_argument("--healthy-csv", default=None)
    a = ap.parse_args()
    if a.healthy_csv:
        retrain_on_healthy_csv(a.healthy_csv)
    else:
        train(a.scale)
