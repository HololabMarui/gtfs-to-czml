// ============================================================
// GTFS-JP → CZML 変換ツール (JavaScript版)
// Python: gtfsjp_to_czml.py の主要ロジックを移植
// ============================================================

// ---------- DOM refs ----------
const dropZone      = document.getElementById('drop-zone');
const fileInput     = document.getElementById('file-input');
const fileInfo      = document.getElementById('file-info');
const fileCheck     = document.getElementById('file-check');
const serviceDate   = document.getElementById('service-date');
const routeSelect   = document.getElementById('route-select');
const routeHint     = document.getElementById('route-hint');
const convertBtn    = document.getElementById('convert-btn');
const downloadBtn   = document.getElementById('download-btn');
const progressBar   = document.getElementById('progress-bar');
const progressFill  = document.getElementById('progress-fill');
const logArea       = document.getElementById('log-area');
const errorArea     = document.getElementById('error-area');

// ---------- State ----------
let loadedZip = null;
let czmlBlob  = null;
let zipFileName = '';
let routeColorMap = {}; // route_id → '#RRGGBB'

// ---------- Init ----------
serviceDate.value = new Date().toISOString().slice(0, 10);

// ---------- Drop zone ----------
dropZone.addEventListener('click', () => fileInput.click());
dropZone.addEventListener('keydown', e => { if (e.key === 'Enter' || e.key === ' ') fileInput.click(); });

dropZone.addEventListener('dragover', e => { e.preventDefault(); dropZone.classList.add('drag-over'); });
dropZone.addEventListener('dragleave', () => dropZone.classList.remove('drag-over'));
dropZone.addEventListener('drop', e => {
  e.preventDefault();
  dropZone.classList.remove('drag-over');
  const f = e.dataTransfer.files[0];
  if (f) handleFile(f);
});

fileInput.addEventListener('change', () => {
  if (fileInput.files[0]) handleFile(fileInput.files[0]);
});

async function handleFile(file) {
  if (!file.name.toLowerCase().endsWith('.zip')) {
    showError('ZIP ファイルを選択してください。');
    return;
  }
  zipFileName = file.name.replace(/\.zip$/i, '');
  clearState();

  try {
    const buf = await file.arrayBuffer();
    loadedZip = await JSZip.loadAsync(buf);
  } catch (e) {
    showError('ZIPファイルの読み込みに失敗しました: ' + e.message);
    return;
  }

  dropZone.classList.add('has-file');
  fileInfo.classList.remove('hidden');
  fileInfo.innerHTML = `✅ ${file.name} (${(file.size / 1024).toFixed(0)} KB)`;

  await checkAndLoadZip();
}

async function checkAndLoadZip() {
  const required = ['routes.txt', 'trips.txt', 'stop_times.txt', 'stops.txt'];
  const optional = ['shapes.txt', 'calendar.txt', 'calendar_dates.txt'];

  fileCheck.classList.remove('hidden');
  fileCheck.innerHTML = '';

  let allOk = true;
  for (const name of required) {
    const found = await findInZip(loadedZip, name);
    const chip = document.createElement('span');
    chip.className = found ? 'chip ok' : 'chip missing';
    chip.textContent = (found ? '✓ ' : '✗ ') + name;
    fileCheck.appendChild(chip);
    if (!found) allOk = false;
  }
  for (const name of optional) {
    const found = await findInZip(loadedZip, name);
    const chip = document.createElement('span');
    chip.className = found ? 'chip ok' : 'chip warn';
    chip.textContent = (found ? '✓ ' : '○ ') + name;
    fileCheck.appendChild(chip);
  }

  if (!allOk) {
    showError('必須ファイルが ZIP 内に見つかりません。上記の ✗ ファイルを確認してください。');
    return;
  }

  // Load routes for dropdown
  try {
    const routesRaw = await readTextFromZip(loadedZip, 'routes.txt');
    const routes = parseCsv(routesRaw);
    populateRouteSelect(routes);
  } catch (e) {
    // non-fatal
  }

  convertBtn.disabled = false;
  hideError();
}

function populateRouteSelect(routes) {
  routeSelect.innerHTML = '<option value="">（全路線）</option>';
  routeColorMap = {};

  const colorSection = document.getElementById('route-colors-section');
  const colorList    = document.getElementById('route-colors-list');
  colorList.innerHTML = '';

  for (const r of routes) {
    const opt = document.createElement('option');
    opt.value = r.route_id || '';
    const label = [r.route_short_name, r.route_long_name].filter(Boolean).join(' / ') || r.route_id;
    opt.textContent = `${r.route_id} — ${label}`;
    routeSelect.appendChild(opt);

    // Default color: from route_color field or fallback blue
    const defaultHex = r.route_color
      ? '#' + r.route_color.replace(/^#/, '')
      : '#0080ff';
    routeColorMap[r.route_id] = defaultHex;

    // Color picker row
    const row = document.createElement('div');
    row.className = 'route-color-row';

    const picker = document.createElement('input');
    picker.type = 'color';
    picker.value = defaultHex;
    picker.dataset.routeId = r.route_id;
    picker.addEventListener('input', e => {
      routeColorMap[e.target.dataset.routeId] = e.target.value;
    });

    const nameSpan = document.createElement('span');
    nameSpan.className = 'route-color-name';
    nameSpan.textContent = label;

    const idSpan = document.createElement('span');
    idSpan.className = 'route-color-id';
    idSpan.textContent = r.route_id;

    row.appendChild(picker);
    row.appendChild(nameSpan);
    row.appendChild(idSpan);
    colorList.appendChild(row);
  }

  routeSelect.disabled = false;
  routeHint.textContent = `${routes.length} 路線を読み込みました`;
  colorSection.classList.remove('hidden');
}

// ---------- Convert ----------
convertBtn.addEventListener('click', runConvert);

async function runConvert() {
  if (!loadedZip) return;
  const date = serviceDate.value;
  if (!date) { showError('サービス日を入力してください。'); return; }

  hideError();
  czmlBlob = null;
  downloadBtn.classList.add('hidden');
  logArea.classList.remove('hidden');
  logArea.innerHTML = '';
  progressBar.classList.remove('hidden');
  setProgress(0);
  convertBtn.disabled = true;

  try {
    czmlBlob = await buildCzml({ date });
    if (czmlBlob) {
      log('✅ CZML 生成完了', 'ok');
      downloadBtn.classList.remove('hidden');
      setProgress(100);
    }
  } catch (e) {
    showError(e.message || String(e));
    log('❌ ' + (e.message || e), 'error');
  } finally {
    convertBtn.disabled = false;
  }
}

async function buildCzml({ date }) {
  const modelUrl    = document.getElementById('model-url').value.trim() || null;
  const modelScale  = parseFloat(document.getElementById('model-scale').value) || 1.0;
  const lineWidth   = parseFloat(document.getElementById('line-width').value) || 3.0;
  const lineOpacity = parseFloat(document.getElementById('line-opacity').value);
  const trail       = parseFloat(document.getElementById('trail').value) || 0;
  const sampleEvery = parseFloat(document.getElementById('sample-every').value) || 50;
  const fallback    = document.getElementById('fallback-mode').value;
  const clamp       = document.getElementById('clamp-to-ground').checked;
  const routeFilter = routeSelect.value ? [routeSelect.value] : null;

  log('📂 GTFSファイルを読み込み中…', 'info');
  setProgress(5);

  const routesRaw    = await readTextFromZip(loadedZip, 'routes.txt');
  const tripsRaw     = await readTextFromZip(loadedZip, 'trips.txt');
  const stopTimesRaw = await readTextFromZip(loadedZip, 'stop_times.txt');
  const stopsRaw     = await readTextFromZip(loadedZip, 'stops.txt');
  const shapesRaw    = await readTextFromZipOpt(loadedZip, 'shapes.txt');
  const calRaw       = await readTextFromZipOpt(loadedZip, 'calendar.txt');
  const calDatesRaw  = await readTextFromZipOpt(loadedZip, 'calendar_dates.txt');

  setProgress(15);
  log('📋 CSV をパース中…', 'info');

  const routesArr    = parseCsv(routesRaw);
  const tripsArr     = parseCsv(tripsRaw);
  const stopTimesArr = parseCsv(stopTimesRaw);
  const stopsArr     = parseCsv(stopsRaw);
  const shapesArr    = shapesRaw ? parseCsv(shapesRaw) : [];
  const calArr       = calRaw    ? parseCsv(calRaw)    : [];
  const calDatesArr  = calDatesRaw ? parseCsv(calDatesRaw) : [];

  // Build lookup maps
  const routesById = {};
  for (const r of routesArr) routesById[r.route_id] = r;

  const stopsById = {};
  for (const s of stopsArr) stopsById[s.stop_id] = s;

  // stop_times by trip_id
  const stopTimesByTrip = {};
  for (const st of stopTimesArr) {
    const tid = st.trip_id;
    if (!tid) continue;
    (stopTimesByTrip[tid] = stopTimesByTrip[tid] || []).push(st);
  }
  for (const tid in stopTimesByTrip) {
    stopTimesByTrip[tid].sort((a, b) => parseInt(a.stop_sequence) - parseInt(b.stop_sequence));
  }

  setProgress(25);
  log(`📍 shapes.txt: ${shapesArr.length > 0 ? shapesArr.length + ' 行' : 'なし（フォールバックモード: ' + fallback + '）'}`, 'info');

  const shapes = loadShapes(shapesArr);

  // Active services
  const serviceDay = parseDate(date); // { y, m, d, dayOfWeek }
  const activeServices = servicesActiveOn(calArr, calDatesArr, date, serviceDay);
  log(`📅 運行サービス: ${activeServices.size} 件 (${date})`, 'info');

  // Filter trips
  let trips = tripsArr;
  if (routeFilter) trips = trips.filter(t => routeFilter.includes(t.route_id));
  trips = trips.filter(t => activeServices.has(t.service_id));

  if (trips.length === 0) {
    throw new Error(`有効な trip が見つかりません。service-date (${date}) に運行する便がないか、route_id の指定を確認してください。`);
  }
  log(`🚌 対象 trip: ${trips.length} 件`, 'info');
  setProgress(35);

  // Build CZML
  const czml = [{
    id: 'document',
    name: 'GTFS-JP runs',
    version: '1.0',
    clock: { interval: null, currentTime: null, multiplier: 1, range: 'CLAMPED' }
  }];

  const emittedRouteShapes = new Set();
  let docStart = null, docEnd = null;
  let tripCount = 0;
  const total = trips.length;

  for (let i = 0; i < trips.length; i++) {
    const t = trips[i];
    const tripId  = t.trip_id;
    const routeId = t.route_id;
    const shapeId = t.shape_id;

    const st = stopTimesByTrip[tripId] || [];
    if (st.length === 0) continue;

    // Determine shape
    let useShape, routeShapeKey, isFallback = false;
    if (shapeId && shapes[shapeId] && shapes[shapeId].length >= 2) {
      useShape = shapes[shapeId];
      routeShapeKey = `${routeId}:${shapeId}`;
    } else {
      if (fallback === 'none') continue;
      useShape = buildShapeFromStops(st, stopsById);
      if (useShape.length < 2) continue;
      routeShapeKey = `${routeId}:pseudo-${tripId}`;
      isFallback = true;
    }

    // Route line (once per route×shape)
    if (!emittedRouteShapes.has(routeShapeKey)) {
      const hexColor = routeColorMap[routeId] || ('#' + (routesById[routeId] || {}).route_color || '#0080ff');
      const color = parseHexColor(hexColor);
      const colorWithOpacity = withOpacity(color, lineOpacity);
      czml.push(buildRouteEntity(routeShapeKey, useShape, colorWithOpacity, lineWidth, clamp));
      emittedRouteShapes.add(routeShapeKey);
    }

    // Position samples
    const samples = buildSamples(useShape, st, stopsById, date, sampleEvery, 0.0);
    if (samples.length < 2) continue;

    // Clock range
    const s0 = samples[0][0], sN = samples[samples.length - 1][0];
    if (!docStart || s0 < docStart) docStart = s0;
    if (!docEnd   || sN > docEnd)   docEnd   = sN;

    const routeHex = routeColorMap[routeId] || '#0080ff';
    czml.push(buildTripEntity(tripId, samples, modelUrl, modelScale, trail, isFallback, routeHex));
    tripCount++;

    if ((i + 1) % 20 === 0) {
      setProgress(35 + Math.round(55 * (i + 1) / total));
      await yieldFrame();
    }
  }

  if (tripCount === 0) {
    const reason = fallback === 'none'
      ? 'shapes.txt がなく、fallback-mode=none のため描画対象がありません。'
      : '有効なサンプル列を持つ trip がありませんでした。';
    throw new Error(reason);
  }

  log(`🗺 ルート線: ${emittedRouteShapes.size} 本 / trip エンティティ: ${tripCount} 件`, 'ok');

  if (docStart && docEnd) {
    czml[0].clock.interval    = `${toIsoUtc(docStart)}/${toIsoUtc(docEnd)}`;
    czml[0].clock.currentTime = toIsoUtc(docStart);
  }

  setProgress(95);
  const json = JSON.stringify(czml, null, 2);
  return new Blob([json], { type: 'application/json' });
}

// ---------- Download ----------
downloadBtn.addEventListener('click', () => {
  if (!czmlBlob) return;
  const url = URL.createObjectURL(czmlBlob);
  const a = document.createElement('a');
  a.href = url;
  a.download = (zipFileName || 'output') + '.czml';
  a.click();
  setTimeout(() => URL.revokeObjectURL(url), 5000);
});

// ============================================================
// GTFS ロジック
// ============================================================

// --- Haversine distance (meters) ---
function haversineM(lat1, lon1, lat2, lon2) {
  const R = 6371000.0;
  const p1 = toRad(lat1), p2 = toRad(lat2);
  const dp = toRad(lat2 - lat1), dl = toRad(lon2 - lon1);
  const a = Math.sin(dp/2)**2 + Math.cos(p1)*Math.cos(p2)*Math.sin(dl/2)**2;
  return 2*R*Math.asin(Math.sqrt(a));
}
function toRad(d) { return d * Math.PI / 180; }

// --- Parse hex color → [r,g,b,a] ---
function parseHexColor(s, def=[0,128,255,255]) {
  if (!s) return def;
  s = s.trim().replace(/^#/, '');
  if (s.length === 6) return [parseInt(s.slice(0,2),16), parseInt(s.slice(2,4),16), parseInt(s.slice(4,6),16), 255];
  if (s.length === 8) return [parseInt(s.slice(0,2),16), parseInt(s.slice(2,4),16), parseInt(s.slice(4,6),16), parseInt(s.slice(6,8),16)];
  return def;
}

function withOpacity([r,g,b], alpha) {
  return [r, g, b, Math.round(Math.max(0, Math.min(1, alpha)) * 255)];
}

// --- Load shapes from rows ---
function loadShapes(rows) {
  const groups = {};
  for (const r of rows) {
    const sid = r.shape_id;
    if (!sid) continue;
    const seq = parseInt(r.shape_pt_sequence);
    const lat = parseFloat(r.shape_pt_lat);
    const lon = parseFloat(r.shape_pt_lon);
    const distRaw = r.shape_dist_traveled;
    const dist = (distRaw && distRaw.trim() !== '') ? parseFloat(distRaw) : null;
    (groups[sid] = groups[sid] || []).push({ seq, lat, lon, dist });
  }
  const shapes = {};
  for (const sid in groups) {
    const items = groups[sid].sort((a, b) => a.seq - b.seq);
    const pts = [];
    let cum = 0, prev = null;
    for (const { seq, lat, lon, dist } of items) {
      let d = dist;
      if (d === null) {
        d = prev ? cum + haversineM(prev.lat, prev.lon, lat, lon) : 0;
      }
      cum = d;
      pts.push({ seq, lat, lon, dist_m: d });
      prev = { lat, lon };
    }
    if (pts.length >= 2) shapes[sid] = pts;
  }
  return shapes;
}

// --- Build shape from stop sequence (fallback) ---
function buildShapeFromStops(stopTimes, stopsById) {
  const pts = [];
  let cum = 0, prev = null;
  for (const r of stopTimes) {
    const s = stopsById[r.stop_id];
    if (!s || !s.stop_lat || !s.stop_lon) continue;
    const lat = parseFloat(s.stop_lat), lon = parseFloat(s.stop_lon);
    if (prev) cum += haversineM(prev.lat, prev.lon, lat, lon);
    pts.push({ seq: parseInt(r.stop_sequence), lat, lon, dist_m: cum });
    prev = { lat, lon };
  }
  return pts;
}

// --- Project stop onto shape, return shape distance ---
function nearestShapeDistance(shape, stopLat, stopLon) {
  let best = Infinity, bestDist = shape[0].dist_m;
  for (let i = 1; i < shape.length; i++) {
    const a = shape[i-1], b = shape[i];
    const abx = b.lon - a.lon, aby = b.lat - a.lat;
    const ab2 = abx*abx + aby*aby;
    let t = ab2 === 0 ? 0 : ((stopLon - a.lon)*abx + (stopLat - a.lat)*aby) / ab2;
    t = Math.max(0, Math.min(1, t));
    const qx = a.lon + abx*t, qy = a.lat + aby*t;
    const d = haversineM(stopLat, stopLon, qy, qx);
    if (d < best) { best = d; bestDist = a.dist_m + (b.dist_m - a.dist_m)*t; }
  }
  return bestDist;
}

// --- Interpolate [lat, lon] at distance along shape ---
function coordAtDist(shape, targetM) {
  if (targetM <= shape[0].dist_m) return [shape[0].lat, shape[0].lon];
  const last = shape[shape.length - 1];
  if (targetM >= last.dist_m) return [last.lat, last.lon];
  let lo = 0, hi = shape.length - 1;
  while (lo <= hi) {
    const mid = (lo+hi) >> 1;
    if (shape[mid].dist_m < targetM) lo = mid+1; else hi = mid-1;
  }
  const i = Math.max(1, lo);
  const a = shape[i-1], b = shape[i];
  const t = (b.dist_m === a.dist_m) ? 0 : (targetM - a.dist_m) / (b.dist_m - a.dist_m);
  return [a.lat + (b.lat - a.lat)*t, a.lon + (b.lon - a.lon)*t];
}

// --- Calendar: services active on a given date ---
// Returns Set<service_id>
function servicesActiveOn(calArr, calDatesArr, dateStr, serviceDay) {
  const ymd = dateStr.replace(/-/g, '');
  const wdNames = ['monday','tuesday','wednesday','thursday','friday','saturday','sunday'];
  const wd = serviceDay.dayOfWeek; // 0=Mon..6=Sun

  const active = new Set();

  for (const r of calArr) {
    if (!r.start_date || !r.end_date) continue;
    if (r.start_date <= ymd && ymd <= r.end_date) {
      const val = r[wdNames[wd]];
      if (val === '1' || val === 'true' || val === 'TRUE') active.add(r.service_id);
    }
  }

  for (const r of calDatesArr) {
    if (r.date !== ymd) continue;
    if (r.exception_type === '1') active.add(r.service_id);
    else if (r.exception_type === '2') active.delete(r.service_id);
  }

  // If no calendar data, include all services (GTFS datasets sometimes omit calendar)
  if (calArr.length === 0 && calDatesArr.length === 0) {
    return new Set(['__all__']);
  }

  return active;
}

// Patch trips filter when no calendar data
const _origFilter = Array.prototype.filter;
function tripsActiveOn(trips, activeServices) {
  if (activeServices.has('__all__')) return trips;
  return trips.filter(t => activeServices.has(t.service_id));
}

// --- Parse GTFS time "HH:MM:SS" → seconds since midnight (supports >24h) ---
function parseGtfsTime(s) {
  if (!s || !s.trim()) return null;
  const parts = s.trim().split(':');
  return parseInt(parts[0])*3600 + parseInt(parts[1])*60 + parseInt(parts[2]);
}

// --- Convert (serviceDate, secondsSinceMidnight) → UTC ms ---
// Asia/Tokyo = UTC+9 (fixed, per PDF)
function gtfsSecToUtcMs(dateStr, sec) {
  const [y, m, d] = dateStr.split('-').map(Number);
  const extraDays = Math.floor(sec / 86400);
  const s = sec % 86400;
  const hh = Math.floor(s / 3600);
  const mm = Math.floor((s % 3600) / 60);
  const ss = s % 60;
  // Build local datetime (JST = UTC+9), convert to UTC
  const jstMs = Date.UTC(y, m-1, d+extraDays, hh, mm, ss) - 9*3600*1000;
  return jstMs;
}

// --- To ISO UTC string from ms ---
function toIsoUtc(ms) {
  return new Date(ms).toISOString().replace('.000Z','Z');
}

// --- Build position samples list: [[utcMs, lat, lon, height], ...] ---
function buildSamples(shape, stopTimes, stopsById, dateStr, sampleEvery, heightM) {
  const dmax = shape[shape.length - 1].dist_m;
  if (dmax <= 0) return [];

  // Keyframes: [utcMs, dist_m]
  const kps = [];
  for (const r of stopTimes) {
    const arr = r.arrival_time || r.departure_time;
    const sec = parseGtfsTime(arr);
    if (sec === null) continue;
    const tMs = gtfsSecToUtcMs(dateStr, sec);

    let sd = null;
    const distRaw = r.shape_dist_traveled;
    if (distRaw && distRaw.trim() !== '') sd = parseFloat(distRaw);
    if (sd === null) {
      const s = stopsById[r.stop_id];
      if (s && s.stop_lat && s.stop_lon) {
        sd = nearestShapeDistance(shape, parseFloat(s.stop_lat), parseFloat(s.stop_lon));
      }
    }
    if (sd === null) {
      const idx = parseInt(r.stop_sequence);
      sd = dmax * (idx - 1) / Math.max(1, stopTimes.length - 1);
    }
    sd = Math.max(0, Math.min(dmax, sd));
    kps.push([tMs, sd]);
  }

  if (kps.length < 2) return [];
  kps.sort((a, b) => a[0] - b[0]);

  const samples = [];
  for (let i = 1; i < kps.length; i++) {
    let [t0, d0] = kps[i-1], [t1, d1] = kps[i];
    if (d1 < d0) { [t0, d0, t1, d1] = [t1, d1, t0, d0]; }
    const dist = d1 - d0, secs = (t1 - t0) / 1000;
    if (dist <= 0 || secs <= 0) continue;
    const step = Math.max(1, sampleEvery);
    for (let dd = d0; dd < d1; dd += step) {
      const ratio = (dd - d0) / dist;
      const tt = t0 + (t1 - t0) * ratio;
      const [lat, lon] = coordAtDist(shape, dd);
      samples.push([tt, lat, lon, heightM]);
    }
    const [lat1, lon1] = coordAtDist(shape, d1);
    samples.push([t1, lat1, lon1, heightM]);
  }

  samples.sort((a, b) => a[0] - b[0]);
  return samples;
}

// ============================================================
// CZML builders
// ============================================================

function buildRouteEntity(key, shape, color, width, clamp) {
  const pos = [];
  for (const p of shape) pos.push(p.lon, p.lat, 0.0);
  const e = {
    id: `route-${key}`,
    name: `route ${key}`,
    polyline: {
      positions: { cartographicDegrees: pos },
      width,
      material: { solidColor: { color: { rgba: color } } }
    }
  };
  if (clamp) e.polyline.clampToGround = true;
  return e;
}

function buildTripEntity(tripId, samples, modelUrl, modelScale, trailSec, isFallback, routeHex) {
  const epoch = new Date(samples[0][0]).toISOString().replace('.000Z', 'Z');
  const t0Ms  = samples[0][0];
  const posArr = [];
  for (const [tMs, lat, lon, h] of samples) {
    posArr.push((tMs - t0Ms) / 1000, lon, lat, h);
  }

  const avail = `${toIsoUtc(samples[0][0])}/${toIsoUtc(samples[samples.length-1][0])}`;

  const ent = {
    id: `trip-${tripId}`,
    name: `trip ${tripId}`,
    availability: avail,
    position: {
      epoch,
      cartographicDegrees: posArr,
      interpolationAlgorithm: 'LAGRANGE',
      interpolationDegree: 1
    },
    orientation: { velocityReference: '#position' },
    path: { show: true, leadTime: 0, trailTime: trailSec, width: 2 },
    properties: { shape_fallback: { boolean: isFallback } }
  };

  if (modelUrl) {
    ent.model = {
      gltf: modelUrl,
      scale: modelScale,
      minimumPixelSize: 48,
      shadows: 'ENABLED',
      heightReference: 'RELATIVE_TO_GROUND'
    };
  } else {
    // No model URL: show a colored box as a stand-in
    const boxColor = parseHexColor(routeHex || '#0080ff');
    ent.box = {
      dimensions: { cartesian: [3.0, 3.0, 3.0] },
      fill: true,
      material: { solidColor: { color: { rgba: boxColor } } },
      heightReference: 'RELATIVE_TO_GROUND'
    };
  }

  return ent;
}

// ============================================================
// Utilities
// ============================================================

// Parse date string "YYYY-MM-DD" → { y, m, d, dayOfWeek (0=Mon..6=Sun) }
function parseDate(s) {
  const [y, m, d] = s.split('-').map(Number);
  const jsDay = new Date(y, m-1, d).getDay(); // 0=Sun..6=Sat
  const dayOfWeek = jsDay === 0 ? 6 : jsDay - 1; // convert to 0=Mon..6=Sun
  return { y, m, d, dayOfWeek };
}

// Find a file in ZIP (case-insensitive, handles subdirectory)
async function findInZip(zip, filename) {
  const lc = filename.toLowerCase();
  for (const path of Object.keys(zip.files)) {
    const base = path.split('/').pop().toLowerCase();
    if (base === lc) return path;
  }
  return null;
}

async function readTextFromZip(zip, filename) {
  const path = await findInZip(zip, filename);
  if (!path) throw new Error(`${filename} が ZIP 内に見つかりません`);
  return await zip.files[path].async('string');
}

async function readTextFromZipOpt(zip, filename) {
  const path = await findInZip(zip, filename);
  if (!path) return null;
  return await zip.files[path].async('string');
}

function parseCsv(text) {
  const result = Papa.parse(text.replace(/^﻿/, ''), {
    header: true,
    skipEmptyLines: true,
    trimHeaders: true
  });
  return result.data;
}

function yieldFrame() {
  return new Promise(r => requestAnimationFrame(r));
}

// ---------- UI helpers ----------
function log(msg, type = 'info') {
  const line = document.createElement('div');
  line.className = `log-line ${type}`;
  line.textContent = msg;
  logArea.appendChild(line);
  logArea.scrollTop = logArea.scrollHeight;
}

function setProgress(pct) {
  progressFill.style.width = pct + '%';
}

function showError(msg) {
  errorArea.textContent = msg;
  errorArea.classList.remove('hidden');
}

function hideError() {
  errorArea.classList.add('hidden');
  errorArea.textContent = '';
}

function clearState() {
  loadedZip = null;
  czmlBlob = null;
  dropZone.classList.remove('has-file');
  fileInfo.classList.add('hidden');
  fileCheck.classList.add('hidden');
  fileCheck.innerHTML = '';
  logArea.classList.add('hidden');
  logArea.innerHTML = '';
  progressBar.classList.add('hidden');
  downloadBtn.classList.add('hidden');
  routeSelect.innerHTML = '<option value="">（全路線）</option>';
  routeSelect.disabled = true;
  routeHint.textContent = 'ZIPを読み込むと路線一覧が表示されます';
  routeColorMap = {};
  document.getElementById('route-colors-section').classList.add('hidden');
  document.getElementById('route-colors-list').innerHTML = '';
  convertBtn.disabled = true;
  hideError();
}
