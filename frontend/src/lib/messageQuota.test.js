import { describe, expect, it } from 'vitest';
import {
  formatMessageQuotaCountdown,
  messageQuotaCountdownSeconds,
  normalizeMessageQuota,
} from './messageQuota.js';

describe('message quota helpers', () => {
  it('normalizes the API payload', () => {
    expect(normalizeMessageQuota({
      limit: 5,
      used: 3,
      remaining: 2,
      window_hours: 5,
      bucket_seconds: 3600,
      retry_at: null,
      retry_after_seconds: 0,
    })).toEqual({
      limit: 5,
      used: 3,
      remaining: 2,
      window_hours: 5,
      bucket_seconds: 3600,
      retry_at: null,
      retry_after_seconds: 0,
    });
  });

  it('calculates a live countdown from retry_at', () => {
    const quota = { retry_at: '2026-07-19T17:00:00Z', retry_after_seconds: 1 };
    const now = Date.parse('2026-07-19T12:30:00Z');
    expect(messageQuotaCountdownSeconds(quota, now)).toBe(16200);
    expect(formatMessageQuotaCountdown(16200)).toBe('4h 30m 00s');
  });
});
