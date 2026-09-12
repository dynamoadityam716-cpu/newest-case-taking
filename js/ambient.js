/* Case-Taking "new" — interactive dot grid for the hero.
   Vanilla port of React Bits' <DotGrid /> (reactbits.dev):
   a grid of dots brightens near the pointer, scatters with inertia
   on fast movement, and ripples outward on click — then springs back
   elastically. No dependencies (gsap's inertia/elastic emulated with a
   damped spring). Respects prefers-reduced-motion, pauses when the hero
   is off-screen or the tab is hidden. */
(function () {
  'use strict';
  var canvas = document.getElementById('hero-ambient');
  if (!canvas) return;
  var reduceMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  var ctx = canvas.getContext('2d');
  var w = 0, h = 0, dpr = 1;
  var dots = [];
  var raf = 0;
  var running = false;

  // ---- DotGrid props (React Bits defaults, tuned to the terminal hero) ----
  var DOT_SIZE = 5;          // dotSize px
  var GAP = 34;              // gap px
  var PROXIMITY = 150;       // px — dots brighten within this radius
  var SPEED_TRIGGER = 110;   // px/s — mouse speed that triggers inertia
  var SHOCK_RADIUS = 260;    // px — click shockwave radius
  var SHOCK_STRENGTH = 4;    // click push multiplier
  var MAX_SPEED = 5000;      // clamp pointer velocity
  var SPRING_K = 0.02;       // spring constant (return to origin)
  var DAMPING = 0.92;        // velocity damping (inertia resistance)
  var BASE_RGB = { r: 96, g: 122, b: 58 };      // dim olive — idle dots
  var ACTIVE_RGB = { r: 199, g: 248, b: 90 };   // bright lime — near pointer

  var pointer = { x: -9999, y: -9999, vx: 0, vy: 0, speed: 0, lastX: 0, lastY: 0, lastT: 0 };

  function buildGrid() {
    var cell = DOT_SIZE + GAP;
    var cols = Math.max(1, Math.floor((w + GAP) / cell));
    var rows = Math.max(1, Math.floor((h + GAP) / cell));
    var gridW = cell * cols - GAP;
    var gridH = cell * rows - GAP;
    var startX = (w - gridW) / 2 + DOT_SIZE / 2;
    var startY = (h - gridH) / 2 + DOT_SIZE / 2;
    dots = [];
    for (var y = 0; y < rows; y++) {
      for (var x = 0; x < cols; x++) {
        dots.push({
          cx: startX + x * cell,
          cy: startY + y * cell,
          x: 0, y: 0,      // current offset
          vx: 0, vy: 0,    // velocity
          applied: false
        });
      }
    }
  }

  function resize() {
    var rect = canvas.parentElement.getBoundingClientRect();
    w = rect.width; h = rect.height;
    dpr = Math.min(window.devicePixelRatio || 1, 2);
    canvas.width = Math.round(w * dpr);
    canvas.height = Math.round(h * dpr);
    canvas.style.width = w + 'px';
    canvas.style.height = h + 'px';
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    buildGrid();
    if (reduceMotion) drawStatic();
  }

  function drawStatic() {
    ctx.clearRect(0, 0, w, h);
    ctx.fillStyle = 'rgba(' + BASE_RGB.r + ',' + BASE_RGB.g + ',' + BASE_RGB.b + ',0.5)';
    for (var i = 0; i < dots.length; i++) {
      ctx.beginPath();
      ctx.arc(dots[i].cx, dots[i].cy, DOT_SIZE / 2, 0, Math.PI * 2);
      ctx.fill();
    }
  }

  function blend(t) {
    return 'rgb(' +
      Math.round(BASE_RGB.r + (ACTIVE_RGB.r - BASE_RGB.r) * t) + ',' +
      Math.round(BASE_RGB.g + (ACTIVE_RGB.g - BASE_RGB.g) * t) + ',' +
      Math.round(BASE_RGB.b + (ACTIVE_RGB.b - BASE_RGB.b) * t) + ')';
  }

  function step(ts) {
    var dt = Math.min(32, ts - (step.last || ts)) / 16.6; // ~60fps normalized
    step.last = ts;
    ctx.clearRect(0, 0, w, h);

    // cheap viewport check — skip drawing when the hero is off-screen
    var vp = canvas.getBoundingClientRect();
    if (vp.bottom < -40 || vp.top > window.innerHeight + 40) return;

    var px = pointer.x, py = pointer.y;
    var proxSq = PROXIMITY * PROXIMITY;

    for (var i = 0; i < dots.length; i++) {
      var d = dots[i];
      // damped spring back to origin (elastic return)
      d.vx += (-SPRING_K * d.x - (1 - DAMPING) * d.vx) * dt;
      d.vy += (-SPRING_K * d.y - (1 - DAMPING) * d.vy) * dt;
      d.x += d.vx * dt;
      d.y += d.vy * dt;
      // settled? allow re-trigger
      if (d.applied && Math.abs(d.x) < 0.3 && Math.abs(d.y) < 0.3 && Math.abs(d.vx) < 0.3 && Math.abs(d.vy) < 0.3) {
        d.x = 0; d.y = 0; d.vx = 0; d.vy = 0; d.applied = false;
      }

      var dx = d.cx - px, dy = d.cy - py;
      var dsq = dx * dx + dy * dy;
      var t = 0;
      if (dsq <= proxSq) t = 1 - Math.sqrt(dsq) / PROXIMITY;
      ctx.beginPath();
      ctx.arc(d.cx + d.x, d.cy + d.y, DOT_SIZE / 2, 0, Math.PI * 2);
      ctx.fillStyle = blend(t);
      ctx.fill();
    }
  }

  function loop(ts) {
    try {
      step(ts);
    } catch (err) {
      // never let one bad frame kill the animation
      if (window.console && console.error) console.error('dotgrid step error', err);
    }
    raf = requestAnimationFrame(loop);
  }

  function start() {
    if (running) return;
    running = true;
    raf = requestAnimationFrame(loop);
  }
  function stop() {
    if (!running) return;
    running = false;
    cancelAnimationFrame(raf);
  }

  function applyPush(d, pushX, pushY) {
    if (d.applied) return;
    d.applied = true;
    d.vx += pushX * 0.02;
    d.vy += pushY * 0.02;
  }

  function onPointerMove(e) {
    var now = performance.now();
    var pr = pointer;
    var dt = pr.lastT ? (now - pr.lastT) : 16;
    var dx = e.clientX - pr.lastX;
    var dy = e.clientY - pr.lastY;
    var vx = (dx / dt) * 1000;
    var vy = (dy / dt) * 1000;
    var speed = Math.hypot(vx, vy);
    if (speed > MAX_SPEED) {
      var s = MAX_SPEED / speed;
      vx *= s; vy *= s; speed = MAX_SPEED;
    }
    pr.lastT = now; pr.lastX = e.clientX; pr.lastY = e.clientY;
    pr.vx = vx; pr.vy = vy; pr.speed = speed;

    var rect = canvas.getBoundingClientRect();
    pr.x = e.clientX - rect.left;
    pr.y = e.clientY - rect.top;

    // fast movement → inertia scatter inside proximity
    if (speed > SPEED_TRIGGER) {
      for (var i = 0; i < dots.length; i++) {
        var d = dots[i];
        var dist = Math.hypot(d.cx - pr.x, d.cy - pr.y);
        if (dist < PROXIMITY) {
          var pushX = (d.cx - pr.x) + pr.vx * 0.005;
          var pushY = (d.cy - pr.y) + pr.vy * 0.005;
          applyPush(d, pushX, pushY);
        }
      }
    }
  }

  function onClick(e) {
    var rect = canvas.getBoundingClientRect();
    var cx = e.clientX - rect.left;
    var cy = e.clientY - rect.top;
    for (var i = 0; i < dots.length; i++) {
      var d = dots[i];
      var dist = Math.hypot(d.cx - cx, d.cy - cy);
      if (dist < SHOCK_RADIUS) {
        var falloff = Math.max(0, 1 - dist / SHOCK_RADIUS);
        var pushX = (d.cx - cx) * SHOCK_STRENGTH * falloff;
        var pushY = (d.cy - cy) * SHOCK_STRENGTH * falloff;
        applyPush(d, pushX, pushY);
      }
    }
  }

  // throttle like the reference (50ms)
  var lastMove = 0;
  window.addEventListener('mousemove', function (e) {
    var now = performance.now();
    if (now - lastMove < 50) return;
    lastMove = now;
    onPointerMove(e);
  }, { passive: true });
  window.addEventListener('pointerdown', onClick, { passive: true });
  window.addEventListener('resize', resize);

  document.addEventListener('visibilitychange', function () {
    if (document.hidden) stop(); else start();
  });

  if (reduceMotion) {
    resize();
    return;
  }

  resize();
  start();
})();