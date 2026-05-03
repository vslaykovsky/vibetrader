function pad2(n) {
  return String(n).padStart(2, '0');
}

/** @param {Date} d */
export function toIsoDate(d) {
  return `${d.getFullYear()}-${pad2(d.getMonth() + 1)}-${pad2(d.getDate())}`;
}

/** @param {string | null | undefined} s */
export function parseIsoDate(s) {
  const t = String(s || '').trim();
  if (!/^\d{4}-\d{2}-\d{2}$/.test(t)) return null;
  const [y, m, d] = t.split('-').map(Number);
  const dt = new Date(y, m - 1, d);
  if (dt.getFullYear() !== y || dt.getMonth() !== m - 1 || dt.getDate() !== d) return null;
  return dt;
}

/** @param {Date} d */
export function startOfDay(d) {
  return new Date(d.getFullYear(), d.getMonth(), d.getDate());
}

/**
 * Unix seconds for ``YYYY-MM-DD`` at 12:00 UTC (simulation ``start_date`` temporal anchor).
 * @param {string} isoDate
 */
export function startNoonUnix(isoDate) {
  return Math.floor(new Date(`${isoDate}T12:00:00Z`).getTime() / 1000);
}
