export function browserTimeZone() {
  try {
    const tz = Intl.DateTimeFormat().resolvedOptions().timeZone;
    return typeof tz === 'string' && tz ? tz : 'UTC';
  } catch {
    return 'UTC';
  }
}

export function isValidTimeZone(value) {
  const tz = String(value || '').trim();
  if (!tz) return false;
  try {
    new Intl.DateTimeFormat('en-US', { timeZone: tz }).format(new Date());
    return true;
  } catch {
    return false;
  }
}

export function normalizeTimeZone(value, fallback = browserTimeZone()) {
  const tz = String(value || '').trim();
  return isValidTimeZone(tz) ? tz : fallback;
}

/** Calendar date in UTC for API ``start_date`` / ``end_date`` bounds from unix seconds. */
export function unixToIsoDateUTC(sec) {
  const d = new Date(sec * 1000);
  const yyyy = d.getUTCFullYear();
  const mm = String(d.getUTCMonth() + 1).padStart(2, '0');
  const dd = String(d.getUTCDate()).padStart(2, '0');
  return `${yyyy}-${mm}-${dd}`;
}

export function parseIsoInstant(value) {
  if (typeof value !== 'string') return null;
  let t = value.trim();
  if (!t) return null;
  if (/^\d{4}-\d{2}-\d{2}T/.test(t) && !/[zZ]|[+-]\d{2}:\d{2}$/.test(t)) {
    t = `${t}Z`;
  }
  t = t.replace(/(\.\d{3})\d+([zZ]|[+-]\d{2}:\d{2})$/, '$1$2');
  const ms = Date.parse(t);
  return Number.isFinite(ms) ? ms : null;
}

function partsFor(date, timeZone, options = {}) {
  const fmt = new Intl.DateTimeFormat('en-US', {
    timeZone: normalizeTimeZone(timeZone),
    ...options,
  });
  return Object.fromEntries(fmt.formatToParts(date).map((p) => [p.type, p.value]));
}

export function dateKeyForMs(ms, timeZone) {
  if (!Number.isFinite(ms)) return null;
  const p = partsFor(new Date(ms), timeZone, {
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
  });
  if (!p.year || !p.month || !p.day) return null;
  return `${p.year}-${p.month}-${p.day}`;
}

export function dateKeyFromIso(value, timeZone) {
  const ms = parseIsoInstant(value);
  return ms == null ? null : dateKeyForMs(ms, timeZone);
}

export function todayDateKey(timeZone) {
  return dateKeyForMs(Date.now(), timeZone);
}

export function formatIsoDateTime(value, timeZone) {
  const ms = parseIsoInstant(value);
  if (ms == null) return typeof value === 'string' ? value : '';
  return formatMsDateTime(ms, timeZone);
}

export function formatUnixDateTime(value, timeZone) {
  if (value == null || !Number.isFinite(Number(value))) return '—';
  const n = Number(value);
  return formatMsDateTime(n > 2e10 ? n : n * 1000, timeZone);
}

export function formatMsDateTime(ms, timeZone) {
  if (!Number.isFinite(ms)) return '';
  return new Intl.DateTimeFormat('en-US', {
    timeZone: normalizeTimeZone(timeZone),
    year: 'numeric',
    month: 'short',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
    hour12: false,
    hourCycle: 'h23',
  }).format(new Date(ms));
}

export function formatChartTick(timeSec, timeZone, isIntraday) {
  const u = typeof timeSec === 'number'
    ? timeSec
    : (timeSec && typeof timeSec === 'object' && 'timestamp' in timeSec)
      ? Number(timeSec.timestamp)
      : NaN;
  if (!Number.isFinite(u)) return '';
  const p = partsFor(new Date(u * 1000), timeZone, {
    month: 'short',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
    hourCycle: 'h23',
  });
  const dayMonth = `${p.day || ''} ${p.month || ''}`.trim();
  if (!isIntraday) return dayMonth;
  const hh = p.hour === '24' ? '00' : p.hour;
  const mm = p.minute || '00';
  if (hh === '00' && mm === '00') return dayMonth;
  return `${hh}:${mm}`;
}

export function supportedTimeZones() {
  if (typeof Intl.supportedValuesOf === 'function') {
    try {
      return Intl.supportedValuesOf('timeZone');
    } catch {
      return [];
    }
  }
  return [];
}
