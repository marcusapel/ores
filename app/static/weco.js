(function(){
  'use strict';

  const $ = (sel, root=document) => root.querySelector(sel);
  const $$ = (sel, root=document) => [...root.querySelectorAll(sel)];

  // ── State ─────────────────────────────────────────────────────────
  let importedWells = null;
  let correlationResult = null;
  let selectedDemo = null;
  let currentDemoId = '';
  let wellDetails = [];  // per-well data for log preview

  // ── DOM refs ──────────────────────────────────────────────────────
  const tabs = $$('.wc-tab');
  const bodies = $$('.wc-body');
  const healthDot = $('#health-dot');
  const healthTxt = $('#health-text');

  // Data tab
  const dsF1 = $('#ds-f1');
  const dsF2 = $('#ds-f2');
  const dsCount = $('#ds-count');
  const dsSel = $('#ds-select');
  const btnImport = $('#btn-import');
  const btnRefreshDs = $('#btn-refresh-ds');
  const importSpin = $('#import-spinner');
  const importStat = $('#import-status');
  const wellsSumm = $('#wells-summary');
  const wellCount = $('#well-count');
  const wellChips = $('#well-chips');
  const dataNames = $('#data-names');
  const regionNames = $('#region-names');
  const demoGrid = $('#demo-grid');
  const btnSelectAll = $('#btn-select-all-wells');
  const btnSelectNone = $('#btn-select-none-wells');

  // Log tab
  const logWellList = $('#log-well-list');
  const logChannelSel = $('#log-channel-select');
  const logRegionSel = $('#log-region-select');
  const logCanvas = $('#log-canvas');

  // Params tab
  const paramPreset = $('#param-preset');
  const showAdv = $('#show-advanced');
  const btnSuggest = $('#btn-suggest');
  const suggestSt = $('#suggest-status');

  // Run tab
  const runSummary = $('#run-summary');
  const btnRun = $('#btn-run');
  const btnQuickRun = $('#btn-quick-run');
  const btnRunDemo = $('#btn-run-demo');
  const runSpin = $('#run-spinner');
  const runProgress = $('#run-progress');
  const runError = $('#run-error');
  const engineLog = $('#engine-log');
  const workflowName = $('#workflow-name');
  const btnSaveWorkflow = $('#btn-save-workflow');
  const btnLoadWorkflows = $('#btn-load-workflows');
  const workflowStatus = $('#workflow-status');
  const workflowList = $('#workflow-list');

  // Results tab
  const resEmpty = $('#results-empty');
  const resSummary = $('#results-summary');
  const resNWells = $('#res-n-wells');
  const resNRes = $('#res-n-results');
  const resElapsed = $('#res-elapsed');
  const resMode = $('#res-mode');
  const resSelector = $('#res-selector');
  const resCards = $('#results-cards');
  const btnResPrev = $('#btn-res-prev');
  const btnResNext = $('#btn-res-next');
  const resCostLabel = $('#res-cost-label');
  const resRanking = $('#res-ranking');

  // Export tab
  const btnExportRddms = $('#btn-export-rddms');
  const btnExportJson = $('#btn-export-json');
  const btnExportCsv = $('#btn-export-csv');
  const exportStatus = $('#export-status');

  // ── Tab switching ─────────────────────────────────────────────────
  const tabHelpLink = $('#tab-help-link');
  const tabHelpMap = (window.WECO_CONFIG && window.WECO_CONFIG.tabHelp) || {};

  function switchTab(name) {
    tabs.forEach(t => {
      const isTarget = t.dataset.tab === name;
      t.classList.toggle('active', isTarget);
      if (isTarget) t.classList.remove('disabled');
    });
    bodies.forEach(b => b.classList.toggle('active', b.id === 'tab-' + name));
    // Update context-specific help link
    if (tabHelpLink && tabHelpMap[name]) {
      tabHelpLink.href = tabHelpMap[name];
    }
  }
  tabs.forEach(t => t.addEventListener('click', () => {
    if (!t.classList.contains('disabled')) switchTab(t.dataset.tab);
  }));

  // ── API helpers ───────────────────────────────────────────────────
  async function api(method, path, body) {
    const opts = { method, headers: {'Content-Type': 'application/json'} };
    if (body) opts.body = JSON.stringify(body);
    const resp = await fetch('/weco' + path, opts);
    if (!resp.ok) {
      const txt = await resp.text();
      throw new Error(txt || `HTTP ${resp.status}`);
    }
    return resp.json();
  }

  function _contextLabel() {
    const parts = [];
    if (selectedDemo) parts.push(`Demo: ${selectedDemo}`);
    const ds = dsSel.value;
    if (ds) parts.push(`[${ds}]`);
    return parts.length ? parts.join(' ') + ' —' : '';
  }

  function setStatus(el, cls, msg) {
    el.className = 'wc-status ' + cls;
    el.textContent = msg;
    el.style.display = msg ? 'block' : 'none';
  }

  function esc(s) {
    const d = document.createElement('div');
    d.textContent = String(s);
    return d.innerHTML;
  }

  // ── Health check ──────────────────────────────────────────────────
  async function checkHealth() {
    try {
      const d = await api('GET', '/health');
      healthDot.className = 'health-dot ' + (d.connected ? 'ok' : 'err');
      healthTxt.textContent = d.connected
        ? `Engine OK (v${d.version})`
        : 'Engine unavailable';
    } catch(e) {
      healthDot.className = 'health-dot err';
      healthTxt.textContent = 'Engine check failed';
    }
  }
  checkHealth();

  // ── Dataspace list ────────────────────────────────────────────────
  const _defaultDs = (window.WECO_CONFIG && window.WECO_CONFIG.defaultDataspace) || 'maap/weco';
  let allDataspaces = [];
  let selectedWells = new Set(); // well names currently selected

  async function loadDataspaces() {
    const dsStatus = $('#ds-loading-status');
    const dsElapsed = $('#ds-loading-elapsed');
    let elapsed = 0;
    if (dsStatus) dsStatus.style.display = 'block';
    const timer = setInterval(() => { elapsed++; if (dsElapsed) dsElapsed.textContent = elapsed; }, 1000);
    try {
      const d = await api('GET', '/dataspaces');
      allDataspaces = (d.dataspaces || []).map(ds => {
        if (typeof ds === 'string') return ds;
        return ds.path || ds.DataspaceId || ds.id || ds.name || '';
      }).filter(Boolean);
      applyDsFilter();
    } catch(e) {
      dsSel.innerHTML = '<option disabled>Could not load dataspaces</option>';
    } finally {
      clearInterval(timer);
      if (dsStatus) dsStatus.style.display = 'none';
    }
  }

  function selectDataspace(dsPath) {
    // Try to select the given dataspace in the dropdown
    for (let i = 0; i < dsSel.options.length; i++) {
      if (dsSel.options[i].value === dsPath) {
        dsSel.selectedIndex = i;
        return true;
      }
    }
    return false;
  }

  function applyDsFilter() {
    const q1 = (dsF1.value || '').trim().toLowerCase();
    const q2 = (dsF2.value || '').trim().toLowerCase();
    dsSel.innerHTML = '';
    let matched = 0;
    allDataspaces.forEach(path => {
      const lp = path.toLowerCase();
      const parts = lp.split('/');
      const seg1 = parts[0] || '';
      const seg2 = parts.slice(1).join('/');
      if (q1 && !seg1.includes(q1)) return;
      if (q2 && !seg2.includes(q2)) return;
      const o = document.createElement('option');
      o.value = path;
      o.textContent = path;
      dsSel.appendChild(o);
      matched++;
    });
    dsCount.textContent = (q1 || q2) ? `${matched}/${allDataspaces.length}` : `${allDataspaces.length}`;
    // Auto-select default dataspace (demo dataspace), fall back to first
    if (!selectDataspace(_defaultDs) && dsSel.options.length > 0 && !dsSel.value) {
      dsSel.selectedIndex = 0;
    }
  }

  dsF1.addEventListener('input', applyDsFilter);
  dsF2.addEventListener('input', applyDsFilter);
  btnRefreshDs.addEventListener('click', loadDataspaces);
  loadDataspaces();

  // ── Import wells ──────────────────────────────────────────────────
  btnImport.addEventListener('click', async () => {
    const ds = dsSel.value;
    if (!ds) { setStatus(importStat, 'warn', 'Select a dataspace first'); return; }
    importSpin.style.display = 'inline';
    setStatus(importStat, '', '');
    try {
      const data = await api('POST', '/import', { dataspace: ds });
      importedWells = data;
      showWellsSummary(data);
      setStatus(importStat, 'ok', `Imported ${data.well_count} wells (${(data.data_names||[]).length} logs, ${(data.region_names||[]).length} regions)`);
      enableAfterImport();
    } catch(e) {
      setStatus(importStat, 'err', 'Import failed: ' + e.message);
    } finally {
      importSpin.style.display = 'none';
    }
  });

  function showWellsSummary(data) {
    wellsSumm.style.display = 'block';
    const names = data.well_names || [];
    wellCount.textContent = names.length;
    selectedWells = new Set(names); // select all by default
    renderWellChips(names);
    dataNames.textContent = (data.data_names || []).join(', ') || '(none)';
    regionNames.textContent = (data.region_names || []).join(', ') || '(none)';
    populateDropdowns(data.data_names || [], data.region_names || []);
    populateLogSelectors(data.data_names || [], data.region_names || []);
    updateLogWellList(names);
    runSummary.textContent = `${_contextLabel()} ${names.length} wells loaded | Logs: ${(data.data_names||[]).join(', ')} | Regions: ${(data.region_names||[]).join(', ')}`;
  }

  function renderWellChips(names) {
    const filter = (($('#well-filter') || {}).value || '').toLowerCase();
    wellChips.innerHTML = names.map(n => {
      const sel = selectedWells.has(n);
      if (filter && !n.toLowerCase().includes(filter)) return '';
      // Show log availability indicator if wellDetails loaded
      const wd = wellDetails.find(w => w.name === n);
      const logCount = wd ? wd.data_names.length : 0;
      const logBadge = logCount ? `<span class="log-badge">${logCount} logs</span>` : '';
      // Tooltip with metadata
      let tip = n;
      if (wd) {
        tip = `${n}\nSize: ${wd.size} pts`;
        if (wd.x !== undefined) tip += `\nX: ${wd.x.toFixed(1)}, Y: ${wd.y.toFixed(1)}, Z: ${wd.z.toFixed(1)}`;
        if (wd.data_names) tip += `\nLogs: ${wd.data_names.join(', ')}`;
        if (wd.region_names && wd.region_names.length) tip += `\nRegions: ${wd.region_names.join(', ')}`;
        if (wd.uuid) tip += `\nUUID: ${wd.uuid}`;
        if (wd.demo) tip += `\nDemo: ${wd.demo}`;
      }
      return `<span class="well-chip ${sel ? 'selected' : 'excluded'}" data-name="${esc(n)}" title="${esc(tip)}" tabindex="0" role="checkbox" aria-checked="${sel}">${esc(n)} ${logBadge}</span>`;
    }).join('');
    // Click to toggle selection
    wellChips.querySelectorAll('.well-chip').forEach(chip => {
      chip.addEventListener('click', () => {
        const name = chip.dataset.name;
        if (selectedWells.has(name)) {
          selectedWells.delete(name);
          chip.classList.remove('selected');
          chip.classList.add('excluded');
          chip.setAttribute('aria-checked', 'false');
        } else {
          selectedWells.add(name);
          chip.classList.add('selected');
          chip.classList.remove('excluded');
          chip.setAttribute('aria-checked', 'true');
        }
        wellCount.textContent = selectedWells.size;
      });
    });
  }

  // Well filter input
  const wellFilter = $('#well-filter');
  if (wellFilter) {
    wellFilter.addEventListener('input', () => {
      const names = importedWells ? (importedWells.well_names || []) : [];
      renderWellChips(names);
    });
  }

  btnSelectAll.addEventListener('click', () => {
    wellChips.querySelectorAll('.well-chip').forEach(c => {
      selectedWells.add(c.dataset.name);
      c.classList.add('selected');
      c.classList.remove('excluded');
    });
    wellCount.textContent = selectedWells.size;
  });

  btnSelectNone.addEventListener('click', () => {
    selectedWells.clear();
    wellChips.querySelectorAll('.well-chip').forEach(c => {
      c.classList.remove('selected');
      c.classList.add('excluded');
    });
    wellCount.textContent = 0;
  });

  function enableAfterImport() {
    btnRun.disabled = false;
    btnQuickRun.disabled = false;
    const qrInfo = $('#quick-run-info');
    if (qrInfo) qrInfo.style.display = 'block';
    tabs.forEach(t => {
      if (['logs', 'params', 'run'].includes(t.dataset.tab)) t.classList.remove('disabled');
    });
  }

  function populateDropdowns(dataLogs, regions) {
    const skip = new Set(['Depth', 'DEPTH', 'MD', 'X', 'Y', 'Z']);
    const usableLogs = dataLogs.filter(n => !skip.has(n));
    const dataSelects = ['#p-var-data', '#p-var-data2'];
    const regionSelects = ['#p-no-crossing', '#p-same-region', '#p-polarity-region',
                           '#p-dist-distal', '#p-dist-facies'];
    dataSelects.forEach(sel => {
      const el = $(sel);
      if (!el) return;
      el.innerHTML = '<option value="">-- select --</option>';
      dataLogs.forEach(n => { el.innerHTML += `<option value="${esc(n)}">${esc(n)}</option>`; });
    });
    regionSelects.forEach(sel => {
      const el = $(sel);
      if (!el) return;
      el.innerHTML = '<option value="">-- none --</option>';
      regions.forEach(n => { el.innerHTML += `<option value="${esc(n)}">${esc(n)}</option>`; });
    });
    // Auto-select first usable log (skip Depth/X/Y/Z)
    if (usableLogs.length > 0) {
      const el = $('#p-var-data');
      if (el) el.value = usableLogs[0];
    }
    // Auto-suggest best parameters from the backend
    autoSuggest();
  }

  async function autoSuggest() {
    try {
      const data = await api('POST', '/suggest-defaults');
      if (data.options && Object.keys(data.options).length > 0) {
        applyOptions(data.options);
      }
    } catch(e) { /* silent fallback to manual selection */ }
  }

  // ── Demo datasets ─────────────────────────────────────────────────
  async function loadDemos() {
    try {
      const resp = await api('GET', '/demos');
      const items = Array.isArray(resp) ? resp : (resp.demos || []);
      if (!items.length) {
        demoGrid.innerHTML = '<div class="muted">No demos available</div>';
        return;
      }
      demoGrid.innerHTML = items.map(d => `
        <div class="demo-card" data-id="${esc(d.id || d.demo_id || '')}">
          <h4>${esc(d.title || d.name || d.id || '?')}</h4>
          <p>${esc(d.description || d.geology || '')}</p>
          <div class="demo-meta">
            ${d.n_wells || '?'} wells | ${d.group || ''}
            ${d.data_names && d.data_names.length ? ' | Logs: ' + d.data_names.join(', ') : ''}
          </div>
        </div>
      `).join('');
    } catch(e) {
      demoGrid.innerHTML = '<div class="muted">Could not load demos</div>';
    }
  }

  demoGrid.addEventListener('click', async e => {
    const card = e.target.closest('.demo-card');
    if (!card) return;
    $$('.demo-card', demoGrid).forEach(c => c.classList.remove('active'));
    card.classList.add('active');
    selectedDemo = card.dataset.id;
    currentDemoId = selectedDemo;
    if (btnRunDemo) {
      btnRunDemo.style.display = 'inline-block';
      btnRunDemo.textContent = `\u25B6 Run "${selectedDemo}"`;
    }

    // Reset parameters form for the new demo
    resetParamsForm();

    // Also check if this can be imported from RDDMS
    const btnRddms = $('#btn-import-demo-rddms');
    if (btnRddms) {
      btnRddms.style.display = 'inline-block';
      btnRddms.textContent = `Import "${selectedDemo}" from RDDMS`;
      btnRddms.onclick = () => importDemoFromRddms(selectedDemo);
    }

    // Load wells for this demo (new: shows well/log matrix before running)
    setStatus(importStat, 'info', `Loading wells for "${selectedDemo}"...`);
    try {
      const data = await api('GET', `/demos/${encodeURIComponent(selectedDemo)}/wells`);
      importedWells = data;
      showWellsSummary({
        well_count: data.n_wells,
        well_names: data.wells.map(w => w.name),
        data_names: data.all_data_names || [],
        region_names: data.all_region_names || [],
      });
      // Show per-well log availability
      wellDetails = data.wells;
      // Apply demo-specific recommended options to Parameters form
      if (data.recommended_options) applyOptionsToForm(data.recommended_options);
      if (data.ai_settings) _setAiSettings(data.ai_settings);
      enableAfterImport();
      setStatus(importStat, 'ok', `${data.n_wells} wells loaded from demo "${selectedDemo}" — running auto-correlation...`);
    } catch(e) {
      setStatus(importStat, 'warn', `Could not pre-load demo wells: ${e.message}`);
    }
    // Always trigger Quick Run after demo select (works even without pre-load)
    quickRun();
  });
  loadDemos();

  // ── Log preview ───────────────────────────────────────────────────
  function populateLogSelectors(dataLogs, regions) {
    logChannelSel.innerHTML = dataLogs.map(n =>
      `<option value="${esc(n)}">${esc(n)}</option>`
    ).join('') || '<option value="">-- no logs --</option>';
    logRegionSel.innerHTML = '<option value="">-- none --</option>' +
      regions.map(n => `<option value="${esc(n)}">${esc(n)}</option>`).join('');
  }

  function updateLogWellList(names) {
    logWellList.innerHTML = names.map((n, i) =>
      `<div class="well-chip" data-idx="${i}">${esc(n)}</div>`
    ).join('');
  }

  logWellList.addEventListener('click', e => {
    const chip = e.target.closest('.well-chip');
    if (!chip) return;
    $$('.well-chip', logWellList).forEach(c => c.classList.remove('selected'));
    chip.classList.add('selected');
    drawLogPreview(parseInt(chip.dataset.idx));
  });

  logChannelSel.addEventListener('change', () => {
    const sel = $('.well-chip.selected', logWellList);
    if (sel) drawLogPreview(parseInt(sel.dataset.idx));
  });

  logRegionSel.addEventListener('change', () => {
    const sel = $('.well-chip.selected', logWellList);
    if (sel) drawLogPreview(parseInt(sel.dataset.idx));
  });

  async function drawLogPreview(wellIdx) {
    const canvas = logCanvas;
    const ctx = canvas.getContext('2d');
    const dpr = window.devicePixelRatio || 1;
    const rect = canvas.getBoundingClientRect();
    canvas.width = rect.width * dpr;
    canvas.height = rect.height * dpr;
    ctx.scale(dpr, dpr);
    ctx.clearRect(0, 0, rect.width, rect.height);

    // Fetch well detail if not cached
    if (!wellDetails.length) {
      try {
        const info = await api('GET', '/wells');
        wellDetails = info.wells || [];
      } catch(e) {
        ctx.fillStyle = '#c62828';
        ctx.font = '13px sans-serif';
        ctx.fillText('Could not load well data', 10, 30);
        return;
      }
    }

    const well = wellDetails[wellIdx];
    if (!well) return;

    const channel = logChannelSel.value;
    if (!channel || !well.data_names.includes(channel)) {
      ctx.fillStyle = '#605e5c';
      ctx.font = '13px sans-serif';
      ctx.fillText(`No "${channel}" data for ${well.name}`, 10, 30);
      // Show available logs for this well
      ctx.fillText(`Available: ${well.data_names.join(', ')}`, 10, 50);
      return;
    }

    // Fetch actual log values
    try {
      const resp = await api('GET', `/well-data/${wellIdx}?channel=${encodeURIComponent(channel)}`);
      const values = resp.values || [];
      const depth = resp.depth || [];

      ctx.fillStyle = '#333';
      ctx.font = 'bold 13px sans-serif';
      ctx.fillText(`${well.name} — ${channel} (${values.length} samples)`, 10, 20);

      if (!values.length) return;

      const margin = {top: 40, bottom: 20, left: 50, right: 20};
      const w = rect.width - margin.left - margin.right;
      const h = rect.height - margin.top - margin.bottom;

      // Scale
      const vMin = Math.min(...values.filter(v => v !== null && isFinite(v)));
      const vMax = Math.max(...values.filter(v => v !== null && isFinite(v)));
      const dMin = depth.length ? depth[0] : 0;
      const dMax = depth.length ? depth[depth.length - 1] : values.length;

      // Draw axes
      ctx.strokeStyle = '#e1dfdd';
      ctx.lineWidth = 1;
      ctx.beginPath();
      ctx.moveTo(margin.left, margin.top);
      ctx.lineTo(margin.left, margin.top + h);
      ctx.lineTo(margin.left + w, margin.top + h);
      ctx.stroke();

      // Axis labels
      ctx.fillStyle = '#605e5c';
      ctx.font = '10px sans-serif';
      ctx.fillText(dMin.toFixed(1), 2, margin.top + 10);
      ctx.fillText(dMax.toFixed(1), 2, margin.top + h);
      ctx.fillText(vMin.toFixed(1), margin.left, margin.top + h + 14);
      ctx.fillText(vMax.toFixed(1), margin.left + w - 20, margin.top + h + 14);

      // Draw curve (depth on Y-axis, value on X-axis)
      ctx.strokeStyle = '#0078d4';
      ctx.lineWidth = 1.5;
      ctx.beginPath();
      let started = false;
      for (let i = 0; i < values.length; i++) {
        if (values[i] === null || !isFinite(values[i])) continue;
        const x = margin.left + ((values[i] - vMin) / (vMax - vMin || 1)) * w;
        const y = margin.top + ((i) / (values.length - 1 || 1)) * h;
        if (!started) { ctx.moveTo(x, y); started = true; }
        else ctx.lineTo(x, y);
      }
      ctx.stroke();
    } catch(e) {
      ctx.fillStyle = '#605e5c';
      ctx.font = '13px sans-serif';
      ctx.fillText(`${well.name} — ${channel}`, 10, 20);
      ctx.fillText(`(preview not available: ${e.message})`, 10, 38);
    }
  }

  // ── Advanced toggle ───────────────────────────────────────────────
  showAdv.addEventListener('change', () => {
    const show = showAdv.checked;
    $$('.adv').forEach(el => { el.style.display = show ? '' : 'none'; });
  });

  // ── Auto-suggest ──────────────────────────────────────────────────
  btnSuggest.addEventListener('click', async () => {
    suggestSt.textContent = 'Analyzing...';
    try {
      const data = await api('POST', '/suggest-defaults');
      if (data.options) {
        applyOptions(data.options);
        suggestSt.textContent = '\u2713 Applied';
      } else {
        suggestSt.textContent = 'No suggestions';
      }
    } catch(e) {
      suggestSt.textContent = 'Failed';
    }
  });

  function applyOptions(opts) {
    const map = {
      'var-data': '#p-var-data', 'var-weight': '#p-var-weight',
      'var-data2': '#p-var-data2', 'var-weight2': '#p-var-weight2',
      'no-crossing': '#p-no-crossing', 'same-region': '#p-same-region',
      'polarity-region': '#p-polarity-region',
      'const-gap-cost': '#p-gap-cost',
      'dist-distal': '#p-dist-distal', 'dist-facies': '#p-dist-facies',
      'band-width': '#p-band-width', 'max-cor': '#p-max-cor',
    };
    for (const [key, sel] of Object.entries(map)) {
      if (opts[key] !== undefined) {
        const el = $(sel);
        if (el) el.value = opts[key];
      }
    }
  }

  // ── Preset handling ───────────────────────────────────────────────
  paramPreset.addEventListener('change', () => {
    const preset = paramPreset.value;
    if (!preset) return;
    const presets = {
      simple: {'var-weight': 1.0},
      constrained: {'var-weight': 1.0},
      distality: {'var-weight': 1.0},
      'multi-log': {'var-weight': 0.6, 'var-weight2': 0.4},
    };
    if (presets[preset]) applyOptions(presets[preset]);
  });

  // ── Run correlation ───────────────────────────────────────────────
  btnRun.addEventListener('click', () => runCorrelation());
  if (btnRunDemo) btnRunDemo.addEventListener('click', () => runDemo());
  btnQuickRun.addEventListener('click', () => quickRun());

  async function quickRun() {
    switchTab('run');
    runSpin.style.display = 'inline';
    runProgress.style.display = 'block';
    runProgress.classList.add('indeterminate');
    setStatus(runError, '', '');
    btnQuickRun.disabled = true;
    btnRun.disabled = true;
    engineLog.textContent = '⚡ Quick Run: auto-detecting parameters...\n';

    try {
      const payload = selectedDemo ? {demo_id: selectedDemo} : {};
      const data = await api('POST', '/auto', payload);
      correlationResult = data;
      if (data.wells_plot_data) wellDetails = data.wells_plot_data;
      engineLog.textContent += `\nEnvironment: ${data.reasoning?.detected_environment || data.reasoning?.source || 'auto'}\n`;
      engineLog.textContent += `Options: ${JSON.stringify(data.suggested_options)}\n`;
      engineLog.textContent += `Completed: ${data.n_results} diverse solutions in ${data.elapsed_ms} ms\n`;
      // Update parameters form to reflect actual options used
      if (data.suggested_options) applyOptionsToForm(data.suggested_options);
      showResults(data);
      enableResultsTabs();
      switchTab('results');
    } catch(e) {
      setStatus(runError, 'err', 'Quick Run failed: ' + e.message);
      engineLog.textContent += `\nERROR: ${e.message}\n`;
    } finally {
      runSpin.style.display = 'none';
      runProgress.style.display = 'none';
      runProgress.classList.remove('indeterminate');
      btnQuickRun.disabled = false;
      btnRun.disabled = false;
    }
  }

  async function runCorrelation() {
    runSpin.style.display = 'inline';
    runProgress.style.display = 'block';
    runProgress.classList.add('indeterminate');
    setStatus(runError, '', '');
    btnRun.disabled = true;
    engineLog.textContent = 'Starting correlation engine...\n';

    const options = gatherOptions();
    const nBest = parseInt($('#p-n-best').value) || 5;
    const wellNamesList = selectedWells.size > 0 ? Array.from(selectedWells) : null;

    try {
      engineLog.textContent += `Options: ${JSON.stringify(options)}\nN-best: ${nBest}\n`;
      if (wellNamesList) engineLog.textContent += `Wells: ${wellNamesList.length} selected\n`;
      const data = await api('POST', '/run', { options, n_best: nBest, well_names: wellNamesList });
      correlationResult = data;
      if (data.wells_plot_data) wellDetails = data.wells_plot_data;
      engineLog.textContent += `\nCompleted: ${data.n_results} solutions in ${data.elapsed_ms} ms\n`;
      engineLog.textContent += `Mode: ${data.mode || 'in-process'}\n`;
      if (data.options_used) engineLog.textContent += `Options applied: ${JSON.stringify(data.options_used)}\n`;
      showResults(data);
      enableResultsTabs();
      switchTab('results');
    } catch(e) {
      setStatus(runError, 'err', 'Correlation failed: ' + e.message);
      engineLog.textContent += `\nERROR: ${e.message}\n`;
    } finally {
      runSpin.style.display = 'none';
      runProgress.style.display = 'none';
      runProgress.classList.remove('indeterminate');
      btnRun.disabled = false;
    }
  }

  async function runDemo() {
    if (!selectedDemo) return;
    runSpin.style.display = 'inline';
    runProgress.style.display = 'block';
    runProgress.classList.add('indeterminate');
    setStatus(runError, '', '');
    engineLog.textContent = `Running demo "${selectedDemo}"...\n`;
    switchTab('run');

    const options = gatherOptions();
    const nBest = parseInt($('#p-n-best').value) || 5;
    const wellNamesList = selectedWells.size > 0 ? Array.from(selectedWells) : null;

    try {
      // Use /run (with user options + well selection) since wells are pre-loaded
      engineLog.textContent += `Options: ${JSON.stringify(options)}\nN-best: ${nBest}\n`;
      if (wellNamesList) engineLog.textContent += `Wells: ${wellNamesList.length} selected\n`;
      const data = await api('POST', '/run', { options, n_best: nBest, well_names: wellNamesList });
      correlationResult = data;
      if (data.wells_plot_data) wellDetails = data.wells_plot_data;
      engineLog.textContent += `\nCompleted: ${data.n_results || '?'} solutions in ${data.elapsed_ms} ms\n`;
      engineLog.textContent += `Mode: ${data.mode || 'in-process'}\n`;
      engineLog.textContent += `Options: ${JSON.stringify(data.options_used || {})}\n`;

      showResults(data);
      enableResultsTabs();
      switchTab('results');
    } catch(e) {
      setStatus(runError, 'err', 'Demo failed: ' + e.message);
      engineLog.textContent += `\nERROR: ${e.message}\n`;
    } finally {
      runSpin.style.display = 'none';
      runProgress.style.display = 'none';
      runProgress.classList.remove('indeterminate');
    }
  }

  // ── Import demo from RDDMS ─────────────────────────────────────────
  async function importDemoFromRddms(demoId) {
    importSpin.style.display = 'inline';
    const ds = dsSel.value || _defaultDs;
    setStatus(importStat, 'info', `Importing "${demoId}" from RDDMS (${ds})...`);
    try {
      const data = await api('POST', `/import/demo?demo_id=${encodeURIComponent(demoId)}&dataspace=${encodeURIComponent(ds)}`);
      importedWells = data;
      showWellsSummary(data);
      setStatus(importStat, 'ok', `Imported ${data.well_count} wells from RDDMS (demo: ${demoId})`);
      enableAfterImport();
    } catch(e) {
      const msg = e.message || '';
      if (msg.includes('no healthy upstream') || msg.includes('502') || msg.includes('503') || msg.includes('No wells')) {
        setStatus(importStat, 'err', `Dataspace "${ds}" not available. Try running local demo instead.`);
      } else {
        setStatus(importStat, 'err', `RDDMS import failed: ${msg}. Try running local demo instead.`);
      }
    } finally {
      importSpin.style.display = 'none';
    }
  }

  function resetParamsForm() {
    /**Clear all parameter fields to defaults before applying new demo options.*/
    const set = (sel, v) => { const el = $(sel); if (el) el.value = v; };
    set('#p-var-data', '');
    set('#p-var-weight', '');
    set('#p-var-data2', '');
    set('#p-var-weight2', '');
    set('#p-no-crossing', '');
    set('#p-same-region', '');
    set('#p-gap-cost', '');
    set('#p-polarity-region', '');
    set('#p-dist-distal', '');
    set('#p-dist-facies', '');
    set('#p-band-width', '');
    set('#p-max-cor', '');
    set('#p-n-best', '5');
    // Hide advanced fields (demo-specific applyOptionsToForm will re-show relevant ones)
    $$('.adv').forEach(el => { el.style.display = 'none'; });
    if (showAdv) showAdv.checked = false;
  }

  function applyOptionsToForm(opts) {
    /**Apply recommended/demo-specific options to the Parameters form fields.
     * Also auto-reveals advanced fields that have meaningful values.*/
    const set = (sel, v) => { const el = $(sel); if (el && v != null) el.value = v; };
    // Primary log
    set('#p-var-data', opts['var-data'] || '');
    set('#p-var-weight', opts['var-weight'] != null ? opts['var-weight'] : '');
    // Secondary log
    set('#p-var-data2', opts['var-data2'] || '');
    set('#p-var-weight2', opts['var-weight2'] != null ? opts['var-weight2'] : '');
    // Constraints
    set('#p-no-crossing', opts['no-crossing'] || '');
    set('#p-same-region', opts['same-region'] || '');
    // Cost modifiers
    set('#p-gap-cost', opts['const-gap-cost'] != null ? opts['const-gap-cost'] : '');
    set('#p-polarity-region', opts['polarity-region'] || '');
    // Distality
    set('#p-dist-distal', opts['dist-distal'] || '');
    set('#p-dist-facies', opts['dist-facies'] || '');
    // Engine tuning
    set('#p-band-width', opts['band-width'] != null ? opts['band-width'] : '');
    set('#p-max-cor', opts['max-cor'] != null ? opts['max-cor'] : '');
    set('#p-n-best', opts['nbr-cor'] || opts['out-nbr-cor'] || 5);

    // Auto-reveal advanced fields if demo uses them
    const hasAdvanced = opts['var-data2'] || opts['const-gap-cost'] ||
        opts['dist-distal'] || opts['dist-facies'] || opts['band-width'] ||
        opts['polarity-region'];
    if (hasAdvanced) {
      $$('.adv').forEach(el => { el.style.display = ''; });
      if (showAdv) showAdv.checked = true;
    }
  }

  function gatherOptions() {
    const opts = {};
    const val = (sel) => { const el = $(sel); return el ? el.value : ''; };

    if (val('#p-var-data'))  opts['var-data'] = val('#p-var-data');
    const w1 = parseFloat(val('#p-var-weight'));
    if (!isNaN(w1) && w1 !== 1.0) opts['var-weight'] = w1;
    if (val('#p-no-crossing'))  opts['no-crossing'] = val('#p-no-crossing');
    if (val('#p-same-region'))  opts['same-region'] = val('#p-same-region');
    if (val('#p-var-data2'))  opts['var-data2'] = val('#p-var-data2');
    const w2 = parseFloat(val('#p-var-weight2'));
    if (val('#p-var-data2') && !isNaN(w2)) opts['var-weight2'] = w2;
    const gc = parseFloat(val('#p-gap-cost'));
    if (!isNaN(gc) && gc !== 0) opts['const-gap-cost'] = gc;
    if (val('#p-polarity-region'))  opts['polarity-region'] = val('#p-polarity-region');
    if (val('#p-dist-distal'))  opts['dist-distal'] = val('#p-dist-distal');
    if (val('#p-dist-facies'))  opts['dist-facies'] = val('#p-dist-facies');
    const bw = parseInt(val('#p-band-width'));
    if (!isNaN(bw) && bw > 0) opts['band-width'] = bw;
    const mc = parseInt(val('#p-max-cor'));
    if (!isNaN(mc) && mc > 0 && mc !== 50) opts['max-cor'] = mc;

    return opts;
  }

  function enableResultsTabs() {
    tabs.forEach(t => {
      if (['results', 'export'].includes(t.dataset.tab)) t.classList.remove('disabled');
    });
  }

  // ── Plot controls toolbar (injected above canvas) ─────────────────
  function ensurePlotControls() {
    if ($('#corr-plot-controls')) return;
    const container = corrCanvas.parentElement;
    const bar = document.createElement('div');
    bar.id = 'corr-plot-controls';
    bar.style.cssText = 'display:flex;gap:8px;align-items:center;flex-wrap:wrap;padding:4px 8px;background:#f8f8f8;border:1px solid #e0e0e0;border-radius:4px;margin-bottom:4px;font-size:11px;';
    bar.innerHTML = `
      <label style="font-weight:600;">Align:</label>
      <select id="ctrl-align" style="font-size:11px;padding:1px 4px;">
        <option value="marker">By marker (fill view)</option>
        <option value="absolute">Absolute depth</option>
      </select>
      <label style="font-weight:600;margin-left:8px;">Logs:</label>
      <select id="ctrl-logs" multiple style="font-size:10px;min-width:100px;max-height:50px;"></select>
      <label><input type="checkbox" id="ctrl-discrete" checked> Discrete</label>
      <label><input type="checkbox" id="ctrl-strat"> StratCol</label>
      <label><input type="checkbox" id="ctrl-global-strat"> Global</label>
      <label><input type="checkbox" id="ctrl-md" checked> MD</label>
      <label><input type="checkbox" id="ctrl-tvdss"> TVDSS</label>
      <button id="btn-download-png" style="margin-left:auto;font-size:10px;padding:2px 8px;cursor:pointer;border:1px solid #ccc;border-radius:3px;background:#fff;" title="Download plot as PNG">📥 PNG</button>
    `;
    container.insertBefore(bar, corrCanvas);

    // Wire up controls
    const ctrlAlign = bar.querySelector('#ctrl-align');
    ctrlAlign.addEventListener('change', () => {
      corrPlotConfig.alignMode = ctrlAlign.value;
      window.redrawCorrelationPlot();
    });
    bar.querySelector('#ctrl-discrete').addEventListener('change', (e) => {
      corrPlotConfig.showDiscrete = e.target.checked;
      window.redrawCorrelationPlot();
    });
    bar.querySelector('#ctrl-strat').addEventListener('change', (e) => {
      corrPlotConfig.showStratColumn = e.target.checked;
      window.redrawCorrelationPlot();
    });
    bar.querySelector('#ctrl-global-strat').addEventListener('change', async (e) => {
      corrPlotConfig.showGlobalStrat = e.target.checked;
      if (e.target.checked && !cachedGlobalStrat) {
        try {
          const sc = await api('GET', '/strat-column');
          if (sc.loaded) cachedGlobalStrat = sc;
        } catch(err) { /* no strat column loaded */ }
      }
      window.redrawCorrelationPlot();
    });
    bar.querySelector('#ctrl-md').addEventListener('change', (e) => {
      corrPlotConfig.showMD = e.target.checked;
      window.redrawCorrelationPlot();
    });
    bar.querySelector('#ctrl-tvdss').addEventListener('change', (e) => {
      corrPlotConfig.showTVDSS = e.target.checked;
      window.redrawCorrelationPlot();
    });

    // Log multi-select
    const logSelect = bar.querySelector('#ctrl-logs');
    logSelect.addEventListener('change', () => {
      const selected = [...logSelect.selectedOptions].map(o => o.value);
      corrPlotConfig.showLogs = selected.length > 0 ? selected : null;
      window.redrawCorrelationPlot();
    });
    // Download PNG button
    bar.querySelector("#btn-download-png").addEventListener("click", () => {
      const canvas = document.getElementById("corr-canvas");
      if (!canvas) return;
      canvas.toBlob((blob) => {
        const url = URL.createObjectURL(blob);
        const a = document.createElement("a");
        a.href = url;
        a.download = "weco_correlation_plot.png";
        a.click();
        URL.revokeObjectURL(url);
      }, "image/png");
    });
  }

  function updateLogSelector(plotData) {
    const logSelect = $('#ctrl-logs');
    if (!logSelect) return;
    const allLogs = new Set();
    for (const wd of plotData) {
      const names = wd.log_names || Object.keys(wd.logs || {});
      for (const n of names) allLogs.add(n);
    }
    const skip = new Set(['Depth', 'DEPTH', 'depth', 'MD', 'TVD', 'TVDSS']);
    logSelect.innerHTML = [...allLogs]
      .filter(n => !skip.has(n))
      .map(n => `<option value="${n}">${n}</option>`)
      .join('');
  }

  // ── Results display ───────────────────────────────────────────────
  const corrCanvas = $('#corr-canvas');
  const resultsPlot = $('#results-plot');
  const resultsComposite = $('#results-composite');
  const btnViewPlot = $('#btn-view-plot');
  const btnViewComposite = $('#btn-view-composite');
  const btnViewTable = $('#btn-view-table');

  btnViewPlot.addEventListener('click', () => {
    resultsPlot.style.display = 'block';
    resultsComposite.style.display = 'none';
    resCards.style.display = 'none';
  });
  btnViewComposite.addEventListener('click', () => {
    resultsPlot.style.display = 'none';
    resultsComposite.style.display = 'block';
    resCards.style.display = 'none';
    drawCompositeView();
  });
  btnViewTable.addEventListener('click', () => {
    resultsPlot.style.display = 'none';
    resultsComposite.style.display = 'none';
    resCards.style.display = 'block';
  });

  function drawCompositeView() {
    if (!correlationResult) return;
    const results = correlationResult.results || [];
    const nPanels = Math.min(3, results.length);
    for (let p = 0; p < nPanels; p++) {
      const canvas = $(`#comp-canvas-${p}`);
      if (canvas) {
        drawCorrelationPlot(correlationResult, p, canvas);
      }
    }
  }

  function showResults(data) {
    resEmpty.style.display = 'none';
    resSummary.style.display = 'block';
    // Show demo name / well list context
    const resCtx = $('#res-context');
    if (resCtx) {
      if (selectedDemo) {
        resCtx.textContent = selectedDemo;
      } else if (data.well_names && data.well_names.length <= 6) {
        resCtx.textContent = data.well_names.join(', ');
      } else if (data.well_names) {
        resCtx.textContent = data.well_names.slice(0, 4).join(', ') + ` (+${data.well_names.length - 4})`;
      } else {
        resCtx.textContent = '';
      }
    }
    resNWells.textContent = data.n_wells || '?';
    resNRes.textContent = data.n_results || 0;
    resElapsed.textContent = data.elapsed_ms || '-';
    resMode.textContent = data.mode || 'in-process';

    const results = data.results || [];
    resSelector.innerHTML = results.map((r, i) =>
      `<option value="${i}">#${i+1} (cost: ${r.cost != null ? r.cost.toFixed(4) : '-'})</option>`
    ).join('');

    // Build cost ranking display
    if (results.length > 1) {
      const maxCost = Math.max(...results.map(r => r.cost || 0));
      let rankHtml = '<strong>Cost Ranking (lowest = best):</strong><br>';
      results.forEach((r, i) => {
        const pct = maxCost > 0 ? ((r.cost || 0) / maxCost * 100) : 0;
        const sel = i === 0 ? ' style="background:#e3f2fd; font-weight:bold;"' : '';
        rankHtml += `<div class="rank-row" data-idx="${i}"${sel}>` +
          `#${i+1} cost=${(r.cost != null ? r.cost.toFixed(4) : '-')} ` +
          `<span style="display:inline-block;height:8px;width:${Math.max(pct, 2)}%;background:#1565c0;border-radius:2px;vertical-align:middle;"></span></div>`;
      });
      resRanking.innerHTML = rankHtml;
      resRanking.style.display = 'block';

      // Clickable ranking rows
      resRanking.querySelectorAll('.rank-row').forEach(row => {
        row.style.cursor = 'pointer';
        row.addEventListener('click', () => {
          const idx = parseInt(row.dataset.idx);
          resSelector.value = idx;
          resSelector.dispatchEvent(new Event('change'));
        });
      });
    } else {
      resRanking.style.display = 'none';
    }

    // Show first result
    updateResultView(results, 0, data);

    resSelector.onchange = () => {
      const idx = parseInt(resSelector.value);
      updateResultView(results, idx, data);
    };

    btnResPrev.onclick = () => {
      const idx = parseInt(resSelector.value) || 0;
      if (idx > 0) { resSelector.value = idx - 1; resSelector.dispatchEvent(new Event('change')); }
    };
    btnResNext.onclick = () => {
      const idx = parseInt(resSelector.value) || 0;
      if (idx < results.length - 1) { resSelector.value = idx + 1; resSelector.dispatchEvent(new Event('change')); }
    };
  }

  function updateResultView(results, idx, data) {
    renderResultCard(results, idx, data.well_names);
    // Ensure plot container is visible before measuring canvas
    resultsPlot.style.display = 'block';
    // Defer drawing until the browser has laid out the canvas
    requestAnimationFrame(() => {
      requestAnimationFrame(() => {
        drawCorrelationPlot(data, idx);
      });
    });
    // Update cost label and highlight in ranking
    const r = results[idx];
    resCostLabel.textContent = r ? `Cost: ${r.cost != null ? r.cost.toFixed(4) : '-'} | ${(r.lines||[]).length} lines` : '';
    // Highlight active row in ranking
    resRanking.querySelectorAll('.rank-row').forEach((row, i) => {
      row.style.background = i === idx ? '#e3f2fd' : '';
      row.style.fontWeight = i === idx ? 'bold' : '';
    });
    // AI analysis (if enabled)
    _runAiAnalysis(idx);
  }

  // ── AI Analysis ─────────────────────────────────────────────────────
  let _aiSettings = {quality: true, anomaly: false, uncertainty: false};

  function _setAiSettings(settings) {
    if (settings) Object.assign(_aiSettings, settings);
  }

  function _runAiAnalysis(corIndex) {
    const panel = document.getElementById('ai-results-panel');
    if (!panel) return;
    if (!_aiSettings.quality && !_aiSettings.anomaly && !_aiSettings.uncertainty) {
      panel.style.display = 'none';
      return;
    }
    panel.style.display = 'block';
    panel.innerHTML = '<em style="font-size:11px; color:#888;">Running AI analysis...</em>';

    fetch('/weco/ai/analyse', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({
        quality: _aiSettings.quality,
        anomaly: _aiSettings.anomaly,
        uncertainty: _aiSettings.uncertainty,
        cor_index: corIndex,
      }),
    })
    .then(r => r.ok ? r.json() : r.json().then(e => { throw new Error(e.detail || 'AI error'); }))
    .then(data => {
      let html = '<div style="font-size:11px; padding:4px 8px; background:#f3e5f5; border:1px solid #ce93d8; border-radius:4px;">';
      html += '<strong>AI Analysis</strong><br>';
      if (data.quality) {
        html += `★ Quality: <b>${data.quality.overall.toFixed(2)}</b> `;
        html += `(cost=${data.quality.cost_score.toFixed(2)}, gaps=${data.quality.gap_score.toFixed(2)}, sim=${data.quality.similarity_score.toFixed(2)})<br>`;
      }
      if (data.anomaly) {
        if (data.anomaly.n_flagged > 0) {
          html += `⚠ Anomaly: <b>${data.anomaly.n_flagged}</b> suspicious line(s) flagged<br>`;
        } else {
          html += `✓ No anomalous lines detected<br>`;
        }
      }
      if (data.uncertainty) {
        html += `↔ Uncertainty: mean spread=${data.uncertainty.mean_spread.toFixed(2)}, max=${data.uncertainty.max_spread.toFixed(2)}<br>`;
      }
      html += '</div>';
      panel.innerHTML = html;
    })
    .catch(err => {
      panel.innerHTML = `<span style="font-size:11px; color:#c62828;">[AI: ${err.message}]</span>`;
    });
  }

  function renderResultCard(results, idx, wellNames) {
    if (!results[idx]) { resCards.innerHTML = ''; return; }
    const r = results[idx];
    // lines[] format: each line has markers[] array
    const lines = r.lines || [];
    const names = wellNames || [];

    let html = `<div class="result-card">
      <div style="display:flex;justify-content:space-between;align-items:center;">
        <span class="result-rank">#${idx+1}</span>
        <span class="result-cost">Cost: ${r.cost != null ? r.cost.toFixed(4) : '-'} | ${lines.length} correlation lines</span>
      </div>`;

    if (lines.length && names.length) {
      html += '<table class="corr-table"><thead><tr><th>Line</th>';
      names.forEach(n => { html += `<th>${esc(n)}</th>`; });
      html += '</tr></thead><tbody>';
      lines.forEach((line, li) => {
        const markers = line.markers || line;
        html += `<tr><td style="font-weight:600;">${li+1}</td>`;
        if (Array.isArray(markers)) {
          markers.forEach(v => { html += `<td>${v != null ? v : '-'}</td>`; });
        }
        html += '</tr>';
      });
      html += '</tbody></table>';
    }
    html += '</div>';
    resCards.innerHTML = html;
  }

  // ── Correlation Plot Configuration ──────────────────────────────
  // Plot display options (can be toggled via UI controls)
  const corrPlotConfig = {
    alignMode: 'marker',     // 'absolute' | 'marker' (align by top marker/log start)
    alignMarker: 'top',      // 'top' = first boundary marker or log start
    showLogs: null,          // null = auto-select, or array of log names
    showDiscrete: true,      // show discrete logs (biozones) as separate track
    showStratColumn: false,  // render stratcolumn strip
    showGlobalStrat: false,  // render global strat column reference strip (left side)
    showMD: true,            // show MD depth ticks per well
    showTVDSS: false,        // show TVDSS if available
    logScaleLogs: ['RT', 'RDEEP', 'RSHAL', 'RES', 'RLLD', 'RLLS'],  // logs drawn with log10 scale
    maxContinuousLogs: 3,    // max continuous log traces per well
    maxDiscreteLogs: 2,      // max discrete/zone tracks per well
  };

  // Cached global strat column data
  let cachedGlobalStrat = null;

  // Expose config for external UI controls
  window.corrPlotConfig = corrPlotConfig;
  window.redrawCorrelationPlot = () => {
    if (correlationResult) {
      const idx = parseInt(resSelector.value) || 0;
      drawCorrelationPlot(correlationResult, idx);
    }
  };

  // ── Correlation Plot (canvas-based) ───────────────────────────────
  async function drawCorrelationPlot(data, resultIdx, targetCanvas) {
    const canvas = targetCanvas || corrCanvas;
    const ctx = canvas.getContext('2d');
    const dpr = window.devicePixelRatio || 1;
    const rect = canvas.getBoundingClientRect();
    // Fallback if canvas not yet laid out (tab just became visible)
    const cw = rect.width > 0 ? rect.width : canvas.parentElement.clientWidth || 800;
    const ch = rect.height > 0 ? rect.height : (targetCanvas ? 320 : 560);
    canvas.width = cw * dpr;
    canvas.height = ch * dpr;
    ctx.scale(dpr, dpr);
    ctx.clearRect(0, 0, cw, ch);

    // Draw panel title for composite view
    if (targetCanvas) {
      const cost = (data.results || [])[resultIdx]?.cost;
      const title = `Result #${resultIdx + 1}` + (cost != null ? ` (cost: ${cost.toFixed(4)})` : '');
      ctx.fillStyle = '#323130';
      ctx.font = 'bold 11px sans-serif';
      ctx.textAlign = 'center';
      ctx.fillText(title, cw / 2, 12);
      ctx.textAlign = 'start';
    }

    const nWells = data.n_wells || 0;
    const wellNames = data.well_names || [];
    const results = data.results || [];
    const result = results[resultIdx];
    if (!result || !nWells) return;

    // Get well plot data (depths + logs + regions)
    let plotData = data.wells_plot_data;
    if (!plotData) {
      try {
        const pd = await api('GET', '/plot-data');
        plotData = pd.wells;
      } catch(e) {
        ctx.fillStyle = '#c62828';
        ctx.font = '13px sans-serif';
        ctx.fillText('Could not load plot data: ' + e.message, 10, 30);
        return;
      }
    }

    // Inject plot controls toolbar and populate log selector
    ensurePlotControls();
    updateLogSelector(plotData);

    // ── Classify logs into continuous vs discrete ───────────────────
    const discreteLogNames = new Set();
    const continuousLogNames = [];
    const allLogNames = new Set();

    for (const wd of plotData) {
      const logs = wd.logs || {};
      const names = wd.log_names || Object.keys(logs);
      for (const n of names) allLogNames.add(n);
    }

    // Heuristic: discrete if values are integers only or few unique values
    for (const lname of allLogNames) {
      let isDiscrete = false;
      for (const wd of plotData) {
        const vals = (wd.logs || {})[lname];
        if (!vals || vals.length < 2) continue;
        const valid = vals.filter(v => v != null);
        if (valid.length === 0) continue;
        const unique = new Set(valid);
        // Discrete: few unique values relative to count, or name suggests it
        const discreteNames = ['FACIES', 'LITH', 'ZONE', 'BIOZONE', 'BIOSTRAT',
                               'SEQUENCE', 'STRAT', 'FORMATION', 'REGION', 'CLASS'];
        if (discreteNames.some(dn => lname.toUpperCase().includes(dn))) {
          isDiscrete = true; break;
        }
        if (unique.size <= 20 && unique.size < valid.length * 0.1) {
          isDiscrete = true; break;
        }
      }
      if (isDiscrete) discreteLogNames.add(lname);
      else continuousLogNames.push(lname);
    }

    // Select which logs to show
    let showContinuous = corrPlotConfig.showLogs;
    if (!showContinuous) {
      const priority = ['GR', 'RT', 'DEN', 'SPT', 'CAL', 'SON', 'NEU', 'COND', 'MS', 'WC'];
      showContinuous = priority.filter(l => continuousLogNames.includes(l));
      for (const l of continuousLogNames) {
        if (!showContinuous.includes(l)) showContinuous.push(l);
      }
      showContinuous = showContinuous.slice(0, corrPlotConfig.maxContinuousLogs);
    }

    const showDiscrete = corrPlotConfig.showDiscrete
      ? [...discreteLogNames].slice(0, corrPlotConfig.maxDiscreteLogs)
      : [];

    // ── Layout calculation ──────────────────────────────────────────
    const margin = {top: 52, bottom: 30, left: 50, right: 15};
    const W = cw - margin.left - margin.right;
    const H = ch - margin.top - margin.bottom;

    // Each well gets: [discrete tracks] [gap] [continuous tracks] [gap between wells for corr lines]
    const nContTracks = Math.max(showContinuous.length, 1);
    const nDiscTracks = showDiscrete.length;
    const nStratTrack = (corrPlotConfig.showStratColumn && plotData[0] && plotData[0].region_names) ? 1 : 0;
    const trackWidth = 50;  // pixels per log track
    const discreteTrackWidth = 25;  // narrower for discrete
    const stratTrackWidth = 20;
    const baseGapWidth = 40;  // default gap between wells for correlation lines

    // Compute proportional gap widths from well coordinates (V8)
    let gapWidths = Array(Math.max(0, nWells - 1)).fill(baseGapWidth);
    if (plotData.length >= 2 && plotData[0].x != null && plotData[0].y != null) {
      const dists = [];
      for (let gi = 0; gi < nWells - 1; gi++) {
        const dx = (plotData[gi + 1].x || 0) - (plotData[gi].x || 0);
        const dy = (plotData[gi + 1].y || 0) - (plotData[gi].y || 0);
        dists.push(Math.sqrt(dx * dx + dy * dy));
      }
      const maxDist = Math.max(...dists, 1);
      if (maxDist > 0 && dists.some(d => d > 0)) {
        const minGap = 25, maxGap = 80;
        gapWidths = dists.map(d => minGap + (d / maxDist) * (maxGap - minGap));
      }
    }
    const totalGapWidth = gapWidths.reduce((a, b) => a + b, 0);

    const wellTotalWidth = nContTracks * trackWidth + nDiscTracks * discreteTrackWidth + nStratTrack * stratTrackWidth;
    const totalPlotWidth = nWells * wellTotalWidth + totalGapWidth;

    // Scale if doesn't fit
    let scale = 1.0;
    if (totalPlotWidth > W) {
      scale = W / totalPlotWidth;
    }
    const sTrackW = trackWidth * scale;
    const sDiscW = discreteTrackWidth * scale;
    const sStratW = stratTrackWidth * scale;
    const sGapWidths = gapWidths.map(g => g * scale);
    const sWellW = wellTotalWidth * scale;
    const sTotalW = totalPlotWidth * scale;
    const offsetX = margin.left + (W - sTotalW) / 2;

    // Compute X positions for each well and its tracks
    const wellLayout = [];  // {x, contTracks: [{x, w}], discTracks: [{x, w}], stratTrack: {x, w}}
    let curX = offsetX;
    for (let i = 0; i < nWells; i++) {
      const wl = {x: curX, contTracks: [], discTracks: [], stratTrack: null, centerX: 0, rightEdge: 0};
      // Strat column first (leftmost)
      if (nStratTrack) {
        wl.stratTrack = {x: curX, w: sStratW};
        curX += sStratW;
      }
      // Discrete tracks
      for (let d = 0; d < nDiscTracks; d++) {
        wl.discTracks.push({x: curX, w: sDiscW});
        curX += sDiscW;
      }
      // Continuous log tracks
      for (let c = 0; c < nContTracks; c++) {
        wl.contTracks.push({x: curX, w: sTrackW});
        curX += sTrackW;
      }
      wl.rightEdge = curX;
      wl.centerX = wl.x + (curX - wl.x) / 2;
      wellLayout.push(wl);
      // Gap for correlation lines (except after last well)
      if (i < nWells - 1) curX += sGapWidths[i];
    }

    // ── Depth alignment ─────────────────────────────────────────────
    // Compute per-well depth offset for marker-based alignment
    const wellDepthOffset = new Array(nWells).fill(0);
    const wellDepthArrays = plotData.map(wd => wd.depth || null);

    if (corrPlotConfig.alignMode === 'marker') {
      // Find alignment reference: first boundary line marker depth, or top of log
      const lines = result.lines || [];
      const firstBoundary = lines.find(l => (l.line_type || 'framework') === 'boundary');

      for (let i = 0; i < nWells; i++) {
        const wd = plotData[i];
        const depth = wd.depth;
        if (!depth || depth.length === 0) continue;

        let alignDepth = depth[0]; // default: start of log
        if (firstBoundary) {
          const markers = firstBoundary.markers || firstBoundary;
          const mIdx = Array.isArray(markers) ? markers[i] : null;
          if (mIdx != null && mIdx >= 0 && mIdx < depth.length) {
            alignDepth = depth[mIdx];
          }
        }
        wellDepthOffset[i] = alignDepth;
      }
    }

    // Compute aligned depth ranges per well, then global visible range
    let alignedMin = Infinity, alignedMax = -Infinity;
    for (let i = 0; i < nWells; i++) {
      const wd = plotData[i];
      const depth = wd.depth;
      if (!depth || depth.length === 0) {
        alignedMin = Math.min(alignedMin, 0);
        alignedMax = Math.max(alignedMax, wd.size || 100);
        continue;
      }
      const first = depth[0] - wellDepthOffset[i];
      const last = depth[depth.length - 1] - wellDepthOffset[i];
      alignedMin = Math.min(alignedMin, first);
      alignedMax = Math.max(alignedMax, last);
    }
    if (!isFinite(alignedMin)) { alignedMin = 0; alignedMax = 100; }
    // Add small padding
    const depthRange = alignedMax - alignedMin;
    const pad = depthRange * 0.03;
    alignedMin -= pad;
    alignedMax += pad;

    // Depth-to-Y: maps aligned depth to pixel Y
    const depthToY = (d, wellIdx) => {
      const aligned = d - wellDepthOffset[wellIdx];
      return margin.top + ((aligned - alignedMin) / (alignedMax - alignedMin)) * H;
    };

    // Zone color palette (Set3-like pastels)
    const zonePalette = [
      '#8dd3c7','#ffffb3','#bebada','#fb8072','#80b1d3',
      '#fdb462','#b3de69','#fccde5','#d9d9d9','#bc80bd','#ccebc5','#ffed6f'
    ];

    // Log trace colors for continuous logs
    const logColors = ['#1565c0', '#c62828', '#2e7d32', '#6a1b9a', '#e65100', '#00695c', '#bf360c'];

    // ── Draw each well ──────────────────────────────────────────────
    for (let i = 0; i < nWells; i++) {
      const wd = plotData[i];
      const wl = wellLayout[i];
      const depth = wd.depth;

      // Well name header
      ctx.fillStyle = '#333';
      ctx.font = 'bold 10px sans-serif';
      ctx.textAlign = 'center';
      ctx.fillText(wd.name, wl.centerX, margin.top - 22);

      // MD / TVDSS labels
      if (corrPlotConfig.showMD && depth && depth.length > 0) {
        ctx.font = '7px sans-serif';
        ctx.fillStyle = '#888';
        const topD = depth[0];
        const botD = depth[depth.length - 1];
        ctx.textAlign = 'center';
        ctx.fillText(`MD: ${topD.toFixed(0)}-${botD.toFixed(0)}`, wl.centerX, margin.top - 12);
      }
      if (corrPlotConfig.showTVDSS && wd.tvdss && wd.tvdss.length > 0) {
        ctx.font = '7px sans-serif';
        ctx.fillStyle = '#668';
        const topT = wd.tvdss[0];
        const botT = wd.tvdss[wd.tvdss.length - 1];
        ctx.textAlign = 'center';
        ctx.fillText(`TVDSS: ${topT.toFixed(0)}-${botT.toFixed(0)}`, wl.centerX, margin.top - 4);
      }

      // Well column border (covers all tracks)
      const y0 = depth ? depthToY(depth[0], i) : depthToY(0, i);
      const y1 = depth ? depthToY(depth[depth.length - 1], i) : depthToY(wd.size, i);
      ctx.strokeStyle = '#ddd';
      ctx.lineWidth = 0.5;
      ctx.strokeRect(wl.x, y0, wl.rightEdge - wl.x, y1 - y0);

      // ── Stratcolumn strip ────────────────────────────────────────
      if (wl.stratTrack && corrPlotConfig.showStratColumn) {
        const st = wl.stratTrack;
        const regions = wd.regions || {};
        const regionNames = wd.region_names || Object.keys(regions);
        if (regionNames.length > 0) {
          const rname = regionNames[0];
          const rvals = regions[rname];
          if (rvals && rvals.length) {
            const uniqueZones = [...new Set(rvals.filter(v => v != null && v !== ''))];
            const zoneColorMap = {};
            uniqueZones.forEach((z, zi) => { zoneColorMap[z] = zonePalette[zi % zonePalette.length]; });

            let prevVal = rvals[0], startIdx = 0;
            for (let s = 1; s <= rvals.length; s++) {
              if (s === rvals.length || rvals[s] !== prevVal) {
                if (prevVal && zoneColorMap[prevVal]) {
                  const yt = depthToY(depth ? depth[startIdx] : startIdx, i);
                  const yb = depthToY(depth ? depth[Math.min(s - 1, (depth || []).length - 1)] : s - 1, i);
                  ctx.fillStyle = zoneColorMap[prevVal];
                  ctx.globalAlpha = 0.8;
                  ctx.fillRect(st.x, yt, st.w, yb - yt);
                  ctx.globalAlpha = 1.0;
                  // Thin border between zones
                  ctx.strokeStyle = '#999';
                  ctx.lineWidth = 0.3;
                  ctx.strokeRect(st.x, yt, st.w, yb - yt);
                }
                if (s < rvals.length) { prevVal = rvals[s]; startIdx = s; }
              }
            }
          }
        }
      }

      // ── Discrete log tracks (biozones, facies) ───────────────────
      const logs = wd.logs || {};
      for (let di = 0; di < showDiscrete.length; di++) {
        const dtrack = wl.discTracks[di];
        if (!dtrack) continue;
        const lname = showDiscrete[di];
        const vals = logs[lname];
        if (!vals || vals.length === 0) continue;

        // Build unique value → color mapping
        const unique = [...new Set(vals.filter(v => v != null && v !== ''))];
        const colorMap = {};
        unique.forEach((v, vi) => { colorMap[v] = zonePalette[vi % zonePalette.length]; });

        // Draw as colour strips (contiguous runs)
        let prevVal = vals[0], startIdx = 0;
        for (let s = 1; s <= vals.length; s++) {
          if (s === vals.length || vals[s] !== prevVal) {
            if (prevVal != null && prevVal !== '' && colorMap[prevVal]) {
              const yt = depthToY(depth ? depth[startIdx] : startIdx, i);
              const yb = depthToY(depth ? depth[Math.min(s - 1, (depth || []).length - 1)] : s - 1, i);
              ctx.fillStyle = colorMap[prevVal];
              ctx.globalAlpha = 0.7;
              ctx.fillRect(dtrack.x, yt, dtrack.w, yb - yt);
              ctx.globalAlpha = 1.0;
            }
            if (s < vals.length) { prevVal = vals[s]; startIdx = s; }
          }
        }

        // Track header
        ctx.font = '6px sans-serif';
        ctx.fillStyle = '#555';
        ctx.textAlign = 'center';
        ctx.fillText(lname.slice(0, 6), dtrack.x + dtrack.w / 2, y0 - 2);
      }

      // ── Continuous log tracks ────────────────────────────────────
      for (let ci = 0; ci < showContinuous.length; ci++) {
        const ctrack = wl.contTracks[ci];
        if (!ctrack) continue;
        const lname = showContinuous[ci];
        const logVals = logs[lname] || (ci === 0 ? wd.log_values : null);
        if (!logVals || logVals.length < 2) {
          // No data label
          ctx.font = '7px sans-serif';
          ctx.fillStyle = '#ccc';
          ctx.textAlign = 'center';
          ctx.save();
          ctx.translate(ctrack.x + ctrack.w / 2, (y0 + y1) / 2);
          ctx.rotate(-Math.PI / 2);
          ctx.fillText(`no ${lname}`, 0, 0);
          ctx.restore();
          continue;
        }

        // Compute value range (1st-99th percentile)
        const valid = logVals.filter(v => v != null);
        if (valid.length === 0) continue;
        valid.sort((a, b) => a - b);
        let lMin = valid[Math.floor(valid.length * 0.01)];
        let lMax = valid[Math.floor(valid.length * 0.99)];
        if (lMax === lMin) lMax = lMin + 1;

        // Log-scale for resistivity-type logs
        const useLogScale = corrPlotConfig.logScaleLogs.some(
          n => lname.toUpperCase().includes(n)
        ) && lMin > 0;
        let logMin, logMax;
        if (useLogScale) {
          logMin = Math.log10(Math.max(lMin, 0.001));
          logMax = Math.log10(Math.max(lMax, 0.01));
          if (logMax === logMin) logMax = logMin + 1;
        }

        // Track background (subtle)
        ctx.fillStyle = '#fafafa';
        ctx.fillRect(ctrack.x, y0, ctrack.w, y1 - y0);
        ctx.strokeStyle = '#e0e0e0';
        ctx.lineWidth = 0.3;
        ctx.strokeRect(ctrack.x, y0, ctrack.w, y1 - y0);

        // Draw log trace
        const lcolor = logColors[ci % logColors.length];
        ctx.beginPath();
        ctx.strokeStyle = lcolor;
        ctx.lineWidth = 1.2;
        let started = false;
        for (let s = 0; s < (wd.size || logVals.length) && s < logVals.length; s++) {
          if (logVals[s] == null) { started = false; continue; }
          const y = depthToY(depth ? depth[s] : s, i);
          let normV;
          if (useLogScale && logVals[s] > 0) {
            normV = Math.max(0, Math.min(1, (Math.log10(logVals[s]) - logMin) / (logMax - logMin)));
          } else {
            normV = Math.max(0, Math.min(1, (logVals[s] - lMin) / (lMax - lMin)));
          }
          const x = ctrack.x + normV * ctrack.w;
          if (!started) { ctx.moveTo(x, y); started = true; }
          else ctx.lineTo(x, y);
        }
        ctx.stroke();

        // Track header (log name + range)
        ctx.font = '7px sans-serif';
        ctx.fillStyle = lcolor;
        ctx.textAlign = 'center';
        ctx.fillText(lname, ctrack.x + ctrack.w / 2, y0 - 2);
      }

      // ── Per-well depth ticks ─────────────────────────────────────
      if (corrPlotConfig.showMD && depth && depth.length > 0) {
        const wellH = y1 - y0;
        const nTicks = Math.max(2, Math.min(8, Math.floor(wellH / 40)));
        ctx.font = '7px sans-serif';
        ctx.fillStyle = '#aaa';
        ctx.textAlign = 'right';
        for (let t = 0; t <= nTicks; t++) {
          const frac = t / nTicks;
          const dIdx = Math.floor(frac * (depth.length - 1));
          const d = depth[dIdx];
          const y = depthToY(d, i);
          ctx.fillText(d.toFixed(0), wl.x - 2, y + 3);
          // Tick mark
          ctx.strokeStyle = '#e8e8e8';
          ctx.lineWidth = 0.3;
          ctx.beginPath();
          ctx.moveTo(wl.x, y);
          ctx.lineTo(wl.rightEdge, y);
          ctx.stroke();
        }
      }
    }

    // ── Global Stratigraphic Column reference strip (left margin) ───
    if (corrPlotConfig.showGlobalStrat && cachedGlobalStrat && cachedGlobalStrat.ranks) {
      const gsX = 4;  // left edge
      const gsW = margin.left - 10;  // fill the left margin
      const gsY0 = margin.top;
      const gsH = H;
      // Use first rank's units (typically Formation level)
      const rank = cachedGlobalStrat.ranks[0];
      if (rank && rank.units && rank.units.length > 0) {
        const units = rank.units;
        const unitH = gsH / units.length;
        ctx.font = '8px sans-serif';
        ctx.textAlign = 'center';
        for (let u = 0; u < units.length; u++) {
          const unit = units[u];
          const uy = gsY0 + u * unitH;
          // Colored band
          ctx.fillStyle = unit.color || '#CCCCCC';
          ctx.globalAlpha = 0.7;
          ctx.fillRect(gsX, uy, gsW, unitH);
          ctx.globalAlpha = 1.0;
          // Border
          ctx.strokeStyle = '#666';
          ctx.lineWidth = 0.4;
          ctx.strokeRect(gsX, uy, gsW, unitH);
          // Unit name (truncated)
          ctx.fillStyle = '#222';
          const label = unit.name.length > 8 ? unit.name.slice(0, 7) + '…' : unit.name;
          ctx.fillText(label, gsX + gsW / 2, uy + unitH / 2 + 3);
        }
        // Rank title at top
        ctx.font = 'bold 8px sans-serif';
        ctx.fillStyle = '#444';
        ctx.fillText(rank.name || 'Strat', gsX + gsW / 2, gsY0 - 4);
      }
    }

    // ── Draw correlation lines in gaps between wells ────────────────
    const lines = result.lines || [];
    const lineStyles = {
      boundary: { color: '#D32F2F', width: 1.8, alpha: 0.85, dash: [] },
      gap:      { color: '#1565C0', width: 1.2, alpha: 0.6, dash: [4, 3] },
      framework:{ color: '#888888', width: 0.5, alpha: 0.25, dash: [] },
    };

    for (let li = 0; li < lines.length; li++) {
      const line = lines[li];
      const markers = line.markers || line;
      if (!Array.isArray(markers)) continue;
      const lt = line.line_type || 'framework';
      const style = lineStyles[lt] || lineStyles.framework;

      ctx.strokeStyle = style.color;
      ctx.lineWidth = style.width;
      ctx.globalAlpha = style.alpha;
      ctx.setLineDash(style.dash);

      // Draw line segments ONLY in gaps between adjacent wells
      for (let w = 0; w < nWells - 1 && w < markers.length - 1; w++) {
        const mL = markers[w];
        const mR = markers[w + 1];
        if (mL == null || mL < 0 || mR == null || mR < 0) continue;

        const wdL = plotData[w];
        const wdR = plotData[w + 1];
        const depthL = wdL.depth ? (wdL.depth[mL] != null ? wdL.depth[mL] : mL) : mL;
        const depthR = wdR.depth ? (wdR.depth[mR] != null ? wdR.depth[mR] : mR) : mR;

        const yL = depthToY(depthL, w);
        const yR = depthToY(depthR, w + 1);

        // X coords: right edge of left well → left edge of right well
        const xL = wellLayout[w].rightEdge;
        const xR = wellLayout[w + 1].x;

        ctx.beginPath();
        ctx.moveTo(xL, yL);
        ctx.lineTo(xR, yR);
        ctx.stroke();
      }

      // Marker dots for boundary lines at well edges
      if (lt === 'boundary') {
        ctx.globalAlpha = 1.0;
        ctx.setLineDash([]);
        for (let w = 0; w < nWells && w < markers.length; w++) {
          const mIdx = markers[w];
          if (mIdx == null || mIdx < 0) continue;
          const wd = plotData[w];
          const d = wd.depth ? (wd.depth[mIdx] != null ? wd.depth[mIdx] : mIdx) : mIdx;
          const y = depthToY(d, w);
          // Dot at right edge of well (for left-to-right reading)
          const x = wellLayout[w].rightEdge;
          ctx.beginPath();
          ctx.arc(x, y, 2.5, 0, 2 * Math.PI);
          ctx.fillStyle = style.color;
          ctx.fill();
          // Also at left edge
          const xL = wellLayout[w].x;
          ctx.beginPath();
          ctx.arc(xL, y, 2.5, 0, 2 * Math.PI);
          ctx.fill();
        }
      }
    }

    ctx.globalAlpha = 1.0;
    ctx.setLineDash([]);

    // ── Aligned depth axis labels (left side) ───────────────────────
    ctx.fillStyle = '#605e5c';
    ctx.font = '9px sans-serif';
    ctx.textAlign = 'right';
    const nTicks = 8;
    for (let t = 0; t <= nTicks; t++) {
      const alignedD = alignedMin + (alignedMax - alignedMin) * t / nTicks;
      const y = margin.top + (t / nTicks) * H;
      // Show as relative offset if marker-aligned
      if (corrPlotConfig.alignMode === 'marker') {
        ctx.fillText((alignedD >= 0 ? '+' : '') + alignedD.toFixed(0), margin.left - 4, y + 3);
      } else {
        ctx.fillText(alignedD.toFixed(1), margin.left - 4, y + 3);
      }
      ctx.strokeStyle = '#f3f2f1';
      ctx.lineWidth = 0.4;
      ctx.beginPath();
      ctx.moveTo(margin.left, y);
      ctx.lineTo(cw - margin.right, y);
      ctx.stroke();
    }

    // ── Legend (bottom) ──────────────────────────────────────────────
    const legendY = ch - 8;
    let legendX = margin.left;
    ctx.font = '9px sans-serif';
    ctx.textAlign = 'left';

    // Log legend
    for (let ci = 0; ci < showContinuous.length; ci++) {
      const lcolor = logColors[ci % logColors.length];
      ctx.strokeStyle = lcolor; ctx.lineWidth = 2; ctx.setLineDash([]);
      ctx.beginPath(); ctx.moveTo(legendX, legendY - 3); ctx.lineTo(legendX + 15, legendY - 3); ctx.stroke();
      ctx.fillStyle = lcolor;
      ctx.fillText(showContinuous[ci], legendX + 18, legendY);
      legendX += ctx.measureText(showContinuous[ci]).width + 28;
    }

    // Separator
    legendX += 10;

    // Correlation line legend
    ctx.strokeStyle = '#D32F2F'; ctx.lineWidth = 2; ctx.setLineDash([]);
    ctx.beginPath(); ctx.moveTo(legendX, legendY - 3); ctx.lineTo(legendX + 15, legendY - 3); ctx.stroke();
    ctx.fillStyle = '#D32F2F'; ctx.fillText('Boundary', legendX + 18, legendY);
    legendX += 75;

    ctx.strokeStyle = '#1565C0'; ctx.lineWidth = 1.5; ctx.setLineDash([4, 3]);
    ctx.beginPath(); ctx.moveTo(legendX, legendY - 3); ctx.lineTo(legendX + 15, legendY - 3); ctx.stroke();
    ctx.fillStyle = '#1565C0'; ctx.fillText('Gap', legendX + 18, legendY);
    legendX += 45;

    ctx.strokeStyle = '#888'; ctx.lineWidth = 0.8; ctx.setLineDash([]);
    ctx.beginPath(); ctx.moveTo(legendX, legendY - 3); ctx.lineTo(legendX + 15, legendY - 3); ctx.stroke();
    ctx.fillStyle = '#888'; ctx.fillText('Framework', legendX + 18, legendY);
    ctx.setLineDash([]);

    // Alignment mode indicator (top-right)
    ctx.font = '8px sans-serif';
    ctx.fillStyle = '#999';
    ctx.textAlign = 'right';
    ctx.fillText(`Align: ${corrPlotConfig.alignMode === 'marker' ? 'by marker' : 'absolute depth'}`, cw - 10, 12);
  }

  // ── Export ────────────────────────────────────────────────────────
  btnExportRddms.addEventListener('click', async () => {
    setStatus(exportStatus, 'info', 'Exporting to RDDMS...');
    try {
      const data = await api('POST', '/export');
      if (data.status === 'ok') {
        setStatus(exportStatus, 'ok',
          `Exported ${data.n_wells_exported} wells × ${data.n_markers_per_well} markers to ${data.dataspace}`);
      } else if (data.status === 'pending') {
        setStatus(exportStatus, 'warn', data.message);
      } else {
        setStatus(exportStatus, 'ok', 'Exported to RDDMS');
      }
    } catch(e) {
      setStatus(exportStatus, 'err', 'Export failed: ' + e.message);
    }
  });

  btnExportJson.addEventListener('click', () => {
    if (!correlationResult) return;
    const blob = new Blob([JSON.stringify(correlationResult, null, 2)], {type:'application/json'});
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url; a.download = 'weco_results.json'; a.click();
    URL.revokeObjectURL(url);
    setStatus(exportStatus, 'ok', 'JSON downloaded');
  });

  btnExportCsv.addEventListener('click', () => {
    if (!correlationResult) return;
    const results = correlationResult.results || [];
    const names = correlationResult.well_names || [];
    if (!results.length) return;

    let csv = 'Solution,Cost,' + names.join(',') + '\n';
    results.forEach((r, i) => {
      const markers = r.markers || r.correlation || [];
      markers.forEach((row, mi) => {
        csv += `${i+1},${r.cost != null ? r.cost : ''},`;
        if (Array.isArray(row)) csv += row.map(v => v != null ? v : '').join(',');
        csv += '\n';
      });
    });

    const blob = new Blob([csv], {type:'text/csv'});
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url; a.download = 'weco_markers.csv'; a.click();
    URL.revokeObjectURL(url);
    setStatus(exportStatus, 'ok', 'CSV downloaded');
  });

  // ── Workflow save/load ────────────────────────────────────────────────
  btnSaveWorkflow.addEventListener('click', async () => {
    const name = (workflowName.value || '').trim();
    if (!name) { setStatus(workflowStatus, 'err', 'Enter a workflow name'); return; }
    const body = {
      name,
      demo_id: currentDemoId || '',
      dataspace: dsSel.value || '',
      options: gatherOptions(),
      n_best: parseInt($('#p-n-best')?.value || '5', 10),
      well_ids: [],
      notes: '',
    };
    try {
      const res = await api('POST', '/workflows', body);
      setStatus(workflowStatus, 'ok', `Saved "${res.name}" (id=${res.id})`);
      loadWorkflowList();
    } catch(e) {
      setStatus(workflowStatus, 'err', 'Save failed: ' + e.message);
    }
  });

  btnLoadWorkflows.addEventListener('click', () => loadWorkflowList());

  async function loadWorkflowList() {
    try {
      const list = await api('GET', '/workflows');
      if (!list.length) {
        workflowList.innerHTML = '<span class="muted">No saved workflows</span>';
        return;
      }
      workflowList.innerHTML = list.map(wf => {
        const d = new Date(wf.updated_at * 1000).toLocaleDateString();
        return `<div style="display:flex; justify-content:space-between; align-items:center; padding:2px 0; border-bottom:1px solid #f3f2f1;">
          <span><a href="#" class="wf-load" data-id="${wf.id}" style="font-weight:500;">${esc(wf.name)}</a>
            <span class="muted" style="font-size:11px;"> ${wf.demo_id ? '(' + wf.demo_id + ')' : ''} ${d}</span></span>
          <a href="#" class="wf-delete" data-id="${wf.id}" style="color:#a4262c; font-size:11px;">del</a>
        </div>`;
      }).join('');
      // Bind load links
      workflowList.querySelectorAll('.wf-load').forEach(a => {
        a.addEventListener('click', async (e) => {
          e.preventDefault();
          await loadWorkflow(parseInt(a.dataset.id, 10));
        });
      });
      // Bind delete links
      workflowList.querySelectorAll('.wf-delete').forEach(a => {
        a.addEventListener('click', async (e) => {
          e.preventDefault();
          await deleteWorkflow(parseInt(a.dataset.id, 10));
        });
      });
    } catch(e) {
      workflowList.innerHTML = '<span class="muted">Failed to load</span>';
    }
  }

  async function loadWorkflow(id) {
    try {
      const wf = await api('GET', '/workflows/' + id);
      workflowName.value = wf.name;
      if (wf.options && typeof wf.options === 'object') {
        applyOptions(wf.options);
      }
      if (wf.n_best && $('#p-n-best')) $('#p-n-best').value = wf.n_best;
      if (wf.demo_id) currentDemoId = wf.demo_id;
      setStatus(workflowStatus, 'ok', `Loaded "${wf.name}"`);
    } catch(e) {
      setStatus(workflowStatus, 'err', 'Load failed: ' + e.message);
    }
  }

  async function deleteWorkflow(id) {
    try {
      await api('DELETE', '/workflows/' + id);
      setStatus(workflowStatus, 'ok', 'Deleted');
      loadWorkflowList();
    } catch(e) {
      setStatus(workflowStatus, 'err', 'Delete failed: ' + e.message);
    }
  }

  // ── RESQML Object Browser ──────────────────────────────────────────
  const objBrowser = $('#obj-browser');
  const objBrowserHint = $('#obj-browser-hint');
  const objTypeSelect = $('#obj-type-select');
  const objTypeChips = $('#obj-type-chips');
  const objFilter = $('#obj-filter');
  const objList = $('#obj-list');
  const objCount = $('#obj-count');
  const objSelectedCount = $('#obj-selected-count');
  const btnObjLoad = $('#btn-obj-load');
  const btnObjSelectAll = $('#btn-obj-select-all');
  const btnObjSelectNone = $('#btn-obj-select-none');

  let objTypes = [];
  let objCache = {}; // type -> [{uuid, name, lastChanged, storeCreated}]
  let objSelected = new Set(); // "type::uuid" keys

  async function loadObjectTypes() {
    try {
      const resp = await api('GET', '/objects/types');
      objTypes = resp.types || [];
      objTypeSelect.innerHTML = '<option value="">-- Select type --</option>' +
        objTypes.map(t => `<option value="${esc(t.type)}">${esc(t.label)} (${esc(t.group)})</option>`).join('');
    } catch(e) { /* silent */ }
  }
  loadObjectTypes();

  // Show browser when a dataspace is selected
  dsSel.addEventListener('change', () => {
    if (dsSel.value) {
      objBrowser.style.display = 'block';
      objBrowserHint.style.display = 'none';
      objCache = {};
      objTypeChips.innerHTML = '';
      objList.innerHTML = '';
      objCount.textContent = '';
    }
  });

  btnObjLoad.addEventListener('click', async () => {
    const ds = dsSel.value;
    const typ = objTypeSelect.value;
    if (!ds || !typ) return;
    objCount.textContent = 'Loading...';
    try {
      const resp = await api('GET', `/objects?dataspace=${encodeURIComponent(ds)}&type=${encodeURIComponent(typ)}`);
      objCache[typ] = resp.objects || [];
      renderObjTypeChips();
      renderObjList(typ);
    } catch(e) {
      objCount.textContent = 'Error: ' + e.message;
    }
  });

  function renderObjTypeChips() {
    objTypeChips.innerHTML = Object.keys(objCache).map(typ => {
      const info = objTypes.find(t => t.type === typ);
      const label = info ? info.label : typ.split('.').pop();
      const count = objCache[typ].length;
      return `<span class="obj-type-chip active" data-type="${esc(typ)}">${esc(label)}<span class="type-count">(${count})</span></span>`;
    }).join('');
    objTypeChips.querySelectorAll('.obj-type-chip').forEach(chip => {
      chip.addEventListener('click', () => renderObjList(chip.dataset.type));
    });
  }

  function renderObjList(typ) {
    const objects = objCache[typ] || [];
    const filter = (objFilter.value || '').toLowerCase();
    const filtered = objects.filter(o =>
      !filter || o.name.toLowerCase().includes(filter) || o.uuid.toLowerCase().includes(filter)
    );
    objCount.textContent = `${filtered.length}/${objects.length} objects`;
    objList.innerHTML = filtered.map(o => {
      const key = `${typ}::${o.uuid}`;
      const checked = objSelected.has(key) ? 'checked' : '';
      const date = o.lastChanged ? new Date(o.lastChanged).toLocaleDateString() : '';
      return `<div class="obj-row ${checked ? 'selected' : ''}" data-key="${esc(key)}">
        <input type="checkbox" ${checked}>
        <span class="obj-name" title="${esc(o.name)}">${esc(o.name)}</span>
        <span class="obj-uuid">${esc(o.uuid.slice(0,8))}…</span>
        <span class="obj-date">${esc(date)}</span>
      </div>`;
    }).join('');
    // Click to toggle
    objList.querySelectorAll('.obj-row').forEach(row => {
      row.addEventListener('click', (e) => {
        if (e.target.tagName === 'INPUT') return; // handled by checkbox
        const key = row.dataset.key;
        const cb = row.querySelector('input[type="checkbox"]');
        if (objSelected.has(key)) {
          objSelected.delete(key);
          cb.checked = false;
          row.classList.remove('selected');
        } else {
          objSelected.add(key);
          cb.checked = true;
          row.classList.add('selected');
        }
        updateObjSelectedCount();
      });
      const cb = row.querySelector('input[type="checkbox"]');
      cb.addEventListener('change', () => {
        const key = row.dataset.key;
        if (cb.checked) { objSelected.add(key); row.classList.add('selected'); }
        else { objSelected.delete(key); row.classList.remove('selected'); }
        updateObjSelectedCount();
      });
    });
    updateObjSelectedCount();
  }

  function updateObjSelectedCount() {
    objSelectedCount.textContent = objSelected.size ? `${objSelected.size} selected` : '';
  }

  if (objFilter) objFilter.addEventListener('input', () => {
    const typ = objTypeSelect.value;
    if (typ && objCache[typ]) renderObjList(typ);
  });

  btnObjSelectAll.addEventListener('click', () => {
    objList.querySelectorAll('.obj-row').forEach(row => {
      const key = row.dataset.key;
      objSelected.add(key);
      row.classList.add('selected');
      row.querySelector('input[type="checkbox"]').checked = true;
    });
    updateObjSelectedCount();
  });

  btnObjSelectNone.addEventListener('click', () => {
    objList.querySelectorAll('.obj-row').forEach(row => {
      const key = row.dataset.key;
      objSelected.delete(key);
      row.classList.remove('selected');
      row.querySelector('input[type="checkbox"]').checked = false;
    });
    updateObjSelectedCount();
  });

  // ── Accessibility: Keyboard Navigation (Q2) ──────────────────────
  // Tab keyboard navigation (Arrow Left/Right, Home, End)
  const tabBar = document.querySelector('.wc-tabs');
  if (tabBar) {
    tabBar.addEventListener('keydown', e => {
      const enabledTabs = tabs.filter(t => !t.classList.contains('disabled'));
      const currentIdx = enabledTabs.indexOf(document.activeElement);
      if (currentIdx < 0) return;
      let nextIdx = -1;
      if (e.key === 'ArrowRight' || e.key === 'ArrowDown') {
        nextIdx = (currentIdx + 1) % enabledTabs.length;
      } else if (e.key === 'ArrowLeft' || e.key === 'ArrowUp') {
        nextIdx = (currentIdx - 1 + enabledTabs.length) % enabledTabs.length;
      } else if (e.key === 'Home') {
        nextIdx = 0;
      } else if (e.key === 'End') {
        nextIdx = enabledTabs.length - 1;
      } else if (e.key === 'Enter' || e.key === ' ') {
        e.preventDefault();
        enabledTabs[currentIdx].click();
        return;
      }
      if (nextIdx >= 0) {
        e.preventDefault();
        enabledTabs[nextIdx].focus();
        enabledTabs[nextIdx].click();
      }
    });
  }

  // Update ARIA attributes on tab switch
  const origSwitchTab = switchTab;
  switchTab = function(name) {
    origSwitchTab(name);
    tabs.forEach(t => {
      const isTarget = t.dataset.tab === name;
      t.setAttribute('aria-selected', isTarget ? 'true' : 'false');
      t.setAttribute('tabindex', isTarget ? '0' : '-1');
      if (t.classList.contains('disabled')) {
        t.setAttribute('aria-disabled', 'true');
      } else {
        t.removeAttribute('aria-disabled');
      }
    });
  };

  // Demo card keyboard navigation (Enter/Space to activate, arrows to move)
  demoGrid.addEventListener('keydown', e => {
    const cards = $$('.demo-card', demoGrid);
    if (!cards.length) return;
    const currentIdx = cards.indexOf(document.activeElement);
    if (e.key === 'Enter' || e.key === ' ') {
      e.preventDefault();
      if (document.activeElement.classList.contains('demo-card')) {
        document.activeElement.click();
      }
    } else if (e.key === 'ArrowRight' || e.key === 'ArrowDown') {
      e.preventDefault();
      const next = Math.min(currentIdx + 1, cards.length - 1);
      cards[next].focus();
    } else if (e.key === 'ArrowLeft' || e.key === 'ArrowUp') {
      e.preventDefault();
      const prev = Math.max(currentIdx - 1, 0);
      cards[prev].focus();
    }
  });

  // Make demo cards focusable and add ARIA roles when rendered
  const demoObserver = new MutationObserver(() => {
    $$('.demo-card', demoGrid).forEach(card => {
      if (!card.hasAttribute('tabindex')) {
        card.setAttribute('tabindex', '0');
        card.setAttribute('role', 'button');
      }
    });
  });
  demoObserver.observe(demoGrid, { childList: true });

  // Well chip keyboard support
  wellChips.addEventListener('keydown', e => {
    if (e.key === 'Enter' || e.key === ' ') {
      e.preventDefault();
      if (e.target.classList.contains('well-chip')) {
        e.target.click();
      }
    }
  });

})();
