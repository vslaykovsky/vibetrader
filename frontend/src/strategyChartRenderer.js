import {
  createChart,
  CandlestickSeries,
  LineSeries,
  AreaSeries,
  HistogramSeries,
  BaselineSeries,
  BarSeries,
  createSeriesMarkers,
  LineStyle,
  MismatchDirection,
} from 'lightweight-charts';
import Plotly from 'plotly.js-dist-min';

const SERIES_TYPE_MAP = {
  Candlestick: CandlestickSeries,
  Line: LineSeries,
  Area: AreaSeries,
  Histogram: HistogramSeries,
  Baseline: BaselineSeries,
  Bar: BarSeries,
};

const CHART_THEME = {
  layout: {
    background: { color: '#131722' },
    textColor: '#d1d4dc',
    attributionLogo: false,
  },
  grid: {
    vertLines: { color: '#1e2130' },
    horzLines: { color: '#1e2130' },
  },
  crosshair: {
    mode: 1,
    vertLine: { style: LineStyle.Dashed },
  },
  timeScale: { borderColor: '#363a45', timeVisible: true },
  rightPriceScale: { borderColor: '#363a45' },
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

function makeSection(container, titleText) {
  const details = document.createElement('details');
  details.className = 'strategy-chart-details';
  details.open = true;

  const summary = document.createElement('summary');
  summary.className = 'strategy-chart-summary';
  summary.textContent = titleText || 'Chart';
  details.appendChild(summary);

  const chartEl = document.createElement('div');
  chartEl.className = 'strategy-chart-body';
  chartEl.style.cssText = 'width:100%;';
  details.appendChild(chartEl);

  container.appendChild(details);
  return { details, chartEl };
}

function renderLightweightChart(container, chartSpec) {
  const { details, chartEl: el } = makeSection(container, chartSpec.title || '');
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
      series.setData(s.data);
    }
    if (Array.isArray(s.markers) && s.markers.length > 0) {
      const sorted = [...s.markers].sort((a, b) =>
        a.time < b.time ? -1 : a.time > b.time ? 1 : 0,
      );
      createSeriesMarkers(series, sorted);
    }
  }

  chart.timeScale().fitContent();
  return { chart, primarySeries };
}

function priceFromBarForCrosshair(bar) {
  if (bar == null || typeof bar !== 'object') {
    return null;
  }
  const v = bar.value;
  if (typeof v === 'number' && Number.isFinite(v)) {
    return v;
  }
  const c = bar.close;
  if (typeof c === 'number' && Number.isFinite(c)) {
    return c;
  }
  const o = bar.open;
  if (typeof o === 'number' && Number.isFinite(o)) {
    return o;
  }
  return null;
}

export function attachSyncedCrosshair(bindings) {
  const valid = bindings.filter((b) => b.series != null);
  if (valid.length < 2) {
    return undefined;
  }
  let syncing = false;
  const subs = valid.map(({ chart, series }) => {
    const handler = (param) => {
      if (syncing) {
        return;
      }
      if (!param.time) {
        syncing = true;
        try {
          for (const { chart: c } of valid) {
            if (c !== chart) {
              c.clearCrosshairPosition();
            }
          }
        } finally {
          syncing = false;
        }
        return;
      }
      if (param.sourceEvent == null) {
        return;
      }
      const t = param.time;
      syncing = true;
      try {
        for (const { chart: targetChart, series: targetSeries } of valid) {
          if (targetChart === chart) {
            continue;
          }
          const idx = targetChart.timeScale().timeToIndex(t, true);
          if (idx === null) {
            targetChart.clearCrosshairPosition();
            continue;
          }
          let bar;
          try {
            bar = targetSeries.dataByIndex(idx, MismatchDirection.NearestLeft);
          } catch {
            bar = undefined;
          }
          const price = priceFromBarForCrosshair(bar);
          if (price == null) {
            targetChart.clearCrosshairPosition();
          } else {
            targetChart.setCrosshairPosition(price, t, targetSeries);
          }
        }
      } finally {
        syncing = false;
      }
    };
    chart.subscribeCrosshairMove(handler);
    return { chart, handler };
  });
  return () => {
    for (const { chart, handler } of subs) {
      chart.unsubscribeCrosshairMove(handler);
    }
  };
}

function renderPlotlyChart(container, chartSpec) {
  const { details, chartEl: el } = makeSection(container, chartSpec.title || '');
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

function renderTablePanel(container, table, title) {
  if (!Array.isArray(table) || table.length === 0) return;
  const first = table[0];
  if (!first || typeof first !== 'object') return;
  const columns = Object.keys(first);
  if (columns.length === 0) return;

  const details = document.createElement('details');
  details.className = 'strategy-chart-details';
  details.open = true;
  const summary = document.createElement('summary');
  summary.className = 'strategy-chart-summary';
  summary.textContent = typeof title === 'string' && title.trim() ? title : 'Table';
  details.appendChild(summary);

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
  dlBtn.addEventListener('click', () => {
    const csv = tableRowsToCsv(columns, table);
    triggerCsvDownload('strategy-table.csv', csv);
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
  for (const col of columns) {
    const th = document.createElement('th');
    th.textContent = col.replace(/_/g, ' ');
    th.style.cssText =
      'text-align:left;padding:10px 12px;border-bottom:1px solid #363a45;color:#888;font-weight:600;text-transform:capitalize;';
    hr.appendChild(th);
  }
  thead.appendChild(hr);
  tbl.appendChild(thead);

  const tbody = document.createElement('tbody');
  for (const row of table) {
    const tr = document.createElement('tr');
    tr.style.cssText = 'border-bottom:1px solid #2a2e39;';
    for (const col of columns) {
      const td = document.createElement('td');
      const v = row[col];
      if (v === null || v === undefined) {
        td.textContent = '';
      } else if (typeof v === 'number' && Number.isFinite(v)) {
        td.textContent = Number.isInteger(v)
          ? String(v)
          : v.toLocaleString('en-US', { maximumFractionDigits: 6 });
      } else {
        td.textContent = String(v);
      }
      td.style.cssText = 'padding:8px 12px;';
      tr.appendChild(td);
    }
    tbody.appendChild(tr);
  }
  tbl.appendChild(tbody);

  tableScroll.appendChild(tbl);
  wrap.appendChild(tableScroll);
  details.appendChild(wrap);
  container.appendChild(details);
}

function renderMetricsPanel(container, metrics) {
  if (!metrics || typeof metrics !== 'object') return;

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

  if (items.length === 0) return;

  const details = document.createElement('details');
  details.className = 'strategy-chart-details';
  details.open = true;
  const summary = document.createElement('summary');
  summary.className = 'strategy-chart-summary';
  summary.textContent = 'Metrics';
  details.appendChild(summary);

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

export function renderCharts(container, dataJson) {
  container.innerHTML = '';

  const lwCharts = [];
  const lwCrosshairBindings = [];
  const charts = dataJson?.charts;
  if (Array.isArray(charts)) {
    for (const spec of charts) {
      if (spec.type === 'lightweight-charts') {
        const { chart, primarySeries } = renderLightweightChart(container, spec);
        lwCharts.push(chart);
        lwCrosshairBindings.push({ chart, series: primarySeries });
      } else if (spec.type === 'plotly') {
        renderPlotlyChart(container, spec);
      } else if (spec.type === 'table') {
        renderTablePanel(container, spec.rows, spec.title);
      }
    }
  }

  renderMetricsPanel(container, dataJson?.metrics);

  return { lwCharts, lwCrosshairBindings };
}
