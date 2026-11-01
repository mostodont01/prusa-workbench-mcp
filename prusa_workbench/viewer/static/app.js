import * as THREE from 'three';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';

// ---------- constants ----------

// PrusaSlicer's default feature colors, in its legend order.
const FEATURES = [
  ['Perimeter', 0xffe64d], ['External perimeter', 0xff7d38], ['Overhang perimeter', 0x1f1fff],
  ['Internal infill', 0xb03029], ['Solid infill', 0x9654cc], ['Top solid infill', 0xf04040],
  ['Ironing', 0xff8c69], ['Bridge infill', 0x4d80ba], ['Gap fill', 0xffffff], ['Skirt/Brim', 0x00876e],
  ['Support material', 0x00ff00], ['Support material interface', 0x008000], ['Wipe tower', 0xb3e3ab],
  ['Custom', 0x5ed194],
];
const FEATURE_COLOR = new Map(FEATURES);
const FEATURE_ORDER = new Map(FEATURES.map(([n], i) => [n, i]));
const UNKNOWN_COLOR = 0x9a9ea8;
const TRAVEL_COLOR = 0x3d8bfd;
const MAX_TYPES = 32;
const SEG_STRIDE = 10, TRAVEL_STRIDE = 8;
const POLL_MS = 2000;

const SETTING_LABELS = {
  layer_height: ['Layer height', 'mm'], first_layer_height: ['First layer', 'mm'], perimeters: ['Perimeters', ''],
  top_solid_layers: ['Top layers', ''], bottom_solid_layers: ['Bottom layers', ''], fill_density: ['Infill', ''],
  fill_pattern: ['Infill pattern', ''], brim_width: ['Brim', 'mm'], skirts: ['Skirts', ''],
  temperature: ['Nozzle', '°C'], first_layer_temperature: ['Nozzle, 1st layer', '°C'],
  bed_temperature: ['Bed', '°C'], filament_type: ['Filament type', ''], nozzle_diameter: ['Nozzle Ø', 'mm'],
};

// ---------- DOM ----------

const $ = (id) => document.getElementById(id);
const el = {
  job: $('job'), tabs: $('tabs'), canvas: $('c'), view: $('view'), legend: $('legend'), info: $('info'),
  layer: $('layer'), layerTop: $('layerTop'), layerBottom: $('layerBottom'), single: $('single'),
  move: $('move'), moveLabel: $('moveLabel'), status: $('status'),
};
const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const hexCss = (h) => '#' + h.toString(16).padStart(6, '0');
const rgb = (h) => new THREE.Vector3(((h >> 16) & 255) / 255, ((h >> 8) & 255) / 255, (h & 255) / 255);

// ---------- three.js scene ----------

const renderer = new THREE.WebGLRenderer({ canvas: el.canvas, antialias: true, preserveDrawingBuffer: false });
renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
const scene = new THREE.Scene();
scene.background = new THREE.Color(getComputedStyle(document.documentElement).getPropertyValue('--view-bg').trim() || '#1d1f23');
const camera = new THREE.PerspectiveCamera(35, 1, 0.5, 5000);
camera.up.set(0, 0, 1);
const controls = new OrbitControls(camera, el.canvas);
controls.enableDamping = true;
controls.dampingFactor = 0.15;
controls.screenSpacePanning = true;
controls.addEventListener('change', requestRender);

let bedGroup = null, bedKey = '', bedSize = { w: 180, d: 180, h: 180, cx: 90, cy: 90 };

function buildBed(shapeStr, maxH) {
  const key = `${shapeStr}|${maxH}`;
  if (key === bedKey) return;
  bedKey = key;
  if (bedGroup) { scene.remove(bedGroup); bedGroup.traverse((o) => { o.geometry?.dispose(); o.material?.dispose(); }); }
  let pts = (shapeStr || '0x0,180x0,180x180,0x180').split(',').map((p) => p.split('x').map(Number));
  if (pts.some((p) => p.length !== 2 || p.some(Number.isNaN))) pts = [[0, 0], [180, 0], [180, 180], [0, 180]];
  const xs = pts.map((p) => p[0]), ys = pts.map((p) => p[1]);
  const x0 = Math.min(...xs), x1 = Math.max(...xs), y0 = Math.min(...ys), y1 = Math.max(...ys);
  bedSize = { w: x1 - x0, d: y1 - y0, h: Number(maxH) || 180, cx: (x0 + x1) / 2, cy: (y0 + y1) / 2 };

  bedGroup = new THREE.Group();
  const shape = new THREE.Shape(pts.map(([x, y]) => new THREE.Vector2(x, y)));
  const plate = new THREE.Mesh(new THREE.ShapeGeometry(shape), new THREE.MeshBasicMaterial({ color: 0x2b2e34 }));
  plate.position.z = -0.02;
  bedGroup.add(plate);

  // Faint 1 cm grid, plus bold center lines that split the bed into 2 x 2 quarters.
  const mx = (x0 + x1) / 2, my = (y0 + y1) / 2;
  const minor = [], major = [x0, my, 0.005, x1, my, 0.005, mx, y0, 0.005, mx, y1, 0.005];
  for (let x = Math.ceil(x0 / 10) * 10; x <= x1; x += 10) if (Math.abs(x - mx) > 0.01) minor.push(x, y0, 0, x, y1, 0);
  for (let y = Math.ceil(y0 / 10) * 10; y <= y1; y += 10) if (Math.abs(y - my) > 0.01) minor.push(x0, y, 0, x1, y, 0);
  const lines = (arr, color) => {
    const g = new THREE.BufferGeometry();
    g.setAttribute('position', new THREE.Float32BufferAttribute(arr, 3));
    return new THREE.LineSegments(g, new THREE.LineBasicMaterial({ color }));
  };
  bedGroup.add(lines(minor, 0x33363d), lines(major, 0x6b717c));
  const outline = [];
  pts.forEach((p, i) => { const q = pts[(i + 1) % pts.length]; outline.push(p[0], p[1], 0, q[0], q[1], 0); });
  bedGroup.add(lines(outline, 0x6b707a));
  // origin marker: X red, Y green
  const axis = (dx, dy, color) => lines([x0, y0, 0.01, x0 + dx, y0 + dy, 0.01], color);
  bedGroup.add(axis(15, 0, 0xeb5757), axis(0, 15, 0x6fcf97));
  scene.add(bedGroup);
}

// Bounding box of the printed objects (without the purge line, which is 'Custom').
function printBox(data) {
  const custom = data.types.indexOf('Custom'), s = data.segs;
  let x0 = Infinity, y0 = Infinity, z0 = Infinity, x1 = -Infinity, y1 = -Infinity, z1 = -Infinity;
  for (let i = 0; i < data.nSeg; i++) {
    const o = i * SEG_STRIDE;
    if (s[o + 8] === custom) continue;
    for (const k of [0, 3]) {
      x0 = Math.min(x0, s[o + k]); x1 = Math.max(x1, s[o + k]);
      y0 = Math.min(y0, s[o + k + 1]); y1 = Math.max(y1, s[o + k + 1]);
      z0 = Math.min(z0, s[o + k + 2]); z1 = Math.max(z1, s[o + k + 2]);
    }
  }
  return x0 === Infinity ? null : { x0, y0, z0, x1, y1, z1 };
}

function setView(kind) {
  const { w, d, cx, cy } = bedSize, r = Math.max(w, d);
  const target = new THREE.Vector3(cx, cy, 0);
  if (kind === 'fit') {
    const b = state.shown && (state.shown.box ??= printBox(state.shown.data));
    if (!b) return;
    const c = new THREE.Vector3((b.x0 + b.x1) / 2, (b.y0 + b.y1) / 2, (b.z0 + b.z1) / 2);
    const size = Math.max(b.x1 - b.x0, b.y1 - b.y0, b.z1 - b.z0, 10);
    const dirv = camera.position.clone().sub(controls.target).normalize();
    const dist = (size * 0.75) / Math.tan(THREE.MathUtils.degToRad(camera.fov / 2)) + size * 0.5;
    controls.target.copy(c);
    camera.position.copy(c).addScaledVector(dirv, dist);
    controls.update();
    requestRender();
    return;
  }
  if (kind === 'top') camera.position.set(cx, cy - 0.001, r * 2.3);
  else if (kind === 'front') camera.position.set(cx, cy - r * 2.2, r * 0.25);
  else camera.position.set(cx - r * 0.55, cy - r * 1.55, r * 1.25);
  controls.target.copy(target);
  controls.update();
  requestRender();
}

// One unit box per segment, stretched along the segment in the vertex shader:
// x 0..1 along the move, y -0.5..0.5 across (x width), z -1..0 downwards (x layer height).
function unitBox() {
  const faces = [
    [[0, 0, 1], [[0, -.5, 0], [1, -.5, 0], [1, .5, 0], [0, .5, 0]]],
    [[0, 0, -1], [[0, -.5, -1], [0, .5, -1], [1, .5, -1], [1, -.5, -1]]],
    [[0, 1, 0], [[0, .5, -1], [0, .5, 0], [1, .5, 0], [1, .5, -1]]],
    [[0, -1, 0], [[0, -.5, -1], [1, -.5, -1], [1, -.5, 0], [0, -.5, 0]]],
    [[1, 0, 0], [[1, -.5, -1], [1, .5, -1], [1, .5, 0], [1, -.5, 0]]],
    [[-1, 0, 0], [[0, -.5, -1], [0, -.5, 0], [0, .5, 0], [0, .5, -1]]],
  ];
  const pos = [], nor = [], idx = [];
  faces.forEach(([n, vs], f) => {
    vs.forEach((v) => { pos.push(...v); nor.push(...n); });
    idx.push(4 * f, 4 * f + 1, 4 * f + 2, 4 * f, 4 * f + 2, 4 * f + 3);
  });
  return { pos: new Float32Array(pos), nor: new Float32Array(nor), idx };
}
const BOX = unitBox();

const uniforms = {
  uLayerMin: { value: 0 }, uLayerMax: { value: 1e9 }, uSegMax: { value: 1e9 },
  uColors: { value: Array.from({ length: MAX_TYPES }, () => new THREE.Vector3(0.6, 0.6, 0.6)) },
  uVis: { value: new Array(MAX_TYPES).fill(1) },
};
const travelUniforms = {
  uLayerMin: uniforms.uLayerMin, uLayerMax: uniforms.uLayerMax, uSegMax: uniforms.uSegMax,
  uColor: { value: rgb(TRAVEL_COLOR) },
};

const extrusionMaterial = new THREE.ShaderMaterial({
  glslVersion: THREE.GLSL3,
  uniforms,
  side: THREE.DoubleSide,
  vertexShader: /* glsl */`
    in vec3 iStart; in vec3 iEnd; in vec2 iWH; in vec2 iMeta;
    uniform float uLayerMin, uLayerMax, uSegMax;
    uniform vec3 uColors[${MAX_TYPES}];
    uniform float uVis[${MAX_TYPES}];
    out vec3 vColor; out vec3 vNormal;
    void main() {
      int t = int(iMeta.x + 0.5);
      float layer = iMeta.y;
      if (layer < uLayerMin || layer > uLayerMax || float(gl_InstanceID) >= uSegMax || uVis[t] < 0.5) {
        gl_Position = vec4(2.0, 2.0, 2.0, 1.0);   // outside the clip volume -> not drawn
        return;
      }
      vec3 d = iEnd - iStart;
      float L = length(d);
      vec3 dir = L > 1e-5 ? d / L : vec3(1.0, 0.0, 0.0);
      vec3 side = cross(vec3(0.0, 0.0, 1.0), dir);
      float sl = length(side);
      side = sl > 1e-4 ? side / sl : vec3(0.0, 1.0, 0.0);
      vec3 up = cross(dir, side);
      float ext = 0.5 * iWH.x;                      // overlap at the ends so corners look closed
      vec3 p = iStart + dir * mix(-ext, L + ext, position.x)
             + side * (position.y * iWH.x) + up * (position.z * iWH.y);
      vNormal = normalize(normalMatrix * (dir * normal.x + side * normal.y + up * normal.z));
      vColor = uColors[t];
      gl_Position = projectionMatrix * modelViewMatrix * vec4(p, 1.0);
    }`,
  fragmentShader: /* glsl */`
    in vec3 vColor; in vec3 vNormal;
    out vec4 outColor;
    void main() {
      vec3 n = normalize(vNormal);
      if (!gl_FrontFacing) n = -n;
      float key = max(dot(n, normalize(vec3(0.35, 0.6, 0.75))), 0.0);
      float fill = max(dot(n, normalize(vec3(-0.6, -0.2, 0.4))), 0.0);
      outColor = vec4(vColor * (0.32 + 0.6 * key + 0.18 * fill), 1.0);
    }`,
});

const travelMaterial = new THREE.ShaderMaterial({
  glslVersion: THREE.GLSL3,
  uniforms: travelUniforms,
  vertexShader: /* glsl */`
    in vec2 aMeta;
    uniform float uLayerMin, uLayerMax, uSegMax;
    void main() {
      if (aMeta.x < uLayerMin || aMeta.x > uLayerMax || aMeta.y > uSegMax) { gl_Position = vec4(2.0, 2.0, 2.0, 1.0); return; }
      gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0);
    }`,
  fragmentShader: /* glsl */`
    uniform vec3 uColor; out vec4 outColor;
    void main() { outColor = vec4(uColor, 1.0); }`,
});

function buildMeshes(data) {
  const out = { extrusion: null, travel: null };
  if (data.nSeg > 0) {
    const g = new THREE.InstancedBufferGeometry();
    g.setAttribute('position', new THREE.BufferAttribute(BOX.pos, 3));
    g.setAttribute('normal', new THREE.BufferAttribute(BOX.nor, 3));
    g.setIndex(BOX.idx);
    const ib = new THREE.InstancedInterleavedBuffer(data.segs, SEG_STRIDE, 1);
    g.setAttribute('iStart', new THREE.InterleavedBufferAttribute(ib, 3, 0));
    g.setAttribute('iEnd', new THREE.InterleavedBufferAttribute(ib, 3, 3));
    g.setAttribute('iWH', new THREE.InterleavedBufferAttribute(ib, 2, 6));
    g.setAttribute('iMeta', new THREE.InterleavedBufferAttribute(ib, 2, 8));
    g.instanceCount = data.nSeg;
    out.extrusion = new THREE.Mesh(g, extrusionMaterial);
    out.extrusion.frustumCulled = false;
  }
  const nT = data.travels.length / TRAVEL_STRIDE;
  if (nT > 0) {
    const pos = new Float32Array(nT * 6), meta = new Float32Array(nT * 4), t = data.travels;
    for (let i = 0; i < nT; i++) {
      const s = i * TRAVEL_STRIDE;
      pos.set(t.subarray(s, s + 6), i * 6);
      meta[i * 4] = meta[i * 4 + 2] = t[s + 6];
      meta[i * 4 + 1] = meta[i * 4 + 3] = t[s + 7];
    }
    const g = new THREE.BufferGeometry();
    g.setAttribute('position', new THREE.BufferAttribute(pos, 3));
    g.setAttribute('aMeta', new THREE.BufferAttribute(meta, 2));
    out.travel = new THREE.LineSegments(g, travelMaterial);
    out.travel.frustumCulled = false;
  }
  return out;
}

const marker = new THREE.Mesh(new THREE.ConeGeometry(0.9, 3, 16), new THREE.MeshBasicMaterial({ color: 0xfa6831 }));
marker.rotation.x = -Math.PI / 2;   // tip pointing down (-z)
marker.visible = false;
scene.add(marker);

let renderQueued = false;
function requestRender() {
  if (renderQueued) return;
  renderQueued = true;
  requestAnimationFrame(() => {
    renderQueued = false;
    if (controls.update()) requestRender();   // damping still moving
    renderer.render(scene, camera);
  });
}

function resize() {
  const { clientWidth: w, clientHeight: h } = el.view;
  if (!w || !h) return;
  renderer.setSize(w, h, false);
  camera.aspect = w / h;
  camera.updateProjectionMatrix();
  requestRender();
}
new ResizeObserver(resize).observe(el.view);

// ---------- worker ----------

const worker = new Worker('gcode-worker.js');
const pending = new Map();
let reqId = 0;
worker.onmessage = ({ data }) => {
  const p = pending.get(data.id);
  if (!p) return;
  if (data.progress !== undefined) p.onProgress(data.progress);
  else { pending.delete(data.id); data.error ? p.reject(new Error(data.error)) : p.resolve(data.result); }
};
function parseGcode(url, onProgress) {
  return new Promise((resolve, reject) => {
    const id = ++reqId;
    pending.set(id, { resolve, reject, onProgress });
    worker.postMessage({ id, url });
  });
}

// ---------- state ----------

const params = new URLSearchParams(location.search);
const state = {
  follow: !params.get('job'),
  jobs: [],
  job: null,          // {name, beds: [...]}
  bed: params.get('bed'),
  cache: new Map(),   // bed name -> {mtime, data, meshes}
  shown: null,        // cache entry currently in the scene
  typeVisible: new Map(), // feature name -> bool (kept across beds)
  showTravel: false,
  loadToken: 0,
  viewSet: false,
};

function showStatus(msg, { progress = null, error = false } = {}) {
  el.status.className = 'show' + (error ? ' error' : '') + (progress === null ? ' idle' : '');
  el.status.querySelector('.msg').textContent = msg;
  if (progress !== null) el.status.querySelector('.bar div').style.width = `${Math.round(progress * 100)}%`;
}
const hideStatus = () => { el.status.className = ''; };

function updateUrl() {
  const p = new URLSearchParams();
  if (!state.follow && state.job) p.set('job', state.job.name);
  if (state.bed) p.set('bed', state.bed);
  const q = p.toString();
  history.replaceState(null, '', q ? `?${q}` : location.pathname);
}

// ---------- jobs + beds ----------

async function getJSON(url) {
  const r = await fetch(url, { cache: 'no-store' });
  if (!r.ok) throw new Error(`${url}: ${r.status}`);
  return r.json();
}

function renderJobSelect() {
  const cur = state.job?.name;
  const opts = [`<option value="">Newest job (follow)</option>`].concat(state.jobs.map((j) =>
    `<option value="${esc(j.name)}">${esc(j.name)} · ${j.beds.length} bed${j.beds.length === 1 ? '' : 's'}</option>`));
  el.job.innerHTML = opts.join('');
  el.job.value = state.follow ? '' : (cur ?? '');
  if (state.follow && cur) el.job.options[0].textContent = `Newest: ${cur}`;
}

function renderTabs() {
  const beds = state.job?.beds ?? [];
  el.tabs.innerHTML = beds.map((b) =>
    `<button data-bed="${esc(b.name)}" class="${b.name === state.bed ? 'active' : ''}" role="tab" aria-selected="${b.name === state.bed}">
       ${esc(b.name)}<small>${esc(b.error ? 'failed' : (b.print_time ?? ''))}</small></button>`).join('');
}

async function selectJob(name, { keepBed = false } = {}) {
  let job;
  try { job = await getJSON(`/api/jobs/${encodeURIComponent(name)}`); }
  catch (e) { showStatus(`Could not load job ${name}: ${e.message}`, { error: true }); return; }
  const changedJob = state.job?.name !== job.name;
  if (changedJob) clearCache();
  state.job = job;
  if (!keepBed || !job.beds.some((b) => b.name === state.bed)) {
    state.bed = job.beds.some((b) => b.name === state.bed) && !changedJob ? state.bed : job.beds[0]?.name ?? null;
  }
  renderJobSelect();
  renderTabs();
  updateUrl();
  await showBed();
}

function clearCache() {
  for (const c of state.cache.values()) disposeEntry(c);
  state.cache.clear();
  if (state.shown) state.shown = null;
}
function disposeEntry(c) {
  for (const m of Object.values(c.meshes ?? {})) if (m) { scene.remove(m); m.geometry.dispose(); }
}

async function showBed() {
  const bed = state.job?.beds.find((b) => b.name === state.bed);
  document.getElementById('send').disabled = !(bed && bed.gcode);
  if (state.shown) {
    for (const m of Object.values(state.shown.meshes)) if (m) scene.remove(m);
    state.shown = null;
  }
  renderInfo(bed);
  el.view.classList.add('nodata');   // hide sliders/legend until this bed's toolpaths are in
  marker.visible = false;
  if (!bed) {
    el.legend.innerHTML = '';
    showStatus(state.jobs.length ? 'This job has no beds.' : 'No jobs yet. Slice something into ~/print-jobs.');
    requestRender();
    return;
  }
  buildBed(bed.settings?.bed_shape, bed.settings?.max_print_height);
  if (!state.viewSet) { setView('iso'); state.viewSet = true; }

  const token = ++state.loadToken;
  if (!bed.gcode) {   // slicing failed: only bed-N.json with the error exists
    el.legend.innerHTML = '';
    showStatus(`${bed.name}: slicing failed. ${bed.error ?? ''}`, { error: true });
    requestRender();
    return;
  }
  let entry = state.cache.get(bed.name);
  if (!entry || entry.mtime !== bed.mtime) {
    if (entry) { disposeEntry(entry); state.cache.delete(bed.name); }
    showStatus(`Loading ${bed.name}…`, { progress: 0 });
    let data;
    try {
      data = await parseGcode(bed.gcode, (p) => token === state.loadToken && showStatus(`Reading ${bed.name}…`, { progress: p }));
    } catch (e) {
      if (token === state.loadToken) showStatus(`Could not read ${bed.name}: ${e.message}`, { error: true });
      return;
    }
    entry = { mtime: bed.mtime, data, meshes: buildMeshes(data) };
    state.cache.set(bed.name, entry);
    if (token !== state.loadToken) return;   // user switched away meanwhile
  }
  hideStatus();
  el.view.classList.remove('nodata');
  state.shown = entry;
  for (const m of Object.values(entry.meshes)) if (m) scene.add(m);
  applyTypes(entry.data);
  renderLegend(entry.data);
  setupLayerSlider(entry.data);
  requestRender();
}

// ---------- legend / visibility ----------

function applyTypes(data) {
  data.types.forEach((name, i) => {
    if (i >= MAX_TYPES) return;
    uniforms.uColors.value[i].copy(rgb(FEATURE_COLOR.get(name) ?? UNKNOWN_COLOR));
    uniforms.uVis.value[i] = state.typeVisible.get(name) === false ? 0 : 1;
  });
  if (state.shown?.meshes.travel) state.shown.meshes.travel.visible = state.showTravel;
}

function renderLegend(data) {
  const items = data.types.map((name, i) => ({ name, i, len: data.typeLength[i] }))
    .filter((t) => t.len > 0)
    .sort((a, b) => (FEATURE_ORDER.get(a.name) ?? 99) - (FEATURE_ORDER.get(b.name) ?? 99));
  const fmt = (mm) => mm >= 1000 ? `${(mm / 1000).toFixed(1)} m` : `${mm.toFixed(0)} mm`;
  el.legend.innerHTML = items.map((t) => {
    const on = state.typeVisible.get(t.name) !== false;
    return `<label class="${on ? '' : 'off'}"><input type="checkbox" data-type="${esc(t.name)}" ${on ? 'checked' : ''}>
      <span class="sw" style="background:${hexCss(FEATURE_COLOR.get(t.name) ?? UNKNOWN_COLOR)}"></span>
      <span class="name">${esc(t.name)}</span><span class="len">${fmt(t.len)}</span></label>`;
  }).join('') + (data.travels.length ? `<hr><label class="${state.showTravel ? '' : 'off'}">
      <input type="checkbox" data-travel ${state.showTravel ? 'checked' : ''}>
      <span class="sw" style="background:${hexCss(TRAVEL_COLOR)}"></span><span class="name">Travel</span>
      <span class="len">${data.travels.length / TRAVEL_STRIDE} moves</span></label>` : '');
}

el.legend.addEventListener('change', (ev) => {
  const t = ev.target;
  if (t.dataset.travel !== undefined) state.showTravel = t.checked;
  else if (t.dataset.type) state.typeVisible.set(t.dataset.type, t.checked);
  t.closest('label').classList.toggle('off', !t.checked);
  if (state.shown) applyTypes(state.shown.data);
  requestRender();
});

// ---------- layer + move sliders ----------

function setupLayerSlider(data) {
  const n = data.layerZ.length;
  el.layer.max = String(n - 1);
  el.layer.value = String(n - 1);
  updateLayers();
}

function updateLayers({ keepMove = false } = {}) {
  const data = state.shown?.data;
  if (!data) return;
  const top = Number(el.layer.value), n = data.layerZ.length;
  uniforms.uLayerMax.value = top;
  uniforms.uLayerMin.value = el.single.checked ? top : 0;
  el.layerTop.textContent = `${top + 1}\n${data.layerZ[top].toFixed(2)}`;
  el.layerBottom.textContent = `${el.single.checked ? top + 1 : 1}`;
  el.layer.title = `Layer ${top + 1} of ${n}, Z ${data.layerZ[top].toFixed(2)} mm`;
  const first = data.layerFirst[top], end = data.layerEnd[top];
  el.move.min = String(first);
  el.move.max = String(end);
  if (!keepMove) el.move.value = String(end);
  updateMove();
}

function updateMove() {
  const data = state.shown?.data;
  if (!data) return;
  const v = Number(el.move.value), first = Number(el.move.min), end = Number(el.move.max);
  uniforms.uSegMax.value = v;
  // Segments are in print order, so everything above the slider can simply be left out of the draw call.
  const ext = state.shown.meshes.extrusion;
  if (ext) ext.geometry.instanceCount = Math.max(0, Math.min(v, data.nSeg));
  el.moveLabel.textContent = end > first ? `move ${v - first} / ${end - first}` : 'no extrusions';
  if (v < end && v > 0) {
    const s = (v - 1) * SEG_STRIDE;
    marker.position.set(data.segs[s + 3], data.segs[s + 4], data.segs[s + 5] + 1.5);
    marker.visible = true;
  } else marker.visible = false;
  requestRender();
}

el.layer.addEventListener('input', () => updateLayers());
el.single.addEventListener('change', () => updateLayers({ keepMove: true }));
el.move.addEventListener('input', updateMove);

function step(input, d) {
  const v = Math.min(Number(input.max), Math.max(Number(input.min), Number(input.value) + d));
  input.value = String(v);
  input.dispatchEvent(new Event('input'));
}

document.addEventListener('keydown', (ev) => {
  if (document.getElementById('sendDialog').open || document.getElementById('deleteDialog').open) return;
  const tag = ev.target.tagName;
  if (tag === 'SELECT' || (tag === 'INPUT' && ev.target.type !== 'checkbox')) return;
  const k = ev.shiftKey ? 10 : 1;
  if (ev.key === 'ArrowUp') step(el.layer, k);
  else if (ev.key === 'ArrowDown') step(el.layer, -k);
  else if (ev.key === 'ArrowRight') step(el.move, k);
  else if (ev.key === 'ArrowLeft') step(el.move, -k);
  else if (ev.key === 'Home') { el.layer.value = el.layer.min; updateLayers(); }
  else if (ev.key === 'End') { el.layer.value = el.layer.max; updateLayers(); }
  else if (ev.key === 's' || ev.key === 'S') { el.single.checked = !el.single.checked; updateLayers({ keepMove: true }); }
  else if (ev.key === '1') setView('iso');
  else if (ev.key === '2') setView('top');
  else if (ev.key === '3') setView('front');
  else if (ev.key === 'z' || ev.key === 'Z') setView('fit');
  else return;
  ev.preventDefault();
});

document.getElementById('views').addEventListener('click', (ev) => {
  const v = ev.target.dataset?.view;
  if (v) setView(v);
});

// ---------- info panel ----------

function fmtSetting(key, v, s) {
  const [, unit] = SETTING_LABELS[key] ?? ['', ''];
  if (key === 'temperature' || key === 'first_layer_temperature' || key === 'bed_temperature') return `${v} ${unit}`;
  return unit && v !== '' ? `${v} ${unit}` : v;
}

function supportsText(s) {
  if (s.support_material === undefined) return s.support_material_threshold ? `threshold ${s.support_material_threshold}°` : null;
  if (s.support_material !== '1') return 'off';
  // PrusaSlicer's default: supports only where painted. Nothing is painted from the CLI, so effectively off.
  if (s.support_material_auto === '0') return 'only where painted';
  const parts = ['auto'];
  if (s.support_material_buildplate_only === '1') parts.push('build plate only');
  if (s.support_material_threshold && s.support_material_threshold !== '0') parts.push(`${s.support_material_threshold}°`);
  return 'on, ' + parts.join(', ');
}

function renderInfo(bed) {
  if (!bed) { el.info.innerHTML = `<div class="empty">${state.jobs.length ? 'No bed selected.' : 'No print jobs yet.'}</div>`; return; }
  const s = bed.settings ?? {};
  const overrides = new Set(Object.keys(bed.overrides ?? {}).map((k) => k.replace(/-/g, '_')));
  const ch = (k) => overrides.has(k) ? ' class="changed"' : '';
  const stat = (v, k, wide) => `<div class="stat${wide ? ' wide' : ''}"><div class="v">${esc(v ?? '–')}</div><div class="k">${esc(k)}</div></div>`;
  const filament = bed.filament_g != null ? `${bed.filament_g.toFixed(1)} g` : null;

  const rows = Object.keys(SETTING_LABELS).filter((k) => s[k] !== undefined && s[k] !== '')
    .map((k) => `<dt>${esc(SETTING_LABELS[k][0])}</dt><dd${ch(k)}>${esc(fmtSetting(k, s[k], s))}</dd>`);
  const sup = supportsText(s);
  if (sup) rows.splice(7, 0, `<dt>Supports</dt><dd${['support_material', 'support_material_auto', 'support_material_buildplate_only', 'support_material_threshold'].some((k) => overrides.has(k)) ? ' class="changed"' : ''}>${esc(sup)}</dd>`);

  if (!bed.gcode) {
    el.info.innerHTML = `<h2>${esc(bed.name)}</h2><div class="sub">${esc(state.job.name)}</div>
      <h3>Slicing failed</h3><p class="err">${esc(bed.error ?? 'no G-code was written')}</p>
      ${bed.print_profile ? `<dl class="kv"><dt>Print profile</dt><dd>${esc(bed.print_profile)}</dd></dl>` : ''}`;
    return;
  }
  const warnings = bed.warnings ?? [];
  el.info.innerHTML = `
    <h2>${esc(bed.name)}</h2>
    <div class="sub">${esc(state.job.name)}${bed.gcode ? ` · ${(bed.size / 1e6).toFixed(1)} MB` : ''}${bed.slice_seconds ? ` · sliced in ${bed.slice_seconds} s` : ''}</div>
    ${bed.error ? `<p class="err">${esc(bed.error)}</p>` : ''}
    ${bed.thumbnail ? `<img class="thumb" src="${esc(bed.thumbnail)}" alt="Thumbnail stored in the G-code">` : ''}
    <div class="stats">
      ${stat(bed.print_time, 'print time', true)}
      ${stat(filament, `filament${bed.filament_m != null ? ` · ${bed.filament_m} m` : ''}`)}
      ${stat(bed.layers, 'layers')}
    </div>

    <h3>Profiles</h3>
    <dl class="kv">
      <dt>Printer</dt><dd>${esc(s.printer_settings_id ?? '–')}</dd>
      <dt>Print</dt><dd>${esc(s.print_settings_id ?? '–')}</dd>
      <dt>Filament</dt><dd>${esc(s.filament_settings_id ?? '–')}</dd>
    </dl>

    <h3>Settings used</h3>
    <dl class="kv">${rows.join('')}</dl>
    ${overrides.size ? `<div class="note"><span class="dot">●</span> changed from the profile</div>` : ''}

    <h3>Objects</h3>
    <ul class="plain">${(bed.objects ?? []).map((o) => {
      const os = bed.object_settings?.[o];
      if (!os) return `<li>${esc(o)}</li>`;
      const sup = supportsText(os);
      const rest = Object.entries(os).filter(([k]) => !k.startsWith('support_material')).map(([k, v]) => `${k} ${v}`);
      const txt = [sup ? `supports ${sup}` : null, ...rest].filter(Boolean).join(', ');
      return `<li>${esc(o)}<div class="note"><span class="dot">●</span> ${esc(txt)}</div></li>`;
    }).join('') || '<li class="note">–</li>'}</ul>

    <h3>Warnings</h3>
    ${warnings.length ? `<ul class="plain warn">${warnings.map((w) => `<li>${esc(w)}</li>`).join('')}</ul>` : '<div class="ok">None reported</div>'}
  `;
}

// ---------- events ----------

el.tabs.addEventListener('click', (ev) => {
  const b = ev.target.closest('button[data-bed]');
  if (!b || b.dataset.bed === state.bed) return;
  state.bed = b.dataset.bed;
  renderTabs();
  updateUrl();
  showBed();
});

el.job.addEventListener('change', () => {
  const v = el.job.value;
  state.follow = v === '';
  const name = v || state.jobs[0]?.name;
  if (name) selectJob(name);
});

// Poll the jobs folder: new jobs (when following), re-sliced beds, added/removed beds.
async function poll() {
  try {
    const jobs = await getJSON('/api/jobs');
    const listChanged = JSON.stringify(jobs.map((j) => [j.name, j.beds])) !== JSON.stringify(state.jobs.map((j) => [j.name, j.beds]));
    state.jobs = jobs;
    if (state.follow && jobs[0] && jobs[0].name !== state.job?.name) {
      await selectJob(jobs[0].name);
    } else if (state.job) {
      const cur = jobs.find((j) => j.name === state.job.name);
      if (cur) {
        const fresh = await getJSON(`/api/jobs/${encodeURIComponent(state.job.name)}`);
        const sig = (j) => JSON.stringify(j.beds.map((b) => [b.name, b.mtime]));
        if (sig(fresh) !== sig(state.job)) {
          state.job = fresh;
          if (!fresh.beds.some((b) => b.name === state.bed)) state.bed = fresh.beds[0]?.name ?? null;
          renderTabs();
          await showBed();
        }
      }
      if (listChanged) renderJobSelect();
    } else if (listChanged) renderJobSelect();
  } catch { /* server restarting; try again next tick */ }
  setTimeout(poll, POLL_MS);
}

// ---------- send to printer (Prusa Connect) ----------

const dlg = document.getElementById('sendDialog');
const dlgBody = document.getElementById('sendBody');
const dlgActions = document.getElementById('sendActions');
let connectInfo = null;

async function post(url, body) {
  const r = await fetch(url, { method: 'POST', headers: { 'Content-Type': 'application/json', 'X-Workbench': '1' },
    body: JSON.stringify(body ?? {}) });
  return r.json().catch(() => ({ ok: false, error: `HTTP ${r.status}` }));
}

function currentBed() { return state.job?.beds.find((b) => b.name === state.bed); }

function dlgSet(body, actions) {
  dlgBody.innerHTML = body;
  dlgActions.innerHTML = actions;
}

function modelMismatch(bed, printer) {
  const norm = (x) => (x || '').toUpperCase().replace(/[^A-Z0-9]/g, '');
  const p = norm(printer?.model);
  const model = bed.settings?.printer_model, profile = bed.settings?.printer_settings_id;
  if (!p || !(model || profile)) return null;
  const g = norm(model);
  const ok = g ? (g.includes(p) || p.includes(g)) : norm(profile).includes(p);   // older summaries: profile name only
  return ok ? null : `The G-code was sliced for ${model || profile}, this printer is a ${printer.model}.`;
}

async function openSend() {
  const bed = currentBed();
  if (!bed?.gcode) return;
  dlg.showModal();
  dlgSet('<p><span class="spinner"></span>Checking Prusa Connect…</p>', '<button value="cancel">Cancel</button>');
  connectInfo = await fetch('/api/connect', { cache: 'no-store' }).then((r) => r.json()).catch((e) => ({ logged_in: false, error: String(e) }));
  if (!dlg.open) return;
  connectInfo.logged_in ? renderSendForm(bed) : renderLogin(connectInfo.error);
}

function renderLogin(error) {
  dlgSet(`<p>Sending goes through <b>Prusa Connect</b>. Log in once with your Prusa Account; a sign-in window opens on your desktop.</p>
    ${error ? `<p class="warn">${esc(error)}</p>` : ''}`,
    `<button value="cancel">Cancel</button><button type="button" class="primary" id="loginBtn">Log in to Prusa Connect</button>`);
}

async function doLogin() {
  const r = await post('/api/connect/login');
  if (r.ok === false) { renderLogin(r.error); return; }
  dlgSet('<p><span class="spinner"></span>Sign in in the window that opened on your desktop…</p>', '<button value="cancel">Cancel</button>');
  for (;;) {
    await new Promise((res) => setTimeout(res, 1000));
    if (!dlg.open) return;
    const st = await fetch('/api/connect/login', { cache: 'no-store' }).then((x) => x.json()).catch(() => null);
    if (st && !st.running) {
      if (st.result?.ok) { openSend(); } else { renderLogin(st.result?.error || 'Login window closed.'); }
      return;
    }
  }
}

function renderSendForm(bed) {
  const printers = connectInfo.printers || [];
  if (!printers.length) {
    dlgSet(`<p>Logged in, but no printers were found in your Prusa Connect account.</p>
      ${connectInfo.error ? `<p class="warn">${esc(connectInfo.error)}</p>` : ''}`,
      `<button type="button" class="link left" id="logoutBtn">Log out</button><button value="cancel">Close</button>`);
    return;
  }
  const def = printers.find((p) => p.uuid === connectInfo.default_printer) ?? printers[0];
  const warnings = bed.warnings ?? [];
  dlgSet(`
    <div class="what"><b>${esc(state.job.name)} / ${esc(bed.name)}</b><br>
      ${esc(bed.print_time ?? '')}${bed.filament_g != null ? ` · ${bed.filament_g.toFixed(1)} g` : ''} ·
      ${esc(bed.settings?.print_settings_id ?? '')} · ${esc(bed.settings?.filament_settings_id ?? '')}</div>
    ${warnings.map((w) => `<p class="warn">${esc(w)}</p>`).join('')}
    <label class="field" for="printerSel">Printer</label>
    <select id="printerSel">${printers.map((p) => `<option value="${esc(p.uuid)}" ${p.uuid === def.uuid ? 'selected' : ''}>
      ${esc(p.name)}${p.model ? ` — ${esc(p.model)}` : ''}${p.state ? ` — ${esc(p.state)}` : ''}</option>`).join('')}</select>
    <div id="printerWarn"></div>
    <label class="field" for="fileName">File name on the printer</label>
    <input type="text" id="fileName" value="${esc(`${state.job.name}-${bed.name}`)}" spellcheck="false">
    <fieldset class="actionsel">
      <legend class="field">After uploading</legend>
      <label><input type="radio" name="sendAction" value="upload"> Just upload</label>
      <label><input type="radio" name="sendAction" value="queue"> Add to the end of the queue</label>
      <label><input type="radio" name="sendAction" value="print"> Print now (first in the queue)</label>
    </fieldset>
    <p class="note" id="actionHint"></p>`,
    `<button type="button" class="link left" id="logoutBtn">Log out</button>
     <button value="cancel">Cancel</button><button type="button" class="primary" id="uploadBtn">Upload</button>`);
  const sel = document.getElementById('printerSel');
  const radios = [...document.querySelectorAll('input[name=sendAction]')];
  (radios.find((r) => r.value === connectInfo.default_action) ?? radios[0]).checked = true;
  const update = () => {
    const p = printers.find((x) => x.uuid === sel.value);
    const msgs = [modelMismatch(bed, p), p?.state && /OFFLINE/i.test(p.state) ? 'The printer is offline in Prusa Connect.' : null].filter(Boolean);
    document.getElementById('printerWarn').innerHTML = msgs.map((m) => `<p class="warn" style="margin-top:6px">${esc(m)}</p>`).join('');
    const canQueue = p?.queue !== false;
    radios.filter((r) => r.value !== 'upload').forEach((r) => { r.disabled = !canQueue; if (!canQueue && r.checked) radios[0].checked = true; });
    const action = radios.find((r) => r.checked).value;
    const st = (p?.state || '').toUpperCase();
    const hint = {
      upload: 'The file goes onto the printer. Start it on the printer or in Prusa Connect.',
      queue: 'The file goes onto the printer and to the end of its queue in Prusa Connect.',
      print: st === 'READY'
        ? 'The printer is READY: it starts printing as soon as the file has arrived.'
        : `The printer is ${st || 'not ready'}. It starts once someone presses "Set ready" on the printer or in Prusa Connect (that's the "bed is clear" check).`,
    }[action];
    document.getElementById('actionHint').textContent = canQueue ? hint : hint + ' (This printer has no queue in Prusa Connect.)';
  };
  sel.addEventListener('change', update);
  radios.forEach((r) => r.addEventListener('change', update));
  update();
}

async function doUpload() {
  const bed = currentBed();
  const uuid = document.getElementById('printerSel').value;
  const filename = document.getElementById('fileName').value;
  const action = document.querySelector('input[name=sendAction]:checked')?.value ?? 'upload';
  const printer = (connectInfo.printers || []).find((p) => p.uuid === uuid);
  dlgSet(`<p><span class="spinner"></span>Preparing binary G-code and uploading to ${esc(printer?.name ?? 'the printer')}…</p>`, '');
  const r = await post('/api/send', { job: state.job.name, bed: bed.name, printer_uuid: uuid, filename, action });
  if (r.ok) {
    const mb = (r.size / 1e6).toFixed(2);
    const link = `<a href="${esc(r.printer_page)}" target="_blank" rel="noopener">Prusa Connect</a>`;
    let next;
    if (r.queue_error) next = `<p class="warn">Uploaded, but adding it to the queue failed: ${esc(r.queue_error)}</p><p>Start it on the printer or in ${link}.</p>`;
    else if (action === 'print') next = (r.printer_state || '').toUpperCase() === 'READY'
      ? `<p>It's first in the queue and starts as soon as the file has reached the printer. Follow it in ${link}.</p>`
      : `<p>It's first in the queue. The printer is ${esc(r.printer_state || 'not ready')}: press <b>Set ready</b> on the printer or in ${link} when the bed is clear, and it starts.</p>`;
    else if (action === 'queue') next = `<p>Added to the queue (position ${(r.queue?.position ?? 0) + 1}). See it in ${link}.</p>`;
    else next = `<p>Start it on the printer, or in ${link}.</p>`;
    dlgSet(`<p class="ok">Uploaded to ${esc(printer?.name ?? 'the printer')}${r.path ? ` as <b>${esc(r.path)}</b>` : ''}.</p>
      <p class="note">${r.format === 'binary' ? 'Binary G-code' : 'Text G-code'}, ${mb} MB, to Connect in ${r.seconds} s.
        Connect now copies it to the printer over its own network link.${r.note ? ` (${esc(r.note)})` : ''}</p>
      ${next}`,
      '<button value="cancel" class="primary">Done</button>');
  } else if (r.login_required) {
    renderLogin(r.error);
  } else {
    dlgSet(`<p class="err">Upload failed.</p><p>${esc(r.error)}</p>`,
      '<button value="cancel">Close</button><button type="button" class="primary" id="retryBtn">Try again</button>');
  }
}

document.getElementById('send').addEventListener('click', openSend);
dlg.addEventListener('click', async (ev) => {
  const id = ev.target.id;
  if (id === 'loginBtn') doLogin();
  else if (id === 'uploadBtn') doUpload();
  else if (id === 'retryBtn') openSend();
  else if (id === 'logoutBtn') { await post('/api/connect/logout'); renderLogin(); }
});

// ---------- delete jobs ----------

const delDlg = document.getElementById('deleteDialog');
const delList = document.getElementById('deleteList');
const delBtn = document.getElementById('deleteBtn');
const delAll = document.getElementById('deleteAll');

function delChecked() { return [...delList.querySelectorAll('input:checked')].map((i) => i.value); }
function delUpdate() {
  const n = delChecked().length, total = delList.querySelectorAll('input').length;
  delBtn.disabled = n === 0;
  delBtn.textContent = n ? `Delete ${n} job${n === 1 ? '' : 's'}` : 'Delete';
  delAll.checked = n > 0 && n === total;
  delAll.indeterminate = n > 0 && n < total;
}

function openDelete() {
  const when = (t) => new Date(t * 1000).toLocaleString([], { dateStyle: 'short', timeStyle: 'short' });
  delList.innerHTML = state.jobs.length ? state.jobs.map((j) => `
    <label><input type="checkbox" value="${esc(j.name)}" ${j.name === state.job?.name ? 'checked' : ''}>
      <span>${esc(j.name)}${j.name === state.job?.name ? '<span class="cur">shown now</span>' : ''}</span>
      <span class="meta">${j.beds.length} bed${j.beds.length === 1 ? '' : 's'} · ${esc(when(j.mtime))}</span></label>`).join('')
    : '<p class="note" style="padding:10px">No jobs.</p>';
  delUpdate();
  delDlg.showModal();
}

async function doDelete() {
  const names = delChecked();
  if (!names.length) return;
  delBtn.disabled = true;
  delBtn.textContent = 'Deleting…';
  const r = await post('/api/jobs/delete', { names });
  const errors = Object.entries(r.errors || {});
  if (errors.length) alert('Could not delete:\n' + errors.map(([n, e]) => `${n}: ${e}`).join('\n'));
  delDlg.close();
  state.jobs = await getJSON('/api/jobs').catch(() => state.jobs);
  if (state.job && !state.jobs.some((j) => j.name === state.job.name)) {
    clearCache();
    state.follow = true;
    if (state.jobs.length) await selectJob(state.jobs[0].name);
    else { state.job = null; state.bed = null; renderJobSelect(); renderTabs(); updateUrl(); await showBed(); }
  } else renderJobSelect();
}

document.getElementById('deleteJobs').addEventListener('click', openDelete);
delList.addEventListener('change', delUpdate);
delAll.addEventListener('change', () => { delList.querySelectorAll('input').forEach((i) => { i.checked = delAll.checked; }); delUpdate(); });
delBtn.addEventListener('click', doDelete);

// ---------- start ----------

async function start() {
  try { state.jobs = await getJSON('/api/jobs'); }
  catch (e) { showStatus(`Viewer server not reachable: ${e.message}`, { error: true }); return; }
  const wanted = params.get('job');
  const name = wanted && state.jobs.some((j) => j.name === wanted) ? wanted : state.jobs[0]?.name;
  if (wanted && name !== wanted) state.follow = true;
  renderJobSelect();
  if (name) await selectJob(name, { keepBed: true });
  else { buildBed(); setView('iso'); state.viewSet = true; await showBed(); }
  setTimeout(poll, POLL_MS);
}
start();
