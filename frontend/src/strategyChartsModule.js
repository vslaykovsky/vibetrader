import * as ImportedLightweightCharts from 'lightweight-charts';

const VIBE_LWC_GLOBAL = '__vibeLightweightCharts';
if (typeof window !== 'undefined') {
  window[VIBE_LWC_GLOBAL] = ImportedLightweightCharts;
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

function buildModuleSource(bindings, body) {
  return `const __vibeCharts = [];
const __LWC__ = globalThis[${JSON.stringify(VIBE_LWC_GLOBAL)}];
if (!__LWC__) {
  throw new Error('Lightweight Charts not available');
}
const LightweightCharts = Object.fromEntries(
  Object.keys(__LWC__).map(k =>
    k === 'createChart'
      ? [k, (container, options) => {
          const chart = __LWC__.createChart(container, options);
          chart.applyOptions({ layout: { attributionLogo: false } });
          __vibeCharts.push(chart);
          return chart;
        }]
      : [k, __LWC__[k]]
  )
);
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
  const full = buildModuleSource(bindings, body);
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
