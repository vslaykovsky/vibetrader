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

export function normalizeHourFormat(value, fallback = 'auto') {
  const fmt = String(value || '').trim().toLowerCase();
  return fmt === 'auto' || fmt === '12h' || fmt === '24h' ? fmt : fallback;
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

function browserHourOptions(hourFormat = 'auto') {
  const fmt = normalizeHourFormat(hourFormat);
  if (fmt === '12h') return { hour12: true };
  if (fmt === '24h') return { hour12: false, hourCycle: 'h23' };
  try {
    const opts = new Intl.DateTimeFormat(undefined, { hour: 'numeric' }).resolvedOptions();
    if (typeof opts.hourCycle === 'string' && opts.hourCycle) return { hourCycle: opts.hourCycle };
    if (typeof opts.hour12 === 'boolean') return { hour12: opts.hour12 };
  } catch {
    return {};
  }
  return {};
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

export function formatIsoDateTime(value, timeZone, hourFormat = 'auto') {
  const ms = parseIsoInstant(value);
  if (ms == null) return typeof value === 'string' ? value : '';
  return formatMsDateTime(ms, timeZone, hourFormat);
}

export function formatUnixDateTime(value, timeZone, hourFormat = 'auto') {
  if (value == null || !Number.isFinite(Number(value))) return '—';
  const n = Number(value);
  return formatMsDateTime(n > 2e10 ? n : n * 1000, timeZone, hourFormat);
}

export function formatMsDateTime(ms, timeZone, hourFormat = 'auto') {
  if (!Number.isFinite(ms)) return '';
  return new Intl.DateTimeFormat('en-US', {
    timeZone: normalizeTimeZone(timeZone),
    year: 'numeric',
    month: 'short',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
    ...browserHourOptions(hourFormat),
  }).format(new Date(ms));
}

function formatMsDate(ms, timeZone) {
  if (!Number.isFinite(ms)) return '';
  return new Intl.DateTimeFormat('en-US', {
    timeZone: normalizeTimeZone(timeZone),
    year: 'numeric',
    month: 'short',
    day: '2-digit',
  }).format(new Date(ms));
}

function businessDayFromChartTime(value) {
  if (typeof value === 'string') {
    const m = value.trim().match(/^(\d{4})-(\d{2})-(\d{2})$/);
    if (!m) return null;
    return { year: Number(m[1]), month: Number(m[2]), day: Number(m[3]) };
  }
  if (value && typeof value === 'object' && 'year' in value && 'month' in value && 'day' in value) {
    const year = Number(value.year);
    const month = Number(value.month);
    const day = Number(value.day);
    if ([year, month, day].every((n) => Number.isFinite(n))) {
      return { year, month, day };
    }
  }
  return null;
}

function businessDayDate(day) {
  return new Date(Date.UTC(day.year, day.month - 1, day.day));
}

function chartTimeSeconds(value) {
  if (typeof value === 'number') return value;
  if (value && typeof value === 'object' && 'timestamp' in value) return Number(value.timestamp);
  if (typeof value === 'string' && value.trim()) {
    const t = value.trim().replace(' ', 'T');
    const ms = Date.parse(/[zZ]|[+-]\d{2}:?\d{2}$/.test(t) ? t : `${t}Z`);
    return Number.isFinite(ms) ? Math.floor(ms / 1000) : NaN;
  }
  return NaN;
}

function isMidnightParts(parts) {
  const hh = parts.hour || '';
  const mm = parts.minute || '00';
  if (mm !== '00') return false;
  if (parts.dayPeriod) {
    const h = Number(hh);
    return (h === 0 || h === 12) && parts.dayPeriod.toLowerCase() === 'am';
  }
  return hh === '00' || hh === '24';
}

function formatTimeParts(parts) {
  const mm = parts.minute || '00';
  if (parts.dayPeriod) {
    const h = Number(parts.hour);
    const hh = Number.isFinite(h) ? String(h) : (parts.hour || '');
    return `${hh}:${mm} ${parts.dayPeriod}`;
  }
  const hh = parts.hour === '24' ? '00' : (parts.hour || '');
  return `${hh}:${mm}`;
}

export function formatChartTick(timeSec, timeZone, isIntraday, hourFormat = 'auto') {
  const businessDay = businessDayFromChartTime(timeSec);
  if (businessDay && !isIntraday) {
    const p = partsFor(businessDayDate(businessDay), 'UTC', {
      month: 'short',
      day: '2-digit',
    });
    return `${p.day || ''} ${p.month || ''}`.trim();
  }
  const u = chartTimeSeconds(timeSec);
  if (!Number.isFinite(u)) return '';
  const p = partsFor(new Date(u * 1000), timeZone, {
    month: 'short',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    ...browserHourOptions(hourFormat),
  });
  const dayMonth = `${p.day || ''} ${p.month || ''}`.trim();
  if (!isIntraday) return dayMonth;
  if (isMidnightParts(p)) return dayMonth;
  return formatTimeParts(p);
}

export function formatChartCrosshairTime(time, timeZone, isIntraday, hourFormat = 'auto') {
  const businessDay = businessDayFromChartTime(time);
  if (businessDay && !isIntraday) {
    return new Intl.DateTimeFormat('en-US', {
      timeZone: 'UTC',
      year: 'numeric',
      month: 'short',
      day: '2-digit',
    }).format(businessDayDate(businessDay));
  }
  const u = chartTimeSeconds(time);
  if (!Number.isFinite(u)) return '';
  return isIntraday ? formatMsDateTime(u * 1000, timeZone, hourFormat) : formatMsDate(u * 1000, timeZone);
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
