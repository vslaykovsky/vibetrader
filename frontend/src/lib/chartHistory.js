/**
 * Max visible bar-width (logical span) when zooming out in SimulationCharts, and legacy cap for
 * trimming very large in-memory series if reintroduced.
 */
export const MAX_CHART_DISPLAY_BARS = 1000;

/**
 * Coerce LWC visible time range point (UTCTimestamp or BusinessDay) to unix seconds.
 * @param {unknown} t
 * @returns {number | null}
 */
export function coerceChartTimeSeconds(t) {
  if (typeof t === 'number' && Number.isFinite(t)) return t;
  if (typeof t === 'string' && t.trim()) {
    const ms = Date.parse(t.includes('T') ? t : `${t}T00:00:00Z`);
    if (Number.isFinite(ms)) return Math.floor(ms / 1000);
  }
  if (t && typeof t === 'object' && 'year' in t && 'month' in t && 'day' in t) {
    const y = Number(t.year);
    const m = Number(t.month);
    const d = Number(t.day);
    if (![y, m, d].every((x) => Number.isFinite(x))) return null;
    return Math.floor(Date.UTC(y, m - 1, d) / 1000);
  }
  return null;
}

/**
 * @param {unknown} from
 * @param {unknown} to
 * @returns {{ from: number; to: number } | null}
 */
export function visibleTimeRangeToUnix(from, to) {
  const a = coerceChartTimeSeconds(from);
  const b = coerceChartTimeSeconds(to);
  if (a == null || b == null) return null;
  return { from: Math.min(a, b), to: Math.max(a, b) };
}

/**
 * Keep at most ``maxBars`` bars closest to ``centerUnix`` (sim start / visible center), then sort by time.
 * @template {{ unixtime: number }} T
 * @param {T[]} bars
 * @param {number} centerUnix
 * @param {number} maxBars
 * @returns {T[]}
 */
export function capBarsAroundCenter(bars, centerUnix, maxBars) {
  if (!bars.length || bars.length <= maxBars) return bars;
  const c = Number.isFinite(centerUnix) ? centerUnix : bars[Math.floor(bars.length / 2)].unixtime;
  const scored = bars.map((b) => ({ b, d: Math.abs(b.unixtime - c) }));
  scored.sort((x, y) => x.d - y.d || x.b.unixtime - y.b.unixtime);
  const kept = scored.slice(0, maxBars).map((x) => x.b);
  kept.sort((a, b) => a.unixtime - b.unixtime);
  return kept;
}

/**
 * When over ``maxBars``, keep bars nearest the visible interval (fallback: center of range).
 * @template {{ time: number }} T
 * @param {T[]} candles
 * @param {{ from: number; to: number } | null} visible
 * @param {number} maxBars
 * @param {number} [fallbackCenterUnix]
 * @returns {T[]}
 */
export function capCandlesForDisplay(candles, visible, maxBars, fallbackCenterUnix = 0) {
  if (!candles.length || candles.length <= maxBars) return candles;
  let center = fallbackCenterUnix;
  if (visible && Number.isFinite(visible.from) && Number.isFinite(visible.to)) {
    center = (visible.from + visible.to) / 2;
  } else if (!center) {
    center = candles[Math.floor(candles.length / 2)].time;
  }
  const scored = candles.map((row) => ({ row, d: Math.abs(row.time - center) }));
  scored.sort((x, y) => x.d - y.d || x.row.time - y.row.time);
  const kept = scored.slice(0, maxBars).map((x) => x.row);
  kept.sort((a, b) => a.time - b.time);
  return kept;
}
