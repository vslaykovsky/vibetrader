import {
  createChart,
  CandlestickSeries,
  LineSeries,
  AreaSeries,
  HistogramSeries,
  BaselineSeries,
  BarSeries,
  createSeriesMarkers,
} from 'lightweight-charts';
import Plotly from 'plotly.js-dist-min';
import { CHART_THEME } from './lib/chartTheme.js';
import {
  chartDetailsSignature,
  chartsOrderSignature,
  reorderChartPanels,
  sanitizeDetailsOpenState,
  validateChartOrder,
} from './lib/chartOrder.js';

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

const INTRADAY_TIME_RE = /[T ]\d{2}:\d{2}/;
const DATE_ONLY_RE = /^\d{4}-\d{2}-\d{2}$/;

const CHART_ORDER_LS_PREFIX = 'vibetrader:chartPanelOrder:';
const DETAILS_OPEN_LS_PREFIX = 'vibetrader:chartDetailsOpen:';

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

function createChartDndRow(srcIdx) {
  const row = document.createElement('div');
  row.className = 'strategy-chart-dnd-row';
  row.dataset.srcIdx = String(srcIdx);

  const handle = document.createElement('div');
  handle.className = 'strategy-chart-dnd-handle';
  handle.draggable = true;
  handle.title = 'Drag to reorder charts';
  handle.setAttribute('aria-label', 'Drag to reorder charts');

  const inner = document.createElement('div');
  inner.className = 'strategy-chart-dnd-inner';

  row.appendChild(handle);
  row.appendChild(inner);
  return row;
}

function setupChartPanelDnD(dndRoot, storageKey, n, signal) {
  const rows = () => [...dndRoot.querySelectorAll('.strategy-chart-dnd-row')];

  const applyOrderToDom = (order) => {
    for (const srcIdx of order) {
      const row = dndRoot.querySelector(`.strategy-chart-dnd-row[data-src-idx="${srcIdx}"]`);
      if (row) dndRoot.appendChild(row);
    }
  };

  let draggedSrc = null;

  const onDragStart = (e) => {
    const handle = e.target;
    if (!handle?.classList?.contains?.('strategy-chart-dnd-handle')) return;
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
    let order = rows().map((r) => Number(r.dataset.srcIdx));
    order = reorderChartPanels(order, dragSrc, dropSrc);
    if (!validateChartOrder(order, n)) return;
    applyOrderToDom(order);
    saveChartOrder(storageKey, order);
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

function makeSection(container, titleText, openStore, panelKey) {
  const details = document.createElement('details');
  details.className = 'strategy-chart-details';

  const summary = document.createElement('summary');
  summary.className = 'strategy-chart-summary';
  summary.textContent = titleText || 'Chart';
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

function renderLightweightChart(container, chartSpec, openStore, panelKey) {
  const { details, chartEl: el } = makeSection(
    container,
    chartSpec.title || '',
    openStore,
    panelKey,
  );
  el.style.height = '350px';

  const chart = createChart(el, { ...CHART_THEME, autoSize: true, height: 350 });

  details.addEventListener('toggle', () => {
    if (!details.open) return;
    requestAnimationFrame(() => {
      const w = el.clientWidth;
      const h = el.clientHeight || 350;
      chart.resize(w, h);
    });
  });

  const normalizeTime = chartHasIntradayTime(chartSpec) ? toUnixSeconds : toBusinessDay;

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
      const sorted = [...normalizedMarkers].sort((a, b) =>
        a.time < b.time ? -1 : a.time > b.time ? 1 : 0,
      );
      createSeriesMarkers(series, sorted);
    }
  }

  chart.timeScale().fitContent();
  return { chart, primarySeries };
}

export { attachSyncedCrosshair } from './lib/lwcSync.js';

function renderPlotlyChart(container, chartSpec, openStore, panelKey) {
  const { details, chartEl: el } = makeSection(
    container,
    chartSpec.title || '',
    openStore,
    panelKey,
  );
  el.style.minHeight = '300px';

  const layout = {
    ...PLOTLY_LAYOUT_DEFAULTS,
    ...(chartSpec.layout || {}),
    font: { ...PLOTLY_LAYOUT_DEFAULTS.font, ...(chartSpec.layout?.font || {}) },
    xaxis: { ...PLOTLY_LAYOUT_DEFAULTS.xaxis, ...(chartSpec.layout?.xaxis || {}) },
    yaxis: { ...PLOTLY_LAYOUT_DEFAULTS.yaxis, ...(chartSpec.layout?.yaxis || {}) },
  };

  Plotly.newPlot(el, chartSpec.data || [], layout, PLOTLY_CONFIG);

  details.addEventListener('toggle', () => {
    if (!details.open) return;
    requestAnimationFrame(() => Plotly.Plots.resize(el));
  });
}

function escapeCsvField(value) {
  const s = value == null ? '' : String(value);
  if (/[",\n\r]/.test(s)) {
    return `"${s.replace(/"/g, '""')}"`;
  }
  return s;
}

function tableRowsToCsv(columns, rows) {
  const header = columns.map((c) => escapeCsvField(c)).join(',');
  const lines = [header];
  for (const row of rows) {
    const cells = columns.map((col) => {
      const v = row[col];
      let raw;
      if (v === null || v === undefined) {
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

function renderTablePanel(container, table, title, openStore, panelKey) {
  if (!Array.isArray(table) || table.length === 0) return;
  const first = table[0];
  if (!first || typeof first !== 'object') return;
  const columns = Object.keys(first);
  if (columns.length === 0) return;

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
  summary.textContent = typeof title === 'string' && title.trim() ? title : 'Table';
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
  dlBtn.textContent = 'Download CSV';
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
  const maxVisibleRows = 20;
  if (table.length > maxVisibleRows) {
    tableScroll.style.cssText = 'overflow:auto;max-height:560px;';
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

  const tbody = document.createElement('tbody');

  const fmtCell = (v) => {
    if (v === null || v === undefined) return '';
    if (typeof v === 'number' && Number.isFinite(v)) {
      return Number.isInteger(v) ? String(v) : v.toLocaleString('en-US', { maximumFractionDigits: 6 });
    }
    return String(v);
  };

  const renderBody = () => {
    tbody.innerHTML = '';
    for (const row of currentRows) {
      const tr = document.createElement('tr');
      tr.style.cssText = 'border-bottom:1px solid #2a2e39;';
      for (const col of columns) {
        const td = document.createElement('td');
        td.textContent = fmtCell(row?.[col]);
        td.style.cssText = 'padding:8px 12px;';
        tr.appendChild(td);
      }
      tbody.appendChild(tr);
    }
  };

  const updateHeaders = () => {
    for (const th of hr.children) {
      const col = th?.dataset?.col;
      const base = typeof col === 'string' ? col.replace(/_/g, ' ') : '';
      if (col && col === sortCol && sortDir) {
        th.textContent = `${base} ${sortDir === 'asc' ? '▲' : '▼'}`;
      } else {
        th.textContent = base;
      }
    }
  };

  for (const col of columns) {
    const th = document.createElement('th');
    th.dataset.col = col;
    th.textContent = col.replace(/_/g, ' ');
    th.style.cssText =
      'text-align:left;padding:10px 12px;border-bottom:1px solid #363a45;color:#888;font-weight:600;text-transform:capitalize;cursor:pointer;user-select:none;';
    th.addEventListener('click', () => {
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
    hr.appendChild(th);
  }
  thead.appendChild(hr);
  tbl.appendChild(thead);
  tbl.appendChild(tbody);

  dlBtn.addEventListener('click', () => {
    const csv = tableRowsToCsv(columns, currentRows);
    triggerCsvDownload('strategy-table.csv', csv);
  });

  updateHeaders();
  renderBody();

  tableScroll.appendChild(tbl);
  wrap.appendChild(tableScroll);
  details.appendChild(wrap);
  container.appendChild(details);
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
      label: 'Total Return',
      value: fmtPercent3(metrics.total_return),
      color: (metrics.total_return ?? 0) >= 0 ? '#26a69a' : '#ef5350',
    },
    { label: 'Sharpe Ratio', value: fmtNumber3(metrics.sharpe_ratio), color: '#d1d4dc' },
    { label: 'Max Drawdown', value: fmtPercent3(metrics.max_drawdown), color: '#ef5350' },
    { label: 'Win Rate', value: fmtPercent3(metrics.win_rate), color: '#d1d4dc' },
    {
      label: '# Trades',
      value:
        typeof metrics.num_trades === 'number' && Number.isFinite(metrics.num_trades)
          ? fmtNumber0(metrics.num_trades)
          : metrics.num_trades,
      color: '#d1d4dc',
    },
    { label: 'Final Equity', value: `$${fmtNumber3(metrics.final_equity)}`, color: '#d1d4dc' },
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
  summary.textContent = 'Metrics';
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

function renderOneChartPanel(panelHost, spec, openStore, srcIdx) {
  const panelKey = `c${srcIdx}`;
  if (spec.type === 'lightweight-charts') {
    return renderLightweightChart(panelHost, spec, openStore, panelKey);
  }
  if (spec.type === 'plotly') {
    renderPlotlyChart(panelHost, spec, openStore, panelKey);
    return { chart: null, primarySeries: null };
  }
  if (spec.type === 'table') {
    renderTablePanel(panelHost, spec.rows, spec.title, openStore, panelKey);
    return { chart: null, primarySeries: null };
  }
  return { chart: null, primarySeries: null };
}

export function renderCharts(container, dataJson, options) {
  container.innerHTML = '';

  const lwCharts = [];
  const lwCrosshairBindings = [];
  let dndSignal = null;
  const charts = dataJson?.charts;
  const base = options?.chartOrderStorageBase;
  const chartCount = Array.isArray(charts) ? charts.length : 0;
  const includeMetrics = buildMetricsPanelItems(dataJson?.metrics ?? null).length > 0;
  const openBase = typeof base === 'string' && base.trim() !== '';
  const detailsSig = chartDetailsSignature(charts || [], dataJson?.metrics);
  const openStore =
    openBase && (chartCount > 0 || includeMetrics)
      ? createDetailsOpenStore(
          `${DETAILS_OPEN_LS_PREFIX}${base}:${detailsSig}`,
          chartCount,
          includeMetrics,
        )
      : null;

  if (Array.isArray(charts) && charts.length > 0) {
    const n = charts.length;
    const useDnd = typeof base === 'string' && base.trim() !== '' && n > 1;
    if (useDnd) {
      const sig = chartsOrderSignature(charts);
      const storageKey = `${CHART_ORDER_LS_PREFIX}${base}:${sig}`;
      const order = loadChartOrder(storageKey, n) ?? [...Array(n).keys()];
      const list = document.createElement('div');
      list.className = 'strategy-charts-dnd-list';
      container.appendChild(list);
      for (const srcIdx of order) {
        const spec = charts[srcIdx];
        if (!spec) continue;
        const row = createChartDndRow(srcIdx);
        list.appendChild(row);
        const inner = row.querySelector('.strategy-chart-dnd-inner');
        const { chart, primarySeries } = renderOneChartPanel(inner, spec, openStore, srcIdx);
        if (chart) {
          lwCharts.push(chart);
          lwCrosshairBindings.push({ chart, series: primarySeries });
        }
      }
      dndSignal = new AbortController();
      setupChartPanelDnD(list, storageKey, n, dndSignal.signal);
    } else {
      for (let srcIdx = 0; srcIdx < charts.length; srcIdx++) {
        const spec = charts[srcIdx];
        const { chart, primarySeries } = renderOneChartPanel(container, spec, openStore, srcIdx);
        if (chart) {
          lwCharts.push(chart);
          lwCrosshairBindings.push({ chart, series: primarySeries });
        }
      }
    }
  }

  renderMetricsPanel(container, dataJson?.metrics, openStore);

  return {
    lwCharts,
    lwCrosshairBindings,
    detachChartDnD: () => {
      dndSignal?.abort();
    },
  };
}
