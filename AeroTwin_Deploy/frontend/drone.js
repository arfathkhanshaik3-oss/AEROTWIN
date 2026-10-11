/* AeroTwin 3D airframe: a real-time visual twin of the aircraft, driven by live telemetry.
 *  - propeller / rotor speed  <- measured engine RPM
 *  - engine glow colour        <- cylinder-head temperature
 *  - airframe shake            <- vibration
 *  - beacon colour + blink, smoke, roll / sink  <- alert level (nominal / degrading / critical)
 *  - bank-turn / descent       <- operator decision (return / modify)
 * Pure scene logic (DroneRig) is separate from rendering (DroneView) so it can be unit-tested headless.
 */
(function (root, factory) {
  if (typeof module === "object" && module.exports) module.exports = factory();
  else root.DroneLib = factory();
})(typeof self !== "undefined" ? self : this, function () {
  "use strict";

  var LEVEL_COL = { nominal: 0x2ee59d, degrading: 0xffb020, critical: 0xff3b3b };
  var clamp = function (x, a, b) { return Math.max(a, Math.min(b, x)); };
  var smooth = function (x) { x = clamp(x, 0, 1); return x * x * (3 - 2 * x); };

  function DroneRig(THREE, type) {
    this.T = THREE;
    this.root = new THREE.Group();          // whole aircraft (receives shake / roll / yaw)
    this.scene = new THREE.Scene();
    this.scene.add(this.root);
    this.time = 0;
    this.yaw = 0;
    this.maneuver = null;
    this.wire = false;
    this.target = { rpm: 3100, temp: 95, vib: 0.8, level: "nominal", health: 100, sensorIssue: false };
    this.cur = { omega: 6, glow: 0, jit: 0, lvl: 0 };
    this.parts = [];                         // meshes (for edge overlay + wire mode)
    this.edges = [];
    this.dist = 0;
    this._build(type || "male");
    this._lights();
    this._ground();
    this._smoke();
  }

  DroneRig.prototype._mat = function (color, o) {
    return new this.T.MeshStandardMaterial(Object.assign({ color: color, metalness: 0.35, roughness: 0.5 }, o || {}));
  };

  DroneRig.prototype._add = function (parent, geo, mat, x, y, z, rx, ry, rz) {
    var m = new this.T.Mesh(geo, mat);
    m.position.set(x || 0, y || 0, z || 0);
    m.rotation.set(rx || 0, ry || 0, rz || 0);
    parent.add(m);
    this.parts.push(m);
    var e = new this.T.LineSegments(new this.T.EdgesGeometry(geo, 25), new this.T.LineBasicMaterial({ color: 0x7ddcff, transparent: true, opacity: 0.35 }));
    m.add(e);
    this.edges.push(e);
    return m;
  };

  DroneRig.prototype._build = function (type) {
    var T = this.T, self = this;
    if (this.air) { this.root.remove(this.air); }
    this.parts = []; this.edges = [];
    this.type = type;
    var air = new T.Group();
    this.air = air; this.root.add(air);
    this.glowMat = this._mat(0x2ee59d, { emissive: 0x2ee59d, emissiveIntensity: 0.6, metalness: 0.2, roughness: 0.4 });
    this.beaconMat = new T.MeshBasicMaterial({ color: 0x2ee59d });
    var body = this._mat(0xd6dde6), dark = this._mat(0x1b2430, { metalness: 0.6, roughness: 0.35 }), wing = this._mat(0xb9c4d1);
    this.rotors = [];

    if (type === "male") {
      // fuselage, nose, sensor turret
      this._add(air, new T.CylinderGeometry(0.12, 0.23, 2.7, 28), body, 0, 0, 0, 0, 0, Math.PI / 2);
      var nose = this._add(air, new T.SphereGeometry(0.23, 24, 16), body, 1.35, 0, 0); nose.scale.set(1.6, 1, 1);
      this._add(air, new T.SphereGeometry(0.13, 16, 12), dark, 1.2, -0.27, 0);
      // wings, inverted V-tail
      this._add(air, new T.BoxGeometry(0.58, 0.05, 6.8), wing, 0.1, 0.13, 0);
      this._add(air, new T.BoxGeometry(0.46, 0.04, 1.25), wing, -1.2, -0.36, 0.5, -0.95, 0, 0);
      this._add(air, new T.BoxGeometry(0.46, 0.04, 1.25), wing, -1.2, -0.36, -0.5, 0.95, 0, 0);
      // pusher engine pod (glows with CHT)
      this._add(air, new T.CylinderGeometry(0.15, 0.17, 0.55, 20), this.glowMat, -1.55, 0.02, 0, 0, 0, Math.PI / 2);
      this._add(air, new T.CylinderGeometry(0.05, 0.05, 0.4, 10), dark, -1.5, -0.2, 0);
      // propeller (spins about X)
      var prop = new T.Group(); prop.position.set(-1.95, 0.02, 0); air.add(prop);
      for (var i = 0; i < 3; i++) {
        var piv = new T.Group(); piv.rotation.x = i * 2 * Math.PI / 3; prop.add(piv);
        this._add(piv, new T.BoxGeometry(0.02, 0.95, 0.1), dark, 0, 0.5, 0);
      }
      var disc = new T.Mesh(new T.CircleGeometry(0.98, 48), new T.MeshBasicMaterial({ color: 0x9fdcff, transparent: true, opacity: 0.0, side: T.DoubleSide, depthWrite: false }));
      disc.rotation.y = Math.PI / 2; prop.add(disc);
      this._add(air, new T.ConeGeometry(0.1, 0.32, 16), dark, -2.12, 0.02, 0, 0, 0, Math.PI / 2);
      this.rotors.push({ g: prop, axis: "x", disc: disc, k: 1, fail: false });
      // nav lights + beacon
      var g = new T.Mesh(new T.SphereGeometry(0.06, 10, 8), new T.MeshBasicMaterial({ color: 0x00ff66 })); g.position.set(0.1, 0.13, 3.4); air.add(g);
      var r = new T.Mesh(new T.SphereGeometry(0.06, 10, 8), new T.MeshBasicMaterial({ color: 0xff2a2a })); r.position.set(0.1, 0.13, -3.4); air.add(r);
      var bc = new T.Mesh(new T.SphereGeometry(0.075, 12, 10), this.beaconMat); bc.position.set(0.2, 0.3, 0); air.add(bc);
      this.engine = new T.Vector3(-1.7, 0.05, 0);
      this.light = new T.PointLight(0x2ee59d, 0.8, 4); this.light.position.set(-1.6, 0.1, 0); air.add(this.light);
      this.gridSpeed = 3.2;
    } else {
      // rescue quad-rotor
      this._add(air, new T.BoxGeometry(1.0, 0.22, 1.0), body, 0, 0, 0);
      this._add(air, new T.BoxGeometry(0.6, 0.06, 0.6), this.glowMat, 0, -0.14, 0);
      this._add(air, new T.BoxGeometry(0.62, 0.015, 0.62), this._mat(0xffffff), 0, 0.12, 0);
      this._add(air, new T.BoxGeometry(0.46, 0.02, 0.13), this._mat(0xe53935), 0, 0.13, 0);
      this._add(air, new T.BoxGeometry(0.13, 0.02, 0.46), this._mat(0xe53935), 0, 0.13, 0);
      this._add(air, new T.BoxGeometry(2.9, 0.07, 0.1), dark, 0, 0.02, 0, 0, Math.PI / 4, 0);
      this._add(air, new T.BoxGeometry(2.9, 0.07, 0.1), dark, 0, 0.02, 0, 0, -Math.PI / 4, 0);
      var corners = [[1.02, 1.02], [1.02, -1.02], [-1.02, -1.02], [-1.02, 1.02]];
      for (var c = 0; c < 4; c++) {
        var cx = corners[c][0], cz = corners[c][1];
        this._add(air, new T.CylinderGeometry(0.12, 0.12, 0.2, 16), dark, cx, 0.08, cz);
        var rg = new T.Group(); rg.position.set(cx, 0.22, cz); air.add(rg);
        for (var b = 0; b < 2; b++) {
          var pv = new T.Group(); pv.rotation.y = b * Math.PI; rg.add(pv);
          this._add(pv, new T.BoxGeometry(0.66, 0.015, 0.1), dark, 0.33, 0, 0);
        }
        var dd = new T.Mesh(new T.CircleGeometry(0.68, 40), new T.MeshBasicMaterial({ color: 0x9fdcff, transparent: true, opacity: 0.0, side: T.DoubleSide, depthWrite: false }));
        dd.rotation.x = -Math.PI / 2; rg.add(dd);
        this.rotors.push({ g: rg, axis: "y", disc: dd, k: (c % 2 ? -1 : 1), fail: false });
        this._add(air, new T.BoxGeometry(0.04, 0.36, 0.04), dark, cx * 0.55, -0.22, cz * 0.55);
      }
      var bc2 = new T.Mesh(new T.SphereGeometry(0.075, 12, 10), this.beaconMat); bc2.position.set(0, 0.25, 0); air.add(bc2);
      this.engine = new T.Vector3(0, 0.1, 0);
      this.light = new T.PointLight(0x2ee59d, 0.8, 4); this.light.position.set(0, -0.2, 0); air.add(this.light);
      this.gridSpeed = 0.6;
    }
    this.setWire(this.wire);
  };

  DroneRig.prototype.setType = function (t) { if (t !== this.type) this._build(t); };

  DroneRig.prototype.setWire = function (on) {
    this.wire = !!on;
    var self = this;
    this.parts.forEach(function (m) { m.material.visible = !self.wire; });
    this.edges.forEach(function (e) { e.material.opacity = self.wire ? 0.95 : 0.3; });
  };

  DroneRig.prototype._lights = function () {
    var T = this.T;
    this.scene.add(new T.AmbientLight(0x8fa6c4, 0.75));
    var key = new T.DirectionalLight(0xffffff, 0.95); key.position.set(5, 8, 4); this.scene.add(key);
    var rim = new T.DirectionalLight(0x4aa8ff, 0.55); rim.position.set(-6, 3, -5); this.scene.add(rim);
  };

  DroneRig.prototype._ground = function () {
    var T = this.T;
    this.grid = new T.GridHelper(40, 40, 0x1f6f8f, 0x15384d);
    this.grid.position.y = -1.7;
    this.grid.material.transparent = true; this.grid.material.opacity = 0.55;
    this.scene.add(this.grid);
    this.shadow = new T.Mesh(new T.CircleGeometry(1.2, 32), new T.MeshBasicMaterial({ color: 0x000000, transparent: true, opacity: 0.28 }));
    this.shadow.rotation.x = -Math.PI / 2; this.shadow.position.y = -1.69; this.scene.add(this.shadow);
  };

  DroneRig.prototype._smoke = function () {
    var T = this.T;
    this.puffs = [];
    var geo = new T.SphereGeometry(0.12, 8, 6);
    for (var i = 0; i < 28; i++) {
      var m = new T.Mesh(geo, new T.MeshBasicMaterial({ color: 0x3a3f46, transparent: true, opacity: 0 }));
      m.visible = false; this.root.add(m);
      this.puffs.push({ m: m, life: 0, max: 1.6, vx: 0, vy: 0 });
    }
  };

  // ------------------------------------------------------------------ public API
  DroneRig.prototype.setState = function (s) {
    for (var k in s) { if (s[k] !== undefined && s[k] !== null) this.target[k] = s[k]; }
  };

  DroneRig.prototype.doManeuver = function (name) {
    if (name === "return") this.maneuver = { name: name, t: 0, dur: 3.2 };
    else if (name === "modify") this.maneuver = { name: name, t: 0, dur: 2.2 };
    else if (name === "reset") { this.maneuver = null; this.cur.omega = Math.min(this.cur.omega, 4); }
  };

  DroneRig.prototype.step = function (dt) {
    dt = clamp(dt, 0, 0.1);
    var T = this.T, t = this.target, c = this.cur;
    this.time += dt;
    var lvl = t.level === "critical" ? 2 : (t.level === "degrading" ? 1 : 0);
    c.lvl += (lvl - c.lvl) * Math.min(1, dt * 2.5);
    var lf = clamp(c.lvl / 2, 0, 1);                       // 0 nominal .. 1 critical
    var rpmN = clamp(t.rpm / 3120, 0, 1.6);

    // ---- propeller / rotor speed follows measured RPM (with sputter when critical)
    var sputter = 1 - 0.35 * lf * (0.5 + 0.5 * Math.sin(this.time * 17) * Math.sin(this.time * 5.3));
    var omegaT = rpmN * 26 * sputter;
    c.omega += (omegaT - c.omega) * Math.min(1, dt * 2.2);
    var self = this;
    this.rotors.forEach(function (r, i) {
      var f = 1;
      if (self.type === "quad" && i === 1) f = 1 - 0.9 * lf;        // one rotor fails when critical
      var w = c.omega * f * r.k;
      if (r.axis === "x") r.g.rotation.x += w * dt; else r.g.rotation.y += w * dt;
      r.disc.material.opacity = clamp((Math.abs(c.omega * f) - 8) / 40, 0, 0.42);
    });

    // ---- engine glow follows CHT
    var hot = clamp((t.temp - 90) / 70, 0, 1);
    c.glow += (hot - c.glow) * Math.min(1, dt * 2);
    var col = new T.Color(0x2ee59d).lerp(new T.Color(0xff5a1f), c.glow);
    this.glowMat.color.copy(col); this.glowMat.emissive.copy(col); this.glowMat.emissiveIntensity = 0.5 + 1.4 * c.glow;
    this.light.color.copy(col); this.light.intensity = 0.6 + 1.6 * c.glow;

    // ---- beacon colour + blink by alert level
    var bcol = LEVEL_COL[t.level] || LEVEL_COL.nominal;
    var hz = [0.6, 1.8, 4.5][lvl];
    var on = Math.sin(this.time * Math.PI * 2 * hz) > (lvl === 2 ? -0.1 : -0.6);
    this.beaconMat.color.setHex(on ? bcol : 0x111111);

    // ---- vibration shake + critical wobble / sink
    var vibN = clamp((t.vib - 0.85) / 2.2, 0, 1);
    c.jit += (vibN - c.jit) * Math.min(1, dt * 3);
    var sh = c.jit * 0.045;
    var tt = this.time;
    this.root.position.set(Math.sin(tt * 53) * sh, Math.sin(tt * 61) * sh + Math.sin(tt * 0.9) * 0.03 - 0.25 * lf, Math.sin(tt * 47) * sh);
    var roll = Math.sin(tt * 0.8) * 0.03 + lf * (0.16 * Math.sin(tt * 1.6) + 0.1) + (this.type === "quad" ? lf * 0.22 : 0);
    var pitch = Math.sin(tt * 0.6) * 0.02 - 0.1 * lf;
    var yawAdd = 0, rollAdd = 0, pitchAdd = 0;
    var m = this.maneuver;
    if (m) {
      m.t += dt;
      var p = clamp(m.t / m.dur, 0, 1);
      if (m.name === "return") { yawAdd = Math.PI * smooth(p); rollAdd = 0.55 * Math.sin(Math.PI * p); }
      if (m.name === "modify") { pitchAdd = -0.18 * Math.sin(Math.PI * p); }
      if (p >= 1) { if (m.name === "return") this.yaw += Math.PI; this.maneuver = null; }
    }
    this.root.rotation.set(roll + rollAdd, this.yaw + yawAdd, pitch + pitchAdd);

    // ---- moving ground grid => visible airspeed
    this.dist += rpmN * this.gridSpeed * dt * (1 - 0.5 * lf);
    this.grid.position.x = -(this.dist % 1);
    this.shadow.scale.setScalar(1 + 0.15 * lf);

    // ---- smoke when hot / critical
    var smoke = Math.max(lf > 0.7 ? 1 : (lf > 0.2 ? 0.35 : 0), clamp((t.temp - 130) / 40, 0, 1));
    var ep = this.engine;
    this.puffs.forEach(function (pf) {
      if (pf.life <= 0) {
        if (Math.random() < smoke * dt * 14) {
          pf.life = pf.max; pf.m.visible = true;
          pf.m.position.set(ep.x + (Math.random() - 0.5) * 0.15, ep.y + 0.1, ep.z + (Math.random() - 0.5) * 0.15);
          pf.vx = -0.5 - Math.random() * 0.4; pf.vy = 0.5 + Math.random() * 0.5;
        }
      } else {
        pf.life -= dt;
        pf.m.position.x += pf.vx * dt; pf.m.position.y += pf.vy * dt;
        var a = clamp(pf.life / pf.max, 0, 1);
        pf.m.material.opacity = 0.5 * a; pf.m.scale.setScalar(1 + (1 - a) * 2.2);
        if (pf.life <= 0) pf.m.visible = false;
      }
    });
  };

  // ------------------------------------------------------------------ renderer wrapper (browser only)
  function DroneView(THREE, el, type) {
    this.T = THREE; this.el = el;
    this.rig = new DroneRig(THREE, type || "male");
    this.renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true });
    this.renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
    this.renderer.setClearColor(0x000000, 0);
    el.appendChild(this.renderer.domElement);
    this.camera = new THREE.PerspectiveCamera(38, 1, 0.1, 100);
    this.az = 0.9; this.el_ = 0.32; this.zoom = 8.2; this.auto = true; this.drag = false;
    var self = this, cv = this.renderer.domElement;
    cv.style.touchAction = "none"; cv.style.cursor = "grab";
    cv.addEventListener("pointerdown", function (e) { self.drag = true; self.lx = e.clientX; self.ly = e.clientY; cv.setPointerCapture(e.pointerId); cv.style.cursor = "grabbing"; });
    cv.addEventListener("pointerup", function () { self.drag = false; cv.style.cursor = "grab"; });
    cv.addEventListener("pointermove", function (e) {
      if (!self.drag) return;
      self.az -= (e.clientX - self.lx) * 0.008; self.el_ = clamp(self.el_ + (e.clientY - self.ly) * 0.006, -0.1, 1.2);
      self.lx = e.clientX; self.ly = e.clientY;
    });
    cv.addEventListener("wheel", function (e) { e.preventDefault(); self.zoom = clamp(self.zoom + e.deltaY * 0.005, 4.5, 14); }, { passive: false });
    this.last = performance.now();
    this._resize();
    if (window.ResizeObserver) new ResizeObserver(function () { self._resize(); }).observe(el);
    var loop = function (now) {
      var dt = (now - self.last) / 1000; self.last = now;
      if (self.auto && !self.drag) self.az += dt * 0.18;
      self.rig.step(dt);
      var r = self.zoom;
      self.camera.position.set(Math.cos(self.az) * Math.cos(self.el_) * r, Math.sin(self.el_) * r, Math.sin(self.az) * Math.cos(self.el_) * r);
      self.camera.lookAt(0, -0.2, 0);
      self.renderer.render(self.rig.scene, self.camera);
      requestAnimationFrame(loop);
    };
    requestAnimationFrame(loop);
  }
  DroneView.prototype._resize = function () {
    var w = this.el.clientWidth || 300, h = this.el.clientHeight || 300;
    this.renderer.setSize(w, h, false);
    this.renderer.domElement.style.width = "100%"; this.renderer.domElement.style.height = "100%";
    this.camera.aspect = w / h; this.camera.updateProjectionMatrix();
  };

  return { DroneRig: DroneRig, DroneView: DroneView };
});
