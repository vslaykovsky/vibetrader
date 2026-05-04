const EMPTY_STATE = {
  series: {},
  bars: {},
  indicators: {},
  equity: [],
  positions: {},
  positionTickers: new Set(),
  trades: [],
  annotations: [],
  status: null,
};

function cloneEmptyState() {
  return {
    series: {},
    bars: {},
    indicators: {},
    equity: [],
    positions: {},
    positionTickers: new Set(),
    trades: [],
    annotations: [],
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

function asOptionalNumber(v) {
  if (v == null || v === '') return null;
  return asNumber(v);
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
  const equity = asOptionalNumber(data?.equity);
  if (equity != null) {
    upsertByTime(state.equity, { time, value: equity });
  }
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

function mergeTradeComment(prevComment, nextComment) {
  const prev = asString(prevComment).trim();
  const next = asString(nextComment).trim();
  if (!prev) return next;
  if (!next) return prev;
  if (prev === next || prev.includes(next)) return prev;
  if (next.includes(prev)) return next;
  return `${prev}; ${next}`;
}

function tradeValueUsd(data) {
  const value = asOptionalNumber(data?.value_usd);
  if (value != null) return Math.abs(value);
  const notional = asOptionalNumber(data?.notional);
  if (notional != null) return Math.abs(notional);
  const price = asOptionalNumber(data?.price);
  const qty = asOptionalNumber(data?.qty);
  if (price != null && qty != null) return Math.abs(price * qty);
  const filledAvgPrice = asOptionalNumber(data?.filled_avg_price);
  const filledQty = asOptionalNumber(data?.filled_qty);
  if (filledAvgPrice != null && filledQty != null) return Math.abs(filledAvgPrice * filledQty);
  return null;
}

function applyTrade(state, data) {
  const time = pointTime(data);
  if (time == null) return false;
  const next = {
    rowKey: data?.seq ?? `${time}-${state.trades.length}`,
    time,
    unixtime: time,
    ticker: asString(data?.ticker),
    direction: asString(data?.direction),
    action: asString(data?.action),
    label: asString(data?.label),
    price: data?.price ?? null,
    qty: data?.qty ?? null,
    value_usd: tradeValueUsd(data),
    deposit_ratio: data?.deposit_ratio ?? null,
    position_before_order: data?.position_before_order ?? null,
    position_after_order_filled: data?.position_after_order_filled ?? null,
    alpaca_order_id: asString(data?.alpaca_order_id),
    client_order_id: asString(data?.client_order_id),
    status: asString(data?.status),
    comment: asString(data?.comment) || asString(data?.reason),
  };
  const cid = next.client_order_id;
  const aid = next.alpaca_order_id;
  const idx =
    cid || aid
      ? state.trades.findIndex(
          (row) =>
            (cid && row.client_order_id === cid) ||
            (aid && row.alpaca_order_id === aid),
        )
      : -1;
  if (idx >= 0) {
    const prev = state.trades[idx];
    state.trades[idx] = {
      ...prev,
      ticker: next.ticker || prev.ticker,
      direction: next.direction || prev.direction,
      action: next.action || prev.action,
      label: next.label || prev.label,
      price: next.price ?? prev.price,
      qty: next.qty ?? prev.qty,
      value_usd: next.value_usd ?? prev.value_usd,
      deposit_ratio: next.deposit_ratio ?? prev.deposit_ratio,
      position_before_order: next.position_before_order ?? prev.position_before_order,
      position_after_order_filled: next.position_after_order_filled ?? prev.position_after_order_filled,
      alpaca_order_id: next.alpaca_order_id || prev.alpaca_order_id,
      client_order_id: next.client_order_id || prev.client_order_id,
      status: next.status || prev.status,
      comment: mergeTradeComment(prev.comment, next.comment),
    };
  } else {
    state.trades.push(next);
  }
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

function applyAnnotation(state, data) {
  const time = pointTime(data);
  const kind = asString(data?.kind) || 'live_start';
  if (time == null) return false;
  const next = {
    time,
    kind,
    label: asString(data?.label) || (kind === 'live_start' ? 'Live trading starts' : kind),
  };
  const idx = state.annotations.findIndex((row) => row.time === time && row.kind === kind);
  if (idx >= 0) {
    state.annotations[idx] = next;
  } else {
    state.annotations.push(next);
    state.annotations.sort((a, b) => a.time - b.time || a.kind.localeCompare(b.kind));
  }
  return true;
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
    for (const row of Array.isArray(data.annotations) ? data.annotations : []) applyAnnotation(fresh, row);
    if (data.status) applyStatus(fresh, data.status);
    Object.assign(nextState, fresh);
    result.changed =
      Object.keys(fresh.bars).length > 0 ||
      Object.keys(fresh.indicators).length > 0 ||
      fresh.equity.length > 0 ||
      Object.keys(fresh.positions).length > 0 ||
      fresh.annotations.length > 0;
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
  } else if (kind === 'annotation') {
    result.changed = applyAnnotation(nextState, data);
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

function annotationMarkers(state) {
  return [...(state.annotations || [])].map((row) => ({
    time: row.time,
    label: row.label,
    kind: row.kind,
    color: row.kind === 'live_start' ? '#f59e0b' : '#94a3b8',
  }));
}

function withAnnotationTimes(data, annotations) {
  const rows = Array.isArray(data) ? [...data] : [];
  for (const marker of annotations) {
    if (marker.time == null || rows.some((row) => row?.time === marker.time)) continue;
    rows.push({ time: marker.time });
  }
  rows.sort((a, b) => Number(a.time) - Number(b.time));
  return rows;
}

function buildOhlcvCharts(state) {
  const priceSeries = [];
  const annotations = annotationMarkers(state);
  for (const [seriesId, bucket] of Object.entries(state.bars)) {
    const meta = state.series[seriesId] || {};
    const ticker = meta.ticker || '';
    const matchingTrades = state.trades.filter((t) => !ticker || !t.ticker || t.ticker === ticker);
    priceSeries.push({
      type: 'Candlestick',
      label: seriesLabel(state, seriesId, 'Price'),
      data: withAnnotationTimes(bucket.data || [], annotations),
      markers: matchingTrades.map(markerForTrade),
    });
  }
  const charts = [];
  if (priceSeries.length > 0) {
    charts.push({
      type: 'lightweight-charts',
      title: 'Live Price',
      series: priceSeries,
      verticalMarkers: annotations,
    });
  }
  return charts;
}

function buildPositionCharts(state) {
  const annotations = annotationMarkers(state);
  const series = Object.entries(state.positions)
    .sort(([a], [b]) => a.localeCompare(b))
    .map(([seriesId, data]) => ({
      type: 'Line',
      label: seriesLabel(state, seriesId, seriesId),
      data: withAnnotationTimes(data, annotations),
    }))
    .filter((row) => row.data.length > 0);
  if (series.length === 0) return [];
  return [
    {
      type: 'lightweight-charts',
      title: 'Current position value',
      series,
      verticalMarkers: annotations,
    },
  ];
}

function buildEquityCharts(state) {
  const annotations = annotationMarkers(state);
  const equity = state.equity || [];
  if (equity.length === 0) return [];
  const data = withAnnotationTimes(equity, annotations);
  return [
    {
      type: 'lightweight-charts',
      title: 'Equity curve',
      series: [
        {
          type: 'Line',
          label: 'Strategy equity',
          options: { color: '#2962ff', lineWidth: 2 },
          data,
          markers: state.trades.map(markerForTrade),
        },
      ],
      verticalMarkers: annotations,
    },
  ];
}

function buildIndicatorCharts(state) {
  const grouped = {};
  const annotations = annotationMarkers(state);
  for (const [seriesId, data] of Object.entries(state.indicators)) {
    const meta = state.series[seriesId] || {};
    const chartId = meta.chart_id || (meta.source === 'output_indicator' ? 'output_indicators' : 'input_indicators');
    if (!grouped[chartId]) grouped[chartId] = [];
    grouped[chartId].push({
      type: 'Line',
      label: seriesLabel(state, seriesId, seriesId),
      data: withAnnotationTimes(data, annotations),
    });
  }
  const charts = [];
  if (grouped.input_indicators?.length) {
    charts.push({
      type: 'lightweight-charts',
      title: 'Live Input Indicators',
      series: grouped.input_indicators,
      verticalMarkers: annotations,
    });
  }
  if (grouped.output_indicators?.length) {
    charts.push({
      type: 'lightweight-charts',
      title: 'Live Output Indicators',
      series: grouped.output_indicators,
      verticalMarkers: annotations,
    });
  }
  for (const [chartId, series] of Object.entries(grouped)) {
    if (chartId === 'input_indicators' || chartId === 'output_indicators') continue;
    charts.push({
      type: 'lightweight-charts',
      title: chartId.replace(/_/g, ' '),
      series,
      verticalMarkers: annotations,
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
    charts: [...buildOhlcvCharts(s), ...buildEquityCharts(s), ...buildPositionCharts(s), ...buildIndicatorCharts(s)],
  };
}

export function liveTrades(state) {
  return [...(state?.trades || [])];
}
