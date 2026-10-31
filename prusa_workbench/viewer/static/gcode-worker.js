// Web Worker: fetch a PrusaSlicer G-code file and turn it into flat typed arrays.
//
// Extrusions -> one record per segment, SEG_STRIDE floats: start xyz, end xyz, width, height, type, layer.
// Travels    -> one record per move, TRAVEL_STRIDE floats: start xyz, end xyz, layer, extrusion index at that time.
// Segments come out in G-code order, so every layer is a contiguous range [layerFirst[i], layerEnd[i]).

const SEG_STRIDE = 10;
const TRAVEL_STRIDE = 8;
const ARC_STEP = 0.6; // mm of arc per straight piece

class Grow {
  constructor(n) { this.a = new Float32Array(n); this.n = 0; }
  push(...v) {
    if (this.n + v.length > this.a.length) {
      const b = new Float32Array(Math.max(this.a.length * 2, this.n + v.length));
      b.set(this.a); this.a = b;
    }
    for (let i = 0; i < v.length; i++) this.a[this.n++] = v[i];
  }
  done() { return this.a.slice(0, this.n); }
}

function parse(text, progress) {
  const segs = new Grow(1 << 20), travels = new Grow(1 << 18);
  const types = [], typeIndex = new Map(), typeLength = [];
  const layerZ = [], layerFirst = [];
  let x = 0, y = 0, z = 0, e = 0;
  let absXYZ = true, relE = true;
  let type = typeOf('Custom'), width = 0.45, height = 0.2, layer = 0, layerStarted = false;
  let nSeg = 0;

  function typeOf(name) {
    let i = typeIndex.get(name);
    if (i === undefined) { i = types.length; types.push(name); typeIndex.set(name, i); typeLength.push(0); }
    return i;
  }
  function addSeg(x0, y0, z0, x1, y1, z1) {
    segs.push(x0, y0, z0, x1, y1, z1, width, height, type, layer);
    typeLength[type] += Math.hypot(x1 - x0, y1 - y0, z1 - z0);
    nSeg++;
  }

  let pos = 0, lineNo = 0;
  const len = text.length;
  while (pos < len) {
    let end = text.indexOf('\n', pos);
    if (end < 0) end = len;
    let line = text.substring(pos, end);
    pos = end + 1;
    if ((++lineNo & 0x3ffff) === 0) progress(pos / len);

    if (line.charCodeAt(0) === 59 /* ; */) {
      if (line.startsWith(';TYPE:')) type = typeOf(line.substring(6).trim());
      else if (line.startsWith(';WIDTH:')) width = parseFloat(line.substring(7));
      else if (line.startsWith(';HEIGHT:')) height = parseFloat(line.substring(8));
      else if (line.startsWith(';LAYER_CHANGE')) {
        // Anything before the first layer (purge line) counts as layer 0.
        if (layerStarted) { layer++; layerFirst[layer] = nSeg; } else { layerFirst[0] = 0; }
        layerStarted = true;
        layerZ[layer] = null;
      } else if (line.startsWith(';Z:') && layerStarted && layerZ[layer] === null) {
        layerZ[layer] = parseFloat(line.substring(3));
      }
      continue;
    }
    const sc = line.indexOf(';');
    if (sc >= 0) line = line.substring(0, sc);
    line = line.trim();
    if (!line) continue;

    const words = line.split(/\s+/);
    const cmd = words[0].toUpperCase();
    if (cmd === 'G0' || cmd === 'G1' || cmd === 'G2' || cmd === 'G3') {
      let nx = x, ny = y, nz = z, de = 0, hasE = false, I = 0, J = 0;
      for (let i = 1; i < words.length; i++) {
        const w = words[i], c = w.charCodeAt(0) | 32, v = parseFloat(w.substring(1));
        if (Number.isNaN(v)) continue;
        if (c === 120) nx = absXYZ ? v : x + v;        // x
        else if (c === 121) ny = absXYZ ? v : y + v;   // y
        else if (c === 122) nz = absXYZ ? v : z + v;   // z
        else if (c === 101) { hasE = true; de = relE ? v : v - e; e = relE ? e + v : v; } // e
        else if (c === 105) I = v;                     // i
        else if (c === 106) J = v;                     // j
      }
      const moved = nx !== x || ny !== y;
      if (moved) {
        const extruding = hasE && de > 0;
        if (cmd === 'G2' || cmd === 'G3') {
          // Arc around (x+I, y+J); G2 clockwise, G3 counter-clockwise.
          const cx = x + I, cy = y + J, r = Math.hypot(I, J);
          const a0 = Math.atan2(y - cy, x - cx);
          let a1 = Math.atan2(ny - cy, nx - cx), sweep = a1 - a0;
          if (cmd === 'G2') { if (sweep >= 0) sweep -= 2 * Math.PI; }
          else if (sweep <= 0) sweep += 2 * Math.PI;
          const n = Math.max(1, Math.ceil(Math.abs(sweep) * r / ARC_STEP));
          let px = x, py = y, pz = z;
          for (let k = 1; k <= n; k++) {
            const t = k / n, a = a0 + sweep * t;
            const qx = k === n ? nx : cx + r * Math.cos(a), qy = k === n ? ny : cy + r * Math.sin(a);
            const qz = z + (nz - z) * t;
            if (extruding) addSeg(px, py, pz, qx, qy, qz);
            else travels.push(px, py, pz, qx, qy, qz, layer, nSeg);
            px = qx; py = qy; pz = qz;
          }
        } else if (extruding) addSeg(x, y, z, nx, ny, nz);
        else travels.push(x, y, z, nx, ny, nz, layer, nSeg);
      }
      x = nx; y = ny; z = nz;
    } else if (cmd === 'G90') absXYZ = true;
    else if (cmd === 'G91') absXYZ = false;
    else if (cmd === 'M82') relE = false;
    else if (cmd === 'M83') relE = true;
    else if (cmd === 'G92') {
      for (let i = 1; i < words.length; i++) {
        const c = words[i].charCodeAt(0) | 32, v = parseFloat(words[i].substring(1));
        if (c === 101) e = v; else if (c === 120) x = v; else if (c === 121) y = v; else if (c === 122) z = v;
      }
    }
  }

  // Close layer ranges; layers that never got a ;Z: use the z of their first segment.
  const nLayers = Math.max(1, layerFirst.length);
  if (!layerFirst.length) { layerFirst[0] = 0; layerZ[0] = null; }
  const segArr = segs.done();
  const layerEnd = new Uint32Array(nLayers), first = new Uint32Array(nLayers), zs = new Float32Array(nLayers);
  for (let i = 0; i < nLayers; i++) {
    first[i] = layerFirst[i];
    layerEnd[i] = i + 1 < nLayers ? layerFirst[i + 1] : nSeg;
    zs[i] = layerZ[i] ?? (layerEnd[i] > first[i] ? segArr[first[i] * SEG_STRIDE + 5] : 0);
  }
  return { segs: segArr, travels: travels.done(), types, typeLength, layerZ: zs, layerFirst: first, layerEnd, nSeg };
}

self.onmessage = async ({ data: { url, id } }) => {
  try {
    const res = await fetch(url, { cache: 'no-store' });
    if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
    const text = await res.text();
    const out = parse(text, p => self.postMessage({ id, progress: p }));
    self.postMessage({ id, result: out },
      [out.segs.buffer, out.travels.buffer, out.layerZ.buffer, out.layerFirst.buffer, out.layerEnd.buffer]);
  } catch (err) {
    self.postMessage({ id, error: String(err && err.message || err) });
  }
};
