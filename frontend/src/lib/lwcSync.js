import { MismatchDirection } from 'lightweight-charts';

/**
 * Keep horizontal zoom/pan aligned across multiple charts (logical bar indices).
 * @param {import('lightweight-charts').IChartApi[]} charts
 * @returns {(() => void) | undefined}
 */
export function attachSyncedTimeScales(charts) {
  if (charts.length < 2) {
    return undefined;
  }
  let syncing = false;
  const subscriptions = charts.map((chart) => {
    const handler = (logicalRange) => {
      if (syncing || logicalRange === null) {
        return;
      }
      syncing = true;
      try {
        for (const other of charts) {
          if (other !== chart) {
            other.timeScale().setVisibleLogicalRange(logicalRange);
          }
        }
      } finally {
        syncing = false;
      }
    };
    chart.timeScale().subscribeVisibleLogicalRangeChange(handler);
    return { chart, handler };
  });
  return () => {
    for (const { chart, handler } of subscriptions) {
      chart.timeScale().unsubscribeVisibleLogicalRangeChange(handler);
    }
  };
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

/**
 * @param {{ chart: import('lightweight-charts').IChartApi; series: import('lightweight-charts').ISeriesApi<any> | null }[]} bindings
 * @returns {(() => void) | undefined}
 */
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
