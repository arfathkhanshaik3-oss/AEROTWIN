/* AeroTwin dashboard — everything shown here comes from the backend over WebSocket (/ws). */
(function () {
  "use strict";
  var $ = function (id) { return document.getElementById(id); };
  var qa = function (s, r) { return Array.prototype.slice.call((r || document).querySelectorAll(s)); };
  var clamp = function (x, a, b) { return Math.max(a, Math.min(b, x)); };
  var fmt = function (x, d) { return (x === null || x === undefined || !isFinite(x)) ? "—" : Number(x).toFixed(d === undefined ? 1 : d); };
  var fmtT = function (s) {
    if (s === null || s === undefined) return "not within horizon";
    s = Math.round(s); if (s < 60) return s + " s"; return Math.floor(s / 60) + " m " + (s % 60) + " s";
  };
  var COL = { nominal: "#2ee59d", degrading: "#ffb020", critical: "#ff3b3b", cyan: "#00d4ff" };
  var NAMES = { cool: "Cooling loss", bear: "Bearing wear", leak: "Oil leak" };

  var S = { frame: null, hist: [], lastMsg: 0, mode: "sim", sound: false, tab: "cmd", level: "nominal", dirty: false, status: null, lastDiag: 0, ws: null };
  var needles = {};   // animated gauge needles

  // =================================================================== REST helper
  function api(path, body, method) {
    var o = { method: method || (body === undefined ? "GET" : "POST"), headers: { "Content-Type": "application/json" } };
    if (body !== undefined) o.body = JSON.stringify(body);
    return fetch(path, o).then(function (r) { return r.json().catch(function () { return {}; }).then(function (j) { j._status = r.status; return j; }); });
  }

  // =================================================================== analog dial (real speedometer look)
  function drawDial(cv, o, t) {
    var ctx = cv.getContext("2d"), W = cv.width, H = cv.height;
    ctx.clearRect(0, 0, W, H);
    var cx = W / 2, cy = H * 0.55, R = Math.min(W, H * 1.05) * 0.42;
    var a0 = 135 * Math.PI / 180, a1 = 405 * Math.PI / 180;
    var ang = function (v) { return a0 + (a1 - a0) * clamp((v - o.min) / (o.max - o.min), 0, 1); };
    // chrome bezel
    var g = ctx.createRadialGradient(cx - R * 0.3, cy - R * 0.4, R * 0.2, cx, cy, R * 1.2);
    g.addColorStop(0, "#5b7391"); g.addColorStop(0.55, "#1b2d46"); g.addColorStop(1, "#070e1a");
    ctx.beginPath(); ctx.arc(cx, cy, R * 1.15, 0, Math.PI * 2); ctx.fillStyle = g; ctx.fill();
    ctx.beginPath(); ctx.arc(cx, cy, R * 1.08, 0, Math.PI * 2); ctx.fillStyle = "#03070d"; ctx.fill();
    // dial face
    var f = ctx.createRadialGradient(cx, cy - R * 0.2, R * 0.1, cx, cy, R * 1.05);
    f.addColorStop(0, "#10223a"); f.addColorStop(1, "#050b14");
    ctx.beginPath(); ctx.arc(cx, cy, R * 1.03, 0, Math.PI * 2); ctx.fillStyle = f; ctx.fill();
    // colour bands
    (o.bands || []).forEach(function (b) {
      ctx.beginPath(); ctx.arc(cx, cy, R * 0.9, ang(b.a), ang(b.b)); ctx.lineWidth = R * 0.085; ctx.lineCap = "butt";
      ctx.strokeStyle = b.color; ctx.globalAlpha = 0.92; ctx.stroke(); ctx.globalAlpha = 1;
    });
    // ticks + numbers
    var minor = o.minor || (o.max - o.min) / 50, major = o.major || (o.max - o.min) / 10, labEvery = o.labEvery || major;
    var nmin = Math.round((o.max - o.min) / minor);
    for (var i = 0; i <= nmin; i++) {
      var v = o.min + i * minor, a = ang(v);
      var isMaj = Math.abs((v - o.min) / major - Math.round((v - o.min) / major)) < 1e-6;
      var r1 = R * 0.80, r2 = isMaj ? R * 0.68 : R * 0.745;
      ctx.beginPath(); ctx.moveTo(cx + Math.cos(a) * r1, cy + Math.sin(a) * r1); ctx.lineTo(cx + Math.cos(a) * r2, cy + Math.sin(a) * r2);
      ctx.strokeStyle = isMaj ? "#e8f1fb" : "#5f7a9e"; ctx.lineWidth = isMaj ? R * 0.028 : R * 0.014; ctx.stroke();
      if (isMaj && Math.abs((v - o.min) / labEvery - Math.round((v - o.min) / labEvery)) < 1e-6) {
        ctx.fillStyle = "#cfe2f7"; ctx.font = "600 " + Math.round(R * 0.115) + "px Consolas, monospace"; ctx.textAlign = "center"; ctx.textBaseline = "middle";
        ctx.fillText(o.labFmt ? o.labFmt(v) : String(Math.round(v)), cx + Math.cos(a) * R * 0.55, cy + Math.sin(a) * R * 0.55);
      }
    }
    // labels
    ctx.textAlign = "center"; ctx.textBaseline = "alphabetic";
    ctx.fillStyle = "#8ca3c4"; ctx.font = "600 " + Math.round(R * 0.1) + "px Consolas, monospace";
    ctx.fillText(o.unit || "", cx, cy + R * 0.30);
    ctx.fillStyle = o.valColor || "#ffffff"; ctx.font = "700 " + Math.round(R * (o.big ? 0.30 : 0.22)) + "px Consolas, monospace";
    ctx.fillText(o.text !== undefined ? o.text : "", cx, cy + R * (o.big ? 0.66 : 0.60));
    // needle (with counter-weight tail + drop shadow)
    var na = ang(o.value + (o.tremble ? Math.sin(t * 0.05) * o.tremble : 0));
    ctx.save(); ctx.translate(cx, cy); ctx.rotate(na);
    ctx.shadowColor = "rgba(0,0,0,.7)"; ctx.shadowBlur = R * 0.06; ctx.shadowOffsetY = R * 0.03;
    ctx.beginPath(); ctx.moveTo(R * 0.84, 0); ctx.lineTo(0, -R * 0.034); ctx.lineTo(-R * 0.2, -R * 0.05); ctx.lineTo(-R * 0.2, R * 0.05); ctx.lineTo(0, R * 0.034); ctx.closePath();
    var ng = ctx.createLinearGradient(0, 0, R * 0.84, 0); ng.addColorStop(0, "#ff8a8a"); ng.addColorStop(1, "#ff2b2b");
    ctx.fillStyle = ng; ctx.fill(); ctx.restore();
    ctx.beginPath(); ctx.arc(cx, cy, R * 0.095, 0, Math.PI * 2);
    var hg = ctx.createRadialGradient(cx - R * 0.03, cy - R * 0.03, 1, cx, cy, R * 0.1); hg.addColorStop(0, "#dfe9f5"); hg.addColorStop(1, "#33445c");
    ctx.fillStyle = hg; ctx.fill();
    // glass reflection
    var gl = ctx.createLinearGradient(cx, cy - R * 1.05, cx, cy);
    gl.addColorStop(0, "rgba(255,255,255,.17)"); gl.addColorStop(1, "rgba(255,255,255,0)");
    ctx.beginPath(); ctx.ellipse(cx, cy - R * 0.45, R * 0.82, R * 0.5, 0, Math.PI, 0); ctx.fillStyle = gl; ctx.fill();
  }

  var DIALS = {
    temp: { min: 40, max: 200, minor: 4, major: 20, labEvery: 40, unit: "°C", dec: 0, bands: [{ a: 40, b: 110, color: "#2ee59d" }, { a: 110, b: 135, color: "#ffb020" }, { a: 135, b: 200, color: "#ff3b3b" }] },
    rpm: { min: 0, max: 6000, minor: 120, major: 1000, labEvery: 1000, unit: "RPM", dec: 0, labFmt: function (v) { return (v / 1000).toFixed(0); }, bands: [{ a: 0, b: 1500, color: "#ffb020" }, { a: 1500, b: 4900, color: "#2ee59d" }, { a: 4900, b: 6000, color: "#ff3b3b" }] },
    vib: { min: 0, max: 3, minor: 0.06, major: 0.5, labEvery: 0.5, unit: "g", dec: 2, labFmt: function (v) { return v.toFixed(1); }, bands: [{ a: 0, b: 1.1, color: "#2ee59d" }, { a: 1.1, b: 1.6, color: "#ffb020" }, { a: 1.6, b: 3, color: "#ff3b3b" }] },
    press: { min: 0, max: 3, minor: 0.06, major: 0.5, labEvery: 0.5, unit: "bar", dec: 2, labFmt: function (v) { return v.toFixed(1); }, bands: [{ a: 0, b: 0.8, color: "#ff3b3b" }, { a: 0.8, b: 1.0, color: "#ffb020" }, { a: 1.0, b: 3, color: "#2ee59d" }] }
  };
  var HEALTH = { min: 0, max: 100, minor: 2, major: 10, labEvery: 20, unit: "TWIN HEALTH INDEX", big: true, bands: [{ a: 0, b: 50, color: "#ff3b3b" }, { a: 50, b: 80, color: "#ffb020" }, { a: 80, b: 100, color: "#2ee59d" }] };

  function animate(now) {
    // needle springs
    var f = S.frame;
    var tgt = { health: f ? f.health.fused : 0 };
    ["temp", "rpm", "vib", "press"].forEach(function (c) { tgt[c] = f ? f.sensors[c].used : DIALS[c].min; });
    Object.keys(tgt).forEach(function (k) {
      var n = needles[k] || (needles[k] = { v: k === "health" ? 0 : (DIALS[k].min), vel: 0 });
      var rng = k === "health" ? 100 : (DIALS[k].max - DIALS[k].min);
      var d = tgt[k] - n.v; n.vel = n.vel * 0.72 + d * 0.085; n.v += n.vel;
      if (!isFinite(n.v)) n.v = tgt[k] || 0;
    });
    var crit = S.level === "critical";
    var hv = needles.health ? needles.health.v : 0;
    drawDial($("gHealth"), Object.assign({}, HEALTH, { value: hv, text: f ? fmt(f.health.fused, 0) : "—", valColor: COL[S.level], tremble: crit ? 1.2 : 0 }), now);
    ["temp", "rpm", "vib", "press"].forEach(function (c) {
      var cv = $("dl-" + c).querySelector("canvas"), d = DIALS[c];
      var val = f ? f.sensors[c].value : null;
      drawDial(cv, Object.assign({}, d, { value: needles[c].v, text: val === null || val === undefined ? "N/A" : fmt(val, d.dec) }), now);
    });
    if (S.dirty && now - (S._lastPanels || 0) > 250) { S._lastPanels = now; S.dirty = false; renderPanels(); }
    requestAnimationFrame(animate);
  }

  // =================================================================== charts
  function prep(cv) {
    var dpr = Math.min(window.devicePixelRatio || 1, 2), w = cv.clientWidth, h = cv.clientHeight;
    if (w < 10 || h < 10) return null;
    if (cv.width !== Math.round(w * dpr) || cv.height !== Math.round(h * dpr)) { cv.width = Math.round(w * dpr); cv.height = Math.round(h * dpr); }
    var ctx = cv.getContext("2d"); ctx.setTransform(dpr, 0, 0, dpr, 0, 0); ctx.clearRect(0, 0, w, h);
    return { ctx: ctx, w: w, h: h };
  }
  function bandsBg(ctx, x0, y0, w, h) {
    var y = function (v) { return y0 + h * (1 - v / 100); };
    [[80, 100, "rgba(46,229,157,.07)"], [50, 80, "rgba(255,176,32,.08)"], [0, 50, "rgba(255,59,59,.09)"]].forEach(function (b) {
      ctx.fillStyle = b[2]; ctx.fillRect(x0, y(b[1]), w, y(b[0]) - y(b[1]));
    });
    ctx.strokeStyle = "#1c3454"; ctx.lineWidth = 1; ctx.fillStyle = "#5f7a9e"; ctx.font = "10px Consolas, monospace"; ctx.textAlign = "right";
    [0, 20, 35, 50, 80, 100].forEach(function (v) {
      ctx.beginPath(); ctx.moveTo(x0, y(v)); ctx.lineTo(x0 + w, y(v)); ctx.setLineDash(v === 35 || v === 50 || v === 80 ? [4, 4] : []); ctx.stroke(); ctx.setLineDash([]);
      ctx.fillText(String(v), x0 - 6, y(v) + 3);
    });
  }
  function drawSpark() {
    var p = prep($("cSpark")); if (!p) return;
    var ctx = p.ctx, x0 = 34, y0 = 8, w = p.w - 44, h = p.h - 22, hist = S.hist.slice(-600);
    bandsBg(ctx, x0, y0, w, h);
    if (hist.length < 2) return;
    ctx.beginPath();
    hist.forEach(function (q, i) { var x = x0 + w * i / (hist.length - 1), y = y0 + h * (1 - q.h / 100); if (i) ctx.lineTo(x, y); else ctx.moveTo(x, y); });
    ctx.strokeStyle = COL.cyan; ctx.lineWidth = 2; ctx.stroke();
  }
  function drawForecast() {
    var p = prep($("cFore")); if (!p) return;
    var f = S.frame, ctx = p.ctx, x0 = 38, y0 = 10, w = p.w - 54, h = p.h - 34;
    bandsBg(ctx, x0, y0, w, h);
    var past = S.hist.slice(-300), wi = f && f.whatif;
    var horizon = wi ? Math.max(wi.mission_s, wi.return_s) : 300, tmin = -Math.min(300, Math.max(past.length - 1, 60)), tmax = horizon;
    var X = function (t) { return x0 + w * (t - tmin) / (tmax - tmin); }, Y = function (v) { return y0 + h * (1 - v / 100); };
    ctx.fillStyle = "#5f7a9e"; ctx.font = "10px Consolas, monospace"; ctx.textAlign = "center";
    for (var t = Math.ceil(tmin / 100) * 100; t <= tmax; t += 100) { ctx.fillText((t > 0 ? "+" : "") + t + "s", X(t), y0 + h + 14); ctx.strokeStyle = "rgba(28,52,84,.5)"; ctx.beginPath(); ctx.moveTo(X(t), y0); ctx.lineTo(X(t), y0 + h); ctx.stroke(); }
    if (past.length > 1) {
      ctx.beginPath();
      past.forEach(function (q, i) { var tt = -(past.length - 1 - i); var x = X(tt), y = Y(q.h); if (i) ctx.lineTo(x, y); else ctx.moveTo(x, y); });
      ctx.strokeStyle = COL.cyan; ctx.lineWidth = 2.5; ctx.stroke();
    }
    ctx.strokeStyle = "#8ca3c4"; ctx.setLineDash([3, 3]); ctx.beginPath(); ctx.moveTo(X(0), y0); ctx.lineTo(X(0), y0 + h); ctx.stroke(); ctx.setLineDash([]);
    ctx.fillStyle = "#8ca3c4"; ctx.textAlign = "left"; ctx.fillText("NOW", X(0) + 4, y0 + 10);
    if (!wi) return;
    var grid = wi.grid_s, sc = wi.scenarios, cur = f.health.fused;
    var band = function (lo, hi, color) {
      ctx.beginPath(); var started = false;
      for (var i = 0; i < grid.length; i++) { if (hi[i] === null) continue; var x = X(grid[i]), y = Y(hi[i]); if (!started) { ctx.moveTo(x, y); started = true; } else ctx.lineTo(x, y); }
      for (var j = grid.length - 1; j >= 0; j--) { if (lo[j] === null) continue; ctx.lineTo(X(grid[j]), Y(lo[j])); }
      ctx.closePath(); ctx.fillStyle = color; ctx.fill();
    };
    band(sc.continue.traj_p10, sc.continue.traj_p90, "rgba(255,90,60,.16)");
    var line = function (arr, color) {
      ctx.beginPath(); var st = false;
      ctx.moveTo(X(0), Y(cur)); st = true;
      for (var i = 0; i < grid.length; i++) { if (arr[i] === null) continue; ctx.lineTo(X(grid[i]), Y(arr[i])); }
      ctx.strokeStyle = color; ctx.lineWidth = 2.2; ctx.setLineDash([7, 5]); ctx.stroke(); ctx.setLineDash([]);
    };
    line(sc.continue.traj_median, "#ff5a3c"); line(sc.modify.traj_median, "#ffb020"); line(sc.return.traj_median, "#2ee59d");
    // ML +120 s marker
    var mx = X(120), my = Y(f.health.forecast_ml_120s);
    ctx.fillStyle = "#fff"; ctx.beginPath(); ctx.moveTo(mx, my - 7); ctx.lineTo(mx + 7, my); ctx.lineTo(mx, my + 7); ctx.lineTo(mx - 7, my); ctx.closePath(); ctx.fill();
    ctx.fillStyle = "#fff"; ctx.textAlign = "left"; ctx.fillText("ML +120s", mx + 10, my + 3);
    // legend
    var lg = [["measured", COL.cyan], ["continue", "#ff5a3c"], ["modify", "#ffb020"], ["return", "#2ee59d"]];
    lg.forEach(function (l, i) { ctx.fillStyle = l[1]; ctx.fillRect(x0 + 10 + i * 100, y0 + h - 16, 14, 4); ctx.fillStyle = "#cfe2f7"; ctx.textAlign = "left"; ctx.fillText(l[0], x0 + 30 + i * 100, y0 + h - 11); });
  }

  // =================================================================== panels
  function setLevel(level) {
    if (level === S.level && document.body.classList.contains("lvl-" + level)) return;
    S.level = level;
    ["nominal", "degrading", "critical"].forEach(function (l) { document.body.classList.toggle("lvl-" + l, l === level); $("dronePanel").classList.toggle("lvl-" + l, l === level); });
  }

  function applyMode(mode) {
    S.mode = mode; var live = mode === "live";
    $("srcBadge").textContent = live ? "LIVE DEVICE" : "SIMULATOR"; $("srcBadge").className = "badge " + (live ? "live" : "sim");
    $("btnMode").textContent = live ? "SWITCH TO SIMULATOR" : "SWITCH TO LIVE DEVICE";
    $("harness").style.display = live ? "none" : "block";
  }

  function onFrame(f) {
    S.frame = f; S.lastMsg = performance.now(); S.mode = f.mode || S.mode;
    S.hist.push({ t: f.t, h: f.health.fused }); if (S.hist.length > 900) S.hist.shift();
    setLevel(f.alert.level);
    var s = f.sensors;
    if (window.droneView) window.droneView.rig.setState({ rpm: s.rpm.used, temp: s.temp.used, vib: s.vib.used, level: f.alert.level, health: f.health.fused, sensorIssue: f.alert.sensor_issue });
    S.dirty = true;
  }

  function renderPanels() {
    var f = S.frame; if (!f) return;
    var lvl = f.alert.level, rec = f.whatif && f.whatif.recommendation;
    // status pill + alert bar
    var pill = $("stPill"); pill.textContent = f.warming_up ? "CALIBRATING…" : lvl.toUpperCase(); pill.className = "status-pill st-" + lvl;
    var bar = $("alertbar");
    if (lvl === "critical") bar.textContent = "⚠ CRITICAL ENGINE CONDITION — " + (rec ? rec.label.toUpperCase() + " RECOMMENDED" : "");
    else if (lvl === "degrading") bar.textContent = "ENGINE DEGRADING — " + (f.alert.reasons[0] || "check diagnostics");
    // kv
    $("kFused").textContent = fmt(f.health.fused) + " %"; $("kMl").textContent = fmt(f.health.ml) + " %"; $("kTw").textContent = fmt(f.health.twin) + " %";
    $("kF").textContent = fmt(f.health.forecast_ml_120s) + " %"; $("kA").textContent = fmt(f.health.anomaly, 2); $("kC").textContent = fmt(100 * f.health.confidence, 0) + " %";
    var rs = $("reasons"); rs.innerHTML = "";
    f.alert.reasons.forEach(function (r) { var li = document.createElement("li"); li.textContent = r; rs.appendChild(li); });
    if (!f.alert.reasons.length) { var li0 = document.createElement("li"); li0.textContent = "All monitored parameters consistent with healthy operation."; rs.appendChild(li0); }
    // recommendation
    var box = $("recBox");
    if (rec) {
      $("recAction").textContent = rec.label.toUpperCase(); $("recReason").textContent = rec.reason;
      $("recConf").textContent = fmt(100 * rec.confidence, 0) + " %"; $("recConfBar").style.width = (100 * rec.confidence) + "%";
      box.classList.toggle("emergency", !!rec.emergency);
    } else { $("recAction").textContent = "—"; $("recReason").textContent = "Collecting " + (30 - Math.min(30, f.t)) + " s of data…"; }
    // sensor dials state
    ["temp", "rpm", "vib", "press"].forEach(function (c) {
      var d = $("dl-" + c), st = f.sensors[c].state;
      d.classList.toggle("bad", st !== "ok"); d.querySelector(".st").textContent = st.toUpperCase();
    });
    // drone HUD
    $("droneState").innerHTML = '<span style="color:' + COL[lvl] + '">● ' + lvl.toUpperCase() + "</span>";
    $("droneHud").innerHTML = "ROTOR <b>" + fmt(f.sensors.rpm.used, 0) + "</b> rpm<br>CHT <b>" + fmt(f.sensors.temp.used, 0) + "</b> °C &nbsp; VIB <b>" + fmt(f.sensors.vib.used, 2) + "</b> g";
    // header
    var live = (f.mode === "live");
    $("srcBadge").textContent = live ? "LIVE DEVICE" : "SIMULATOR"; $("srcBadge").className = "badge " + (live ? "live" : "sim");
    $("btnMode").textContent = live ? "SWITCH TO SIMULATOR" : "SWITCH TO LIVE DEVICE";
    $("harness").style.display = live ? "none" : "block";
    if (!S.advMsgAt || performance.now() - S.advMsgAt > 7000)
      $("advNote").textContent = live ? "Live mode: advisory only — AeroTwin never commands the aircraft." : "Simulator mode: applying an action changes the simulated engine's load.";

    if (S.tab === "cmd") drawSpark();
    if (S.tab === "fore") renderForecast(f);
    if (S.tab === "wi") renderWhatIf(f);
    if (S.tab === "diag") renderDiag(f);
  }

  function renderForecast(f) {
    drawForecast(); var w = f.whatif;
    $("etaDeg").textContent = w ? fmtT(w.eta_degraded_s) : "—"; $("etaCrit").textContent = w ? fmtT(w.eta_critical_s) : "—"; $("etaFail").textContent = w ? fmtT(w.eta_failure_s) : "—";
    $("mlf").textContent = fmt(f.health.forecast_ml_120s) + " %";
  }

  function renderWhatIf(f) {
    var w = f.whatif, host = $("wiCards"); if (!w) { host.innerHTML = '<div class="note">Calibrating…</div>'; return; }
    var html = "";
    ["continue", "modify", "return"].forEach(function (k) {
      var s = w.scenarios[k], best = w.recommendation.action === k;
      var col = s.health_end > 80 ? COL.nominal : (s.health_end > 50 ? COL.degrading : COL.critical);
      html += '<div class="card' + (best ? " best" : "") + '"><h4>' + s.label.toUpperCase() + (best ? ' <span class="pill ok">RECOMMENDED</span>' : "") + "</h4>" +
        '<div class="note">Predicted health at end of exposure (' + fmtT(s.exposure_s) + ")</div>" +
        '<div class="num" style="color:' + col + '">' + fmt(s.health_end, 0) + '%</div><div class="note mono">p10–p90: ' + fmt(s.health_end_p10, 0) + "–" + fmt(s.health_end_p90, 0) + "%</div>" +
        '<div style="margin:10px 0"><span class="risk ' + s.risk + '">RISK ' + s.risk + '</span></div>' +
        '<div class="kv"><span>Survival probability</span><span>' + fmt(100 * s.p_survive, 0) + ' %</span><span>Engine load</span><span>' + fmt(100 * s.load, 0) + ' %</span><span>Mission value kept</span><span>' + fmt(100 * s.mission_value, 0) + ' %</span><span>Utility</span><span>' + fmt(s.utility, 2) + "</span></div>" +
        '<div style="margin-top:10px"><button class="btn' + (best ? " pri" : "") + '" data-act="' + k + '">APPLY ' + s.label.toUpperCase() + "</button></div></div>";
    });
    host.innerHTML = html;
    var r = w.recommendation; $("wiAct").textContent = r.label.toUpperCase() + "  (" + fmt(100 * r.confidence, 0) + "% of simulations agree)"; $("wiWhy").textContent = r.reason;
    $("wiRec").classList.toggle("emergency", !!r.emergency);
    bindActs($("wiCards"));
  }

  function renderDiag(f) {
    var rows = "";
    ["temp", "rpm", "vib", "press"].forEach(function (c) {
      var s = f.sensors[c], cls = s.state === "ok" ? "ok" : "bad";
      rows += "<tr><td>" + c.toUpperCase() + '</td><td class="r">' + fmt(s.value, 2) + '</td><td class="r">' + fmt(s.used, 2) + '</td><td class="r">' + fmt(s.z, 1) + '</td><td class="r">' + fmt(100 * s.confidence, 0) + '%</td><td><span class="pill ' + cls + '">' + s.state.toUpperCase() + "</span></td></tr>";
    });
    $("tblSensors").innerHTML = rows;
    var d = f.diagnosis, pr = '<div class="note mono" style="margin-bottom:6px">CLASSIFIER OUTPUT</div>';
    var bar = function (lab, v, color) { return '<div style="display:flex;align-items:center;gap:8px;margin:4px 0"><span class="note mono" style="width:150px">' + lab + '</span><div class="bar" style="flex:1"><i style="width:' + (100 * v) + "%;background:" + color + '"></i></div><span class="mono" style="width:44px;text-align:right">' + fmt(100 * v, 0) + "%</span></div>"; };
    pr += bar("ENGINE FAULT", d.engine_fault_prob, COL.critical);
    ["temp", "rpm", "vib", "press"].forEach(function (c) { pr += bar("SENSOR FAULT: " + c, d.sensor_probs[c] || 0, COL.degrading); });
    $("probs").innerHTML = pr;
    var tw = f.twin, kv = "";
    var add = function (a, b) { kv += "<span>" + a + "</span><span>" + b + "</span>"; };
    add("Cooling efficiency", fmt(tw.cooling_efficiency_pct) + " %"); add("Thermal-model CHT", fmt(tw.T_hat) + " °C");
    Object.keys(tw.severity).forEach(function (k) { add(NAMES[k] + " severity", fmt(100 * tw.severity[k], 0) + " %  (growth " + fmt(100 * tw.rates_per_min[k], 1) + " %/min)"); });
    add("Estimated engine load", fmt(100 * f.load_est, 0) + " %"); add("Ambient", fmt(f.ambient) + " °C");
    add("Channels in twin", Object.keys(tw.channels_used).filter(function (k) { return tw.channels_used[k]; }).join(", ") || "none");
    if (f.sim_truth) { add("— SIM ground truth —", ""); add("True health", fmt(f.sim_truth.health) + " %"); add("True severity c/b/l", ["cool", "bear", "leak"].map(function (k) { return fmt(100 * f.sim_truth.d[k], 0); }).join(" / ") + " %"); }
    $("twinKv").innerHTML = kv;
    if (performance.now() - S.lastDiag > 4000) {
      S.lastDiag = performance.now();
      api("/api/metrics").then(function (m) {
        var out = { trained_on: m.trained_on, window_level: m.window_level, system_level: m.system_level };
        $("metrics").textContent = JSON.stringify(out, function (k, v) { return k.indexOf("confusion") === 0 ? undefined : v; }, 1);
      });
      api("/api/events?limit=12").then(function (ev) {
        if (!ev.map) return;
        $("events").innerHTML = ev.map(function (e) { return "<tr><td class='mono'>" + new Date(e.ts * 1000).toLocaleTimeString() + "</td><td>" + e.kind + "</td><td class='mono'>" + JSON.stringify(e.detail) + "</td></tr>"; }).join("");
      });
    }
  }

  function renderConn() {
    api("/api/status").then(function (st) {
      if (!st || st.mode === undefined) return;
      S.status = st;
      var kv = "", add = function (a, b) { kv += "<span>" + a + "</span><span>" + b + "</span>"; };
      add("Data source", st.mode === "live" ? "LIVE DEVICE" : "SIMULATOR"); add("Live input", st.live_input); add("Adapter", st.live_adapter_status || "HTTP/WebSocket (passive)");
      add("Last device sample", st.seconds_since_last_device_sample === null ? "never" : st.seconds_since_last_device_sample + " s ago"); add("Samples received", st.device_samples_received);
      add("API key required", st.api_key_required ? "yes" : "no"); add("Models", st.models); add("Frames logged", st.frames_logged);
      $("connKv").innerHTML = kv;
      var o = location.origin;
      $("curl").textContent = "curl -X POST " + o + "/api/ingest \\\n  -H 'Content-Type: application/json' " + (st.api_key_required ? "-H 'X-API-Key: <key>' " : "") + "\\\n  -d '{\"temp\":96,\"rpm\":3100,\"vib\":0.82,\"press\":1.4}'";
      $("wsurl").textContent = o.replace("http", "ws") + "/ws/ingest" + (st.api_key_required ? "?key=<key>" : "") + '\n{"temp":96,"rpm":3100,"vib":0.82,"press":1.4}';
    });
  }

  // =================================================================== wiring
  function bindActs(root) {
    qa("[data-act]", root).forEach(function (b) {
      b.onclick = function () {
        var a = b.getAttribute("data-act");
        api("/api/decision", { action: a }).then(function (r) {
          if (window.droneView) window.droneView.rig.doManeuver(a === "continue" ? "none" : a);
          S.advMsgAt = performance.now();
          $("advNote").textContent = r.applied ? ("Applied to simulated engine: " + a.toUpperCase()) : (r.note || "Advisory only.");
        });
      };
    });
  }

  function bind() {
    qa(".tab").forEach(function (t) {
      t.onclick = function () {
        qa(".tab").forEach(function (x) { x.classList.toggle("on", x === t); });
        S.tab = t.getAttribute("data-tab");
        qa(".view").forEach(function (v) { v.classList.toggle("on", v.id === "v-" + S.tab); });
        if (S.tab === "conn") renderConn();
        S.dirty = true; S.lastDiag = 0;
      };
    });
    bindActs(document);
    var setSrc = function (mode) { return api("/api/source", { mode: mode }).then(function (r) { S.hist = []; S.frame = null; S.lastMsg = 0; applyMode(r.mode || mode); setLevel("nominal"); renderConn(); }); };
    $("btnMode").onclick = function () { setSrc(S.mode === "live" ? "sim" : "live"); };
    $("btnLive").onclick = function () { setSrc("live"); };
    $("btnSim").onclick = function () { setSrc("sim"); };
    $("btnSound").onclick = function () {
      S.sound = !S.sound; $("btnSound").textContent = "ALARM SOUND: " + (S.sound ? "ON" : "OFF"); $("btnSound").classList.toggle("on", S.sound);
      if (S.sound && !S.audio) { try { S.audio = new (window.AudioContext || window.webkitAudioContext)(); } catch (e) { S.audio = null; } }
    };
    setInterval(function () {
      if (!S.sound || !S.audio || S.level !== "critical") return;
      var o = S.audio.createOscillator(), g = S.audio.createGain(); o.type = "square"; o.frequency.value = 880; g.gain.value = 0.06;
      o.connect(g); g.connect(S.audio.destination); o.start(); o.stop(S.audio.currentTime + 0.22);
    }, 1000);
    qa("[data-fault]").forEach(function (b) { b.onclick = function () { api("/api/sim/fault", { kind: b.getAttribute("data-fault"), ttf_s: parseFloat($("ttf").value) }); }; });
    $("btnSF").onclick = function () { api("/api/sim/sensor_fault", { channel: $("sfCh").value, mode: $("sfMode").value }); };
    $("btnClear").onclick = function () { api("/api/sim/clear", {}); };
    $("btnReset").onclick = function () {
      api("/api/sim/reset", {}).then(function () { S.hist = []; if (window.droneView) { window.droneView.rig.doManeuver("reset"); } $("load").value = 0.4; $("loadV").textContent = "auto"; });
    };
    $("load").oninput = function () { $("loadV").textContent = fmt(100 * parseFloat($("load").value), 0) + " %"; api("/api/sim/load", { load: parseFloat($("load").value) }); };
    $("btnLoadAuto").onclick = function () { $("loadV").textContent = "auto"; api("/api/sim/load", { load: null }); };
    qa("[data-speed]").forEach(function (b) { b.onclick = function () { api("/api/sim/speed", { speed: parseFloat(b.getAttribute("data-speed")) }); }; });
    $("btnMission").onclick = function () { api("/api/mission", { remaining_s: parseFloat($("inMission").value), return_s: parseFloat($("inReturn").value) }); };
    // airframe controls
    var rig = function () { return window.droneView && window.droneView.rig; };
    $("bMale").onclick = function () { if (rig()) rig().setType("male"); $("bMale").classList.add("on"); $("bQuad").classList.remove("on"); };
    $("bQuad").onclick = function () { if (rig()) rig().setType("quad"); $("bQuad").classList.add("on"); $("bMale").classList.remove("on"); };
    $("bWire").onclick = function () { var on = !$("bWire").classList.contains("on"); $("bWire").classList.toggle("on", on); if (rig()) rig().setWire(on); };
    $("bAuto").onclick = function () { var on = !$("bAuto").classList.contains("on"); $("bAuto").classList.toggle("on", on); if (window.droneView) window.droneView.auto = on; };
    window.addEventListener("resize", function () { S.dirty = true; });
  }

  // =================================================================== websocket
  function link(ok, txt) { $("linkDot").className = "dot " + (ok ? "ok" : "bad"); $("linkTxt").textContent = txt; }
  function connect() {
    var proto = location.protocol === "https:" ? "wss" : "ws";
    var ws = new WebSocket(proto + "://" + location.host + "/ws"); S.ws = ws;
    ws.onopen = function () { link(true, "server connected"); };
    ws.onmessage = function (e) {
      var m; try { m = JSON.parse(e.data); } catch (x) { return; }
      if (m.type === "hello") { S.hist = (m.recent || []).map(function (r) { return { t: r.t, h: r.health }; }); applyMode(m.mode || S.mode); return; }
      onFrame(m);
    };
    ws.onclose = function () { link(false, "server disconnected — retrying"); setTimeout(connect, 1500); };
    ws.onerror = function () { try { ws.close(); } catch (x) { } };
  }

  setInterval(function () {
    var age = (performance.now() - S.lastMsg) / 1000, nd = $("nodata");
    $("ageBadge").textContent = S.lastMsg ? "last frame " + age.toFixed(1) + " s ago" : "no frames yet";
    var stale = !S.frame || age > 4;
    nd.style.display = stale ? "flex" : "none";
    $("nodataTxt").innerHTML = S.mode === "live" ? "WAITING FOR DEVICE DATA<br><span style='font-size:11px;letter-spacing:.1em;color:#8ca3c4'>send samples to /api/ingest — see CONNECT DRONE tab</span>" : "NO DATA FROM SERVER";
    if (S.tab === "conn") renderConn();
  }, 2000);

  // =================================================================== boot
  function boot() {
    bind();
    var box = $("droneBox");
    try {
      if (window.THREE && window.DroneLib) window.droneView = new window.DroneLib.DroneView(window.THREE, box, "male");
      else box.insertAdjacentHTML("beforeend", '<div class="note" style="padding:20px">3D view unavailable (Three.js not loaded).</div>');
    } catch (e) { console.error("3D init failed", e); box.insertAdjacentHTML("beforeend", '<div class="note" style="padding:20px">3D view unavailable (WebGL not supported).</div>'); }
    connect();
    requestAnimationFrame(animate);
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", boot); else boot();
})();
