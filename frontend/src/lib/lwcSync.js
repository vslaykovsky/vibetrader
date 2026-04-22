import { MismatchDirection } from 'lightweight-charts';

const SYNC_SUPPRESS_MS = 80;

function nowMs() {
  if (typeof performance !== 'undefined' && typeof performance.now === 'function') {
    return performance.now();
  }
  return Date.now();
}

/**
 * Keep horizontal zoom/pan aligned across multiple charts.
 *
 * Prefer syncing by visible time range (works even when charts have different
 * numbers of bars, e.g. Renko vs time-series). Fall back to logical range if
 * the time-range API isn't available.
 *
 * To avoid feedback loops where chart A -> setRange(B) -> B snaps to slightly
 * different range -> B emits event -> loop, we briefly suppress range-change
 * events on any chart we just wrote to programmatically.
 *
 * @param {import('lightweight-charts').IChartApi[]} charts
 * @returns {(() => void) | undefined}
 */
export function attachSyncedTimeScales(charts) {
  if (charts.length < 2) {
    return undefined;
  }

  const suppressUntil = new WeakMap();

  const isSuppressed = (chart) => {
    const until = suppressUntil.get(chart) || 0;
    return nowMs() < until;
  };

  const suppress = (chart) => {
    suppressUntil.set(chart, nowMs() + SYNC_SUPPRESS_MS);
  };

  const subscriptions = charts.map((chart) => {
    const timeScale = chart.timeScale();
    const hasTimeRangeApi =
      typeof timeScale.subscribeVisibleTimeRangeChange === 'function' &&
      typeof timeScale.unsubscribeVisibleTimeRangeChange === 'function' &&
      typeof timeScale.setVisibleRange === 'function';

    if (hasTimeRangeApi) {
      const handler = (timeRange) => {
        if (timeRange === null) {
          return;
        }
        if (isSuppressed(chart)) {
          return;
        }
        for (const other of charts) {
          if (other === chart) continue;
          suppress(other);
          try {
            other.timeScale().setVisibleRange(timeRange);
          } catch {
            /* ignore */
          }
        }
      };
      timeScale.subscribeVisibleTimeRangeChange(handler);
      return { chart, handler, kind: 'timeRange' };
    }

    const handler = (logicalRange) => {
      if (logicalRange === null) {
        return;
      }
      if (isSuppressed(chart)) {
        return;
      }
      for (const other of charts) {
        if (other === chart) continue;
        suppress(other);
        try {
          other.timeScale().setVisibleLogicalRange(logicalRange);
        } catch {
          /* ignore */
        }
      }
    };
    timeScale.subscribeVisibleLogicalRangeChange(handler);
    return { chart, handler, kind: 'logicalRange' };
  });

  return () => {
    for (const { chart, handler, kind } of subscriptions) {
      const ts = chart.timeScale();
      if (kind === 'timeRange') {
        ts.unsubscribeVisibleTimeRangeChange(handler);
      } else {
        ts.unsubscribeVisibleLogicalRangeChange(handler);
      }
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
      if (param.time == null) {
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
