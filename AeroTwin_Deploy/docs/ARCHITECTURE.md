# AeroTwin architecture

## Data flow (one frame per second)
1. **Ingest** – sample `{temp,rpm,vib,press,throttle?,ambient?}` from simulator / HTTP / WS / serial / MAVLink; `Resampler` averages to 1 Hz.
2. **Validation** – range checks (`brain.RANGE`); NaN/out-of-range ⇒ channel treated as offline.
3. **Graceful reconstruction** – an offline channel is predicted from the other three (HistGradientBoosting cross-predictors, trained on healthy data).
4. **Cross-sensor residuals** – z-score of every channel vs its cross-prediction over a 30 s window.
5. **Fault classification** – two classifiers on residual/flat-line/jump/slope features:
   *engine head* (binary: coherent multi-channel deviation) and *sensor head* (none/temp/rpm/vib/press). Rules add certainty: NaN ⇒ dropout, 15 identical samples ⇒ stuck.
   A channel is **masked** if the sensor head is confident (>0.6, smoothed) and the engine head is not (<0.5).
6. **Digital twin** (`twin.py`) – thermal ODE `C dT/dt = Q(rpm) − h·η·(0.5+0.5·rpm/3000)(T−Tamb)` with online LMS estimate of cooling efficiency η;
   vibration and oil-pressure envelopes give bearing-wear and leak severities; couplings between faults are modelled. Severity histories give growth rates `r` (log-linear regression, shrunk by one standard error).
7. **ML health** – window features (last/mean/std/slope + physics residuals) → health (0–100), +120 s change, Isolation-Forest anomaly index.
8. **Fusion** – `health = 0.7·ML + 0.3·twin`, EMA smoothed; confidence from ML/twin agreement and masked-sensor count; alert level with hysteresis (3 frames to escalate, 5 to de-escalate).
9. **What-if** (`whatif.py`) – fault growth law `d' = r·g(load)·(1+d)` has closed form `d(t) = (1+d₀)e^{r·g·t} − 1`, so 300 Monte-Carlo draws (log-normal rate uncertainty) × 3 scenarios evaluate in ms.
   Survival = P(health ≥ 35 at end of exposure). Utility = survival × (1.0 aircraft + 0.6·mission fraction). Confidence = share of draws in which the chosen option is (weakly) best (common random numbers).
   If every option has ~0 survival ⇒ **emergency** ⇒ return.
10. **Frame** broadcast over WebSocket and logged to SQLite.

## Health definition (synthetic ground truth)
`health = 100·(1 − 0.85·max(dₖ) − 0.15·mean(dₖ))`, dₖ ∈ [0,1] = severity of cooling loss / bearing wear / oil leak. <80 degrading, <50 critical, <35 failed.

## Training data
`train.py` simulates 400 labelled runs (healthy, engine-fault, sensor-fault, combined; random ambient, load profile, fault type/rate, sensor-fault mode) of 1200 s, 80/20 split by run.
Window features every 6 s. Metrics are on held-out runs (`models/metrics.json`).

## Frontend
Static files served by FastAPI. `drone.js` = headless-testable scene logic (`DroneRig`) + WebGL wrapper (`DroneView`); Three.js r128 vendored (works offline). `app.js` = WebSocket client, canvas speedometers/charts, controls.
