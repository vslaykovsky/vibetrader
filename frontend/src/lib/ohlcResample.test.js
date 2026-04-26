import { describe, expect, it } from 'vitest';
import {
  bucketStart,
  mondayWeekBucketUTC,
  resampleOhlc,
  resampleEquity,
  isDisplayTfFinerThanSource,
  normalizeTimeframe,
  coarserChartTimeframes,
  finerChartTimeframes,
  equityOnCandleCloseTimes,
} from './ohlcResample.js';

describe('ohlcResample', () => {
  it('aggregates daily bars to weekly buckets (UTC Monday)', () => {
    const wed = 1704067200;
    const bars = [
      {
        unixtime: wed,
        ohlc: { open: 10, high: 12, low: 9, close: 11, volume: 100 },
      },
      {
        unixtime: wed + 86400,
        ohlc: { open: 11, high: 13, low: 10, close: 12, volume: 200 },
      },
    ];
    const out = resampleOhlc(bars, '1w');
    expect(out.length).toBe(1);
    expect(out[0].time).toBe(mondayWeekBucketUTC(wed));
    expect(out[0].open).toBe(10);
    expect(out[0].close).toBe(12);
    expect(out[0].high).toBe(13);
    expect(out[0].low).toBe(9);
    expect(out[0].volume).toBe(300);
  });

  it('resamples equity to last value in bucket', () => {
    const t0 = 1_700_006_400;
    const pts = [
      { unixtime: t0, equity: 100 },
      { unixtime: t0 + 3600, equity: 101 },
      { unixtime: t0 + 7200, equity: 102 },
    ];
    const out = resampleEquity(pts, '4h');
    expect(out.length).toBeGreaterThanOrEqual(1);
    const bucket = bucketStart(t0, '4h');
    const row = out.find((r) => r.time === bucket);
    expect(row).toBeDefined();
    expect(row.value).toBe(102);
  });

  it('detects finer display TF than source', () => {
    expect(isDisplayTfFinerThanSource('1d', '1h')).toBe(true);
    expect(isDisplayTfFinerThanSource('1h', '1d')).toBe(false);
  });

  it('normalizes timeframe strings from API', () => {
    expect(normalizeTimeframe(' 1D ')).toBe('1d');
    expect(normalizeTimeframe('4H')).toBe('4h');
    expect(normalizeTimeframe('bad')).toBe('');
  });

  it('lists only coarser chart TFs than daily source', () => {
    expect(coarserChartTimeframes('1d')).toEqual(['1w']);
    expect(coarserChartTimeframes('1w')).toEqual([]);
  });

  it('lists finer chart TFs than daily source (4h before 1m)', () => {
    expect(finerChartTimeframes('1d')).toEqual(['4h', '1h', '15m', '1m']);
  });

  it('aligns equity to fine candle times (step from last daily snapshot)', () => {
    const candles = [{ time: 100 }, { time: 200 }, { time: 300 }];
    const eq = [
      { unixtime: 50, equity: 1000 },
      { unixtime: 150, equity: 1010 },
      { unixtime: 250, equity: 1020 },
    ];
    const out = equityOnCandleCloseTimes(candles, eq);
    expect(out).toEqual([
      { time: 100, value: 1000 },
      { time: 200, value: 1010 },
      { time: 300, value: 1020 },
    ]);
  });
});
