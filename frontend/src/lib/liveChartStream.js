const EMPTY_STATE = {
  series: {},
  bars: {},
  indicators: {},
  positions: {},
  positionTickers: new Set(),
  trades: [],
  status: null,
};

function cloneEmptyState() {
  return {
    series: {},
    bars: {},
    indicators: {},
    positions: {},
    positionTickers: new Set(),
    trades: [],
    status: null,
  };
}

export function createLiveChartState() {
  return cloneEmptyState();
}

function asNumber(v) {
  const n = Number(v);
  return Number.isFinite(n) ? n : null;
}

function asString(v) {
  return typeof v === 'string' ? v : '';
}

function pointTime(data) {
  const n = asNumber(data?.time);
  return n == null ? null : Math.floor(n);
}

function upsertByTime(items, next) {
  const idx = items.findIndex((x) => x.time === next.time);
  if (idx >= 0) {
    items[idx] = next;
  } else {
    items.push(next);
    items.sort((a, b) => a.time - b.time);
  }
}

function seriesMetaFromData(data, fallbackSource) {
  const rawSource = asString(data?.source);
  const source =
    rawSource === 'input'
      ? 'input_indicator'
      : rawSource === 'output'
        ? 'output_indicator'
        : rawSource || fallbackSource;
  return {
    chart_id: asString(data?.chart_id),
    series_id: asString(data?.series_id),
    source,
    label: asString(data?.label) || asString(data?.series_id),
    name: asString(data?.name),
    ticker: asString(data?.ticker),
    scale: asString(data?.scale),
    description: asString(data?.description),
  };
}

function rememberSeries(state, meta) {
  if (!meta.series_id) return;
  state.series[meta.series_id] = {
    ...(state.series[meta.series_id] || {}),
    ...meta,
  };
}

function applyBar(state, data) {
  const time = pointTime(data);
  const open = asNumber(data?.open);
  const high = asNumber(data?.high);
  const low = asNumber(data?.low);
  const close = asNumber(data?.close);
  const seriesId = asString(data?.series_id);
  if (!seriesId || time == null || open == null || high == null || low == null || close == null) return false;
  rememberSeries(state, seriesMetaFromData(data, 'ohlcv'));
  const bucket = state.bars[seriesId] || { data: [] };
  upsertByTime(bucket.data, { time, open, high, low, close });
  state.bars[seriesId] = bucket;
  return true;
}

function applyIndicator(state, data) {
  const time = pointTime(data);
  const value = asNumber(data?.value);
  const seriesId = asString(data?.series_id);
  if (!seriesId || time == null || value == null) return false;
  rememberSeries(state, seriesMetaFromData(data, `${asString(data?.source)}_indicator`));
  const bucket = state.indicators[seriesId] || [];
  upsertByTime(bucket, { time, value });
  state.indicators[seriesId] = bucket;
  return true;
}

function applyPosition(state, data) {
  const time = pointTime(data);
  if (time == null) return false;
  const rows = Array.isArray(data?.positions) ? data.positions : [];
  const active = new Set();
  for (const row of rows) {
    const ticker = asString(row?.ticker);
    if (!ticker) continue;
    active.add(ticker);
    state.positionTickers.add(ticker);
    const seriesId = `position:${ticker}`;
    rememberSeries(state, {
      chart_id: 'positions',
      series_id: seriesId,
      source: 'position',
      label: `${ticker} position value`,
      ticker,
    });
    const bucket = state.positions[seriesId] || [];
    upsertByTime(bucket, { time, value: asNumber(row?.value) ?? 0 });
    state.positions[seriesId] = bucket;
  }
  for (const ticker of state.positionTickers) {
    if (active.has(ticker)) continue;
    const seriesId = `position:${ticker}`;
    const bucket = state.positions[seriesId] || [];
    upsertByTime(bucket, { time, value: 0 });
    state.positions[seriesId] = bucket;
  }
  return true;
}

function applyTrade(state, data) {
  const time = pointTime(data);
  if (time == null) return false;
  state.trades.push({
    rowKey: data?.seq ?? `${time}-${state.trades.length}`,
    time,
    unixtime: time,
    ticker: asString(data?.ticker),
    direction: asString(data?.direction),
    action: asString(data?.action),
    label: asString(data?.label),
    price: data?.price ?? null,
    qty: data?.qty ?? null,
    deposit_ratio: data?.deposit_ratio ?? null,
    position_before_order: data?.position_before_order ?? null,
    position_after_order_filled: data?.position_after_order_filled ?? null,
    alpaca_order_id: asString(data?.alpaca_order_id),
    client_order_id: asString(data?.client_order_id),
    status: asString(data?.status),
    comment: asString(data?.comment) || asString(data?.reason),
  });
  return true;
}

function applyStatus(state, data) {
  if (!data || typeof data !== 'object') return false;
  state.status = {
    status: asString(data.status),
    message: asString(data.message),
    ticker: asString(data.ticker),
    base_scale: asString(data.base_scale),
  };
  return Boolean(state.status.status);
}

export function applyLiveStreamEvent(state, event) {
  const nextState = state || cloneEmptyState();
  const kind = asString(event?.kind);
  const data = event?.data && typeof event.data === 'object' ? event.data : {};
  const result = { changed: false, tradesChanged: false, statusChanged: false };

  if (kind === 'snapshot') {
    const fresh = cloneEmptyState();
    const seriesRows = Array.isArray(data.series) ? data.series : [];
    for (const row of seriesRows) rememberSeries(fresh, seriesMetaFromData(row, asString(row?.source)));
    for (const row of Array.isArray(data.bars) ? data.bars : []) applyBar(fresh, row);
    for (const row of Array.isArray(data.indicators) ? data.indicators : []) applyIndicator(fresh, row);
    for (const row of Array.isArray(data.positions) ? data.positions : []) applyPosition(fresh, row);
    for (const row of Array.isArray(data.trades) ? data.trades : []) applyTrade(fresh, row);
    if (data.status) applyStatus(fresh, data.status);
    Object.assign(nextState, fresh);
    result.changed =
      Object.keys(fresh.bars).length > 0 ||
      Object.keys(fresh.indicators).length > 0 ||
      Object.keys(fresh.positions).length > 0;
    result.tradesChanged = true;
    result.statusChanged = Boolean(fresh.status?.status);
    return result;
  }

  if (kind === 'bar') {
    result.changed = applyBar(nextState, data);
  } else if (kind === 'indicator') {
    result.changed = applyIndicator(nextState, data);
  } else if (kind === 'position') {
    result.changed = applyPosition(nextState, data);
  } else if (kind === 'trade') {
    result.tradesChanged = applyTrade(nextState, { ...data, seq: event?.seq });
    result.changed = result.tradesChanged;
  } else if (kind === 'status') {
    result.statusChanged = applyStatus(nextState, data);
  }
  return result;
}

function markerForTrade(t) {
  const buy = String(t.direction || '').toLowerCase() === 'buy';
  const label = [t.direction, t.status].filter(Boolean).join(' ');
  return {
    time: t.time,
    position: buy ? 'belowBar' : 'aboveBar',
    color: buy ? '#26a69a' : '#ef5350',
    shape: buy ? 'arrowUp' : 'arrowDown',
    text: label || 'order',
  };
}

function seriesLabel(state, seriesId, fallback) {
  const meta = state.series[seriesId] || {};
  return meta.label || fallback || seriesId;
}

function buildOhlcvCharts(state) {
  const priceSeries = [];
  for (const [seriesId, bucket] of Object.entries(state.bars)) {
    const meta = state.series[seriesId] || {};
    const ticker = meta.ticker || '';
    const matchingTrades = state.trades.filter((t) => !ticker || !t.ticker || t.ticker === ticker);
    priceSeries.push({
      type: 'Candlestick',
      label: seriesLabel(state, seriesId, 'Price'),
      data: bucket.data || [],
      markers: matchingTrades.map(markerForTrade),
    });
  }
  const charts = [];
  if (priceSeries.length > 0) {
    charts.push({
      type: 'lightweight-charts',
      title: 'Live Price',
      series: priceSeries,
    });
  }
  return charts;
}

function buildPositionCharts(state) {
  const series = Object.entries(state.positions)
    .sort(([a], [b]) => a.localeCompare(b))
    .map(([seriesId, data]) => ({
      type: 'Line',
      label: seriesLabel(state, seriesId, seriesId),
      data,
    }))
    .filter((row) => row.data.length > 0);
  if (series.length === 0) return [];
  return [
    {
      type: 'lightweight-charts',
      title: 'Current position value',
      series,
    },
  ];
}

function buildIndicatorCharts(state) {
  const grouped = {};
  for (const [seriesId, data] of Object.entries(state.indicators)) {
    const meta = state.series[seriesId] || {};
    const chartId = meta.chart_id || (meta.source === 'output_indicator' ? 'output_indicators' : 'input_indicators');
    if (!grouped[chartId]) grouped[chartId] = [];
    grouped[chartId].push({
      type: 'Line',
      label: seriesLabel(state, seriesId, seriesId),
      data,
    });
  }
  const charts = [];
  if (grouped.input_indicators?.length) {
    charts.push({
      type: 'lightweight-charts',
      title: 'Live Input Indicators',
      series: grouped.input_indicators,
    });
  }
  if (grouped.output_indicators?.length) {
    charts.push({
      type: 'lightweight-charts',
      title: 'Live Output Indicators',
      series: grouped.output_indicators,
    });
  }
  for (const [chartId, series] of Object.entries(grouped)) {
    if (chartId === 'input_indicators' || chartId === 'output_indicators') continue;
    charts.push({
      type: 'lightweight-charts',
      title: chartId.replace(/_/g, ' '),
      series,
    });
  }
  return charts;
}

function outputCatalog(state) {
  return Object.values(state.series)
    .filter((row) => row.source === 'output_indicator' && row.name)
    .map((row) => ({ name: row.name, description: row.description || '' }));
}

export function liveChartsDataJson(state) {
  const s = state || EMPTY_STATE;
  return {
    indicator_series_catalog: outputCatalog(s),
    charts: [...buildOhlcvCharts(s), ...buildPositionCharts(s), ...buildIndicatorCharts(s)],
  };
}

export function liveTrades(state) {
  return [...(state?.trades || [])];
}
