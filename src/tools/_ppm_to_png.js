// Read a P6 binary PPM file and return { width, height, png_base64 }.
//
// PPM P6 layout:
//   "P6\n<width> <height>\n<maxval>\n<binary rgb bytes>"
// Whitespace between the numeric fields can be any \s; a single trailing
// whitespace separates the maxval line from the binary payload.
//
// Only maxval=255 is supported (which is what the emulator emits).

import { readFile } from "node:fs/promises";
import { PNG } from "pngjs";

export async function ppmFileToPng(path) {
  const buf = await readFile(path);
  // Walk a tiny ASCII header parser. We can't use Buffer.toString() on the
  // entire file because the payload is binary; instead scan byte-by-byte.
  let i = 0;
  const readToken = () => {
    // Skip whitespace + comments (# until \n).
    while (i < buf.length) {
      const c = buf[i];
      if (c === 0x23 /* # */) {
        while (i < buf.length && buf[i] !== 0x0a) i++;
      } else if (c === 0x20 || c === 0x09 || c === 0x0a || c === 0x0d) {
        i++;
      } else break;
    }
    const start = i;
    while (i < buf.length) {
      const c = buf[i];
      if (c === 0x20 || c === 0x09 || c === 0x0a || c === 0x0d) break;
      i++;
    }
    return buf.slice(start, i).toString("ascii");
  };

  const magic = readToken();
  if (magic !== "P6") {
    throw new Error(`Not a binary PPM (P6) file: magic='${magic}' at ${path}`);
  }
  const width = parseInt(readToken(), 10);
  const height = parseInt(readToken(), 10);
  const maxval = parseInt(readToken(), 10);
  if (maxval !== 255) {
    throw new Error(`Unsupported PPM maxval=${maxval} (only 255 handled).`);
  }
  // Skip exactly one whitespace byte after maxval, then payload starts.
  i++;
  const expected = width * height * 3;
  if (buf.length - i < expected) {
    throw new Error(
      `Truncated PPM payload: got ${buf.length - i} bytes, expected ${expected}`
    );
  }

  const png = new PNG({ width, height });
  const dst = png.data;
  for (let p = 0, srcP = i; p < width * height; p++) {
    dst[p * 4 + 0] = buf[srcP++];
    dst[p * 4 + 1] = buf[srcP++];
    dst[p * 4 + 2] = buf[srcP++];
    dst[p * 4 + 3] = 0xff;
  }
  const png_base64 = PNG.sync.write(png).toString("base64");
  return { width, height, png_base64 };
}
