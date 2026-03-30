import lightweightChartsUrl from 'lightweight-charts?url';

function resolveLwcSpecifierForBlobImport(specifier) {
  if (/^https?:\/\//i.test(specifier)) {
    return specifier;
  }
  if (typeof window === 'undefined' || !window.location?.href) {
    return specifier;
  }
  return new URL(specifier, window.location.href).href;
}

function stripLightweightChartsImports(source) {
  let remaining = source;
  const parts = [];
  for (;;) {
    const named =
      /^\s*import\s+\{([\s\S]*?)\}\s+from\s+['"]lightweight-charts['"]\s*;?\s*/m.exec(remaining);
    if (named) {
      const inner = named[1].replace(/\s+/g, ' ').trim();
      parts.push(`const { ${inner} } = LightweightCharts;\n`);
      remaining = remaining.slice(0, named.index) + remaining.slice(named.index + named[0].length);
      continue;
    }
    const star =
      /^\s*import\s+\*\s+as\s+(\w+)\s+from\s+['"]lightweight-charts['"]\s*;?\s*/m.exec(remaining);
    if (star) {
      parts.push(`const ${star[1]} = LightweightCharts;\n`);
      remaining = remaining.slice(0, star.index) + remaining.slice(star.index + star[0].length);
      continue;
    }
    const defaultImport =
      /^\s*import\s+(\w+)\s+from\s+['"]lightweight-charts['"]\s*;?\s*/m.exec(remaining);
    if (defaultImport) {
      parts.push(`const ${defaultImport[1]} = LightweightCharts;\n`);
      remaining = remaining.slice(0, defaultImport.index) + remaining.slice(defaultImport.index + defaultImport[0].length);
      continue;
    }
    break;
  }
  return { bindings: parts.join(''), body: remaining };
}

function buildModuleSource(bindings, body, lwcImportHref) {
  return `const __vibeCharts = [];
import * as __LWC__ from ${JSON.stringify(lwcImportHref)};
const LightweightCharts = new Proxy(__LWC__, {
  get(target, prop, receiver) {
    if (prop === 'createChart') {
      return (container, options) => {
        const chart = target.createChart(container, options);
        __vibeCharts.push(chart);
        return chart;
      };
    }
    return Reflect.get(target, prop, receiver);
  },
});
${bindings}
${body}

export function __vibeGetCollectedCharts() {
  return __vibeCharts;
}
`;
}

export async function loadStrategyChartsModule(userSource) {
  let { bindings, body } = stripLightweightChartsImports(userSource);
  if (!bindings.trim()) {
    bindings = `const { createChart, LineSeries, CandlestickSeries, AreaSeries, HistogramSeries, BaselineSeries, ColorType } = LightweightCharts;\n`;
  }
  const lwcImportHref = resolveLwcSpecifierForBlobImport(lightweightChartsUrl);
  const full = buildModuleSource(bindings, body, lwcImportHref);
  const blob = new Blob([full], { type: 'text/javascript' });
  const url = URL.createObjectURL(blob);
  try {
    const mod = await import(/* @vite-ignore */ url);
    if (typeof mod.render_charts !== 'function') {
      URL.revokeObjectURL(url);
      throw new Error('charts.js did not export render_charts');
    }
    return {
      render_charts: mod.render_charts,
      getCollectedCharts: () => mod.__vibeGetCollectedCharts(),
      revokeModuleUrl: () => URL.revokeObjectURL(url),
    };
  } catch (err) {
    URL.revokeObjectURL(url);
    throw err;
  }
}
