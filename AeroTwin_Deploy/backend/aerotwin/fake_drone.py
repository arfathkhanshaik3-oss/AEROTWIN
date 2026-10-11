"""
Device emulator: behaves exactly like a real drone/ESP32 posting telemetry to AeroTwin over HTTP.
Use it to test the LIVE path end-to-end without hardware.
    python -m aerotwin.fake_drone --url http://localhost:8000 --fault bear@60
    python -m aerotwin.fake_drone --sensor-fault vib:stuck@40 --seconds 300
"""
import argparse, json, time, urllib.request
from .simulator import EngineSim


def post(url, body, key):
    req = urllib.request.Request(url + "/api/ingest", data=json.dumps(body).encode(), headers={"Content-Type": "application/json", **({"X-API-Key": key} if key else {})})
    with urllib.request.urlopen(req, timeout=5) as r:
        return json.loads(r.read())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://localhost:8000"); ap.add_argument("--key", default="")
    ap.add_argument("--seconds", type=int, default=600); ap.add_argument("--rate", type=float, default=1.0, help="samples per second")
    ap.add_argument("--fault", default="", help="kind@second e.g. bear@60 (cool|bear|leak)")
    ap.add_argument("--ttf", type=float, default=240.0)
    ap.add_argument("--sensor-fault", default="", help="channel:mode@second e.g. vib:stuck@40")
    a = ap.parse_args()
    sim = EngineSim(ambient=26.0); sim.set_load(0.42)
    fk, fs = (a.fault.split("@") + ["0"])[:2] if a.fault else ("", "0")
    sf, ss = (a.sensor_fault.split("@") + ["0"])[:2] if a.sensor_fault else ("", "0")
    n = int(a.seconds * a.rate)
    for i in range(n):
        t = i / a.rate
        if fk and abs(t - float(fs)) < 0.5 / a.rate: sim.inject_fault(fk, a.ttf); print(f"[{t:.0f}s] injected engine fault {fk}")
        if sf and abs(t - float(ss)) < 0.5 / a.rate:
            ch, mode = sf.split(":"); sim.inject_sensor_fault(ch, mode); print(f"[{t:.0f}s] injected sensor fault {sf}")
        sim.dt = 1.0 / a.rate
        r = sim.step()
        body = {c: (None if v != v else round(v, 3)) for c, v in r["meas"].items()}
        body["throttle"] = round(r["throttle"], 3); body["ambient"] = r["ambient"]
        try:
            res = post(a.url, body, a.key)
        except Exception as e:
            print("post failed:", e); time.sleep(1); continue
        if i % 10 == 0: print(f"[{t:.0f}s] sent {body}  -> accepted={res['accepted']} mode={res['mode']}")
        time.sleep(1.0 / a.rate)


if __name__ == "__main__":
    main()
