import { loadTranspiler } from "./_transpiler_loader.js";
import { PNG } from "pngjs";

function rgbaToPngBase64(rgba, width, height) {
  const png = new PNG({ width, height });
  png.data = Buffer.from(rgba.buffer, rgba.byteOffset, rgba.byteLength);
  return PNG.sync.write(png).toString("base64");
}

export const definition = {
  name: "ngpc_screenshot",
  description:
    "Render a PNG of the NGPC framebuffer at frame N. Internally calls NGPC_Interp.runFrames + NGPC_VDP.renderToPixels, then encodes via pngjs. Returns base64 PNG (160×152) + dimensions. Useful to verify the visual after a code change.",
  inputSchema: {
    type: "object",
    properties: {
      code: { type: "string" },
      frame: {
        type: "integer",
        default: 1,
        minimum: 0,
        maximum: 3600,
        description:
          "Number of vsync ticks to advance before capturing. 0 = capture immediately after main() body runs (no vsync).",
      },
    },
    required: ["code"],
  },
};

export async function handler({ code, frame = 1 }) {
  const { Interp } = await loadTranspiler();
  const r = Interp.runFrames(code, { frames: frame, captureFramebuffer: true });
  if (!r.ok) {
    return {
      ok: false,
      kind: r.kind,
      ...(r.errors ? { errors: r.errors } : {}),
      ...(r.message ? { message: r.message } : {}),
    };
  }
  if (!r.framebuffer) {
    return { ok: false, message: "VDP not available — framebuffer not captured" };
  }
  const png_base64 = rgbaToPngBase64(
    r.framebuffer.rgba,
    r.framebuffer.width,
    r.framebuffer.height
  );
  return {
    ok: true,
    width: r.framebuffer.width,
    height: r.framebuffer.height,
    frame_captured: r.framesAdvanced,
    log_count: r.logs.length,
    png_base64,
  };
}
