/*
 * SIH26047 — speech.js
 * ---------------------------------------------------------------------------
 * Shared ASR / TTS provider layer + kiosk accessibility helpers (Module A /
 * Module D voice requirements).
 *
 *   window.SIH.asr.transcribe({ lang })   — speech → text  (Promise<string>)
 *   window.SIH.tts.speak(text, lang)      — text → speech
 *   window.SIH.kiosk.{ enable, disable, announce, textMode }
 *
 * Providers: `webspeech` works in any modern Chrome/Edge/Safari without keys
 * (the demo/fallback path) and `bhashini` is the production provider for
 * Indian-language, noisy-OPD voice. The Bhashini provider calls an Edge
 * Function (supabase/functions/bhashini) whose URL is configured in
 * js/env.js — until that env var exists it fails with a clear message and the
 * UI falls back to webspeech. No audio is persisted by this module.
 */
(function () {
  'use strict';

  function env() {
    return (typeof window !== 'undefined' && window.SIH_ENV) ? window.SIH_ENV : {};
  }

  function langTag(code) {
    // app codes en/hi/mr → BCP-47 for the speech engines
    var map = { en: 'en-IN', hi: 'hi-IN', mr: 'mr-IN' };
    return map[code] || code || 'en-IN';
  }

  // ---------------- ASR ----------------
  var provider = 'webspeech';

  var webSpeechSupported = (function () {
    if (typeof window === 'undefined') return false;
    var SR = window.SpeechRecognition || window.webkitSpeechRecognition;
    return !!SR;
  })();

  function transcribeWebSpeech(opts) {
    return new Promise(function (resolve, reject) {
      var SR = window.SpeechRecognition || window.webkitSpeechRecognition;
      if (!SR) return reject(new Error('Speech-to-text not supported in this browser.'));
      var rec = new SR();
      rec.lang = langTag(opts.lang);
      rec.interimResults = false;
      rec.maxAlternatives = 1;
      var settled = false;
      var timer = setTimeout(function () {
        if (!settled) { settled = true; try { rec.stop(); } catch (e) {} reject(new Error('No speech heard — try again.')); }
      }, opts.timeoutMs || 12000);
      rec.onresult = function (e) {
        if (settled) return;
        settled = true;
        clearTimeout(timer);
        var text = '';
        for (var i = 0; i < e.results.length; i++) {
          text += e.results[i][0].transcript;
        }
        resolve(text.trim());
      };
      rec.onerror = function (e) {
        if (settled) return;
        settled = true;
        clearTimeout(timer);
        reject(new Error('Speech error: ' + (e.error || 'unknown')));
      };
      rec.onend = function () {
        if (!settled) { settled = true; clearTimeout(timer); reject(new Error('No speech heard — try again.')); }
      };
      try { rec.start(); } catch (e) { reject(e); }
    });
  }

  function transcribeBhashini(opts) {
    var base = env().bhashiniUrl || env().bhashiniEdgeUrl;
    if (!base) {
      return Promise.reject(new Error('Bhashini is not configured — add bhashiniEdgeUrl to js/env.js, or use the webspeech provider.'));
    }
    return fetch(base, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ op: 'asr', audioUrl: opts.audioUrl, lang: langTag(opts.lang) })
    }).then(function (r) {
      if (!r.ok) throw new Error('Bhashini ASR failed (' + r.status + ')');
      return r.json();
    }).then(function (data) {
      var text = data && (data.text || (data.data && data.data.text)) || '';
      if (!text) throw new Error('Bhashini returned no transcript.');
      return String(text).trim();
    });
  }

  var asr = {
    get provider() { return provider; },
    setProvider: function (name) {
      if (name !== 'webspeech' && name !== 'bhashini') throw new Error('Unknown ASR provider: ' + name);
      provider = name;
    },
    supported: function () { return webSpeechSupported || !!env().bhashiniEdgeUrl; },
    transcribe: function (opts) {
      opts = opts || {};
      if (provider === 'bhashini') return transcribeBhashini(opts);
      return transcribeWebSpeech(opts);
    }
  };

  // -------- On-device dictation (works without the browser speech service) ---
  // The browser's own recogniser depends on a speech service that is missing on
  // `file://` pages, in some Chromium builds, and on locked-down networks. In
  // those cases we record the mic and transcribe locally with Whisper running in
  // WebAssembly: the engine arrives from the CDN on first use, the model is then
  // kept in the browser cache, and the audio never leaves the device.
  var TRANSFORMERS_URL = 'https://cdn.jsdelivr.net/npm/@xenova/transformers@2.17.2';
  var DEFAULT_MODEL = 'Xenova/whisper-tiny'; // ~40 MB, multilingual (en/hi/mr)
  var LANG_NAME = { en: 'english', hi: 'hindi', mr: 'marathi' };

  var transformersPromise = null;
  var transcriberPromise = null;
  var stream = null, recorder = null, chunks = [], recording = false, mime = '';

  function loadEngine() {
    if (window.__sihTransformers) return Promise.resolve(window.__sihTransformers);
    if (transformersPromise) return transformersPromise;
    transformersPromise = import(TRANSFORMERS_URL).then(function (mod) {
      window.__sihTransformers = mod;
      try {
        mod.env.allowLocalModels = false; // models come from the Hub, not this folder
        mod.env.useBrowserCache = typeof caches !== 'undefined'; // keep it after the first download
      } catch (e) {}
      return mod;
    }).catch(function () {
      transformersPromise = null;
      throw new Error('The on-device speech engine could not be downloaded — check the connection.');
    });
    return transformersPromise;
  }

  function getTranscriber(onProgress) {
    if (transcriberPromise) return transcriberPromise;
    var model = env().localAsrModel || DEFAULT_MODEL;
    transcriberPromise = loadEngine().then(function (t) {
      return t.pipeline('automatic-speech-recognition', model, {
        progress_callback: function (p) {
          if (onProgress && p && p.status === 'progress' && p.total) onProgress(Math.round((p.loaded / p.total) * 100));
        }
      });
    }).catch(function (err) { transcriberPromise = null; throw err; });
    return transcriberPromise;
  }

  function releaseStream() {
    if (stream) { try { stream.getTracks().forEach(function (t) { t.stop(); }); } catch (e) {} }
    stream = null; recorder = null;
  }

  // Whisper happily invents words for silence — "[laughter]", "Thank you." — and
  // a patient's notes must never grow a word nobody said. Drop the markers it
  // writes for non-speech, and treat those leftovers as "heard nothing".
  var NON_SPEECH_TEXT = /\[[^\]]*\]|\([^)]*\)|\*[^*]*\*/g;
  // Whole-utterance filler only (punctuation ignored) — a note that merely
  // starts with "So" or "And" is kept.
  var FILLER = {
    'thank you': 1, 'thank you very much': 1, 'thanks': 1, 'thanks for watching': 1,
    'please subscribe': 1, 'subscribe': 1, 'bye': 1, 'you': 1, 'okay': 1, 'ok': 1,
    'so': 1, 'and': 1, 'hmm': 1, 'uh': 1, 'um': 1, 'please': 1, 'hello': 1, 'hi': 1, '': 1
  };
  function cleanTranscript(text) {
    var t = String(text || '').replace(NON_SPEECH_TEXT, ' ').replace(/\s+/g, ' ').trim();
    if (!t) return '';
    var bare = t.replace(/^[\s.,!?]+|[\s.,!?]+$/g, '').toLowerCase();
    if (FILLER[bare]) return '';
    if (/^subtitles? by/i.test(t) || /amara\.org/i.test(t)) return '';
    return t;
  }

  // A quiet room still gives the recorder a hiss; real speech is far louder.
  var SILENCE_PEAK = 0.01;
  function isSilent(audio) {
    var peak = 0;
    for (var i = 0; i < audio.length; i += 8) {
      var v = audio[i] < 0 ? -audio[i] : audio[i];
      if (v > peak) peak = v;
    }
    return peak < SILENCE_PEAK;
  }

  function micMessage(err) {
    var name = err && err.name;
    if (name === 'NotAllowedError' || name === 'SecurityError') return 'Microphone access is blocked — allow the mic for this page and try again.';
    if (name === 'NotFoundError' || name === 'DevicesNotFoundError') return 'No microphone was found — check that one is connected.';
    return (err && err.message) || 'Voice did not start — please retry.';
  }

  function start() {
    if (recording) return Promise.resolve();
    if (!available()) return Promise.reject(new Error('This browser cannot record audio — please type instead.'));
    return navigator.mediaDevices.getUserMedia({
      audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true }
    }).then(function (s) {
      stream = s;
      chunks = [];
      mime = '';
      var candidates = ['audio/webm;codecs=opus', 'audio/webm', 'audio/ogg;codecs=opus', 'audio/mp4'];
      for (var i = 0; i < candidates.length; i++) {
        if (window.MediaRecorder.isTypeSupported && window.MediaRecorder.isTypeSupported(candidates[i])) { mime = candidates[i]; break; }
      }
      recorder = mime ? new window.MediaRecorder(stream, { mimeType: mime }) : new window.MediaRecorder(stream);
      recorder.ondataavailable = function (e) { if (e.data && e.data.size) chunks.push(e.data); };
      recorder.start();
      recording = true;
    }, function (err) { throw new Error(micMessage(err)); });
  }

  function stop(opts) {
    opts = opts || {};
    if (!recorder || !recording) return Promise.reject(new Error('Nothing is being recorded.'));
    var finished = new Promise(function (resolve, reject) {
      recorder.onstop = function () {
        recording = false;
        var type = recorder.mimeType || mime || 'audio/webm';
        releaseStream();
        var blob = new Blob(chunks, { type: type });
        chunks = [];
        if (blob.size) resolve(blob); else reject(new Error('No audio was captured — please try again.'));
      };
      try { recorder.stop(); } catch (e) { recording = false; releaseStream(); reject(e); }
    });
    return finished
      .then(function (blob) { if (opts.onState) opts.onState('decoding'); return decodeMono16k(blob); })
      .then(function (audio) {
        if (isSilent(audio)) throw new Error('Nothing was heard — the microphone only picked up silence. Speak a little closer, then try again.');
        if (opts.onState) opts.onState('transcribing');
        return getTranscriber(opts.onProgress).then(function (pipe) {
          return pipe(audio, {
            task: 'transcribe',
            language: LANG_NAME[String(opts.lang || 'en').split('-')[0]] || 'english',
            chunk_length_s: 30,
            stride_length_s: 5
          });
        });
      })
      .then(function (out) { return cleanTranscript((out && out.text) || String(out || '')); });
  }

  // Whisper wants 16 kHz mono samples; the recorder gives us compressed audio.
  function decodeMono16k(blob) {
    var Ctx = window.AudioContext || window.webkitAudioContext;
    var Off = window.OfflineAudioContext || window.webkitOfflineAudioContext;
    return blob.arrayBuffer().then(function (buf) {
      var ctx = new Ctx();
      return new Promise(function (resolve, reject) {
        ctx.decodeAudioData(buf, resolve, function () { reject(new Error('The recording could not be read — please try again.')); });
      }).then(function (decoded) {
        if (!Off) return decoded.getChannelData(0); // no resampler: hand over what we have
        var frames = Math.max(1, Math.ceil(decoded.duration * 16000));
        var off = new Off(1, frames, 16000);
        var src = off.createBufferSource();
        src.buffer = decoded;
        src.connect(off.destination);
        src.start();
        return off.startRendering();
      }).then(function (rendered) {
        try { ctx.close(); } catch (e) {}
        return rendered.getChannelData(0);
      });
    });
  }

  function available() {
    return !!(typeof navigator !== 'undefined' && navigator.mediaDevices &&
      navigator.mediaDevices.getUserMedia && window.MediaRecorder);
  }

  // Throw the current recording away (used when the form or language resets).
  function cancel() {
    if (!recorder || !recording) return;
    recording = false;
    try { recorder.onstop = null; recorder.stop(); } catch (e) {}
    releaseStream();
    chunks = [];
  }

  var dictation = {
    available: available,
    isRecording: function () { return recording; },
    start: start,
    stop: stop,
    cancel: cancel,
    // Optional: fetch the engine + model before the doctor taps the mic.
    warmUp: function (onProgress) { return getTranscriber(onProgress).then(function () { return true; }); }
  };

  // ---------------- TTS ----------------
  function speakWeb(text, lang) {
    if (typeof window === 'undefined' || !('speechSynthesis' in window)) {
      return Promise.reject(new Error('Speech synthesis not supported in this browser.'));
    }
    return new Promise(function (resolve) {
      var u = new SpeechSynthesisUtterance(String(text));
      u.lang = langTag(lang);
      u.rate = 0.98;
      u.onend = function () { resolve(); };
      u.onerror = function () { resolve(); }; // never block the flow on TTS failure
      window.speechSynthesis.cancel();
      window.speechSynthesis.speak(u);
      // some browsers never fire onend — resolve after a beat anyway
      setTimeout(resolve, Math.min(6000, 800 + String(text).length * 45));
    });
  }

  var tts = {
    speak: function (text, lang) { return speakWeb(text, lang); },
    stop: function () {
      if (typeof window !== 'undefined' && 'speechSynthesis' in window) window.speechSynthesis.cancel();
    }
  };

  // ---------------- Kiosk / accessibility ----------------
  var kiosk = {
    enabled: false,
    enable: function () {
      kiosk.enabled = true;
      document.body.classList.add('kiosk-mode');
      kiosk.ensureLiveRegion();
    },
    disable: function () {
      kiosk.enabled = false;
      document.body.classList.remove('kiosk-mode');
    },
    // textMode: body class that enlarges type / increases contrast
    textMode: function (on) {
      document.body.classList.toggle('text-big', !!on);
    },
    ensureLiveRegion: function () {
      if (document.getElementById('sihLiveRegion')) return;
      var region = document.createElement('div');
      region.id = 'sihLiveRegion';
      region.setAttribute('aria-live', 'polite');
      region.className = 'sih-live-region';
      document.body.appendChild(region);
    },
    // Read a step aloud (audio prompt) and mirror it to the live region.
    announce: function (text, lang) {
      kiosk.ensureLiveRegion();
      var region = document.getElementById('sihLiveRegion');
      if (region) region.textContent = text || '';
      if (kiosk.enabled && text) return tts.speak(text, lang);
      return Promise.resolve();
    }
  };

  window.SIH = window.SIH || {};
  window.SIH.asr = asr;
  window.SIH.dictation = dictation;
  window.SIH.tts = tts;
  window.SIH.kiosk = kiosk;
})();
