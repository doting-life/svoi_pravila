// Stress ramp against the fake-provider app: 1,5,10,15,20,30 RPS (and optional 50).
import { assist, stagedScenarios, SUMMARY_TREND_STATS } from './lib.js';

const levels = [
  { rps: 1, duration: 30 },
  { rps: 5, duration: 30 },
  { rps: 10, duration: 60 },
  { rps: 15, duration: 30 },
  { rps: 20, duration: 30 },
  { rps: 30, duration: 30 },
];
if (__ENV.EXTRA_LEVELS) {
  for (const rps of __ENV.EXTRA_LEVELS.split(',')) levels.push({ rps: parseInt(rps, 10), duration: 30 });
}
const { scenarios, thresholds } = stagedScenarios(levels, 5, 400);

export const options = {
  scenarios,
  thresholds,
  summaryTrendStats: SUMMARY_TREND_STATS,
};

export function run() {
  assist(__ENV.LEVEL);
}

export function handleSummary(data) {
  return { [__ENV.SUMMARY || '/results/stress-summary.json']: JSON.stringify(data, null, 2) };
}
