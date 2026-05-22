import { loadTranspiler } from "./_transpiler_loader.js";

export const definition = {
  name: "ngpc_quickrun",
  description:
    "Transpile + execute C code headlessly via the live-editor interpreter (NGPC_Interp.runFrames). If the code calls ngpc_vsync(), main() becomes a generator and we drive it for `frames` ticks. Returns logs, frames advanced, completion state, and a small VRAM/state digest. NO audio output (PSG state-only). NO real frame timing — purely instruction-execution-bounded. Best for smoke-testing logic before writing to disk.",
  inputSchema: {
    type: "object",
    properties: {
      code: { type: "string" },
      frames: {
        type: "integer",
        default: 60,
        minimum: 1,
        maximum: 3600,
        description:
          "Max generator iterations to advance (~1 NGPC frame each). Ignored if main() doesn't use vsync.",
      },
    },
    required: ["code"],
  },
};

export async function handler({ code, frames = 60 }) {
  const { Interp } = await loadTranspiler();
  const r = Interp.runFrames(code, { frames, captureFramebuffer: false });
  if (!r.ok) {
    return {
      ok: false,
      kind: r.kind,
      ...(r.errors ? { errors: r.errors, formatted: r.formatted } : {}),
      ...(r.message ? { message: r.message } : {}),
      ...(r.frame !== undefined ? { frame: r.frame } : {}),
      logs: r.logs ?? [],
    };
  }
  return {
    ok: true,
    frames_advanced: r.framesAdvanced,
    main_completed: r.mainCompleted,
    log_count: r.logs.length,
    logs: r.logs.slice(-50),
    state_digest: {
      bg_color: r.state.bgColor,
      scr1_ofs_x: r.state.scr1OfsX,
      scr1_ofs_y: r.state.scr1OfsY,
      scr2_ofs_x: r.state.scr2OfsX,
      scr2_ofs_y: r.state.scr2OfsY,
    },
  };
}
