// ============================================================
// GTFS-JP Maker — STEP 1 & STEP 2 logic
// ============================================================

// ---------- Tab navigation ----------
(function initTabs() {
  const tabBtns = document.querySelectorAll('.tab-btn[data-tab]');
  const tabContents = document.querySelectorAll('.tab-content');

  function switchTab(tabId) {
    tabBtns.forEach(b => {
      b.classList.toggle('active', b.dataset.tab === tabId);
      b.setAttribute('aria-selected', b.dataset.tab === tabId ? 'true' : 'false');
    });
    tabContents.forEach(c => c.classList.toggle('active', c.id === tabId));
  }

  tabBtns.forEach(btn => btn.addEventListener('click', () => switchTab(btn.dataset.tab)));

  document.querySelectorAll('.btn-inline-tab[data-goto]').forEach(btn => {
    btn.addEventListener('click', () => switchTab(btn.dataset.goto));
  });
})();

// ---------- STEP 1: AI Prompt & Templates ----------
(function initStep1() {
  const promptEl = document.getElementById('ai-prompt');
  const copyBtn  = document.getElementById('copy-prompt-btn');

  copyBtn?.addEventListener('click', async () => {
    try {
      await navigator.clipboard.writeText(promptEl.textContent);
      copyBtn.textContent = '✅ コピーしました';
      setTimeout(() => { copyBtn.textContent = '📋 プロンプトをコピー'; }, 2000);
    } catch {
      copyBtn.textContent = '❌ コピー失敗';
      setTimeout(() => { copyBtn.textContent = '📋 プロンプトをコピー'; }, 2000);
    }
  });

  const TEMPLATES = {
    routes: `route_id,route_short_name,route_long_name,direction_id,direction_name,route_color,notes\nR001,北回り,○○町コミュニティバス 北回り,0,北回り,2E86DE,\nR001,北回り,○○町コミュニティバス 北回り,1,南回り,2E86DE,`,
    stops:  `stop_id,stop_name,stop_lat,stop_lon,stop_sequence,notes\nS001,市役所前,35.000000,139.000000,1,\nS002,文化センター前,35.001000,139.002000,2,\nS003,病院前,35.003000,139.004000,3,`,
    timetable: `route_id,direction_id,service_id,trip_name,notes,S001,S002,S003\nR001,0,weekday,第1便,,08:00,08:05,08:12\nR001,0,weekday,第2便,,09:00,09:05,09:12\nR001,0,saturday,第1便,,08:30,08:35,08:42`,
    calendar: `service_id,monday,tuesday,wednesday,thursday,friday,saturday,sunday,start_date,end_date,description,notes\nweekday,1,1,1,1,1,0,0,20260401,20270331,平日,\nsaturday,0,0,0,0,0,1,0,20260401,20270331,土曜,\nholiday,0,0,0,0,0,0,1,20260401,20270331,日祝,`,
  };

  function downloadCSV(name, content) {
    const bom = '﻿';
    const blob = new Blob([bom + content], { type: 'text/csv;charset=utf-8;' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url; a.download = name + '.csv'; a.click();
    URL.revokeObjectURL(url);
  }

  document.querySelectorAll('.btn-dl[data-template]').forEach(btn => {
    btn.addEventListener('click', () => {
      const key = btn.dataset.template;
      downloadCSV(key, TEMPLATES[key]);
    });
  });

  document.getElementById('dl-all-templates-btn')?.addEventListener('click', () => {
    Object.entries(TEMPLATES).forEach(([k, v]) => downloadCSV(k, v));
  });
})();

// ---------- STEP 2: CSV → GTFS-JP ----------
(function initStep2() {

  // State
  const csvData = { routes: null, stops: null, timetable: null, calendar: null };
  let generatedZipBlob = null;

  // DOM
  const previewSection    = document.getElementById('maker-preview-section');
  const validationSection = document.getElementById('maker-validation-section');
  const agencySection     = document.getElementById('maker-agency-section');
  const generateSection   = document.getElementById('maker-generate-section');
  const generateBtn       = document.getElementById('generate-btn');
  const makerLog          = document.getElementById('maker-log');
  const makerError        = document.getElementById('maker-error');
  const dlGroup           = document.getElementById('maker-download-group');
  const dlGtfsBtn         = document.getElementById('download-gtfs-btn');
  const useInStep3Btn     = document.getElementById('use-in-step3-btn');
  const warningNotice     = document.getElementById('maker-warning-notice');

  // CSV file inputs (with drag-drop support)
  ['routes', 'stops', 'timetable', 'calendar'].forEach(key => {
    const input = document.getElementById(`input-${key}`);
    const zone  = document.querySelector(`#upload-${key} .csv-drop-zone`);
    const status = document.getElementById(`status-${key}`);

    function loadFile(file) {
      if (!file) return;
      Papa.parse(file, {
        header: true,
        skipEmptyLines: true,
        complete: result => {
          csvData[key] = result.data;
          status.innerHTML = `<span class="csv-ok">✅ ${file.name}（${result.data.length}行）</span>`;
          zone.classList.add('loaded');
          zone.querySelector('.csv-drop-text').textContent = file.name;
          onAllCSVCheck();
        },
        error: () => {
          status.innerHTML = `<span class="csv-err">❌ 読み込みエラー</span>`;
        }
      });
    }

    input.addEventListener('change', e => loadFile(e.target.files[0]));

    zone.addEventListener('dragover', e => { e.preventDefault(); zone.classList.add('drag-over'); });
    zone.addEventListener('dragleave', () => zone.classList.remove('drag-over'));
    zone.addEventListener('drop', e => {
      e.preventDefault();
      zone.classList.remove('drag-over');
      loadFile(e.dataTransfer.files[0]);
    });
  });

  // Preview tabs
  let currentPreview = 'routes';
  document.querySelectorAll('.preview-tab-btn').forEach(btn => {
    btn.addEventListener('click', () => {
      document.querySelectorAll('.preview-tab-btn').forEach(b => b.classList.remove('active'));
      btn.classList.add('active');
      currentPreview = btn.dataset.preview;
      renderPreview(currentPreview);
    });
  });

  function renderPreview(key) {
    const wrap = document.getElementById('preview-table-wrap');
    const data = csvData[key];
    if (!data || data.length === 0) { wrap.innerHTML = '<p class="no-data">データなし</p>'; return; }
    const cols = Object.keys(data[0]);
    const rows = data.slice(0, 20);
    let html = '<div class="table-scroll"><table class="preview-table"><thead><tr>';
    cols.forEach(c => { html += `<th>${esc(c)}</th>`; });
    html += '</tr></thead><tbody>';
    rows.forEach(row => {
      html += '<tr>';
      cols.forEach(c => { html += `<td>${esc(row[c] ?? '')}</td>`; });
      html += '</tr>';
    });
    html += '</tbody></table></div>';
    if (data.length > 20) html += `<p class="preview-note">先頭20行を表示（全${data.length}行）</p>`;
    wrap.innerHTML = html;
  }

  function onAllCSVCheck() {
    const allLoaded = csvData.routes && csvData.stops && csvData.timetable && csvData.calendar;

    const anyLoaded = Object.values(csvData).some(Boolean);
    previewSection.classList.toggle('hidden', !anyLoaded);
    if (anyLoaded) renderPreview(currentPreview);

    if (allLoaded) {
      validationSection.classList.remove('hidden');
      agencySection.classList.remove('hidden');
      generateSection.classList.remove('hidden');
      runValidation();
    }
  }

  // ---------- Validation ----------
  function runValidation() {
    const errors = [];
    const warnings = [];

    const routes    = csvData.routes   || [];
    const stops     = csvData.stops    || [];
    const timetable = csvData.timetable || [];
    const calendar  = csvData.calendar  || [];

    const requiredCols = {
      routes:    ['route_id', 'route_short_name', 'route_long_name'],
      stops:     ['stop_id', 'stop_name'],
      timetable: ['route_id', 'service_id', 'trip_name'],
      calendar:  ['service_id', 'monday', 'tuesday', 'wednesday', 'thursday', 'friday', 'saturday', 'sunday', 'start_date', 'end_date'],
    };
    for (const [key, cols] of Object.entries(requiredCols)) {
      const data = csvData[key];
      if (!data || data.length === 0) { errors.push(`${key}.csv にデータがありません`); continue; }
      const actualCols = Object.keys(data[0]);
      cols.forEach(c => {
        if (!actualCols.includes(c)) errors.push(`${key}.csv に必須列「${c}」がありません`);
      });
    }

    if (errors.length) { renderValidation(errors, warnings); updateGenerateBtn(errors, warnings); return; }

    const stopIds = stops.map(r => r.stop_id).filter(Boolean);
    const dupStopIds = stopIds.filter((id, i) => stopIds.indexOf(id) !== i);
    if (dupStopIds.length) errors.push(`stops.csv に stop_id が重複しています: ${[...new Set(dupStopIds)].join(', ')}`);

    const routeIdSet = new Set(routes.map(r => r.route_id));
    [...new Set(timetable.map(r => r.route_id))].forEach(id => {
      if (!routeIdSet.has(id)) errors.push(`timetable.csv の route_id「${id}」が routes.csv に存在しません`);
    });

    const calServiceIds = new Set(calendar.map(r => r.service_id));
    [...new Set(timetable.map(r => r.service_id))].forEach(id => {
      if (!calServiceIds.has(id)) errors.push(`timetable.csv の service_id「${id}」が calendar.csv に存在しません`);
    });

    const stopIdSet = new Set(stopIds);
    const stopCols = getStopCols(timetable);
    stopCols.forEach(col => {
      if (!stopIdSet.has(col)) errors.push(`timetable.csv の列「${col}」が stops.csv の stop_id に存在しません`);
    });

    const timeRe = /^\d{1,2}:\d{2}$/;
    let badTimeCount = 0;
    timetable.forEach(row => {
      stopCols.forEach(col => { if (row[col] && !timeRe.test(row[col])) badTimeCount++; });
    });
    if (badTimeCount > 0) errors.push(`時刻形式が不正な値が ${badTimeCount} 件あります（HH:MM 形式で入力してください）`);

    const noLatLon = stops.filter(r => !r.stop_lat || !r.stop_lon);
    if (noLatLon.length) warnings.push(`緯度経度が空欄の停留所が ${noLatLon.length} 件あります（shapes.txt は未設定停留所を除外します）`);

    timetable.forEach(row => {
      let prev = -1;
      stopCols.forEach(col => {
        const v = row[col];
        if (!v) return;
        const t = parseTimeMin(v);
        if (t !== null && t < prev) warnings.push(`便「${row.trip_name}」で時刻が逆行しています（${col}: ${v}）`);
        if (t !== null) prev = t;
      });
    });

    routes.forEach(r => {
      if (!r.route_color) warnings.push(`routes.csv の route_id「${r.route_id}」に route_color が設定されていません`);
    });

    const withNotes = [...routes, ...stops, ...timetable, ...calendar].filter(r => r.notes && r.notes.trim());
    if (withNotes.length) warnings.push(`notes 列に未確認情報が ${withNotes.length} 件あります。内容を確認してください`);

    renderValidation(errors, warnings);
    updateGenerateBtn(errors, warnings);
  }

  function getStopCols(timetable) {
    if (!timetable.length) return [];
    const fixedCols = new Set(['route_id', 'direction_id', 'service_id', 'trip_name', 'notes']);
    return Object.keys(timetable[0]).filter(c => !fixedCols.has(c));
  }

  function parseTimeMin(s) {
    const m = s.match(/^(\d{1,2}):(\d{2})$/);
    if (!m) return null;
    return parseInt(m[1]) * 60 + parseInt(m[2]);
  }

  function renderValidation(errors, warnings) {
    const container = document.getElementById('validation-results');
    let html = '';
    if (errors.length === 0 && warnings.length === 0) {
      html = '<div class="val-item val-ok">✅ 検査OK — すべてのチェックを通過しました</div>';
    }
    errors.forEach(e => { html += `<div class="val-item val-error">❌ エラー: ${esc(e)}</div>`; });
    warnings.forEach(w => { html += `<div class="val-item val-warn">⚠️ 警告: ${esc(w)}</div>`; });
    container.innerHTML = html;
  }

  function updateGenerateBtn(errors, warnings) {
    generateBtn.disabled = errors.length > 0;
    if (warnings.length > 0 && errors.length === 0) {
      warningNotice.textContent = `⚠️ ${warnings.length} 件の警告があります。確認のうえ、生成を実行してください。`;
      warningNotice.classList.remove('hidden');
    } else {
      warningNotice.classList.add('hidden');
    }
  }

  // ---------- Generate GTFS-JP ----------
  generateBtn?.addEventListener('click', async () => {
    generateBtn.disabled = true;
    makerLog.classList.remove('hidden');
    makerError.classList.add('hidden');
    dlGroup.classList.add('hidden');
    makerLog.innerHTML = '';
    generatedZipBlob = null;

    try {
      const zip = new JSZip();
      const routes    = csvData.routes;
      const stops     = csvData.stops;
      const timetable = csvData.timetable;
      const calendar  = csvData.calendar;
      const stopCols  = getStopCols(timetable);

      log('agency.txt を生成中...');
      zip.file('agency.txt', makeAgencyTxt());
      log('routes.txt を生成中...');
      zip.file('routes.txt', makeRoutesTxt(routes));
      log('stops.txt を生成中...');
      zip.file('stops.txt', makeStopsTxt(stops));
      log('trips.txt / stop_times.txt を生成中...');
      const { tripsTxt, stopTimesTxt } = makeTripsAndStopTimes(timetable, stopCols, stops);
      zip.file('trips.txt', tripsTxt);
      zip.file('stop_times.txt', stopTimesTxt);
      log('calendar.txt を生成中...');
      zip.file('calendar.txt', makeCalendarTxt(calendar));
      log('calendar_dates.txt を生成中（空ファイル）...');
      zip.file('calendar_dates.txt', 'service_id,date,exception_type\n');
      log('shapes.txt を生成中...');
      zip.file('shapes.txt', makeShapesTxt(routes, stops, stopCols, timetable));
      log('feed_info.txt を生成中...');
      zip.file('feed_info.txt', makeFeedInfoTxt());
      log('ZIPをパック中...');
      generatedZipBlob = await zip.generateAsync({ type: 'blob' });
      log('✅ 生成完了！');
      dlGroup.classList.remove('hidden');
    } catch (e) {
      makerError.textContent = '❌ 生成中にエラーが発生しました: ' + e.message;
      makerError.classList.remove('hidden');
    } finally {
      generateBtn.disabled = false;
    }
  });

  function log(msg) {
    const line = document.createElement('div');
    line.textContent = msg;
    makerLog.appendChild(line);
  }

  function makeAgencyTxt() {
    const id    = v('agency-id')    || 'A001';
    const name  = v('agency-name')  || '未設定事業者';
    const url   = v('agency-url')   || 'https://example.com';
    const phone = v('agency-phone') || '';
    return csvLine(['agency_id', 'agency_name', 'agency_url', 'agency_timezone', 'agency_lang', 'agency_phone']) +
           csvLine([id, name, url, 'Asia/Tokyo', 'ja', phone]);
  }

  function makeRoutesTxt(routes) {
    const agencyId = v('agency-id') || 'A001';
    let out = csvLine(['route_id', 'agency_id', 'route_short_name', 'route_long_name', 'route_type', 'route_color', 'route_text_color']);
    const seen = new Set();
    routes.forEach(r => {
      if (seen.has(r.route_id)) return;
      seen.add(r.route_id);
      out += csvLine([r.route_id, agencyId, r.route_short_name || '', r.route_long_name || '', '3', (r.route_color || '000080').replace('#', ''), 'FFFFFF']);
    });
    return out;
  }

  function makeStopsTxt(stops) {
    let out = csvLine(['stop_id', 'stop_name', 'stop_lat', 'stop_lon']);
    stops.forEach(r => { out += csvLine([r.stop_id, r.stop_name, r.stop_lat || '', r.stop_lon || '']); });
    return out;
  }

  function makeTripsAndStopTimes(timetable, stopCols, stops) {
    let tripsTxt     = csvLine(['route_id', 'service_id', 'trip_id', 'trip_headsign', 'direction_id', 'shape_id']);
    let stopTimesTxt = csvLine(['trip_id', 'arrival_time', 'departure_time', 'stop_id', 'stop_sequence']);
    timetable.forEach((row, i) => {
      const tripId  = 'T' + String(i + 1).padStart(3, '0');
      const dirId   = row.direction_id || '0';
      const shapeId = `${row.route_id}_${dirId}`;
      tripsTxt += csvLine([row.route_id, row.service_id, tripId, row.trip_name || '', dirId, shapeId]);
      let seq = 1;
      stopCols.forEach(col => {
        const hhmm = normalizeTime((row[col] || '').trim());
        if (!hhmm) return;
        stopTimesTxt += csvLine([tripId, hhmm + ':00', hhmm + ':00', col, String(seq++)]);
      });
    });
    return { tripsTxt, stopTimesTxt };
  }

  function normalizeTime(s) {
    const m = s.match(/^(\d{1,2}):(\d{2})$/);
    if (!m) return null;
    return m[1].padStart(2, '0') + ':' + m[2];
  }

  function makeCalendarTxt(calendar) {
    let out = csvLine(['service_id', 'monday', 'tuesday', 'wednesday', 'thursday', 'friday', 'saturday', 'sunday', 'start_date', 'end_date']);
    calendar.forEach(r => {
      out += csvLine([r.service_id, r.monday || '0', r.tuesday || '0', r.wednesday || '0', r.thursday || '0', r.friday || '0', r.saturday || '0', r.sunday || '0', r.start_date || '', r.end_date || '']);
    });
    return out;
  }

  function makeShapesTxt(routes, stops, stopCols, timetable) {
    const stopMap = {};
    stops.forEach(r => {
      if (r.stop_lat && r.stop_lon) stopMap[r.stop_id] = { lat: parseFloat(r.stop_lat), lon: parseFloat(r.stop_lon) };
    });
    const shapeStops = {};
    timetable.forEach(row => {
      const dirId   = row.direction_id || '0';
      const shapeId = `${row.route_id}_${dirId}`;
      if (shapeStops[shapeId]) return;
      shapeStops[shapeId] = stopCols.filter(col => row[col] && row[col].trim());
    });
    let out = csvLine(['shape_id', 'shape_pt_lat', 'shape_pt_lon', 'shape_pt_sequence', 'shape_dist_traveled']);
    let hasAny = false;
    for (const [shapeId, stopIds] of Object.entries(shapeStops)) {
      let seq = 1, cumDist = 0, prevPt = null;
      stopIds.forEach(stopId => {
        const pt = stopMap[stopId];
        if (!pt) return;
        if (prevPt) cumDist += haversine(prevPt.lat, prevPt.lon, pt.lat, pt.lon);
        out += csvLine([shapeId, pt.lat.toFixed(6), pt.lon.toFixed(6), String(seq++), cumDist.toFixed(1)]);
        prevPt = pt;
        hasAny = true;
      });
    }
    return hasAny ? out : 'shape_id,shape_pt_lat,shape_pt_lon,shape_pt_sequence,shape_dist_traveled\n';
  }

  function makeFeedInfoTxt() {
    const name  = v('feed-publisher-name') || v('agency-name') || '未設定';
    const url   = v('feed-publisher-url')  || v('agency-url')  || 'https://example.com';
    const start = v('feed-start-date')?.replace(/-/g, '') || '';
    const end   = v('feed-end-date')?.replace(/-/g, '')   || '';
    return csvLine(['feed_publisher_name', 'feed_publisher_url', 'feed_lang', 'feed_start_date', 'feed_end_date']) +
           csvLine([name, url, 'ja', start, end]);
  }

  dlGtfsBtn?.addEventListener('click', () => {
    if (!generatedZipBlob) return;
    const url = URL.createObjectURL(generatedZipBlob);
    const a = document.createElement('a');
    a.href = url; a.download = 'gtfs-jp.zip'; a.click();
    URL.revokeObjectURL(url);
  });

  useInStep3Btn?.addEventListener('click', () => {
    if (!generatedZipBlob) return;
    const file = new File([generatedZipBlob], 'gtfs-jp.zip', { type: 'application/zip' });
    window._makerGeneratedFile = file;
    document.querySelector('.tab-btn[data-tab="tab-step3"]').click();
    const notice = document.getElementById('step3-from-maker');
    if (notice) notice.classList.remove('hidden');
    const fileInput = document.getElementById('file-input');
    if (fileInput) {
      setTimeout(() => {
        const dt = new DataTransfer();
        dt.items.add(file);
        fileInput.files = dt.files;
        fileInput.dispatchEvent(new Event('change', { bubbles: true }));
      }, 100);
    }
  });

  document.getElementById('step3-clear-maker')?.addEventListener('click', () => {
    document.getElementById('step3-from-maker')?.classList.add('hidden');
  });

  // ---------- Helpers ----------
  function v(id) { return document.getElementById(id)?.value?.trim() || ''; }

  function csvLine(fields) {
    return fields.map(f => {
      const s = String(f ?? '');
      return (s.includes(',') || s.includes('"') || s.includes('\n')) ? `"${s.replace(/"/g, '""')}"` : s;
    }).join(',') + '\r\n';
  }

  function esc(s) {
    return String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
  }

  function haversine(lat1, lon1, lat2, lon2) {
    const R = 6371000;
    const toRad = d => d * Math.PI / 180;
    const dLat = toRad(lat2 - lat1);
    const dLon = toRad(lon2 - lon1);
    const a = Math.sin(dLat/2)**2 + Math.cos(toRad(lat1)) * Math.cos(toRad(lat2)) * Math.sin(dLon/2)**2;
    return R * 2 * Math.atan2(Math.sqrt(a), Math.sqrt(1-a));
  }

})();
