import { loadTranspiler } from "./_transpiler_loader.js";

export const definition = {
  name: "ngpc_psg_trace",
  description:
    "Run C code N frames and return the chronological list of PSG state changes (tone divider, attenuation, noise control, reset). Each event ties a frame index to a structured PSG write — useful to diagnose silent BGM, stuck channels, BGM stop frames, etc. without needing audio output. Backed by NGPC_PSG.getEvents() exposed via NGPC_Interp.runFrames(capturePsgEvents=true).",
  inputSchema: {
    type: "object",
    properties: {
      code: { type: "string" },
      frames: {
        type: "integer",
        default: 60,
        minimum: 1,
        maximum: 3600,
      },
      filter: {
        type: "string",
        enum: ["any", "tone", "attn", "noise", "reset"],
        default: "any",
        description: "Filter events by type. 'any' returns everything.",
      },
      limit: {
        type: "integer",
        default: 200,
        description: "Max events returned (chronological order, head).",
      },
    },
    required: ["code"],
  },
};

function summarize(events) {
  // Build a per-channel summary so the caller doesn't have to re-aggregate.
  const channels = { 0: {}, 1: {}, 2: {}, 3: {} };
  let resetCount = 0;
  for (const e of events) {
    if (e.type === "reset") { resetCount++; continue; }
    const ch = e.ch;
    if (!(ch in channels)) continue;
    const c = channels[ch];
    if (e.type === "tone") {
      c.lastDivider = e.divider;
      c.lastFreqHz = Math.round(e.freq);
      c.toneWrites = (c.toneWrites || 0) + 1;
    } else if (e.type === "attn") {
      c.lastAttn = e.attn;
      c.lastSilent = e.silent;
      c.attnWrites = (c.attnWrites || 0) + 1;
    } else if (e.type === "noise") {
      c.lastNoiseCtrl = e.ctrl;
      c.lastWhite = e.white;
      c.noiseWrites = (c.noiseWrites || 0) + 1;
    }
  }
  return { reset_count: resetCount, channels };
}

export async function handler({ code, frames = 60, filter = "any", limit = 200 }) {
  const { Interp } = await loadTranspiler();
  const r = Interp.runFrames(code, {
    frames,
    captureFramebuffer: false,
    capturePsgEvents: true,
  });
  if (!r.ok) {
    return {
      ok: false,
      kind: r.kind,
      ...(r.errors ? { errors: r.errors } : {}),
      ...(r.message ? { message: r.message } : {}),
    };
  }
  let events = r.psgEvents ?? [];
  if (filter !== "any") events = events.filter((e) => e.type === filter);
  return {
    ok: true,
    frames_advanced: r.framesAdvanced,
    main_completed: r.mainCompleted,
    event_count_total: (r.psgEvents ?? []).length,
    event_count_filtered: events.length,
    summary: summarize(r.psgEvents ?? []),
    events: events.slice(0, limit),
  };
}
