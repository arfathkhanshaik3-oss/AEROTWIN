"""
Shared engine physics used by BOTH the simulator (ground truth) and the digital twin
(its internal model).  The twin's copy can be re-calibrated from real healthy flight logs
(see calibrate.py) without touching the simulator's copy.

Channels:  temp = cylinder-head temperature [degC]
           rpm  = engine speed [rpm]
           vib  = vibration magnitude [g]
           press= oil pressure [bar]
"""
from __future__ import annotations
import json, math
from dataclasses import dataclass, asdict
from pathlib import Path

CH = ["temp", "rpm", "vib", "press"]
FAULTS = ["cool", "bear", "leak"]            # cooling loss, bearing wear, oil leak
FAULT_NAMES = {"cool": "Cooling loss", "bear": "Bearing wear", "leak": "Oil leak"}

WARN_HEALTH = 80.0     # below -> DEGRADING
CRIT_HEALTH = 50.0     # below -> CRITICAL
FAIL_HEALTH = 35.0     # below -> engine considered failed for mission-survival maths


@dataclass
class Phys:
    kq: float = 68.8          # heat-release coefficient
    h0: float = 1.0           # cooling coefficient
    cap: float = 40.0         # thermal capacity -> time constant ~40 s
    heat_exp: float = 1.3     # heat ~ (rpm/3000)^heat_exp
    v_a: float = 0.30         # vibration baseline  v = v_a + v_b*rpm
    v_b: float = 0.000167
    p_k: float = 1.4          # oil pressure p = p_k*(rpm/3120)^p_exp
    p_exp: float = 0.7
    cool_k: float = 0.65      # cooling efficiency lost per unit cooling-fault severity
    bear_vib: float = 1.8     # vibration added per unit bearing-wear severity
    leak_p: float = 0.55      # fractional pressure lost per unit leak severity
    bear_heat: float = 0.18   # extra heat from bearing wear
    leak_heat: float = 0.10   # extra heat from oil leak
    c_vib_leak: float = 0.4   # cross-couplings
    c_vib_cool: float = 0.3
    c_p_cool: float = 0.12
    c_p_bear: float = 0.15

    def to_json(self):
        return json.dumps(asdict(self), indent=2)


SIM_PHYS = Phys()           # simulator ground truth - never recalibrated


def load_twin_phys(model_dir: Path) -> Phys:
    f = Path(model_dir) / "calibration.json"
    if f.exists():
        try:
            d = json.loads(f.read_text())
            return Phys(**{**asdict(Phys()), **d})
        except Exception:
            pass
    return Phys()


# ---------------------------------------------------------------- helper physics
def rpm_target(load: float) -> float:
    return 2000.0 + 2800.0 * load


def load_from_rpm(rpm: float) -> float:
    return min(1.0, max(0.0, (rpm - 2000.0) / 2800.0))


def heat_in(p: Phys, rpm: float, d_bear: float, d_leak: float) -> float:
    r = max(rpm, 1.0) / 3000.0
    return p.kq * (r ** p.heat_exp) * (1 + p.bear_heat * d_bear) * (1 + p.leak_heat * d_leak)


def cool_coef(p: Phys, rpm: float, eta: float) -> float:
    return p.h0 * eta * (0.5 + 0.5 * rpm / 3000.0)


def eta_from_cool(p: Phys, d_cool: float) -> float:
    return 1.0 - p.cool_k * d_cool


def vib_expected(p: Phys, rpm: float) -> float:
    return p.v_a + p.v_b * rpm


def press_expected(p: Phys, rpm: float) -> float:
    return p.p_k * (max(rpm, 1.0) / 3120.0) ** p.p_exp


def temp_expected(p: Phys, rpm: float, amb: float) -> float:
    """Healthy steady-state CHT."""
    return amb + heat_in(p, rpm, 0, 0) / cool_coef(p, rpm, 1.0)


def health_from(dc: float, db: float, dl: float) -> float:
    m = max(dc, db, dl)
    return 100.0 * (1.0 - 0.85 * m - 0.15 * (dc + db + dl) / 3.0)


def growth(load: float) -> float:
    """Fault growth multiplier vs engine stress (load)."""
    return min(4.0, max(0.15, (max(load, 0.0) / 0.4) ** 2))


def status_for(h: float) -> str:
    return "nominal" if h > WARN_HEALTH else ("degrading" if h > CRIT_HEALTH else "critical")
