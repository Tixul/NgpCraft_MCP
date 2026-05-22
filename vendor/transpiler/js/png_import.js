// PNG import dialog controller.
//
// `NGPC_PngImport.open(file, host)` shows the modal bound to `file`, lets
// the user pick sprite vs tilemap + parameters, runs the ported converter
// from js/asset_tools.js, and calls `host.onResult({ cPath, hPath,
// cSource, hSource })` when the user confirms. `host.onCancel()` fires on
// dismiss.
//
// The modal is reusable — `open` can be called multiple times; state
// (filename, preview, param values) is re-seeded each call. Errors from
// the converter surface in the modal's status line so the user can tweak
// the frame size without losing the dialog.

const NGPC_PngImport = (() => {
  let modal, titleEl, infoEl, previewEl, nameEl, statusEl;
  let kindRadios, spriteParams, tilemapParams;
  let frameWEl, frameHEl, frameCountEl, tileBaseEl, palBaseEl, animDurationEl;
  let maxPalettesEl, blackTransparentEl;
  let okBtn, cancelBtn;
  let currentFile = null;
  let currentImage = null;    // { data, width, height }
  let host = null;

  function ensureWired() {
    if (modal) return;
    modal          = document.getElementById('asset-import-modal');
    titleEl        = document.getElementById('asset-import-title');
    infoEl         = document.getElementById('asset-import-info');
    previewEl      = document.getElementById('asset-import-preview');
    nameEl         = document.getElementById('asset-name');
    statusEl       = document.getElementById('asset-import-status');
    kindRadios     = Array.from(document.querySelectorAll('input[name="asset-kind"]'));
    spriteParams   = document.getElementById('asset-sprite-params');
    tilemapParams  = document.getElementById('asset-tilemap-params');
    frameWEl       = document.getElementById('asset-frame-w');
    frameHEl       = document.getElementById('asset-frame-h');
    frameCountEl   = document.getElementById('asset-frame-count');
    tileBaseEl     = document.getElementById('asset-tile-base');
    palBaseEl      = document.getElementById('asset-pal-base');
    animDurationEl = document.getElementById('asset-anim-duration');
    maxPalettesEl  = document.getElementById('asset-max-palettes');
    blackTransparentEl = document.getElementById('asset-black-transparent');
    okBtn          = document.getElementById('asset-import-ok');
    cancelBtn      = document.getElementById('asset-import-cancel');

    kindRadios.forEach(r => r.addEventListener('change', refreshKind));
    cancelBtn.addEventListener('click', close);
    okBtn.addEventListener('click', runImport);
    modal.addEventListener('click', (e) => { if (e.target === modal) close(); });
    document.addEventListener('keydown', (e) => {
      if (modal.hidden) return;
      if (e.key === 'Escape') { e.preventDefault(); close(); }
    });
  }

  function currentKind() {
    return kindRadios.find(r => r.checked)?.value || 'sprite';
  }
  function refreshKind() {
    const kind = currentKind();
    spriteParams.hidden  = (kind !== 'sprite');
    tilemapParams.hidden = (kind !== 'tilemap');
  }

  function setStatus(msg, level = 'info') {
    statusEl.textContent = msg || '';
    statusEl.className = 'modal-status' + (msg ? ` modal-status-${level}` : '');
  }

  function drawPreview(image) {
    // Fit the source inside the preview canvas with integer nearest-neighbor
    // scaling when possible.
    const ctx = previewEl.getContext('2d');
    ctx.imageSmoothingEnabled = false;
    const maxW = previewEl.width, maxH = previewEl.height;
    const scale = Math.max(1, Math.min(
      Math.floor(maxW / image.width) || 1,
      Math.floor(maxH / image.height) || 1,
    ));
    const drawW = Math.min(maxW, image.width * scale);
    const drawH = Math.min(maxH, image.height * scale);
    ctx.fillStyle = '#111';
    ctx.fillRect(0, 0, maxW, maxH);
    // Rebuild an ImageBitmap-ish path: blit the raw ImageData to a temp
    // canvas first so we can call drawImage (fastest nearest-neighbor).
    const src = document.createElement('canvas');
    src.width = image.width;
    src.height = image.height;
    const sctx = src.getContext('2d');
    const data = new ImageData(new Uint8ClampedArray(image.data), image.width, image.height);
    sctx.putImageData(data, 0, 0);
    const x = Math.floor((maxW - drawW) / 2);
    const y = Math.floor((maxH - drawH) / 2);
    ctx.drawImage(src, 0, 0, image.width, image.height, x, y, drawW, drawH);
  }

  async function open(file, hostCallbacks) {
    ensureWired();
    currentFile = file;
    host = hostCallbacks || {};
    setStatus('');
    titleEl.textContent = `Import PNG — ${file.name}`;
    // Strip the extension and sanitize to derive a symbol name.
    const base = file.name.replace(/\.[^.]+$/, '');
    nameEl.value = NGPC_AssetTools.sanitizeCIdentifier(base);
    try {
      currentImage = await NGPC_AssetTools.decodePng(file);
    } catch (e) {
      alert(`Could not decode ${file.name}:\n${e.message}`);
      currentFile = null; currentImage = null;
      return;
    }
    infoEl.textContent = `${currentImage.width} × ${currentImage.height} px`;
    drawPreview(currentImage);

    // Seed sensible frame size from common sprite sheet conventions:
    // if the image is taller than wide → assume vertical strip; if the
    // width/height divide cleanly by 8, keep defaults.
    const w = currentImage.width, h = currentImage.height;
    if (w === h) {
      frameWEl.value = w;
      frameHEl.value = h;
    } else if (w % h === 0) {
      // Horizontal strip: N frames of (h × h).
      frameWEl.value = h;
      frameHEl.value = h;
    } else {
      frameWEl.value = 16;
      frameHEl.value = 16;
    }
    frameCountEl.value = '';

    refreshKind();
    modal.hidden = false;
    nameEl.focus();
    nameEl.select();
  }

  function close() {
    modal.hidden = true;
    currentFile = null;
    currentImage = null;
    if (host && typeof host.onCancel === 'function') host.onCancel();
    host = null;
  }

  async function runImport() {
    if (!currentImage) return;
    try {
      const name = NGPC_AssetTools.sanitizeCIdentifier(nameEl.value || 'asset');
      const kind = currentKind();
      let result;
      if (kind === 'sprite') {
        const frameCountRaw = frameCountEl.value.trim();
        result = NGPC_AssetTools.exportSprite(
          currentImage.data, currentImage.width, currentImage.height,
          {
            name,
            frameW: parseInt(frameWEl.value, 10),
            frameH: parseInt(frameHEl.value, 10),
            frameCount: frameCountRaw ? parseInt(frameCountRaw, 10) : null,
            tileBase: parseInt(tileBaseEl.value, 10) || 0,
            palBase:  parseInt(palBaseEl.value, 10)  || 0,
            animDuration: parseInt(animDurationEl.value, 10) || 6,
          }
        );
      } else {
        result = NGPC_AssetTools.exportTilemap(
          currentImage.data, currentImage.width, currentImage.height,
          {
            name,
            maxPalettes: parseInt(maxPalettesEl.value, 10) || 16,
            blackIsTransparent: blackTransparentEl.checked,
          }
        );
      }
      const payload = {
        kind,
        name,
        cPath: `src/${name}${kind === 'sprite' ? '_mspr' : ''}.c`,
        hPath: `src/${name}${kind === 'sprite' ? '_mspr' : ''}.h`,
        cSource: result.cSource,
        hSource: result.hSource,
        summary: result.summary,
      };
      if (host && typeof host.onResult === 'function') {
        host.onResult(payload);
      }
      modal.hidden = true;
      currentFile = null;
      currentImage = null;
      host = null;
    } catch (e) {
      setStatus(e.message, 'err');
    }
  }

  return { open };
})();

// Expose to globalThis so non-browser hosts (Node vm, Workers, electron) can
// access this binding — top-level `const` is otherwise script-scoped.
if (typeof globalThis !== 'undefined') globalThis.NGPC_PngImport = NGPC_PngImport;
