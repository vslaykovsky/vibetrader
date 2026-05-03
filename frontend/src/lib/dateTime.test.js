import { describe, expect, it } from 'vitest';
import { parseIsoInstant } from './dateTime.js';

describe('parseIsoInstant', () => {
  it('parses timezone-less ISO timestamps as UTC instants', () => {
    expect(parseIsoInstant('2026-05-03T19:10:00')).toBe(Date.UTC(2026, 4, 3, 19, 10, 0));
  });
});
