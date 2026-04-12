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
  crosshair: { mode: 1 },
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
  const wrap = document.createElement('div');
  wrap.style.cssText = 'margin-bottom:8px;';

  const title = document.createElement('div');
  title.textContent = titleText;
  title.style.cssText =
    'color:#6b707c;font-size:13px;font-weight:600;padding:10px 4px 6px;letter-spacing:0.3px;';
  wrap.appendChild(title);

  const chartEl = document.createElement('div');
  chartEl.style.cssText = 'width:100%;';
  wrap.appendChild(chartEl);

  container.appendChild(wrap);
  return chartEl;
}

function renderLightweightChart(container, chartSpec) {
  const el = makeSection(container, chartSpec.title || '');
  el.style.height = '350px';

  const chart = createChart(el, { ...CHART_THEME, autoSize: true, height: 350 });

  for (const s of chartSpec.series || []) {
    const SeriesClass = SERIES_TYPE_MAP[s.type];
    if (!SeriesClass) continue;
    const series = chart.addSeries(SeriesClass, {
      priceLineVisible: false,
      lastValueVisible: true,
      ...s.options,
    });
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
  return chart;
}

function renderPlotlyChart(container, chartSpec) {
  const el = makeSection(container, chartSpec.title || '');
  el.style.minHeight = '300px';

  const layout = {
    ...PLOTLY_LAYOUT_DEFAULTS,
    ...(chartSpec.layout || {}),
    font: { ...PLOTLY_LAYOUT_DEFAULTS.font, ...(chartSpec.layout?.font || {}) },
    xaxis: { ...PLOTLY_LAYOUT_DEFAULTS.xaxis, ...(chartSpec.layout?.xaxis || {}) },
    yaxis: { ...PLOTLY_LAYOUT_DEFAULTS.yaxis, ...(chartSpec.layout?.yaxis || {}) },
  };

  Plotly.newPlot(el, chartSpec.data || [], layout, PLOTLY_CONFIG);
}

function renderMetricsPanel(container, metrics) {
  if (!metrics || typeof metrics !== 'object') return;

  const fmt = (v) =>
    typeof v === 'number'
      ? v.toLocaleString('en-US', { minimumFractionDigits: 0, maximumFractionDigits: 0 })
      : String(v ?? '');

  const items = [
    {
      label: 'Total Return',
      value: `${metrics.total_return}%`,
      color: (metrics.total_return ?? 0) >= 0 ? '#26a69a' : '#ef5350',
    },
    { label: 'Sharpe Ratio', value: Number(metrics.sharpe_ratio ?? 0).toFixed(3), color: '#d1d4dc' },
    { label: 'Max Drawdown', value: `${metrics.max_drawdown}%`, color: '#ef5350' },
    { label: 'Win Rate', value: `${metrics.win_rate}%`, color: '#d1d4dc' },
    { label: '# Trades', value: metrics.num_trades, color: '#d1d4dc' },
    { label: 'Final Equity', value: `$${fmt(metrics.final_equity)}`, color: '#d1d4dc' },
  ].filter((item) => item.value != null && item.value !== 'undefined%' && item.value !== '$undefined');

  if (items.length === 0) return;

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

  container.appendChild(el);
}

export function renderCharts(container, dataJson) {
  container.innerHTML = '';

  const charts = dataJson?.charts;
  if (!Array.isArray(charts) || charts.length === 0) return { lwCharts: [] };

  const lwCharts = [];

  for (const spec of charts) {
    if (spec.type === 'lightweight-charts') {
      const chart = renderLightweightChart(container, spec);
      lwCharts.push(chart);
    } else if (spec.type === 'plotly') {
      renderPlotlyChart(container, spec);
    }
  }

  renderMetricsPanel(container, dataJson?.metrics);

  return { lwCharts };
}
