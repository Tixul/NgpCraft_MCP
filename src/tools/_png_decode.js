// Pure-Node PNG decode → { data: Uint8ClampedArray (RGBA), width, height }
// Replaces the browser-only NGPC_AssetTools.decodePng (which uses Image/Canvas).

import { PNG } from "pngjs";

export function decodePngBase64(base64) {
  const buf = Buffer.from(base64, "base64");
  return decodePngBuffer(buf);
}

export function decodePngBuffer(buffer) {
  const png = PNG.sync.read(buffer);
  // pngjs always normalizes to RGBA — png.data is Buffer with R,G,B,A bytes.
  const u8 = new Uint8ClampedArray(png.data.buffer, png.data.byteOffset, png.data.byteLength);
  return { data: u8, width: png.width, height: png.height };
}
