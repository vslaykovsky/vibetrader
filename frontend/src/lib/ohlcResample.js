/** Finer timeframes have lower rank (1m = 0). */
const TF_RANK = {
  '1m': 0,
  '15m': 1,
  '1h': 2,
  '4h': 3,
  '1d': 4,
  '1w': 5,
};

const TF_SECONDS = {
  '1m': 60,
  '15m': 15 * 60,
  '1h': 3600,
  '4h': 4 * 3600,
  '1d': 86400,
};

export const DISPLAY_TF_OPTIONS = ['1m', '15m', '1h', '4h', '1d', '1w'];

/** Lowercase trim for API / params (`1D`, ` 4H ` → `1d`, `4h`). */
export function normalizeTimeframe(tf) {
  if (tf == null) return '';
  const k = String(tf).trim().toLowerCase();
  return Object.prototype.hasOwnProperty.call(TF_RANK, k) ? k : '';
}

/**
 * @param {string} tf
 * @returns {number | null}
 */
export function timeframeRank(tf) {
  const k = normalizeTimeframe(tf);
  return k ? TF_RANK[k] : null;
}

/**
 * Timeframes strictly coarser than ``sourceTf`` (higher rank), for chart-only aggregation.
 * @param {string} sourceTf
 * @returns {string[]}
 */
export function coarserChartTimeframes(sourceTf) {
  const rs = timeframeRank(sourceTf);
  if (rs == null) return [];
  return DISPLAY_TF_OPTIONS.filter((tf) => {
    const rt = timeframeRank(tf);
    return rt != null && rt > rs;
  });
}

/**
 * Timeframes strictly finer than ``sourceTf`` (lower rank), for chart-only (fetched market OHLC).
 * Ordered coarse → fine within this band (e.g. 4h, 1h, 15m, 1m for a daily strategy).
 * @param {string} sourceTf
 * @returns {string[]}
 */
export function finerChartTimeframes(sourceTf) {
  const rs = timeframeRank(sourceTf);
  if (rs == null) return [];
  return DISPLAY_TF_OPTIONS.filter((tf) => {
    const rt = timeframeRank(tf);
    return rt != null && rt < rs;
  }).sort((a, b) => timeframeRank(b) - timeframeRank(a));
}

/**
 * Whether target display TF is strictly finer than source (resample would invent bars).
 * @param {string} sourceTf
 * @param {string} targetTf
 */
export function isDisplayTfFinerThanSource(sourceTf, targetTf) {
  const a = timeframeRank(sourceTf);
  const b = timeframeRank(normalizeTimeframe(targetTf));
  if (a == null || b == null) return false;
  return b < a;
}

/** Monday 00:00:00 UTC for the week containing unix (seconds). */
export function mondayWeekBucketUTC(unix) {
  const d = new Date(unix * 1000);
  const day = d.getUTCDay();
  const daysFromMonday = (day + 6) % 7;
  d.setUTCDate(d.getUTCDate() - daysFromMonday);
  d.setUTCHours(0, 0, 0, 0);
  return Math.floor(d.getTime() / 1000);
}

/**
 * @param {number} unix
 * @param {string} tf
 */
export function bucketStart(unix, tf) {
  const k = normalizeTimeframe(tf);
  if (k === '1w') {
    return mondayWeekBucketUTC(unix);
  }
  const sec = TF_SECONDS[k];
  if (!sec) return unix;
  return Math.floor(unix / sec) * sec;
}

/**
 * @param {{ unixtime: number; ohlc: { open: number; high: number; low: number; close: number } }[]} bars
 * @param {string} targetTf
 * @returns {{ time: number; open: number; high: number; low: number; close: number }[]}
 */
export function resampleOhlc(bars, targetTf) {
  if (!Array.isArray(bars) || bars.length === 0) return [];
  const tf = normalizeTimeframe(targetTf);
  if (!tf) return [];
  const map = new Map();
  for (const b of bars) {
    const t = bucketStart(b.unixtime, tf);
    let row = map.get(t);
    if (!row) {
      row = {
        time: t,
        open: b.ohlc.open,
        high: b.ohlc.high,
        low: b.ohlc.low,
        close: b.ohlc.close,
      };
      map.set(t, row);
    } else {
      row.high = Math.max(row.high, b.ohlc.high);
      row.low = Math.min(row.low, b.ohlc.low);
      row.close = b.ohlc.close;
    }
  }
  return [...map.values()].sort((a, b) => a.time - b.time);
}

/**
 * @param {{ unixtime: number; equity: number }[]} points
 * @param {string} targetTf
 * @returns {{ time: number; value: number }[]}
 */
export function resampleEquity(points, targetTf) {
  if (!Array.isArray(points) || points.length === 0) return [];
  const tf = normalizeTimeframe(targetTf);
  if (!tf) return [];
  const map = new Map();
  for (const p of points) {
    const t = bucketStart(p.unixtime, tf);
    map.set(t, { time: t, value: p.equity });
  }
  return [...map.values()].sort((a, b) => a.time - b.time);
}

/**
 * @param {{ unixtime: number; direction?: string; price?: number; deposit_ratio?: number }[]} trades
 * @param {string} targetTf
 */
export function tradeMarkerTimes(trades, targetTf) {
  return (trades || []).map((tr) => ({
    ...tr,
    bucketTime: bucketStart(tr.unixtime, targetTf),
  }));
}

/**
 * For each candle ``time`` (e.g. 4h bar end), use the last strategy equity snapshot with ``unixtime <= time``.
 * Produces one line point per candle so the lower chart shares the fine time scale.
 *
 * @param {{ time: number }[]} candles ascending by time
 * @param {{ unixtime: number; equity: number }[]} equityRows strategy pnl events
 * @returns {{ time: number; value: number }[]}
 */
export function equityOnCandleCloseTimes(candles, equityRows) {
  if (!Array.isArray(candles) || candles.length === 0) return [];
  if (!Array.isArray(equityRows) || equityRows.length === 0) return [];
  const sorted = [...equityRows].sort((a, b) => a.unixtime - b.unixtime);
  let j = 0;
  let last = undefined;
  const out = [];
  for (const c of candles) {
    const t = c.time;
    while (j < sorted.length && sorted[j].unixtime <= t) {
      last = sorted[j].equity;
      j++;
    }
    if (last !== undefined && last !== null && Number.isFinite(last)) {
      out.push({ time: t, value: last });
    }
  }
  return out;
}
