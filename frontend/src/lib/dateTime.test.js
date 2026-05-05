import { describe, expect, it } from 'vitest';
import { formatChartCrosshairTime, formatChartTick, parseIsoInstant } from './dateTime.js';

describe('parseIsoInstant', () => {
  it('parses timezone-less ISO timestamps as UTC instants', () => {
    expect(parseIsoInstant('2026-05-03T19:10:00')).toBe(Date.UTC(2026, 4, 3, 19, 10, 0));
  });
});

describe('formatChartCrosshairTime', () => {
  it('formats lightweight chart timestamps with the requested hour format', () => {
    const utcNoon = Date.UTC(2026, 4, 5, 12, 30, 0) / 1000;

    expect(formatChartCrosshairTime(utcNoon, 'Asia/Tbilisi', true, '24h')).toBe('May 05, 2026, 16:30:00');
    expect(formatChartCrosshairTime(utcNoon, 'Asia/Tbilisi', true, '12h')).toBe('May 05, 2026, 04:30:00 PM');
    expect(formatChartCrosshairTime({ year: 2026, month: 5, day: 5 }, 'Asia/Tbilisi', false)).toBe('May 05, 2026');
  });
});

describe('formatChartTick', () => {
  it('uses the requested hour format for intraday labels', () => {
    const utcNoon = Date.UTC(2026, 4, 5, 12, 30, 0) / 1000;

    expect(formatChartTick(utcNoon, 'Asia/Tbilisi', true, '24h')).toBe('16:30');
    expect(formatChartTick(utcNoon, 'Asia/Tbilisi', true, '12h')).toBe('4:30 PM');
  });
});
