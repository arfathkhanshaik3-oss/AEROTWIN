---
title: AeroTwin
emoji: ✈️
colorFrom: blue
colorTo: indigo
sdk: docker
app_port: 7860
pinned: false
---

# AeroTwin — Mission-Aware Digital Twin for Aero Piston Engines (MALE UAV)

Real-time engine health monitoring, fault prediction and mission-level decision support.
**Connect a data source → open the dashboard.** No data source yet? A built-in physics simulator drives everything.

```
Drone / ESP32 / MAVLink ──► [ ingest ] ──► validation ──► sensor-vs-engine fault classifier ──► trusted sensors
                                                                                                   │
        dashboard (3D airframe, speedometers, alerts) ◄── what-if simulator ◄── digital twin + ML health/forecast
```

## 1. Run it (2 minutes)
```bash
./start.sh            # Linux/macOS      (Windows: start.bat)   — creates a venv, installs deps, starts the server
# or: pip install -r requirements.txt && python run.py
```
Open **http://localhost:8000**. Python 3.10+. Trained models ship in `models/` (re-trained automatically if they cannot be loaded).
Docker: `docker compose up --build`.

First thing to do: press **BEARING WEAR** in the *Simulator test harness*, watch the speedometer fall, the screen flash red,
the propeller slow, the engine glow/smoke, and the recommendation change to RETURN. Then press **RESET SYSTEM**.
Then inject a *stuck vib sensor* — the engine stays green: that is the sensor-vs-engine-fault logic.

## 2. Connect your drone
Switch to **LIVE DEVICE** (header button or `AEROTWIN_SOURCE=live`). Anything that can send these values works:

| channel | unit | required |
|---|---|---|
| `temp` cylinder-head temperature | °C | yes |
| `rpm` engine speed | rpm | yes |
| `vib` vibration magnitude | g | yes |
| `press` oil pressure | bar | yes |
| `throttle` 0–1, `ambient` °C | | optional |

Missing values may be `null`/omitted — the system degrades gracefully (reconstructs the channel, lowers confidence).

| Method | How |
|---|---|
| **HTTP** (ESP32, Raspberry Pi, ground station) | `POST /api/ingest` JSON, ~1 Hz (faster rates are averaged to 1 Hz). `firmware/esp32_aerotwin/` has a ready sketch. |
| **WebSocket** | `ws://host:8000/ws/ingest`, same JSON |
| **Serial / telemetry radio** | `AEROTWIN_LIVE_INPUT=serial AEROTWIN_SERIAL_PORT=COM5` — device prints a JSON line or CSV `temp,rpm,vib,press` |
| **MAVLink / ArduPilot** | `AEROTWIN_LIVE_INPUT=mavlink AEROTWIN_MAVLINK=udp:127.0.0.1:14550` — reads `EFI_STATUS` (rpm, CHT, manifold pressure, throttle) + `VIBRATION`. MAVLink has no oil-pressure message; manifold pressure is used as `press` unless you map your own sensor in `sources.py`. |

Test the live path with no hardware: `cd backend && python -m aerotwin.fake_drone --url http://localhost:8000 --fault bear@60`
Security: set `AEROTWIN_API_KEY` and devices must send `X-API-Key`.
**AeroTwin is advisory only. It never sends a command to the aircraft.**

### Make it fit *your* engine (important)
The shipped models are trained on a physics simulator. Before trusting numbers on a real engine:
1. Log 10–30 min of **healthy** running (`temp,rpm,vib,press[,ambient]`, 1 Hz, in order) → `healthy.csv`.
2. `python -m aerotwin.calibrate healthy.csv` — fits the twin's healthy envelope (vibration baseline, oil-pressure law, heat/cooling ratio).
3. `python -m aerotwin.train --healthy-csv healthy.csv` — re-trains the label-free parts (cross-sensor validators, anomaly detector) on real data.
4. Set mission time in the *What-If* tab (`AEROTWIN_MISSION_S`, `AEROTWIN_RETURN_S`).
Real run-to-failure data is rare; supervised health/forecast models stay simulator-trained. Re-validate before relying on them.

## 3. What is inside
| Component | File | What it does |
|---|---|---|
| Engine simulator | `simulator.py` | thermal ODE + vibration/pressure laws, 3 progressive engine faults, 5 sensor fault modes (stuck, drift, bias, dropout, spike) |
| Digital twin | `twin.py` | running physics model corrected by data; online estimate of cooling efficiency, bearing-wear and oil-leak severity + growth rates |
| Sensor validator | `models.py`, `brain.py` | each sensor is predicted from the other three; a classifier separates *engine fault* (coherent multi-sensor pattern) from *sensor fault* (isolated / flat-line / jump). Faulty channels are masked and reconstructed |
| ML models | `train.py` | gradient-boosted health regressor, 120 s forecast (delta model), engine-fault & sensor-fault classifiers, Isolation-Forest anomaly index |
| What-if engine | `whatif.py` | Monte-Carlo forward simulation of *continue / modify / return* from the twin state; survival probability, utility, recommendation + confidence |
| Server | `server.py` | FastAPI: REST, WebSocket, ingest, SQLite log, CSV export |
| Dashboard | `frontend/` | analog speedometer gauges, forecast chart, what-if cards, diagnostics, **3D airframe twin** (prop speed ← RPM, engine glow ← CHT, shake ← vibration, smoke/roll/sink + red full-screen alert ← critical, bank-turn ← return) |

Details: `docs/ARCHITECTURE.md`. Measured results: `docs/RESULTS.md`.

## 4. Measured performance (simulated engine, fresh held-out runs)
| metric | result |
|---|---|
| health estimate error (MAE) | 0.8 health points |
| 120 s forecast error during active faults | 4.3 vs 9.6 for "no change" baseline |
| engine-fault detection | 8/8 caught, median warning **93 s** (min 38 s) before CRITICAL |
| false alarms, healthy engine | 0/8 runs |
| false engine alarms from a faulty sensor | **0/8 with validator** vs 1/8 without |
| pipeline latency | ~30 ms per 1 Hz frame |
Small samples (8 runs per scenario) on **simulated** data — shows the design works, not real-world accuracy.

## 5. Honest limitations
* Not validated on a real aircraft or real engine data; not certified; do not use as the sole basis for flight-safety decisions.
* Simulator faults mature in minutes; real wear takes hours. Set mission/return times accordingly; the ML forecast horizon is fixed at 120 s and must be re-trained for real timescales.
* Sensor-vs-engine classifier assumes at most one faulty sensor at a time, and is weaker when a real engine fault and a sensor fault happen simultaneously.
* The 3D aircraft is a stylised model built from primitives, not a photoreal asset (drop in a GLTF if you have one). WebGL could not be rendered in the build environment — the scene logic and dashboard were verified headlessly; give it a visual check on first run.
* ESP32 firmware and the MAVLink adapter are untested on hardware.
* Stuck-sensor rule flags 15 identical consecutive readings — raise `STUCK_N` in `brain.py` for coarse-resolution sensors.

## 6. Tests
```bash
cd backend && python -m pytest -q          # 8 tests: physics, twin, what-if, sensor-fault logic, API, WebSocket, ingest
python -m aerotwin.train                   # re-train (~30 s)
python -m aerotwin.evaluate --all --runs 8 # re-measure system-level metrics
```

## 7. API
`GET /api/status` · `POST /api/source {mode}` · `POST /api/ingest` · `WS /ws` (dashboard) · `WS /ws/ingest` · `POST /api/decision {action}` ·
`POST /api/mission` · `GET /api/metrics|history|events|export.csv` · simulator only: `POST /api/sim/{fault|sensor_fault|load|speed|clear|reset}`
