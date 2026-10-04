// Platform capacity: run against an app instance with LLM_PROVIDER=fake.
// 1 / 2 / 5 / 10 RPS, 60 s each (override with DURATION).
import { assist, stagedScenarios, SUMMARY_TREND_STATS } from './lib.js';

const D = parseInt(__ENV.DURATION || '60', 10);
const { scenarios, thresholds } = stagedScenarios([
  { rps: 1, duration: D },
  { rps: 2, duration: D },
  { rps: 5, duration: D },
  { rps: 10, duration: D },
]);

export const options = { scenarios, thresholds, summaryTrendStats: SUMMARY_TREND_STATS };

export function run() {
  assist(__ENV.LEVEL);
}

export function handleSummary(data) {
  return { [__ENV.SUMMARY || '/results/platform-summary.json']: JSON.stringify(data, null, 2) };
}
