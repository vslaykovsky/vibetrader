export function validateChartOrder(order, n) {
  if (!Array.isArray(order) || order.length !== n || n < 0) return false;
  const set = new Set(order);
  if (set.size !== n) return false;
  for (let i = 0; i < n; i++) {
    if (!set.has(i)) return false;
  }
  return true;
}

export function reorderChartPanels(order, dragSrc, dropBeforeSrc) {
  if (!Array.isArray(order) || dragSrc === dropBeforeSrc) return order;
  const next = order.filter((x) => x !== dragSrc);
  const j = next.indexOf(dropBeforeSrc);
  if (j === -1) return order;
  next.splice(j, 0, dragSrc);
  return next;
}

export function chartsOrderSignature(charts) {
  const parts = charts.map((c) => [c.type, c.title ?? '']);
  const s = JSON.stringify(parts);
  let h = 5381;
  for (let i = 0; i < s.length; i++) {
    h = (h * 33) ^ s.charCodeAt(i);
  }
  return `${(h >>> 0).toString(36)}_${charts.length}`;
}

export function chartDetailsSignature(charts, metrics) {
  if (Array.isArray(charts) && charts.length > 0) {
    return chartsOrderSignature(charts);
  }
  const m = metrics;
  if (m && typeof m === 'object' && !Array.isArray(m)) {
    const keys = Object.keys(m).sort();
    const s = keys.join('\0');
    let h = 5381;
    for (let i = 0; i < s.length; i++) {
      h = (h * 33) ^ s.charCodeAt(i);
    }
    return `mx_${(h >>> 0).toString(36)}_${keys.length}`;
  }
  return 'empty';
}

export function sanitizeDetailsOpenState(raw, chartCount, includeMetrics) {
  let obj = raw;
  if (typeof raw === 'string') {
    try {
      obj = JSON.parse(raw);
    } catch {
      return {};
    }
  }
  if (!obj || typeof obj !== 'object' || Array.isArray(obj)) return {};
  const out = {};
  for (let i = 0; i < chartCount; i++) {
    const k = `c${i}`;
    if (obj[k] === true || obj[k] === false) {
      out[k] = obj[k];
    }
  }
  if (includeMetrics && (obj.metrics === true || obj.metrics === false)) {
    out.metrics = obj.metrics;
  }
  return out;
}
