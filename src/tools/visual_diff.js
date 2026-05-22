import { loadTranspiler } from "./_transpiler_loader.js";
import { PNG } from "pngjs";

function rgbaToPngBase64(rgba, width, height) {
  const png = new PNG({ width, height });
  png.data = Buffer.from(rgba.buffer, rgba.byteOffset, rgba.byteLength);
  return PNG.sync.write(png).toString("base64");
}

// Render both at the same frame and produce a side-by-side diff:
//  - count of differing pixels
//  - a third RGBA buffer where unchanged pixels are dimmed (50% gray) and
//    changed pixels are bright magenta — easy to spot at a glance.
function makeDiff(a, b) {
  if (a.length !== b.length) {
    throw new Error(`framebuffer length mismatch: ${a.length} vs ${b.length}`);
  }
  const diff = new Uint8ClampedArray(a.length);
  let changed = 0;
  for (let i = 0; i < a.length; i += 4) {
    const same =
      a[i] === b[i] &&
      a[i + 1] === b[i + 1] &&
      a[i + 2] === b[i + 2] &&
      a[i + 3] === b[i + 3];
    if (same) {
      // Dim the unchanged background to ~30% so changes pop visually.
      const g = (a[i] * 0.3) | 0;
      diff[i] = g;
      diff[i + 1] = g;
      diff[i + 2] = g;
      diff[i + 3] = 255;
    } else {
      diff[i] = 255;     // bright magenta
      diff[i + 1] = 0;
      diff[i + 2] = 255;
      diff[i + 3] = 255;
      changed++;
    }
  }
  return { diff, changedPixels: changed, totalPixels: a.length / 4 };
}

export const definition = {
  name: "ngpc_visual_diff",
  description:
    "Render two C code snippets at the same frame index and compare their framebuffers pixel-by-pixel. Returns the changed-pixel count + a third PNG (magenta on dimmed grey) showing exactly where they differ. Use to verify a refactor is visually equivalent, or to highlight the visible effect of a change.",
  inputSchema: {
    type: "object",
    properties: {
      code_a: { type: "string", description: "Baseline C source." },
      code_b: { type: "string", description: "Modified C source." },
      frame: {
        type: "integer",
        default: 1,
        minimum: 0,
        maximum: 3600,
        description: "Vsync ticks to advance both before capturing.",
      },
      include_pngs: {
        type: "boolean",
        default: true,
        description:
          "If false, return only counts + dimensions (no base64 payloads — useful in CI / scripted contexts).",
      },
    },
    required: ["code_a", "code_b"],
  },
};

export async function handler({ code_a, code_b, frame = 1, include_pngs = true }) {
  const { Interp } = await loadTranspiler();

  const ra = Interp.runFrames(code_a, { frames: frame, captureFramebuffer: true });
  if (!ra.ok) {
    return { ok: false, side: "a", kind: ra.kind, errors: ra.errors, message: ra.message };
  }
  const rb = Interp.runFrames(code_b, { frames: frame, captureFramebuffer: true });
  if (!rb.ok) {
    return { ok: false, side: "b", kind: rb.kind, errors: rb.errors, message: rb.message };
  }
  if (!ra.framebuffer || !rb.framebuffer) {
    return { ok: false, message: "Framebuffer missing on at least one side." };
  }
  if (
    ra.framebuffer.width !== rb.framebuffer.width ||
    ra.framebuffer.height !== rb.framebuffer.height
  ) {
    return {
      ok: false,
      message: `Framebuffer dimension mismatch: ${ra.framebuffer.width}x${ra.framebuffer.height} vs ${rb.framebuffer.width}x${rb.framebuffer.height}`,
    };
  }

  const W = ra.framebuffer.width;
  const H = ra.framebuffer.height;
  const { diff, changedPixels, totalPixels } = makeDiff(
    ra.framebuffer.rgba,
    rb.framebuffer.rgba
  );

  const result = {
    ok: true,
    width: W,
    height: H,
    frame_captured: ra.framesAdvanced,
    changed_pixels: changedPixels,
    total_pixels: totalPixels,
    changed_ratio: +(changedPixels / totalPixels).toFixed(4),
    identical: changedPixels === 0,
  };
  if (include_pngs) {
    result.png_a_base64 = rgbaToPngBase64(ra.framebuffer.rgba, W, H);
    result.png_b_base64 = rgbaToPngBase64(rb.framebuffer.rgba, W, H);
    result.png_diff_base64 = rgbaToPngBase64(diff, W, H);
  }
  return result;
}
