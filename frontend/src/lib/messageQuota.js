export function normalizeMessageQuota(value) {
  if (!value || typeof value !== 'object') return null;
  const limit = Number(value.limit);
  const used = Number(value.used);
  const remaining = Number(value.remaining);
  if (!Number.isInteger(limit) || limit < 1 || !Number.isFinite(used) || !Number.isFinite(remaining)) {
    return null;
  }
  return {
    limit,
    used: Math.max(0, Math.trunc(used)),
    remaining: Math.max(0, Math.trunc(remaining)),
    window_hours: Number(value.window_hours) || 5,
    bucket_seconds: Number(value.bucket_seconds) || 3600,
    retry_at: typeof value.retry_at === 'string' && value.retry_at ? value.retry_at : null,
    retry_after_seconds: Math.max(0, Math.trunc(Number(value.retry_after_seconds) || 0)),
  };
}

export function messageQuotaCountdownSeconds(quota, nowMs = Date.now()) {
  if (!quota?.retry_at) return Math.max(0, Number(quota?.retry_after_seconds) || 0);
  const retryAtMs = Date.parse(quota.retry_at);
  if (!Number.isFinite(retryAtMs)) return Math.max(0, Number(quota?.retry_after_seconds) || 0);
  return Math.max(0, Math.ceil((retryAtMs - nowMs) / 1000));
}

export function formatMessageQuotaCountdown(totalSeconds) {
  const total = Math.max(0, Math.trunc(Number(totalSeconds) || 0));
  const hours = Math.floor(total / 3600);
  const minutes = Math.floor((total % 3600) / 60);
  const seconds = total % 60;
  if (hours > 0) return `${hours}h ${String(minutes).padStart(2, '0')}m ${String(seconds).padStart(2, '0')}s`;
  return `${minutes}m ${String(seconds).padStart(2, '0')}s`;
}
