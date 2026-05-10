import { describe, it, expect } from 'vitest';
import {
  validateChartOrder,
  reorderChartPanels,
  reorderTableColumns,
  validateTableColumnOrder,
  moveTableTimeColumnFirst,
  sanitizeDetailsOpenState,
} from './chartOrder.js';

describe('validateChartOrder', () => {
  it('accepts only length-n permutations of 0..n-1', () => {
    expect(validateChartOrder([2, 0, 1], 3)).toBe(true);
    expect(validateChartOrder([0], 1)).toBe(true);
    expect(validateChartOrder([0, 0], 2)).toBe(false);
    expect(validateChartOrder([0, 2], 2)).toBe(false);
    expect(validateChartOrder([0, 1], 3)).toBe(false);
    expect(validateChartOrder(null, 1)).toBe(false);
  });
});

describe('reorderChartPanels', () => {
  it('inserts dragSrc immediately before dropBeforeSrc', () => {
    expect(reorderChartPanels([0, 1, 2], 2, 0)).toEqual([2, 0, 1]);
  });
});

describe('reorderTableColumns', () => {
  it('moves dragged columns before or after the drop column', () => {
    expect(reorderTableColumns(['date', 'pnl', 'ticker'], 'ticker', 'date')).toEqual(['ticker', 'date', 'pnl']);
    expect(reorderTableColumns(['date', 'pnl', 'ticker'], 'date', 'ticker', 'after')).toEqual(['pnl', 'ticker', 'date']);
  });
});

describe('validateTableColumnOrder', () => {
  it('accepts only permutations of the table columns', () => {
    expect(validateTableColumnOrder(['pnl', 'date'], ['date', 'pnl'])).toBe(true);
    expect(validateTableColumnOrder(['date', 'date'], ['date', 'pnl'])).toBe(false);
    expect(validateTableColumnOrder(['date'], ['date', 'pnl'])).toBe(false);
    expect(validateTableColumnOrder(['date', 'ticker'], ['date', 'pnl'])).toBe(false);
  });
});

describe('moveTableTimeColumnFirst', () => {
  it('moves the time column to the front when present', () => {
    expect(moveTableTimeColumnFirst(['ticker', 'direction', 'time', 'price'])).toEqual(['time', 'ticker', 'direction', 'price']);
    expect(moveTableTimeColumnFirst(['time', 'ticker'])).toEqual(['time', 'ticker']);
    expect(moveTableTimeColumnFirst(['entry_time', 'ticker'])).toEqual(['entry_time', 'ticker']);
  });
});

describe('sanitizeDetailsOpenState', () => {
  it('keeps only valid c* keys and optional metrics when allowed', () => {
    expect(
      sanitizeDetailsOpenState({ c0: false, c1: true, c9: false, metrics: false, x: 1 }, 2, true),
    ).toEqual({ c0: false, c1: true, metrics: false });
    expect(sanitizeDetailsOpenState({ c0: false, metrics: true }, 1, false)).toEqual({ c0: false });
    expect(sanitizeDetailsOpenState('{"c0":true}', 1, false)).toEqual({ c0: true });
  });
});
