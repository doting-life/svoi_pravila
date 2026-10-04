// Real GigaChat end-to-end ramp. PAID: each request = >=1 provider call.
// Default ramp 0.2 / 0.5 / 1 / 2 RPS. Select levels with LEVELS (e.g. LEVELS=0.2,0.5)
// and duration per level with DURATION (seconds). Estimate calls before running:
//   calls ~= sum(rps * duration) * (1 + retry_rate)
import { assist, stagedScenarios, SUMMARY_TREND_STATS } from './lib.js';

const D = parseInt(__ENV.DURATION || '60', 10);
const LEVELS = (__ENV.LEVELS || '0.2,0.5,1,2').split(',').map(Number);
const { scenarios, thresholds } = stagedScenarios(
  LEVELS.map((rps) => ({ rps, duration: D })),
  parseInt(__ENV.GAP || '20', 10),
  100,
);

export const options = {
  scenarios,
  thresholds: {
    ...thresholds,
    // Abort the whole ramp if the provider degrades (errors > 5%).
    checks: [{ threshold: 'rate>0.95', abortOnFail: true, delayAbortEval: '60s' }],
  },
  summaryTrendStats: SUMMARY_TREND_STATS,
};

export function run() {
  assist(__ENV.LEVEL);
}

export function handleSummary(data) {
  return { [__ENV.SUMMARY || '/results/gigachat-summary.json']: JSON.stringify(data, null, 2) };
}
