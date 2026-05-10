import {
  createChart,
  CandlestickSeries,
  LineSeries,
  AreaSeries,
  HistogramSeries,
  BaselineSeries,
  BarSeries,
  createSeriesMarkers,
  MismatchDirection,
} from 'lightweight-charts';
import Plotly from 'plotly.js-dist-min';
import { CHART_THEME } from './lib/chartTheme.js';
import { t, tCol, tVal } from './lib/i18n.js';
import {
  chartDetailsSignature,
  chartsOrderSignature,
  moveTableTimeColumnFirst,
  reorderChartPanels,
  reorderTableColumns,
  sanitizeDetailsOpenState,
  validateTableColumnOrder,
  validateChartOrder,
} from './lib/chartOrder.js';
import { formatChartCrosshairTime, formatChartTick, formatUnixDateTime, normalizeTimeZone } from './lib/dateTime.js';

const SERIES_TYPE_MAP = {
  Candlestick: CandlestickSeries,
  Line: LineSeries,
  Area: AreaSeries,
  Histogram: HistogramSeries,
  Baseline: BaselineSeries,
  Bar: BarSeries,
};

const PLOTLY_LAYOUT_DEFAULTS = {
  paper_bgcolor: '#131722',
  plot_bgcolor: '#131722',
  font: { color: '#d1d4dc', family: 'system-ui, -apple-system, sans-serif', size: 12 },
  margin: { t: 10, r: 20, b: 40, l: 50 },
  xaxis: { gridcolor: '#1e2130', zerolinecolor: '#363a45' },
  yaxis: { gridcolor: '#1e2130', zerolinecolor: '#363a45' },
  legend: { bgcolor: 'rgba(0,0,0,0)', font: { color: '#d1d4dc' } },
};

const PLOTLY_CONFIG = {
  responsive: true,
  displayModeBar: false,
};

const PLOTLY_DEFAULT_HEIGHT = 450;

const INTRADAY_TIME_RE = /[T ]\d{2}:\d{2}/;
const DATE_ONLY_RE = /^\d{4}-\d{2}-\d{2}$/;

const TIME_COLUMN_NAMES = new Set([
  'time', 'date', 'datetime', 'timestamp',
  'entry_time', 'exit_time', 'open_time', 'close_time',
  'created_at', 'updated_at', 'submitted_at', 'filled_at', 'cancelled_at',
  'submitted_time', 'filled_time', 'order_time',
]);

function detectTimeColumn(columns, rows) {
  for (const col of columns) {
    if (TIME_COLUMN_NAMES.has(col.toLowerCase())) return col;
  }
  for (const col of columns) {
    const sample = rows.slice(0, 3);
    const allParseable = sample.length > 0 && sample.every((r) => {
      const u = toUnixSeconds(r?.[col]);
      return typeof u === 'number' && Number.isFinite(u) && u > 946684800;
    });
    if (allParseable) return col;
  }
  return null;
}

function detectDisplayTimeColumns(columns, rows) {
  const out = new Set();
  const sample = rows.slice(0, 3);
  for (const col of columns) {
    if (TIME_COLUMN_NAMES.has(col.toLowerCase())) {
      out.add(col);
      continue;
    }
    const allParseable = sample.length > 0 && sample.every((r) => {
      const u = toUnixSeconds(r?.[col]);
      return typeof u === 'number' && Number.isFinite(u) && u > 946684800;
    });
    if (allParseable) out.add(col);
  }
  return out;
}

const CHART_ORDER_LS_PREFIX = 'vibetrader:chartPanelOrder:';
const DETAILS_OPEN_LS_PREFIX = 'vibetrader:chartDetailsOpen:';
const HIDDEN_PANELS_LS_PREFIX = 'vibetrader:hiddenChartPanels:';
const TABLE_COLUMN_ORDER_LS_PREFIX = 'vibetrader:tableColumnOrder:';
const MAX_SPARSE_LWC_BAR_SPACING = 28;
const MIN_SPARSE_LWC_VISIBLE_SPAN = 12;

function createDetailsOpenStore(storageKey, chartCount, includeMetrics) {
  let state = {};
  try {
    const raw = localStorage.getItem(storageKey);
    if (raw) {
      const parsed = JSON.parse(raw);
      state = sanitizeDetailsOpenState(parsed, chartCount, includeMetrics);
    }
  } catch {
    state = {};
  }

  function persist() {
    try {
      localStorage.setItem(storageKey, JSON.stringify(state));
    } catch {
      /* ignore */
    }
  }

  return {
    initialOpen(panelKey) {
      const v = state[panelKey];
      return typeof v === 'boolean' ? v : true;
    },
    bind(details, panelKey) {
      details.open = this.initialOpen(panelKey);
      details.addEventListener('toggle', () => {
        state = { ...state, [panelKey]: details.open };
        persist();
      });
    },
  };
}

function loadChartOrder(storageKey, n) {
  if (typeof localStorage === 'undefined') return null;
  try {
    const raw = localStorage.getItem(storageKey);
    if (!raw) return null;
    const arr = JSON.parse(raw);
    return validateChartOrder(arr, n) ? arr : null;
  } catch {
    return null;
  }
}

function saveChartOrder(storageKey, order) {
  if (typeof localStorage === 'undefined') return;
  try {
    localStorage.setItem(storageKey, JSON.stringify(order));
  } catch {
    /* ignore */
  }
}

function tableColumnOrderSignature(columns) {
  const s = JSON.stringify(columns);
  let h = 5381;
  for (let i = 0; i < s.length; i++) {
    h = (h * 33) ^ s.charCodeAt(i);
  }
  return `${(h >>> 0).toString(36)}_${columns.length}`;
}

function loadTableColumnOrder(storageKey, columns) {
  if (typeof localStorage === 'undefined' || !storageKey) return null;
  try {
    const raw = localStorage.getItem(storageKey);
    if (!raw) return null;
    const arr = JSON.parse(raw);
    return validateTableColumnOrder(arr, columns) ? arr : null;
  } catch {
    return null;
  }
}

function saveTableColumnOrder(storageKey, columns) {
  if (typeof localStorage === 'undefined' || !storageKey) return;
  try {
    localStorage.setItem(storageKey, JSON.stringify(columns));
  } catch {
    /* ignore */
  }
}

function loadHiddenPanelKeys(storageKey, chartCount) {
  if (typeof localStorage === 'undefined' || !storageKey) return new Set();
  try {
    const raw = localStorage.getItem(storageKey);
    if (!raw) return new Set();
    const arr = JSON.parse(raw);
    if (!Array.isArray(arr)) return new Set();
    const out = new Set();
    for (const key of arr) {
      if (typeof key !== 'string') continue;
      const m = /^c(\d+)$/.exec(key);
      if (!m) continue;
      const i = Number(m[1]);
      if (Number.isInteger(i) && i >= 0 && i < chartCount) out.add(key);
    }
    return out;
  } catch {
    return new Set();
  }
}

function saveHiddenPanelKeys(storageKey, hiddenKeys) {
  if (typeof localStorage === 'undefined' || !storageKey) return;
  try {
    localStorage.setItem(storageKey, JSON.stringify([...hiddenKeys].sort()));
  } catch {
    /* ignore */
  }
}

function createChartDndRow(srcIdx) {
  const row = document.createElement('div');
  row.className = 'strategy-chart-dnd-row';
  row.dataset.srcIdx = String(srcIdx);

  const inner = document.createElement('div');
  inner.className = 'strategy-chart-dnd-inner';

  row.appendChild(inner);
  return row;
}

function enableChartDndHeader(row) {
  const header = row.querySelector('.strategy-chart-summary');
  if (!header) return;
  header.classList.add('strategy-chart-dnd-handle');
  header.draggable = true;
  header.title = t('chart.drag_reorder');
}

function setupChartPanelDnD(dndRoot, storageKey, n, signal, allPanelIds = null) {
  const rows = () => [...dndRoot.querySelectorAll('.strategy-chart-dnd-row')];

  const applyOrderToDom = (order) => {
    for (const srcIdx of order) {
      const row = dndRoot.querySelector(`.strategy-chart-dnd-row[data-src-idx="${srcIdx}"]`);
      if (row) dndRoot.appendChild(row);
    }
  };

  let draggedSrc = null;

  const onDragStart = (e) => {
    if (e.target?.closest?.('button, a, input, select, textarea')) return;
    const handle = e.target?.closest?.('.strategy-chart-dnd-handle');
    if (!handle || !dndRoot.contains(handle)) return;
    const row = handle.closest('.strategy-chart-dnd-row');
    if (!row) return;
    draggedSrc = Number(row.dataset.srcIdx);
    e.dataTransfer.effectAllowed = 'move';
    e.dataTransfer.setData('text/plain', String(draggedSrc));
    row.classList.add('strategy-chart-dnd-row--dragging');
  };

  const onDragEnd = () => {
    draggedSrc = null;
    for (const r of rows()) {
      r.classList.remove('strategy-chart-dnd-row--dragging');
    }
  };

  const onDragOver = (e) => {
    if (!dndRoot.contains(e.target)) return;
    e.preventDefault();
    e.dataTransfer.dropEffect = 'move';
  };

  const onDrop = (e) => {
    if (!dndRoot.contains(e.target)) return;
    e.preventDefault();
    const dropRow = e.target.closest?.('.strategy-chart-dnd-row');
    if (!dropRow || !dndRoot.contains(dropRow)) return;
    const dropSrc = Number(dropRow.dataset.srcIdx);
    let dragSrc = Number(e.dataTransfer.getData('text/plain'));
    if (!Number.isFinite(dragSrc)) dragSrc = draggedSrc;
    if (!Number.isFinite(dragSrc) || !Number.isFinite(dropSrc) || dragSrc === dropSrc) return;
    let visibleOrder = rows().map((r) => Number(r.dataset.srcIdx));
    visibleOrder = reorderChartPanels(visibleOrder, dragSrc, dropSrc);
    const fullOrder = Array.isArray(allPanelIds)
      ? [...visibleOrder, ...allPanelIds.filter((srcIdx) => !visibleOrder.includes(srcIdx))]
      : visibleOrder;
    if (!validateChartOrder(fullOrder, n)) return;
    applyOrderToDom(visibleOrder);
    saveChartOrder(storageKey, fullOrder);
  };

  dndRoot.addEventListener('dragstart', onDragStart, { signal });
  dndRoot.addEventListener('dragend', onDragEnd, { signal });
  dndRoot.addEventListener('dragover', onDragOver, { signal });
  dndRoot.addEventListener('drop', onDrop, { signal });
}

function toBusinessDay(value) {
  if (typeof value !== 'string') return value;
  const t = value.trim();
  if (!t) return value;
  const m = t.match(/^(\d{4}-\d{2}-\d{2})/);
  return m ? m[1] : value;
}

function toUnixSeconds(value) {
  if (typeof value === 'number' && Number.isFinite(value)) {
    return value > 1e12 ? Math.floor(value / 1000) : Math.floor(value);
  }
  if (typeof value !== 'string') return value;
  const t = value.trim();
  if (!t) return value;
  let iso = t;
  if (DATE_ONLY_RE.test(iso)) {
    iso = `${iso}T00:00:00Z`;
  } else {
    iso = iso.replace(' ', 'T');
    if (!/[zZ]|[+-]\d{2}:?\d{2}$/.test(iso)) {
      iso = `${iso}Z`;
    }
  }
  const ms = Date.parse(iso);
  if (!Number.isFinite(ms)) return value;
  return Math.floor(ms / 1000);
}

function chartHasIntradayTime(chartSpec) {
  for (const s of chartSpec?.series || []) {
    for (const p of s?.data || []) {
      const t = p?.time;
      if (typeof t === 'number') return true;
      if (typeof t === 'string' && INTRADAY_TIME_RE.test(t)) return true;
    }
    for (const m of s?.markers || []) {
      const t = m?.time;
      if (typeof t === 'number') return true;
      if (typeof t === 'string' && INTRADAY_TIME_RE.test(t)) return true;
    }
  }
  for (const m of chartSpec?.verticalMarkers || []) {
    const t = m?.time;
    if (typeof t === 'number') return true;
    if (typeof t === 'string' && INTRADAY_TIME_RE.test(t)) return true;
  }
  return false;
}

function normalizeLwcItems(items, normalizeTime) {
  if (!Array.isArray(items) || items.length === 0) return items;
  let changed = false;
  const out = items.map((it) => {
    if (!it || typeof it !== 'object') return it;
    const nt = normalizeTime(it.time);
    if (nt === it.time) return it;
    changed = true;
    return { ...it, time: nt };
  });
  return changed ? out : items;
}

function lwcDataPointCount(chartSpec) {
  let maxLen = 0;
  for (const s of chartSpec?.series || []) {
    if (Array.isArray(s?.data)) maxLen = Math.max(maxLen, s.data.length);
  }
  return maxLen;
}

function applyInitialLwcTimeRange(chart, el, pointCount, alignRightEdge = false) {
  if (pointCount <= 0) return;
  const width = el.clientWidth || 700;
  const dataSpan = Math.max(1, pointCount - 1);
  const sparseSpan = Math.max(MIN_SPARSE_LWC_VISIBLE_SPAN, Math.ceil(width / MAX_SPARSE_LWC_BAR_SPACING));
  if (dataSpan >= sparseSpan) {
    chart.timeScale().fitContent();
    return;
  }
  const pad = sparseSpan - dataSpan;
  const from = alignRightEdge ? dataSpan - sparseSpan : -pad / 2;
  const to = alignRightEdge ? dataSpan : dataSpan + pad / 2;
  try {
    chart.timeScale().setVisibleLogicalRange({ from, to });
  } catch {
    chart.timeScale().fitContent();
  }
}

function shouldUseSparseLwcRange(el, pointCount) {
  if (pointCount <= 0) return false;
  const width = el.clientWidth || 700;
  const dataSpan = Math.max(1, pointCount - 1);
  const sparseSpan = Math.max(MIN_SPARSE_LWC_VISIBLE_SPAN, Math.ceil(width / MAX_SPARSE_LWC_BAR_SPACING));
  return dataSpan < sparseSpan;
}

function installVerticalMarkers(chart, el, markers, normalizeTime) {
  if (!Array.isArray(markers) || markers.length === 0) return () => {};
  const normalized = normalizeLwcItems(markers, normalizeTime).filter((m) => m && m.time != null);
  if (normalized.length === 0) return () => {};
  if (window.getComputedStyle(el).position === 'static') {
    el.style.position = 'relative';
  }
  const layer = document.createElement('div');
  layer.className = 'strategy-chart-vertical-markers';
  layer.style.cssText = 'position:absolute;inset:0;pointer-events:none;z-index:4;overflow:hidden;';
  el.appendChild(layer);
  const nodes = normalized.map((marker) => {
    const wrap = document.createElement('div');
    wrap.style.cssText = 'position:absolute;top:0;bottom:0;width:0;display:none;';
    const line = document.createElement('div');
    line.style.cssText = `position:absolute;top:0;bottom:0;border-left:1px dashed ${marker.color || '#f59e0b'};opacity:.9;`;
    const label = document.createElement('div');
    label.textContent = marker.label || '';
    label.style.cssText = `position:absolute;top:8px;left:6px;padding:2px 6px;border-radius:999px;background:${marker.color || '#f59e0b'};color:#111827;font:11px system-ui, sans-serif;white-space:nowrap;box-shadow:0 1px 3px rgba(0,0,0,.25);`;
    wrap.appendChild(line);
    if (label.textContent) wrap.appendChild(label);
    layer.appendChild(wrap);
    return { marker, wrap };
  });
  const render = () => {
    const ts = chart.timeScale();
    for (const item of nodes) {
      const x = ts.timeToCoordinate(item.marker.time);
      if (typeof x !== 'number' || !Number.isFinite(x)) {
        item.wrap.style.display = 'none';
        continue;
      }
      item.wrap.style.display = 'block';
      item.wrap.style.left = `${Math.round(x)}px`;
    }
  };
  const ts = chart.timeScale();
  const unsubs = [];
  if (typeof ts.subscribeVisibleLogicalRangeChange === 'function') {
    ts.subscribeVisibleLogicalRangeChange(render);
    unsubs.push(() => ts.unsubscribeVisibleLogicalRangeChange(render));
  }
  if (typeof ts.subscribeVisibleTimeRangeChange === 'function') {
    ts.subscribeVisibleTimeRangeChange(render);
    unsubs.push(() => ts.unsubscribeVisibleTimeRangeChange(render));
  }
  let observer = null;
  if (typeof ResizeObserver !== 'undefined') {
    observer = new ResizeObserver(() => requestAnimationFrame(render));
    observer.observe(el);
  }
  requestAnimationFrame(render);
  return () => {
    for (const unsub of unsubs) unsub();
    observer?.disconnect();
    layer.remove();
  };
}

function catalogMapFromDataJson(dataJson) {
  const map = new Map();
  const raw = dataJson?.indicator_series_catalog;
  if (!Array.isArray(raw)) return map;
  for (const row of raw) {
    if (row && typeof row.name === 'string') {
      map.set(row.name, typeof row.description === 'string' ? row.description : '');
    }
  }
  return map;
}

function helpTextForLightweightChart(spec, catalogMap) {
  if (!spec || spec.type !== 'lightweight-charts' || catalogMap.size === 0) return null;
  const names = new Set();
  for (const s of spec.series || []) {
    const lab = typeof s.label === 'string' ? s.label.trim() : '';
    const m = /^output:(.+)$/.exec(lab);
    if (m) names.add(m[1]);
  }
  if (names.size === 0) return null;
  const parts = [];
  for (const name of names) {
    const d = catalogMap.get(name);
    if (d) parts.push(`${name}\n${d}`);
  }
  if (parts.length === 0) return null;
  return parts.join('\n\n');
}

function makeSection(container, titleText, openStore, panelKey, helpText, onRemove) {
  const details = document.createElement('details');
  details.className = 'strategy-chart-details';

  const summary = document.createElement('summary');
  summary.className = 'strategy-chart-summary';

  const titleEl = document.createElement('span');
  titleEl.className = 'strategy-chart-summary-title';
  titleEl.textContent = titleText || t('chart.default_title');
  summary.appendChild(titleEl);

  const trimmedHelp = typeof helpText === 'string' ? helpText.trim() : '';
  if (trimmedHelp) {
    const wrap = document.createElement('span');
    wrap.className = 'strategy-chart-help-wrap';
    const helpBtn = document.createElement('button');
    helpBtn.type = 'button';
    helpBtn.className = 'strategy-chart-help-btn';
    helpBtn.setAttribute('aria-label', t('chart.description_aria'));
    helpBtn.textContent = '?';
    const stopToggle = (ev) => {
      ev.preventDefault();
      ev.stopPropagation();
    };
    helpBtn.addEventListener('mousedown', stopToggle);
    helpBtn.addEventListener('click', stopToggle);
    const tooltip = document.createElement('div');
    tooltip.className = 'strategy-chart-help-tooltip';
    tooltip.setAttribute('role', 'tooltip');
    tooltip.textContent = trimmedHelp;
    wrap.appendChild(helpBtn);
    wrap.appendChild(tooltip);
    summary.appendChild(wrap);
  }
  const removeWrap = document.createElement('span');
  removeWrap.className = 'strategy-chart-help-wrap strategy-chart-remove-wrap';
  const removeBtn = document.createElement('button');
  removeBtn.type = 'button';
  removeBtn.className = 'strategy-chart-help-btn strategy-chart-remove-btn';
  removeBtn.setAttribute('aria-label', t('chart.remove_chart'));
  removeBtn.textContent = 'x';
  const stopRemoveToggle = (ev) => {
    ev.preventDefault();
    ev.stopPropagation();
  };
  removeBtn.addEventListener('mousedown', stopRemoveToggle);
  removeBtn.addEventListener('click', (ev) => {
    stopRemoveToggle(ev);
    if (typeof onRemove === 'function') {
      onRemove(panelKey, details);
      return;
    }
    const dndRow = details.closest('.strategy-chart-dnd-row');
    if (dndRow) {
      dndRow.remove();
      return;
    }
    details.remove();
  });
  removeWrap.appendChild(removeBtn);
  summary.appendChild(removeWrap);
  details.appendChild(summary);

  const chartEl = document.createElement('div');
  chartEl.className = 'strategy-chart-body';
  chartEl.style.cssText = 'width:100%;';
  details.appendChild(chartEl);

  if (openStore && typeof panelKey === 'string' && panelKey !== '') {
    openStore.bind(details, panelKey);
  } else {
    details.open = true;
  }

  container.appendChild(details);
  return { details, chartEl };
}

function renderLightweightChart(container, chartSpec, openStore, panelKey, helpText, timeZone, hourFormat, onCrosshairMove, onRemove, alignRightEdge) {
  const { details, chartEl: el } = makeSection(
    container,
    chartSpec.title || '',
    openStore,
    panelKey,
    helpText,
    onRemove,
  );
  el.style.height = '350px';

  const isIntraday = chartHasIntradayTime(chartSpec);
  const chart = createChart(el, {
    ...CHART_THEME,
    autoSize: true,
    height: 350,
    localization: {
      ...(CHART_THEME.localization || {}),
      timeFormatter: (time) => formatChartCrosshairTime(time, timeZone, isIntraday, hourFormat),
    },
    timeScale: {
      ...(CHART_THEME.timeScale || {}),
      tickMarkFormatter: (timeSec) => formatChartTick(timeSec, timeZone, isIntraday, hourFormat),
    },
  });

  details.addEventListener('toggle', () => {
    if (!details.open) return;
    requestAnimationFrame(() => {
      const w = el.clientWidth;
      const h = el.clientHeight || 350;
      chart.resize(w, h);
    });
  });

  const normalizeTime = chartHasIntradayTime(chartSpec) ? toUnixSeconds : toBusinessDay;
  const pointCount = lwcDataPointCount(chartSpec);
  let sparseResizeObserver = null;
  let verticalMarkerCleanup = null;

  let primarySeries = null;
  for (const s of chartSpec.series || []) {
    const SeriesClass = SERIES_TYPE_MAP[s.type];
    if (!SeriesClass) continue;
    const series = chart.addSeries(SeriesClass, {
      priceLineVisible: false,
      lastValueVisible: true,
      ...s.options,
      ...(typeof s.label === 'string' && s.label !== '' ? { title: s.label } : {}),
    });
    if (primarySeries == null) {
      primarySeries = series;
    }
    if (Array.isArray(s.data)) {
      series.setData(normalizeLwcItems(s.data, normalizeTime));
    }
    if (Array.isArray(s.markers) && s.markers.length > 0) {
      const normalizedMarkers = normalizeLwcItems(s.markers, normalizeTime);
      const sorted = [...normalizedMarkers]
        .sort((a, b) => a.time < b.time ? -1 : a.time > b.time ? 1 : 0)
        .map((m) => typeof m.text === 'string' && m.text ? { ...m, text: tVal(m.text) } : m);
      createSeriesMarkers(series, sorted);
    }
  }

  applyInitialLwcTimeRange(chart, el, pointCount, alignRightEdge);
  verticalMarkerCleanup = installVerticalMarkers(chart, el, chartSpec.verticalMarkers, normalizeTime);
  if (shouldUseSparseLwcRange(el, pointCount) && typeof ResizeObserver !== 'undefined') {
    sparseResizeObserver = new ResizeObserver(() => {
      requestAnimationFrame(() => applyInitialLwcTimeRange(chart, el, pointCount, alignRightEdge));
    });
    sparseResizeObserver.observe(el);
  }

  const setCrosshairAt = (unixSec) => {
    if (!primarySeries) return;
    const ts = chart.timeScale();
    if (typeof ts.timeToIndex !== 'function') return;
    const idx = ts.timeToIndex(unixSec, true);
    if (idx === null) return;
    const lr = ts.getVisibleLogicalRange?.();
    if (lr && Number.isFinite(lr.from) && Number.isFinite(lr.to)) {
      const span = lr.to - lr.from;
      if (idx < lr.from || idx > lr.to) {
        try {
          ts.setVisibleLogicalRange({ from: idx - span / 2, to: idx + span / 2 });
        } catch { /* ignore */ }
      }
    }
    let bar;
    try { bar = primarySeries.dataByIndex(idx, MismatchDirection.NearestLeft); } catch { /* ignore */ }
    const price = bar?.close ?? bar?.value ?? bar?.open ?? null;
    if (price != null) {
      try { chart.setCrosshairPosition(price, unixSec, primarySeries); } catch { /* ignore */ }
    }
  };

  const clearCrosshair = () => {
    try { chart.clearCrosshairPosition(); } catch { /* ignore */ }
  };

  let crosshairUnsub = null;
  if (typeof onCrosshairMove === 'function') {
    const handler = (param) => {
      if (param.sourceEvent == null) return;
      if (param.time == null) {
        onCrosshairMove(null);
      } else {
        const u = typeof param.time === 'number'
          ? (param.time > 1e12 ? Math.floor(param.time / 1000) : Math.floor(param.time))
          : toUnixSeconds(param.time);
        if (typeof u === 'number' && Number.isFinite(u)) {
          onCrosshairMove(u);
        }
      }
    };
    chart.subscribeCrosshairMove(handler);
    crosshairUnsub = () => chart.unsubscribeCrosshairMove(handler);
  }

  const cleanup = () => {
    if (crosshairUnsub) crosshairUnsub();
    verticalMarkerCleanup?.();
    sparseResizeObserver?.disconnect();
  };

  return { chart, primarySeries, setCrosshairAt, clearCrosshair, crosshairUnsub: cleanup };
}

export { attachSyncedCrosshair } from './lib/lwcSync.js';

function renderPlotlyChart(container, chartSpec, openStore, panelKey, helpText, onRemove) {
  const { details, chartEl: el } = makeSection(
    container,
    chartSpec.title || '',
    openStore,
    panelKey,
    helpText,
    onRemove,
  );

  const layout = {
    ...PLOTLY_LAYOUT_DEFAULTS,
    autosize: true,
    ...(chartSpec.layout || {}),
    font: { ...PLOTLY_LAYOUT_DEFAULTS.font, ...(chartSpec.layout?.font || {}) },
    xaxis: { ...PLOTLY_LAYOUT_DEFAULTS.xaxis, ...(chartSpec.layout?.xaxis || {}) },
    yaxis: { ...PLOTLY_LAYOUT_DEFAULTS.yaxis, ...(chartSpec.layout?.yaxis || {}) },
  };
  const layoutHeight = Number(layout.height);
  const plotHeight = Number.isFinite(layoutHeight) && layoutHeight > 0 ? layoutHeight : PLOTLY_DEFAULT_HEIGHT;
  el.style.minHeight = '300px';
  el.style.height = `${plotHeight}px`;

  let disposed = false;
  let resizeRaf = 0;
  let initialResizeTimer = 0;
  const resizePlot = () => {
    if (disposed || !details.open || !el.isConnected) return;
    try {
      Plotly.Plots.resize(el);
    } catch {}
  };
  const scheduleResize = () => {
    if (resizeRaf) cancelAnimationFrame(resizeRaf);
    resizeRaf = requestAnimationFrame(() => {
      resizeRaf = 0;
      resizePlot();
    });
  };

  Promise.resolve(Plotly.newPlot(el, chartSpec.data || [], layout, PLOTLY_CONFIG)).then(() => {
    if (disposed) return;
    scheduleResize();
    initialResizeTimer = window.setTimeout(scheduleResize, 100);
  });

  let resizeObserver = null;
  if (typeof ResizeObserver !== 'undefined') {
    resizeObserver = new ResizeObserver(scheduleResize);
    resizeObserver.observe(el);
  }

  details.addEventListener('toggle', () => {
    if (!details.open) return;
    scheduleResize();
  });

  return {
    cleanup: () => {
      disposed = true;
      resizeObserver?.disconnect();
      if (resizeRaf) cancelAnimationFrame(resizeRaf);
      if (initialResizeTimer) window.clearTimeout(initialResizeTimer);
      try {
        Plotly.purge(el);
      } catch {}
    },
  };
}

function escapeCsvField(value) {
  const s = value == null ? '' : String(value);
  if (/[",\n\r]/.test(s)) {
    return `"${s.replace(/"/g, '""')}"`;
  }
  return s;
}

function tableRowsToCsv(columns, rows, formatCell = null) {
  const header = columns.map((c) => escapeCsvField(c)).join(',');
  const lines = [header];
  for (const row of rows) {
    const cells = columns.map((col) => {
      const v = row[col];
      let raw;
      if (typeof formatCell === 'function') {
        raw = formatCell(v, col);
      } else if (v === null || v === undefined) {
        raw = '';
      } else if (typeof v === 'number' && Number.isFinite(v)) {
        raw = String(v);
      } else if (typeof v === 'object') {
        raw = JSON.stringify(v);
      } else {
        raw = String(v);
      }
      return escapeCsvField(raw);
    });
    lines.push(cells.join(','));
  }
  return lines.join('\r\n');
}

function triggerCsvDownload(filename, csvText) {
  const blob = new Blob([csvText], { type: 'text/csv;charset=utf-8' });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = filename;
  a.rel = 'noopener';
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  URL.revokeObjectURL(url);
}

function renderTablePanel(container, table, title, openStore, panelKey, helpText, timeZone, hourFormat, onRowHover, onRowLeave, columnOrderStorageBase) {
  if (!Array.isArray(table) || table.length === 0) return null;
  const first = table[0];
  if (!first || typeof first !== 'object') return null;
  let columns = Object.keys(first);
  if (columns.length === 0) return null;
  const sourceColumns = columns;
  const defaultColumns = moveTableTimeColumnFirst(sourceColumns);
  const columnOrderStorageKey =
    typeof columnOrderStorageBase === 'string' && columnOrderStorageBase !== '' && typeof panelKey === 'string' && panelKey !== ''
      ? `${TABLE_COLUMN_ORDER_LS_PREFIX}${columnOrderStorageBase}:${panelKey}:${tableColumnOrderSignature(sourceColumns)}`
      : '';
  columns = loadTableColumnOrder(columnOrderStorageKey, sourceColumns) ?? defaultColumns;
  const timeCol = detectTimeColumn(columns, table);
  const displayTimeCols = detectDisplayTimeColumns(columns, table);

  const toComparable = (v) => {
    if (v === null || v === undefined) return { kind: 'empty', value: null };
    if (typeof v === 'number' && Number.isFinite(v)) return { kind: 'number', value: v };
    if (v instanceof Date) return { kind: 'number', value: v.getTime() };
    if (typeof v === 'boolean') return { kind: 'number', value: v ? 1 : 0 };
    const s = typeof v === 'string' ? v.trim() : typeof v === 'object' ? JSON.stringify(v) : String(v);
    const n = s !== '' ? Number(s) : NaN;
    if (Number.isFinite(n) && /^-?\d+(\.\d+)?([eE][+-]?\d+)?$/.test(s)) {
      return { kind: 'number', value: n };
    }
    return { kind: 'string', value: s.toLowerCase() };
  };

  const makeSortedRows = (rows, sortCol, sortDir) => {
    if (!sortCol || !sortDir) return rows;
    const dir = sortDir === 'desc' ? -1 : 1;
    const decorated = rows.map((r, i) => ({ r, i }));
    decorated.sort((a, b) => {
      const av = toComparable(a.r?.[sortCol]);
      const bv = toComparable(b.r?.[sortCol]);
      if (av.kind !== bv.kind) {
        const order = av.kind === 'empty' ? 1 : bv.kind === 'empty' ? -1 : av.kind === 'number' ? -1 : 1;
        return order * dir;
      }
      if (av.value < bv.value) return -1 * dir;
      if (av.value > bv.value) return 1 * dir;
      return a.i - b.i;
    });
    return decorated.map((d) => d.r);
  };

  const details = document.createElement('details');
  details.className = 'strategy-chart-details';
  const summary = document.createElement('summary');
  summary.className = 'strategy-chart-summary';
  const titleEl = document.createElement('span');
  titleEl.className = 'strategy-chart-summary-title';
  titleEl.textContent = typeof title === 'string' && title.trim() ? title : t('chart.table_default_title');
  summary.appendChild(titleEl);
  const trimmedHelp = typeof helpText === 'string' ? helpText.trim() : '';
  if (trimmedHelp) {
    const wrap = document.createElement('span');
    wrap.className = 'strategy-chart-help-wrap';
    const helpBtn = document.createElement('button');
    helpBtn.type = 'button';
    helpBtn.className = 'strategy-chart-help-btn';
    helpBtn.setAttribute('aria-label', t('chart.description_aria'));
    helpBtn.textContent = '?';
    const stopToggle = (ev) => { ev.preventDefault(); ev.stopPropagation(); };
    helpBtn.addEventListener('mousedown', stopToggle);
    helpBtn.addEventListener('click', stopToggle);
    const tooltip = document.createElement('div');
    tooltip.className = 'strategy-chart-help-tooltip';
    tooltip.setAttribute('role', 'tooltip');
    tooltip.textContent = trimmedHelp;
    wrap.appendChild(helpBtn);
    wrap.appendChild(tooltip);
    summary.appendChild(wrap);
  }
  details.appendChild(summary);

  if (openStore && typeof panelKey === 'string' && panelKey !== '') {
    openStore.bind(details, panelKey);
  } else {
    details.open = true;
  }

  const wrap = document.createElement('div');
  wrap.style.cssText = 'padding:14px 4px 4px;';

  const toolbar = document.createElement('div');
  toolbar.style.cssText =
    'display:flex;justify-content:flex-end;align-items:center;margin-bottom:8px;padding:0 2px;';
  const dlBtn = document.createElement('button');
  dlBtn.type = 'button';
  dlBtn.textContent = t('chart.download_csv');
  dlBtn.style.cssText =
    'cursor:pointer;font-size:12px;font-weight:600;padding:6px 12px;border-radius:6px;border:1px solid #363a45;background:#2a2e39;color:#d1d4dc;';
  dlBtn.addEventListener('mouseenter', () => {
    dlBtn.style.background = '#363a45';
  });
  dlBtn.addEventListener('mouseleave', () => {
    dlBtn.style.background = '#2a2e39';
  });
  toolbar.appendChild(dlBtn);
  wrap.appendChild(toolbar);

  const tableScroll = document.createElement('div');
  const maxVisibleRows = 10;
  if (table.length > maxVisibleRows) {
    tableScroll.style.cssText = 'overflow:auto;max-height:300px;';
  } else {
    tableScroll.style.cssText = 'overflow-x:auto;';
  }

  const tbl = document.createElement('table');
  tbl.style.cssText =
    'width:100%;border-collapse:collapse;font-size:13px;color:#d1d4dc;background:#1e2130;border-radius:6px;';

  const thead = document.createElement('thead');
  const hr = document.createElement('tr');

  let sortCol = null;
  let sortDir = null;
  let currentRows = [...table];
  let draggedCol = null;
  let suppressHeaderClick = false;

  const tbody = document.createElement('tbody');

  const fmtCell = (v, col = '') => {
    if (v === null || v === undefined) return '';
    if (displayTimeCols.has(col)) {
      const u = toUnixSeconds(v);
      if (typeof u === 'number' && Number.isFinite(u) && u > 0) {
        return formatUnixDateTime(u, timeZone, hourFormat);
      }
    }
    if (typeof v === 'number' && Number.isFinite(v)) {
      return Number.isInteger(v) ? String(v) : v.toLocaleString('en-US', { maximumFractionDigits: 6 });
    }
    return tVal(String(v));
  };

  let rowTimeData = [];
  let chartHighlightedTr = null;

  const setChartHighlight = (tr) => {
    if (chartHighlightedTr && chartHighlightedTr !== tr) {
      chartHighlightedTr.style.outline = '';
      chartHighlightedTr.style.background = '';
    }
    chartHighlightedTr = tr;
    if (tr) {
      tr.style.outline = '1px solid rgba(255,255,255,0.25)';
      tr.style.background = 'rgba(255,255,255,0.06)';
    }
  };

  const clearChartHighlight = () => {
    if (chartHighlightedTr) {
      chartHighlightedTr.style.outline = '';
      chartHighlightedTr.style.background = '';
      chartHighlightedTr = null;
    }
  };

  const buildRowTimeData = () => {
    chartHighlightedTr = null;
    rowTimeData = [];
    if (!timeCol) return;
    const trs = [...tbody.children];
    for (let i = 0; i < trs.length; i++) {
      const row = currentRows[i];
      if (!row) continue;
      const u = toUnixSeconds(row[timeCol]);
      if (typeof u === 'number' && Number.isFinite(u) && u > 0) {
        rowTimeData.push({ unixSec: u, tr: trs[i] });
      }
    }
  };

  const renderBody = () => {
    tbody.innerHTML = '';
    for (const row of currentRows) {
      const tr = document.createElement('tr');
      tr.style.cssText = 'border-bottom:1px solid #2a2e39;transition:background 0.1s;';
      for (const col of columns) {
        const td = document.createElement('td');
        td.textContent = fmtCell(row?.[col], col);
        td.style.cssText = 'padding:8px 12px;';
        tr.appendChild(td);
      }
      tbody.appendChild(tr);
    }
    buildRowTimeData();
  };

  const clearHeaderDropStyles = () => {
    for (const th of hr.children) {
      th.style.boxShadow = '';
      th.style.opacity = '';
    }
  };

  const getHeaderDropTarget = (e) => {
    const th = e.target?.closest?.('th[data-col]');
    return th && hr.contains(th) ? th : null;
  };

  const getHeaderDropPlacement = (e, th) => {
    const rect = th.getBoundingClientRect();
    return e.clientX > rect.left + rect.width / 2 ? 'after' : 'before';
  };

  const updateHeaders = () => {
    for (const th of hr.children) {
      const col = th?.dataset?.col;
      const base = typeof col === 'string' ? tCol(col) : '';
      if (col && col === sortCol && sortDir) {
        th.textContent = `${base} ${sortDir === 'asc' ? '▲' : '▼'}`;
      } else {
        th.textContent = base;
      }
    }
  };

  const renderHeaders = () => {
    hr.innerHTML = '';
    for (const col of columns) {
      const th = document.createElement('th');
      th.dataset.col = col;
      th.draggable = true;
      th.title = t('chart.sort_reorder_hint');
      th.textContent = tCol(col);
      th.style.cssText =
        'position:sticky;top:0;z-index:1;text-align:left;padding:10px 12px;border-bottom:1px solid #363a45;color:#888;font-weight:600;text-transform:capitalize;cursor:grab;user-select:none;background:#1e2130;';
      th.addEventListener('click', () => {
        if (suppressHeaderClick) return;
        if (sortCol !== col) {
          sortCol = col;
          sortDir = 'asc';
        } else if (sortDir === 'asc') {
          sortDir = 'desc';
        } else if (sortDir === 'desc') {
          sortCol = null;
          sortDir = null;
        } else {
          sortDir = 'asc';
        }
        currentRows = makeSortedRows([...table], sortCol, sortDir);
        updateHeaders();
        renderBody();
      });
      th.addEventListener('dragstart', (e) => {
        draggedCol = col;
        suppressHeaderClick = true;
        e.dataTransfer.effectAllowed = 'move';
        e.dataTransfer.setData('text/plain', col);
        th.style.opacity = '0.55';
      });
      th.addEventListener('dragend', () => {
        draggedCol = null;
        clearHeaderDropStyles();
        setTimeout(() => {
          suppressHeaderClick = false;
        }, 0);
      });
      hr.appendChild(th);
    }
    updateHeaders();
  };

  hr.addEventListener('dragover', (e) => {
    const th = getHeaderDropTarget(e);
    if (!th || !draggedCol || draggedCol === th.dataset.col) return;
    e.preventDefault();
    e.dataTransfer.dropEffect = 'move';
    clearHeaderDropStyles();
    const placement = getHeaderDropPlacement(e, th);
    th.style.boxShadow = placement === 'after' ? 'inset -3px 0 0 #9aa0a6' : 'inset 3px 0 0 #9aa0a6';
  });

  hr.addEventListener('dragleave', (e) => {
    if (!hr.contains(e.relatedTarget)) clearHeaderDropStyles();
  });

  hr.addEventListener('drop', (e) => {
    const th = getHeaderDropTarget(e);
    if (!th) return;
    e.preventDefault();
    const dragCol = draggedCol || e.dataTransfer.getData('text/plain');
    const dropCol = th.dataset.col;
    const placement = getHeaderDropPlacement(e, th);
    const nextColumns = reorderTableColumns(columns, dragCol, dropCol, placement);
    clearHeaderDropStyles();
    if (nextColumns === columns) return;
    columns = nextColumns;
    saveTableColumnOrder(columnOrderStorageKey, columns);
    renderHeaders();
    renderBody();
  });

  renderHeaders();
  thead.appendChild(hr);
  tbl.appendChild(thead);
  tbl.appendChild(tbody);

  dlBtn.addEventListener('click', () => {
    const csv = tableRowsToCsv(columns, currentRows, fmtCell);
    triggerCsvDownload('strategy-table.csv', csv);
  });

  updateHeaders();
  renderBody();

  if (timeCol && typeof onRowHover === 'function') {
    tbody.addEventListener('mouseover', (e) => {
      const tr = e.target.closest('tr');
      if (!tr || !tbody.contains(tr)) return;
      const idx = [...tbody.children].indexOf(tr);
      const row = currentRows[idx];
      if (!row) return;
      const u = toUnixSeconds(row[timeCol]);
      if (typeof u === 'number' && Number.isFinite(u) && u > 0) {
        for (const item of rowTimeData) {
          item.tr.style.background = '';
        }
        tr.style.background = 'rgba(255,255,255,0.06)';
        onRowHover(u);
      }
    });
    tbody.addEventListener('mouseleave', () => {
      for (const item of rowTimeData) {
        item.tr.style.background = '';
      }
      onRowLeave?.();
    });
  }

  const scrollToTime = (unixSec) => {
    if (!rowTimeData.length) return;
    let nearest = rowTimeData[0];
    let nearestDist = Math.abs(rowTimeData[0].unixSec - unixSec);
    for (const item of rowTimeData) {
      const d = Math.abs(item.unixSec - unixSec);
      if (d < nearestDist) {
        nearestDist = d;
        nearest = item;
      }
    }
    const tr = nearest.tr;
    if (!tr) return;
    setChartHighlight(tr);
    const containerTop = tableScroll.scrollTop;
    const containerBottom = containerTop + tableScroll.clientHeight;
    const trTop = tr.offsetTop;
    const trBottom = trTop + tr.offsetHeight;
    if (trTop < containerTop) {
      tableScroll.scrollTop = trTop;
    } else if (trBottom > containerBottom) {
      tableScroll.scrollTop = trBottom - tableScroll.clientHeight;
    }
  };

  tableScroll.appendChild(tbl);
  wrap.appendChild(tableScroll);
  details.appendChild(wrap);
  container.appendChild(details);
  return { scrollToTime, clearHighlight: clearChartHighlight };
}

function buildMetricsPanelItems(metrics) {
  if (!metrics || typeof metrics !== 'object') return [];

  const fmtNumber0 = (v) =>
    typeof v === 'number' && Number.isFinite(v)
      ? v.toLocaleString('en-US', { minimumFractionDigits: 0, maximumFractionDigits: 0 })
      : String(v ?? '');

  const fmtNumber3 = (v) =>
    typeof v === 'number' && Number.isFinite(v)
      ? v.toLocaleString('en-US', { minimumFractionDigits: 3, maximumFractionDigits: 3 })
      : String(v ?? '');

  const fmtPercent3 = (v) =>
    typeof v === 'number' && Number.isFinite(v) ? `${fmtNumber3(v)}%` : `${String(v ?? '')}%`;

  const items = [
    {
      label: t('chart.total_return'),
      value: fmtPercent3(metrics.total_return),
      color: (metrics.total_return ?? 0) >= 0 ? '#26a69a' : '#ef5350',
    },
    { label: t('chart.sharpe_ratio'), value: fmtNumber3(metrics.sharpe_ratio), color: '#d1d4dc' },
    { label: t('chart.max_drawdown'), value: fmtPercent3(metrics.max_drawdown), color: '#ef5350' },
    { label: t('chart.win_rate'), value: fmtPercent3(metrics.win_rate), color: '#d1d4dc' },
    {
      label: t('chart.orders_count'),
      value:
        typeof metrics.num_trades === 'number' && Number.isFinite(metrics.num_trades)
          ? fmtNumber0(metrics.num_trades)
          : metrics.num_trades,
      color: '#d1d4dc',
    },
    { label: t('chart.final_equity'), value: `$${fmtNumber3(metrics.final_equity)}`, color: '#d1d4dc' },
  ].filter((item) => item.value != null && item.value !== 'undefined%' && item.value !== '$undefined');

  return items;
}

function renderMetricsPanel(container, metrics, openStore) {
  const items = buildMetricsPanelItems(metrics);
  if (items.length === 0) return;

  const details = document.createElement('details');
  details.className = 'strategy-chart-details';
  const summary = document.createElement('summary');
  summary.className = 'strategy-chart-summary';
  summary.textContent = t('chart.metrics_title');
  details.appendChild(summary);

  if (openStore) {
    openStore.bind(details, 'metrics');
  } else {
    details.open = true;
  }

  const el = document.createElement('div');
  el.style.cssText = 'display:flex;flex-wrap:wrap;gap:10px;padding:14px 4px 4px;';

  for (const item of items) {
    const card = document.createElement('div');
    card.style.cssText =
      'flex:1;min-width:110px;background:#1e2130;border-radius:6px;padding:12px 10px;text-align:center;';
    card.innerHTML = `
      <div style="color:#888;font-size:10px;text-transform:uppercase;letter-spacing:0.6px;margin-bottom:6px;">${item.label}</div>
      <div style="color:${item.color};font-size:19px;font-weight:700;">${item.value}</div>
    `;
    el.appendChild(card);
  }

  details.appendChild(el);
  container.appendChild(details);
}

function renderOneChartPanel(panelHost, spec, openStore, srcIdx, catalogMap, timeZone, hourFormat, onCrosshairMove, onRowHover, onRowLeave, onRemove, alignRightEdge, columnOrderStorageBase) {
  const panelKey = `c${srcIdx}`;
  const chartDesc = typeof spec.description === 'string' ? spec.description.trim() : '';
  if (spec.type === 'lightweight-charts') {
    const catalogHelp = helpTextForLightweightChart(spec, catalogMap);
    const helpText = [chartDesc, catalogHelp].filter(Boolean).join('\n\n') || null;
    return renderLightweightChart(panelHost, spec, openStore, panelKey, helpText, timeZone, hourFormat, onCrosshairMove, onRemove, alignRightEdge);
  }
  if (spec.type === 'plotly') {
    return { chart: null, primarySeries: null, ...renderPlotlyChart(panelHost, spec, openStore, panelKey, chartDesc || null, onRemove) };
  }
  if (spec.type === 'table') {
    const tableSync = renderTablePanel(panelHost, spec.rows, spec.title, openStore, panelKey, chartDesc || null, timeZone, hourFormat, onRowHover, onRowLeave, columnOrderStorageBase);
    return { chart: null, primarySeries: null, scrollToTime: tableSync?.scrollToTime ?? null, clearHighlight: tableSync?.clearHighlight ?? null };
  }
  return { chart: null, primarySeries: null };
}

export function renderCharts(container, dataJson, options) {
  container.innerHTML = '';

  const lwCharts = [];
  const lwCrosshairBindings = [];
  const lwSyncFns = [];
  const tableSyncers = [];
  const crosshairUnsubs = [];
  const cleanupFns = [];
  let dndSignal = null;
  const charts = dataJson?.charts;
  const base = options?.chartOrderStorageBase;
  const timeZone = normalizeTimeZone(options?.timeZone);
  const hourFormat = options?.hourFormat;
  const alignRightEdge = options?.alignRightEdge === true;
  const chartCount = Array.isArray(charts) ? charts.length : 0;
  const catalogMap = catalogMapFromDataJson(dataJson);
  const includeMetrics = buildMetricsPanelItems(dataJson?.metrics ?? null).length > 0;
  const metricsSrcIdx = chartCount;
  const panelCount = chartCount + (includeMetrics ? 1 : 0);
  const openBase = typeof base === 'string' && base.trim() !== '';
  const detailsSig = chartDetailsSignature(charts || [], dataJson?.metrics);
  const chartUiStorageBase = openBase ? `${base}:${detailsSig}` : '';
  const hiddenStorageKey = openBase ? `${HIDDEN_PANELS_LS_PREFIX}${base}:${detailsSig}` : '';
  const hiddenPanelKeys = loadHiddenPanelKeys(hiddenStorageKey, chartCount);
  const openStore =
    openBase && (chartCount > 0 || includeMetrics)
      ? createDetailsOpenStore(
          `${DETAILS_OPEN_LS_PREFIX}${base}:${detailsSig}`,
          chartCount,
          includeMetrics,
        )
      : null;

  const onCrosshairMove = (unixSec) => {
    if (unixSec == null) {
      for (const { clearHighlight } of tableSyncers) clearHighlight();
      return;
    }
    for (const { scrollToTime } of tableSyncers) scrollToTime(unixSec);
  };

  const onRowHover = (unixSec) => {
    for (const { setCrosshairAt } of lwSyncFns) {
      setCrosshairAt(unixSec);
    }
  };

  const onRowLeave = () => {
    for (const { clearCrosshair } of lwSyncFns) {
      clearCrosshair();
    }
  };

  const collectResult = (result) => {
    const { chart, primarySeries, setCrosshairAt, clearCrosshair, crosshairUnsub, scrollToTime, clearHighlight, cleanup } = result ?? {};
    if (chart) {
      lwCharts.push(chart);
      lwCrosshairBindings.push({ chart, series: primarySeries });
      if (setCrosshairAt) lwSyncFns.push({ setCrosshairAt, clearCrosshair });
      if (crosshairUnsub) crosshairUnsubs.push(crosshairUnsub);
    }
    if (scrollToTime) {
      tableSyncers.push({ scrollToTime, clearHighlight: clearHighlight ?? (() => {}) });
    }
    if (cleanup) {
      cleanupFns.push(cleanup);
    }
  };

  const removePanel = (panelKey, details) => {
    if (typeof panelKey === 'string' && panelKey !== '') {
      hiddenPanelKeys.add(panelKey);
      saveHiddenPanelKeys(hiddenStorageKey, hiddenPanelKeys);
    }
    const dndRow = details?.closest?.('.strategy-chart-dnd-row');
    if (dndRow) {
      dndRow.remove();
      return;
    }
    details?.remove?.();
  };

  if (panelCount > 0) {
    const useDnd = typeof base === 'string' && base.trim() !== '' && panelCount > 1;
    if (useDnd) {
      const sig = chartsOrderSignature(charts);
      const storageKey = `${CHART_ORDER_LS_PREFIX}${base}:${sig}`;
      const defaultOrder = [
        ...(includeMetrics ? [metricsSrcIdx] : []),
        ...[...Array(chartCount).keys()],
      ];
      const order = loadChartOrder(storageKey, panelCount) ?? defaultOrder;
      const list = document.createElement('div');
      list.className = 'strategy-charts-dnd-list';
      container.appendChild(list);
      for (const srcIdx of order) {
        if (!(includeMetrics && srcIdx === metricsSrcIdx) && hiddenPanelKeys.has(`c${srcIdx}`)) continue;
        const row = createChartDndRow(srcIdx);
        list.appendChild(row);
        const inner = row.querySelector('.strategy-chart-dnd-inner');
        if (includeMetrics && srcIdx === metricsSrcIdx) {
          renderMetricsPanel(inner, dataJson?.metrics, openStore);
        } else {
          const spec = charts[srcIdx];
          if (!spec) continue;
          collectResult(renderOneChartPanel(inner, spec, openStore, srcIdx, catalogMap, timeZone, hourFormat, onCrosshairMove, onRowHover, onRowLeave, removePanel, alignRightEdge, chartUiStorageBase));
        }
        enableChartDndHeader(row);
      }
      dndSignal = new AbortController();
      setupChartPanelDnD(list, storageKey, panelCount, dndSignal.signal, order);
    } else {
      renderMetricsPanel(container, dataJson?.metrics, openStore);
      if (Array.isArray(charts)) {
        for (let srcIdx = 0; srcIdx < charts.length; srcIdx++) {
          if (hiddenPanelKeys.has(`c${srcIdx}`)) continue;
          const spec = charts[srcIdx];
          collectResult(renderOneChartPanel(container, spec, openStore, srcIdx, catalogMap, timeZone, hourFormat, onCrosshairMove, onRowHover, onRowLeave, removePanel, alignRightEdge, chartUiStorageBase));
        }
      }
    }
  }

  return {
    lwCharts,
    lwCrosshairBindings,
    detachChartDnD: () => {
      dndSignal?.abort();
      for (const unsub of crosshairUnsubs) unsub();
      for (const cleanup of cleanupFns) cleanup();
    },
  };
}
