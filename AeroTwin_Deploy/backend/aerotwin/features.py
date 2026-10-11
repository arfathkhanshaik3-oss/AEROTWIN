"""Vectorised feature extraction shared by training and live inference (so they can never drift apart)."""
from __future__ import annotations
import numpy as np
from .physics import Phys, CH

W = 30            # window length [samples @ 1 Hz]
H_FORECAST = 120  # ML forecast horizon [samples]
SIGMA = np.array([0.6, 8.0, 0.025, 0.015])     # nominal healthy sensor noise (temp,rpm,vib,press)


def _slope(Xw):
    t = np.arange(Xw.shape[1], dtype=float); t -= t.mean()
    return (Xw * t[None, :, None]).sum(1) / (t ** 2).sum()


def phys_resid(Xw, amb, p: Phys):
    """Residuals of the last 10 samples vs the healthy physics envelope. Xw:(N,W,4) amb:(N,)"""
    m = Xw[:, -10:, :].mean(1)
    rpm = np.maximum(m[:, 1], 1.0)
    r_vib = m[:, 2] - (p.v_a + p.v_b * rpm)
    r_press = m[:, 3] / (p.p_k * (rpm / 3120.0) ** p.p_exp) - 1.0
    q = p.kq * (rpm / 3000.0) ** p.heat_exp
    r_temp = m[:, 0] - (amb + q / (p.h0 * (0.5 + 0.5 * rpm / 3000.0)))
    return np.stack([r_temp, r_vib, r_press], 1)


def health_features(Xw, amb, p: Phys):
    last = Xw[:, -1, :]
    mean = Xw.mean(1)
    std = Xw.std(1)
    slope = _slope(Xw)
    res = phys_resid(Xw, amb, p)
    return np.concatenate([last, mean, std, slope, res, amb[:, None]], 1)


def anomaly_features(Xw, amb, p: Phys):
    sl = _slope(Xw)
    res = phys_resid(Xw, amb, p)
    return np.concatenate([res, sl[:, [0, 2, 3]] * 30.0], 1)


def sensor_features(Xw, Zw):
    """Xw raw window (N,W,4), Zw cross-prediction z-scores (N,W,4)."""
    N = Xw.shape[0]
    z10 = Zw[:, -10:, :].mean(1)
    z3 = Zw[:, -3:, :].mean(1)
    zabs = np.abs(Zw).max(1)
    zstd = Zw.std(1)
    ptp12 = (Xw[:, -12:, :].max(1) - Xw[:, -12:, :].min(1)) / SIGMA[None, :]
    jump = np.abs(np.diff(Xw, axis=1)).max(1) / SIGMA[None, :]
    zsl = _slope(Zw) * W
    a = np.sort(np.abs(z10), axis=1)[:, ::-1]
    nflag = (np.abs(z10) > 3.0).sum(1, keepdims=True)
    return np.concatenate([z10, z3, zabs, zstd, ptp12, jump, zsl, a[:, :2], nflag], 1)


def windows(arr, w=W):
    """arr (T,k) -> (T-w+1, w, k) sliding windows (view)."""
    return np.lib.stride_tricks.sliding_window_view(arr, w, axis=0).transpose(0, 2, 1)
