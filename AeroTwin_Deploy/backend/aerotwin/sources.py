"""
Live data adapters.  Anything that can produce {temp, rpm, vib, press[, throttle, ambient]} can feed AeroTwin:
  * HTTP  POST /api/ingest          (ESP32 / Raspberry Pi / ground station)       -> handled in server.py
  * WebSocket /ws/ingest            (same, persistent connection)                  -> handled in server.py
  * Serial  JSON lines or CSV       (Arduino / ESP32 over USB or telemetry radio)  -> SerialSource
  * MAVLink (ArduPilot EFI_STATUS + VIBRATION)                                     -> MavlinkSource
"""
from __future__ import annotations
import json, math, threading, time


class Resampler:
    """Models are trained on 1 Hz data.  Averages whatever rate the device sends into 1 Hz frames."""
    def __init__(self, period=1.0):
        self.period = period
        self.buf = []
        self.last = None

    def push(self, s: dict, now: float | None = None):
        now = time.time() if now is None else now
        if self.last is None:
            self.last = now
        self.buf.append(s)
        if now - self.last >= self.period:
            out = {}
            for k in ("temp", "rpm", "vib", "press", "throttle", "ambient"):
                vals = [b[k] for b in self.buf if b.get(k) is not None and not (isinstance(b[k], float) and math.isnan(b[k]))]
                out[k] = sum(vals) / len(vals) if vals else None
            self.buf = []; self.last = now
            return out
        return None


def parse_line(line: str):
    line = line.strip()
    if not line:
        return None
    try:
        if line.startswith("{"):
            d = json.loads(line)
            return {k: d.get(k) for k in ("temp", "rpm", "vib", "press", "throttle", "ambient")}
        parts = [p.strip() for p in line.split(",")]
        keys = ["temp", "rpm", "vib", "press", "throttle", "ambient"]
        return {k: (float(v) if v not in ("", "nan", "NaN") else None) for k, v in zip(keys, parts)}
    except Exception:
        return None


class SerialSource(threading.Thread):
    def __init__(self, port, baud, cb):
        super().__init__(daemon=True); self.port, self.baud, self.cb = port, baud, cb; self.stop = False; self.status = "starting"

    def run(self):
        try:
            import serial
        except ImportError:
            self.status = "pyserial not installed"; return
        while not self.stop:
            try:
                with serial.Serial(self.port, self.baud, timeout=1) as ser:
                    self.status = f"connected {self.port}"
                    while not self.stop:
                        s = parse_line(ser.readline().decode(errors="ignore"))
                        if s:
                            self.cb(s)
            except Exception as e:
                self.status = f"error: {e}"; time.sleep(2)


class MavlinkSource(threading.Thread):
    """Reads ArduPilot telemetry: EFI_STATUS (rpm, cylinder_head_temperature, intake_manifold_pressure, throttle) and
    VIBRATION.  NOTE: standard MAVLink has no oil-pressure message; intake-manifold pressure (kPa->bar) is used as the
    'press' channel - map your own sensor if you have one.  Not validated on real aircraft."""
    def __init__(self, conn, cb):
        super().__init__(daemon=True); self.conn, self.cb = conn, cb; self.stop = False; self.status = "starting"
        self.vib = None

    def run(self):
        try:
            from pymavlink import mavutil
        except ImportError:
            self.status = "pymavlink not installed"; return
        while not self.stop:
            try:
                m = mavutil.mavlink_connection(self.conn)
                self.status = f"waiting heartbeat on {self.conn}"
                m.wait_heartbeat(timeout=10)
                self.status = "connected"
                while not self.stop:
                    msg = m.recv_match(type=["EFI_STATUS", "VIBRATION"], blocking=True, timeout=2)
                    if msg is None:
                        continue
                    if msg.get_type() == "VIBRATION":
                        self.vib = math.sqrt(msg.vibration_x ** 2 + msg.vibration_y ** 2 + msg.vibration_z ** 2) / 9.81
                    else:
                        self.cb({"temp": float(msg.cylinder_head_temperature), "rpm": float(msg.rpm), "vib": self.vib,
                                 "press": float(msg.intake_manifold_pressure) / 100.0,
                                 "throttle": min(1.0, max(0.0, float(msg.throttle_position) / 100.0)), "ambient": None})
            except Exception as e:
                self.status = f"error: {e}"; time.sleep(3)
