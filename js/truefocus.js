/* Case-Taking "new" — TrueFocus headline, vanilla port of React Bits'
   <TrueFocus /> (reactbits.dev). Words of the hero lead sharpen one at a
   time inside a glowing corner frame; the others stay softly blurred.
   Auto mode cycles through the words forever; manual hover mode is
   available via manualMode. No dependency — the frame is driven with
   CSS transitions, blur with a class toggle. Respects
   prefers-reduced-motion (renders all words sharp, frame hidden). */
(function () {
  'use strict';
  var container = document.getElementById('trueFocus');
  if (!container) return;
  var reduceMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;

  var sentence = 'The diagnosis lives in the case history';
  var manualMode = false;
  var blurAmount = 5;
  var borderColor = '#C7F85A';
  var glowColor = 'rgba(199,248,90,0.6)';
  var animationDuration = 0.5;      // seconds
  var pauseBetweenAnimations = 1.3; // seconds

  var words = Array.prototype.slice.call(container.querySelectorAll('.focus-word'));
  var frame = container.querySelector('.focus-frame');
  if (!words.length || !frame) return;

  var currentIndex = 0;
  var lastActiveIndex = null;
  var timer = null;
  var raf = 0;
  var lastTick = 0;

  function applyBlur() {
    words.forEach(function (w, i) {
      w.classList.toggle('blurred', i !== currentIndex);
    });
  }

  function updateFrame() {
    var parentRect = container.getBoundingClientRect();
    var active = words[currentIndex];
    var r = active.getBoundingClientRect();
    frame.style.transform = 'translate(' + (r.left - parentRect.left) + 'px,' + (r.top - parentRect.top) + 'px)';
    frame.style.width = r.width + 'px';
    frame.style.height = r.height + 'px';
  }

  function focusIndex(i) {
    currentIndex = i;
    applyBlur();
    updateFrame();
  }

  function next() {
    focusIndex((currentIndex + 1) % words.length);
  }

  // styles for the frame colors
  frame.style.setProperty('--border-color', borderColor);
  frame.style.setProperty('--glow-color', glowColor);
  frame.style.transitionDuration = animationDuration + 's';

  if (reduceMotion) {
    applyBlur(); // CSS forces blur off / frame hidden under reduced-motion
    return;
  }

  // start: first word sharp, frame over it
  focusIndex(0);

  if (!manualMode) {
    // rAF-driven cycle: pauses with the tab like the dot grid, never drifts
    var step = function (ts) {
      if (!lastTick) lastTick = ts;
      if (ts - lastTick >= (animationDuration + pauseBetweenAnimations) * 1000) {
        lastTick = ts;
        next();
      }
      raf = requestAnimationFrame(step);
    };
    raf = requestAnimationFrame(step);
    window.addEventListener('resize', updateFrame);
    document.addEventListener('visibilitychange', function () {
      if (!document.hidden) {
        lastTick = 0;
        updateFrame();
      }
    });
  } else {
    words.forEach(function (w, i) {
      w.addEventListener('mouseenter', function () {
        lastActiveIndex = currentIndex;
        focusIndex(i);
      });
      w.addEventListener('mouseleave', function () {
        if (lastActiveIndex !== null) focusIndex(lastActiveIndex);
      });
    });
  }

  // cleanup if the page unloads (defensive; timers die with the page anyway)
  window.addEventListener('beforeunload', function () {
    if (timer) clearInterval(timer);
    if (raf) cancelAnimationFrame(raf);
  });
})();