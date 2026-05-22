// T6W28 PSG emulator (WebAudio backend) for the NgpCraft Live Editor.
//
// Hardware reference summary (AUDIO.md §1.1 / §1.5 / §1.6):
//   - 3 square-wave tone generators + 1 noise generator (4 voices total).
//   - Tone frequency: F = 3072000 / (32 * divider), divider 1..1023 (10-bit).
//   - Attenuation per voice: 4-bit, 0 = loudest, 15 = silent.
//   - Noise control byte: bits 1..0 = rate select, bit 2 = 0 periodic / 1 white.
//
// This module does NOT emulate the Z80 coprocessor or the PSG byte protocol.
// It is driven at a higher level by runtime.js: the `Sfx_*` / `Bgm_*` stubs
// translate game-visible calls into `setTone` / `setAttn` / `setNoise` here.
//
// Browser constraint: AudioContext can only be created / resumed after a user
// gesture (click, keydown, touch). `init()` is idempotent and safe to call
// before unlock; it allocates the context but expects `resume()` to actually
// produce sound. Until then, every write is no-op'd and state is queued on the
// internal model so that a later `resume()` picks up the current state.

const NGPC_PSG = (() => {
  // Base frequency of the chip's master clock divider (3.072 MHz / 32 = 96 kHz).
  // Tone frequency = PSG_CLOCK / divider where divider ∈ [1, 1023].
  const PSG_CLOCK = 3072000 / 32;

  // 4-bit attenuation → linear gain. Standard SN76489-family: 2 dB per step.
  // Clamp attn >= 15 to full silence (0 gain) so a properly-released note is
  // actually silent (10^(-30/20) would still leak audio).
  function attnToGain(attn) {
    if (attn >= 15) return 0;
    return Math.pow(10, -2 * attn / 20);
  }

  // Voice model — mirrors the chip regardless of whether WebAudio is live.
  // Writes update these fields synchronously; `applyAll` pushes them into the
  // WebAudio graph when a context exists and is running.
  const voices = {
    tone0: { type: 'tone',  divider: 1023, attn: 15 },
    tone1: { type: 'tone',  divider: 1023, attn: 15 },
    tone2: { type: 'tone',  divider: 1023, attn: 15 },
    noise: { type: 'noise', ctrl: 0,       attn: 15 },
  };
  const TONE_ORDER = ['tone0', 'tone1', 'tone2'];

  // WebAudio graph — built lazily on first `init()` call.
  let ctx = null;
  let master = null;                // master GainNode -> destination
  const toneOsc = [null, null, null];
  const toneGain = [null, null, null];
  let noiseSrc = null;              // AudioBufferSourceNode
  let noiseGain = null;             // GainNode

  // White noise buffer (1 second, looped). Regenerated once per ctx lifetime.
  function makeWhiteNoiseBuffer() {
    const len = ctx.sampleRate | 0;
    const buf = ctx.createBuffer(1, len, ctx.sampleRate);
    const out = buf.getChannelData(0);
    for (let i = 0; i < len; i++) out[i] = Math.random() * 2 - 1;
    return buf;
  }

  // Periodic noise: short repeating LFSR-like pattern. Gives a "buzzy" tone
  // distinct from white noise (matches the `bit 2 = 0` T6W28 periodic mode).
  function makePeriodicNoiseBuffer() {
    const periodSamples = 16;
    const loopLen = periodSamples * Math.round(ctx.sampleRate / periodSamples);
    const buf = ctx.createBuffer(1, loopLen, ctx.sampleRate);
    const out = buf.getChannelData(0);
    for (let i = 0; i < loopLen; i++) {
      out[i] = ((i % periodSamples) < periodSamples / 2) ? 1 : -1;
    }
    return buf;
  }

  // Map the noise control byte to a playbackRate multiplier. Rates are
  // relative to a nominal "N/512" base at playbackRate = 1.0 (AUDIO.md §1.6
  // bit assignment). Rate 3 = "use Tone3 output" — approximated as white
  // noise at the tone3 divider's frequency (v1 shortcut).
  function rateMultiplier(ctrl) {
    switch (ctrl & 3) {
      case 0: return 1.0;       // N/512
      case 1: return 0.5;       // N/1024
      case 2: return 0.25;      // N/2048
      case 3: {                 // Tone3 sync
        const div3 = voices.tone2.divider || 1;
        // Pick a playbackRate roughly proportional to tone3 frequency.
        return Math.max(0.0625, Math.min(4.0, (PSG_CLOCK / div3) / 500));
      }
    }
    return 1.0;
  }

  function ensureGraph() {
    if (!ctx) return false;
    if (master) return true;
    master = ctx.createGain();
    master.gain.value = 0.25;   // prevent clipping when all 4 voices sum
    master.connect(ctx.destination);
    for (let i = 0; i < 3; i++) {
      const osc = ctx.createOscillator();
      osc.type = 'square';
      osc.frequency.value = PSG_CLOCK / 1023;
      const g = ctx.createGain();
      g.gain.value = 0;
      osc.connect(g).connect(master);
      osc.start();
      toneOsc[i] = osc;
      toneGain[i] = g;
    }
    // Default to white noise; replaced on setNoise() if periodic requested.
    noiseSrc = ctx.createBufferSource();
    noiseSrc.buffer = makeWhiteNoiseBuffer();
    noiseSrc.loop = true;
    noiseGain = ctx.createGain();
    noiseGain.gain.value = 0;
    noiseSrc.connect(noiseGain).connect(master);
    noiseSrc.start();
    return true;
  }

  function applyTone(ch) {
    if (!ensureGraph()) return;
    const v = voices[TONE_ORDER[ch]];
    const f = PSG_CLOCK / Math.max(1, v.divider);
    toneOsc[ch].frequency.setValueAtTime(f, ctx.currentTime);
    toneGain[ch].gain.setValueAtTime(attnToGain(v.attn), ctx.currentTime);
  }

  function applyNoise() {
    if (!ensureGraph()) return;
    // Swap buffer between white / periodic if bit 2 changed.
    const wantWhite = (voices.noise.ctrl & 4) !== 0;
    const curIsWhite = noiseSrc.__isWhite !== false;
    if (wantWhite !== curIsWhite) {
      try { noiseSrc.stop(); } catch (_) {}
      try { noiseSrc.disconnect(); } catch (_) {}
      noiseSrc = ctx.createBufferSource();
      noiseSrc.buffer = wantWhite ? makeWhiteNoiseBuffer() : makePeriodicNoiseBuffer();
      noiseSrc.loop = true;
      noiseSrc.connect(noiseGain);
      noiseSrc.start();
      noiseSrc.__isWhite = wantWhite;
    }
    noiseSrc.playbackRate.setValueAtTime(rateMultiplier(voices.noise.ctrl), ctx.currentTime);
    noiseGain.gain.setValueAtTime(attnToGain(voices.noise.attn), ctx.currentTime);
  }

  // --- Public API ---

  // Allocate the AudioContext. Safe to call before user gesture — sound
  // stays silent until `resume()` is invoked from a user-triggered handler.
  // Guarded against non-browser hosts (Node tests, Workers without window) —
  // the PSG state model still updates so headless callers can introspect it.
  function init() {
    if (ctx) return;
    if (typeof window === 'undefined') return;
    const AC = window.AudioContext || window.webkitAudioContext;
    if (!AC) return;  // no WebAudio → PSG model still updates for tests
    ctx = new AC();
    ensureGraph();
  }

  function resume() {
    if (!ctx) init();
    if (ctx && ctx.state !== 'running') {
      try { ctx.resume(); } catch (_) {}
    }
  }

  function isUnlocked() {
    return !!(ctx && ctx.state === 'running');
  }

  // Diagnostic event log — every state-changing call appends a structured
  // entry. Independent of WebAudio: a Node host introspects this to answer
  // questions like "at what frame did channel 0 go silent?" without ever
  // creating an audio context. `setEventSink(fn)` streams events live.
  // `getEvents(clear=true)` drains the buffer.
  const events = [];
  let eventSink = null;
  let eventBudget = 4096;  // ring-buffer cap so a runaway loop doesn't OOM
  function emit(evt) {
    if (events.length >= eventBudget) events.shift();
    events.push(evt);
    if (eventSink) try { eventSink(evt); } catch (_) {}
  }
  function setEventSink(fn) { eventSink = fn; }
  function setEventBudget(n) { eventBudget = Math.max(1, n | 0); }
  function getEvents(clear = true) {
    const out = events.slice();
    if (clear) events.length = 0;
    return out;
  }

  function setTone(ch, divider) {
    if (ch < 0 || ch > 2) return;
    const v = voices[TONE_ORDER[ch]];
    v.divider = Math.max(1, Math.min(1023, divider | 0));
    applyTone(ch);
    emit({ type: 'tone', ch, divider: v.divider, freq: PSG_CLOCK / v.divider });
  }

  function setAttn(ch, attn) {
    const a = Math.max(0, Math.min(15, attn | 0));
    if (ch === 3) {
      voices.noise.attn = a;
      applyNoise();
      emit({ type: 'attn', ch: 3, voice: 'noise', attn: a, silent: a >= 15 });
    } else if (ch >= 0 && ch <= 2) {
      voices[TONE_ORDER[ch]].attn = a;
      applyTone(ch);
      emit({ type: 'attn', ch, voice: TONE_ORDER[ch], attn: a, silent: a >= 15 });
    }
  }

  function setNoise(ctrl) {
    voices.noise.ctrl = ctrl & 0xFF;
    applyNoise();
    emit({ type: 'noise', ctrl: voices.noise.ctrl, white: !!(ctrl & 4) });
  }

  // Silence all voices without tearing down the graph.
  function reset() {
    for (let ch = 0; ch < 3; ch++) setAttn(ch, 15);
    setAttn(3, 15);
    emit({ type: 'reset' });
  }

  // Expose the voice model for tests + debugging.
  function getState() {
    return {
      tone0: { ...voices.tone0 },
      tone1: { ...voices.tone1 },
      tone2: { ...voices.tone2 },
      noise: { ...voices.noise },
      freqs: TONE_ORDER.map(k => PSG_CLOCK / Math.max(1, voices[k].divider)),
      unlocked: isUnlocked(),
    };
  }

  return {
    init, resume, isUnlocked,
    setTone, setAttn, setNoise, reset,
    getState,
    // Diagnostic event log (headless / Node / MCP / tests).
    getEvents, setEventSink, setEventBudget,
    // Helper for callers who only know the note index → divider table.
    PSG_CLOCK,
    attnToGain,
  };
})();

// Expose to globalThis so non-browser hosts (Node vm, Workers, electron) can
// access this binding — top-level `const` is otherwise script-scoped.
if (typeof globalThis !== 'undefined') globalThis.NGPC_PSG = NGPC_PSG;
