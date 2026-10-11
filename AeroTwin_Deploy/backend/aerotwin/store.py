"""SQLite logging of every processed frame + operator events (audit trail / data for later retraining)."""
from __future__ import annotations
import json, sqlite3, threading, time
from pathlib import Path


class Store:
    def __init__(self, path: Path):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(path), check_same_thread=False)
        self.lock = threading.Lock()
        self.db.executescript("""
        CREATE TABLE IF NOT EXISTS frames(id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL, source TEXT, health REAL, level TEXT,
            temp REAL, rpm REAL, vib REAL, press REAL, fault TEXT, sensor_fault TEXT);
        CREATE TABLE IF NOT EXISTS events(id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL, kind TEXT, detail TEXT);
        """)
        self.n = 0

    def log_frame(self, f: dict, source: str):
        s = f["sensors"]
        with self.lock:
            self.db.execute("INSERT INTO frames(ts,source,health,level,temp,rpm,vib,press,fault,sensor_fault) VALUES(?,?,?,?,?,?,?,?,?,?)",
                            (f.get("ts") or time.time(), source, f["health"]["fused"], f["alert"]["level"],
                             s["temp"]["value"], s["rpm"]["value"], s["vib"]["value"], s["press"]["value"],
                             f["diagnosis"]["primary_fault"], ",".join(f["diagnosis"]["declared_sensor_faults"])))
            self.n += 1
            if self.n % 20 == 0:
                self.db.commit()

    def event(self, kind: str, detail: dict):
        with self.lock:
            self.db.execute("INSERT INTO events(ts,kind,detail) VALUES(?,?,?)", (time.time(), kind, json.dumps(detail)))
            self.db.commit()

    def history(self, limit=600):
        with self.lock:
            cur = self.db.execute("SELECT ts,source,health,level,temp,rpm,vib,press,fault,sensor_fault FROM frames ORDER BY id DESC LIMIT ?", (limit,))
            rows = cur.fetchall()
        keys = ["ts", "source", "health", "level", "temp", "rpm", "vib", "press", "fault", "sensor_fault"]
        return [dict(zip(keys, r)) for r in rows][::-1]

    def events(self, limit=100):
        with self.lock:
            rows = self.db.execute("SELECT ts,kind,detail FROM events ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        return [{"ts": r[0], "kind": r[1], "detail": json.loads(r[2])} for r in rows]

    def csv(self):
        rows = self.history(10**7)
        keys = ["ts", "source", "health", "level", "temp", "rpm", "vib", "press", "fault", "sensor_fault"]
        yield ",".join(keys) + "\n"
        for r in rows:
            yield ",".join("" if r[k] is None else str(r[k]) for k in keys) + "\n"
